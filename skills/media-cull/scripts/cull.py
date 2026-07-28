#!/usr/bin/env python3
"""Find photos worth deleting on their own merits and (optionally) quarantine them.

Unlike media-dedup (which finds *redundant* copies), every file flagged here is
judged bad in isolation -- a blown-out flare, an accidental pocket shot, a smeared
frame. Detection is pure numpy/scipy: deterministic, no model inference, same
answer on every run.

Never modifies or moves your originals unless you pass --move. The only file it
writes by default is the contact sheet, which exists because "is this photo bad?"
is a visual judgment a metric cannot finish -- you make the final call by looking.

Reasons:
  blown    a large contiguous region is clipped to white AND has no texture left
  dark     the frame is overwhelmingly crushed to black (pocket / lens-cap shots)
  uniform  almost no detail anywhere (blank wall, ceiling, covered lens)
  blurry   variance-of-Laplacian below an absolute floor (smeared, not just softer
           than a sibling -- that softer-than case is media-dedup's job)

Run via uv (system python usually lacks these libs):
  uv run --with pillow-heif --with pillow --with numpy --with scipy \
    python3 cull.py DIR [--move] [--sheet PATH] [--stats]
"""
import sys, os, argparse
import pillow_heif
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from scipy import ndimage

pillow_heif.register_heif_opener()

IMG_EXT = (".heic", ".heif", ".jpg", ".jpeg", ".png")
VID_EXT = ("MP4", "MOV", "mp4", "mov")
REASONS = ("blown", "dark", "uniform", "blurry")
SHEET_NAME = "cull_review.jpg"

WORK = 640          # long edge for region metrics
SHARP_BOX = 256     # matches media-dedup, so sharpness numbers are comparable

# Tuned against real folders; every gate needs TWO independent signals to agree,
# so a legitimately bright subject (white shirt, string lights) is not "blown"
# and a deliberately moody low-key shot is not "dark".
T_BLOWN_BLOB, T_BLOWN_DEAD = 0.15, 0.10
T_DARK_FRAC, T_DARK_MEAN = 0.60, 35.0
T_UNIFORM_FLAT, T_UNIFORM_STD = 0.90, 12.0
# Calibrated by blurring a known-good frame: original 1563, gaussian r=8 -> 248,
# r=12 -> 120. Real photos in a test folder bottomed out at 1510, so 250 fires on
# visibly-ruined blur with ~6x margin. Sharpness is measured after a 256x256
# downscale, so mild blur is invisible to it by design -- this catches gross
# smearing only, and low-detail scenes (plain walls, fog) can score low honestly.
T_BLURRY_SHARP = 250.0


def sharpness(g):
    """Variance of the Laplacian on a fixed 256x256 grayscale copy."""
    a = np.asarray(g.resize((SHARP_BOX, SHARP_BOX)), dtype=np.float64)
    lap = (-4 * a + np.roll(a, 1, 0) + np.roll(a, -1, 0)
           + np.roll(a, 1, 1) + np.roll(a, -1, 1))
    return float(lap.var())


def largest_blob(mask):
    """Largest contiguous True region as a fraction of the frame.

    This is what separates a flare from scattered specular highlights: both can
    clip the same number of pixels, but only the flare clips them all in one piece.
    """
    lbl, n = ndimage.label(mask)
    if not n:
        return 0.0
    return float(np.bincount(lbl.ravel())[1:].max() / mask.size)


def analyze(path):
    im = Image.open(path)
    try:
        im.draft("L", (WORK, WORK))   # cheap DCT-domain downscale on JPEG
    except Exception:
        pass
    g = im.convert("L")
    sharp = sharpness(g)
    g.thumbnail((WORK, WORK))
    a = np.asarray(g, dtype=np.float32)

    # local texture: stdev in a 7x7 window
    mean = ndimage.uniform_filter(a, 7)
    var = ndimage.uniform_filter(a * a, 7) - mean * mean
    flat = np.sqrt(np.maximum(var, 0)) < 3.0

    clipped = a >= 250
    crushed = a <= 16

    return {
        "path": path,
        "name": os.path.basename(path),
        "blob": largest_blob(clipped),        # biggest clipped region
        "dead": float((clipped & flat).mean()),  # clipped AND textureless
        "darkf": float(crushed.mean()),
        "flat": float(flat.mean()),
        "std": float(a.std()),
        "luma": float(a.mean()),
        "sharp": round(sharp, 1),
    }


def verdict(m):
    """Return a reason string, or None. Order matters: most specific first."""
    if m["blob"] >= T_BLOWN_BLOB and m["dead"] >= T_BLOWN_DEAD:
        return "blown"
    if m["darkf"] >= T_DARK_FRAC and m["luma"] < T_DARK_MEAN:
        return "dark"
    if m["flat"] >= T_UNIFORM_FLAT and m["std"] < T_UNIFORM_STD:
        return "uniform"
    if m["sharp"] < T_BLURRY_SHARP:
        return "blurry"
    return None


def why(m, reason):
    """One-line evidence for the report and the contact-sheet caption."""
    if reason == "blown":
        return f"{m['blob']*100:.0f}% clipped blob, {m['dead']*100:.0f}% textureless"
    if reason == "dark":
        return f"{m['darkf']*100:.0f}% crushed black, luma {m['luma']:.0f}"
    if reason == "uniform":
        return f"{m['flat']*100:.0f}% flat, std {m['std']:.1f}"
    return f"sharpness {m['sharp']}"


def _font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/TTF/DejaVuSans.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def contact_sheet(flagged, out_path, cell=360, cols=4):
    """Grid of every flagged image, captioned with file name and evidence.

    Rendered through pillow-heif's bundled libheif, so it shows HEICs the system
    image libraries may be too old to open.
    """
    pad, bar = 8, 34
    cols = max(1, min(cols, len(flagged)))   # don't pad out a grid we can't fill
    rows = (len(flagged) + cols - 1) // cols
    W = cols * (cell + pad) + pad
    H = rows * (cell + bar + pad) + pad
    sheet = Image.new("RGB", (W, H), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    f_name, f_why = _font(13), _font(11)

    for i, (m, reason) in enumerate(flagged):
        cx = pad + (i % cols) * (cell + pad)
        cy = pad + (i // cols) * (cell + bar + pad)
        try:
            th = Image.open(m["path"]).convert("RGB")
            th.thumbnail((cell, cell))
            sheet.paste(th, (cx + (cell - th.width) // 2,
                             cy + (cell - th.height) // 2))
        except Exception as e:
            d.text((cx + 6, cy + 6), f"unreadable\n{e}", font=f_why, fill=(255, 90, 90))
        d.text((cx, cy + cell + 3), f"{m['name']}  [{reason}]",
               font=f_name, fill=(255, 255, 255))
        d.text((cx, cy + cell + 19), why(m, reason), font=f_why, fill=(170, 170, 170))

    sheet.save(out_path, quality=88)
    return out_path


def move_with_pair(name, src_dir, dst_dir):
    """Move a still and its Live Photo video (same stem) together."""
    moved = []
    src = os.path.join(src_dir, name)
    if os.path.exists(src):
        os.makedirs(dst_dir, exist_ok=True)
        os.replace(src, os.path.join(dst_dir, name))
        moved.append(name)
    stem = os.path.splitext(name)[0]
    for ext in VID_EXT:
        vp = os.path.join(src_dir, f"{stem}.{ext}")
        if os.path.exists(vp):
            os.makedirs(dst_dir, exist_ok=True)
            os.replace(vp, os.path.join(dst_dir, f"{stem}.{ext}"))
            moved.append(f"{stem}.{ext}")
    return moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--move", action="store_true",
                    help="quarantine flagged files into culled/<reason>/")
    ap.add_argument("--sheet", metavar="PATH",
                    help="where to write the contact sheet "
                         "(default: <dir>/" + SHEET_NAME + "; '-' to skip)")
    ap.add_argument("--stats", action="store_true",
                    help="print metrics for every file, not just flagged ones")
    args = ap.parse_args()
    DIR = args.dir

    # Never analyze our own contact sheet: it lives in the scanned folder, and a
    # sparse one is mostly dark background, so a re-run could flag and quarantine it.
    sheet_name = os.path.basename(args.sheet) if args.sheet and args.sheet != "-" \
        else SHEET_NAME

    files = [os.path.join(DIR, f) for f in sorted(os.listdir(DIR))
             if f.lower().endswith(IMG_EXT) and f != sheet_name]
    items = []
    for p in files:
        try:
            items.append(analyze(p))
        except Exception as e:
            print(f"skip {p}: {e}", file=sys.stderr)

    flagged = [(m, r) for m in items for r in [verdict(m)] if r]

    if args.stats:
        print(f"{'file':<28}{'blob%':>7}{'dead%':>7}{'dark%':>7}{'flat%':>7}"
              f"{'std':>7}{'luma':>7}{'sharp':>9}  flag")
        for m in sorted(items, key=lambda x: -x["blob"]):
            print(f"{m['name']:<28}{m['blob']*100:7.1f}{m['dead']*100:7.1f}"
                  f"{m['darkf']*100:7.1f}{m['flat']*100:7.1f}{m['std']:7.1f}"
                  f"{m['luma']:7.0f}{m['sharp']:9.1f}  {verdict(m) or ''}")
        print()

    print(f"{len(items)} images scanned. {len(flagged)} flagged as removable.\n")
    for reason in REASONS:
        rows = sorted([m for m, r in flagged if r == reason], key=lambda m: m["name"])
        if not rows:
            continue
        print(f"===== {reason.upper()} : {len(rows)} files =====")
        for m in rows:
            print(f"  {m['name']:<28} {why(m, reason)}")
        print()

    if not flagged:
        print("Nothing flagged. No contact sheet written, nothing moved.")
        return

    sheet_path = None
    if args.sheet != "-":
        sheet_path = args.sheet or os.path.join(DIR, SHEET_NAME)
        contact_sheet(flagged, sheet_path)

    if not args.move:
        print("-" * 64)
        print(f"NOTHING WAS CHANGED. All {len(items)} originals are still in place.")
        if sheet_path:
            print(f"One new file was written: {sheet_path}")
        print("\nWHAT TO DO NEXT")
        if sheet_path:
            print(f"  1. Open {os.path.basename(sheet_path)} and look at the "
                  f"{len(flagged)} flagged image(s).")
            print("     A metric can mistake a white shirt or stage lights for a")
            print("     blowout, and a moody shot for 'dark' -- so check by eye.")
            print("  2. If you agree they are junk, re-run the same command with")
            print("     --move. If you disagree, do nothing and delete the sheet.")
        else:
            print("  Re-run with --move if you agree the files above are junk.")
        print("\n--move does NOT delete: it relocates files into "
              "culled/<reason>/,\nwhich you can undo with "
              "`mv culled/*/* . && rm -rf culled`.")
        return

    base = os.path.join(DIR, "culled")
    print(f"Moving into {base}/ ...")
    for reason in REASONS:
        dst = os.path.join(base, reason)
        n_still = n_vid = 0
        for m, r in flagged:
            if r != reason:
                continue
            for mv in move_with_pair(m["name"], DIR, dst):
                if mv.lower().endswith(IMG_EXT):
                    n_still += 1
                else:
                    n_vid += 1
        if n_still or n_vid:
            print(f"  {reason}: {n_still} stills + {n_vid} paired videos")
    print("\nDone. Undo everything with "
          "`mv culled/*/* . && rm -rf culled`, or delete one reviewed "
          "category with `rm -rf culled/<reason>`.")


if __name__ == "__main__":
    main()

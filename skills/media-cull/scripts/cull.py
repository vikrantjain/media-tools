#!/usr/bin/env python3
"""Find photos worth deleting on their own merits and (optionally) quarantine them.

Unlike media-dedup (which finds *redundant* copies), every file flagged here is
judged bad in isolation -- a blown-out flare, an accidental pocket shot, a smeared
frame. Detection is pure numpy/scipy: deterministic, no model inference, same
answer on every run.

Never modifies or moves your originals unless you pass --move, and never deletes:
--move relocates into culled/<reason>/, suffixing __1 rather than overwriting if
that name is already quarantined. The only file it writes by default is the contact
sheet, which exists because "is this photo bad?" is a visual judgment a metric
cannot finish -- you make the final call by looking.

Scans the given directory only, not subdirectories.

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
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin
import numpy as np
from scipy import ndimage

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))
from media_common import (IMG_EXT, is_image, sharpness, progress, move_with_pair,
                          sheet_skip_names, is_tool_sheet, undo_hint,
                          SHEET_MARKER, SHEET_NAME)

pillow_heif.register_heif_opener()

REASONS = ("blown", "dark", "uniform", "blurry")
QUARANTINE = "culled"
PER_SHEET = 40      # flagged images per contact sheet before paginating

UNDO = undo_hint(QUARANTINE)

WORK = 640          # long edge for region metrics

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
# smearing only, and low-detail scenes (plain walls, fog, smooth gradients) can
# score low honestly: a synthetic sharp-but-smooth frame measured 111.
T_BLURRY_SHARP = 250.0


def largest_blob(mask):
    """Largest contiguous True region as a fraction of the frame.

    This is what separates a flare from scattered specular highlights: both can
    clip the same number of pixels, but only the flare clips them all in one piece.

    8-connectivity, so a flare bridged only diagonally counts as the single mass
    it visually is; 4-connectivity would split it and under-measure the blob.
    """
    lbl, n = ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))
    if not n:
        return 0.0
    return float(np.bincount(lbl.ravel())[1:].max() / mask.size)


def analyze(path):
    with Image.open(path) as im:
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


def guard_sheet_target(path):
    """Refuse to write a sheet over anything we did not write ourselves.

    Originals are never modified by this tool, and that has to hold for the sheet
    path too: `--sheet vacation.jpg` used to overwrite vacation.jpg in place, which
    destroyed the photo and its metadata, while the same name being excluded from
    the scan meant the report never mentioned it.
    """
    if os.path.exists(path) and not is_tool_sheet(path):
        sys.exit(f"error: refusing to overwrite {path} -- it exists and was not "
                 f"written by this tool.\n"
                 f"       Originals are never modified. Pick another --sheet path, "
                 f"or delete that file first if you are sure.")


def save_sheet(sheet, out_path):
    """Write a sheet, stamped with SHEET_MARKER so a rerun may replace it."""
    if os.path.splitext(out_path)[1].lower() == ".png":
        meta = PngImagePlugin.PngInfo()
        meta.add_text("Comment", SHEET_MARKER)
        sheet.save(out_path, pnginfo=meta)
        return
    try:
        sheet.save(out_path, quality=88, comment=SHEET_MARKER.encode())
    except (TypeError, ValueError, OSError):
        # Format without comment support: still write it, but an unmarked sheet
        # will not be overwritable next run -- safe direction.
        sheet.save(out_path, quality=88)


def sheet_page_paths(n_flagged, out_path, per_sheet=PER_SHEET):
    """Every path contact_sheets() would write, so all can be guarded up front.

    The guard used to run inside one_sheet, i.e. per page as it was about to be
    saved -- so `--sheet photo.jpg` with 90 flagged files could write photo_001.jpg
    and only then refuse at photo_002.jpg, having already done partial work.
    """
    if n_flagged <= per_sheet:
        return [out_path]
    stem, ext = os.path.splitext(out_path)
    return [f"{stem}_{i // per_sheet + 1:03d}{ext}"
            for i in range(0, n_flagged, per_sheet)]


def contact_sheets(flagged, out_path, per_sheet=PER_SHEET):
    """Write flagged images as one or more contact sheets; return the paths.

    Paginated because a single sheet grows without bound: 200 flagged files makes
    a 1480x20108 image no one can review, and past ~650 the save fails outright on
    Pillow's 65500px limit -- after the whole folder has already been analyzed.
    """
    pages = sheet_page_paths(len(flagged), out_path, per_sheet)
    return [one_sheet(flagged[i * per_sheet:(i + 1) * per_sheet], p)
            for i, p in enumerate(pages)]


def one_sheet(flagged, out_path, cell=360, cols=4):
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
            with Image.open(m["path"]) as src:
                th = src.convert("RGB")
            th.thumbnail((cell, cell))
            sheet.paste(th, (cx + (cell - th.width) // 2,
                             cy + (cell - th.height) // 2))
        except Exception as e:
            d.text((cx + 6, cy + 6), f"unreadable\n{e}", font=f_why, fill=(255, 90, 90))
        d.text((cx, cy + cell + 3), f"{m['name']}  [{reason}]",
               font=f_name, fill=(255, 255, 255))
        d.text((cx, cy + cell + 19), why(m, reason), font=f_why, fill=(170, 170, 170))

    guard_sheet_target(out_path)   # backstop; main() already guarded every page
    save_sheet(sheet, out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--move", action="store_true",
                    help=f"quarantine flagged files into {QUARANTINE}/<reason>/")
    ap.add_argument("--sheet", metavar="PATH",
                    help="where to write the contact sheet "
                         "(default: <dir>/" + SHEET_NAME + "; '-' to skip)")
    ap.add_argument("--stats", action="store_true",
                    help="print metrics for every file, not just flagged ones")
    args = ap.parse_args()
    DIR = args.dir

    if not os.path.isdir(DIR):
        sys.exit(f"error: not a directory: {DIR}")
    # Checked up front: Pillow only rejects an unknown extension at save time,
    # which would otherwise blow up after the whole folder has been analyzed.
    if args.sheet and args.sheet != "-":
        Image.init()
        ext = os.path.splitext(args.sheet)[1].lower()
        if ext not in Image.registered_extensions():
            sys.exit(f"error: --sheet needs a known image extension, got '{ext or args.sheet}' "
                     f"(try .jpg or .png)")
        # Before analysis, so pointing --sheet at a photo fails instantly. The
        # paginated page names depend on the flagged count, so they get guarded
        # below -- still before any sheet is written.
        guard_sheet_target(args.sheet)

    names = sorted(os.listdir(DIR))
    skip = sheet_skip_names(names, args.sheet)
    files = [os.path.join(DIR, f) for f in names if is_image(f) and f not in skip]
    items = []
    for i, p in enumerate(files):
        if is_tool_sheet(p):
            continue          # a contact sheet under some other name
        try:
            items.append(analyze(p))
        except Exception as e:
            print(f"skip {p}: {e}", file=sys.stderr)
        progress(i + 1, len(files))

    flagged = [(m, r) for m in items for r in [verdict(m)] if r]

    # Widen the name column to fit the longest name instead of letting it spill
    # and knock every following column out of alignment.
    w = max(28, min(60, max((len(m["name"]) for m in items), default=0) + 2))

    if args.stats:
        print(f"{'file':<{w}}{'blob%':>7}{'dead%':>7}{'dark%':>7}{'flat%':>7}"
              f"{'std':>7}{'luma':>7}{'sharp':>9}  flag")
        for m in sorted(items, key=lambda x: -x["blob"]):
            print(f"{m['name']:<{w}}{m['blob']*100:7.1f}{m['dead']*100:7.1f}"
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
            print(f"  {m['name']:<{w}} {why(m, reason)}")
        print()

    if not flagged:
        print("Nothing flagged. No contact sheet written, nothing moved.")
        return

    sheet_paths = []
    if args.sheet != "-":
        target = args.sheet or os.path.join(DIR, SHEET_NAME)
        for p in sheet_page_paths(len(flagged), target):
            guard_sheet_target(p)   # every page, before writing any of them
        sheet_paths = contact_sheets(flagged, target)

    if not args.move:
        print("-" * 64)
        print(f"NOTHING WAS CHANGED. All {len(items)} originals are still in place.")
        if sheet_paths:
            label = "One new file was" if len(sheet_paths) == 1 else \
                    f"{len(sheet_paths)} new files were"
            print(f"{label} written: {', '.join(sheet_paths)}")
        print("\nWHAT TO DO NEXT")
        if sheet_paths:
            what = "it" if len(sheet_paths) == 1 else "them"
            print(f"  1. Open {', '.join(os.path.basename(p) for p in sheet_paths)} "
                  f"and look at the {len(flagged)} flagged image(s) in {what}.")
            print("     A metric can mistake a white shirt or stage lights for a")
            print("     blowout, and a moody shot for 'dark' -- so check by eye.")
            print("  2. If you agree they are junk, re-run the same command with")
            print("     --move. If you disagree, do nothing and delete the sheet.")
        else:
            print("  Re-run with --move if you agree the files above are junk.")
        print("\n--move does NOT delete: it relocates files into "
              f"{QUARANTINE}/<reason>/,\nwhich you can undo with `{UNDO}`.")
        return

    base = os.path.join(DIR, QUARANTINE)
    print(f"Moving into {base}/ ...")
    all_renamed = []
    for reason in REASONS:
        dst = os.path.join(base, reason)
        n_still = n_vid = 0
        for m, r in flagged:
            if r != reason:
                continue
            moved, renamed = move_with_pair(m["name"], DIR, dst, names)
            all_renamed += [(reason, a, b) for a, b in renamed]
            for mv in moved:
                if is_image(mv):
                    n_still += 1
                else:
                    n_vid += 1
        if n_still or n_vid:
            print(f"  {reason}: {n_still} stills + {n_vid} paired videos")
    if all_renamed:
        print("\nRenamed on arrival -- a file of that name was already quarantined")
        print("from an earlier run, and overwriting it would have been a delete:")
        for reason, a, b in all_renamed:
            print(f"  {QUARANTINE}/{reason}/{a} was taken, saved as {b}")
    print(f"\nDone. Undo everything with `{UNDO}`,")
    print(f"or delete one reviewed category with `rm -rf {QUARANTINE}/<reason>`.")


if __name__ == "__main__":
    main()

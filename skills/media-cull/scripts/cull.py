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
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from scipy import ndimage

pillow_heif.register_heif_opener()

IMG_EXT = (".heic", ".heif", ".jpg", ".jpeg", ".png")
VID_EXT = ("MP4", "MOV", "mp4", "mov")
REASONS = ("blown", "dark", "uniform", "blurry")
SHEET_NAME = "cull_review.jpg"
PER_SHEET = 40      # flagged images per contact sheet before paginating

# `mv -n` so restoring never clobbers a same-named file that is back in the folder,
# and `rmdir` rather than `rm -rf` because it refuses to remove a non-empty dir --
# so anything mv skipped stays safely in culled/ instead of being deleted.
UNDO = "mv -n culled/*/* . && rmdir culled/* culled"

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
    """Variance of the Laplacian on the interior of a fixed 256x256 grayscale copy.

    The border is excluded because np.roll wraps: including it compares the top
    row against the bottom (and left against right) and counts that seam as
    detail. On a horizon shot -- bright sky over dark ground -- the fake seam
    gradient is big enough to hide real blur: a gaussian r=10 blur measured 285
    with the seam included and 2.6 without, against a threshold of 250. Detailed
    frames move less than 1%, so T_BLURRY_SHARP did not need recalibrating.
    """
    a = np.asarray(g.resize((SHARP_BOX, SHARP_BOX)), dtype=np.float64)
    lap = (-4 * a + np.roll(a, 1, 0) + np.roll(a, -1, 0)
           + np.roll(a, 1, 1) + np.roll(a, -1, 1))
    return float(lap[1:-1, 1:-1].var())


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


def contact_sheets(flagged, out_path, per_sheet=PER_SHEET):
    """Write flagged images as one or more contact sheets; return the paths.

    Paginated because a single sheet grows without bound: 200 flagged files makes
    a 1480x20108 image no one can review, and past ~650 the save fails outright on
    Pillow's 65500px limit -- after the whole folder has already been analyzed.
    """
    if len(flagged) <= per_sheet:
        return [one_sheet(flagged, out_path)]
    stem, ext = os.path.splitext(out_path)
    paths = []
    for i in range(0, len(flagged), per_sheet):
        page = i // per_sheet + 1
        paths.append(one_sheet(flagged[i:i + per_sheet], f"{stem}_{page:03d}{ext}"))
    return paths


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


def sheet_names(names, sheet_arg):
    """Contact sheets we may have written, this run or an earlier one.

    Never analyze our own sheet. A sheet of dark frames self-flags -- measured
    `dark` at 1-2 cells, `uniform` at 5, `blurry` at 6+ -- so culling a folder of
    pocket shots leaves a sheet that the next --move run would quarantine.
    Both the default name and any --sheet name are excluded, plus their _001
    pagination pages: keying only on the name passed in would let a stale
    cull_review.jpg from a previous default run become an input.
    """
    stems = [os.path.splitext(SHEET_NAME)]
    if sheet_arg and sheet_arg != "-":
        stems.append(os.path.splitext(os.path.basename(sheet_arg)))
    skip = set()
    for stem, ext in stems:
        for n in names:
            b, e = os.path.splitext(n)
            if e.lower() == ext.lower() and (b == stem or b.startswith(stem + "_")):
                skip.add(n)
    return skip


def free_path(dst_dir, name):
    """A destination path that does not already exist, suffixing __1, __2, ...

    os.replace would silently overwrite, which for this tool means destroying a
    file already quarantined under the same name by an earlier run -- a real
    delete, in a tool whose whole promise is that it never deletes.
    """
    stem, ext = os.path.splitext(name)
    cand, i = os.path.join(dst_dir, name), 1
    while os.path.exists(cand):
        cand = os.path.join(dst_dir, f"{stem}__{i}{ext}")
        i += 1
    return cand


def move_with_pair(name, src_dir, dst_dir):
    """Move a still and its Live Photo video (same stem) together."""
    moved, renamed = [], []

    def relocate(fname):
        dst = free_path(dst_dir, fname)
        os.makedirs(dst_dir, exist_ok=True)
        os.replace(os.path.join(src_dir, fname), dst)
        moved.append(fname)
        if os.path.basename(dst) != fname:
            renamed.append((fname, os.path.basename(dst)))

    if os.path.exists(os.path.join(src_dir, name)):
        relocate(name)
    stem = os.path.splitext(name)[0]
    for ext in VID_EXT:
        if os.path.exists(os.path.join(src_dir, f"{stem}.{ext}")):
            relocate(f"{stem}.{ext}")
    return moved, renamed


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

    names = sorted(os.listdir(DIR))
    skip = sheet_names(names, args.sheet)
    files = [os.path.join(DIR, f) for f in names
             if f.lower().endswith(IMG_EXT) and f not in skip]
    items = []
    for p in files:
        try:
            items.append(analyze(p))
        except Exception as e:
            print(f"skip {p}: {e}", file=sys.stderr)

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
        sheet_paths = contact_sheets(flagged, args.sheet or os.path.join(DIR, SHEET_NAME))

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
              f"culled/<reason>/,\nwhich you can undo with `{UNDO}`.")
        return

    base = os.path.join(DIR, "culled")
    print(f"Moving into {base}/ ...")
    all_renamed = []
    for reason in REASONS:
        dst = os.path.join(base, reason)
        n_still = n_vid = 0
        for m, r in flagged:
            if r != reason:
                continue
            moved, renamed = move_with_pair(m["name"], DIR, dst)
            all_renamed += [(reason, a, b) for a, b in renamed]
            for mv in moved:
                if mv.lower().endswith(IMG_EXT):
                    n_still += 1
                else:
                    n_vid += 1
        if n_still or n_vid:
            print(f"  {reason}: {n_still} stills + {n_vid} paired videos")
    if all_renamed:
        print("\nRenamed on arrival -- a file of that name was already quarantined")
        print("from an earlier run, and overwriting it would have been a delete:")
        for reason, a, b in all_renamed:
            print(f"  culled/{reason}/{a} was taken, saved as {b}")
    print(f"\nDone. Undo everything with `{UNDO}`,")
    print("or delete one reviewed category with `rm -rf culled/<reason>`.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Shared helpers for media-cull and media-dedup.

Both tools quarantine files into subfolders, both move Live Photo pairs, and both
score sharpness -- and for a while each carried its own copy of that code. The
copies drifted, in the direction that costs data: the unsafe undo command and the
unreported collision rename were fixed in cull and left broken in dedup. The
shared parts live here so the next fix cannot land in only one of them.
"""
import os
import sys
import numpy as np
from PIL import Image

IMG_EXT = (".heic", ".heif", ".jpg", ".jpeg", ".png")
# Compared case-insensitively (see video_pairs), so listing lowercase is enough --
# the old per-tool tuples spelled out "MP4"/"mp4" and still missed ".Mp4".
VID_EXT = (".mp4", ".mov")

SHARP_BOX = 256          # both tools score sharpness here, so numbers compare
PROGRESS_EVERY = 100     # emit a progress line every N files
PROGRESS_MIN = 200       # ...but only for runs big enough to look hung

# Stamped into every contact sheet media-cull writes, so a later run of *either*
# tool can tell regenerable output apart from one of the user's photos.
SHEET_MARKER = "media-cull-contact-sheet"
SHEET_NAME = "cull_review.jpg"


def is_image(name):
    return name.lower().endswith(IMG_EXT)


def is_video(name):
    return name.lower().endswith(VID_EXT)


def progress(i, total, label="analyzed"):
    """Heartbeat on stderr so a large folder does not look like a hung process.

    Decoding thousands of HEICs takes minutes before either tool prints anything,
    and dedup then spends minutes more on its O(n^2) hash comparison.
    """
    if total >= PROGRESS_MIN and (i % PROGRESS_EVERY == 0 or i == total):
        print(f"  {label} {i}/{total}...", file=sys.stderr)


def sharpness(im):
    """Variance of the Laplacian on the interior of a fixed 256x256 grayscale copy.

    The border is excluded because np.roll wraps: including it compares the top
    row against the bottom (and left against right) and counts that seam as
    detail. On a horizon shot -- bright sky over dark ground -- the fake seam
    gradient is big enough to hide real blur: a gaussian r=10 blur measured 285
    with the seam included and 2.6 without, against a threshold of 250.

    Note this is scale-blind: both images are squashed to 256x256 first, so a
    downscaled *and re-sharpened* copy can outscore the original it came from.
    Callers comparing two files must break that tie on resolution -- see
    media-dedup's MP_VETO.
    """
    a = np.asarray(im.convert("L").resize((SHARP_BOX, SHARP_BOX)), dtype=np.float64)
    lap = (-4 * a + np.roll(a, 1, 0) + np.roll(a, -1, 0)
           + np.roll(a, 1, 1) + np.roll(a, -1, 1))
    return float(lap[1:-1, 1:-1].var())


def is_tool_sheet(path):
    """True if `path` is a contact sheet one of these tools wrote."""
    try:
        with Image.open(path) as im:
            info = dict(im.info)
    except Exception:
        return False
    for v in (info.get("comment"), info.get("Comment")):
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        if v and SHEET_MARKER in v:
            return True
    return False


def sheet_skip_names(names, *extra):
    """Which of `names` are contact sheets we may have written, this run or before.

    Never analyze our own sheet. A sheet of dark frames self-flags -- measured
    `dark` at 1-2 cells, `uniform` at 5, `blurry` at 6+ -- so culling a folder of
    pocket shots leaves a sheet that the next --move run would quarantine. dedup
    is exposed to the same thing from the other side: the documented workflow is
    cull first then dedup, which leaves cull's sheet sitting in dedup's input.

    Keying on the default name as well as any --sheet name matters: a stale
    cull_review.jpg from an earlier default run would otherwise become an input.
    Pagination pages (_001, _002, ...) are covered too. Names are a cheap first
    pass; is_tool_sheet catches a sheet written under some other name.
    """
    stems = [os.path.splitext(SHEET_NAME)]
    for e in extra:
        if e and e != "-":
            stems.append(os.path.splitext(os.path.basename(e)))
    skip = set()
    for stem, ext in stems:
        for n in names:
            b, e = os.path.splitext(n)
            if e.lower() == ext.lower() and (b == stem or b.startswith(stem + "_")):
                skip.add(n)
    return skip


def free_path(dst_dir, name):
    """A destination path that does not already exist, suffixing __1, __2, ...

    os.replace would silently overwrite, which for these tools means destroying a
    file already quarantined under the same name by an earlier run -- a real
    delete, in tools whose whole promise is that they never delete.
    """
    stem, ext = os.path.splitext(name)
    cand, i = os.path.join(dst_dir, name), 1
    while os.path.exists(cand):
        cand = os.path.join(dst_dir, f"{stem}__{i}{ext}")
        i += 1
    return cand


def move_with_pair(name, src_dir, dst_dir, entries=None):
    """Move a still and its Live Photo video (same stem) together.

    The video stays put while another still on the same stem remains in src_dir:
    X.HEIC beside its own X.JPG conversion share X.MOV, and dedup flagging the JPG
    used to carry the keeper's video off with it. If that other still is flagged
    too, the video goes with whichever of them moves last.

    Returns (moved, renamed). `renamed` must be surfaced to the user: a file that
    arrived under a suffixed name is not restored to its original name by the undo
    command, so silently suffixing is a silent surprise at restore time.
    """
    moved, renamed = [], []

    def relocate(fname):
        os.makedirs(dst_dir, exist_ok=True)
        dst = free_path(dst_dir, fname)
        os.replace(os.path.join(src_dir, fname), dst)
        moved.append(fname)
        if os.path.basename(dst) != fname:
            renamed.append((fname, os.path.basename(dst)))

    if os.path.exists(os.path.join(src_dir, name)):
        relocate(name)

    # Match the pair case-insensitively on the extension: IMG_1.HEIC pairs with
    # IMG_1.MP4, IMG_1.mp4 or IMG_1.Mp4 depending on where the files came from.
    stem = os.path.splitext(name)[0]
    if entries is None:
        try:
            entries = os.listdir(src_dir)
        except OSError:
            entries = []
    present = [f for f in entries if os.path.exists(os.path.join(src_dir, f))]
    if any(os.path.splitext(f)[0] == stem and is_image(f) for f in present):
        return moved, renamed
    for f in present:
        b, e = os.path.splitext(f)
        if b == stem and e.lower() in VID_EXT:
            relocate(f)
    return moved, renamed


def undo_hint(base):
    """The undo command for a quarantine folder, in the only form that is safe.

    `mv -n` so restoring never clobbers a same-named file that is back in the
    folder, and `rmdir` rather than `rm -rf` because it refuses to remove a
    non-empty directory -- so anything mv skipped stays in the quarantine folder
    instead of being deleted. The plain `mv ... && rm -rf` form silently does
    both: it overwrote a same-named original and then deleted the evidence.
    """
    return f"mv -n {base}/*/* . && rmdir {base}/* {base}"

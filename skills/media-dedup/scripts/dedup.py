#!/usr/bin/env python3
"""Find near-duplicate images and (optionally) quarantine the lower-quality copies.

Read-only by default: prints tiered groups and writes nothing but a report.
With --move, moves flagged copies into dupes_by_confidence/<tier>/ subfolders,
carrying each still's paired Live Photo video (.MP4/.MOV of the same stem) along.
Keepers (the sharpest copy in each group) are never moved. Nothing is deleted.

A copy is flagged only if BOTH hold:
  * it is near-identical to a better copy in the same group, and
  * it is lower quality (softer, by variance-of-Laplacian) than that copy.
Tier = how strict the visual match is:
  1_confident  phash<=5  AND dhash<=5
  2_likely     phash<=8  AND dhash<=8
  3_possible   phash<=10 (single hash; the loose net)

Run via uv (system python usually lacks these libs):
  uv run --with pillow-heif --with pillow --with imagehash --with numpy \
    python3 dedup.py DIR [--move]
"""
import sys, os, argparse
import pillow_heif
from PIL import Image
import imagehash
import numpy as np

pillow_heif.register_heif_opener()

IMG_EXT = (".heic", ".heif", ".jpg", ".jpeg", ".png")
VID_EXT = ("MP4", "MOV", "mp4", "mov")
TIER_DIRS = {1: "1_confident", 2: "2_likely", 3: "3_possible"}


def sharpness(im):
    """Variance of the Laplacian on a 256x256 grayscale copy — higher = sharper.

    Interior only: np.roll wraps, so including the border scores the top-vs-bottom
    seam as detail. On a bright-sky-over-dark-ground frame that fake gradient is
    large enough to swamp the real difference between a sharp and a blurred
    sibling, which is exactly the comparison this feeds. Kept identical to
    media-cull's copy so sharpness numbers stay comparable across the two tools.
    """
    g = im.convert("L").resize((256, 256))
    a = np.asarray(g, dtype=np.float64)
    lap = (-4 * a + np.roll(a, 1, 0) + np.roll(a, -1, 0)
           + np.roll(a, 1, 1) + np.roll(a, -1, 1))
    return float(lap[1:-1, 1:-1].var())


def analyze(path):
    im = Image.open(path)
    w, h = im.size
    return {
        "path": path, "name": os.path.basename(path),
        "phash": imagehash.phash(im), "dhash": imagehash.dhash(im),
        "mp": round(w * h / 1e6, 2), "sharp": round(sharpness(im), 1),
        "bytes": os.path.getsize(path),
    }


def build_groups(items):
    """Union-find at the loose level (phash<=10) so every near-dup is considered."""
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for i in range(n):
        for j in range(i + 1, n):
            if (items[i]["phash"] - items[j]["phash"]) <= 10:
                union(i, j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if len(g) > 1]


def tier_of(cand, keep):
    pd = cand["phash"] - keep["phash"]
    dd = cand["dhash"] - keep["dhash"]
    if pd <= 5 and dd <= 5:
        return 1
    if pd <= 8 and dd <= 8:
        return 2
    return 3


def classify(items):
    """Return {tier: [(cand, keeper), ...]} of flagged lower-quality copies."""
    def qual(idx):
        it = items[idx]
        return (it["sharp"], it["mp"], it["bytes"])

    tiers = {1: [], 2: [], 3: []}
    for g in build_groups(items):
        ranked = sorted(g, key=qual, reverse=True)
        keep = items[ranked[0]]
        for idx in ranked[1:]:
            cand = items[idx]
            if cand["sharp"] >= keep["sharp"]:
                continue  # not lower quality — leave it
            tiers[tier_of(cand, keep)].append((cand, keep))
    return tiers


def free_path(dst_dir, name):
    """A destination path that does not already exist, suffixing __1, __2, ...

    os.replace would silently overwrite, which here means destroying a file
    already quarantined under the same name by an earlier run -- a real delete,
    despite this tool promising it never deletes.
    """
    stem, ext = os.path.splitext(name)
    cand, i = os.path.join(dst_dir, name), 1
    while os.path.exists(cand):
        cand = os.path.join(dst_dir, f"{stem}__{i}{ext}")
        i += 1
    return cand


def move_with_pair(name, src_dir, dst_dir):
    moved = []

    def relocate(fname):
        os.makedirs(dst_dir, exist_ok=True)
        os.replace(os.path.join(src_dir, fname), free_path(dst_dir, fname))
        moved.append(fname)

    if os.path.exists(os.path.join(src_dir, name)):
        relocate(name)
    stem = os.path.splitext(name)[0]
    for ext in VID_EXT:
        if os.path.exists(os.path.join(src_dir, f"{stem}.{ext}")):
            relocate(f"{stem}.{ext}")
    return moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--move", action="store_true",
                    help="quarantine flagged copies into dupes_by_confidence/")
    args = ap.parse_args()
    DIR = args.dir

    files = [os.path.join(DIR, f) for f in sorted(os.listdir(DIR))
             if f.lower().endswith(IMG_EXT)]
    items = []
    for p in files:
        try:
            items.append(analyze(p))
        except Exception as e:
            print(f"skip {p}: {e}", file=sys.stderr)

    tiers = classify(items)
    total = sum(len(v) for v in tiers.values())
    print(f"\n{len(items)} images scanned. {total} lower-quality near-duplicates "
          f"across {sum(1 for v in tiers.values() if v)} populated tiers.\n")

    for t in (1, 2, 3):
        rows = sorted(tiers[t], key=lambda r: r[0]["name"])
        print(f"===== TIER {TIER_DIRS[t]} : {len(rows)} files =====")
        for cand, keep in rows:
            print(f"  {cand['name']:<26} sharp={cand['sharp']:<9} {cand['mp']}MP "
                  f"{cand['bytes']//1024}KB   (keeper {keep['name']} "
                  f"sharp={keep['sharp']})")
        print()

    if not args.move:
        print("Read-only. Re-run with --move to quarantine into "
              "dupes_by_confidence/<tier>/ (reversible; keepers and videos of "
              "keepers stay; nothing is deleted).")
        return

    base = os.path.join(DIR, "dupes_by_confidence")
    print(f"Moving into {base}/ ...")
    for t in (1, 2, 3):
        dst = os.path.join(base, TIER_DIRS[t])
        n_still = n_vid = 0
        for cand, _ in tiers[t]:
            for m in move_with_pair(cand["name"], DIR, dst):
                if m.lower().endswith(IMG_EXT):
                    n_still += 1
                else:
                    n_vid += 1
        print(f"  {TIER_DIRS[t]}: {n_still} stills + {n_vid} paired videos")
    print("\nDone. Review each folder; delete a tier with "
          "`rm -rf dupes_by_confidence/<tier>` or undo all with "
          "`mv dupes_by_confidence/*/* . && rm -rf dupes_by_confidence`.")


if __name__ == "__main__":
    main()

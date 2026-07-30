#!/usr/bin/env python3
"""Find near-duplicate images and (optionally) quarantine the lower-quality copies.

Read-only by default: prints tiered groups to stdout and writes no files at all.
With --move, moves flagged copies into dupes_by_confidence/<tier>/ subfolders,
carrying each still's paired Live Photo video (.MP4/.MOV of the same stem) along.
Keepers (the best copy in each group) are never moved. Nothing is deleted.

A copy is flagged only if ALL of these hold:
  * it is near-identical to a better copy in the same group (phash within 10), and
  * it is lower quality than that copy by a MEANINGFUL margin (see MIN_MARGIN), and
  * it is not materially higher-resolution than that copy (see MP_VETO).
Copies that match closely but whose quality is within the margin are reported as
ties and left alone -- naming a loser between two equally good frames is a coin
flip, and this tool moves files.
Tier = how strict the visual match to the keeper is:
  1_confident  phash<=5  AND dhash<=5
  2_likely     phash<=8  AND dhash<=8
  3_possible   phash<=10 (single hash; the loose net)

Run via uv (system python usually lacks these libs):
  uv run --with pillow-heif --with pillow --with imagehash --with numpy \
    python3 dedup.py DIR [--move] [--stats]
"""
import sys, os, argparse
import pillow_heif
from PIL import Image
import imagehash

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))
from media_common import (IMG_EXT, is_image, sharpness, progress, move_with_pair,
                          sheet_skip_names, is_tool_sheet, undo_hint)

pillow_heif.register_heif_opener()

TIER_DIRS = {1: "1_confident", 2: "2_likely", 3: "3_possible"}
QUARANTINE = "dupes_by_confidence"

# Grouping is transitive, so a group can chain out to members far from its keeper;
# this is the documented ceiling on how far a *flagged* file may sit from the copy
# it is judged against. Beyond it, no tier applies and the file is left alone.
TIER_MAX_PHASH = 10

# A candidate more than this many times the keeper's megapixels is never flagged.
# Sharpness is scale-blind -- both images are squashed to 256x256 before it is
# measured -- so a downscaled *and re-sharpened* copy, which is exactly what
# messaging apps produce, outscores the original it was made from. Measured: a
# 1152x864 re-save scored 285.4 against its own 4032x3024 original's 111.2, which
# made the re-save the keeper and put the 12MP original in 2_likely. Resolution is
# the tiebreaker sharpness cannot be, so it gets a veto.
MP_VETO = 1.3

# Minimum sharpness gap, as a fraction of the sharper copy, before one frame may be
# called the worse copy of another. Below it the two are reported as a tie and neither
# is flagged.
#
# Sharpness is measured on a 256x256 downscale, so on a 3520x1980 photo ~97% of the
# pixels are gone before the number exists. It resolves "smeared vs sharp" and nothing
# finer. Without a floor the comparison still always named a loser, and on a burst-heavy
# folder that is most of the output: of 32 flags on a real 122-file export, 21 rested on
# a gap under 5% and 6 under 1% -- the narrowest a pair of showroom frames at 2783.6 vs
# 2780.7, two visibly different moments, both sharp.
#
# Those verdicts are not merely close, they are unstable: re-measuring at a 252px box
# instead of 256 -- an implementation detail with no bearing on photo quality -- reversed
# 2 of 6 near-ties tested, swapping keeper and discard. A margin the metric cannot
# resolve is noise, and 5% is comfortably above that noise while still catching real
# differences (a genuine soft/sharp pair in that folder differed by 30%).
#
# Resolution differences bypass this entirely: a downscaled re-save is the worse copy
# however it scores, which is a class difference, not a margin.
MIN_MARGIN = 0.05


def analyze(path):
    with Image.open(path) as im:
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
            if (items[i]["phash"] - items[j]["phash"]) <= TIER_MAX_PHASH:
                union(i, j)
        progress(i + 1, n, "compared")
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if len(g) > 1]


def tier_of(cand, keep):
    """Tier for a candidate relative to its keeper, or None if it is too far.

    Tier 3 used to be a bare `else`, which meant it had no upper bound at all:
    because grouping is transitive, a pan sequence put files phash 18 from the
    keeper into 3_possible while the docs promised phash<=10. Returning None for
    those keeps the documented bound honest and errs toward leaving files alone.
    """
    pd = cand["phash"] - keep["phash"]
    dd = cand["dhash"] - keep["dhash"]
    if pd <= 5 and dd <= 5:
        return 1
    if pd <= 8 and dd <= 8:
        return 2
    if pd <= TIER_MAX_PHASH:
        return 3
    return None


def full_res(item, best_mp):
    """Is this copy at (near) the best resolution present in its group?"""
    return item["mp"] >= best_mp / MP_VETO


def margin(cand, keep):
    """Sharpness shortfall of `cand` against `keep`, as a fraction of the sharper one.

    Positive => cand is softer. Normalised so the threshold means the same thing on a
    dim interior at 1400 and a detailed streetscape at 3400.
    """
    hi = max(cand["sharp"], keep["sharp"])
    return 0.0 if hi <= 0 else (keep["sharp"] - cand["sharp"]) / hi


def quality_verdict(cand, keep, best_mp, min_margin):
    """'worse' | 'tie' | 'better' -- is `cand` a worse copy of `keep`?

    Resolution class first, sharpness only *within* a class. Sharpness alone got
    this exactly backwards on the commonest real case -- an original plus a
    messaging-app re-save -- because the re-save is unsharp-masked and scores
    higher: it both crowned the 1MP re-save as keeper and then spared it for
    being "sharper" than the 12MP original it was displacing.

    Within a class the gap must clear `min_margin`, or the two are a tie. A class
    difference is decisive on its own and never falls back to the margin.
    """
    cand_full, keep_full = full_res(cand, best_mp), full_res(keep, best_mp)
    if cand_full != keep_full:
        return "worse" if keep_full else "better"   # the downscaled copy is worse
    m = margin(cand, keep)
    if m > min_margin:
        return "worse"
    if m < -min_margin:
        return "better"
    return "tie"


def classify(items, min_margin=MIN_MARGIN):
    """Return (tiers, ties, notes).

    tiers  {tier: [(cand, keeper), ...]}  flagged as meaningfully worse copies
    ties   [(cand, keeper), ...]          matched closely, quality too close to call
    """
    tiers = {1: [], 2: [], 3: []}
    ties = []
    notes = {"far": 0, "highres": 0}

    for g in build_groups(items):
        best_mp = max(items[i]["mp"] for i in g)

        def qual(idx):
            it = items[idx]
            # Full-resolution copies rank above downscaled ones no matter how
            # sharp the downscaled one scores; within a resolution class,
            # sharpness decides. Without the first term the keeper of an
            # (original, re-save) pair is whichever one got unsharp-masked.
            return (full_res(it, best_mp), it["sharp"], it["mp"], it["bytes"])

        ranked = sorted(g, key=qual, reverse=True)
        keep = items[ranked[0]]
        for idx in ranked[1:]:
            cand = items[idx]
            # Belt and braces: ranking above already puts the higher-resolution
            # copy first, so this should be unreachable -- but it is the one
            # invariant that must never break, so it is checked, not assumed.
            if cand["mp"] > keep["mp"] * MP_VETO:
                notes["highres"] += 1
                continue
            verdict = quality_verdict(cand, keep, best_mp, min_margin)
            if verdict == "better":
                continue
            t = tier_of(cand, keep)
            if t is None:
                notes["far"] += 1             # only transitively linked
                continue
            # A tie is still a close visual match worth showing -- it just isn't a
            # quality judgment, so it is reported and never moved.
            (ties if verdict == "tie" else tiers[t]).append((cand, keep))
    return tiers, ties, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--move", action="store_true",
                    help=f"quarantine flagged copies into {QUARANTINE}/")
    ap.add_argument("--stats", action="store_true",
                    help="print metrics for every file, not just flagged ones")
    ap.add_argument("--min-margin", type=float, default=MIN_MARGIN * 100,
                    metavar="PCT",
                    help=f"sharpness gap required to call one copy worse than another "
                         f"(default {MIN_MARGIN * 100:g}%%); below it the pair is a tie. "
                         f"0 restores the old always-pick-a-loser behaviour.")
    args = ap.parse_args()
    DIR = args.dir
    min_margin = args.min_margin / 100.0

    if not os.path.isdir(DIR):
        sys.exit(f"error: not a directory: {DIR}")

    names = sorted(os.listdir(DIR))
    skip = sheet_skip_names(names)
    files = [os.path.join(DIR, f) for f in names if is_image(f) and f not in skip]
    items = []
    for i, p in enumerate(files):
        if is_tool_sheet(p):
            continue          # a media-cull contact sheet, not one of the photos
        try:
            items.append(analyze(p))
        except Exception as e:
            print(f"skip {p}: {e}", file=sys.stderr)
        progress(i + 1, len(files))

    tiers, ties, notes = classify(items, min_margin)
    total = sum(len(v) for v in tiers.values())

    if args.stats:
        w = max(28, min(60, max((len(m["name"]) for m in items), default=0) + 2))
        print(f"{'file':<{w}}{'MP':>8}{'KB':>9}{'sharp':>10}  phash")
        for m in sorted(items, key=lambda x: -x["sharp"]):
            print(f"{m['name']:<{w}}{m['mp']:8.2f}{m['bytes']//1024:9d}"
                  f"{m['sharp']:10.1f}  {m['phash']}")
        print()

    print(f"\n{len(items)} images scanned. {total} lower-quality near-duplicates "
          f"across {sum(1 for v in tiers.values() if v)} populated tiers"
          + (f", plus {len(ties)} pair(s) too close to call.\n" if ties else ".\n"))

    for t in (1, 2, 3):
        rows = sorted(tiers[t], key=lambda r: r[0]["name"])
        if not rows:
            continue
        print(f"===== TIER {TIER_DIRS[t]} : {len(rows)} files =====")
        for cand, keep in rows:
            print(f"  {cand['name']:<26} sharp={cand['sharp']:<9} {cand['mp']}MP "
                  f"{cand['bytes']//1024}KB   (keeper {keep['name']} "
                  f"sharp={keep['sharp']})")
        print()

    if ties:
        print(f"===== TOO CLOSE TO CALL : {len(ties)} pairs =====")
        print(f"Near-identical, but quality differs by less than "
              f"{min_margin * 100:g}% -- which this metric cannot resolve. NOT flagged "
              f"and not moved; pick by eye if you want one gone.")
        for cand, keep in sorted(ties, key=lambda r: r[0]["name"]):
            print(f"  {cand['name']:<26} sharp={cand['sharp']:<9} vs "
                  f"{keep['name']} sharp={keep['sharp']}"
                  f"   ({margin(cand, keep) * 100:+.2f}%)")
        print()

    if notes["highres"] or notes["far"]:
        print("Spared (grouped, but not flagged):")
        if notes["highres"]:
            print(f"  {notes['highres']} higher-resolution than the best copy in "
                  f"their group -- likely the original of a downscaled re-save.")
        if notes["far"]:
            print(f"  {notes['far']} linked to their group only transitively, "
                  f"further than phash {TIER_MAX_PHASH} from its keeper.")
        print()

    if not total:
        print("Nothing flagged. Nothing was moved and no files were written.")
        if ties:
            print(f"({len(ties)} pair(s) matched closely but were too close to call.)")
        return

    if not args.move:
        print("Read-only. Re-run with --move to quarantine into "
              f"{QUARANTINE}/<tier>/ (reversible; keepers and videos of "
              "keepers stay; nothing is deleted).")
        return

    base = os.path.join(DIR, QUARANTINE)
    print(f"Moving into {base}/ ... (ties are not moved)")
    all_renamed = []
    for t in (1, 2, 3):
        dst = os.path.join(base, TIER_DIRS[t])
        n_still = n_vid = 0
        for cand, _ in tiers[t]:
            moved, renamed = move_with_pair(cand["name"], DIR, dst, names)
            all_renamed += [(TIER_DIRS[t], a, b) for a, b in renamed]
            for m in moved:
                if is_image(m):
                    n_still += 1
                else:
                    n_vid += 1
        if n_still or n_vid:
            print(f"  {TIER_DIRS[t]}: {n_still} stills + {n_vid} paired videos")

    if all_renamed:
        print("\nRenamed on arrival -- a file of that name was already quarantined")
        print("from an earlier run, and overwriting it would have been a delete:")
        for tier, a, b in all_renamed:
            print(f"  {QUARANTINE}/{tier}/{a} was taken, saved as {b}")

    print(f"\nDone. Undo everything with `{undo_hint(QUARANTINE)}`,")
    print(f"or delete one reviewed tier with `rm -rf {QUARANTINE}/<tier>`.")


if __name__ == "__main__":
    main()

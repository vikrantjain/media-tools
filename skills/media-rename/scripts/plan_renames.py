#!/usr/bin/env python3
"""Plan (and optionally perform) capture-timestamp renames, keeping Live Photo pairs together.

Naming is allocated per *group*, not per file. A group is one Live Photo -- a still plus
its motion clip, identified by Apple's ContentIdentifier -- or a single standalone file.
Every member of a group gets the same `yyyymmdd_hhmmss` stem and keeps its own extension.

Why grouping is required, rather than naming each file from its own timestamp:

  1. iPhone writes the motion clip's CreationDate up to a second BEFORE the still's
     DateTimeOriginal. Measured on a real export: IMG_1626.HEIC 20:14:48 vs
     IMG_1626.MP4 20:14:47 -- 4 of 37 pairs in that folder straddled a second boundary.
     Named independently, the pair lands on two different stems.
  2. Collision suffixes were allocated per target *name*, so the counters for `.heic`
     and `.MP4` ran independently. Three stills sharing a second took ``/`-1`/`-2` while
     the single video of that second took ``. One pair split, and -- worse -- the video
     then shared a stem with an unrelated still.

Both matter beyond tidiness: media-cull and media-dedup pair a video to its still BY STEM,
and the documented order is rename -> cull -> dedup. A forged stem makes a later `--move`
carry off a stranger's video and orphan the real still.

Embedded metadata is never touched. The rename is `os.rename` (a directory-entry change)
and the timestamp is `os.utime` (filesystem mtime, outside the file) -- neither can rewrite
a byte of the image, which is a stronger guarantee than exiftool's `-FileName<` offers.
"""

import json
import os
import re
import subprocess
import sys
from collections import defaultdict

# Highest priority first. CreationDate is the local-time video tag and carries a UTC
# offset; CreateDate is QuickTime UTC (converted by -api QuickTimeUTC=1); DateTimeOriginal
# is the still's local shutter time.
TS_TAGS = ("CreationDate", "CreateDate", "DateTimeOriginal")
STILL_EXT = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".dng", ".tif", ".tiff"}
STAMP_RE = re.compile(r"^\d{8}_\d{6}$")


def read_meta(et, paths, recurse=True):
    cmd = [et, "-json", "-api", "QuickTimeUTC=1", "-d", "%Y%m%d_%H%M%S",
           "-ContentIdentifier", *(f"-{t}" for t in TS_TAGS)]
    if recurse:
        cmd.append("-r")
    p = subprocess.run(cmd + list(paths), capture_output=True, text=True)
    if not p.stdout.strip():
        sys.stderr.write(p.stderr)
        return []
    return json.loads(p.stdout)


def pull_in_pair_members(et, paths, records):
    """Naming a still explicitly must not orphan its motion clip.

    Renaming `IMG_1794.HEIC` alone moves the still to a new stem and leaves
    `IMG_1794.MP4` behind -- the very pair split this script exists to prevent, arrived
    at by a different route. So when individual FILES are named, scan their directories
    and adopt any sibling sharing a ContentIdentifier with one of them. Returns the
    adopted records, which the caller reports so the extra renames are never a surprise.
    """
    file_args = [p for p in paths if os.path.isfile(p)]
    if not file_args:
        return []                      # directory args already cover their contents
    have = {os.path.abspath(r["SourceFile"]) for r in records}
    want = {r.get("ContentIdentifier") for r in records if r.get("ContentIdentifier")}
    if not want:
        return []
    dirs = sorted({os.path.dirname(os.path.abspath(p)) or "." for p in file_args})
    adopted = []
    for rec in read_meta(et, dirs, recurse=False):
        cid = rec.get("ContentIdentifier")
        if cid in want and os.path.abspath(rec["SourceFile"]) not in have:
            adopted.append(rec)
    return adopted


def timestamp(rec):
    """Per-file capture stamp, highest-priority tag that holds a value."""
    for tag in TS_TAGS:
        v = rec.get(tag)
        if isinstance(v, str) and STAMP_RE.match(v.strip()):
            return v.strip()
    return None


def build_groups(records):
    """-> list of groups; each is (dirname, sort_key, [(path, ext, ts), ...])."""
    buckets = defaultdict(list)
    undated = []
    for rec in records:
        src = rec["SourceFile"]
        ts = timestamp(rec)
        if ts is None:
            undated.append(src)
            continue
        d = os.path.dirname(os.path.abspath(src))
        cid = rec.get("ContentIdentifier")
        # A Live Photo lives in one directory, so the pairing key is (dir, ContentIdentifier).
        # Files without one are their own group, keyed by path so they never merge.
        key = ("cid", d, cid) if cid else ("solo", d, os.path.abspath(src))
        buckets[key].append((src, os.path.splitext(src)[1], ts))

    groups, split = [], []
    for (kind, d, ident), members in buckets.items():
        # One stem can hold only one file per extension. Duplicate extensions inside a
        # group cannot share it, so they become their own groups and are reported.
        by_ext = defaultdict(list)
        for m in members:
            by_ext[m[1].lower()].append(m)
        primary, extras = [], []
        for ext, ms in by_ext.items():
            ms.sort(key=lambda m: m[0])
            primary.append(ms[0])
            extras.extend(ms[1:])
        if extras:
            split.append((ident, [m[0] for m in primary], [m[0] for m in extras]))
        for grp in [primary] + [[e] for e in extras]:
            groups.append((d, grp))
    return groups, undated, split


def group_stamp(members):
    """The still's stamp is the shutter moment; prefer it over the motion clip's."""
    stills = [m for m in members if m[1].lower() in STILL_EXT]
    pool = stills or members
    return min(m[2] for m in pool)


def allocate(groups):
    """Assign one stem per group from a per-directory namespace. Deterministic, and
    idempotent: a group already sitting on a valid stem keeps it."""
    plan = {}
    by_dir = defaultdict(list)
    for d, members in groups:
        by_dir[d].append((group_stamp(members), members))

    for d, entries in by_dir.items():
        on_disk = defaultdict(set)          # stem -> set of abs paths present
        try:
            for name in os.listdir(d):
                on_disk[os.path.splitext(name)[0]].add(os.path.join(d, name))
        except OSError:
            pass

        # Deterministic order, independent of current filenames so a rerun does not
        # reshuffle suffixes: stamp, then ContentIdentifier-stable member order.
        entries.sort(key=lambda e: (e[0], sorted(os.path.basename(m[0]) for m in e[1])))

        taken = set()

        def stem_free(stem, members):
            mine = {os.path.abspath(m[0]) for m in members}
            return stem not in taken and not (on_disk.get(stem, set()) - mine)

        # Pass 1: honour groups already correctly named, so reruns are no-ops.
        pending = []
        for stamp, members in entries:
            cur = {os.path.splitext(os.path.basename(m[0]))[0] for m in members}
            stem = next(iter(cur)) if len(cur) == 1 else None
            if stem and re.match(rf"^{stamp}(-\d+)?$", stem) and stem_free(stem, members):
                taken.add(stem)
                plan[id(members)] = (stem, members)
            else:
                pending.append((stamp, members))

        # Pass 2: first free stem for everyone else.
        for stamp, members in pending:
            i, stem = 0, stamp
            while not stem_free(stem, members):
                i += 1
                stem = f"{stamp}-{i}"
            taken.add(stem)
            plan[id(members)] = (stem, members)

    out = []
    for stem, members in plan.values():
        for path, ext, ts in members:
            dst = os.path.join(os.path.dirname(os.path.abspath(path)), stem + ext)
            out.append((path, dst, stem, ts, len(members) > 1))
    out.sort(key=lambda r: (r[2], r[1]))
    return out


def apply_plan(plan):
    """Rename in dependency order, staging only files caught in a cycle.

    A file can move straight to its target unless another file still sits there. So
    renames are ordered: anything whose target is free goes now, and vacating a path
    releases whoever was waiting on it. Only a true cycle (A wants B's name, B wants A's)
    has no such starting point, and only those files detour through a temp name.

    Staging *everything* also worked, but an interrupted run then left the whole folder as
    `<target>.renametmp` -- recoverable, since the intended name is right there in the
    suffix, but alarming and far wider than the problem warrants.

    In fact `allocate()` cannot currently produce a cycle at all: `stem_free()` refuses any
    stem occupied by a file outside the group, so a chosen target is never a path another
    file still holds. The cycle branch is therefore a guard against a future change to the
    allocator, not a routine path -- it is exercised by feeding apply_plan a hand-built
    swap, since no folder layout will provoke one.
    """
    moves = [(os.path.abspath(src), dst) for src, dst, *_ in plan
             if os.path.abspath(src) != dst]
    pending = {src for src, _ in moves}      # paths still holding a file that must move

    ready, waiting = [], defaultdict(list)
    for src, dst in moves:
        (waiting[dst] if dst in pending else ready).append((src, dst))

    done = 0
    while ready:
        src, dst = ready.pop()
        if os.path.exists(dst):              # something unexpected claimed it
            print(f"  SKIPPED (target exists): {src}", file=sys.stderr)
            continue
        os.rename(src, dst)
        done += 1
        pending.discard(src)
        ready.extend(waiting.pop(src, []))   # src is free now; unblock its waiter

    # Anything left is a cycle. Break it by parking every member on a temp name first.
    cycles = [m for ms in waiting.values() for m in ms]
    staged = []
    for src, dst in cycles:
        tmp, n = dst + ".renametmp", 0
        while os.path.exists(tmp):
            n += 1
            tmp = f"{dst}.renametmp{n}"
        os.rename(src, tmp)
        staged.append((tmp, dst, src))
    if staged:
        print(f"  {len(staged)} file(s) staged via temp names to break a rename cycle",
              file=sys.stderr)
    for tmp, dst, src in staged:
        if os.path.exists(dst):
            os.rename(tmp, src)
            print(f"  SKIPPED (target appeared): {src}", file=sys.stderr)
            continue
        os.rename(tmp, dst)
        done += 1
    return done


def main():
    if len(sys.argv) < 4:
        sys.exit("usage: plan_renames.py preview|apply EXIFTOOL PATH [PATH ...]")
    mode, et, paths = sys.argv[1], sys.argv[2], sys.argv[3:]

    records = read_meta(et, paths)
    if not records:
        sys.exit("no readable media files found")
    adopted = pull_in_pair_members(et, paths, records)
    records += adopted
    groups, undated, split = build_groups(records)
    plan = allocate(groups)

    changing = [r for r in plan if os.path.abspath(r[0]) != r[1]]
    # Key on (directory, stem): the stem namespace is per-directory, so the same stem in
    # two folders is two distinct groups and must not collapse into one count.
    pairs = {(os.path.dirname(r[1]), r[2]) for r in plan if r[4]}
    suffixed = sorted(r[2] for r in {(os.path.dirname(r[1]), r[2]): r
                                     for r in plan if re.search(r"-\d+$", r[2])}.values())

    def show(p):
        rel = os.path.relpath(p)
        return rel if not rel.startswith("..") else p

    for src, dst, stem, ts, paired in plan:
        mark = "  [pair]" if paired else ""
        if os.path.abspath(src) == dst:
            print(f"{show(src)}  -->  unchanged (already correct){mark}")
        else:
            print(f"{show(src)}  -->  {show(dst)}{mark}")

    print(f"\n{len(plan)} files in {len(groups)} groups; "
          f"{len(pairs)} Live Photo pair(s) kept on a shared stem.")
    if adopted:
        print(f"{len(adopted)} file(s) not named on the command line were included so a "
              f"Live Photo pair stays together:")
        for rec in adopted:
            print(f"  {show(rec['SourceFile'])}")
    if suffixed:
        print(f"{len(suffixed)} same-second collision(s) suffixed: {', '.join(suffixed)}")
    if undated:
        print(f"\n{len(undated)} file(s) have none of {'/'.join(TS_TAGS)} and were left alone:")
        for p in undated:
            print(f"  {p}")
    if split:
        print(f"\n{len(split)} Live Photo group(s) had two files of the same type; "
              f"only one can hold the shared stem, so the extra(s) got their own:")
        for ident, keep, extra in split:
            print(f"  {ident}: shared={[os.path.basename(k) for k in keep]} "
                  f"separate={[os.path.basename(e) for e in extra]}")

    if mode == "preview":
        print(f"\nPREVIEW ONLY -- nothing changed. {len(changing)} file(s) would be renamed.")
        return

    n = apply_plan(plan)
    for src, dst, stem, ts, paired in plan:
        target = dst if os.path.exists(dst) else src
        if os.path.exists(target):
            # Filesystem mtime only -- lives outside the file, rewrites no image bytes.
            t = ts
            import time
            epoch = time.mktime(time.strptime(t, "%Y%m%d_%H%M%S"))
            os.utime(target, (epoch, epoch))
    print(f"\n{n} file(s) renamed; filesystem mtime stamped to capture time. "
          f"Embedded metadata untouched.")


if __name__ == "__main__":
    main()

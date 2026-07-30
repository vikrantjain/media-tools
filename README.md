# media-tools

A Claude Code plugin with utilities for working with image and video files
(HEIC/HEIF, JPG, PNG, MOV, MP4, and more).

## Hard guarantee: embedded metadata is never modified

No skill here writes the data inside your photos — EXIF, GPS, camera/lens, capture
timestamps are only ever *read*. Originals are touched in exactly two ways, both of
which leave the file's bytes identical: they are **renamed**, and they are **moved**
(`--move` quarantines are filesystem moves). Verified byte-for-byte via md5.

`media-rename` additionally sets the *filesystem* modify-time — the "Date Modified"
your file browser shows, which lives outside the file and is not part of the image.
That is a deliberate feature, and it still rewrites none of the photo's own bytes.

## One-step: `/media-organize [folder]`

Runs the whole pipeline unattended — snapshot, metadata survey, rename, cull, dedup — and
reports at the end. Everything it does is reversible except the rename, which is why step 0
is a **hardlink snapshot** (`cp -al`): instant, consumes no extra disk, and preserves every
original filename, which no undo command can. It works because these tools only rename and
move, never rewrite contents, so the links never diverge.

The snapshot is made as a **sibling** of the target folder, never inside it: `media-rename`
recurses into subdirectories, so an inner snapshot would be renamed too and protect nothing.

Review afterwards via `cull_review.jpg`, `culled/` and `dupes_by_confidence/`, undo whatever
you disagree with, then `rm -rf` the snapshot. If you would rather approve each stage, skip
the command and just ask — the skills below prompt before renaming or moving.

## Skills

### `media-metadata` — read metadata

Extracts EXIF, GPS, camera/lens info, timestamps, dimensions, and video codec/duration.

**Prefers exiftool** — the most complete option, and the only one here that reads GPS embedded
in `.MOV`/`.MP4`. The bootstrap uses a system `exiftool` on PATH if present; otherwise it
downloads the standalone Perl distribution from CPAN **once** (~8 MB), verifies it against a
pinned sha256 before extracting, and caches it under `~/.cache/media-tools` (pure Perl, needs
only `perl`, no root, no compile). Subsequent runs
use the cache with no network. If exiftool truly can't be obtained (no perl and no network), it
falls back — **only after telling you and getting approval** — to a `uv`-run
`pillow-heif`/`pillow` + `ffprobe` path (fewer tags, no video GPS, but fully offline).

Ask naturally: "read the metadata of these images", "which photos have GPS", "build a capture
timeline for this folder".

### `media-rename` — rename by capture timestamp

Renames files to `yyyymmdd_hhmmss.ext` (e.g. `20231202_203640.jpg`) and stamps the filesystem
modified-time to match — **without altering embedded metadata** (file bytes stay identical).
exiftool reads the timestamps via the shared bootstrap (`CreationDate` → `CreateDate` →
`DateTimeOriginal`, highest priority first, with `-api QuickTimeUTC=1` so non-Apple videos
aren't named in UTC); the rename itself is `os.rename` + `os.utime`, so no write path into the
file exists at all. Collisions get `-1`, `-2` suffixes, and there is a preview-then-apply flow.

Live Photo pairs (`.HEIC` + `.MP4`/`.MOV`) are named **as a group**, keyed on Apple's
`ContentIdentifier` rather than on filename or timestamp — because iPhone writes the motion
clip up to a second before the still, and because per-extension collision counters used to
give a pair two different stems. Both split pairs on a real export (5 of 37), and one gave an
unrelated still and clip a shared stem, which would make a later `media-cull`/`media-dedup`
`--move` carry off the wrong video. Now 37/37 survive.

Ask naturally: "rename these photos by the date they were taken", "organize this folder by
timestamp".

### `media-dedup` — find near-duplicate photos

Finds visually near-duplicate images (burst shots, Live Photo stills, re-saves) via
perceptual hashing (phash + dhash) and flags the **lower-quality** copy in each group.
Quality is **resolution first, then sharpness** — sharpness alone is scale-blind, so a
downscaled-and-sharpened messaging-app re-save outscores the 12 MP original it came from and
would otherwise have been kept in its place. Sharpness must also differ by a **minimum margin**
(default 5%, `--min-margin`) before either copy is called worse: measured on a 256×256
downscale it cannot resolve finer than that, and below the margin the verdict flips under
trivial changes to the measurement. Pairs inside the margin are reported as **ties** and never
moved — on a real 122-file export this cut 32 flags to 11 real ones plus 21 ties.
**Read-only by default**; with `--move` it
quarantines flagged copies into confidence-tiered subfolders
(`dupes_by_confidence/1_confident`, `2_likely`, `3_possible`) — a reversible move, **never a
delete**. Keepers stay, and each flagged still's paired Live Photo video travels with it.
`phash ≤10` is a hard ceiling on every tier, so transitively-linked files that don't actually
resemble their keeper are reported as spared rather than flagged. Runs via `uv`
(`pillow-heif`, `pillow`, `imagehash`, `numpy`).

Ask naturally: "find duplicate photos in this folder", "which of these burst shots is the
sharpest", "clean up similar images".

### `media-cull` — find unusable photos

Flags images that are bad **on their own merits**, not merely redundant: `blown` (lens flare
or light leak — a large *contiguous* clipped region with no texture left), `dark` (pocket and
lens-cap shots), `uniform` (blank wall, covered lens), `blurry` (gross smearing). The first
three gates need two signals to agree, so a white shirt under stage lights isn't "blown";
`blurry` is a single signal and the catch-all, so it's the label to double-check.
**Read-only by default**, writing a report plus a **contact sheet** — because "is this photo
bad?" is a visual call a metric can't finish, and the sheet renders HEICs through bundled
libheif even when the desktop viewer can't open them. With `--move` it quarantines into
`culled/<reason>/` — reversible, **never a delete**: a name already quarantined gets a `__1`
suffix rather than being overwritten. Detection is pure numpy/scipy: no model inference,
deterministic, no per-image inference cost at any library size. Scans one folder, not
subdirectories.

Complements `media-dedup` rather than overlapping it — a flare-ruined frame is perfectly
sharp and its perceptual hash lands far outside dedup's grouping threshold, so dedup can
never see it. Cull first, then dedup what remains.

Ask naturally: "find bad photos in this folder", "which of these are overexposed", "cull this
shoot".

## Layout

```
lib/media_common.py        shared by media-cull and media-dedup
skills/<skill>/SKILL.md    what the agent reads
skills/<skill>/scripts/    what it runs
```

`media-cull` and `media-dedup` both quarantine files, move Live Photo pairs and score
sharpness. Those parts live in `lib/media_common.py` rather than being copied into each:
when they *were* copied, the copies drifted, and the fixes for an unsafe undo command and an
unreported collision-rename landed in only one of the two.

## iPhone specifics

Handles HEIC + Live Photo (`.MP4`) pairs and local-vs-UTC timestamp correlation (image
`DateTimeOriginal` is local; ffprobe `creation_time` is UTC; video `CreationDate` carries the
local offset).

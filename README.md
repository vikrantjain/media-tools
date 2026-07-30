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

## Skills

### `media-metadata` — read metadata

Extracts EXIF, GPS, camera/lens info, timestamps, dimensions, and video codec/duration.

**Prefers exiftool** — the most complete option, and the only one here that reads GPS embedded
in `.MOV`/`.MP4`. The bootstrap uses a system `exiftool` on PATH if present; otherwise it
downloads the standalone Perl distribution from CPAN **once** (~7 MB) and caches it under
`~/.cache/media-tools` (pure Perl, needs only `perl`, no root, no compile). Subsequent runs
use the cache with no network. If exiftool truly can't be obtained (no perl and no network), it
falls back — **only after telling you and getting approval** — to a `uv`-run
`pillow-heif`/`pillow` + `ffprobe` path (fewer tags, no video GPS, but fully offline).

Ask naturally: "read the metadata of these images", "which photos have GPS", "build a capture
timeline for this folder".

### `media-rename` — rename by capture timestamp

Renames files to `yyyymmdd_hhmmss.ext` (e.g. `20231202_203640.jpg`) and stamps the filesystem
modified-time to match — **without altering embedded metadata** (file bytes stay identical).
Uses exiftool's native date-based rename via the shared bootstrap: local-time source
(`DateTimeOriginal` → `CreateDate` → `CreationDate`), `-c` collision suffixes, and a
preview-then-apply flow. Live Photo pairs (`.HEIC` + `.MP4`/`.MOV`) keep a shared base name.

Ask naturally: "rename these photos by the date they were taken", "organize this folder by
timestamp".

### `media-dedup` — find near-duplicate photos

Finds visually near-duplicate images (burst shots, Live Photo stills, re-saves) via
perceptual hashing (phash + dhash) and flags the **lower-quality** copy in each group by a
sharpness score. **Read-only by default**; with `--move` it quarantines flagged copies into
confidence-tiered subfolders (`dupes_by_confidence/1_confident`, `2_likely`, `3_possible`) —
a reversible move, **never a delete**. Keepers (the sharpest copy) stay, and each flagged
still's paired Live Photo video travels with it. Runs via `uv` (`pillow-heif`, `pillow`,
`imagehash`, `numpy`).

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

## iPhone specifics

Handles HEIC + Live Photo (`.MP4`) pairs and local-vs-UTC timestamp correlation (image
`DateTimeOriginal` is local; ffprobe `creation_time` is UTC; video `CreationDate` carries the
local offset).

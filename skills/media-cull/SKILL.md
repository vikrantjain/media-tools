---
name: media-cull
description: >
  Find photos that are bad on their own merits — blown-out flare, accidental pocket
  shots, blank frames, gross motion blur — and quarantine them into reason-named
  subfolders for review, never deleting. Pure numpy/scipy detection (no model
  inference), plus a contact sheet so the final judgment is visual. Use when a task
  is to find junk/bad/unusable photos, cull a shoot, or clean up a folder of
  throwaway shots. Handles HEIC/JPG/PNG and Live Photo video pairs.
---

# Media Cull

Flag images that are unusable in isolation. **Read-only by default** — it writes a
report and a contact sheet, and only moves files when explicitly asked with
`--move`. It **never deletes**; quarantining is a reversible move into subfolders.

## How this differs from media-dedup

They catch disjoint problems and neither substitutes for the other:

| | media-dedup | media-cull |
|---|---|---|
| Question | "is this a redundant copy?" | "is this photo bad?" |
| Needs a sibling | yes — flags the *worse of a pair* | no — judges each file alone |
| Typical catch | burst frames, re-saves | flare, pocket shot, blank wall |

A flare-ruined frame is invisible to media-dedup for two reasons: it is perfectly
*sharp*, and the flare shifts its perceptual hash so far (measured: phash 22 from a
clean sibling, against a grouping threshold of 10) that dedup never even groups it.
Run both.

## Reasons

The first three gates require **two signals** to agree, so a legitimately bright
subject is not "blown" and a deliberately low-key shot is not "dark". `blurry` is
the exception — one signal, and the last branch, so anything not caught above falls
into it. That makes `blurry` the least trustworthy label of the four; see the limits
at the bottom.

| Reason | Fires when | Catches |
|---|---|---|
| `blown` | largest contiguous clipped region ≥15% **and** clipped-plus-textureless ≥10% | lens flare, light leak, blowout |
| `dark` | ≥60% crushed to black **and** mean luma <35 | pocket shots, lens-cap frames |
| `uniform` | ≥90% locally flat **and** global stdev <12 | blank wall, ceiling, covered lens |
| `blurry` | variance-of-Laplacian <250 (single signal) | gross smearing |

For `blown` and `dark` the two signals really are close to independent. For
`uniform` they are weaker than that — local flatness and global stdev both measure
absence of variation — but it errs toward under-firing, which is the safe direction.

**Contiguity is what makes `blown` work.** A white shirt under stage lights and a
lens flare can clip the same *number* of pixels; only the flare clips them in one
connected mass. Measuring the largest blob rather than the total is what separates
them — a real folder produced exactly one true positive and zero false positives.

## Usage

System `python3` usually lacks the imaging libs, so run via `uv` (do **not** `pip install`):

```bash
# 1) Read-only: report + contact sheet, originals untouched:
uv run --with pillow-heif --with pillow --with numpy --with scipy python3 \
  "${CLAUDE_PLUGIN_ROOT}/skills/media-cull/scripts/cull.py" /path/to/photos

# 2) After reviewing the sheet, quarantine into culled/<reason>/ (reversible):
uv run --with pillow-heif --with pillow --with numpy --with scipy python3 \
  "${CLAUDE_PLUGIN_ROOT}/skills/media-cull/scripts/cull.py" /path/to/photos --move
```

`--stats` prints metrics for every file (useful for tuning or explaining a call).
`--sheet PATH` redirects the contact sheet; `--sheet -` skips it.

Scans **the given folder only, not subdirectories** — point it at the folder that
actually holds the images, not a parent like `DCIM/`, or it will report 0 scanned.

**Always run the read-only pass first and have the user look at the contact sheet
before `--move`.** The bar here is higher than media-dedup's: a quarantined
duplicate still has a surviving keeper, but every file cull flags is *unique*, so a
false positive removes a one-of-a-kind photo from the folder.

## Why a contact sheet rather than just a report

"Is this photo bad?" is a visual judgment a metric cannot finish. A line reading
`20260420_005505.HEIC — blob 6.8%` is unreviewable; the image turns out to be a
fine portrait whose "blob" is a white shirt plus string lights.

The sheet renders through **pillow-heif's bundled libheif**, which matters on
systems whose libheif is too old for iPhone 16/17 `tmap` HEICs — those files show up
on the sheet even when the desktop image viewer refuses to open them. That is why
review is a generated sheet and not "move them and browse the folder".

Sheets hold 40 images each and paginate past that (`cull_review_001.jpg`,
`cull_review_002.jpg`, …) — one unbounded sheet would be both unreviewable and,
past ~650 images, too tall for Pillow to save at all.

## Behavior notes

- **Detection is deterministic** — numpy/scipy only, no model inference. Same answer
  every run, at any library size, with no per-image inference cost. This is
  deliberate: the tool moves files, so decisions must be reproducible and auditable.
- **Live Photo pairs**: a flagged still's same-stem `.MP4`/`.MOV` moves with it.
- **Never overwrites**: if a file of that name is already quarantined from an earlier
  run, the new one lands as `name__1.ext` and the run says so. Overwriting would be
  a silent delete, which this tool does not do.
- **Reversible**: undo with `mv -n culled/*/* . && rmdir culled/* culled`. The `-n`
  keeps a restore from clobbering a same-named file that is back in the folder, and
  `rmdir` refuses to remove a non-empty directory — so anything `mv` skipped stays
  in `culled/` rather than being deleted. To actually delete a reviewed category,
  `rm -rf culled/<reason>`.
- **Known limits**, worth saying out loud rather than discovering later:
  - Sharpness is measured after a 256×256 downscale, so **mild blur is invisible**
    by design — only gross smearing trips `blurry`.
  - Low-detail scenes (fog, plain backdrops, minimalist compositions) can score low
    on `blurry`/`uniform` honestly, and `blurry` has no second signal to catch the
    mistake — a genuinely sharp but smooth frame is indistinguishable from a blurred
    detailed one by this metric. That is what the contact sheet is for.
  - `dark` and `uniform` thresholds are set conservatively and were validated only
    against folders containing no such files — they are calibrated not to
    false-positive, which means they may under-fire. Loosen if a real folder
    proves them too shy.
- Pairs well with **media-dedup**: cull the junk first, then dedup what remains, so
  you are not choosing a "keeper" from among frames that were all unusable.

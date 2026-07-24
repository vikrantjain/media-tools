---
name: media-dedup
description: >
  Find visually near-duplicate photos (burst shots, Live Photo stills, re-saves) in a
  folder and quarantine the lower-quality copies into confidence-tiered subfolders for
  review — never deleting. Uses perceptual hashing (phash + dhash) plus a sharpness
  score. Use when a task is to find similar/duplicate images, clean up a photo folder,
  or pick the best frame from a burst. Handles HEIC/JPG/PNG and Live Photo video pairs.
---

# Media Dedup

Detect near-duplicate images and set aside the weaker copies. **Read-only by default** —
it reports groups and only moves files when explicitly asked with `--move`. It **never
deletes**; quarantining is a reversible move into subfolders.

## What counts as a removable duplicate

A copy is flagged only when **both** are true:

1. **Near-identical** to another image in the same group (perceptual match), and
2. **Lower quality** than that image — softer, by variance-of-Laplacian sharpness
   (ties are kept, not flagged).

The single sharpest copy in each group is the **keeper** and is never moved.

## Confidence tiers

Files are bucketed by how strict the visual match to the keeper is, so the user can
review from least-certain to most-certain:

| Tier | Match strictness | Folder |
|------|------------------|--------|
| 1 | phash ≤5 **and** dhash ≤5 (near-perfect) | `1_confident` |
| 2 | phash ≤8 **and** dhash ≤8 (strong)        | `2_likely`    |
| 3 | phash ≤10 (single hash; loose net)        | `3_possible`  |

Requiring **two** independent hashes to agree (phash + dhash) at the tighter tiers keeps
genuine burst shots — different moments that merely look alike — from being flagged.

## Usage

System `python3` usually lacks the imaging libs, so run via `uv` (do **not** `pip install`):

```bash
# 1) Read-only report — see the tiers, move nothing:
uv run --with pillow-heif --with pillow --with imagehash --with numpy python3 \
  "${CLAUDE_PLUGIN_ROOT}/skills/media-dedup/scripts/dedup.py" /path/to/photos

# 2) Quarantine the flagged copies into dupes_by_confidence/<tier>/ (reversible):
uv run --with pillow-heif --with pillow --with imagehash --with numpy python3 \
  "${CLAUDE_PLUGIN_ROOT}/skills/media-dedup/scripts/dedup.py" /path/to/photos --move
```

**Always run the read-only report first and show the user the tiers before `--move`.**

## Behavior notes

- **Live Photo pairs**: when a flagged `.HEIC`/`.JPG` still has a same-stem `.MP4`/`.MOV`,
  that video is moved with it so the pair stays together. Videos of *keepers* stay put.
- **Videos are not hashed**: only still images are compared. Standalone `.MOV`/`.MP4`
  clips (not Live Photo stills) are ignored by the dedup itself.
- **Reversible**: undo everything with
  `mv dupes_by_confidence/*/* . && rm -rf dupes_by_confidence`. Delete a reviewed tier
  with `rm -rf dupes_by_confidence/1_confident`.
- **Sharpness is a proxy, not judgment**: in near-tie groups the "worse" copy may be only
  marginally softer — that's why files land in review folders rather than being deleted.
- Best paired with the **media-rename** skill: rename first so duplicates sort together
  by capture time, then dedup.

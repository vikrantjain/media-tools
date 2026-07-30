---
name: media-dedup
description: >
  Find visually near-duplicate photos (burst shots, Live Photo stills, re-saves) in a
  folder and quarantine the lower-quality copies into confidence-tiered subfolders for
  review — never deleting. Uses perceptual hashing (phash + dhash) plus a sharpness
  score, with resolution taking precedence so an original is never discarded in favour
  of a downscaled re-save, and a minimum quality margin so two equally good burst frames
  are reported as a tie rather than one being picked arbitrarily. Use when a task is to
  find similar/duplicate images, clean up a photo folder, or pick the best frame from a
  burst. Handles HEIC/JPG/PNG and Live Photo video pairs.
---

# Media Dedup

Detect near-duplicate images and set aside the weaker copies. **Read-only by default** —
it prints groups to stdout and writes no files at all; it only moves anything when
explicitly asked with `--move`. It **never deletes**; quarantining is a reversible move
into subfolders.

## What counts as a removable duplicate

A copy is flagged only when **all** of these hold:

1. **Near-identical** to the keeper of its group — phash within 10 (see tiers), and
2. **Lower quality by a margin the metric can actually resolve** (default 5%), and
3. **Not higher-resolution** than that keeper.

Close matches that fail only #2 are reported under **“too close to call”** and are
never moved.

### Quality means resolution first, then sharpness

Within one resolution class, the softer copy (variance-of-Laplacian) is the weaker one
and ties are kept. Across classes, **the downscaled copy is always the weaker one**,
however sharp it measures.

That ordering is load-bearing, not a nicety. Sharpness here is scale-blind — both images
are squashed to 256×256 before it is measured — so a messaging-app re-save, which is
downscaled *and* unsharp-masked, outscores the original it came from. Measured on a real
pair: a 1152×864 re-save scored **285.4** against its own 4032×3024 original's **111.2**.
Ranking on sharpness alone therefore crowned the 1 MP re-save as keeper and quarantined
the 12 MP original — the exact inversion of the tool's purpose. Resolution gets the
deciding vote, and a candidate more than 1.3× the keeper's megapixels is never flagged
at all, as a backstop.

The best copy in each group is the **keeper** and is never moved.

### A quality difference too small to measure is not a quality difference

Sharpness is measured on a 256×256 downscale — on a 3520×1980 photo, ~97% of the pixels
are gone before the number exists. It resolves *smeared vs sharp*, nothing finer.

The comparison used to have no floor, so it named a loser whichever way the numbers fell.
On a burst-heavy folder that is most of the output: of 32 flags on a real 122-file export,
**21 rested on a gap under 5% and 6 under 1%** — the narrowest a pair of showroom frames at
2783.6 vs 2780.7 (0.10%), two visibly different moments, both perfectly sharp.

Those verdicts were not merely close, they were **unstable**. Re-measuring at a 252px box
instead of 256 — an implementation detail with no bearing on photo quality — reversed 2 of
6 near-ties tested, swapping keeper and discard. Which frame got quarantined was a coin
flip.

So a gap under `--min-margin` (default **5%**) now makes the pair a **tie**: reported with
both scores, flagged as neither, moved never. On that folder this took 32 flags down to 11,
each with a real margin, and surfaced the other 21 as ties. `--min-margin 0` restores the
old always-pick-a-loser behaviour.

**Resolution differences bypass the margin entirely** — a downscaled re-save is the worse
copy however it scores, because that is a class difference, not a margin. Verified: a
re-save measuring 7266.8 against its 6.97 MP original's 2783.6 (2.6× *sharper*) is still
flagged, and the original is still the keeper.

## Confidence tiers

Tiers rank **how strict the visual match is**, not how confident the verdict is. A file in
`1_confident` is one the tool is confident *looks like* its keeper; whether it is the worse
copy is the separate question the margin above answers. Read the tier as "how alike", not
"how safe to delete".

| Tier | Match strictness | Folder |
|------|------------------|--------|
| 1 | phash ≤5 **and** dhash ≤5 (near-perfect) | `1_confident` |
| 2 | phash ≤8 **and** dhash ≤8 (strong)        | `2_likely`    |
| 3 | phash ≤10 (single hash; loose net)        | `3_possible`  |

Requiring **two** independent hashes to agree (phash + dhash) at the tighter tiers keeps
genuine burst shots — different moments that merely look alike — from being flagged.

**phash ≤10 is a hard ceiling on every tier, including 3.** Grouping is transitive
(union-find over pairs within 10), so a group can chain outward: on a pan sequence,
members measured phash **18** from their group's keeper. Tier 3 used to be the `else`
branch with no bound, so those were flagged as "possible" duplicates of a photo they did
not actually resemble. Anything past the ceiling is now left alone and reported as
`spared … linked to their group only transitively`.

## Usage

System `python3` usually lacks the imaging libs, so run via `uv` (do **not** `pip install`):

```bash
# 1) Read-only report — see the tiers, move nothing, write nothing:
uv run --with pillow-heif --with pillow --with imagehash --with numpy python3 \
  "${CLAUDE_PLUGIN_ROOT}/skills/media-dedup/scripts/dedup.py" /path/to/photos

# 2) Quarantine the flagged copies into dupes_by_confidence/<tier>/ (reversible):
uv run --with pillow-heif --with pillow --with imagehash --with numpy python3 \
  "${CLAUDE_PLUGIN_ROOT}/skills/media-dedup/scripts/dedup.py" /path/to/photos --move
```

`--stats` prints megapixels, size, sharpness and phash for every file — useful for
explaining why a particular copy was chosen as the keeper. `--min-margin PCT` sets the
quality gap required to flag (default 5; `0` reproduces the pre-margin behaviour).

**Always run the read-only report first and show the user the tiers before `--move`.**

Scans **the given folder only, not subdirectories**.

## Behavior notes

- **Live Photo pairs**: when a flagged `.HEIC`/`.JPG` still has a same-stem `.MP4`/`.MOV`,
  that video is moved with it so the pair stays together (extension matched
  case-insensitively). Videos of *keepers* stay put.
- **Videos are not hashed**: only still images are compared. Standalone `.MOV`/`.MP4`
  clips (not Live Photo stills) are ignored by the dedup itself.
- **Never overwrites**: if a file of that name is already quarantined from an earlier
  run, the new one lands as `name__1.ext` and **the run says so**. A suffixed file will
  not be restored to its original name by the undo command, so that has to be reported
  rather than done quietly.
- **Reversible**: undo everything with

  ```bash
  mv -n dupes_by_confidence/*/* . && rmdir dupes_by_confidence/* dupes_by_confidence
  ```

  The `-n` keeps a restore from clobbering a same-named file that is back in the folder,
  and `rmdir` refuses to remove a non-empty directory — so anything `mv` skipped stays in
  the quarantine folder instead of being deleted. The bare `mv … && rm -rf` form does
  both: it silently overwrote the file in the folder and then deleted the evidence. To
  actually delete a reviewed tier, `rm -rf dupes_by_confidence/1_confident`.
- **Sharpness is a proxy, not judgment**: it says which frame carries more fine detail, not
  which is the better photo — the sharper burst frame can be the one where someone blinked.
  Pairs whose scores are within the margin are now held back as ties instead of being
  ranked, but a flagged file is still a *suggestion*, which is why these land in review
  folders rather than being deleted. Unlike `media-cull` there is no contact sheet, so
  `--stats` plus opening the pair is how you check a call you doubt.
- **A tie is not "no duplicate found"** — it means the two really do look alike and the tool
  is declining to pick, usually because they are distinct moments in a burst. Deciding
  between them is a visual call; the tool has told you where to look.
- **Ignores media-cull's contact sheets** (`cull_review*.jpg`, and any file carrying the
  sheet marker), since the recommended order is cull first, then dedup — which leaves a
  sheet sitting in this tool's input folder.
- **Cost**: the pairwise comparison is O(n²) — about 4 minutes of hash comparison at
  10,000 images, on top of decode time. Progress is reported on stderr for runs over 200
  files, so a long scan doesn't look like a hung process.
- Best paired with **media-rename** (rename first so duplicates sort together by capture
  time) and with **media-cull** (cull the junk first, so you are not picking a keeper from
  among frames that were all unusable).

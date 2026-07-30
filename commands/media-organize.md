---
description: Snapshot, then rename by capture date, cull unusable shots and dedup near-duplicates in one pass
argument-hint: "[folder] (defaults to the current directory)"
---

Organize a folder of photos and videos end to end using the media-tools skills, without
stopping to ask. Target folder: `$ARGUMENTS` — if empty, use the current working directory.

Run the steps in the order below. Do not reorder them and do not skip the snapshot. Report
progress as you go; the user is not being asked to approve anything mid-run, so the final
report is their only chance to see what happened.

## Step 0 — snapshot (never skip; everything else depends on it)

The rename is the one step in this pipeline that **cannot be undone**: original filenames
live nowhere inside the file, so once `IMG_1618.JPG` becomes `20260418_130630.JPG` the old
name is gone. Quarantine moves are reversible; the rename is not. The snapshot is what makes
running unattended reasonable.

```bash
SRC=<absolute path to the target folder>
SNAP="${SRC%/}.snapshot-$(date +%Y%m%d-%H%M%S)"
cp -al "$SRC" "$SNAP"
```

This is a **hardlink** copy: instant, and it consumes essentially no extra disk regardless of
folder size. It is safe here for a specific reason — these tools only ever rename and move,
never rewrite file contents — so the links never diverge from the originals.

Two hard requirements:

- **The snapshot must be a SIBLING of the target folder, never inside it.** `media-rename`
  recurses into subdirectories, so a snapshot placed inside would be renamed along with the
  originals and would protect nothing.
- **Verify it before touching anything.** Compare file counts, and abort the whole run if
  they differ or `cp -al` failed. A snapshot that silently did not happen is worse than no
  snapshot, because the rest of this command assumes it exists.

If the filesystem does not support hardlinks (`cp -al` fails), fall back to a real copy
`cp -a` if there is room; if there is not, **stop and tell the user** rather than proceeding
unprotected.

## Step 1 — survey the metadata

Use the **media-metadata** skill. Bootstrap exiftool and report: file count by type, capture
date range, devices, GPS coverage, and **any files lacking a usable capture timestamp** —
those will not be renamed, so call them out explicitly.

## Step 2 — rename by capture timestamp

Use the **media-rename** skill. Run `preview` first to see the plan, then `apply` — the
preview is for your own check here, not a user prompt.

Report anything the preview flags: files with no timestamp (left alone), same-second
collisions that got `-1`/`-2` suffixes, files pulled in uninvited to keep a Live Photo pair
together, and Live Photo groups that had two files of the same type.

## Step 3 — cull unusable shots

Use the **media-cull** skill with `--move`. It writes a contact sheet and quarantines into
`culled/<reason>/`.

State clearly in the report that **every file cull flags is unique** — unlike a duplicate,
there is no surviving sibling — so this is the category most worth the user's eye. Name each
flagged file and its reason, and point at the contact sheet.

## Step 4 — dedup near-duplicates

Use the **media-dedup** skill with `--move`. It quarantines into
`dupes_by_confidence/<tier>/`.

Report the tiers, and report the **"too close to call"** ties separately — those are pairs
that look alike but whose quality difference is too small to measure, so nothing was moved
for them. Make clear that a tier describes how *alike* two files look, not how safe the
verdict is.

## Final report

Give the user, in this order:

1. **The snapshot path**, and the fact that it holds every original filename and byte.
2. What was renamed, culled and deduped, with counts.
3. The undo commands, verbatim, run from inside the target folder:
   ```
   mv -n culled/*/* . && rmdir culled/* culled
   mv -n dupes_by_confidence/*/* . && rmdir dupes_by_confidence/* dupes_by_confidence
   ```
   Note that these restore files under their **new timestamp names**, and that undoing a
   cull means dedup never considered those files — so re-run dedup afterwards if they
   restore anything.
4. How to discard the snapshot once satisfied: `rm -rf <snapshot path>`.

## Invariants

- **Never delete anything.** No `rm` on any original, at any point, for any reason.
- **Never modify data inside a photo.** EXIF/GPS/timestamps are read-only. Originals are only
  renamed and moved; both leave bytes identical.
- If any step fails, **stop** and report which step, with the snapshot path prominent — do
  not press on to the next step with the folder half-processed.

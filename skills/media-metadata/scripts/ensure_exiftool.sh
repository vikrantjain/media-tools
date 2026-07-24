#!/usr/bin/env bash
# Ensure exiftool is available; print its path on stdout.
# Order: existing PATH binary -> cached download -> fetch from CPAN (once, then cached).
# The standalone distro is pure Perl: needs only `perl`, no root, no compile.
set -euo pipefail

VER="13.30"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/media-metadata"
DEST="$CACHE/Image-ExifTool-$VER"
BIN="$DEST/exiftool"

# 1. Already on PATH? Prefer a system install.
if command -v exiftool >/dev/null 2>&1; then
  command -v exiftool
  exit 0
fi

# 2. Previously downloaded + cached?
if [ -x "$BIN" ]; then
  echo "$BIN"
  exit 0
fi

# 3. Need perl to run the standalone distro.
if ! command -v perl >/dev/null 2>&1; then
  echo "ERROR: no exiftool and no perl to run the standalone distribution." >&2
  echo "Fall back to the PIL/ffprobe path (read_meta.py)." >&2
  exit 1
fi

# 4. Download + extract from CPAN on first use; cached for subsequent runs.
mkdir -p "$CACHE"
URL="https://cpan.metacpan.org/authors/id/E/EX/EXIFTOOL/Image-ExifTool-$VER.tar.gz"
TARBALL="$CACHE/Image-ExifTool-$VER.tar.gz"
if ! curl -sfL --connect-timeout 10 --max-time 120 "$URL" -o "$TARBALL"; then
  echo "ERROR: download failed/timed out. Fall back to read_meta.py, or retry." >&2
  rm -f "$TARBALL"
  exit 1
fi
tar xzf "$TARBALL" -C "$CACHE"
rm -f "$TARBALL"

if [ -x "$BIN" ]; then
  echo "$BIN"
else
  echo "ERROR: extraction did not yield an exiftool binary." >&2
  exit 1
fi

#!/usr/bin/env bash
# Ensure exiftool is available; print its path on stdout (diagnostics go to stderr).
# Order: existing PATH binary -> cached download -> fetch from CPAN (once, then cached).
# The standalone distro is pure Perl: needs only `perl`, no root, no compile.
set -euo pipefail

VER="13.55"
# sha256 of Image-ExifTool-$VER.tar.gz as published in CPAN's CHECKSUMS for author
# EXIFTOOL, cross-checked against a fresh download. This tarball is Perl source
# that this script then executes, so it is verified before extraction rather than
# trusted because it arrived over TLS. Bump both fields together.
SHA256="5f4c81d34ad406538c2871ad72dbfceb5d9b412b2f16cbbeb4d712d270846667"

CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/media-tools"
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

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | awk '{print $NF}'
  else
    return 1
  fi
}

# 4. Download + verify + extract on first use; cached for subsequent runs.
mkdir -p "$CACHE"
URL="https://cpan.metacpan.org/authors/id/E/EX/EXIFTOOL/Image-ExifTool-$VER.tar.gz"
TMP="$(mktemp -d "$CACHE/.dl.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
TARBALL="$TMP/exiftool.tar.gz"

if ! curl -sfL --connect-timeout 10 --max-time 300 "$URL" -o "$TARBALL"; then
  echo "ERROR: download failed/timed out: $URL" >&2
  echo "       If this is a 404, the release was pruned -- CPAN keeps only recent" >&2
  echo "       versions listed. Check https://exiftool.org for the current version" >&2
  echo "       and update VER + SHA256 at the top of this script." >&2
  echo "       Otherwise retry, or fall back to read_meta.py." >&2
  exit 1
fi

if GOT="$(sha256_of "$TARBALL")"; then
  if [ "$GOT" != "$SHA256" ]; then
    echo "ERROR: checksum mismatch for Image-ExifTool-$VER.tar.gz" >&2
    echo "       expected $SHA256" >&2
    echo "       got      $GOT" >&2
    echo "       Refusing to extract or run it." >&2
    exit 1
  fi
elif [ "${MEDIA_TOOLS_SKIP_CHECKSUM:-}" = "1" ]; then
  echo "WARNING: no sha256 tool available; proceeding unverified because" >&2
  echo "         MEDIA_TOOLS_SKIP_CHECKSUM=1 is set." >&2
else
  echo "ERROR: found no sha256sum, shasum or openssl to verify the download." >&2
  echo "       The tarball is Perl source that would then be executed, so it is" >&2
  echo "       not run unverified. Install one of those tools, or set" >&2
  echo "       MEDIA_TOOLS_SKIP_CHECKSUM=1 to accept the risk." >&2
  exit 1
fi

# Extract into the temp dir and move the finished tree into place, so an
# interrupted run cannot leave a half-extracted cache that step 2 would trust.
tar xzf "$TARBALL" -C "$TMP"
if [ ! -x "$TMP/Image-ExifTool-$VER/exiftool" ]; then
  echo "ERROR: extraction did not yield an exiftool binary." >&2
  exit 1
fi
rm -rf "$DEST"
mv "$TMP/Image-ExifTool-$VER" "$DEST"

if [ -x "$BIN" ]; then
  echo "$BIN"
else
  echo "ERROR: exiftool missing after install to $DEST." >&2
  exit 1
fi

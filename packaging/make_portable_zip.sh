#!/bin/bash
# Assemble a portable "drop-in" zip of smdegrain_bis for a host that does NOT use
# pip (e.g. StaxRip and other GUIs that bundle their own VapourSynth). One zip per
# (OS, arch). The pure-Python package is platform-independent; the only per-platform
# piece is the mvuscale plugin binary (needed for UHDhalf only).
#
# Usage:
#   MVUSCALE_BIN=/path/to/mvuscale.{so,dll,dylib} OS=windows ARCH=x64 \
#     packaging/make_portable_zip.sh
#
# Env:
#   MVUSCALE_BIN  path to the built mvuscale binary for this platform (optional; if
#                 omitted the zip is Python-only and UHDhalf will be unavailable).
#   OS            os tag for the filename: linux | windows | macos (default: uname).
#   ARCH          arch tag: x64 | arm64 (default: uname -m mapped).
#   OUT           output directory (default: dist/).
# The mvuscale binary is NOT bundled from PyPI here — CI passes the artifact it just
# built (native/, cibuildwheel). mvutensils is deliberately NOT bundled (it is a
# separate GPL project); the INSTALL notes list it as a prerequisite.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VER="$(grep -oE '__version__[[:space:]]*=[[:space:]]*"[^"]+"' smdegrain_bis/__init__.py | grep -oE '[0-9][^"]*')"
[ -n "$VER" ] || { echo "could not read __version__" >&2; exit 1; }

uname_os="$(uname -s)"
case "${OS:-}" in
  "") case "$uname_os" in Linux) OS=linux;; Darwin) OS=macos;; MINGW*|MSYS*|CYGWIN*) OS=windows;; *) OS="$(echo "$uname_os" | tr 'A-Z' 'a-z')";; esac;;
esac
case "${ARCH:-}" in
  "") case "$(uname -m)" in x86_64|amd64) ARCH=x64;; aarch64|arm64) ARCH=arm64;; *) ARCH="$(uname -m)";; esac;;
esac
OUT="${OUT:-$ROOT/dist}"
mkdir -p "$OUT"

stage="$(mktemp -d)"; trap 'rm -rf "$stage"' EXIT
pkg="$stage/smdegrain_bis-$VER-$OS-$ARCH"
mkdir -p "$pkg"

# 1. pure-Python package (no __pycache__)
cp -r smdegrain_bis "$pkg/smdegrain_bis"
find "$pkg/smdegrain_bis" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$pkg/smdegrain_bis" -name '*.pyc' -delete 2>/dev/null || true

# 2. native mvuscale plugin (optional — UHDhalf only)
have_plugin="no"
if [ -n "${MVUSCALE_BIN:-}" ]; then
  [ -f "$MVUSCALE_BIN" ] || { echo "MVUSCALE_BIN not found: $MVUSCALE_BIN" >&2; exit 1; }
  mkdir -p "$pkg/vapoursynth-plugins/mvuscale"
  cp "$MVUSCALE_BIN" "$pkg/vapoursynth-plugins/mvuscale/$(basename "$MVUSCALE_BIN")"
  have_plugin="yes"
fi

# 3. licences
cp LICENSE "$pkg/LICENSE"
cp smdegrain_bis/vendor/NOTICE "$pkg/NOTICE-vendor.txt"

# 4. install notes
{
  echo "smdegrain_bis $VER — portable drop-in package ($OS-$ARCH)"
  echo "=================================================================="
  echo
  echo "A motion-compensated temporal denoiser for VapourSynth (core.mvu)."
  echo "GPL-3.0-or-later. Source: https://github.com/FlorianGamper/vs-smdegrain-bis"
  echo
  echo "REQUIREMENTS (install separately — NOT bundled here):"
  echo "  * VapourSynth (API4 / R55+)."
  echo "  * vapoursynth-mvutensils >= 4   (the core.mvu backend). Get it via your"
  echo "    host's plugin manager (vsrepo), 'pip install vapoursynth-mvutensils', or"
  echo "    its GitHub releases. LFR is only thread-safe on v4+."
  echo "  * Optional prefilters: vsrgtools, vs-dfttest2 (imported lazily; skip if unused)."
  echo
  echo "INSTALL:"
  echo "  1. Copy the 'smdegrain_bis/' folder into a directory on your VapourSynth"
  echo "     Python's import path (its site-packages, or your host's scripts folder)."
  if [ "$have_plugin" = yes ]; then
  echo "  2. (UHDhalf only) Copy 'vapoursynth-plugins/mvuscale/*' into your VapourSynth"
  echo "     plugins autoload directory. Skip this if you never use UHDhalf."
  else
  echo "  2. (UHDhalf) This zip has no mvuscale binary — build it from native/ or get"
  echo "     the vapoursynth-mvuscale wheel if you need the UHDhalf path."
  fi
  echo "  3. In your script:   from smdegrain_bis import SMDegrain"
  echo
  echo "HOST NOTE (e.g. StaxRip and similar GUIs that bundle VapourSynth): drop the"
  echo "mvuscale plugin into the host's VapourSynth plugins/autoload folder and the"
  echo "'smdegrain_bis' package where the host's embedded Python imports modules."
  echo "Consult the host's documentation for those exact locations."
  echo
  echo "LICENCES: LICENSE (this package, GPL-3.0-or-later); NOTICE-vendor.txt (bundled"
  echo "Selur vendor code). mvutensils is GPL-2.0-or-later and obtained separately."
} > "$pkg/INSTALL.txt"

# 5. zip it — use `zip` if present, else python's stdlib zipfile (always available)
out_zip="$OUT/smdegrain_bis-$VER-$OS-$ARCH.zip"
rm -f "$out_zip"
base="$(basename "$pkg")"
if command -v zip >/dev/null; then
  ( cd "$stage" && zip -rq "$out_zip" "$base" )
else
  ( cd "$stage" && python3 -m zipfile -c "$out_zip" "$base" )
fi
echo "wrote $out_zip"
echo "  plugin bundled: $have_plugin"
python3 -m zipfile -l "$out_zip" | sed 's/^/    /'

#!/bin/bash
# Build the optional mvuscale native filter (fast UHDhalf vector scaler).
# Portable x86-64 baseline (SSE2) — no AVX runtime requirement.
#
#   VS_INCLUDE=/path/to/vapoursynth/headers OUT=/dest/dir native/build.sh
#
# Writes libmvuscale.so next to this script by default; copy it into your
# VapourSynth plugins directory, or set OUT to build straight into one.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# VapourSynth API4 headers (VapourSynth4.h, VSHelper4.h) — vendored under
# native/include/ so this builds with no VapourSynth SDK installed. Override
# VS_INCLUDE to point at a system copy if you prefer.
VS_INCLUDE="${VS_INCLUDE:-$HERE/include}"
# Default to this directory: it is the only destination that means the same
# thing in every checkout. Anything clone-relative beyond it is a guess about
# the caller's tree, and `mkdir -p` below would silently create it.
OUT="${OUT:-$HERE}"
CXX="${CXX:-g++}"
mkdir -p "$OUT"
set -x
"$CXX" -std=c++17 -O3 -fPIC -shared -Wall -fvisibility=hidden \
  -I"$VS_INCLUDE" "$HERE/mvuscale.cpp" -o "$OUT/libmvuscale.so"

#!/bin/bash
# Build the optional mvuscale native filter (fast UHDhalf vector scaler).
# Portable x86-64 baseline (SSE2) — no AVX runtime requirement.
#
#   VS_INCLUDE=/path/to/vapoursynth/headers OUT=/dest/dir native/build.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# VapourSynth API4 headers (VapourSynth4.h, VSHelper4.h) — vendored under
# native/include/ so this builds with no VapourSynth SDK installed. Override
# VS_INCLUDE to point at a system copy if you prefer.
VS_INCLUDE="${VS_INCLUDE:-$HERE/include}"
OUT="${OUT:-$HERE/../tests/_plugins/mvuscale}"
CXX="${CXX:-g++}"
mkdir -p "$OUT"
set -x
"$CXX" -std=c++17 -O3 -fPIC -shared -Wall -fvisibility=hidden \
  -I"$VS_INCLUDE" "$HERE/mvuscale.cpp" -o "$OUT/libmvuscale.so"

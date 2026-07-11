# vapoursynth-mvuscale

A tiny native **VapourSynth (API4)** filter that scales
[mvutensils](https://github.com/myrsloik/mvutensils) motion vectors between
resolutions — the `manipmv.ScaleVect` replacement used by the
[`smdegrain-bis`](https://github.com/FlorianGamper/vs-smdegrain-bis) `UHDhalf` path, where
motion is searched at half resolution and the vectors are multiplied back up to
full resolution before `Degrain`.

It registers `core.mvuscale.ScaleVect(clip, scale=<int>)`, operating on
mvutensils' vector frame-props (geometry ×s, packed vectors ×s, SAD ×s²). It
links no libpython and no libvapoursynth, so one build per platform serves every
Python 3.x.

## Install

```bash
pip install vapoursynth-mvuscale      # drops the plugin under vapoursynth/plugins/mvuscale/
```

`smdegrain-bis` loads it automatically when `UHDhalf=True`. A host that manages
its own plugin directory can instead relocate the wheel payload (or the
`native/build.sh` output) into that directory, where it autoloads.

## Build from source

```bash
pip wheel native/ -w dist/            # scikit-build-core + CMake
# or, just the .so:
CXX=g++ bash native/build.sh          # -> tests/_plugins/mvuscale/libmvuscale.so
```

The VapourSynth API4 headers are vendored under `native/include/` (see its
`NOTICE`), so no VapourSynth SDK is required to build.

**Licence:** GPL-3.0-or-later.

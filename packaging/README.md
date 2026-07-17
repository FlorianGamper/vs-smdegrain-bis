# Portable / drop-in packaging

For hosts that **don't use pip** — StaxRip and other GUIs that bundle their own
VapourSynth — `make_portable_zip.sh` builds a per-platform drop-in zip:

```
smdegrain_bis-<ver>-<os>-<arch>/
  smdegrain_bis/                     the pure-Python package (drop on the VS Python path)
  vapoursynth-plugins/mvuscale/…     the mvuscale plugin binary (drop in the VS plugins dir; UHDhalf only)
  LICENSE  NOTICE-vendor.txt  INSTALL.txt
```

`mvutensils` is **not** bundled — it is a separate GPL project and is listed as a
prerequisite in `INSTALL.txt` (install via vsrepo / pip / its releases, v4+).

## Build one locally

```bash
# get the mvuscale binary for this platform (either):
CXX=g++ native/build.sh                       # -> tests/_plugins/mvuscale/libmvuscale.so
#   or extract vapoursynth/plugins/mvuscale/<binary> from a vapoursynth-mvuscale wheel

MVUSCALE_BIN=tests/_plugins/mvuscale/libmvuscale.so OS=linux ARCH=x64 \
  packaging/make_portable_zip.sh              # -> dist/smdegrain_bis-<ver>-linux-x64.zip
```

`OS`/`ARCH` default to the current host; override them to tag cross-built binaries.
Omitting `MVUSCALE_BIN` produces a Python-only zip (no UHDhalf). The script needs
only `python3` (uses stdlib `zipfile` when `zip` is absent).

## CI automation (all desktop platforms) — wired

Automated in `publish/release.yml` (the workflow `publish.sh` installs into the public
repo). On a `v*` tag the `mvuscale-wheels` job already cibuildwheels the plugin for
every desktop platform; the **`portable-zips`** job then extracts each wheel's
`vapoursynth/plugins/mvuscale/<binary>`, runs `make_portable_zip.sh` with the matching
`OS`/`ARCH`, and attaches the zips to the GitHub Release (also uploaded as a workflow
artifact):

| cibuildwheel runner | OS | ARCH |
|---|---|---|
| `ubuntu-latest` | linux | x64 |
| `ubuntu-24.04-arm` | linux | arm64 |
| `macos-latest` (both arch wheels) | macos | x64, arm64 |
| `windows-latest` (MSVC) | windows | x64 |

`packaging/` is on `publish/publish.sh`'s allowlist, so this script travels to the
public repo where the job runs.

Not automated / to confirm:
- StaxRip's exact plugin/script folder layout is **not verified** — `INSTALL.txt` gives
  generic "drop into the host's VS plugins/scripts folders" guidance; confirm StaxRip's
  real paths before writing host-specific instructions.

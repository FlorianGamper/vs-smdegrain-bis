# Changelog

Notable changes to `smdegrain_bis`. Format follows
[Keep a Changelog](https://keepachangelog.com/); the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.1] — 2026-07-17

### Changed
- **Over-deep `RefineMotion` now clamps instead of erroring.** Requesting more refine passes than the
  block size allows (each pass halves it down to mvu's 4×4 floor) now clamps to the deepest that fits
  and emits a warning, rather than raising `vs.Error` — so a single `RefineMotion` setting works across
  a range of clip sizes (e.g. a batch/multi-encode tool feeding smaller clips through one script). Bad
  types (negative or non-int) still raise.

## [0.3.0] — 2026-07-17

### Added
- **`RefineMotion` is now int-valued — multiple `Recalculate` passes.** `False`/`0` = off and
  `True`/`1` = the single half-block refine pass Dogway's `SMDegrain.avsi` does (unchanged and
  **byte-identical** to before — verified across the progressive/interlaced/GlobalR/GRAY/UHDhalf
  paths); `RefineMotion=N` chains N `mvu.Recalculate` passes, each halving the block size again
  (e.g. `blksize=32`, `RefineMotion=2` refines 32→16→8), down to mvu's 4×4 floor. `thSAD` is held
  constant across passes — mvu rescales it by block area, so each finer pass refines more
  aggressively. This extends **beyond** Dogway's reference (which is single-pass only), matching
  vsjetpack `mc_degrain`'s `refine`. Requesting more passes than the block size allows raises a clear
  error; existing bool calls are unaffected. Resolves feature request
  [#1](https://github.com/FlorianGamper/vs-smdegrain-bis/issues/1).

### Changed
- **Dependency floor raised to `vapoursynth-mvutensils>=4`** (was `>=2`). The `LFR` mask
  (`mvu.SADMask`) had a data race on mvutensils v2/v3
  ([myrsloik/mvutensils#5](https://github.com/myrsloik/mvutensils/issues/5), fixed in **v4**), so a
  fresh install is now safe for `LFR` by default instead of only when pip happened to resolve v4.

### Documentation
- **`LFR` / `DCTFlicker` are safe again — on mvutensils v4+.** The README warning is downgraded from
  "unsafe on v2" to a v4+ requirement, now guaranteed by the dependency floor above. The upstream
  race ([myrsloik/mvutensils#5](https://github.com/myrsloik/mvutensils/issues/5)) is fixed in v4.

## [0.2.0] — 2026-07-15

### Fixed
- **`search=6` / `search=7` (horizontal / vertical exhaustive) are now accepted.** The mvtools→mvu
  search-id map only covered modes 2–5, so `search=6/7` raised `has no mvutensils equivalent` even
  though mvu supports them — a regression against both the frozen mvtools baseline and Dogway's
  reference (whose `int "search"` is a pass-through; `SMDegrain.html:397-404`). The −2 offset holds
  across 2–7 (`6→4, 7→5`); only mvtools' `0`/`1` (OneTimeSearch/NStepSearch), which mvu dropped,
  still raise. Regression-guarded by `tests/map_search.py`.

### Changed
- **Forward-ported Dogway 4.7.0d's motion-search tuning** — `searchparam`, `pelsearch`, `pglobal`,
  and the `Recalculate` `searchparamr`, each wired to the avsi's default formula
  (`SMDegrain.avsi:228/231/232/233`). These were absent from the v3.1.2d lineage, so bis's motion
  search now follows Dogway's adaptive search radius and global-motion bias instead of the mvtools
  defaults. Output is **byte-identical wherever these coincide with the old baseline** — which is the
  whole tested grid: `pglobal` is discarded by mvu whenever global motion is off
  (`MotionBlockPyramid.cpp:1588`, and `MVglobal` defaults to `truemotion`, `False` on large clips),
  and `searchparam`/`pelsearch` resolve to the mvtools defaults except at full-res UHD
  (`searchparam=1`) or refine+truemotion (`searchparam=5`). The change bites on `truemotion=True` /
  full-res-UHD content with real global motion, which the test fixtures don't exercise — validate
  there with a real-content A/B against Dogway's avsi.
  - `pelsearch` is floored at `1` (Dogway allows `0`): mvu rejects `pelsearch=0` ("must be
    positive"), a 1-unit divergence that only occurs at `searchparam=1`.
  - `plevel` is deliberately **not** changed (Dogway forces `0`); bis keeps its truemotion-preset
    value pending a `truemotion` calibration re-sweep.

## [0.1.3] — 2026-07-13

### Documentation
- **Known issue: `LFR` / `DCTFlicker` are unsafe on mvutensils v2** — documented in the README.
  `LFR` gates its restore with a mask from `mvu.SADMask`, and that filter has a **data race** in
  mvutensils v2 ([myrsloik/mvutensils#5](https://github.com/myrsloik/mvutensils/issues/5), open):
  `SADMask`, `VectorLengthMask` and `OcclusionMask` register as `fmParallel` yet share a single
  filter-instance zimg scratch buffer, so concurrent frame requests corrupt the heap — `double free
  or corruption`, SIGSEGV, or `std::system_error` at nondeterministic frames in a multi-threaded
  render of real content. It is clean single-threaded, hence invisible to `core.num_threads = 1` runs
  and to `vspipe --info`. Keep `LFR=False` (the default) until the upstream fix lands. No code change
  — `SADMask` is the only one of the three filters this pipeline uses, and only when `LFR` is on.

## [0.1.2] — 2026-07-12

### Fixed
- **DCTFlicker: the recursive LF-band pass no longer inherits `UHDhalf=True`.** The inner
  `SMDegrain()` call guarded `LFR`/`DCTFlicker` but not `UHDhalf` (whose signature default is
  `True`), so on UHD-class sources the low-frequency band got a second half-res motion search +
  `mvuscale` vector-scale pass. Dogway's reference (smdegrain-4x.avsi:495) runs that pass at full
  resolution. Output of `DCTFlicker=True` on UHD-class sources changes accordingly; all other
  paths are byte-identical (verified by A/B render).

### Documentation
- **`UHDhalf` requires mod-4 source dimensions** (now documented in the README). The half-res
  search size is forced to even, so on sources whose width or height is not divisible by 4 the
  ×2-scaled vectors no longer match the full-res render super and graph construction fails cleanly
  with `Degrain: The motion vectors passed are not compatible with the super clip`. Crop/pad to
  mod-4 or set `UHDhalf=False`. (Behaviour unchanged — this documents an existing limitation.)

## [0.1.1] — 2026-07-11

### Changed
- Internal cleanup, no behaviour change: dropped a dead `_native/` plugin-load candidate from
  `_ensure_mvuscale` (the scaler is located via the `vapoursynth-mvuscale` package metadata),
  de-duplicated the `call_dfttest` kwargs, and removed an unused `libmvuscale` filename variant.

## [0.1.0] — 2026-07-11

First public release. `smdegrain_bis` is a **VapourSynth** motion-compensated temporal denoiser in the
lineage **Dogway's Avisynth `SMDegrain.avsi` → Selur's VapourSynth `smdegrain.py` → here**: Selur's
v3.1.2d baseline with selected Dogway **v4.7.0d** features back-ported, running on the **mvutensils**
(`core.mvu`) motion backend.

### Motion backend — mvutensils (`core.mvu`)
- Super / AnalyseMany / Recalculate / generic Degrain / SADMask on `core.mvu` (myrsloik/mvutensils),
  with a native C++ vector scaler (`mvuscale`) for UHDhalf.
- **~1.43× faster** (non-UHDhalf) and **~1.46× faster** (UHDhalf) than the equivalent mvtools build on
  a 3840×2160 10-bit source. Output SSIM ≈ 0.9998 vs mvtools — not bit-identical (mvu's auto pyramid
  caps one level shallower), though the atomic SAD / interpolation / motion-compensation ops are
  bit-exact.
- No `manipmv` dependency; UHDhalf vector scaling uses the bundled `mvuscale` filter instead.

### Back-ported from Dogway `SMDegrain.avsi` v4.7.0d
On top of the v3.1.2d baseline, all source-audited to match the avsi:
- **UHDhalf** — half-resolution motion search on 4K+ sources (`w>2599 || h>1499` trigger + defaults),
  with the native `mvuscale` vector scaler.
- **LFR (low-frequency restore) + DCTFlicker** — sigma `3.46·w/1920`, Hz-cutoff, prefilter unsharp,
  restore, and `temp4` flicker repair. LFR runs at native depth via `mvu.SADMask`.
- **thSADC / scaleCSAD** — chroma SAD `thSAD·0.755·0.25·2^scaleCSAD` plus the full subsampling table.
- **thSCD2 = 51 %**.
- **BM3D / DGDenoise prefilters** (avsi prefilter modes 8 / 7).
- **recalc (RefineMotion) thSAD** — `round(exp(-101/(thSAD·0.83))·360)`; keeps the refine re-search
  effective at high thSAD (= 266 at thSAD 400).
- **thSCD1 default** — `round(0.35·thSAD+260)`; scales the scene-change SAD with thSAD (= 400 at
  thSAD 400).

**Not** back-ported (still v3.1.2d): adaptive `searchparam` / `pelsearch`, explicit `plevel=0`,
`hpad` / `vpad`, the ~25-mode preset system, and mvtools2-only `scaleCSAD` / `temporal`.

### Behaviour notes
- **VapourSynth R74+ range detection.** R74 (API 4.2) deprecated `_ColorRange` for `_Range` (H.273
  numbering, with the `vs.RANGE_*` enum flipped in lockstep). Range auto-detection is version-guarded
  and compares to the enum constant — correct on both pre- and post-R74 cores.
- **UHDhalf works with `RefineMotion=False`.** The mvtools crash in that combination cannot occur on
  mvu, so there is no guard.
- **`subpixel>=3`** (NNEDI3 external pel-clip) is not supported on the mvu backend — `mvu.Super`
  accepts only a single-plane pel-clip. It emits a warning and falls back to mvu's internal Wiener
  subpixel.

### Packaging
- **`smdegrain-bis`** — pure-Python wheel. Depends on `vapoursynth-mvutensils`; the `[uhdhalf]` extra
  adds the native scaler and `[extras]` the optional lazily-imported acceleration paths.
- **`vapoursynth-mvuscale`** — the native UHDhalf vector scaler, published as a per-platform wheel whose
  payload is a VapourSynth plugin. `SMDegrain` loads it automatically when UHDhalf engages, so
  `pip install "smdegrain-bis[uhdhalf]"` needs no manual plugin step.
- **Licence:** GPL-3.0-or-later (`LICENSE`) — a derivative of Dogway's GPL-3 `SMDegrain.avsi`.

### Documentation
- README covering the lineage, the processing pipeline, which avsi features are taken over vs left, and
  per-file provenance for the vendored `sharpen.py`.

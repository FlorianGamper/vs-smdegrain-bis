# Changelog

Notable changes to `smdegrain_bis`. Format follows
[Keep a Changelog](https://keepachangelog.com/); the project aims to follow
[Semantic Versioning](https://semver.org/).

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

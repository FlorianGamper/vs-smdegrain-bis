# smdegrain_bis

**A motion-compensated temporal denoiser for [VapourSynth](https://www.vapoursynth.com/).**
A pure temporal denoiser in the lineage of Dogway's Avisynth `SMDegrain` — it estimates motion
between frames and averages each pixel along its motion trajectory, so grain and noise wash out
while detail and edges stay put. Runs on [mvutensils](https://github.com/myrsloik/mvutensils)
(`core.mvu`), which makes it **~1.4× faster than the mvtools implementation it was ported from**
(measured 1.43–1.46× vs the same mvtools settings). On 4K, the optional `UHDhalf` half-res motion
search is a further, separate speedup on top of that.

![Licence: GPL-3.0-or-later](https://img.shields.io/badge/licence-GPL--3.0--or--later-blue)
![VapourSynth: API4](https://img.shields.io/badge/VapourSynth-API4%20(R55%2B)-5b8fb9)
![Python: 3.8+](https://img.shields.io/badge/python-3.8%2B-3776ab)
![Backend: mvutensils](https://img.shields.io/badge/backend-mvutensils%20(core.mvu)-6aa84f)

> **Status: released.** The filter is complete and validated across the pel × tr × UHD × LFR ×
> interlaced grid, and is on PyPI (see [Install](#install)).

---

## Features

- **Motion-compensated temporal denoise** — `Super → Analyse → (Recalculate) → Degrain` on `core.mvu`,
  one generic `Degrain` over the whole `[bw₁,fw₁,…]` vector list.
- **Multi-pass motion refinement** — `RefineMotion=N` chains N `Recalculate` passes, each halving the
  block size (e.g. refine a coarse `blksize=32` search down to 8) to recover detail that large blocks
  would otherwise blur. `N=1` reproduces Dogway's single refine pass; **`N≥2` is a `smdegrain_bis`
  enhancement not present in the original `SMDegrain.avsi`** (which refines only once).
- **Frame-prop aware** — auto-detects TV/PC range (`_ColorRange`/`_Range`, R74+ safe), interlacing
  (`_FieldBased`) and HDR transfer, and derives chroma-SAD / scene-change thresholds from `thSAD`.
- **UHD-aware** — optional half-resolution motion search (`UHDhalf`) on 4K+ sources, with a native
  C++ vector scaler; search runs at half-res, the render stays full-res.
- **8–16-bit** integer YUV/GRAY, native depth throughout (no 8-bit detours).
- **Optional prefilters** — MinBlur / DFTTest / KNLMeansCL / BM3D / DGDenoise, or your own clip.
- **Optional finishing** — low-frequency restore (`LFR`), `DCTFlicker`, contra-sharpening, interlaced
  weave.
- **Bring-your-own tonemapper** — HDR sources are tone-mapped for the *motion search* only if you pass
  a `tonemap_fn`; the filter never bundles a tonemapper.

---

## Install

```bash
pip install smdegrain-bis                 # the filter (pure Python)
pip install smdegrain-bis[uhdhalf]        # + the native UHDhalf vector scaler
```

`smdegrain-bis` is pure Python; `[uhdhalf]` additionally pulls
[`vapoursynth-mvuscale`](native/), a small per-platform native plugin used only when `UHDhalf`
engages. You need a working **VapourSynth** install and the **mvutensils** plugin
(`pip install vapoursynth-mvutensils`, pulled in automatically).

Optional acceleration/prefilter paths (`vsrgtools`, `vs-dfttest2`) come with `pip install
smdegrain-bis[extras]`; they are imported lazily and not required.

> Prefer to manage plugins yourself? Build `mvuscale` from source (see
> [below](#building-the-native-scaler)) and drop it into your VapourSynth plugins directory — it then
> autoloads and `smdegrain-bis` uses it without the `[uhdhalf]` extra.

**No pip? (StaxRip and other hosts that bundle their own VapourSynth).** Use a portable drop-in zip —
the pure-Python package plus the `mvuscale` plugin for your platform — and copy the two folders into
the host's VapourSynth Python path and plugins directory. See [`packaging/`](packaging/README.md) to
build one (`packaging/make_portable_zip.sh`). `vapoursynth-mvutensils` v9+ is a prerequisite either way
(it is not bundled).

---

## Quick start

```python
import vapoursynth as vs
from smdegrain_bis import SMDegrain

core = vs.core
clip = ...                                  # YUV/GRAY, 8–16-bit integer (float is rejected)

den = SMDegrain(clip, tr=2, thSAD=300)                      # basic temporal denoise
den = SMDegrain(clip, tr=3, thSAD=400, RefineMotion=True)   # stronger, refined motion (1 pass)
den = SMDegrain(clip, blksize=32, RefineMotion=2)           # refine coarse blocks 32→16→8 (detail)
den = SMDegrain(clip, tr=2, UHDhalf=True)                   # 4K: half-res motion search
```

`UHDhalf` (default `True`) engages only on sources above **2599×1499**; below that it is a no-op. When
it engages it requires the `mvuscale` plugin (the `[uhdhalf]` extra, or a self-built `.so`), and the
source width **and** height must be **divisible by 4**: the half-res search size is forced to even, so
on a non-mod-4 source the ×2-scaled vectors no longer match the full-res render super and graph
construction fails cleanly with `Degrain: The motion vectors passed are not compatible with the super
clip`. Crop/pad to mod-4 upstream, or set `UHDhalf=False` for such sources.

---

## Key parameters

| parameter | default | meaning |
|---|---|---|
| `tr` | `2` | temporal radius — frames each side used for the average |
| `thSAD` | `300` | degrain strength (luma SAD threshold); higher = stronger |
| `thSADC` | auto | chroma SAD threshold (derived from `thSAD` via the v4.x `scaleCSAD` table) |
| `RefineMotion` | `False` | motion-vector refinement. `False`/`0` = off; `True`/`1` = one `Recalculate` pass at half the block size (Dogway's behaviour); `N` = chain N passes, each halving the block size again (e.g. `blksize=32`, `RefineMotion=2` → 32→16→8), down to the 4×4 floor. Asking for more passes than the block size allows is **clamped** (with a warning) to the deepest that fits, so one setting works across clip sizes. `N≥2` is a `smdegrain_bis` enhancement — the original avsi refines once |
| `pel` | auto | motion precision (½/¼-pel); auto = `1` on UHD, `2` otherwise |
| `prefilter` | `-1` | motion-search prefilter (`-1` MinBlur … `5` BM3D, or a clip) |
| `contrasharp` | auto | contra-sharpen the result toward the source |
| `UHDhalf` | `True` | half-resolution motion search on >2599×1499 sources (dimensions must be mod-4) |
| `LFR` | `False` | low-frequency detail restore, gated by a `SADMask` — **[see below](#low-frequency-restore--de-flicker-lfr--dctflicker)** |
| `DCTFlicker` | `False` | recursive flicker-calming pass (requires `LFR`) |
| `interlaced` | auto | auto-detected from `_FieldBased`; set `False` to force progressive |
| `tv_range` | auto | auto-detected from the range frame-prop |
| `tonemap_fn` | `None` | caller-supplied HDR tonemapper (search only) |

The full v3.1.2d keyword set (`blksize`, `overlap`, `search`, `limit`, `thSCD1/2`, `Str`/`Amp`,
`Globals`, …) is accepted verbatim.

---

## Examples

### HDR sources — `tonemap_fn`

The filter never bundles a tonemapper. For **HDR (PQ/HLG)** sources you pass a `tonemap_fn`
(`callable(clip) -> clip`); it is applied to the **motion-search prefilter only**, and only when the
source's `_Transfer` prop is PQ (16) or HLG (18). The denoised **output keeps your original HDR clip
untouched** — motion estimation just gets to work on perceptually-even values instead of raw PQ.

```python
import vapoursynth as vs
from smdegrain_bis import SMDegrain
core = vs.core

def tonemap(clip):
    # Any clip -> clip callable. Typically libplacebo (vs-placebo); kwargs illustrative.
    return clip.placebo.Tonemap(src_csp=1, dst_csp=0)          # PQ -> SDR for the search

hdr = core.lsmas.LWLibavSource("uhd_hdr.mkv")                  # e.g. 3840x2160 PQ
den = SMDegrain(hdr, tr=2, thSAD=400, UHDhalf=True, tonemap_fn=tonemap)
# `den` is still HDR; only the internal motion search saw the tone-mapped clip.
```

Without a `tonemap_fn`, an HDR source is denoised as-is (motion search runs on raw PQ/HLG values).
The same hook exists on the standalone helper: `prefilter_clip(clip, mode, tonemap_fn=...)`.

### Prefilters (motion search only)

`prefilter` selects the clip motion is estimated on; the result is always degrained from the
*original*. Pass a **mode** (int or name) or **your own clip**:

```python
den = SMDegrain(clip, prefilter=-1)          # auto MinBlur (the default)
den = SMDegrain(clip, prefilter="dfttest")   # DFTTest with a luma mask
den = SMDegrain(clip, prefilter="knlmeans")  # KNLMeansCL (CUDA / ISPC / OpenCL auto-pick)
den = SMDegrain(clip, prefilter="bm3d")      # BM3D
den = SMDegrain(clip, prefilter="dgdenoise") # DGDenoise (DGDecNV)

my_pref = core.bm3dcpu.BM3Dv2(clip, sigma=3.0)   # ... or any prebuilt clip
den = SMDegrain(clip, prefilter=my_pref)
```

### Low-frequency restore & de-flicker (`LFR` / `DCTFlicker`)

> [!IMPORTANT]
> **`LFR` / `DCTFlicker` depend on a `mvu.SADMask` fix that landed in mvutensils v4.** They gate the low-frequency restore with a
> motion-confidence mask from `mvu.SADMask`, which had a **data race** in mvutensils **before v4**:
> [myrsloik/mvutensils#5](https://github.com/myrsloik/mvutensils/issues/5) (fixed in **v4**). On the
> affected versions `SADMask`, `VectorLengthMask` and `OcclusionMask` all register as `fmParallel` but
> share one filter-instance zimg scratch buffer, so parallel frame requests scribble over each other —
> a multi-threaded render of real content dies with `double free or corruption`, a segfault, or
> `std::system_error` at nondeterministic frames. It is **clean single-threaded**, so there it hides
> from `core.num_threads = 1` runs and from `vspipe --info`, surfacing only in a real encode.
>
> **On v4+ the race is fixed and `LFR` is safe.** The package requires `vapoursynth-mvutensils>=9`
> (raised from `>=4` for an unrelated `Recalculate` fix — see the changelog), so a normal
> `pip install` is already well clear of this — it only bites if you force an older mvutensils below
> that floor, in which case upgrade it or keep `LFR=False` (the default).

Back-ported v4.x finishing for high-`tr` / high-`thSAD` (or `truemotion`) runs, where strong temporal
averaging can eat low-frequency detail:

```python
den = SMDegrain(clip, tr=4, thSAD=600, LFR=True)                  # restore low-freq detail
den = SMDegrain(clip, tr=4, thSAD=600, LFR=True, DCTFlicker=True) # + calm the restored detail
den = SMDegrain(clip, LFR=300)                                    # explicit Hz cutoff (0..1920)
```

### Interlaced content

`interlaced` auto-detects from `_FieldBased`; set it explicitly (with field order) when the prop is
absent:

```python
den = SMDegrain(clip, tr=2, interlaced=True, tff=True)           # top-field-first
```

---

## How it works

A single `SMDegrain()` call runs this pipeline (motion stages on `core.mvu`):

1. **Frame-prop setup** — read frame 0 for range / field / transfer; derive `thSADC` and `thSCD1`.
2. **Prefilter** (motion search only) — optional denoise; HDR tone-mapped *iff* `tonemap_fn` is given.
3. **DitherLumaRebuild** — TV→PC luma expansion so motion estimation sees more code values.
4. **UHDhalf** (UHD only) — soft-cubic downscale to half-res for the *search*; render super stays full.
5. **Super** — `mvu.Super` builds the hierarchical pyramid.
6. **Analyse → Recalculate** — `mvu.AnalyseMany(radius=tr)` yields `[bw₁,fw₁,…]`; `RefineMotion=N`
   chains N `mvu.Recalculate` passes over the list, each halving the block size (N=1 is Dogway's single pass).
7. **UHDhalf vector scale** — `mvuscale.ScaleVect` multiplies the half-res vectors up to full-res.
8. **Degrain** — one generic `mvu.Degrain(clip, super, vectors)` does the compensated temporal average.
9. **LFR / DCTFlicker** (optional) — `mvu.SADMask`-gated low-frequency restore + flicker calming.
10. **Contra-sharpen / interlaced weave** (optional) — output finishing.

---

## Version & provenance — this is a *mix*, not full v4.7.0d

`smdegrain_bis` is Selur's VapourSynth **v3.1.2d** port with **selected v4.7.0d features
back-ported** — deliberately not a full v4.7.0d port. Each item below was audited against the
Avisynth source.

<table>
<tr><th>Back-ported from the v4.7.0d avsi</th><th>Added (VapourSynth-era, not in the avsi)</th><th>Not ported — still v3.1.2d</th></tr>
<tr valign="top"><td>

- UHDhalf trigger + half-res search
- `thSADC` / `scaleCSAD` chroma table
- `thSCD2` = 51 %
- recalc-`thSAD` `exp(-101/(thSAD·0.83))·360`
- `thSCD1` `0.35·thSAD+260`
- LFR + DCTFlicker
- BM3D / DGDenoise prefilters, contra-sharpen

</td><td>

- **multi-pass `RefineMotion=N`** (chained Recalculate, 32→…→8; the avsi refines **once**)
- frame-prop defaults (range/field/transfer)
- caller-injected `tonemap_fn` (HDR, search-only)
- public `prefilter_clip()` helper
- lazily-imported optional deps

</td><td>

- adaptive `searchparam` / `pelsearch`
- explicit `plevel=0`
- `hpad`/`vpad` = `isHD?0:blksize`
- the ~25-**mode** preset system
- mvtools2-only `scaleCSAD`, `temporal`

</td></tr>
</table>

The full audit and upgrade roadmap live in the development notes.

---

## The mvutensils port

Backend moved from `core.mv` (mvtools) to `core.mvu` (mvutensils):

- **~1.43× faster** (non-UHDhalf), **~1.46× faster** (UHDhalf) — measured on a 3840×2160 10-bit source.
- **Output SSIM ≈ 0.9998** vs the frozen mvtools reference — not byte-identical (mvu's auto pyramid
  caps one level shallower), though the atomic SAD/interpolation/MC ops *are* bit-exact.
- **Two mvtools bugs fixed:** the 10→8→10-bit `mv.Mask` detour is gone (`mvu.SADMask` at native depth),
  and the `UHDhalf` + `RefineMotion=False` **SIGSEGV** cannot occur on mvu (so `UHDhalf` works with
  refinement off, and the `manipmv` dependency is dropped).
- **`native/mvuscale`** — a ~40-line API4 filter that scales mvu's vector frame-props for UHDhalf;
  ~6× faster than the pure-Python scaler it replaced.

Change history: [`CHANGELOG.md`](CHANGELOG.md).

---

## Vendored code

`smdegrain_bis/vendor/` bundles one upstream module — `sharpen.py` (`MinBlur`, `sbr`,
`ContraSharpening`, `LSFmod`, `Padding`, …) — as a byte-identical copy from
[Selur's VapoursynthScriptsInHybrid](https://github.com/Selur/VapoursynthScriptsInHybrid) (original
authors: LaTo INV., Didée, and the havsfunc lineage). It is **vendored rather than declared as a
dependency** because it's a loose community `.py` file, not a PyPI package. Bundling keeps
`pip install` self-contained and pins the exact code.

It is imported as a package submodule (`smdegrain_bis.vendor.sharpen`), **never** as a top-level
`sharpen`, so a wheel install can't collide with a host's own `sharpen`. Authorship and provenance are
in [`smdegrain_bis/vendor/NOTICE`](smdegrain_bis/vendor/NOTICE) — don't edit it in place; re-vendor by
copying.

---

## Plugin requirements

`smdegrain_bis` degrades gracefully: **the default call needs only the motion backend**, and every
other plugin is pulled in only by the feature that uses it (verified by running the default path with
nothing but `mvutensils` loaded).

**Always required**
- **`mvutensils`** (`core.mvu`) — the motion backend (`pip install vapoursynth-mvutensils`).

**Required by a feature — only when you enable it** (all common VS plugins):

| feature | plugin(s) |
|---|---|
| `UHDhalf` (auto on 4K+ sources) | **`mvuscale`** — the `[uhdhalf]` extra / `vapoursynth-mvuscale` |
| `contrasharp` (auto only when `CClip` is given) | a RemoveGrain provider: **`zsmooth`** *or* **`removegrain`** |
| `prefilter` ≥ 2 (MinBlur r≥2) | **`ctmf`** |
| `prefilter` DFTTest | **`dfttest`** (or `akarin` / `vs-dfttest2`) |
| `prefilter` BM3D | **`bm3dcpu`** / **`bm3dcuda`** |
| `prefilter` DGDenoise | **`dgdecnv`** |
| `prefilter` KNLMeans | **`nlm_ispc`** / **`nlm_cuda`** / `knlmeanscl` |
| `LFR` | **`resize2`** + the `vsrgtools` Python package |

**Optional accelerators** — used if present, silent fallback otherwise:
- **Expr backend:** `llvmexpr` → `akarin` → `cranexpr`, else the built-in `core.std.Expr`. **None is
  required** — any just speeds up the small per-pixel expressions.

**Never required:** `vszip`, `awarp` / `warp`, `rgsf` (all `hasattr`-guarded, float-only, or unreached
by default settings).

**Python:** a working `vapoursynth`; `vapoursynth-mvutensils` (a dependency); optionally `vsrgtools`
and `vs-dfttest2`.

### Building the native scaler

The `mvuscale` plugin ships as the per-platform `vapoursynth-mvuscale` wheel, or build it yourself
(the VapourSynth API4 headers are vendored — no SDK needed):

```bash
pip wheel native/ -w dist/                 # a wheel (scikit-build-core + CMake)
CXX=g++ bash native/build.sh               # or just the .so → native/libmvuscale.so
```

Drop the built `.so` into your VapourSynth plugins directory (or set `MVUSCALE_PLUGIN=/path/to/it`).

---

## A note on internal references

This repository is the published subset of a larger development tree: one commit per release, with
the sources, docs and packaging you need to build and use the package.

Some source comments and changelog entries cite files that are **not** part of that subset — an
analysis document, the test suite, a frozen reference implementation of the pre-port filter, and the
release tooling. Those paths will not resolve here. They are provenance markers left where a
maintainer wanted to record *why* a given constant or code path is what it is, and they are kept
verbatim so that what ships is a faithful copy of what is developed, rather than a rewritten one.

Nothing required to build, install, or run `smdegrain-bis` lives behind them. If you are chasing the
reasoning behind a specific behaviour, the [`CHANGELOG.md`](CHANGELOG.md) entry for the release that
introduced it is the public record, and issues and questions are welcome on the tracker.

---

## Licence

**GPL-3.0-or-later** — see [`LICENSE`](LICENSE). This is a derivative of Dogway's
[`SMDegrain.avsi`](https://github.com/Dogway/Avisynth-Scripts) (GPL-3.0); the motion plugins it uses
(`mvutensils`, and `mvtools` for the reference build) are GPL-2.0-or-later, which GPL-3 subsumes.

**Credits:** Dogway (SMDegrain — original mod and the v4.x algorithm), Caroliano (original idea),
[Selur](https://github.com/Selur/VapoursynthScriptsInHybrid) (the VapourSynth `smdegrain.py` baseline
and the vendored `sharpen` / `nnedi3_resample` / `color` helpers), and the mvtools / mvutensils authors
(dubhater, myrsloik). See [`smdegrain_bis/vendor/NOTICE`](smdegrain_bis/vendor/NOTICE) for the vendored
files and [`native/include/NOTICE`](native/include/NOTICE) for the VapourSynth headers.

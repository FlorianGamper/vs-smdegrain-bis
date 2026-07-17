import vapoursynth as vs
from vapoursynth import core

import math
import warnings

from typing import Sequence, Union, Optional

# Vendored upstream sharpen.py, imported as a package submodule (never top-level) so
# a wheel install can't collide with a host's own top-level `sharpen`.
from .vendor import sharpen
from .vendor.sharpen import (Padding, MinBlur, sbr, ContraSharpening,
                             GetPlane, cround, scale)

################################################################################################
#
#  Simple MDegrain Mod — SMDegrain()  "bis" (mvutensils)
#
#  Mod by Dogway — Original idea by Caroliano.
#  Special Thanks: Sagekilla, Didée, cretindesalpes, Gavino and the MVTools people.
#
#  Lineage:  Dogway's Avisynth SMDegrain.avsi  →  Selur's VapourSynth smdegrain.py (the
#            v3.1.2d baseline)  →  this "bis" mod.  This is NOT a full v4.7.0d port — it is
#            the v3.1.2d base with selected v4.7.0d features back-ported:
#
#    Back-ported from the v4.7.0d avsi (source-audited to match it):
#        · UHDhalf half-res motion search (w>2599 || h>1499 trigger, default on)
#        · thSADC / scaleCSAD chroma-SAD subsampling table
#        · thSCD2 = 51%
#        · recalc-thSAD  round(exp(-101 / (thSAD*0.83)) * 360)
#        · thSCD1        round(0.35*thSAD + 260)
#        · LFR low-frequency restore (sigma 3.46*w/1920) + DCTFlicker
#        · BM3D (prefilter mode 8) and DGDenoise (mode 7) prefilters, contra-sharpen
#    Added on top (VapourSynth-era, not in the avsi):
#        · frame-prop defaults (range / field / transfer auto-detect)
#        · caller-injected tonemap_fn (HDR, motion-search only)
#        · public prefilter_clip() helper, lazily-imported optional deps
#    Still at v3.1.2d — NOT ported from v4.7.0d:
#        · adaptive searchparam / pelsearch, explicit plevel=0, hpad/vpad = isHD?0:blksize
#        · the ~25-mode preset system, and mvtools2-only args (scaleCSAD, temporal)
#
#  Motion backend:  mvutensils (core.mvu).  The frozen mvtools (core.mv) sibling kept as the
#                   A/B reference lives in smdegrain_old/ (it lacks recalc-thSAD + thSCD1).
#
#      v3.1.2d (Dogway's mod)                          — 21 July 2015
#      v3.1.2d + v4.7.0d back-ports (bis, mvutensils)  — Florian Gamper, 2026
#
#  Licence: GPL-3.0-or-later (derivative of Dogway's GPL-3 SMDegrain.avsi).
#  See README.md → Licence and smdegrain_bis/vendor/NOTICE.
#
################################################################################################
#
#  General-purpose simple degrain function. A pure temporal denoiser: a frame-prop-aware
#  wrapper over mvutensils (core.mvu) super/analyse/degrain, with UHD-aware motion search,
#  BM3D / DGDenoise prefilters, LFR low-frequency restore, and a public prefilter_clip() helper.
#
#  The SMDegrain() signature takes all v3.1.2d kwargs plus tonemap_fn, tv_range, UHDhalf, LFR
#  and DCTFlicker. `interlaced` defaults to None (auto-detected from the _FieldBased frame
#  prop); pass interlaced=False to lock progressive processing. On mvutensils the output is
#  not bit-identical to the mvtools original (SSIM ~0.9998); see SMDEGRAIN_BIS_IMPROVEMENTS.md.
#
################################################################################################

def call_dfttest(clip, *, slocation, tbsize=1, planes=None):
    """
    Call dfttest2 (NVRTC backend) when both the C plugin AND the Python
    wrapper module are available; otherwise fall through to
    core.dfttest.DFTTest. Used by prefilter_clip() mode 3.

    `hasattr(core, 'dfttest2_nvrtc')` only signals the C plugin loaded;
    the convenient `dfttest2.DFTTest(...)` Python API ships as the paired
    `vs-dfttest2` pip module. Catching ImportError keeps the filter working
    where the plugin is present but the Python wrapper isn't installed.
    """
    kw = {'tbsize': tbsize, 'slocation': slocation}
    if planes is not None:
        kw['planes'] = planes
    if hasattr(core, 'dfttest2_nvrtc'):
        try:
            import dfttest2
            return dfttest2.DFTTest(clip, backend=dfttest2.Backend.NVRTC, **kw)
        except ImportError:
            pass  # vs-dfttest2 pip wrapper missing — fall through
    return core.dfttest.DFTTest(clip, **kw)


def prefilter_clip(input, mode, planes=None, device=None, tonemap_fn=None):
    """
    Build a prefilter clip for SMDegrain motion estimation.

    Args:
      input: source VideoNode
      mode: int or string. Accepted values:
            -1 / "none" / ""  : no denoising (return input unchanged)
            0 / "minblur0" / "sbr" : MinBlur radius=0 (uses sbr internally)
            1 / "minblur1" / "minblur" : MinBlur radius=1
            2 / "minblur2" : MinBlur radius=2
            3 / "dfttest" : akarin.DFTTest with luma mask
            4 / "knlmeans" / "knlmeanscl" : KNLMeansCL (auto-picks
                                            nlm_ispc/nlm_cuda/knlm via
                                            runtime detect in helper)
            5 / "bm3d" : BM3D — placeholder in this commit, implemented
                        in Task B7
            6 / "dgdenoise" : DGDecNV DGDenoise — placeholder in this
                              commit, implemented in Task B8
      planes: which planes to denoise (default: all planes of input)
      device: GPU device id for KNLMeansCL / BM3D / DGDenoise (default 0).
              Name matches the existing `device` kwarg on vendored
              SMDegrain() for consistency.
      tonemap_fn: optional callable(clip) -> clip. When supplied AND the
                  input clip's `_Transfer` frame prop indicates HDR
                  (16 = PQ, 18 = HLG), tonemap_fn is applied BEFORE
                  denoising. The vendored module does not bundle a
                  tonemap implementation — the caller provides one.
                  When None, the clip is denoised as-is regardless of
                  transfer.

    Returns:
      Denoised VideoNode suitable for use as SMDegrain's prefilter.
    """
    # String -> int mapping
    if isinstance(mode, str):
        m = mode.lower().strip()
        mode_map = {
            "":         -1,  "none":      -1,
            "minblur0":  0,  "sbr":        0,
            "minblur1":  1,  "minblur":    1,
            "minblur2":  2,
            "dfttest":   3,
            "knlmeans":  4,  "knlmeanscl": 4,
            "bm3d":      5,
            "dgdenoise": 6,
        }
        if m not in mode_map:
            raise vs.Error(f"smdegrain_bis.prefilter_clip: unknown mode '{mode}'")
        mode = mode_map[m]

    if mode == -1 or mode is None:
        return input

    if planes is None:
        planes = list(range(input.format.num_planes))

    # HDR tonemap-for-prefilter (optional, caller-supplied)
    if tonemap_fn is not None:
        try:
            src_transfer = input.get_frame(0).props.get('_Transfer', 1)
        except Exception:
            src_transfer = 1
        if src_transfer in (16, 18):  # PQ / HLG
            input = tonemap_fn(input)

    # Mode dispatch — modes 0..4 reuse helpers already defined in this module
    if mode in (0, 1, 2):
        return MinBlur(input, r=mode, planes=planes)
    if mode == 3:
        peak = (1 << input.format.bits_per_sample) - 1
        expr = 'x {i} < {peak} x {j} > 0 {peak} x {i} - {peak} {j} {i} - / * - ? ?'.format(
            i=scale(16, peak), j=scale(75, peak), peak=peak)
        EXPR = get_expr_fn()
        filtered = call_dfttest(input,
                                slocation=[0.0, 4.0, 0.2, 9.0, 1.0, 15.0],
                                planes=planes)
        return core.std.MaskedMerge(filtered, input,
                                    EXPR(GetPlane(input, 0), expr=[expr]),
                                    planes=planes)
    if mode == 4:
        return KNLMeansCL(input, d=1, a=1, h=7, device_id=device)
    if mode == 5:
        # BM3D — auto-pick cuda → cpu via pick_bm3d_plugin().
        # Parameters from Dogway's smdegrain-4x.avsi:328 prefilter call
        # (sigma=10, radius=1, preset="normal" which maps to block_step/
        # bm_range/ps_range below).
        bm3d_plugin = pick_bm3d_plugin()

        # Per-plane sigma — luma=10, chroma=sigma/2=5 (matches Dogway's
        # ex_BM3D: cs = chroma_active ? s/2 : 0). Sigma list length must
        # match num_planes — grayscale clips get a single-element list.
        num_planes = input.format.num_planes
        chroma_active = (1 in planes or 2 in planes) and num_planes > 1
        if num_planes == 1:
            sigma_list = [10.0]
            chroma_kw  = False  # chroma=True on single-plane clip is invalid
        else:
            sigma_list = [10.0,
                          5.0 if chroma_active else 0.0,
                          5.0 if chroma_active else 0.0]
            chroma_kw  = chroma_active

        # BM3DCUDA doesn't have an explicit tv_range kwarg — colorspace
        # is inferred from the clip's _ColorRange frame prop by the plugin.

        device_id = device if device is not None else 0
        return bm3d_plugin.BM3Dv2(
            clip       = input,
            sigma      = sigma_list,
            block_step = [4, 3, 4, 3],     # "normal" preset (Dogway 4.x line 1160)
            bm_range   = [16, 16, 12, 12], # "normal" preset (line 1161)
            radius     = 1,                # temporal radius (Dogway: r=1 for prefilter)
            ps_range   = [5, 6],           # "normal" preset (line 1162)
            chroma     = chroma_kw,
            device_id  = device_id,
            fast       = True,
        )
    if mode == 6:
        # DGDenoise — CUDA-only, user-installed (DGDecNV license).
        # Parameters from Dogway's smdegrain-4x.avsi:327 prefilter call site +
        # ex_DGDenoise function defaults (lines 1218-1226).
        # The VS plugin namespace for DGDecNV's denoiser needs verification
        # on a node where the user has installed DGDecNV — try the documented
        # namespace candidates in order.
        dg_call = None
        for ns in ('dgaltdenoise', 'dgdenoise', 'dgdecnv'):
            if hasattr(core, ns):
                ns_obj = getattr(core, ns)
                if hasattr(ns_obj, 'DGDenoise'):
                    dg_call = getattr(ns_obj, 'DGDenoise')
                    break
        if dg_call is None:
            raise vs.Error(
                "smdegrain_bis: DGDenoise prefilter (mode 6) requires "
                "DGDecNV's VapourSynth plugin. DGDecNV is a paid/proprietary "
                "plugin from rationalqm.us — install per license terms and "
                "place libdgdecnv.so in your VapourSynth plugins directory."
            )

        luma_active   = 0 in planes
        chroma_active = 1 in planes or 2 in planes
        return dg_call(
            input,
            mode   = 1,                                      # spatial only
            str    = 0.10 if luma_active else 0.0,           # luma denoise strength
            strc   = 0.05 if chroma_active else 0.0,         # chroma denoise strength
            qual   = "good",                                  # search window
            tthr   = 0.75,                                    # temporal motion threshold
        )

    raise vs.Error(f"smdegrain_bis.prefilter_clip: mode {mode} out of range (-1..6)")


def _ensure_mvuscale(core):
    """Ensure ``core.mvuscale`` (the UHDhalf vector scaler) is available.

    A host that manages plugins itself (a VapourSynth plugins dir / vsrepo) will
    have autoloaded it already, so this is a no-op. Otherwise load the plugin the
    ``vapoursynth-mvuscale`` wheel installed — located *precisely* from that
    distribution's file metadata, not by scanning search paths — so a plain
    ``pip install smdegrain-bis[uhdhalf]`` works with no manual step. Set
    ``MVUSCALE_PLUGIN`` to an explicit path to override.
    Silent on failure — the caller re-checks ``hasattr(core, 'mvuscale')`` and
    raises a clear error if still missing.

    Security: candidate paths come only from the explicit env override and the
    ``vapoursynth-mvuscale`` distribution's recorded files — never from an
    unqualified search path (which could include the CWD and let a planted
    ``libmvuscale.so`` be dlopen'd).
    """
    if hasattr(core, 'mvuscale'):
        return
    import os
    libnames = ('libmvuscale.so', 'mvuscale.dll', 'libmvuscale.dylib')

    def _candidates():
        env = os.environ.get('MVUSCALE_PLUGIN')       # explicit, trusted override
        if env:
            yield env
        try:                                          # exact file(s) the wheel installed
            from importlib.metadata import files as _dist_files
            for f in (_dist_files('vapoursynth-mvuscale') or ()):
                if os.path.basename(str(f)) in libnames:
                    yield os.fspath(f.locate())
        except Exception:
            pass

    for path in _candidates():
        if path and os.path.isfile(path):
            try:
                core.std.LoadPlugin(path)
            except vs.Error:
                continue
            if hasattr(core, 'mvuscale'):
                return


def SMDegrain(input, tr=2, thSAD=300, thSADC=None, RefineMotion: int = False, contrasharp=None, CClip=None, interlaced=None, tff=None, plane=4, Globals=0, pel=None, subpixel=2, prefilter=-1, mfilter=None,
              blksize=None, overlap=None, search=4, truemotion=None, MVglobal=None, dct=0, limit=255, limitc=None, thSCD1=None, thSCD2=130, chroma=True, hpad=None, vpad=None, Str=1.0, Amp=0.0625, opencl=False, device=None,
              tonemap_fn=None, tv_range=None, UHDhalf=True, LFR=False, DCTFlicker=False):
    if not isinstance(input, vs.VideoNode):
        raise vs.Error('SMDegrain: This is not a clip')

    # FLOAT pixel formats are out of contract — Dogway's smdegrain-4x.avsi:39
    # documents "8-16 bits" only, and several internal paths (peak derivation
    # for LFR Expr, ctmf.CTMF in MinBlur(r=2), expand_mask_to_yuv's INTEGER
    # query) silently misbehave on float input. Reject at the boundary.
    if input.format.sample_type == vs.FLOAT:
        raise vs.Error(
            "smdegrain_bis: FLOAT pixel format not supported (Dogway's "
            "smdegrain-4x.avsi:39 documents 8-16 bit integer only). "
            "Convert via resize.Point(format=vs.YUV420P10) upstream."
        )

    if input.format.color_family == vs.GRAY:
        plane = 0
        chroma = False

    peak = (1 << input.format.bits_per_sample) - 1

    # Decode frame 0 once for all frame-prop reads (range, field order, transfer).
    # HEVC/AVC frame-0 decode is 5-40 ms; the prior code triggered it 2-3× through
    # separate get_frame(0) calls.
    try:
        _f0_props = input.get_frame(0).props
    except Exception:
        _f0_props = {}

    # Range detection is version-guarded. VapourSynth R74 (API 4.2) deprecated the
    # `_ColorRange` prop for `_Range`, which follows H.273 numbering — the 0/1 values
    # are FLIPPED, and the `vs.RANGE_*` Python enum flips in lockstep with it
    # (R73: FULL=0/LIMITED=1; R74+: FULL=1/LIMITED=0). So read the right prop name for
    # the running core and compare against the enum CONSTANT (never a literal): that
    # is correct on both. Verified empirically on R73 and R74. A literal
    # `_ColorRange == 1` is not just stale on R74 — `_ColorRange` there carries the
    # flipped value, so it inverts. Default to limited (TV) when the prop is absent.
    if tv_range is None:
        _range_prop = '_Range' if core.core_version.release_major >= 74 else '_ColorRange'
        tv_range = (_f0_props.get(_range_prop, vs.RANGE_LIMITED) == vs.RANGE_LIMITED)

    # _FieldBased enum: 0 = progressive, 1 = TFF, 2 = BFF.
    # interlaced=None auto-detects; explicit True/False respects the caller.
    _fb = _f0_props.get('_FieldBased', 0)
    if interlaced is None:
        interlaced = (_fb != 0)
        if interlaced and tff is None:
            tff = (_fb == 1)
    elif interlaced and tff is None and _fb != 0:
        tff = (_fb == 1)

    # Defaults & Conditionals
    # Recalc (RefineMotion) thSAD — v4.x saturating curve (Dogway SMDegrain.avsi:169),
    # adopted in place of the v3.1.2d `thSAD//2`. Keeps the refine re-search effective
    # across the thSAD range instead of degrading to a near-no-op at high thSAD
    # (higher thSAD ⇒ fewer blocks re-searched; §15). Only affects RefineMotion=True.
    thSADR = cround(math.exp(-101.0 / (thSAD * 0.83)) * 360)
    # thSCD1 (scene-change SAD threshold) — v4.x scales it with thSAD
    # (avsi:239; MDegrain mode ⇒ the `!TS` ternary branch always applies) instead of a
    # fixed 400. Coincides with 400 at thSAD=400. Feeds Degrain and the LFR SADMask.
    if thSCD1 is None:
        thSCD1 = cround(0.35 * thSAD + 260)
    if thSADC is None:
        sub_w = input.format.subsampling_w
        sub_h = input.format.subsampling_h
        is_hd = (input.width > 1099 or input.height > 599)
        k = _scale_csad(luma_active=True, chroma_active=chroma,
                        sub_w=sub_w, sub_h=sub_h, is_hd=is_hd, ts=False)
        thSADC = cround(thSAD * 0.755 * 0.25 * (2 ** k))

    GlobalR = (Globals == 1)
    GlobalO = (Globals >= 3)
    if1 = CClip is not None

    if contrasharp is None:
        contrasharp = not GlobalO and if1

    w = input.width
    h = input.height

    # UHDhalf eligibility: only fires on UHD-class inputs and when caller
    # didn't opt out. Threshold matches Dogway's 4.x: >2599x1499.
    _uhd_threshold_w = 2599
    _uhd_threshold_h = 1499
    _uhd_eligible = (w > _uhd_threshold_w or h > _uhd_threshold_h)
    _do_uhdhalf = UHDhalf and _uhd_eligible

    # UHDhalf on the mvutensils backend:
    #  - Vector scaling uses the native `mvuscale` filter (scale_vect()), which
    #    replaces manipmv.ScaleVect (manipmv cannot read mvu's vectors) and is
    #    ~6x faster than the pure-Python equivalent — so it is REQUIRED, not
    #    optional. Fail early with a clear message if it is not loaded.
    #  - RefineMotion is NOT required: the mvtools "UHDhalf must have
    #    RefineMotion or it SIGSEGVs" landmine (§8) does not exist on mvu — its
    #    payload is single-level by construction and its Degrain validates the
    #    vectors it is handed (§4.1) instead of trusting a rewritten header. So
    #    that guard is gone; UHDhalf now works with RefineMotion off.
    if _do_uhdhalf:
        _ensure_mvuscale(core)
    if _do_uhdhalf and not hasattr(core, 'mvuscale'):
        raise vs.Error(
            "smdegrain_bis: UHDhalf=True requires the 'mvuscale' plugin. Install it with "
            "`pip install vapoursynth-mvuscale`, or build native/mvuscale.cpp (native/build.sh) "
            "and drop libmvuscale.so into your VapourSynth plugins dir. (The pure-Python vector "
            "scaler was removed — it was ~6x slower, 4 vs 26 fps.) Set UHDhalf=False to opt out."
        )

    # ─── LFR (Low-Frequency Restore) — Tier 2 feature ───
    # Sigma derivation extracted to `derive_lfr_sigma()` so it can be unit-
    # tested in isolation. Returns (active, pixel_space_sigma). Fed to
    # vsrgtools.gauss_blur in C5 (resize2-backed polyphase Gaussian).
    LFR_active, LFR_sigma = derive_lfr_sigma(LFR, w, h)

    DCTFlicker_active = bool(DCTFlicker) and LFR_active
    if bool(DCTFlicker) and not LFR_active:
        raise vs.Error(
            "smdegrain_bis: DCTFlicker=True requires LFR to be enabled "
            "(set LFR=True or LFR=<Hz cutoff>)."
        )

    # LFR on interlaced content blurs across alternate scanlines (inter-field
    # bleeding). Disable LFR when interlaced (auto-detected by Tier 1 or
    # explicit) and warn once. Re-enabling requires a weave+LFR+separate
    # pattern that's out of scope for Tier 2.
    # Runs BEFORE the resize2 check so an interlaced source on a resize2-less
    # worker reports the relevant interlaced auto-disable warning instead of
    # an irrelevant "install resize2" error.
    if LFR_active and interlaced:
        warnings.warn(
            "smdegrain_bis: LFR auto-disabled because interlaced=True "
            "(Gaussian blur on field-separated content produces inter-field "
            "bleeding). Set LFR=False explicitly to silence this warning.",
            RuntimeWarning, stacklevel=2,
        )
        LFR_active = False
        DCTFlicker_active = False

    if LFR_active and not hasattr(core, 'resize2'):
        raise vs.Error(
            "smdegrain_bis: LFR=True requires the resize2 VapourSynth plugin "
            "(used by vsrgtools.gauss_blur). Install resize2 into your VapourSynth "
            "plugins directory, or set LFR=False to opt out."
        )

    preclip = isinstance(prefilter, vs.VideoNode)
    ifC = isinstance(contrasharp, bool)
    if0 = contrasharp if ifC else contrasharp > 0
    is_large = w > 1024 or h > 576

    if pel is None:
        pel = 1 if is_large else 2
    if pel < 2:
        subpixel = min(subpixel, 2)
    if subpixel >= 3:
        # The NNEDI3 external pel-clip (subpixel 3/4) can't run on the mvutensils
        # backend: mvu.Super accepts only a single-plane pel-clip, but the search
        # super is multi-plane. Fall back to mvu's internal subpixel (Wiener).
        warnings.warn(
            "smdegrain_bis: subpixel>=3 (NNEDI3 external pel-clip) is unsupported on the "
            "mvutensils backend (mvu.Super takes a single-plane pel-clip only); using "
            "internal subpixel (sharp=2).", stacklevel=2)
        subpixel = 2

    if blksize is None:
        blksize = 16 if is_large else 8
    blk2 = blksize // 2
    if overlap is None:
        overlap = blk2
    if truemotion is None:
        truemotion = not is_large
    if MVglobal is None:
        MVglobal = truemotion

    planes = [0, 1, 2] if chroma else [0]

    if hpad is None:
        hpad = blksize
    if vpad is None:
        vpad = blksize
    limit = scale(limit, peak)
    if limitc is None:
        limitc = limit
    else:
        limitc = scale(limitc, peak)

    # Error Report
    if not (ifC or isinstance(contrasharp, int)):
        raise vs.Error("smdegrain_bis: 'contrasharp' only accepts bool and integer inputs")
    if if1 and (not isinstance(CClip, vs.VideoNode) or CClip.format.id != input.format.id):
        raise vs.Error("smdegrain_bis: 'CClip' must be the same format as input")
    if interlaced and h & 3:
        raise vs.Error('SMDegrain: Interlaced source requires mod 4 height sizes')
    if interlaced and not isinstance(tff, bool):
        raise vs.Error("smdegrain_bis: 'tff' must be set if source is interlaced. Setting tff to true means top field first and false means bottom field first")
    if not (isinstance(prefilter, int) or preclip):
        raise vs.Error("smdegrain_bis: 'prefilter' only accepts integer and clip inputs")
    if preclip and prefilter.format.id != input.format.id:
        raise vs.Error("smdegrain_bis: 'prefilter' must be the same format as input")
    if mfilter is not None and (not isinstance(mfilter, vs.VideoNode) or mfilter.format.id != input.format.id):
        raise vs.Error("smdegrain_bis: 'mfilter' must be the same format as input")
    if not (isinstance(RefineMotion, int) and RefineMotion >= 0):
        raise vs.Error(
            "smdegrain_bis: 'RefineMotion' must be a bool or a non-negative int "
            "(0/False = no refinement, 1/True = one Recalculate pass, N = N passes "
            "each halving the block size)")
    n_refine = int(RefineMotion)   # False->0, True->1, N->N chained Recalculate passes
    if n_refine and (blksize >> n_refine) < 4:
        max_passes = 0
        _b = blksize
        while (_b >> 1) >= 4:
            _b >>= 1
            max_passes += 1
        raise vs.Error(
            f'SMDegrain: RefineMotion={n_refine} halves the block size once per pass down '
            f'to a 4x4 floor, but blksize={blksize} allows at most {max_passes} pass(es). '
            f'Use RefineMotion<={max_passes} or a larger blksize.')
    # not sure whether this is still true, so I disabled it
    #if not chroma and plane != 0:
    #    raise vs.Error('SMDegrain: Denoising chroma with luma only vectors is bugged in mvtools and thus unsupported')

    # Input preparation for Interlacing
    if not interlaced:
        inputP = input
    else:
        inputP = input.std.SeparateFields(tff=tff)
        h = h/2

    # Prefilter & Motion Filter
    if mfilter is None:
        mfilter = inputP
    if not GlobalR:
        if isinstance(prefilter, vs.VideoNode):
            pref = prefilter   # caller-final: no tonemap, no denoise
        else:
            # HDR tonemap-for-MV: fires once for any int prefilter mode
            # (including -1 "no denoise"). Decoupled from prefilter_clip()
            # so the tonemap_fn contract holds regardless of denoise mode,
            # and so callers passing a VideoNode prefilter (above branch)
            # don't get their clip silently tonemapped.
            pref_in = inputP
            if tonemap_fn is not None and _f0_props.get('_Transfer', 1) in (16, 18):
                pref_in = tonemap_fn(inputP)
            if prefilter is None or prefilter == -1:
                pref = pref_in
            else:
                # prefilter_clip(tonemap_fn=None) — already applied above
                pref = prefilter_clip(pref_in, prefilter,
                                      planes=planes, device=device,
                                      tonemap_fn=None)
    else:
        pref = inputP

    # Default Auto-Prefilter - Luma expansion TV->PC (up to 16% more values for motion estimation)
    if not GlobalR:
        pref = DitherLumaRebuild(pref, s0=Str, c=Amp, chroma=chroma, tv_range=tv_range)

        # LFR prefilter unsharp — expand LF dynamic range before motion search
        # so MVs are more accurate on low-frequency content.
        # Per Dogway smdegrain-4x.avsi:339 (ex_unsharp with thSAD/1800 strength,
        # w/8 cutoff frequency). Uses vsrgtools.unsharpen with a wide-sigma
        # GaussBlur to approximate Dogway's Fc=w/8 intent (lower-frequency
        # cutoff = larger pixel sigma).
        if LFR_active:
            from vsrgtools import unsharpen, gauss_blur
            unsharp_sigma = max(1.0, w / 8.0 / 4.0)  # empirical scale for the cutoff
            pref = unsharpen(pref,
                             strength=thSAD / 1800.0,
                             blur=lambda c: gauss_blur(c, sigma=unsharp_sigma))

    # Motion vectors search
    # Search resolution: half-res for UHDhalf, full-res otherwise.
    # All motion-search-side super clips (super_search + Recalculate) live
    # at search resolution; super_render (consumed by MDegrain) is always
    # full-res. ScaleVect fires ONCE at the end of get_motion_vectors.
    if _do_uhdhalf:
        # Dogway's tuned soft-cubic kernel (smdegrain-4x.avsi:312):
        #   BicubicResize(nw, nh, -0.99, 0.06)
        # nmod2(x): force to even number (mvtools requires even dimensions)
        def nmod2(x): return int(x) - (int(x) % 2)
        nw, nh = nmod2(w / 2.0), nmod2(h / 2.0)
        pref_search = pref.resize.Bicubic(nw, nh,
                                          filter_param_a=-0.99,
                                          filter_param_b=0.06)
    else:
        pref_search = pref

    # ── mvu Super geometry ──
    # blksize/overlap are MANDATORY on mvu Super (mvtools took neither); chroma
    # is gone (moved to Analyse — the render super carries all planes and
    # Degrain's `planes` selects). mvtools levels → mvu onelevel (bool).
    #  - search super: full pyramid (onelevel=False), search-resolution pad;
    #    rfilter 4→2 (the only super whose coarse levels are actually searched).
    #  - render super: consumed by Degrain, which reads only the FINEST level.
    #    non-UHDhalf ⇒ single level (onelevel=True); UHDhalf ⇒ full pyramid with
    #    geometry SCALED ×2 to match the scaled vectors (blksize/overlap/pad all
    #    double), following the proven §6 recipe. rfilter is irrelevant on the
    #    finest-only render and single-level Recalculate supers, so it is omitted.
    _bs = [blksize, blksize]
    _ov = [overlap, overlap]
    _search_pad = [hpad, vpad]
    if _do_uhdhalf:
        _render_bs, _render_ov = [blksize * 2, blksize * 2], [overlap * 2, overlap * 2]
        _render_pad, _render_onelevel = [hpad * 2, vpad * 2], False
    else:
        _render_bs, _render_ov = _bs, _ov
        _render_pad, _render_onelevel = _search_pad, True

    # Motion supers use mvu's internal `sharp` subpixel interpolation. (The NNEDI3
    # external pel-clip path — subpixel 3/4 — was removed: mvu.Super only accepts a
    # single-plane pel-clip, so it can't be used with a multi-plane search super;
    # subpixel is capped to 2 above.)
    super_search = pref_search.mvu.Super(blksize=_bs, overlap=_ov, pad=_search_pad,
                                         pel=pel, sharp=subpixel, rfilter=2)
    if not GlobalR:
        super_render = inputP.mvu.Super(blksize=_render_bs, overlap=_render_ov, pad=_render_pad,
                                        pel=pel, sharp=subpixel, onelevel=_render_onelevel)
        if RefineMotion:
            Recalculate = pref_search.mvu.Super(blksize=_bs, overlap=_ov, pad=_search_pad,
                                                pel=pel, sharp=subpixel, onelevel=True)

    if GlobalR:
        super_render = super_search

    # ── mvu Analyse / Recalculate parameters ──
    # truemotion is a mvtools preset with no mvu equivalent; _truemotion_analyse
    # expands it to explicit penalty args (§2.1). search is renumbered
    # (_map_search); dct(mode)→satd(bool); blksize/overlap are lists. Recalculate
    # is a scalar-thsad refine that takes only mvlambda/pnew from the truemotion
    # bundle (§3 — lsad/plevel/globalmv/pglobal/pzero do not exist on it).
    _tm = _truemotion_analyse(truemotion, MVglobal)
    _mvu_search = _map_search(search)
    # dct: mvtools takes a MODE (0 SAD, 1-4 DCT-domain variants, 5 SATD, 6-10
    # mixed SATD/SAD); mvu has only a satd BOOL. So this mapping is lossy for
    # everything except 0 and 5: dct=1..4 asks for a DCT-domain metric and gets
    # SATD instead, silently. Not a live problem — the filter defaults to dct=0
    # and neither Dogway's reference nor any caller here passes anything else —
    # but if a caller ever needs true DCT, mvu cannot provide it and this line
    # must become a hard error, not a coercion.
    _satd = int(dct != 0)

    # ── Dogway 4.7.0d motion-search tuning (forward-port) ──────────────────────
    # NOT in the v3.1.2d lineage — smdegrain_old passes none of these, so they
    # intentionally diverge bis from the frozen mvtools baseline TOWARD Dogway's
    # reference. Each is wired to the avsi's default formula; all four are
    # accepted by mvu's live Analyse/Recalculate signatures. `plevel` is
    # deliberately excluded: Dogway forces plevel=0 always (avsi:367), but bis's
    # truemotion=True path sets plevel=1 to match mvtools' preset, and moving it
    # perturbs the §14.4 truemotion calibration — its own re-sweep, separate step.
    #   isUHD (avsi:143) = UHD-class source AND NOT UHDhalf (genuine full-res UHD).
    _is_uhd = _uhd_eligible and not _do_uhdhalf
    # searchparam (avsi:231): RefineMotion&&truemotion ? isUHD?2:5 : isUHD?1:2
    if RefineMotion and truemotion:
        _searchparam = 2 if _is_uhd else 5
    else:
        _searchparam = 1 if _is_uhd else 2
    # pelsearch (avsi:233): Dogway floors at 0, but mvu rejects 0 ("pelsearch
    # must be positive") where mvtools accepted it — so the mvu floor is 1. Only
    # bites at searchparam=1 (genuine full-res UHD); a 1-unit mvu-imposed offset.
    _pelsearch = max(1, _searchparam * 2 - 2)
    # searchparamr (avsi:232): Recalculate's own (smaller) search radius. cround
    # matches the avsi Round (half-away-from-zero); the max() clamps the negatives.
    _searchparamr = max(0, cround(math.exp(0.69 * _searchparam - 1.79) - 0.67))

    analyse_params = dict(blksize=_bs, overlap=_ov, search=_mvu_search,
                          searchparam=_searchparam, pelsearch=_pelsearch,
                          chroma=int(chroma), satd=_satd, **_tm)
    analyse_params['pglobal'] = 11     # avsi:228 — unconditional; overrides _tm's 0
    # RefineMotion=N chains N Recalculate passes, each halving the block size (and
    # overlap) below the base Analyse geometry: pass i uses blksize>>(i+1), down to the
    # mvu 4x4 floor (validated by the guard above). thsad is held CONSTANT across passes
    # — mvu rescales it by block area internally, so each finer pass automatically
    # refines more aggressively (this matches vsjetpack mc_degrain's `refine`). N=1
    # reproduces Dogway avsi:388 exactly (bs=blk2, overlap=overlap//2). Overlap is
    # clamped to <= blksize/2 and rounded to the chroma subsampling grid, since mvu
    # rejects a non-divisible overlap when chroma is searched.
    refine_params = None
    if n_refine:
        _ov_align = (1 << max(pref_search.format.subsampling_w,
                              pref_search.format.subsampling_h)) if chroma else 1
        _refine_static = dict(search=_mvu_search, searchparam=_searchparamr,
                              chroma=int(chroma), satd=_satd,
                              mvlambda=_tm['mvlambda'], pnew=_tm['pnew'])
        refine_params = []
        for _i in range(n_refine):
            _bs_i = blksize >> (_i + 1)
            _ov_i = min(overlap >> (_i + 1), _bs_i // 2)
            _ov_i -= _ov_i % _ov_align
            refine_params.append(dict(thsad=thSADR, blksize=[_bs_i, _bs_i],
                                      overlap=[_ov_i, _ov_i], **_refine_static))

    refine_super = None
    if RefineMotion:
        refine_super = Recalculate if not GlobalR else super_render
    vector_scale = 2 if _do_uhdhalf else None
    vectors = get_motion_vectors(super_search, refine_super, analyse_params,
                                 refine_params, tr, interlaced,
                                 vector_scale=vector_scale)

    # ─── LFR mask generation — Tier 2 feature ───
    # Per-delta motion-vector confidence masks averaged into Mavg, which
    # gates the LF restore step (C5) so we only restore detail in regions
    # where vectors are reliable. Per Dogway smdegrain-4x.avsi:407-420.
    #
    # mvu.SADMask derives the mask from the vector clip ALONE (no source clip)
    # and emits it at native bit depth, so the mvtools 10→8→10 `resize.Point`
    # detour is gone (SMDEGRAIN_BIS_IMPROVEMENTS.md §7).
    Mavg = None
    if LFR_active:
        # Per Dogway smdegrain-4x.avsi:412 — DCTFlicker uses gentler gamma
        # (gamma>1 in mvtools' pow(SAD/ml, 1/gamma) → broader/more-permissive
        # mask). Verified values match the reference for both LFR-only
        # (gamma=0.5) and LFR+DCTFlicker (gamma=2.222) cases.
        gm = 2.222 if DCTFlicker_active else 0.5
        Mavg = mv_sadmask_chain(vectors, input, gamma=gm,
                                thscd1=thSCD1, thscd2=thSCD2)

    # ── mvu Degrain — one generic call replaces the Degrain1..6 ladder ──
    # It infers the radius from the vector list. thsad=[luma,chroma];
    # plane→planes list; limit in native pixel units (≥peak ⇒ no clamp, which
    # is how the filter's default scale(255,peak) reads); thscd1 maps 1:1
    # (both default 400); thscd2 is a 0-100 percentage (mvtools 130/256 ⇒ 50.78,
    # mvu default 51.0). Degrained clip is inputP progressive / mfilter
    # interlaced, exactly as the old ladder chose.
    if not GlobalO:
        _degrain_clip = mfilter if interlaced else inputP
        output = core.mvu.Degrain(
            _degrain_clip, super_render, vectors,
            thsad=[thSAD, thSADC],
            planes=_plane_to_planes(plane),
            limit=[float(limit), float(limitc)],
            thscd1=thSCD1,
            thscd2=thSCD2 * 100.0 / 256.0,
        )

    # ─── LFR restore step (+ optional DCTFlicker recursive pass) — Tier 2 ───
    # Adds back the LF detail that MDegrain smoothed away.
    # Per Dogway smdegrain-4x.avsi:463-471.
    # Gated on `not GlobalO` because LFR consumes `output` (the MDegrain
    # result), which is only assigned inside the `if not GlobalO:` block
    # above. Globals>=3 mode skips output computation; LFR has nothing to
    # restore in that mode.
    if LFR_active and not GlobalO:
        # Gaussian-blurred LF band of source (mfilter, which defaults to inputP).
        from vsrgtools import gauss_blur
        mfilterLP = gauss_blur(mfilter, sigma=LFR_sigma)

        # DCTFlicker: recursive SMDegrain pass on the LF band to suppress
        # temporal flicker before LF restoration. Mirrors Dogway smdegrain-
        # 4x.avsi:495 — input is `mfilter` (not inputP) so a caller-supplied
        # mfilter flows into the recursion; chroma=False because the LF
        # restore consumes plane=0 only and chroma motion search on a
        # Gaussian-blurred band would be wasted compute. Recursion guard
        # via explicit LFR=False, DCTFlicker=False on the inner call;
        # UHDhalf=False too — the signature default is True, so on a UHD
        # source the recursion would otherwise run a SECOND half-res
        # motion search + ScaleVect pass on the LF band, which avsi:495
        # does not do (the LF-band pass is a full-resolution denoise).
        if DCTFlicker_active:
            DCTF_pass = SMDegrain(
                mfilter,                      # avsi:495 — was inputP
                tr           = max(1, int((tr + 2) // 3)),  # ceil(tr/3)
                thSAD        = thSAD // 2,
                blksize      = blksize,
                prefilter    = mfilterLP,
                pel          = 1,
                Str          = 0,             # capital Str — Python builtin
                                              # shadow + Tier 1 sig uses
                                              # capital Str (avsi:495 lowercase
                                              # was AVS case-insensitive)
                tv_range     = False,
                plane        = 0,
                chroma       = False,         # avsi:495 — drop wasted chroma MV
                truemotion   = False,
                UHDhalf      = False,         # recursion guard (avsi:495 —
                                              # LF-band pass is full-res)
                LFR          = False,         # recursion guard
                DCTFlicker   = False,         # recursion guard
            )
            # A1 verified: zsmooth.TemporalRepair exists and mode=4 matches
            # Dogway's "temp4" temporal repair semantics. Use it directly.
            mfilterLP = core.zsmooth.TemporalRepair(
                mfilterLP,
                gauss_blur(DCTF_pass, sigma=LFR_sigma),
                mode=4,
            )

        outputLP = gauss_blur(output, sigma=LFR_sigma)

        # Final merge expression per Dogway:
        #   output - (outputLP - mfilterLP) * Mavg / peak
        # i.e. add back the LF detail MDegrain lost, gated by motion-vector
        # confidence. RPN trace for "x y z - a * {peak} / -":
        #   x=output, y=outputLP, z=mfilterLP, a=Mavg
        #   -> [x, (y-z)*a/peak]
        #   -> [x - (y-z)*a/peak]
        EXPR = get_expr_fn()
        expr = f'x y z - a * {peak} / -'
        output = EXPR([output, outputLP, mfilterLP, Mavg], expr=[expr])

    # Contrasharp (only sharpens luma)
    if not GlobalO and if0:
        if if1:
            if interlaced:
                CClip = CClip.std.SeparateFields(tff=tff)
        else:
            CClip = inputP

    # Output
    if not GlobalO:
        if if0:
            if interlaced:
                if ifC:
                    return Weave(ContraSharpening(output, CClip, planes=planes), tff=tff)
                else:
                    return Weave(sharpen.LSFmod(output, strength=contrasharp, source=CClip, Lmode=0, soothe=False, defaults='slow'), tff=tff)
            elif ifC:
                return ContraSharpening(output, CClip, planes=planes)
            else:
                return sharpen.LSFmod(output, strength=contrasharp, source=CClip, Lmode=0, soothe=False, defaults='slow')
        elif interlaced:
            return Weave(output, tff=tff)
        else:
            return output
    else:
        return input

# Helpers

def get_expr_fn():
    """
    Return the best available Expr backend, in order of preference:
    llvmexpr → akarin → cranexpr → std.Expr.

    Centralises the capability-detection logic that previously appeared
    inline at 6 different sites in this module (every Expr call site was
    duplicating the same 4-way `hasattr(core, ...)` chain). Future plugin
    changes (add new backend, drop one) are now one-place.

    The returned function has the same signature as `core.std.Expr`:
    `EXPR(clips: Sequence[VideoNode] | VideoNode, expr: list[str]) -> VideoNode`.
    The order is a heuristic, not a benchmark: llvmexpr and akarin are both
    LLVM-JIT; llvmexpr is listed first because it targets host SIMD (AVX-512),
    whereas akarin's host-feature detection has historically been limited on newer
    LLVM — but a host-tuned akarin is comparable, and for these short expressions
    the difference is marginal (the mvu motion stages dominate runtime anyway).
    cranexpr (Cranelift JIT) then std.Expr (interpreter) follow. All produce
    identical pixel output for a given RPN expression.
    """
    if hasattr(core, 'llvmexpr'):
        return core.llvmexpr.Expr
    if hasattr(core, 'akarin'):
        return core.akarin.Expr
    if hasattr(core, 'cranexpr'):
        return core.cranexpr.Expr
    return core.std.Expr


def pick_bm3d_plugin():
    """
    Pick the best available BM3D plugin (cuda → cpu), raise if neither is
    installed.

    Returns the plugin namespace (e.g. `core.bm3dcuda`) so the caller can
    invoke `pkg.BM3D(...)`, `pkg.BM3Dv2(...)`, etc. directly. CUDA is
    preferred at runtime and falls back to CPU where the CUDA plugin is
    not installed (e.g. non-NVIDIA hosts).

    Used by `prefilter_clip` mode 5. Single call site today, but the
    pattern (capability detection with explicit fallback + clear install-
    hint error message) is worth having factored out.
    """
    if hasattr(core, 'bm3dcuda'):
        return core.bm3dcuda
    if hasattr(core, 'bm3dcpu'):
        return core.bm3dcpu
    raise vs.Error(
        "smdegrain_bis: BM3D prefilter (mode 5) requires the "
        "bm3dcpu or bm3dcuda VapourSynth plugin. Install one of them, "
        "or pass prefilter=<VideoNode> with a pre-built clip."
    )


def derive_lfr_sigma(LFR, w: int, h: int):
    """
    Resolve the user-facing `LFR` parameter into a pixel-space Gaussian
    sigma for `vsrgtools.gauss_blur`.

    Accepts:
      - `False` / `None`     → LFR disabled, sigma 0.0
      - `True`               → Dogway's default sigma = 3.46 * (w / 1920)
                               (pixel-space; ~-3 dB at 300 Hz on 1920-wide)
      - numeric Hz cutoff    → sigma = Fs / (k · 2π) where
                               Fs = max(w, h) · 2,
                               k  = √(ln2/2) · max(Hz, 50)
                               (the 50 Hz floor avoids degenerate sigmas
                               that blow up past image size and crash
                               `gauss_blur`)
      - 0 / negative Hz      → LFR disabled

    Formula source: Dogway's smdegrain-4x.avsi:215-217. The math is pure
    so this lives outside the VS pipeline — unit-testable without a core.

    Returns: `(active: bool, sigma: float)`.
    """
    if LFR is True:
        return True, 3.46 * (w / 1920.0)
    if LFR is False or LFR is None:
        return False, 0.0
    try:
        hz = float(LFR)
    except (TypeError, ValueError):
        raise vs.Error(f"smdegrain_bis: LFR must be False, True, or a numeric Hz cutoff; got {LFR!r}")
    if hz <= 0:
        return False, 0.0
    hz_clamped = max(hz, 50.0)
    Fs         = max(w, h) * 2.0
    k          = math.sqrt(math.log(2) / 2.0) * hz_clamped
    return True, Fs / (k * 2.0 * math.pi)


def expand_mask_to_yuv(mask: vs.VideoNode, target: vs.VideoNode) -> vs.VideoNode:
    """
    Restore an 8-bit `mv.Mask` output to the format/depth/plane-count of
    `target` so it can drive a per-pixel merge with full-bit-depth clips.

    Two cases the caller cares about:
      1. GRAY mask (single-plane, plane=0 path) + YUV target
         → `resize.Point` the Y mask to target bit depth, downscale to
           the chroma subsampling resolution, ShufflePlanes(Y,Y',Y') to
           build a YUV multi-plane. Matches Dogway's `mskY_to_YYY` pattern
           in smdegrain-4x.avsi:420 (Avisynth's equivalent of replicating
           luma to chroma planes by hand).
      2. Same plane count, only bit-depth or subsampling differs
         → straight `resize.Point` with target's format.

    No-op if formats already match.

    Args:
        mask:   8-bit single-plane GRAY or multi-plane YUV (whatever
                `mv.Mask` produced for the source it ran against).
        target: clip whose `.format` and `.width/.height` the mask should
                be restored to.

    Returns: mask in target's format, identical dimensions.
    """
    if mask.format.num_planes == 1 and target.format.num_planes > 1:
        _y_fmt_id = core.query_video_format(
            color_family=vs.GRAY, sample_type=vs.INTEGER,
            bits_per_sample=target.format.bits_per_sample,
            subsampling_w=0, subsampling_h=0,
        ).id
        _cw = target.width  >> target.format.subsampling_w
        _ch = target.height >> target.format.subsampling_h
        # Single resize.Point doing both bit-depth + spatial in one node;
        # _y gets a separate one-shot depth bump (no spatial change).
        _y  = mask.resize.Point(format=_y_fmt_id)
        _uv = mask.resize.Point(format=_y_fmt_id, width=_cw, height=_ch)
        return core.std.ShufflePlanes(
            [_y, _uv, _uv], planes=[0, 0, 0], colorfamily=vs.YUV)
    if mask.format.id != target.format.id:
        return mask.resize.Point(format=target.format.id)
    return mask


def mv_sadmask_chain(vectors, target: vs.VideoNode, gamma: float,
                     thscd1: int, thscd2: int,
                     ml: float = 50.0, scval: float = 255.0) -> vs.VideoNode:
    """
    Build the averaged motion-vector confidence mask used by LFR, on the
    mvutensils backend.

    `mvu.SADMask` is `mv.Mask(kind=1)` — but it takes **no source clip** (the
    mask derives from the vector clip alone) and emits at the vector clip's
    native bit depth. So dubhater/vapoursynth-mvtools' 8-bit-only constraint
    (MVMask.cpp:321) and the 10→8→10 `resize.Point` round trip it forced are
    both gone (SMDEGRAIN_BIS_IMPROVEMENTS.md §7).

    Pipeline:
      1. For each vector clip, run `mvu.SADMask` at native depth. `ysc`→`scval`,
         `kind=1` is implied by the function choice, `thscd2` is rescaled to a
         0-100 percentage (mvtools 130/256 ⇒ 50.78).
      2. Average them (`Average()` — multi-clip pixel mean via Expr).
      3. Restore to `target.format` via `expand_mask_to_yuv()` (Gray→YUV
         replication when the target has chroma; the bit-depth round trip is
         no longer part of it).

    Args:
        vectors:  list of motion vector clips in Degrain order [bw1,fw1,…].
        target:   clip whose format/dimensions the final mask should match.
        gamma:    SADMask gamma (2.222 with DCTFlicker on, else 0.5).
        thscd1:   SCD threshold (passed through, maps 1:1).
        thscd2:   mvtools-scale SCD2 (0-256); rescaled to mvu's 0-100 percentage.
        ml/scval: SADMask constants (Dogway's ml=50, ysc=255).

    Returns: averaged confidence mask in target's format.
    """
    per_delta_masks = [
        core.mvu.SADMask(vec, ml=ml, gamma=gamma, scval=scval,
                         thscd1=thscd1, thscd2=thscd2 * 100.0 / 256.0)
        for vec in vectors
    ]
    Mavg = Average(per_delta_masks)
    return expand_mask_to_yuv(Mavg, target)


def Average(clips: Sequence[vs.VideoNode]) -> vs.VideoNode:
    """
    Pixel-wise arithmetic mean of N clips.

    VapourSynth has no built-in multi-clip pixel mean — `core.std.AverageFrames`
    is *temporal* (single clip, N adjacent frames with weights), not what we
    need. The Avisynth `Average(c1, c2, …)` function (used by Dogway's
    smdegrain-4x.avsi at line 419 for the LFR mask blend) has no direct VS
    equivalent unless you pull in a third-party plugin (`eoe-nephren.average`
    → `core.average.Mean`).

    This helper synthesises the same operation via `core.std.Expr` with an RPN
    sum-and-divide: for N clips, the expression is `x y + z + a + … + N /`.
    `std.Expr` input variables are `x`, `y`, `z`, then `a`, `b`, `c`, … —
    capping the helper at 26 inputs.

    Args:
        clips: List of clips to average. All must have the same format and
               dimensions (a hard requirement of `std.Expr`).

    Returns:
        Per-pixel arithmetic mean of `clips`, in the input format.
    """
    n = len(clips)
    if n == 0:
        raise vs.Error("Average: at least one clip is required")
    if n == 1:
        return clips[0]
    if n > 26:
        raise vs.Error(f"Average: at most 26 clips supported by std.Expr (got {n})")
    def _var(i): return ('x', 'y', 'z')[i] if i < 3 else chr(ord('a') + i - 3)
    expr = _var(0) + ''.join(' ' + _var(i) + ' +' for i in range(1, n)) + f' {n} /'
    return get_expr_fn()(list(clips), expr=[expr])

def _scale_csad(luma_active, chroma_active, sub_w, sub_h, is_hd, ts=False):
    """
    Compute the chroma-SAD exponent k. thSADC = thSAD * 0.755 * 0.25 * 2**k.
    Matches Dogway's smdegrain-4x.avsi:158-165 formula (explicit branches
    per subsampling family for readability + future divergence).

    sub_w / sub_h: VS format.subsampling_w / subsampling_h.
      4:2:0 -> sub_w=1, sub_h=1
      4:2:2 -> sub_w=1, sub_h=0
      4:4:4 -> sub_w=0, sub_h=0
      YV411 -> sub_w=2, sub_h=0  (rare)
    """
    if not luma_active:
        return 2
    if not chroma_active:
        return -2
    if sub_w == 1 and sub_h == 1:   # 4:2:0
        return (1 if ts else 2) if is_hd else 0
    if sub_w == 1 and sub_h == 0:   # 4:2:2
        return (1 if ts else 2) if is_hd else 0
    if sub_w == 0 and sub_h == 0:   # 4:4:4
        return (1 if ts else 2) if is_hd else 1
    if sub_w == 2 and sub_h == 0:   # YV411
        return 0 if is_hd else -1
    # Unknown / unusual subsampling — fall through to 4:2:0-ish defaults
    return (1 if ts else 2) if is_hd else 0


def DitherLumaRebuild(src, s0=2., c=0.0625, chroma=True, tv_range=True):
    # Converts luma (and chroma) to PC levels, and optionally allows tweaking for pumping up the darks. (for the clip to be fed to motion search only)
    # By courtesy of cretindesalpes. (https://forum.doom9.org/showthread.php?p=1548318)

    if not isinstance(src, vs.VideoNode):
        raise TypeError("DitherLumaRebuild: This is not a clip!")

    # If tv_range=False, input is already PC-range — TV->PC expansion is a
    # no-op by definition. Return src unchanged.
    if not tv_range:
        return src

    bd = src.format.bits_per_sample
    isFLOAT = src.format.sample_type == vs.FLOAT
    i = 0.00390625 if isFLOAT else 1 << (bd - 8)

    x = 'x {} /'.format(i) if bd != 8 else 'x'
    expr = 'x 128 * 112 /' if isFLOAT else '{} 128 - 128 * 112 / 128 + {} *'.format(x, i)
    k = (s0 - 1) * c
    t = '{} 16 - 219 / 0 max 1 min'.format(x)
    c1 = 1 + c
    c2 = c1 * c
    e = '{} {} {} {} {} + / - * {} 1 {} - * + {} *'.format(k, c1, c2, t, c, t, k, 256*i)
    EXPR = get_expr_fn()
    return EXPR([src], [e] if src.format.num_planes == 1 else [e, expr if chroma else ''])
    
# Taken from havsfunc
def KNLMeansCL(
    clip: vs.VideoNode,
    d: Optional[int] = None,
    a: Optional[int] = None,
    s: Optional[int] = None,
    h: Optional[float] = None,
    wmode: Optional[int] = None,
    wref: Optional[float] = None,
    device_type: Optional[str] = None,
    device_id: Optional[int] = None,
) -> vs.VideoNode:
    if not isinstance(clip, vs.VideoNode):
        raise vs.Error('KNLMeansCL: this is not a clip')

    if clip.format.color_family != vs.YUV:
        raise vs.Error('KNLMeansCL: this wrapper is intended to be used only for YUV format')

    # device_id=None means "use the default device"; mirror Dogway's
    # smdegrain-4x.avsi:1275 `Default(gpuid, 0)` and the BM3D dispatch
    # normalization at prefilter_clip mode 5.
    if device_id is None:
        device_id = 0

    subsampled = clip.format.subsampling_w > 0 or clip.format.subsampling_h > 0
    # Dispatch order: nlm_cuda → nlm_ispc → knlm. Caller's gpu=true intent
    # (which only sets device_id when capabilities.cuda_available=true)
    # MUST land on the CUDA path when available — but nlm_ispc is shipped
    # in plugins.default on every worker, so checking it first would
    # silently win on hybrid workers. CPU ispc remains the next best
    # choice when no CUDA plugin is loaded.
    #
    # Bug fix: when running the two-pass Y + UV chain on subsampled clips,
    # the second pass MUST operate on the Y-denoised clip — NOT on a
    # method bound to the original. `clip.X.NLMeans` is a bound method;
    # rebinding `clip` does not rebind the method, so the previous form
    # silently discarded the Y-pass result.
    if hasattr(core, 'nlm_cuda'):
        if subsampled:
            clip = clip.nlm_cuda.NLMeans(d=d, a=a, s=s, h=h, channels='Y', wmode=wmode, wref=wref, device_id=device_id)
            return clip.nlm_cuda.NLMeans(d=d, a=a, s=s, h=h, channels='UV', wmode=wmode, wref=wref, device_id=device_id)
        return clip.nlm_cuda.NLMeans(d=d, a=a, s=s, h=h, channels='YUV', wmode=wmode, wref=wref, device_id=device_id)
    if hasattr(core, 'nlm_ispc'):
        if subsampled:
            clip = clip.nlm_ispc.NLMeans(d=d, a=a, s=s, h=h, channels='Y', wmode=wmode, wref=wref)
            return clip.nlm_ispc.NLMeans(d=d, a=a, s=s, h=h, channels='UV', wmode=wmode, wref=wref)
        return clip.nlm_ispc.NLMeans(d=d, a=a, s=s, h=h, channels='YUV', wmode=wmode, wref=wref)
    if subsampled:
        clip = clip.knlm.KNLMeansCL(d=d, a=a, s=s, h=h, channels='Y', wmode=wmode, wref=wref, device_type=device_type, device_id=device_id)
        return clip.knlm.KNLMeansCL(d=d, a=a, s=s, h=h, channels='UV', wmode=wmode, wref=wref, device_type=device_type, device_id=device_id)
    return clip.knlm.KNLMeansCL(d=d, a=a, s=s, h=h, channels='YUV', wmode=wmode, wref=wref, device_type=device_type, device_id=device_id)
        
# ── mvtools → mvutensils mapping helpers ──────────────────────────────────────
# Every constant below is verified against mvutensils source (commit 3d12331 ==
# PyPI wheel v2) or a running cmp; see SMDEGRAIN_BIS_IMPROVEMENTS.md §2-§7.

# mvtools search id → mvu search id. mvu enum (Analyse.cpp:210): 0 logarithmic/
# diamond, 1 exhaustive, 2 hexagon (Hex2), 3 UMH, 4 horizontal, 5 vertical.
# mvtools enum: 0 OneTimeSearch, 1 NStepSearch, 2 diamond, 3 exhaustive,
# 4 hexagon, 5 UMH, 6 horizontal exhaustive, 7 vertical exhaustive — a uniform
# -2 offset for 2..7 (verified against Dogway's SMDegrain.html:397-404, whose
# `int "search"` is a pass-through). Only mvtools 0/1 (OneTimeSearch/NStepSearch)
# were dropped by mvu and have no equivalent. Filter default search=4 → 2.
_MVU_SEARCH_MAP = {2: 0, 3: 1, 4: 2, 5: 3, 6: 4, 7: 5}


def _map_search(mvtools_search):
    try:
        return _MVU_SEARCH_MAP[mvtools_search]
    except KeyError:
        raise vs.Error(
            f"smdegrain_bis: search={mvtools_search} has no mvutensils equivalent "
            f"(mvu dropped mvtools' OneTimeSearch/NStepSearch; supported "
            f"{sorted(_MVU_SEARCH_MAP)} = diamond/exhaustive/hexagon/UMH/"
            f"horizontal/vertical)."
        )


# mvtools MDegrain `plane` (0=Y,1=U,2=V,3=UV,4=YUV) → mvu Degrain `planes` list.
_PLANE_TO_PLANES = {0: [0], 1: [1], 2: [2], 3: [1, 2], 4: [0, 1, 2]}


def _plane_to_planes(plane):
    try:
        return _PLANE_TO_PLANES[plane]
    except KeyError:
        raise vs.Error(f"smdegrain_bis: plane={plane} out of range (0..4)")


def _truemotion_analyse(truemotion, mvglobal):
    """mvtools `truemotion` preset → explicit mvu Analyse penalty args.

    truemotion=False (large clips, the whole grid) is byte-identical-validated.
    truemotion=True (small clips: pel=2, blksize=8) is the CALIBRATION-OPTIMAL
    match to mvtools' preset — each value was swept and is the best achievable
    (SMDEGRAIN_BIS_IMPROVEMENTS.md §14.4): mvlambda=1000 (mvu multiplies it by
    blksize²/64 internally, = mvtools' lambda=1000·blksize²/64), lsad=1200,
    pnew=pzero=50, plevel=1. The residual mvu↔mvtools divergence when penalties
    engage (SSIM 0.999688 on real content) is mvu's rewritten penalty MATH, not a
    value error — mvu's own defaults are further from mvtools, and no value maps
    closer. tests/truemotion_small.py exercises this path (plumbing byte-exact;
    penalties are a no-op on a synthetic pan, so the real-content SSIM is the
    penalty-value evidence).
    """
    if truemotion:
        d = dict(mvlambda=1000, lsad=1200, pnew=50, pzero=50, plevel=1, pglobal=0)
    else:
        d = dict(mvlambda=0, lsad=400, pnew=0, pzero=0, plevel=0, pglobal=0)
    d['globalmv'] = int(bool(mvglobal))
    return d


# ── UHDhalf vector scaling ────────────────────────────────────────────────────
# mvutensils has no vector-scaling op and manipmv cannot read mvu's vectors, so
# the port scales them with the native `mvuscale` filter (native/mvuscale.cpp):
# geometry props ×s, packed vectors ×s, per-block SAD ×s² (SMDEGRAIN_BIS_
# IMPROVEMENTS.md §5/§6). The pure-Python equivalent was ~6x slower (marshals
# ~96k int64 props/frame through the GIL), so it was dropped as a fallback; its
# reference math and a prop-level equality proof live in
# tests/scale_vect_equality.py. core.mvuscale presence is checked at filter
# creation whenever UHDhalf is active.
def scale_vect(vec, s=2):
    """manipmv.ScaleVect(s, s) for mvu vector clips, via the native mvuscale
    filter (byte-identical to the Python reference in the equality test)."""
    return core.mvuscale.ScaleVect(vec, scale=s)


def get_motion_vectors(super_search, refine, analyse_params, recalc_params,
                       tr, interlaced, vector_scale=None):
    """
    Build the mvu vector list in Degrain order [bw1, fw1, bw2, fw2, …].

    super_search:  mvu super at search resolution (full pyramid).
    refine:        mvu super for Recalculate (single-level), or None.
    analyse_params: mvu Analyse kwarg dict (no `delta`/`radius` — supplied here).
    recalc_params: list of per-pass Recalculate kwarg dicts (one per RefineMotion
                   pass, coarsest→finest block size), or None for no refinement.
    tr:            temporal radius. Interlaced caps the delta set at 2,4,6,
                   matching the original ladder (never past Degrain3 interlaced).
    vector_scale:  UHDhalf scale factor (2) applied to every vector, or None.

    mvu's `delta` SIGN carries direction: positive = backward (past), negative =
    forward (future). Progressive uses AnalyseMany, which emits the correctly
    paired list and removes the sign hand-wiring; interlaced needs manual even
    deltas, so the sign is set explicitly there (the single most dangerous line
    of the port — getting it backwards denoises the wrong direction silently).
    """
    if interlaced:
        deltas = [2, 4, 6][:min(tr, 3)]
        vecs = []
        for d in deltas:
            bw = core.mvu.Analyse(super_search, delta=d,  **analyse_params)   # past
            fw = core.mvu.Analyse(super_search, delta=-d, **analyse_params)   # future
            vecs += [bw, fw]
    else:
        # AnalyseMany(radius=tr) already returns [bw1, fw1, bw2, fw2, …].
        vecs = list(core.mvu.AnalyseMany(super_search, radius=tr, **analyse_params))

    if recalc_params:
        # RefineMotion=N: N chained Recalculate passes, each re-searching the whole
        # vector list at a smaller block size with the previous pass's vectors as
        # predictors. Recalculate takes AND returns the whole list, so passes chain
        # on the same (coarsest-built) refine super. N=1 is the single Dogway pass.
        for _p in recalc_params:
            vecs = list(core.mvu.Recalculate(refine, vecs, **_p))

    if vector_scale is not None:
        vecs = [scale_vect(v, vector_scale) for v in vecs]

    return vecs
    
def Weave(clip: vs.VideoNode, tff: Optional[bool] = None) -> vs.VideoNode:
    if not isinstance(clip, vs.VideoNode):
        raise vs.Error('Weave: this is not a clip')

    if tff is None:
        with clip.get_frame(0) as f:
            if f.props.get('_Field') not in [1, 2]:
                raise vs.Error('Weave: tff was not specified and field order could not be determined from frame properties')

    return clip.std.DoubleWeave(tff=tff)[::2]

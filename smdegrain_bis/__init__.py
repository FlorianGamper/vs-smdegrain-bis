"""
smdegrain_bis — a VapourSynth motion-compensated temporal denoiser.

A mvutensils-based descendant of Dogway's Avisynth SMDegrain, via Selur's
VapourSynth `smdegrain.py` port (v3.1.2d baseline). See README.md for the full
lineage, the mvtools→mvutensils port, and how it works.

    from smdegrain_bis import SMDegrain
    out = SMDegrain(clip, tr=2, thSAD=400, RefineMotion=True)

⚠ `UHDhalf=True` requires the native `mvuscale` plugin (native/mvuscale.cpp,
  built via native/build.sh). See SMDEGRAIN_BIS_IMPROVEMENTS.md §5/§6.
"""

from .smdegrain import SMDegrain

__all__ = ["SMDegrain"]
__version__ = "0.1.3"

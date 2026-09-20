"""
smdegrain_bis — a VapourSynth motion-compensated temporal denoiser.

A mvutensils-based descendant of Dogway's Avisynth SMDegrain, via Selur's
VapourSynth `smdegrain.py` port (v3.1.2d baseline). See README.md for the full
lineage, the mvtools→mvutensils port, and how it works.

    from smdegrain_bis import SMDegrain
    out = SMDegrain(clip, tr=2, thSAD=400, RefineMotion=True)

⚠ `UHDhalf=True` requires the native `mvuscale` plugin. Install it with the
  extra — `pip install smdegrain-bis[uhdhalf]` — or build it from
  native/mvuscale.cpp via native/build.sh and drop the .so into your
  VapourSynth plugins directory.
"""

from .smdegrain import SMDegrain

__all__ = ["SMDegrain"]
__version__ = "0.4.1"

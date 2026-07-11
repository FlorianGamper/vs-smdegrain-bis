// mvuscale — a native replacement for the pure-Python scale_vect() used by
// smdegrain_bis' UHDhalf path (SMDEGRAIN_BIS_IMPROVEMENTS.md §5/§6).
//
// manipmv.ScaleVect cannot read mvutensils' vectors, and mvu ships no scaling
// op, so the port scales the vectors in Python. That marshals ~96k int64 props
// per frame through the GIL and is ~4x slower than the mvtools+manipmv UHDhalf
// path. This filter does the same work in C++: it reads mvu's plain-int frame
// props, scales geometry by s, packed vectors (low32=x, high32=y) by s, and
// per-block SAD by s*s, releasing the GIL so frames process in parallel.
//
// The operation is memory-bound (array I/O + the unavoidable frame copy), so
// it is deliberately scalar; -O3 auto-vectorises the trivial loops with the
// x86-64 baseline (SSE2), keeping the .so portable (no AVX runtime requirement).
//
// Boundary frames carry geometry but no Vectors/SAD (§6.1); those are simply
// left untouched.

#include <cstdint>
#include <string>
#include <vector>

#include "VapourSynth4.h"
#include "VSHelper4.h"

namespace {

struct ScaleVectData {
    VSNode *node;
    int scale;
    std::string prefix;
};

// Geometry props that describe a resolution and therefore scale by `s`.
const char *const kGeomSuffix[] = {
    "BlkSizeX", "BlkSizeY", "OverlapX", "OverlapY",
    "Width", "Height", "RealWidth", "RealHeight", "HPad", "VPad",
};

void scaleProps(VSMap *props, const ScaleVectData *d, const VSAPI *vsapi) {
    const int64_t s = d->scale;
    const int64_t s2 = s * s;
    const std::string &pfx = d->prefix;

    // Scalar geometry props: present on every frame, always scaled.
    for (const char *suffix : kGeomSuffix) {
        const std::string key = pfx + "Analysis" + suffix;
        int err = 0;
        int64_t v = vsapi->mapGetInt(props, key.c_str(), 0, &err);
        if (!err)
            vsapi->mapSetInt(props, key.c_str(), v * s, maReplace);
    }

    // Packed motion vectors: low 32 bits = x, high 32 bits = y (two's
    // complement). Absent on sequence-boundary frames — skip if so.
    const std::string vkey = pfx + "AnalysisVectors";
    int verr = 0;
    const int64_t *vec = vsapi->mapGetIntArray(props, vkey.c_str(), &verr);
    if (verr || !vec)
        return;
    const int vcount = vsapi->mapNumElements(props, vkey.c_str());

    // Copy the SAD array out before we overwrite any keys (mapSet* may
    // reallocate the map's storage and invalidate `vec`/`sad`).
    const std::string skey = pfx + "AnalysisSAD";
    int serr = 0;
    const int64_t *sad = vsapi->mapGetIntArray(props, skey.c_str(), &serr);
    const int scount = (!serr && sad) ? vsapi->mapNumElements(props, skey.c_str()) : 0;

    std::vector<int64_t> vout(vcount);
    for (int i = 0; i < vcount; ++i) {
        // Scaling a two's-complement value is a masked multiply: the low 32
        // bits of (signed_half * s) are correct for either sign (multiplication
        // is well-defined mod 2^32). Vectors are small, so no 32-bit overflow.
        const uint64_t packed = static_cast<uint64_t>(vec[i]);
        const int32_t x = static_cast<int32_t>(packed & 0xFFFFFFFFu);
        const int32_t y = static_cast<int32_t>(packed >> 32);
        const uint32_t nx = static_cast<uint32_t>(static_cast<int64_t>(x) * s);
        const uint32_t ny = static_cast<uint32_t>(static_cast<int64_t>(y) * s);
        vout[i] = static_cast<int64_t>(static_cast<uint64_t>(nx) |
                                       (static_cast<uint64_t>(ny) << 32));
    }

    std::vector<int64_t> sout(scount);
    for (int i = 0; i < scount; ++i)
        sout[i] = sad[i] * s2;  // SAD scales with block area (s*s)

    vsapi->mapSetIntArray(props, vkey.c_str(), vout.data(), vcount);
    if (scount)
        vsapi->mapSetIntArray(props, skey.c_str(), sout.data(), scount);
}

const VSFrame *VS_CC scaleVectGetFrame(int n, int activationReason, void *instanceData,
        void **frameData, VSFrameContext *frameCtx, VSCore *core, const VSAPI *vsapi) {
    ScaleVectData *d = static_cast<ScaleVectData *>(instanceData);
    if (activationReason == arInitial) {
        vsapi->requestFrameFilter(n, d->node, frameCtx);
    } else if (activationReason == arAllFramesReady) {
        const VSFrame *src = vsapi->getFrameFilter(n, d->node, frameCtx);
        VSFrame *dst = vsapi->copyFrame(src, core);
        vsapi->freeFrame(src);
        scaleProps(vsapi->getFramePropertiesRW(dst), d, vsapi);
        return dst;
    }
    return nullptr;
}

void VS_CC scaleVectFree(void *instanceData, VSCore *core, const VSAPI *vsapi) {
    ScaleVectData *d = static_cast<ScaleVectData *>(instanceData);
    vsapi->freeNode(d->node);
    delete d;
}

void VS_CC scaleVectCreate(const VSMap *in, VSMap *out, void *userData,
        VSCore *core, const VSAPI *vsapi) {
    ScaleVectData d{};
    d.node = vsapi->mapGetNode(in, "clip", 0, nullptr);

    int err = 0;
    d.scale = vsapi->mapGetIntSaturated(in, "scale", 0, &err);
    if (err)
        d.scale = 2;

    const char *pfx = vsapi->mapGetData(in, "prefix", 0, &err);
    d.prefix = err ? "MVUtensils" : pfx;

    if (d.scale < 1) {
        vsapi->mapSetError(out, "ScaleVect: scale must be >= 1");
        vsapi->freeNode(d.node);
        return;
    }

    const VSVideoInfo *vi = vsapi->getVideoInfo(d.node);
    ScaleVectData *data = new ScaleVectData(std::move(d));
    VSFilterDependency deps[] = {{data->node, rpStrictSpatial}};
    vsapi->createVideoFilter(out, "ScaleVect", vi, scaleVectGetFrame, scaleVectFree,
                             fmParallel, deps, 1, data, core);
}

}  // namespace

VS_EXTERNAL_API(void) VapourSynthPluginInit2(VSPlugin *plugin, const VSPLUGINAPI *vspapi) {
    vspapi->configPlugin("com.smdegrainbis.mvuscale", "mvuscale",
                         "Scale mvutensils vector clips (manipmv.ScaleVect replacement)",
                         VS_MAKE_VERSION(1, 0), VAPOURSYNTH_API_VERSION, 0, plugin);
    vspapi->registerFunction("ScaleVect",
                             "clip:vnode;scale:int:opt;prefix:data:opt;",
                             "clip:vnode;",
                             scaleVectCreate, nullptr, plugin);
}

cmake_minimum_required(VERSION 3.18)
project(mvuscale LANGUAGES CXX)

# mvuscale — a native VapourSynth (API4) filter that scales mvutensils motion
# vectors for the smdegrain_bis UHDhalf path (the manipmv.ScaleVect replacement).
# It links no libpython and no libvapoursynth (VS resolves the API through the
# plugin entry point), so the binary is Python-version-independent: one build
# per (OS, arch) covers all Python 3.x.

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

add_library(mvuscale SHARED mvuscale.cpp)
target_include_directories(mvuscale PRIVATE include)   # vendored VS API4 headers

if(NOT MSVC)
  target_compile_options(mvuscale PRIVATE -O3 -Wall)
endif()

# Install under <site-packages>/vapoursynth/plugins/mvuscale/ — the same payload
# layout the PyPI VS-plugin wheels (manipmv, nlm_ispc, …) use, so a host that
# relocates that tree into its own plugins dir finds the .so unchanged, and a
# plain pip user gets it on sys.path for smdegrain_bis to load.
install(TARGETS mvuscale
        LIBRARY DESTINATION vapoursynth/plugins/mvuscale
        RUNTIME DESTINATION vapoursynth/plugins/mvuscale)

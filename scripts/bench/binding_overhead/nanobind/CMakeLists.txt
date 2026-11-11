# A throwaway nanobind module over the C ABI of the wasm module's source (native/wasm/rp2040_wasm.cpp), to put the cost of
# one call from Python next to ctypes and Cython (docs/records/0109-binding-and-device-runtime-measurements.md).
#
#   cmake -S scripts/bench/binding_overhead/nanobind -B build/nb -G Ninja -DCMAKE_BUILD_TYPE=Release \
#         -DPython_EXECUTABLE=<python> -Dnanobind_DIR=$(<python> -c "import nanobind; print(nanobind.cmake_dir())")
#   cmake --build build/nb
cmake_minimum_required(VERSION 3.18)
project(nbcore LANGUAGES CXX)
find_package(Python 3.10 COMPONENTS Interpreter Development.Module REQUIRED)
find_package(nanobind CONFIG REQUIRED)

set(REPO ${CMAKE_CURRENT_SOURCE_DIR}/../../../..)
# the core as the wasm module and `scripts/bench/wasm_core.py` build it: no exceptions, no RTTI (only the binding below needs them)
add_library(core OBJECT ${REPO}/src/rp2040py/native/wasm/rp2040_wasm.cpp)
target_include_directories(core PRIVATE ${REPO}/src/rp2040py/native/core)
target_compile_options(core PRIVATE -std=c++17 -O2 -fno-exceptions -fno-rtti -ffp-contract=off -fPIC)

nanobind_add_module(nbcore NB_STATIC binding.cpp)
target_sources(nbcore PRIVATE $<TARGET_OBJECTS:core>)

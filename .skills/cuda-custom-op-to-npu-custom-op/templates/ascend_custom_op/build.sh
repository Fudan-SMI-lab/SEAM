#!/bin/bash
# =============================================================================
# Ascend C Custom Op - Build Script Template
# =============================================================================
# This script builds the custom operator package. It handles:
#   1. Creating the build output directory
#   2. Parsing CMakePresets.json for configuration
#   3. Running cmake configure + build
#   4. Cross-compilation support (if enabled in presets)
#
# USAGE:
#   ./build.sh              # Build with default target (package)
#   ./build.sh install      # Build and install (for shared library mode)
#
# PREREQUISITES:
#   - CANN toolkit installed (path set in CMakePresets.json)
#   - cmake >= 3.19 (for --preset support; falls back to manual opts for older)
#   - The cmake/ directory contains CANN-provided cmake modules
#
# NOTE: The cmake/util/preset_parse.py script is provided by the CANN SDK
#       and extracts cache variables from CMakePresets.json for older cmake.
# =============================================================================

set -euo pipefail

script_path=$(realpath "$(dirname "$0")")

BUILD_DIR="build_out"
HOST_NATIVE_DIR="host_native_tiling"
mkdir -p "$BUILD_DIR"
rm -rf "$BUILD_DIR"/*

opts=$(python3 "$script_path/cmake/util/preset_parse.py" "$script_path/CMakePresets.json")
ENABLE_CROSS="-DENABLE_CROSS_COMPILE=True"
ENABLE_BINARY="-DENABLE_BINARY_PACKAGE=True"
ENABLE_LIBRARY="-DASCEND_PACK_SHARED_LIBRARY=True"
cmake_version=$(cmake --version | grep "cmake version" | awk '{print $3}')

target=package
if [ "${1:-}"x != ""x ]; then target=$1; fi
if [[ $opts =~ $ENABLE_LIBRARY ]]; then target=install; fi

if [[ $opts =~ $ENABLE_CROSS ]] && [[ $opts =~ $ENABLE_BINARY ]]
then
  # Cross-compilation: build host tiling first, then cross-compile kernels
  if [ "$cmake_version" \< "3.19.0" ] ; then
    cmake -S . -B "$BUILD_DIR" $opts -DENABLE_CROSS_COMPILE=0
  else
    cmake -S . -B "$BUILD_DIR" --preset=default -DENABLE_CROSS_COMPILE=0
  fi
  cmake --build "$BUILD_DIR" --target cust_optiling
  mkdir "$BUILD_DIR/$HOST_NATIVE_DIR"
  cp "$(find "$BUILD_DIR" -name "libcust_opmaster_rt2.0.so")" "$BUILD_DIR/$HOST_NATIVE_DIR"
  cp -r "$BUILD_DIR/$HOST_NATIVE_DIR" .
  rm -rf "$BUILD_DIR"/*
  mv "$HOST_NATIVE_DIR" "$BUILD_DIR"
  host_native_tiling_lib=$(realpath "$(find "$BUILD_DIR" -type f -name "libcust_opmaster_rt2.0.so")")
  if [ "$cmake_version" \< "3.19.0" ] ; then
    cmake -S . -B "$BUILD_DIR" $opts -DHOST_NATIVE_TILING_LIB="$host_native_tiling_lib"
  else
    cmake -S . -B "$BUILD_DIR" --preset=default -DHOST_NATIVE_TILING_LIB="$host_native_tiling_lib"
  fi
  cmake --build "$BUILD_DIR" --target binary -j"$(nproc)"
  cmake --build "$BUILD_DIR" --target "$target" -j"$(nproc)"
else
  # Same-architecture build (most common case)
  if [ "$cmake_version" \< "3.19.0" ] ; then
    cmake -S . -B "$BUILD_DIR" $opts
  else
      cmake -S . -B "$BUILD_DIR" --preset=default
  fi
  cmake --build "$BUILD_DIR" --target binary -j"$(nproc)"
  cmake --build "$BUILD_DIR" --target "$target" -j"$(nproc)"
fi

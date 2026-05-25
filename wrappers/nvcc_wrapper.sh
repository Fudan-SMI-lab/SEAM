#!/bin/bash
# nvcc → mxcc wrapper for MACA GPU compatibility
# Translates common nvcc flags to mxcc equivalents

MXCC=/opt/maca/mxgpu_llvm/bin/mxcc
MACA_PATH=/opt/maca

# Start with basic mxcc args
ARGS=()

# Detect if we are compiling for device or linking
ARCH_FLAG_SEEN=false
for arg in "$@"; do
    case "$arg" in
        # Skip nvcc-specific flags
        --use_fast_math)
            ARGS+=("-use-fast-math")
            ;;
        --restrict)
            # mxcc may support --restrict (Clang-based), pass through
            ARGS+=("--restrict")
            ;;
        -arch=*)
            # Translate CUDA arch to MACA arch
            ARCH="${arg#-arch=}"
            case "$ARCH" in
                sm_80|sm_8*|80|all)
                    ARGS+=("--offload-arch=xcore1000")
                    ;;
                *)
                    ARGS+=("--offload-arch=xcore1000")
                    ;;
            esac
            ARCH_FLAG_SEEN=true
            ;;
        -gencode=*)
            # Skip gencode, not needed for MACA
            ;;
        -ccbin=*)
            # host compiler is handled via CMAKE_CUDA_HOST_COMPILER
            ;;
        --compiler-options=*|--compiler-bindir=*)
            # Skip nvcc-specific options
            ;;
        -Xcompiler=*)
            # Pass through to host compiler
            ARGS+=("$arg")
            ;;
        --cudart=*)
            # Skip, MACA links its own runtime
            ;;
        --cuda-path=*)
            # Already set via --maca-path
            ;;
        -D*|-I*|-L*|-l*|-O*|-g|-w|-std=*|-fPIC|-fpic)
            # Pass through common flags
            ARGS+=("$arg")
            ;;
        -c|-shared|-o)
            # Common compilation flags
            ARGS+=("$arg")
            ;;
        *.cu|*.c|*.cpp|*.o|*.a|*.so)
            # Input/output files
            ARGS+=("$arg")
            ;;
        *)
            # Pass through unknown args, but skip things that look like nvcc-specific
            if [[ "$arg" == --* ]] && [[ "$arg" != --maca-* ]] && [[ "$arg" != -* ]]; then
                # Skip unknown long opts that might be nvcc-specific
                :
            else
                ARGS+=("$arg")
            fi
            ;;
    esac
done

# Add MACA path if not already present
if ! echo "${ARGS[@]}" | grep -q "maca-path"; then
    ARGS+=("--maca-path=${MACA_PATH}")
fi

# Add offload arch if not specified
if ! $ARCH_FLAG_SEEN; then
    ARGS+=("--offload-arch=xcore1000")
fi

exec $MXCC "${ARGS[@]}"

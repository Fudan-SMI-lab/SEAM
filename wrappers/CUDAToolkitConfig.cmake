# CUDAToolkitConfig.cmake - Minimal CUDA toolkit config for MACA compatibility
# This tells CMake where to find the "CUDA" (MACA) toolkit components.

# Tell CMake we found the toolkit
set(CUDAToolkit_FOUND TRUE)

# Root of the "CUDA" installation
set(CUDAToolkit_ROOT "/opt/maca")

# Include directories - point to MACA includes
set(CUDAToolkit_INCLUDE_DIRS "/opt/maca/include" "/opt/maca/include/mcr")

# Library directories  
set(CUDAToolkit_LIBRARY_DIR "/opt/maca/lib64")
set(CUDAToolkit_LIBRARY_ROOT "/opt/maca/lib64")

# CUDA runtime library
if(NOT TARGET CUDA::cudart)
    find_library(CUDA_CUDART_LIBRARY
        NAMES mcart
        PATHS /opt/maca/lib64
        NO_DEFAULT_PATH
    )
    if(CUDA_CUDART_LIBRARY)
        add_library(CUDA::cudart SHARED IMPORTED)
        set_target_properties(CUDA::cudart PROPERTIES
            IMPORTED_LOCATION "${CUDA_CUDART_LIBRARY}"
            INTERFACE_INCLUDE_DIRECTORIES "${CUDAToolkit_INCLUDE_DIRS}"
        )
    else()
        # If we can't find the library, create an interface target anyway
        add_library(CUDA::cudart INTERFACE IMPORTED)
        set_target_properties(CUDA::cudart PROPERTIES
            INTERFACE_INCLUDE_DIRECTORIES "${CUDAToolkit_INCLUDE_DIRS}"
        )
    endif()
endif()

# CUDA driver library (MACA equivalent)
if(NOT TARGET CUDA::cuda_driver)
    find_library(CUDA_CUDA_LIBRARY
        NAMES maca
        PATHS /opt/maca/lib64
        NO_DEFAULT_PATH
    )
    if(CUDA_CUDA_LIBRARY)
        add_library(CUDA::cuda_driver SHARED IMPORTED)
        set_target_properties(CUDA::cuda_driver PROPERTIES
            IMPORTED_LOCATION "${CUDA_CUDA_LIBRARY}"
            INTERFACE_INCLUDE_DIRECTORIES "${CUDAToolkit_INCLUDE_DIRS}"
        )
    endif()
endif()

# Toolkit version (match MACA version)
set(CUDAToolkit_VERSION "3.5.3")
set(CUDAToolkit_VERSION_MAJOR 3)
set(CUDAToolkit_VERSION_MINOR 5)
set(CUDAToolkit_VERSION_PATCH 3)

# nvcc location - CMake will find our wrapper in the PATH
# This is set separately via CMAKE_CUDA_COMPILER or PATH

message(STATUS "MACA CUDAToolkit compatibility layer loaded")
message(STATUS "  Include: ${CUDAToolkit_INCLUDE_DIRS}")
message(STATUS "  Library: ${CUDAToolkit_LIBRARY_DIR}")

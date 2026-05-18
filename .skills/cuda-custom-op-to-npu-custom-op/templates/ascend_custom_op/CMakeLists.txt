# =============================================================================
# Ascend C Custom Op - CMakeLists.txt Template
# =============================================================================
# This is the top-level CMake file for an Ascend C custom operator project.
# It follows the standard CANN opp (operator package project) structure.
#
# PLACEHOLDERS:
#   {{VENDOR_NAME}} - Your vendor/organization name (e.g., "mycompany")
#
# STRUCTURE EXPECTED:
#   project_root/
#   |-- CMakeLists.txt          (this file)
#   |-- CMakePresets.json       (build configuration)
#   |-- build.sh                (build script)
#   |-- cmake/                  (CANN-provided cmake modules - copy from CANN SDK)
#   |   |-- config.cmake
#   |   |-- func.cmake
#   |   |-- intf.cmake
#   |   |-- makeself.cmake
#   |   `-- util/
#   |-- op_host/                (host-side: tiling + op definition)
#   |   `-- {{op_name}}.cpp
#   |-- op_kernel/              (device-side: Ascend C kernel)
#   |   `-- {{op_name_lower}}.cpp
#   |-- framework/              (optional: framework integration)
#   `-- scripts/                (install/upgrade scripts from CANN template)
# =============================================================================

cmake_minimum_required(VERSION 3.16.0)
project(opp)

# Include CANN-provided cmake modules
# These are copied from: $ASCEND_CANN_PACKAGE_PATH/compiler/tikcpp/ascendc_devkit/tikcpp/samples/cmake/
include(cmake/config.cmake)
include(cmake/func.cmake)
include(cmake/intf.cmake)

# Cross-compilation support (typically not needed for same-arch builds)
if(ENABLE_CROSS_COMPILE)
    if(${CMAKE_SYSTEM_PROCESSOR} STREQUAL x86_64)
        set(CROSS_COMPILE_PLATFORM aarch64)
    else()
        set(CROSS_COMPILE_PLATFORM x86_64)
    endif()
    set(PLATFORM ${CMAKE_SYSTEM_PROCESSOR})
    set(CMAKE_COMPILE_COMPILER_LIBRARY ${ASCEND_CANN_PACKAGE_PATH}/${PLATFORM}-linux/devlib/linux/${CROSS_COMPILE_PLATFORM}/)
    set(CMAKE_COMPILE_RUNTIME_LIBRARY ${ASCEND_CANN_PACKAGE_PATH}/${PLATFORM}-linux/devlib/${CROSS_COMPILE_PLATFORM}/)
    if(CMAKE_CROSS_LIBRARY_PATH)
        set(CMAKE_COMPILE_COMPILER_LIBRARY ${CMAKE_CROSS_LIBRARY_PATH})
        set(CMAKE_COMPILE_RUNTIME_LIBRARY ${CMAKE_CROSS_LIBRARY_PATH})
    endif()
    set(CMAKE_SYSTEM_PROCESSOR ${CROSS_COMPILE_PLATFORM})
    set(CMAKE_COMPILE ${CMAKE_CXX_COMPILER})
    set(CMAKE_CXX_COMPILER ${CMAKE_CROSS_PLATFORM_COMPILER})
else()
    set(CMAKE_COMPILE ${CMAKE_CXX_COMPILER})
endif()

# --- Subdirectories ---
# Each subdirectory has its own CMakeLists.txt provided by the CANN cmake modules.
# The op_host/ and op_kernel/ directories are auto-discovered.
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/framework)
    add_subdirectory(framework)
endif()
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/op_host)
    add_subdirectory(op_host)
endif()
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/op_kernel)
    add_subdirectory(op_kernel)
endif()
if(ENABLE_TEST AND EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/testcases)
    add_subdirectory(testcases)
endif()

# --- Vendor name injection into install/upgrade scripts ---
add_custom_command(OUTPUT ${CMAKE_BINARY_DIR}/scripts/install.sh ${CMAKE_BINARY_DIR}/scripts/upgrade.sh
    COMMAND mkdir -p ${CMAKE_BINARY_DIR}/scripts
    COMMAND cp -r ${CMAKE_SOURCE_DIR}/scripts/* ${CMAKE_BINARY_DIR}/scripts/
    COMMAND sed -i "s/vendor_name=customize/vendor_name=${vendor_name}/g" ${CMAKE_BINARY_DIR}/scripts/*
)
add_custom_target(modify_vendor ALL DEPENDS ${CMAKE_BINARY_DIR}/scripts/install.sh ${CMAKE_BINARY_DIR}/scripts/upgrade.sh)

get_system_info(SYSTEM_INFO)

# --- Version info generation ---
add_custom_target(gen_version_info ALL
        COMMAND bash ${CMAKE_CURRENT_SOURCE_DIR}/cmake/util/gen_version_info.sh ${ASCEND_CANN_PACKAGE_PATH} ${CMAKE_CURRENT_BINARY_DIR}
)

# --- Packaging (CPack) ---
if(NOT ASCEND_PACK_SHARED_LIBRARY)
    install(DIRECTORY ${CMAKE_BINARY_DIR}/scripts/ DESTINATION . FILE_PERMISSIONS OWNER_EXECUTE OWNER_READ GROUP_READ)
    install(FILES ${CMAKE_SOURCE_DIR}/custom.proto DESTINATION packages OPTIONAL)
    install(FILES ${CMAKE_CURRENT_BINARY_DIR}/version.info
            DESTINATION packages/vendors/${vendor_name}/)

    set(CPACK_PACKAGE_NAME ${CMAKE_PROJECT_NAME})
    set(CPACK_PACKAGE_VERSION ${CMAKE_PROJECT_VERSION})
    set(CPACK_PACKAGE_DESCRIPTION "CPack opp project")
    set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "CPack opp project")
    set(CPACK_PACKAGE_DIRECTORY ${CMAKE_INSTALL_PREFIX})
    set(CPACK_PACKAGE_FILE_NAME "custom_opp_${SYSTEM_INFO}.run")
    set(CPACK_GENERATOR External)
    set(CPACK_CMAKE_GENERATOR "Unix Makefiles")
    set(CPACK_EXTERNAL_ENABLE_STAGING TRUE)
    set(CPACK_EXTERNAL_PACKAGE_SCRIPT ${CMAKE_SOURCE_DIR}/cmake/makeself.cmake)
    set(CPACK_EXTERNAL_BUILT_PACKAGES ${CPACK_PACKAGE_DIRECTORY}/_CPack_Packages/Linux/External/${CPACK_PACKAGE_FILE_NAME}/${CPACK_PACKAGE_FILE_NAME})
    include(CPack)
endif()

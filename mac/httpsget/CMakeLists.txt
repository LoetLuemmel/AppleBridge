# httpsget — the guest-side https client behind the mac_https_get MCP tool.
# Build with Retro68 (host-side cross compiler), never with MPW SC: Crypto Ancienne
# needs 64-bit integers, which SC does not have.
#
#   cmake -S . -B build -DCRYANC=/path/to/cryanc \
#         -DCMAKE_TOOLCHAIN_FILE=/path/to/Retro68-build/toolchain/m68k-apple-macos/cmake/retro68.toolchain.cmake
#   cmake --build build            # -> build/httpsget.bin (MacBinary), deploy with mac_put_file
#
# CRYANC must point at a cryanc checkout carrying the two patches beside this file
# (or classilla/cryanc once #24 and #25 are merged).
cmake_minimum_required(VERSION 3.9)
project(httpsget C CXX)
set(CRYANC "${CMAKE_SOURCE_DIR}/../../../cryanc" CACHE PATH "cryanc source tree (patched)")
add_application(httpsget httpsget.c ${CRYANC}/cryanc.c)
target_include_directories(httpsget PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/shim ${CRYANC})
target_compile_definitions(httpsget PRIVATE NO_FUNNY_ALIGNMENT=1 BIG_STRING_SIZE=4096 __BIG_ENDIAN__=1 TTLS_NOSTACK=1 TLS_ENTROPY_HOOK=1)
# -fno-jump-tables: gcc 16 emits a PC-relative switch table whose 8-bit displacement
# overflows in main() ("value -1928 out of range") — measured 2026-08-30.
set_target_properties(httpsget PROPERTIES COMPILE_OPTIONS "-ffunction-sections;-Os;-fno-jump-tables")
set_target_properties(httpsget PROPERTIES LINK_FLAGS "-Wl,-gc-sections -Wl,--mac-strip-macsbug")

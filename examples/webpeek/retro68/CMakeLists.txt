# WebPeek 2 — https on System 7 without a gateway. Retro68 only (see mac/httpsget/README.md
# for the toolchain notes: no CONSOLE, -fno-jump-tables, patched cryanc).
#
#   cmake -S . -B build -DCRYANC=/path/to/cryanc \
#         -DCMAKE_TOOLCHAIN_FILE=/path/to/Retro68-build/toolchain/m68k-apple-macos/cmake/retro68.toolchain.cmake
#   cmake --build build          # -> build/WebPeek2.bin, deploy with mac_put_file
cmake_minimum_required(VERSION 3.9)
project(WebPeek2 C CXX)
set(CRYANC "${CMAKE_SOURCE_DIR}/../../../../cryanc" CACHE PATH "cryanc source tree (patched)")
set(HG "${CMAKE_SOURCE_DIR}/../../../mac/httpsget")
add_application(WebPeek2 webpeek2.c tlsfetch.c html2text.c ${CRYANC}/cryanc.c webpeek2.r CREATOR "WbPk")
target_include_directories(WebPeek2 PRIVATE ${CMAKE_SOURCE_DIR} ${HG} ${HG}/shim ${CRYANC})
target_compile_definitions(WebPeek2 PRIVATE NO_FUNNY_ALIGNMENT=1 BIG_STRING_SIZE=4096 __BIG_ENDIAN__=1 TLS_ENTROPY_HOOK=1)
set_target_properties(WebPeek2 PROPERTIES COMPILE_OPTIONS "-ffunction-sections;-Os;-fno-jump-tables")
set_target_properties(WebPeek2 PROPERTIES LINK_FLAGS "-Wl,-gc-sections -Wl,--mac-strip-macsbug")

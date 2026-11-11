#!/usr/bin/env bash
# Build and run the two benchmarks of docs/records/0109-binding-and-device-runtime-measurements.md:
#   call/  the cost of one call from a C host into a device written in Lua, QuickJS or MicroPython (and in C, as the floor)
#   lcd/   the project's ST7735S device, replayed over a recorded firmware event stream, in the same four languages
#
#   scripts/bench/script_devices/run.sh WORKDIR MICROPYTHON_CHECKOUT
#
# WORKDIR gets the downloaded sources (Lua 5.4.8, quickjs-ng 0.18.0 - untrusted downloads: built, never run from their directory), the
# builds and the traces. MICROPYTHON_CHECKOUT is a MicroPython tree (1.29.0 was used) whose ports/embed generates the embeddable sources.
# Needs a C compiler, make, curl, tar and `uv` (the trace comes from `demo/lcd_run.py` on the project's own native build).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
WORK="$(mkdir -p "${1:?WORKDIR}" && cd "$1" && pwd)"
MPTOP="$(cd "${2:?MICROPYTHON_CHECKOUT}" && pwd)"
CC="${CC:-cc}"
LUA_VERSION=5.4.8
QJS_VERSION=0.18.0

mkdir -p "$WORK/lua" "$WORK/qjs" "$WORK/mp" "$WORK/build" "$WORK/lcd"

fetch() { # url file
    [ -s "$2" ] || curl -sSL --max-time 90 -o "$2" "$1"
}

echo "== sources"
fetch "https://www.lua.org/ftp/lua-$LUA_VERSION.tar.gz" "$WORK/lua/lua.tgz" ||
    fetch "https://github.com/lua/lua/archive/refs/tags/v$LUA_VERSION.tar.gz" "$WORK/lua/lua.tgz"
fetch "https://github.com/quickjs-ng/quickjs/archive/refs/tags/v$QJS_VERSION.tar.gz" "$WORK/qjs/qjs.tgz"
(cd "$WORK/lua" && tar xzf lua.tgz)
(cd "$WORK/qjs" && tar xzf qjs.tgz)
LUA_SRC="$(echo "$WORK"/lua/lua-*/src)"
QJS_SRC="$(echo "$WORK"/qjs/quickjs-*)"

echo "== runtimes"
mkdir -p "$WORK/build/lua" "$WORK/build/qjs" "$WORK/build/mp"
(
    cd "$WORK/build/lua"
    for f in "$LUA_SRC"/*.c; do
        b="$(basename "$f" .c)"
        [ "$b" = lua ] || [ "$b" = luac ] || "$CC" -O2 -DLUA_USE_POSIX -c "$f" -o "$b.o"
    done
    ar rcs liblua.a ./*.o
)
(
    cd "$WORK/build/qjs"
    for f in dtoa libregexp libunicode quickjs; do "$CC" -O2 -D_GNU_SOURCE -I"$QJS_SRC" -c "$QJS_SRC/$f.c" -o "$f.o"; done
    ar rcs libqjs.a ./*.o
)
(
    cd "$WORK/mp"
    cp "$HERE/mpconfigport.h" .
    printf 'MICROPYTHON_TOP = %s\ninclude $(MICROPYTHON_TOP)/ports/embed/embed.mk\n' "$MPTOP" >micropython_embed.mk
    rm -rf build-embed micropython_embed
    make -f micropython_embed.mk -j"$(nproc)" >/dev/null
    cd "$WORK/build/mp"
    for f in $(find "$WORK/mp/micropython_embed" -name '*.c'); do
        "$CC" -O2 -I"$WORK/mp/micropython_embed" -I"$WORK/mp/micropython_embed/port" -I"$WORK/mp" \
            -I"$WORK/mp/micropython_embed/genhdr" -c "$f" -o "$(echo "$f" | md5sum | cut -c1-8)_$(basename "$f" .c).o"
    done
    ar rcs libmp.a ./*.o
)

INC=(-I"$LUA_SRC" -I"$QJS_SRC" -I"$WORK/mp/micropython_embed" -I"$WORK/mp/micropython_embed/port" -I"$WORK/mp"
    -I"$WORK/mp/micropython_embed/genhdr")
LIBS=("$WORK/build/mp/libmp.a" "$WORK/build/lua/liblua.a" "$WORK/build/qjs/libqjs.a" -lm -lpthread -ldl)

echo "== call benchmark (a C host calling a small device)"
"$CC" -O2 "${INC[@]}" -I"$HERE/call" "$HERE"/call/*.c "${LIBS[@]}" -o "$WORK/build/call_bench"
"$WORK/build/call_bench"

echo "== the ST7735S: record the firmware's event stream, run the project's own class over it for the reference"
(cd "$ROOT" && uv run python "$HERE/record_lcd_trace.py" "$WORK/lcd")
(cd "$ROOT" && uv run python "$HERE/lcd_reference.py" "$WORK/lcd")

echo "== the same device in C, Lua, QuickJS and MicroPython (each result is checked against the reference)"
"$CC" -O2 "${INC[@]}" -I"$HERE/lcd" "$HERE"/lcd/*.c "${LIBS[@]}" -o "$WORK/build/lcd_bench"
read -r FLUSHES FNV <"$WORK/lcd/expected.txt"
"$WORK/build/lcd_bench" "$WORK/lcd/trace_tex.bin" "$FLUSHES" "$FNV" 10

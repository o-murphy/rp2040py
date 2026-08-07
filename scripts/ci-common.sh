#!/bin/sh

check_args() {
    python_runtime="$1"
    micropython_version="$2"

    if [ -z "$python_runtime" ] || [ -z "$micropython_version" ]; then
        echo "Error: Python runtime and micropython version must be specified" >&2
        return 1
    fi
}

run_micropython_dump_test() {
    set -e

    python_runtime="$1"
    micropython_version="$2"

    check_args "$python_runtime" "$micropython_version"
    
    echo "Running a test for Python: $python_runtime, MicroPython: $micropython_version"

    uv run \
        --python "$python_runtime" \
        --no-dev -- rp2040py micropython \
            --image "$micropython_version" \
            --dump-fs dump1.img \
            -c "print('dump1')"

    uv run \
        --python "$python_runtime" \
        --no-dev -- rp2040py micropython \
            --image "$micropython_version" \
            --littlefs dump1.img \
            --dump-fs dump2.img \
            -c "print('dump1')"

    cmp -s dump1.img dump2.img
    echo "Success: FS images are equal"
}
#!/usr/bin/env python3
"""Generate a raw-REPL script that writes files into MicroPython's filesystem via plain
`open()`/`write()` calls, for use with `rp2040py micropython --dump-fs <path> <script>` - an
alternative to the `mklittlefs` subcommand that needs no `littlefs-python` (the optional `fs`
extra) on the host. The emulator's own MicroPython firmware formats and writes the littlefs
filesystem itself, exactly as it would on real hardware; `--dump-fs` then reads the resulting
flash content back out into a `--littlefs`-compatible image.

Usage:
    python demo/mklittlefs_dump.py your_main.py your.py files.py here.py --main your_main.py \\
        --output flash_script.py
    rp2040py micropython --dump-fs littlefs.img flash_script.py

`flash_script.py` here is consumed the same way as any other script passed positionally to
`micropython` (see README.md) - it just runs via the raw-REPL protocol like `your_main.py` itself
would, it's only the *filesystem* being built that's incidental to what it does.

Each file keeps its own basename in the generated filesystem; pass `--main <basename>` to write
one of them as `main.py` (MicroPython's auto-run entry point) instead, matching `mklittlefs`'s own
`--main` semantics. Prints the generated script to stdout by default (`--output <path>` to write
it to a file instead, as above) - this only ever produces *source code*, never an image; the image
itself is produced by the real emulator run that executes it under `--dump-fs`.
"""

import argparse
import sys
from pathlib import Path

_WRITE_FILE_TEMPLATE = """with open({dest!r}, 'wb') as fp:
    fp.write({data!r})
"""


def generate_script(files: "list[Path]", main: "str | None" = None) -> str:
    """Builds the raw-REPL source that writes each of `files` to the device's filesystem under
    its own basename (or `main.py`, for the one matching `main`), then lists `/` as a sanity
    check. Mirrors `rp2040py.cli.mklittlefs.build_littlefs_image`'s own basename/`--main`/
    collision handling, without needing littlefs-python to do it."""
    basenames = [f.name for f in files]
    if main is not None and main not in basenames:
        raise ValueError(f"--main {main!r} must match the basename of one of the given files: {basenames}")

    dest_names: dict[str, Path] = {}
    for path in files:
        dest_name = "main.py" if path.name == main else path.name
        if dest_name in dest_names:
            raise ValueError(f"{path!r} and {dest_names[dest_name]!r} would both be written as {dest_name!r}")
        dest_names[dest_name] = path

    script = ""
    for dest_name, path in dest_names.items():
        script += _WRITE_FILE_TEMPLATE.format(dest=dest_name, data=path.read_bytes())
    script += "\nimport os; print(os.listdir('/'))\n"
    return script


def main(argv: "list[str] | None" = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("files", nargs="*", type=Path, help="source files to add, keeping their own basename")
    parser.add_argument("--main", metavar="<basename>", help="write the `files` entry with this basename as main.py")
    parser.add_argument("-o", "--output", type=Path, help="write the generated script here instead of stdout")
    args = parser.parse_args(argv)

    try:
        script = generate_script(args.files, main=args.main)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.output is not None:
        args.output.write_text(script)
    else:
        sys.stdout.write(script)


if __name__ == "__main__":
    main()

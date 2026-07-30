# Simple script to create a littlefs image for running the MicroPython test

import sys
from os.path import basename

from littlefs import LittleFS

files = sys.argv[2:]
# files = ['tests/micropython/main.py']
output_image = sys.argv[1]

# disk_version=0x00020000 (littlefs disk format v2.0): newer littlefs-python releases default to
# a newer on-disk format (v2.1) that MicroPython <=1.21's bundled littlefs (contemporary with
# format v2.0) fails to mount - and hangs indefinitely retrying rather than raising an error,
# instead of an outright failure. v2.0 stays readable by newer littlefs implementations too (incl.
# MicroPython 1.28's), so pinning the *format* here - not the littlefs-python package version -
# keeps images bootable across the whole MicroPython version range this project tests against.
# See docs/PORTING.md#known-differences-from-rp2040js.
lfs = LittleFS(block_size=4096, block_count=352, prog_size=256, disk_version=0x00020000)
main = True
for filename in files:
    with open(filename) as src_file, lfs.open("main.py" if main else basename(filename), "w") as lfs_file:
        lfs_file.write(src_file.read())
    main = False
with open(output_image, "wb") as fh:
    fh.write(lfs.context.buffer)

print(f"Created littlefs image: {output_image}")

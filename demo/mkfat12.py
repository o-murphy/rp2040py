#!/usr/bin/env python3
"""Builds a CIRCUITPY filesystem image - the FAT12 volume CircuitPython auto-runs `boot.py` and
`code.py` from - for `rp2040py micropython --circuitpython --fat12 <image>` (or
`MicroPythonDevice(fat12=...)`, which is what demo/lcd_run.py's own `--code`/`--boot` use).

    python demo/mkfat12.py --output fat12.img demo/cp_lcd_demo.py=code.py

This is the CircuitPython counterpart of the `mklittlefs` subcommand / demo/mklittlefs_dump.py,
and it exists for the same reason the latter does: it needs nothing installed on the host. There
is no `--circuitpython` equivalent of MicroPython's "let the firmware write its own filesystem
over the raw REPL" trick, because CircuitPython deliberately refuses to write to CIRCUITPY while
USB is attached (`storage.remount()` raises), so the host has to lay the bytes down itself.

**8.3 names only.** Every file is written as one short-name (SFN) root-directory entry, with the
lowercase flags real firmware also uses (`DIR_NTres` bits 0x08/0x10), so `code.py` reads back as
`code.py` and not `CODE.PY`. Long filenames would need VFAT/LFN entry chains, which nothing this
demo needs uses - `boot.py`, `code.py`, `main.py` and `lib/` all fit 8.3. A name that doesn't fit
raises rather than being silently mangled; `settings.toml` is the notable one that doesn't.

Two ways to get the volume geometry right, since `--fat12` images are loaded raw into flash and
CircuitPython only mounts what its own driver recognises:

- **From scratch** (the default): `format()` writes a FAT12 volume of `--volume-bytes` (1 MiB by
  default - see below), padded out to `--image-bytes` with `0xff`, the erased-flash value.
- **On top of a dump** (`--base`): patch files into an image previously read back out of a real
  run (`rp2040py micropython --circuitpython --dump-fs <path>`, or demo/lcd_run.py's own
  `--dump-fs`). That keeps whatever the firmware itself put there - `boot_out.txt`, `lib/`,
  `settings.toml`, the macOS index-suppression entries - instead of a bare volume.

The two defaults are this project's Waveshare RP2040-LCD-0.96 numbers, and both are checked
against what the firmware itself produces rather than guessed:

- `--image-bytes` 2 MiB is `fs_blocksize * fs_blockcount` from the board file's CircuitPython
  `layout` (4096 x 512). `load_circuitpython_flash_image()` requires *exactly* that size
  (`check_flash_image_size()`), so the padding is mandatory, not cosmetic.
- `--volume-bytes` 1 MiB is the CIRCUITPY drive itself: this board has 2 MiB of flash and
  CircuitPython's `CIRCUITPY_CIRCUITPY_DRIVE_START_ADDR` is `0x100000` on it (1020 KiB firmware +
  4 KiB NVM - see the board file), leaving 2048 x 512-byte sectors. Dumping a first boot with no
  `--fat12` at all confirms it: CircuitPython formats exactly `totsec16 = 2048`, and every byte
  past that stays `0xff`. Other boards need their own value (`--volume-bytes`), which is why the
  number is a flag and not a constant.

The BPB below otherwise mirrors what that same dump contains: 512-byte sectors, 1 sector/cluster,
1 reserved sector, **one** FAT, 512 root entries, media `0xf8` - the layout CircuitPython's own
`oofatfs` mkfs picks for this volume size. The one field deliberately not copied is the FAT's own
size, which `format()` solves for (6 sectors here) rather than matching the 7 that mkfs rounds up
to: both describe the same 2009 clusters, and firmware reads the count out of the BPB either way -
verified by booting a from-scratch image and finding CircuitPython's `code.py` running from it.
"""

import argparse
import itertools
import struct
import sys
from pathlib import Path

__all__ = ("Fat12Volume", "build_image")

SECTOR_SIZE = 512
# The Waveshare RP2040-LCD-0.96 defaults, both derived in the module docstring above.
DEFAULT_VOLUME_BYTES = 1024 * 1024
DEFAULT_IMAGE_BYTES = 4096 * 512

_ERASED = 0xFF  # NOR flash reads back as all-ones where nothing was ever written
_ATTR_LFN = 0x0F
_ATTR_ARCHIVE = 0x20
_ATTR_VOLUME_ID = 0x08
_FREE = (0x00, 0xE5)  # first byte of a never-used / deleted directory entry
_EOC = 0xFFF  # FAT12 end-of-chain
_NT_LOWER_BASE = 0x08
_NT_LOWER_EXT = 0x10


class Fat12Volume:
    """A FAT12 volume held in memory, addressed through its own boot sector's BPB.

    Only what this demo needs: read the geometry, walk/allocate cluster chains, and write files
    into the *root* directory. No subdirectories, no LFN, no timestamps (every entry is stamped
    1980-01-01, the epoch FAT itself can represent, rather than a host clock that would make
    otherwise-identical images differ).
    """

    def __init__(self, data: bytearray) -> None:
        self.data = data
        boot = data[:SECTOR_SIZE]
        if struct.unpack("<H", boot[510:512])[0] != 0xAA55:
            raise ValueError("not a FAT volume: boot sector signature is missing")
        self.bytes_per_sector = struct.unpack("<H", boot[11:13])[0]
        self.sectors_per_cluster = boot[13]
        self.reserved_sectors = struct.unpack("<H", boot[14:16])[0]
        self.fat_count = boot[16]
        self.root_entries = struct.unpack("<H", boot[17:19])[0]
        self.sectors_per_fat = struct.unpack("<H", boot[22:24])[0]
        self.total_sectors = struct.unpack("<H", boot[19:21])[0] or struct.unpack("<I", boot[32:36])[0]
        self.fat_offset = self.reserved_sectors * self.bytes_per_sector
        self.root_offset = (self.reserved_sectors + self.fat_count * self.sectors_per_fat) * self.bytes_per_sector
        root_sectors = (self.root_entries * 32 + self.bytes_per_sector - 1) // self.bytes_per_sector
        self.data_offset = self.root_offset + root_sectors * self.bytes_per_sector
        self.cluster_size = self.sectors_per_cluster * self.bytes_per_sector
        data_sectors = self.total_sectors - self.reserved_sectors - self.fat_count * self.sectors_per_fat - root_sectors
        self.cluster_count = data_sectors // self.sectors_per_cluster

    # -- FAT ---------------------------------------------------------------------------------
    # FAT12 packs 1.5 bytes per entry, so entry `n` starts at byte `n + n // 2` and is either the
    # low 12 bits of the little-endian 16-bit word there (even `n`) or the high 12 (odd `n`).

    def fat_get(self, cluster: int) -> int:
        i = self.fat_offset + cluster + cluster // 2
        word = self.data[i] | (self.data[i + 1] << 8)
        return (word >> 4) if cluster & 1 else (word & 0xFFF)

    def fat_set(self, cluster: int, value: int) -> None:
        for fat in range(self.fat_count):  # every copy, so a second FAT (if any) stays in sync
            i = self.fat_offset + fat * self.sectors_per_fat * self.bytes_per_sector + cluster + cluster // 2
            word = self.data[i] | (self.data[i + 1] << 8)
            word = (word & 0x000F) | (value << 4) if cluster & 1 else (word & 0xF000) | value
            self.data[i] = word & 0xFF
            self.data[i + 1] = word >> 8

    def chain(self, first_cluster: int) -> "list[int]":
        # Bounded by the cluster count as well as by the end-of-chain marker: a corrupt image can
        # contain a cycle, and this tool is pointed at images real firmware wrote (`--base`).
        clusters, cluster = [], first_cluster
        while 2 <= cluster < min(0xFF8, self.cluster_count + 2) and len(clusters) <= self.cluster_count:
            clusters.append(cluster)
            cluster = self.fat_get(cluster)
        return clusters

    def _free_chain(self, first_cluster: int) -> None:
        for cluster in self.chain(first_cluster):
            self.fat_set(cluster, 0)

    def _allocate(self, count: int) -> "list[int]":
        clusters = []
        for cluster in range(2, self.cluster_count + 2):
            if self.fat_get(cluster) == 0:
                clusters.append(cluster)
                if len(clusters) == count:
                    break
        if len(clusters) < count:
            raise ValueError(f"volume is full: {count} clusters needed, {len(clusters)} free")
        for current, following in itertools.pairwise(clusters):
            self.fat_set(current, following)
        self.fat_set(clusters[-1], _EOC)
        return clusters

    # -- root directory ----------------------------------------------------------------------

    @staticmethod
    def short_name(name: str) -> "tuple[bytes, int]":
        """`"code.py"` -> `(b"CODE    PY ", 0x18)`: the on-disk 8.3 name plus the `DIR_NTres`
        lowercase flags that make it read back as `code.py`. Raises for anything that needs an
        LFN entry chain to be represented (see the module docstring)."""
        base, _, extension = name.partition(".")
        if not base or len(base) > 8 or len(extension) > 3 or any(c in name for c in ' "*+,/:;<=>?[\\]|'):
            raise ValueError(f"{name!r} is not a FAT 8.3 name - this builder writes no LFN entries")
        flags = (_NT_LOWER_BASE if base.islower() else 0) | (_NT_LOWER_EXT if extension.islower() else 0)
        return (base.upper().ljust(8) + extension.upper().ljust(3)).encode("ascii"), flags

    def _entry_offsets(self) -> "list[int]":
        return [self.root_offset + i * 32 for i in range(self.root_entries)]

    def _find_slot(self, raw_name: bytes) -> int:
        """The offset to write this file's directory entry at: its existing entry if the file is
        already there (whose clusters are freed first, so a re-put doesn't leak them), otherwise
        the first free slot."""
        free_slot = None
        for offset in self._entry_offsets():
            entry = self.data[offset : offset + 32]
            if entry[11] == _ATTR_LFN:  # part of a long-name chain: not a file entry of its own
                continue
            if entry[0] in _FREE:
                free_slot = offset if free_slot is None else free_slot
                if entry[0] == 0x00:  # never used: nothing beyond this point is either
                    break
                continue
            if bytes(entry[:11]) == raw_name:
                self._free_chain(struct.unpack("<H", entry[26:28])[0])
                return offset
        if free_slot is None:
            raise ValueError("root directory is full")
        return free_slot

    def put(self, name: str, content: bytes) -> None:
        """Writes `content` to the volume's root directory as `name`, replacing any existing file
        of that name."""
        raw_name, flags = self.short_name(name)
        offset = self._find_slot(raw_name)
        # An empty file still gets no cluster at all (first_cluster = 0), the same way firmware
        # writes one - allocating one for zero bytes would be a leak, not a rounding choice.
        cluster_count = (len(content) + self.cluster_size - 1) // self.cluster_size
        clusters = self._allocate(cluster_count) if cluster_count else []
        for index, cluster in enumerate(clusters):
            start = self.data_offset + (cluster - 2) * self.cluster_size
            chunk = content[index * self.cluster_size : (index + 1) * self.cluster_size]
            self.data[start : start + self.cluster_size] = chunk.ljust(self.cluster_size, b"\x00")
        entry = bytearray(32)
        entry[0:11] = raw_name
        entry[11] = _ATTR_ARCHIVE
        entry[12] = flags
        entry[24:26] = struct.pack("<H", 0x0021)  # date: 1980-01-01, FAT's own epoch
        entry[26:28] = struct.pack("<H", clusters[0] if clusters else 0)
        entry[28:32] = struct.pack("<I", len(content))
        self.data[offset : offset + 32] = entry

    def read(self, name: str) -> bytes:
        """Reads a root-directory file back out - what makes a `--dump-fs` image inspectable
        (`boot_out.txt` is the interesting one: it is where boot.py's own output lands)."""
        raw_name, _ = self.short_name(name)
        for offset in self._entry_offsets():
            entry = self.data[offset : offset + 32]
            if entry[0] == 0x00:
                break
            if entry[11] == _ATTR_LFN or bytes(entry[:11]) != raw_name:
                continue
            size = struct.unpack("<I", entry[28:32])[0]
            clusters = self.chain(struct.unpack("<H", entry[26:28])[0])
            blob = b"".join(
                bytes(self.data[self.data_offset + (c - 2) * self.cluster_size :][: self.cluster_size])
                for c in clusters
            )
            return blob[:size]
        raise KeyError(name)

    # -- mkfs --------------------------------------------------------------------------------

    @classmethod
    def format(cls, volume_bytes: int = DEFAULT_VOLUME_BYTES, label: str = "CIRCUITPY") -> "Fat12Volume":
        """Lays down a fresh, empty FAT12 volume of `volume_bytes` - boot sector, one FAT, an
        empty root directory holding only the volume label."""
        total_sectors = volume_bytes // SECTOR_SIZE
        if not 1 <= total_sectors <= 0xFFFF:
            raise ValueError(f"{volume_bytes} bytes is not a FAT12-sized volume ({total_sectors} sectors)")
        sectors_per_cluster, reserved, fat_count, root_entries = 1, 1, 1, 512
        root_sectors = root_entries * 32 // SECTOR_SIZE
        # Each FAT sector holds 512 / 1.5 entries, and the FAT has to describe the clusters left
        # once it and the root directory are themselves subtracted - so solve for the smallest FAT
        # that closes that loop, rather than hardcoding a size that only holds for one volume.
        sectors_per_fat = 1
        while True:
            clusters = (total_sectors - reserved - fat_count * sectors_per_fat - root_sectors) // sectors_per_cluster
            needed = ((clusters + 2) * 3 + 1) // 2  # 1.5 bytes per entry, including the two reserved ones
            if needed <= sectors_per_fat * SECTOR_SIZE:
                break
            sectors_per_fat += 1
        if clusters > 4084:  # the FAT12/FAT16 boundary; past it firmware would read this as FAT16
            raise ValueError(f"{volume_bytes} bytes needs {clusters} clusters - too many for FAT12")

        boot = bytearray(b"\x00" * SECTOR_SIZE)
        boot[0:3] = b"\xeb\x3c\x90"  # JMP over the BPB, the customary x86 stub every mkfs writes
        boot[3:11] = b"MSDOS5.0"
        boot[11:13] = struct.pack("<H", SECTOR_SIZE)
        boot[13] = sectors_per_cluster
        boot[14:16] = struct.pack("<H", reserved)
        boot[16] = fat_count
        boot[17:19] = struct.pack("<H", root_entries)
        boot[19:21] = struct.pack("<H", total_sectors)
        boot[21] = 0xF8  # media descriptor: "fixed disk", what every non-floppy volume uses
        boot[22:24] = struct.pack("<H", sectors_per_fat)
        boot[24:26] = struct.pack("<H", 1)  # sectors per track \ CHS geometry: meaningless for
        boot[26:28] = struct.pack("<H", 1)  # heads             / flash, but the fields must exist
        boot[36] = 0x80  # BIOS drive number
        boot[38] = 0x29  # extended boot signature: the three fields below are present
        boot[39:43] = struct.pack("<I", 0x0000_0001)  # volume serial
        boot[43:54] = label.upper().ljust(11).encode("ascii")
        boot[54:62] = b"FAT12   "
        boot[510:512] = struct.pack("<H", 0xAA55)

        data = bytearray(b"\x00" * (total_sectors * SECTOR_SIZE))
        data[:SECTOR_SIZE] = boot
        volume = cls(data)
        # FAT[0] is the media descriptor sign-extended to 12 bits; FAT[1] marks the end of chain.
        volume.fat_set(0, 0xF00 | 0xF8)
        volume.fat_set(1, _EOC)
        label_entry = bytearray(32)
        label_entry[0:11] = label.upper().ljust(11).encode("ascii")
        label_entry[11] = _ATTR_VOLUME_ID
        data[volume.root_offset : volume.root_offset + 32] = label_entry
        return volume


def build_image(
    files: "dict[str, bytes]",
    *,
    base: "Path | None" = None,
    volume_bytes: int = DEFAULT_VOLUME_BYTES,
    image_bytes: int = DEFAULT_IMAGE_BYTES,
) -> bytes:
    """The whole job in one call: a `--fat12`-loadable image of exactly `image_bytes`, holding
    `files` (`{"code.py": b"..."}`) either in a freshly formatted volume or patched into `base`."""
    data = bytearray(base.read_bytes()) if base is not None else Fat12Volume.format(volume_bytes).data
    volume = Fat12Volume(data)
    for name, content in files.items():
        volume.put(name, content)
    if len(data) > image_bytes:
        raise ValueError(f"image is {len(data)} bytes, larger than the {image_bytes}-byte flash region")
    # The flash region past the volume is erased, not zeroed - matching what a `--dump-fs` of an
    # untouched board reads back, so a built image and a dumped one are byte-comparable.
    return bytes(data.ljust(image_bytes, bytes([_ERASED])))


def main(argv: "list[str] | None" = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n", 1)[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="SRC[=NAME]",
        help="host file to add, under its own basename or an explicit 8.3 NAME (e.g. demo/cp_lcd_demo.py=code.py)",
    )
    parser.add_argument("--output", type=Path, help="image to write (required unless --read)")
    parser.add_argument("--base", type=Path, help="patch this existing image instead of formatting a fresh volume")
    parser.add_argument("--volume-bytes", type=int, default=DEFAULT_VOLUME_BYTES, help="CIRCUITPY size (%(default)s)")
    parser.add_argument("--image-bytes", type=int, default=DEFAULT_IMAGE_BYTES, help="flash region size (%(default)s)")
    parser.add_argument("--read", metavar="NAME", action="append", help="print a file from --base and exit")
    args = parser.parse_args(argv)

    if args.read:
        if args.base is None:
            parser.error("--read needs --base: it prints a file out of an existing image")
        volume = Fat12Volume(bytearray(args.base.read_bytes()))
        for name in args.read:
            sys.stdout.write(volume.read(name).decode(errors="replace"))
        return

    if args.output is None:
        parser.error("--output is required when building an image")
    files = {}
    for item in args.files:
        source, _, name = item.partition("=")
        path = Path(source)
        files[name or path.name] = path.read_bytes()
    try:
        image = build_image(files, base=args.base, volume_bytes=args.volume_bytes, image_bytes=args.image_bytes)
    except ValueError as exc:  # an 8.3-impossible name, a full volume, a mis-sized region
        parser.error(str(exc))
    args.output.write_bytes(image)
    print(f"{args.output}: {', '.join(f'{n} ({len(c)} bytes)' for n, c in files.items()) or 'empty volume'}")


if __name__ == "__main__":
    main()

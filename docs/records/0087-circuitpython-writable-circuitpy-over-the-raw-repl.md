# 0087 - CircuitPython over the raw REPL: a writable CIRCUITPY on the stack we already have

- Status: **Proposed / documented, nothing implemented.** Everything below is measured; what to
  *build* from it is listed at the end and deliberately left unbuilt.
- Corrects, and is the follow-through from,
  [0085](0085-circuitpython-code-py-and-wifi-on-screen.md)'s own appended correction; changes what
  [0086](0086-fat12-library-and-a-mkfat12-subcommand.md) is deciding.

## The finding

**CircuitPython's CIRCUITPY drive is writable from the guest in this emulator, over the raw REPL
we already implement, with no changes at all.** No "eject", no detach, no MSC emulation. The
firmware can therefore build its own filesystem the same way MicroPython's does - which is the
trick `demo/mklittlefs_dump.py` is built on, and which 0085 wrongly recorded as having no
CircuitPython equivalent.

There is also no separate "CircuitPython raw REPL" to implement: `device/raw_repl.py` is one
family-agnostic `RawReplRunner`, and its own constant says so -
`_RAW_REPL_BANNER = b"raw REPL; CTRL-B to exit\r\n>"`, commented as "the exact banner
MicroPython/**CircuitPython** print in response to Ctrl-A". Every CircuitPython script this
project runs (`-c`, a positional script, `tests/circuitpython/main-cyw43.py`, the whole
`ci-circuitpython.yml` WLAN job) already goes through it. `usb/cdc.py` contains no mention of
either firmware family.

The only CircuitPython-specific lines in the whole console path are three, and none is protocol:

| what | where | why |
|---|---|---|
| send `Ctrl-C` after boot instead of `\r\n` | `cli/__init__.py`'s `_micropython_async()` | CircuitPython runs `code.py` at boot and prints "Press any key to enter the REPL"; MicroPython is already at a prompt |
| FAT12 loader instead of littlefs | `device/mp_device.py.__init__` | different filesystems |
| `dump_circuitpython_flash_image()` | `device/mp_device.dump_flash_image()` | same |

## Why it works here (and not on a real board)

CircuitPython does not gate writes on "is USB attached". It gates them on a lock:

- `shared-module/storage/__init__.c`'s `common_hal_storage_remount()` raises only
  `if (!blockdev_lock(fs_usermount))` - "Cannot remount path when visible via USB."
- The lock is taken in `supervisor/shared/usb/usb_msc_flash.c`'s `tud_msc_is_writable_cb()`:
  `// Lock the blockdev once we say we're writable` / `if (!locked[lun] && !blockdev_lock(vfs))`.
  That is a **TinyUSB callback** - it fires when a USB host actually issues mass-storage traffic,
  not because an MSC interface exists in the descriptors.
- rp2040py never issues any. `usb/cdc.py`'s `extract_endpoint_numbers()` walks the firmware's own
  configuration descriptor for the single interface with `interface_class == CDC_DATA_CLASS` (10)
  and two endpoints, takes those two, and ignores every other interface. The firmware *does*
  expose an MSC interface (class 8); nothing here ever claims it or sends it a CBW.

So the emulator is permanently in the state a real board reaches only after the host ejects the
drive. That is the whole explanation for "we have a virtual USB stack, so why is MSC not
enumerated": the stack is a CDC consumer, not a general-purpose USB host.

## What was measured (2026-08-19, CircuitPython 10.2.1, `boards/waveshare_rp2040_lcd_0_96`)

Writing, through the project's own `-c`:

    storage.remount('/', readonly=False)   ->  REMOUNT: ok
    open('/probe.txt', 'w')                ->  WRITE: ok -> written from the raw REPL
    os.listdir('/')                        ->  [..., 'boot_out.txt', 'probe.txt']

A second run wrote `code.py`, `settings.toml` and `lib/greeter.py` from the REPL and dumped the
drive with `--dump-fs`. All three are in the image, written by the firmware's own FatFS: a real
LFN chain for `settings.toml`, and a `LIB` directory entry with `DIR_NTres = 0x08` (so it reads
back as `lib`) holding `GREETER PY`. **Long names and subdirectories therefore come for free on
this route** - no host-side FAT12 library involved at all.

Through real `mpremote`, over `--tcp-port`'s socket REPL
(`rp2040py mpremote connect socket://127.0.0.1:8765 ...`):

| command | result |
|---|---|
| `exec "import storage; storage.remount('/', readonly=False); print('rw ok')"` | **works** - `rw ok` |
| `run <file.py>` | **works** - the script's output comes back |
| `exec "import os; print(sorted(os.listdir('/')))"` | **works** |
| `fs ls` | fails: `AttributeError: 'module' object has no attribute 'ilistdir'` |
| `fs cp <local> :name` | fails: `OSError: [Errno 2] No such file/directory`, and nothing is written |

The `fs` failures are **not** transport problems and not fixable on our side: mpremote's file
commands are written against MicroPython's `os.ilistdir()` and its `stat`/open semantics, which
CircuitPython's `os` does not provide. Everything that goes through the raw REPL as plain code
works.

## What to build from this (none of it done)

1. **`demo/mkfat12_dump.py`** - the counterpart of `demo/mklittlefs_dump.py` that 0085 said could
   not exist: generate a raw-REPL script that remounts read-write and writes each host file with
   plain `open()`/`write()` (creating directories as needed), run it under `--dump-fs`, and the
   result is a CIRCUITPY image **the firmware itself wrote**. Zero host dependencies, correct
   geometry by construction, LFN and subdirectories included.
2. **A "push over the REPL" mode for `--code`/`--boot`** in `demo/lcd_run.py`, as an alternative
   to building an image up front - closer to how someone actually iterates on a board.
3. **Document the mpremote story** (in `docs/mpremote.md`, which already exists for the
   `socket://` bug): `exec`/`run`/`eval`/`repl` work against CircuitPython; `fs *` does not, and
   why. Worth stating explicitly so nobody re-derives it.
4. **Optional, cosmetic**: move the post-boot handshake difference out of
   `if not args.circuitpython` in the CLI and onto the device/family, so the CLI stops branching on
   a firmware flag it otherwise doesn't care about.

## Constraints whoever builds this has to keep

- **This and USB MSC are mutually exclusive by construction.** Enumerating mass storage - the
  fidelity option, since a real board *is* read-only to the guest while a host holds the drive -
  takes `blockdev_lock` and makes `storage.remount()` raise exactly as it does on hardware. Any
  MSC implementation must therefore be opt-in, and this route documented as depending on its
  absence.
- **`storage.remount()` has to happen first, in the same live session.** The writable flag is RAM
  state (`filesystem_set_writable_by_usb`), so it does not survive a reset, and every tool driven
  through the REPL (including mpremote) needs it done once up front.
- **It costs emulated time.** Writing through the REPL means minutes of emulation where
  `demo/mkfat12.py` builds the same image offline in milliseconds. That is why the 8.3 builder
  keeps a job no matter what happens here - a test or a CI fixture cannot pay boot time - and it is
  exactly the trade [0086](0086-fat12-library-and-a-mkfat12-subcommand.md) now hinges on.
- **Unverified, and worth checking before relying on it**: whether writing `code.py` from the REPL
  trips CircuitPython's auto-reload (and so re-runs it immediately), and whether the write cache
  needs an explicit flush before `--dump-fs` on every path - the two runs measured above both came
  back complete, but both happened to read files back afterwards, which is itself what calls
  `port_internal_flash_flush()`.

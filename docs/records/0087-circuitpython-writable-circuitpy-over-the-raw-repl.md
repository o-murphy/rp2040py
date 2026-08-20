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

## Sequencing (agreed 2026-08-19)

The order these four records get worked in, decided with the maintainer rather than derived here:

1. **This record (0087)** - unify the REPL story for MicroPython and CircuitPython, since it is
   what makes the filesystem writable at all and therefore constrains everything below.
2. **[0086](0086-fat12-library-and-a-mkfat12-subcommand.md)** - pick the stack (a FAT12 library, or
   none) now that the firmware-writes-its-own-filesystem route is on the table.
3. **[0085](0085-circuitpython-code-py-and-wifi-on-screen.md)** - rework the demo half on top of
   whatever 0087 and 0086 settle on, rather than on the premise it was written under.
4. **[0088](0088-usb-host-side-msc-control-lines-and-reset.md)** - the USB host side last, since
   mass storage is mutually exclusive with 1 and nothing above depends on it.

## Update (2026-08-20): 0086 rejected, and what that does to the sequencing above

Step 2 of the sequencing resolved, by maintainer decision rather than by analysis here:
[0086](0086-fat12-library-and-a-mkfat12-subcommand.md) is **rejected in full** - no FAT12 library
dependency, no `mkfat12` CLI subcommand. This record's finding is why: with the firmware writing
its own volume, LFN chains and subdirectories are correct by construction, which was the only
thing a host-side library was ever needed for.

Two things in the text above should be read in that light:

- "the trade [0086] now hinges on" (end of Constraints) is settled the other way round from how
  that sentence expects: the cost argument did not pick a library, it split the two routes by job.
  `demo/mkfat12.py`'s **8.3 builder stays** - `demo/lcd_run.py` measures a format-from-blank boot
  past 30s, `--read` has no counterpart here, and `tests/test_demo_mkfat12.py` is 170 lines of
  offline assertions - while everything needing long names or subdirectories goes through this
  record's route.
- The sequencing list therefore reads: 1. this record (still unbuilt), 2. ~~0086~~ rejected,
  3. [0085](0085-circuitpython-code-py-and-wifi-on-screen.md), 4.
  [0088](0088-usb-host-side-msc-control-lines-and-reset.md).

Item 1 of "What to build from this" (`demo/mkfat12_dump.py`) is now the *only* planned route to a
CIRCUITPY image with long names or subdirectories, which promotes this record's two unverified
questions - auto-reload on writing `code.py`, and whether the write cache needs an explicit flush
before `--dump-fs` - from "worth checking" to blocking on anything built from it.

## The shape item 2 should take (2026-08-20): context manager + `aexec()` + a host-side reset

Proposed by the maintainer, documented here rather than built. It replaces the "two separate
`rp2040py` runs with an image passed between them" reading of item 2 above, and is strictly
cheaper.

### Why a reset removes the round trip entirely

`BaseDevice._on_watchdog_trigger()` (`device/base_device.py`) already performs a live reset as
`mcu.reset(preserve_flash=True)` / `core.pc = FLASH_START_ADDRESS` / `cdc.reset()`. The first
argument is the point: **emulated flash survives**, so the CIRCUITPY the firmware just wrote over
the REPL is still there after the restart. `tests/test_watchdog_reset.py` already asserts both
halves that matter (`..._preserves_flash_content`, `..._resets_usb_cdc_enumeration_state`).

So there is no need to `--dump-fs` an image and boot a second process with `--fat12`. One process:

    async with MicroPythonDevice(board=board, circuitpython=True) as device:
        await device.aexec(write_script)   # storage.remount(rw) + open()/write() per file
        await device.areset()              # <- the piece that does not exist yet
        ...                                # CircuitPython auto-runs code.py; collect frames

`--dump-fs` becomes optional - for *keeping* the image, not for making the flow work.

### Almost all of that already exists

`MicroPythonDevice` has `__aenter__`/`__aexit__` and `aexec()`/`exec_async()`/`aexec_file()`.
The one missing piece is a public reset. Four things it has to get right, all visible in the
current code:

- It cannot be a second `astart()`: `start_async()` raises on `self._started`.
- It must take `self._repl_lock`, the same lock `_aconnect()` and `_aexec()` use, so a reset
  cannot interleave with an exec in flight.
- Its body is `_on_watchdog_trigger()`'s three lines plus the *waiting* half of
  `MicroPythonDevice._aconnect()` (fresh `on_device_connected` event, `simulator.wait_for`) -
  **without** `simulator.start_execution()`, which is already running.
- `cdc.reset()` invalidates the host's console state, so whatever `RawReplRunner` needs to be
  re-primed has to happen after re-enumeration, not before.

### This promotes item 4 from cosmetic to prerequisite

Item 4 above ("move the post-boot handshake off the CLI onto the device/family") is listed as
optional. It stops being optional here. The CircuitPython-vs-MicroPython handshake lives in
`cli/__init__.py`'s `_micropython_async()` as a bare `if not args.circuitpython:` around
`cdc.send_serial_byte(...)` - **after** `await device.astart()`. A reset needs that same handshake
performed again, and a `device.areset()` cannot reach into the CLI to get it. Either the handshake
moves onto the device/family first, or `areset()` silently returns a device the caller cannot talk
to.

### Relation to [0057](0057-run-pin-reset-hook.md)

0057's stated blocker is that nothing can perform the third step (`cdc.reset()` and the host-side
re-enumeration that must follow it), so a RESET button doing only the first two would leave a
stale REPL - "strictly worse than the honest 'not modelled'". A public device-level reset is
exactly that missing step. It does **not** resolve 0057 on its own: that record's other half is
that an `ExternalDevice` gets `attach(rp2040)` and cannot reach the `BaseDevice` at all. Worth
designing the two together rather than adding a reset that 0057 then has to work around.

### Still unverified, and now less load-bearing

Doing the reset from the host removes the dependency on *how a guest triggers one* - there is no
local CircuitPython checkout here to confirm that `microcontroller.reset()` reaches the watchdog
TRIGGER bit the way MicroPython's `machine.reset()` does, and with a host-side API nothing has to.
The auto-reload question from "Constraints" stays open and is worth answering first anyway: if
writing `code.py` from the REPL reloads on its own (or on leaving the raw REPL), the explicit
reset may be redundant for the `--code` case, though not for the general one.


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

## The shape item 2 should take (2026-08-20): context manager + `aexec()` + a **soft** reset

Proposed by the maintainer, documented here rather than built. It replaces the "two separate
`rp2040py` runs with an image passed between them" reading of item 2 above.

### The restart is already supported, and it is a soft reset

Not a chip reset and not an emulator feature - the firmware's own. `docs/reference/mpremote.md`
lists `mpremote connect ... soft-reset` / **Ctrl-D at the raw-REPL prompt** as working, with the
note "handled entirely by firmware's own soft-reset code - **no emulator-side reset needed**", and
`tests/test_mpremote_integration.py`'s fake device models exactly that byte
(`enter_raw_repl(soft_reset=True)`, mpremote's default): a bare Ctrl-D at the prompt, before any
raw-paste probe, answered with `soft reboot\r\n` and a fresh banner.

That is the whole restart this route needs. A soft reset does not touch flash at all - so the
CIRCUITPY the firmware just wrote over the REPL is trivially still there - and it does not
re-enumerate USB, so the console connection stays up across it.

    async with MicroPythonDevice(board=board, circuitpython=True) as device:
        await device.aexec(write_script)   # storage.remount(rw) + open()/write() per file
        await device.asoft_reset()         # <- bare Ctrl-D at the prompt; see below
        ...                                # firmware re-runs code.py; collect frames

`--dump-fs` is then optional - for *keeping* the image, not for making the flow work.

### What is actually missing (much less than a reset API)

`MicroPythonDevice` already has `__aenter__`/`__aexit__` and `aexec()`/`exec_async()`/
`aexec_file()`. What it has no way to express is **a bare Ctrl-D at the prompt**:
`RawReplRunner._on_start()` sends Ctrl-C, Ctrl-C, Ctrl-A, and its `CTRL_D` is only ever the
*terminator* appended to source (`self._queue(bytes(self._source) + bytes([CTRL_D]))`) or the
end-of-output marker in the reply. The soft-reset byte is reachable today only through the
mpremote path, not from the device API.

So the work is a small runner (or a flag on the existing one) that enters raw REPL, sends Ctrl-D,
and waits for `soft reboot` + the banner - not a chip reset, not re-enumeration, and **not** item 4:
because USB never drops, the post-boot handshake difference the CLI carries is irrelevant to this
path. Item 4 stays what it says it is - optional and cosmetic.

### Unverified, and the reason the hard reset is not dead

No local CircuitPython checkout here, so per the 3g rule this has to be read from real source
before anything is built on it: **whether CircuitPython's soft reboot re-runs `code.py`** the way
MicroPython's re-runs `main.py`. If it does, the flow above is complete. If it does not, the
fallback is the hard reset - `BaseDevice._on_watchdog_trigger()`
(`mcu.reset(preserve_flash=True)` / `core.pc = FLASH_START_ADDRESS` / `cdc.reset()`, with
`tests/test_watchdog_reset.py` covering flash preservation and CDC state) - and *that* path does
need a public entry point, a re-enumeration wait, and item 4 done first, since the console drops.

Related: the auto-reload question in "Constraints" may make even the soft reset unnecessary for the
`--code` case, if writing `code.py` reloads on its own or on leaving the raw REPL. Worth answering
in the same pass, from the same source.

### One placement note, for [0057](0057-run-pin-reset-hook.md)

Independent of which restart this route uses. The full hard reset exists exactly once in the tree,
is private, and hangs off `BaseDevice` - not off `RP2040`. `RP2040.reset(preserve_flash=)` resets
registers and core only; the USB half lives in `USBCDC.reset()`/`RPUSBController.reset_device()`;
`RPReset`/`RPPSM`/`RPVregAndChipReset` are guest-visible register blocks, not a host-side action.
0057's blocker is exactly that placement: an `ExternalDevice` gets `attach(rp2040)` and cannot
reach a `BaseDevice`, and the sequence cannot simply move onto `RP2040` because `USBCDC` is
constructed by `BaseDevice` and the reference only points one way (`USBCDC.usb` -> `usb_ctrl`).
Worth designing together if the hard-reset fallback is ever taken.


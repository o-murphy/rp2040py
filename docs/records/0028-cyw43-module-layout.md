# 0028. CYW43439 module layout decision

- Status: Accepted
- Conceived: 2026-08-12
- Related: 0027 (epic)

<!-- migrated verbatim from docs/CYW43_WIFI_BACKLOG.md lines 477-510 -->

## Module layout decision (2026-08-11)

Resolves "where in `src/rp2040py/` does this belong."

**Not in `peripherals/`.** Every file there (`spi.py`, `dma.py`, `pio.py`, ...) inherits
`BasePeripheral` and implements `read_uint32`/`write_uint32` for a specific memory-mapped address
(see `peripherals/peripheral.py`). The CYW43439, from the CPU's point of view, is not a
memory-mapped peripheral at all — it's driven exclusively through `gpio[23/24/25/29]` listeners.
There's a precedent for GPIO-listener-driven behavior even inside `peripherals/`:
`ssi.py:116` also hooks `rp2040.qspi[1].add_listener(...)` as a helper. But there it's a secondary
mechanism layered on top of a real register-based interface (`SSI`'s normal `read_uint32`/
`write_uint32`); for CYW43439 the GPIO-listener bus decode is the **only** mechanism — there is no
backing register block to fall back on.

**New subpackage `src/rp2040py/external/cyw43/`** (relocated 2026-08-12 from a first-draft
top-level `src/rp2040py/cyw43/` - `Cyw43439` is a concrete `ExternalDevice` implementation, same
reasoning `external/led_mock.py` already follows, just big enough - bus/chip/nat - to want its own
subpackage under `external/` instead of a single sibling file), following the same
"real package with an `__init__.py`, not a single file" pattern as `clock/`, `gdb/`, `usb/`,
`utils/`:

- `external/cyw43/bus.py` — bit-bang gSPI decode (step 2 in "Implementation order" below, GPIO-listener
  level: `make_cmd()` header parsing, F0 bus register block).
- `external/cyw43/chip.py` — the chip model itself: F0/F1 registers, backplane windowed addressing,
  `WLC_*`/`WLC_E_*` ioctl and event handling (step 3). This is also where the `Cyw43439` class
  that implements `ExternalDevice` (see "Board composition decision" next) lives.
- `external/cyw43/nat.py` — the SLIRP-style userspace NAT bridge (step 4).

**Wiring:** *not* baked into `RP2040.__init__()` — see "Board composition decision" next.
`RP2040` itself stays unchanged; a board-setup step calls
`attach_external_devices(rp2040, Cyw43439())` (or `Cyw43439().attach(rp2040)` directly) after
construction, once `self.gpio` already exists (`_rp2040.py:120`), so `Cyw43439.attach()` can hook
listeners onto `gpio[23]`/`gpio[24]`/`gpio[25]`/`gpio[29]`.


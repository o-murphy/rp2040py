# 0042. `GSPIBus` `SPI_INTERRUPT_REGISTER` write-1-to-clear (W1C) fix

- Status: Implemented — verified (2026-08-13)
- Conceived: 2026-08-13 · Implemented: 2026-08-13
- Related: 0027 (CYW43439/Pico W epic, where this was found - a live-boot verification pass over
  step 3g noticed the warning as unexplained "chatter"), 0038 (same class of bug - a real-firmware
  behavior real firmware itself printed, root-caused by tracing against a symbol-matched build
  rather than assumed benign)

## Context

Booting real MicroPython `v1.28.0` firmware against this emulator
(`uv run rp2040py --log-level error micropython --board pico_w --image v1.28.0
tests/micropython/main-cyw43.py`) prints one line, right after `Initializing...`, that comes from
the *real firmware itself* (`bus.py` has zero `logger.*()`/`print()` calls by design):

```
[CYW43] Bus error condition detected 0xb9
```

Not fatal - `nic.active()` still ends up `True`, `scan()`/`connect()` still complete (0027's step
3g). But it is real firmware genuinely believing something went wrong on the bus, worth
root-causing rather than dismissing. 0038's own verification table already shows this same line
appearing in a `v1.28.0` run that day, unremarked - this record is the first time it was actually
investigated.

## Root cause (confirmed by live instrumentation, not guessed)

Traced to `lib/cyw43-driver/src/cyw43_ll.c:1021` (checked against a real, symbol-matched
`v1.28.0`/`RPI_PICO_W` checkout, `lib/cyw43-driver` at v1.1.1), inside the SPI variant of
`cyw43_ll_sdpcm_poll_device()`:

```c
uint16_t spi_int = cyw43_read_reg_u16(self, BUS_FUNCTION, SPI_INTERRUPT_REGISTER);
if (last_spi_int != spi_int) {
    if (spi_int & BUS_OVERFLOW_UNDERFLOW) {
        CYW43_WARN("Bus error condition detected 0x%x\n", spi_int);
        ...
```

Temporary `print(..., file=sys.stderr)` instrumentation added to `GSPIBus._read_f0()`/`_write_f0()`
(this file's own established throwaway-debug-print pattern, per 0038/0041 - reverted before
landing) and a live boot captured the exact byte-level sequence. Two writes land at F0 byte offset
4 (`SPI_INTERRUPT_REGISTER`) right after the `SPI_BUS_CONTROL` word-length/endian switch:

```
DEBUG _write_f0 addr=0x4 size=1 value=0x99
...
DEBUG _write_f0 addr=0x4 size=2 value=0xb9
DEBUG _read_f0 addr=0x4 size=2 -> 0xb9 f0[0:8]=...
```

The first write is real, expected firmware behavior, confirmed against `cyw43_ll_bus_init()`
(`cyw43_ll.c:1471-1474`):

```c
// Make sure error interrupt bits are clear
if (cyw43_write_reg_u8(self, BUS_FUNCTION, SPI_INTERRUPT_REGISTER,
    DATA_UNAVAILABLE | COMMAND_ERROR | DATA_ERROR | F1_OVERFLOW) != 0) {
```

`DATA_UNAVAILABLE(0x01) | COMMAND_ERROR(0x08) | DATA_ERROR(0x10) | F1_OVERFLOW(0x80) = 0x99` -
exactly the observed write. The comment says this write means to *clear* those bits (none of them
were actually set - the register starts at 0 on both real hardware and this emulator). `cyw43_spi.h`
confirms this register is a genuine write-1-to-clear (W1C) status register, not plain storage - its
own per-bit comments say so directly (`DATA_UNAVAILABLE`: "Clear by writing a 1"; `COMMAND_ERROR`/
`DATA_ERROR`: "Cleared by writing 1"), and `cyw43_ll_sdpcm_poll_device()`'s own later ack pattern
(`if (spi_int) { cyw43_write_reg_u16(self, BUS_FUNCTION, SPI_INTERRUPT_REGISTER, spi_int); }` -
echoing back exactly whichever bits it just read, specifically to clear them) confirms it applies to
the *whole* register, not just these four bits - the driver would never intentionally set its own
already-observed status bits back onto the chip.

`GSPIBus._write_f0()` had no special case for this register - every F0 register (`SPI_BUS_CONTROL`,
`SPI_STATUS_ENABLE`, `SPI_INTERRUPT_ENABLE_REGISTER`, etc.) is genuinely plain read-write storage,
and `SPI_INTERRUPT_REGISTER` was treated the same way. So the "clear my error bits" write stored
`0x99` **verbatim**, genuinely *setting* `F1_OVERFLOW` (and the other three bits) instead of
clearing them. The very next real bus activity - `_activate_rx_packet()` (step 3e), OR-ing in
`F2_PACKET_AVAILABLE` (`0x20`) for the first real ioctl response - produced `0x99 | 0x20 = 0xb9`,
exactly the observed value. `cyw43_ll_sdpcm_poll_device()`'s own `last_spi_int` starting at 0 (a
`static` local, zero-initialized) meant this was always going to trip on the very first nonzero
read; the specific bit that tripped it (`F1_OVERFLOW`, part of `BUS_OVERFLOW_UNDERFLOW = F1_OVERFLOW
| F2_F3_FIFO_RD_UNDERFLOW | F2_F3_FIFO_WR_OVERFLOW`) traces directly back to this one wrong write,
not a plausible real-hardware transient - this is a real, fixable emulation bug, not benign chatter.

## Fix

`GSPIBus.write_register()` (`src/rp2040py/external/cyw43/bus.py`) now special-cases
`addr == SPI_INTERRUPT_REGISTER` on the `BUS_FUNCTION` path: before delegating to `_write_f0()`, the
value to store is computed as `self._read_f0(addr, size) & ~value` - AND-clearing exactly the bits
the host wrote, leaving every other bit (including ones outside the written `size` window, e.g. a
1-byte write only ever touching the register's low byte) untouched, matching real W1C hardware.
This only rewrites the **host's own writes**, routed through `write_register()` (the real wire-write
entry point, called from `_on_clock_rising()`'s wire decode) - `_activate_rx_packet()`/
`_read_wlan()` still call `_write_f0()` directly to set/clear `F2_PACKET_AVAILABLE` themselves,
representing the chip's own internal status changes rather than a host command, so those correctly
stay plain sets/clears, not W1C. `_write_f0()` itself is unchanged - every other F0 register keeps
its existing plain-storage semantics.

Two regression tests added to `tests/test_cyw43_bus.py`:
- `test_interrupt_register_host_write_clears_the_written_bits_not_sets_them` - a host write of
  `F2_PACKET_AVAILABLE` while that bit is genuinely set (via `queue_rx_packet()`) clears it, proving
  W1C in the general case.
- `test_interrupt_register_host_write_of_error_bits_does_not_set_them` - a direct repro of the real
  bug: writing `0x99` (the exact `cyw43_ll_bus_init()` value) to a register that starts at 0 leaves
  it at 0, not `0x99`.

All 44 tests in `tests/test_cyw43_bus.py` pass; full `uv run pre-commit run --all-files`
(mypy/ruff/pytest, both pure-Python and native-Cython builds) passes clean.

## Verification

Re-ran the exact live-boot command from the bug report. The warning is completely gone; the rest of
the script's output (`active: True`, `scan()` returning the fake AP, `connect()` output) is
unchanged from before this fix - no regression.

```
Initializing...
active: True
Scan for networks
[(b'RP2040PY-GUEST', b'B\x137U\xaa\x01', 6, -87, 0, 1)]
Connected False
None
b'\x00\x00\x00\x00\x00\x00'
('0.0.0.0', '255.255.255.0')
```

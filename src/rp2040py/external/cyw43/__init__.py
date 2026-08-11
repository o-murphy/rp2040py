"""CYW43439 (Pico W WiFi chip) emulation - see docs/CYW43_WIFI_BACKLOG.md for the full plan.
Lives under `external/` (not a top-level package) since `Cyw43439` is a concrete `ExternalDevice`
implementation (`external/device.py`'s Protocol) - the same reasoning `external/led_mock.py`
follows, just big enough (bus/chip/nat) to warrant its own subpackage instead of a single file.

- `external/cyw43/bus.py` - the gSPI bit-bang bus decode (step 2): word framing, command-header
  parsing, and the F0 (`BUS_FUNCTION`) register block real firmware polls during its init
  handshake.
- `external/cyw43/chip.py` - the chip/backplane + SDPCM/`WLC_*` ioctl model (step 3, not built yet).
- `external/cyw43/nat.py` - the SLIRP-style userspace NAT bridge (step 4, not built yet).
"""

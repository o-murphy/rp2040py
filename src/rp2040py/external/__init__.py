"""External devices: board-level hardware that isn't a memory-mapped peripheral - an onboard LED,
a button, a WiFi chip, a display panel - wired to the emulated RP2040 through GPIO pins and
peripheral hooks rather than through the memory bus (`peripherals/` is that other half).

The extension point itself lives here: `ExternalDevice` (a `Protocol` with one method,
`attach(rp2040)`) and `attach_external_devices(rp2040, *devices)`, both re-exported from
`external/device.py` so callers can spell it `from rp2040py.external import ExternalDevice`
instead of reaching into the submodule. Worth the two lines because of the name collision it
defuses: `rp2040py.device` is the *host-side* API (`MicroPythonDevice`, `KalumaDevice` - things
you boot and talk to over a REPL), while `rp2040py.external` is what's attached *inside* the
emulated board. Same word, opposite sides of the USB cable; the deep `rp2040py.external.device`
path put them one identifier apart at import sites, which is exactly where the confusion was.

The submodule keeps its name: `peripherals/peripheral.py` holds `Peripheral` by the same rule,
and `external/external_device.py` would only stutter. It is an implementation detail now.

Concrete devices are *not* re-exported here - import them from their own modules
(`rp2040py.external.led_mock`, `.bootsel_button`, `.key_mock`, `.epd2in9g`, `.st7735s`,
`.cyw43`). Pulling them all in would make importing the protocol cost every device in the tree,
including `cyw43`'s whole bus/chip/NAT stack.

A worked-example write-up of writing your own device or board lives in
docs/reference/external-devices-and-boards.md.
"""

from rp2040py.external.device import ExternalDevice, attach_external_devices

__all__ = ("ExternalDevice", "attach_external_devices")

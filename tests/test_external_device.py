"""Unit tests for `rp2040py.external.device`'s `ExternalDevice` Protocol + `attach_external_devices()`
- the CYW43_WIFI_BACKLOG.md step 0 plumbing that lets board-level hardware wire itself onto an
`RP2040` without that hardware being a memory-mapped peripheral."""

from rp2040py.external import attach_external_devices


class _RecordingDevice:
    def __init__(self):
        self.attached_to = []

    def attach(self, rp2040):
        self.attached_to.append(rp2040)


def test_attach_external_devices_calls_attach_on_each_device_with_the_mcu(rp2040_factory):
    mcu = rp2040_factory()
    a, b = _RecordingDevice(), _RecordingDevice()

    attach_external_devices(mcu, a, b)

    assert a.attached_to == [mcu]
    assert b.attached_to == [mcu]


def test_attach_external_devices_with_no_devices_is_a_no_op(rp2040_factory):
    mcu = rp2040_factory()
    attach_external_devices(mcu)  # must not raise


def test_attach_external_devices_preserves_call_order(rp2040_factory):
    mcu = rp2040_factory()
    order = []

    class _Ordered:
        def __init__(self, label):
            self.label = label

        def attach(self, rp2040):
            order.append(self.label)

    attach_external_devices(mcu, _Ordered("first"), _Ordered("second"), _Ordered("third"))

    assert order == ["first", "second", "third"]

"""Ported from rp2040js's usb/cdc.spec.ts."""

from rp2040py.usb.cdc import (
    CDC_DTR,
    CDC_REQUEST_SET_CONTROL_LINE_STATE,
    CDC_REQUEST_SET_LINE_CODING,
    CDC_RTS,
    LINE_CODING_SIZE,
    USBCDC,
    EndpointNumbers,
    LineCoding,
    extract_control_interface_number,
    extract_endpoint_numbers,
)

_SDK_CDC_DESCRIPTORS = [
    *[9, 2, 84, 0, 3, 1, 0, 128, 125],
    *[8, 11, 0, 2, 2, 2, 0, 0],
    *[9, 4, 0, 0, 1, 2, 2, 0, 4],
    *[5, 36, 0, 32, 1],
    *[5, 36, 1, 0, 1],
    *[4, 36, 2, 2],
    *[5, 36, 6, 0, 1],
    *[7, 5, 129, 3, 8, 0, 16],
    *[9, 4, 1, 0, 2, 10, 0, 0, 0],
    *[7, 5, 2, 2, 64, 0, 0],
    *[7, 5, 130, 2, 64, 0, 0],
    *[9, 4, 2, 0, 0, 255, 0, 1, 5],
]

_MICROPYTHON_DESCRIPTORS = [
    *[9, 2, 75, 0, 2, 1, 0, 128, 125],  # Configuration descriptor
    *[8, 11, 0, 2, 2, 2, 0, 0],
    *[9, 4, 0, 0, 1, 2, 2, 0, 4],  # Interface descriptor
    *[5, 36, 0, 32, 1],
    *[5, 36, 1, 0, 1],
    *[4, 36, 2, 2],
    *[5, 36, 6, 0, 1],
    *[7, 5, 129, 3, 8, 0, 16],  # Endpoint (interrupt)
    *[9, 4, 1, 0, 2, 10, 0, 0, 0],  # interface (CDC-Data class)
    *[7, 5, 2, 2, 64, 0, 0],  # Endpoint (bulk)
    *[7, 5, 130, 2, 64, 0, 0],  # Endpoint (bulk)
]

_CIRCUITPYTHON_DESCRIPTORS = [
    *[9, 2, 218, 0, 6, 1, 0, 128, 50],
    *[8, 11, 0, 2, 2, 2, 0, 0],
    *[9, 4, 0, 0, 1, 2, 2, 0, 4],
    *[5, 36, 0, 16, 1],
    *[5, 36, 1, 1, 1],
    *[4, 36, 2, 2],
    *[5, 36, 6, 0, 1],
    *[7, 5, 129, 3, 64, 0, 16],
    *[9, 4, 1, 0, 2, 10, 0, 0, 5],
    *[7, 5, 2, 2, 64, 0, 0],
    *[7, 5, 130, 2, 64, 0, 0],
    *[9, 4, 2, 0, 2, 8, 6, 80, 6],
    *[7, 5, 131, 2, 64, 0, 0],
    *[7, 5, 3, 2, 64, 0, 0],
    *[9, 4, 3, 0, 2, 3, 0, 0, 7],
    *[9, 33, 17, 1, 0, 1, 34, 195, 0],
    *[7, 5, 132, 3, 64, 0, 8],
    *[7, 5, 4, 3, 64, 0, 8],
    *[9, 4, 4, 0, 0, 1, 1, 0, 11],
    *[9, 36, 1, 0, 1, 9, 0, 1, 5],
    *[9, 4, 5, 0, 2, 1, 3, 0, 10],
    *[7, 36, 1, 0, 1, 37, 0],
    *[6, 36, 2, 1, 1, 8],
    *[6, 36, 2, 2, 2, 0],
    *[9, 36, 3, 1, 3, 1, 2, 1, 9],
    *[9, 36, 3, 2, 4, 1, 1, 1, 0],
    *[7, 5, 5, 2, 64, 0, 0],
    *[5, 37, 1, 1, 1],
    *[7, 5, 133, 2, 64, 0, 0],
    *[5, 37, 1, 1, 3],
]

_ARDUINO_CORE_DESCRIPTORS = [
    *[9, 2, 75, 0, 2, 1, 0, 192, 250],
    *[8, 11, 0, 2, 2, 2, 0, 0],
    *[9, 4, 0, 0, 1, 2, 2, 1, 0],  # Interface
    *[5, 36, 0, 16, 1],
    *[5, 36, 1, 3, 1],
    *[4, 36, 2, 6],
    *[5, 36, 6, 0, 1],
    *[7, 5, 130, 3, 64, 0, 16],  # Endpoint
    *[9, 4, 1, 0, 2, 10, 0, 0, 0],  # Interface
    *[7, 5, 129, 2, 64, 0, 0],  # Endpoint
    *[7, 5, 1, 2, 64, 0, 0],  # Endpoint
]


def test_does_not_die_if_the_descriptors_are_invalid():
    assert extract_endpoint_numbers([0]) == EndpointNumbers(in_endpoint=-1, out_endpoint=-1)
    assert extract_endpoint_numbers([9]) == EndpointNumbers(in_endpoint=-1, out_endpoint=-1)


def test_extracts_the_endpoint_numbers_from_pi_pico_sdk_descriptors():
    assert extract_endpoint_numbers(_SDK_CDC_DESCRIPTORS) == EndpointNumbers(in_endpoint=2, out_endpoint=2)


def test_extracts_the_endpoint_numbers_from_micropython_descriptors():
    assert extract_endpoint_numbers(_MICROPYTHON_DESCRIPTORS) == EndpointNumbers(in_endpoint=2, out_endpoint=2)


def test_extracts_the_endpoint_numbers_from_circuitpython_descriptors():
    assert extract_endpoint_numbers(_CIRCUITPYTHON_DESCRIPTORS) == EndpointNumbers(in_endpoint=2, out_endpoint=2)


def test_extracts_the_endpoint_numbers_from_arduino_core_descriptors():
    assert extract_endpoint_numbers(_ARDUINO_CORE_DESCRIPTORS) == EndpointNumbers(in_endpoint=1, out_endpoint=1)


# --- 0088's control-line/line-coding surface -------------------------------------------------
#
# Descriptor sets above are reused rather than re-typed: the CDC *control* interface is the one
# carrying class 0x02, and every real firmware here numbers it 0 (the data interface, class 0x0A,
# is the one extract_endpoint_numbers() looks for).


def test_extracts_the_cdc_control_interface_number():
    for descriptors in (
        _SDK_CDC_DESCRIPTORS,
        _MICROPYTHON_DESCRIPTORS,
        _CIRCUITPYTHON_DESCRIPTORS,
        _ARDUINO_CORE_DESCRIPTORS,
    ):
        assert extract_control_interface_number(descriptors) == 0


def test_control_interface_number_is_minus_one_without_a_cdc_control_interface():
    assert extract_control_interface_number([0]) == -1
    assert extract_control_interface_number([9]) == -1
    # Data interface (class 10) only - a descriptor set with no control interface at all.
    assert extract_control_interface_number([*[9, 4, 1, 0, 2, 10, 0, 0, 0], *[7, 5, 2, 2, 64, 0, 0]]) == -1


def test_line_coding_packs_the_pstn_payload():
    assert LineCoding(115200).to_bytes() == bytes([0x00, 0xC2, 0x01, 0x00, 0, 0, 8])
    assert LineCoding(1200).to_bytes() == bytes([0xB0, 0x04, 0x00, 0x00, 0, 0, 8])
    assert LineCoding(9600, stop_bits=2, parity=1, data_bits=7).to_bytes() == bytes([0x80, 0x25, 0x00, 0x00, 2, 1, 7])


class _FakeUSBController:
    """Just the four hooks and two methods `USBCDC` touches on `RPUSBController`."""

    def __init__(self):
        self.setup_packets = []
        self.reads_done = []
        self.reset_calls = 0
        self.on_usb_enabled = None
        self.on_reset_received = None
        self.on_endpoint_write = None
        self.on_endpoint_read = None

    def send_setup_packet(self, packet):
        self.setup_packets.append(bytes(packet))

    def endpoint_read_done(self, endpoint, buffer, delay=None):
        self.reads_done.append((endpoint, bytes(buffer)))

    def reset_device(self):
        pass

    def reset(self):
        self.reset_calls += 1


def _enumerated_cdc(descriptors=None):
    """A `USBCDC` walked through the same enumeration the firmware drives, so its endpoint and
    control-interface numbers are the ones a real descriptor set produces."""
    descriptors = list(_MICROPYTHON_DESCRIPTORS if descriptors is None else descriptors)
    usb = _FakeUSBController()
    cdc = USBCDC(usb)
    usb.on_endpoint_write(0, b"")  # asks for the 9-byte configuration descriptor header
    usb.on_endpoint_write(0, bytes(descriptors[:9]))
    usb.on_endpoint_write(0, bytes(descriptors))
    usb.on_endpoint_write(0, b"")  # the SET_CONFIGURATION ack -> SET_CONTROL_LINE_STATE
    return cdc, usb


def _parse_setup(packet):
    return {
        "bmRequestType": packet[0],
        "bRequest": packet[1],
        "wValue": packet[2] | (packet[3] << 8),
        "wIndex": packet[4] | (packet[5] << 8),
        "wLength": packet[6] | (packet[7] << 8),
    }


def test_enumeration_asserts_dtr_and_rts_at_the_control_interface():
    cdc, usb = _enumerated_cdc()

    request = _parse_setup(usb.setup_packets[-1])
    # 0x21 = host-to-device | class | interface recipient, which is what CDC PSTN specifies (0088
    # section 1: we used to send 0x20, a device recipient, and TinyUSB accepted it out of
    # leniency).
    assert request["bmRequestType"] == 0x21
    assert request["bRequest"] == CDC_REQUEST_SET_CONTROL_LINE_STATE
    assert request["wValue"] == CDC_DTR | CDC_RTS
    assert request["wIndex"] == 0
    assert request["wLength"] == 0
    assert cdc.dtr and cdc.rts
    assert cdc.control_line_state == CDC_DTR | CDC_RTS


def test_set_control_lines_can_drop_and_reassert_them():
    cdc, usb = _enumerated_cdc()

    cdc.set_control_lines(dtr=False, rts=False)
    assert _parse_setup(usb.setup_packets[-1])["wValue"] == 0
    assert not cdc.dtr and not cdc.rts

    cdc.set_control_lines(dtr=True, rts=False)
    assert _parse_setup(usb.setup_packets[-1])["wValue"] == CDC_DTR
    assert cdc.dtr and not cdc.rts


def test_set_line_coding_sends_the_request_and_serves_its_data_stage():
    cdc, usb = _enumerated_cdc()
    assert cdc.line_coding == LineCoding()

    cdc.set_line_coding(1200)

    request = _parse_setup(usb.setup_packets[-1])
    assert request["bmRequestType"] == 0x21
    assert request["bRequest"] == CDC_REQUEST_SET_LINE_CODING
    assert request["wValue"] == 0
    assert request["wIndex"] == 0
    assert request["wLength"] == LINE_CODING_SIZE
    # Nothing is applied until the firmware actually collects the payload off EP0 OUT.
    assert cdc.line_coding == LineCoding()

    usb.on_endpoint_read(0, LINE_CODING_SIZE)
    assert usb.reads_done[-1] == (0, LineCoding(1200).to_bytes())
    assert cdc.line_coding == LineCoding(1200)

    # Served once: a later EP0 OUT transfer must not be fed a stale line coding.
    usb.reads_done.clear()
    usb.on_endpoint_read(0, LINE_CODING_SIZE)
    assert usb.reads_done == []


def test_reset_clears_the_control_line_state():
    cdc, usb = _enumerated_cdc()
    cdc.set_line_coding(1200)
    usb.on_endpoint_read(0, LINE_CODING_SIZE)

    cdc.reset()

    # A chip off the bus asserts nothing, and the firmware coming back up starts from its own
    # defaults - the re-enumeration re-asserts DTR/RTS through the same path a first boot uses.
    assert cdc.control_line_state == 0
    assert not cdc.dtr and not cdc.rts
    assert cdc.line_coding == LineCoding()
    assert usb.reset_calls == 1

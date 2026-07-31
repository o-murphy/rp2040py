from rp2040py.cli.intelhex import load_hex


def _record(byte_count: int, address: int, record_type: int, data: bytes) -> str:
    payload = bytes([byte_count, address >> 8, address & 0xFF, record_type]) + data
    checksum = (-sum(payload)) & 0xFF
    return ":" + payload.hex().upper() + f"{checksum:02X}"


def test_single_data_record():
    line = _record(4, 0x0000, 0x00, bytes([0xAA, 0xBB, 0xCC, 0xDD]))
    target = bytearray(4)

    load_hex(line, target)

    assert target == bytearray([0xAA, 0xBB, 0xCC, 0xDD])


def test_data_record_written_at_its_address():
    line = _record(2, 0x0010, 0x00, bytes([0x11, 0x22]))
    target = bytearray(0x12)

    load_hex(line, target)

    assert target[0x10:0x12] == bytearray([0x11, 0x22])


def test_extended_linear_address_shifts_subsequent_records():
    ela = _record(2, 0x0000, 0x04, bytes([0x00, 0x01]))  # high 16 bits = 0x0001
    data = _record(1, 0x0010, 0x00, bytes([0xAA]))
    target = bytearray(0x10020)

    load_hex(ela + "\n" + data, target)

    assert target[0x10010] == 0xAA


def test_base_address_is_subtracted_from_the_record_address():
    # Firmware images are commonly built for FLASH_START_ADDRESS - loading them into a target
    # buffer that starts at 0 means subtracting that base back out.
    ela = _record(2, 0x0000, 0x04, bytes([0x10, 0x00]))  # high 16 bits = 0x1000 -> 0x10000000
    data = _record(2, 0x0004, 0x00, bytes([0xDE, 0xAD]))
    target = bytearray(0x10)

    load_hex(ela + "\n" + data, target, base_address=0x10000000)

    assert target[0x4:0x6] == bytearray([0xDE, 0xAD])


def test_blank_and_non_colon_lines_are_ignored():
    data = _record(1, 0x0000, 0x00, bytes([0x7F]))
    source = f"\n   \n; not a hex record\n{data}\n"
    target = bytearray(1)

    load_hex(source, target)

    assert target[0] == 0x7F


def test_multiple_data_records_accumulate():
    lines = [
        _record(2, 0x0000, 0x00, bytes([0x01, 0x02])),
        _record(2, 0x0002, 0x00, bytes([0x03, 0x04])),
    ]
    target = bytearray(4)

    load_hex("\n".join(lines), target)

    assert target == bytearray([0x01, 0x02, 0x03, 0x04])

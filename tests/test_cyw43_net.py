"""Unit tests for `rp2040py.external.cyw43.net` - pure Ethernet/ARP/IPv4/UDP/TCP/DHCP wire-format
pack/parse helpers (step 4, docs/records/0048-cyw43-nat-reflector.md). Hand-built byte vectors, not
just round-trip self-consistency - `test_internet_checksum_matches_a_known_ipv4_header_vector()`
cross-checks against an independently written reference implementation and a widely-cited textbook
IPv4-header example, not `net.py`'s own code.
"""

from rp2040py.external.cyw43 import net

_MAC_A = bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x01])
_MAC_B = bytes([0x00, 0x10, 0x18, 0x00, 0x00, 0x02])
_IP_A = bytes([10, 0, 0, 1])
_IP_B = bytes([10, 0, 0, 2])


def _reference_checksum(data: bytes) -> int:
    """Independently written RFC 1071 one's-complement checksum - deliberately not sharing any
    code with `net.internet_checksum()`, so a bug shared by both implementations can't hide."""
    if len(data) % 2:
        data = data + b"\x00"
    total = sum((data[i] << 8) | data[i + 1] for i in range(0, len(data), 2))
    while total > 0xFFFF:
        total = (total & 0xFFFF) + (total >> 16)
    return total ^ 0xFFFF


def test_internet_checksum_matches_a_known_ipv4_header_vector():
    # A widely-cited textbook IPv4 header example (20 bytes, checksum field zeroed at offset
    # 10:12) - independently verified (2026-08-16) against a from-scratch reference
    # implementation, not copied from net.py.
    header_zeroed = bytes.fromhex("4500003c1c46400040060000ac100a63ac100a0c")
    assert net.internet_checksum(header_zeroed) == 0xB1E6
    assert _reference_checksum(header_zeroed) == 0xB1E6


def test_internet_checksum_of_a_correctly_checksummed_header_is_zero():
    header_zeroed = bytes.fromhex("4500003c1c46400040060000ac100a63ac100a0c")
    checksum = net.internet_checksum(header_zeroed)
    header_filled = header_zeroed[:10] + checksum.to_bytes(2, "big") + header_zeroed[12:]
    assert net.internet_checksum(header_filled) == 0


def test_internet_checksum_matches_reference_on_odd_length_input():
    data = b"\x01\x02\x03\x04\x05"  # odd length - exercises the trailing zero-pad path
    assert net.internet_checksum(data) == _reference_checksum(data)


def test_ethernet_round_trip():
    frame = net.pack_ethernet(_MAC_A, _MAC_B, net.ETHERTYPE_IPV4, b"payload")
    parsed = net.parse_ethernet(frame)
    assert parsed is not None
    assert (parsed.dst_mac, parsed.src_mac, parsed.ethertype, parsed.payload) == (
        _MAC_A,
        _MAC_B,
        net.ETHERTYPE_IPV4,
        b"payload",
    )


def test_ethernet_parse_rejects_a_too_short_frame():
    assert net.parse_ethernet(bytes(13)) is None


def test_arp_round_trip():
    request = net.pack_arp(net.ARP_OP_REQUEST, _MAC_A, _IP_A, bytes(6), _IP_B)
    parsed = net.parse_arp(request)
    assert parsed is not None
    assert parsed.op == net.ARP_OP_REQUEST
    assert (parsed.sender_mac, parsed.sender_ip, parsed.target_ip) == (_MAC_A, _IP_A, _IP_B)


def test_arp_parse_rejects_wrong_hardware_or_protocol_type():
    request = bytearray(net.pack_arp(net.ARP_OP_REQUEST, _MAC_A, _IP_A, bytes(6), _IP_B))
    request[1] = 0x02  # corrupt HTYPE's low byte (not Ethernet)
    assert net.parse_arp(bytes(request)) is None


def test_ipv4_round_trip_and_self_checksum_is_zero():
    packet = net.pack_ipv4(_IP_A, _IP_B, net.IP_PROTO_UDP, b"payload", ident=0x1234, ttl=32)
    parsed = net.parse_ipv4(packet)
    assert parsed is not None
    assert (parsed.src_ip, parsed.dst_ip, parsed.proto, parsed.payload) == (_IP_A, _IP_B, net.IP_PROTO_UDP, b"payload")
    assert net.internet_checksum(packet[:20]) == 0


def test_ipv4_parse_rejects_non_ipv4_version():
    packet = bytearray(net.pack_ipv4(_IP_A, _IP_B, net.IP_PROTO_UDP, b"x"))
    packet[0] = 0x65  # version=6
    assert net.parse_ipv4(bytes(packet)) is None


def test_udp_round_trip():
    datagram = net.pack_udp(_IP_A, _IP_B, 67, 68, b"hello")
    parsed = net.parse_udp(datagram)
    assert parsed is not None
    assert (parsed.src_port, parsed.dst_port, parsed.payload) == (67, 68, b"hello")


def test_udp_checksum_is_never_emitted_as_the_reserved_all_zero_value():
    # RFC 768: an all-zero checksum on the wire means "no checksum" - net.py must substitute
    # 0xffff on the rare occasion the real sum computes to exactly zero. src_port=60311 with an
    # empty payload is a confirmed (brute-force found, 2026-08-16) case whose pre-substitution
    # checksum is exactly 0 against this fixed _IP_A/_IP_B/dst_port=68 pseudo-header.
    datagram = net.pack_udp(_IP_A, _IP_B, 60311, 68, b"")
    pseudo_header = _IP_A + _IP_B + bytes([0, net.IP_PROTO_UDP]) + (8).to_bytes(2, "big")
    assert net.internet_checksum(pseudo_header + datagram[:6] + bytes(2)) == 0  # confirms the setup
    checksum = int.from_bytes(datagram[6:8], "big")
    assert checksum == 0xFFFF


def test_tcp_round_trip_with_mss_option():
    segment = net.pack_tcp(_IP_A, _IP_B, 12345, 80, seq=1000, ack=0, flags=net.TCP_SYN, window=1000, mss=1460)
    parsed = net.parse_tcp(segment)
    assert parsed is not None
    assert (parsed.src_port, parsed.dst_port, parsed.seq, parsed.ack, parsed.flags, parsed.window, parsed.mss) == (
        12345,
        80,
        1000,
        0,
        net.TCP_SYN,
        1000,
        1460,
    )
    assert parsed.payload == b""


def test_tcp_round_trip_with_data_and_no_mss():
    segment = net.pack_tcp(
        _IP_A,
        _IP_B,
        12345,
        80,
        seq=1001,
        ack=500,
        flags=net.TCP_ACK | net.TCP_PSH,
        window=2000,
        payload=b"GET / HTTP/1.0\r\n\r\n",
    )
    parsed = net.parse_tcp(segment)
    assert parsed is not None
    assert parsed.mss is None
    assert parsed.payload == b"GET / HTTP/1.0\r\n\r\n"
    assert parsed.ack == 500


def test_tcp_seq_ack_wrap_around_32_bits():
    segment = net.pack_tcp(_IP_A, _IP_B, 1, 2, seq=0xFFFFFFFF, ack=0xFFFFFFFE, flags=net.TCP_ACK, window=1)
    parsed = net.parse_tcp(segment)
    assert parsed is not None
    assert (parsed.seq, parsed.ack) == (0xFFFFFFFF, 0xFFFFFFFE)


def test_tcp_parse_rejects_a_truncated_header():
    assert net.parse_tcp(bytes(19)) is None


def test_dhcp_round_trip():
    options = {53: bytes([net.DHCP_MSG_OFFER]), 1: bytes([255, 255, 255, 0]), 3: _IP_A}
    message = net.pack_dhcp(net.DHCP_OP_REPLY, xid=0x12345678, chaddr=_MAC_B, yiaddr=_IP_B, options=options)
    parsed = net.parse_dhcp(message)
    assert parsed is not None
    assert parsed.op == net.DHCP_OP_REPLY
    assert parsed.xid == 0x12345678
    assert parsed.chaddr == _MAC_B
    assert parsed.options[53] == bytes([net.DHCP_MSG_OFFER])
    assert parsed.options[1] == bytes([255, 255, 255, 0])
    assert parsed.options[3] == _IP_A


def test_dhcp_parse_rejects_a_missing_magic_cookie():
    message = bytearray(net.pack_dhcp(net.DHCP_OP_REQUEST, xid=1, chaddr=_MAC_A, yiaddr=bytes(4), options={}))
    message[236] ^= 0xFF  # corrupt the magic cookie's first byte
    assert net.parse_dhcp(bytes(message)) is None

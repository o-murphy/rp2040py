"""Pure Ethernet/ARP/IPv4/UDP/TCP/DHCP wire-format pack/parse helpers for the CYW43 NAT bridge
(`nat.py`, step 4 of `docs/records/0027-cyw43-wifi.md`, decided in
`docs/records/0048-cyw43-nat-reflector.md`). No `rp2040`/`asyncio`/SDPCM knowledge - that framing
stays entirely in `bus.py`, matching how `_build_async_event()` already keeps SDPCM/BDC framing
separate from the protocol content it carries.

Deliberately minimal, not general-purpose: only the fields/options `nat.py`'s DHCP server, ARP
responder, and TCP reflector actually need. No IPv4 options, no TCP window-scale option (confirmed
unneeded - the guest's own vendored lwIP build, `extmod/lwip-include/lwipopts_common.h`, defines no
`LWIP_WND_SCALE`), no inbound checksum validation (this bus's whole trust boundary is an in-process,
lossless FIFO feeding a real, correct guest TCP/IP stack - not an adversarial network).
"""

from dataclasses import dataclass

__all__ = (
    "ARP_OP_REPLY",
    "ARP_OP_REQUEST",
    "DHCP_MAGIC_COOKIE",
    "DHCP_MSG_ACK",
    "DHCP_MSG_DISCOVER",
    "DHCP_MSG_OFFER",
    "DHCP_MSG_REQUEST",
    "DHCP_OP_REPLY",
    "DHCP_OP_REQUEST",
    "ETHERTYPE_ARP",
    "ETHERTYPE_IPV4",
    "IP_PROTO_TCP",
    "IP_PROTO_UDP",
    "TCP_ACK",
    "TCP_FIN",
    "TCP_PSH",
    "TCP_RST",
    "TCP_SYN",
    "ArpPacket",
    "DhcpMessage",
    "EthernetFrame",
    "Ipv4Packet",
    "TcpSegment",
    "UdpDatagram",
    "internet_checksum",
    "pack_arp",
    "pack_dhcp",
    "pack_ethernet",
    "pack_ipv4",
    "pack_tcp",
    "pack_udp",
    "parse_arp",
    "parse_dhcp",
    "parse_ethernet",
    "parse_ipv4",
    "parse_tcp",
    "parse_udp",
)

ETHERTYPE_ARP = 0x0806
ETHERTYPE_IPV4 = 0x0800

IP_PROTO_UDP = 17
IP_PROTO_TCP = 6

ARP_OP_REQUEST = 1
ARP_OP_REPLY = 2

TCP_FIN = 0x01
TCP_SYN = 0x02
TCP_RST = 0x04
TCP_PSH = 0x08
TCP_ACK = 0x10

DHCP_OP_REQUEST = 1  # BOOTREQUEST
DHCP_OP_REPLY = 2  # BOOTREPLY
DHCP_MSG_DISCOVER = 1
DHCP_MSG_OFFER = 2
DHCP_MSG_REQUEST = 3
DHCP_MSG_ACK = 5
DHCP_MAGIC_COOKIE = bytes([0x63, 0x82, 0x53, 0x63])


def internet_checksum(data: bytes) -> int:
    """RFC 1071 one's-complement checksum. `data` is padded with one zero byte if its length is
    odd (RFC 1071 §4.1)."""
    if len(data) % 2:
        data = data + b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


# -- Ethernet -------------------------------------------------------------------------------------


@dataclass
class EthernetFrame:
    dst_mac: bytes
    src_mac: bytes
    ethertype: int
    payload: bytes


def pack_ethernet(dst_mac: bytes, src_mac: bytes, ethertype: int, payload: bytes) -> bytes:
    return dst_mac + src_mac + ethertype.to_bytes(2, "big") + payload


def parse_ethernet(frame: bytes) -> "EthernetFrame | None":
    if len(frame) < 14:
        return None
    return EthernetFrame(frame[0:6], frame[6:12], int.from_bytes(frame[12:14], "big"), frame[14:])


# -- ARP (Ethernet/IPv4 only - hlen=6, plen=4) -----------------------------------------------------


@dataclass
class ArpPacket:
    op: int
    sender_mac: bytes
    sender_ip: bytes
    target_mac: bytes
    target_ip: bytes


def pack_arp(op: int, sender_mac: bytes, sender_ip: bytes, target_mac: bytes, target_ip: bytes) -> bytes:
    return (
        (1).to_bytes(2, "big")  # HTYPE: Ethernet
        + ETHERTYPE_IPV4.to_bytes(2, "big")  # PTYPE
        + bytes([6, 4])  # HLEN, PLEN
        + op.to_bytes(2, "big")
        + sender_mac
        + sender_ip
        + target_mac
        + target_ip
    )


def parse_arp(payload: bytes) -> "ArpPacket | None":
    if len(payload) < 28 or payload[0:4] != b"\x00\x01\x08\x00" or payload[4] != 6 or payload[5] != 4:
        return None
    return ArpPacket(
        op=int.from_bytes(payload[6:8], "big"),
        sender_mac=payload[8:14],
        sender_ip=payload[14:18],
        target_mac=payload[18:24],
        target_ip=payload[24:28],
    )


# -- IPv4 (no options, no fragmentation - not needed by anything nat.py builds/parses) -------------


@dataclass
class Ipv4Packet:
    src_ip: bytes
    dst_ip: bytes
    proto: int
    payload: bytes


def pack_ipv4(src_ip: bytes, dst_ip: bytes, proto: int, payload: bytes, *, ident: int = 0, ttl: int = 64) -> bytes:
    total_len = 20 + len(payload)
    header = (
        bytes([0x45, 0x00])  # version=4, IHL=5 (20 bytes, no options); DSCP/ECN=0
        + total_len.to_bytes(2, "big")
        + ident.to_bytes(2, "big")
        + bytes([0x40, 0x00])  # flags=DF, fragment offset=0
        + bytes([ttl, proto])
        + bytes(2)  # checksum placeholder
        + src_ip
        + dst_ip
    )
    checksum = internet_checksum(header)
    return header[:10] + checksum.to_bytes(2, "big") + header[12:] + payload


def parse_ipv4(packet: bytes) -> "Ipv4Packet | None":
    if len(packet) < 20 or (packet[0] >> 4) != 4:
        return None
    ihl = (packet[0] & 0x0F) * 4
    if ihl < 20 or len(packet) < ihl:
        return None
    total_len = int.from_bytes(packet[2:4], "big")
    end = total_len if ihl <= total_len <= len(packet) else len(packet)
    return Ipv4Packet(src_ip=packet[12:16], dst_ip=packet[16:20], proto=packet[9], payload=packet[ihl:end])


# -- UDP --------------------------------------------------------------------------------------------


@dataclass
class UdpDatagram:
    src_port: int
    dst_port: int
    payload: bytes


def pack_udp(src_ip: bytes, dst_ip: bytes, src_port: int, dst_port: int, payload: bytes) -> bytes:
    length = 8 + len(payload)
    segment = src_port.to_bytes(2, "big") + dst_port.to_bytes(2, "big") + length.to_bytes(2, "big") + bytes(2)
    pseudo_header = src_ip + dst_ip + bytes([0, IP_PROTO_UDP]) + length.to_bytes(2, "big")
    checksum = internet_checksum(pseudo_header + segment + payload)
    checksum = checksum or 0xFFFF  # RFC 768: all-zero on the wire means "no checksum" - never emit it
    return segment[:6] + checksum.to_bytes(2, "big") + payload


def parse_udp(datagram: bytes) -> "UdpDatagram | None":
    if len(datagram) < 8:
        return None
    length = int.from_bytes(datagram[4:6], "big")
    end = length if 8 <= length <= len(datagram) else len(datagram)
    return UdpDatagram(
        src_port=int.from_bytes(datagram[0:2], "big"),
        dst_port=int.from_bytes(datagram[2:4], "big"),
        payload=datagram[8:end],
    )


# -- TCP (no window-scale option - see module docstring) --------------------------------------------


@dataclass
class TcpSegment:
    src_port: int
    dst_port: int
    seq: int
    ack: int
    flags: int
    window: int
    mss: "int | None"
    payload: bytes


def pack_tcp(
    src_ip: bytes,
    dst_ip: bytes,
    src_port: int,
    dst_port: int,
    seq: int,
    ack: int,
    flags: int,
    window: int,
    payload: bytes = b"",
    *,
    mss: "int | None" = None,
) -> bytes:
    options = bytes([2, 4]) + mss.to_bytes(2, "big") if mss is not None else b""
    data_offset_words = (20 + len(options)) // 4
    header = (
        src_port.to_bytes(2, "big")
        + dst_port.to_bytes(2, "big")
        + (seq & 0xFFFFFFFF).to_bytes(4, "big")
        + (ack & 0xFFFFFFFF).to_bytes(4, "big")
        + bytes([data_offset_words << 4, flags])
        + window.to_bytes(2, "big")
        + bytes(2)  # checksum placeholder
        + bytes(2)  # urgent pointer, unused
        + options
    )
    segment = header + payload
    length = len(segment)
    pseudo_header = src_ip + dst_ip + bytes([0, IP_PROTO_TCP]) + length.to_bytes(2, "big")
    checksum = internet_checksum(pseudo_header + segment)
    return segment[:16] + checksum.to_bytes(2, "big") + segment[18:]


def parse_tcp(segment: bytes) -> "TcpSegment | None":
    if len(segment) < 20:
        return None
    data_offset = (segment[12] >> 4) * 4
    if data_offset < 20 or len(segment) < data_offset:
        return None
    mss = None
    options = segment[20:data_offset]
    i = 0
    while i + 1 < len(options):
        kind = options[i]
        if kind == 0:  # end of option list
            break
        if kind == 1:  # NOP
            i += 1
            continue
        opt_len = options[i + 1]
        if opt_len < 2 or i + opt_len > len(options):
            break
        if kind == 2 and opt_len == 4:
            mss = int.from_bytes(options[i + 2 : i + 4], "big")
        i += opt_len
    return TcpSegment(
        src_port=int.from_bytes(segment[0:2], "big"),
        dst_port=int.from_bytes(segment[2:4], "big"),
        seq=int.from_bytes(segment[4:8], "big"),
        ack=int.from_bytes(segment[8:12], "big"),
        flags=segment[13],
        window=int.from_bytes(segment[14:16], "big"),
        mss=mss,
        payload=segment[data_offset:],
    )


# -- DHCP/BOOTP (fixed 236-byte header + 4-byte magic cookie + TLV options) -------------------------


@dataclass
class DhcpMessage:
    op: int
    xid: int
    chaddr: bytes
    ciaddr: bytes
    options: "dict[int, bytes]"


def pack_dhcp(op: int, xid: int, chaddr: bytes, yiaddr: bytes, options: "dict[int, bytes]") -> bytes:
    packed_options = b"".join(bytes([code, len(value)]) + value for code, value in options.items()) + bytes([255])
    header = (
        bytes([op, 1, 6, 0])  # op, htype=Ethernet, hlen=6, hops=0
        + xid.to_bytes(4, "big")
        + bytes(4)  # secs, flags
        + bytes(4)  # ciaddr
        + yiaddr
        + bytes(4)  # siaddr
        + bytes(4)  # giaddr
        + (chaddr + bytes(16))[:16]  # chaddr, zero-padded to 16 bytes
        + bytes(64)  # sname
        + bytes(128)  # file
    )
    return header + DHCP_MAGIC_COOKIE + packed_options


def parse_dhcp(payload: bytes) -> "DhcpMessage | None":
    if len(payload) < 240 or payload[236:240] != DHCP_MAGIC_COOKIE:
        return None
    options: dict[int, bytes] = {}
    i = 240
    while i < len(payload):
        code = payload[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        if i + 1 >= len(payload):
            break
        length = payload[i + 1]
        options[code] = payload[i + 2 : i + 2 + length]
        i += 2 + length
    return DhcpMessage(
        op=payload[0],
        xid=int.from_bytes(payload[4:8], "big"),
        chaddr=payload[28:34],
        ciaddr=payload[12:16],
        options=options,
    )

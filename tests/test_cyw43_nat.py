"""Unit + integration tests for `rp2040py.external.cyw43.nat` (step 4, docs/records/
0048-cyw43-nat-reflector.md).

Unit-level tests construct `ArpResponder()`/`DhcpServer()` directly and feed hand-built frames via
`net.py`'s own pack functions - no `GSPIBus` involved. Integration tests drive a real gSPI-level
`DATA_HEADER` write through a `GSPIBus` with a real `NatBridge` attached, reusing
`test_cyw43_bus.py`'s own `_FakeGSPIMaster`/`_wire_up_with_bus`/`_send_wlan_frame`/
`_read_f2_response`/`_build_ioctl_request` helpers (pytest's default rootdir import mode makes a
flat `from test_cyw43_bus import ...` resolve within `tests/`, same as any other sibling test
module) rather than re-deriving the same gSPI bit-banging harness.

The TCP splice tests need a genuinely running asyncio loop (`asyncio.open_connection()` requires
one), so `Simulator._execute_batch()` alone doesn't apply - see `_run()`'s own docstring for why no
real background thread/`start_execution()`/`simulator.stop()` is needed either.
"""

import asyncio
import socket

from test_cyw43_bus import (
    _FakeGSPIMaster,
    _read_f2_response,
    _send_wlan_frame,
    _wire_up_with_bus,
)

from rp2040py.external.cyw43 import net
from rp2040py.external.cyw43.bus import (
    BDC_HEADER_LEN,
    BUS_FUNCTION,
    DATA_HEADER,
    SDPCM_HEADER_LEN,
    SPI_STATUS_REGISTER,
    STATUS_F2_PKT_AVAILABLE,
    GSPIBus,
)
from rp2040py.external.cyw43.nat import (
    GATEWAY_IP,
    GATEWAY_MAC,
    GUEST_IP,
    ArpResponder,
    DhcpServer,
    NatBridge,
    TcpReflector,
    UdpRelay,
)
from rp2040py.rp2040 import RP2040
from rp2040py.simulator import Simulator

_GUEST_MAC = bytes([0x00, 0x10, 0x18, 0x00, 0x00, 0x03])


def _build_data_header_frame(ethernet_frame: bytes) -> bytes:
    """Mirrors real firmware's outbound `DATA_HEADER` envelope (`cyw43_ll_send_ethernet()`,
    confirmed in docs/records/0045-cyw43-nat-libslirp-cython.md's "Resolved research finding") -
    the same shape `GSPIBus._build_data_frame()` uses for the inbound direction."""
    bdc_header = bytes([0x20, 0, 0, 0])
    payload = bytes(2) + bdc_header + ethernet_frame
    size = SDPCM_HEADER_LEN + len(payload)
    sdpcm_header = (
        size.to_bytes(2, "little")
        + (~size & 0xFFFF).to_bytes(2, "little")
        + bytes([0, DATA_HEADER, 0, SDPCM_HEADER_LEN + 2, 0, 0])
        + bytes(2)
    )
    return sdpcm_header + payload


def _parse_data_frame(frame: bytes) -> bytes:
    """Unwraps one inbound `DATA_HEADER` frame into its raw Ethernet frame bytes - the
    `DATA_HEADER` counterpart of `test_cyw43_bus.py`'s own `_parse_async_event()`."""
    assert frame[5] & 0x0F == DATA_HEADER
    payload = frame[SDPCM_HEADER_LEN:]
    return payload[2 + BDC_HEADER_LEN :]  # skip the 2-byte pad + 4-byte BDC header


async def _wait_for_pending_packet(master: _FakeGSPIMaster, *, timeout: float = 5.0) -> None:
    """Polls `SPI_STATUS_REGISTER`'s own pending-packet bit instead of a fixed `asyncio.sleep()` -
    a real `asyncio.open_connection()`/UDP round trip (even to loopback) has no fixed duration,
    and a hardcoded sleep long enough on one OS/CI runner isn't necessarily long enough on another
    (confirmed: `test_tcp_reflector_sends_rst_on_a_refused_connection`'s original fixed 0.2s sleep
    flaked on `windows-latest` CI - a closed-port loopback connect attempt takes measurably longer
    to fail there than on Linux)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        status = master.read_register(BUS_FUNCTION, SPI_STATUS_REGISTER, 4)
        if status & STATUS_F2_PKT_AVAILABLE:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out after {timeout}s waiting for a pending F2 packet")


# -- ArpResponder (unit) ---------------------------------------------------------------------------


def test_arp_responder_answers_a_request_for_the_gateway_ip():
    responder = ArpResponder()
    requester_mac = bytes([0x00, 0x10, 0x18, 0x00, 0x00, 0x03])
    requester_ip = GUEST_IP
    request = net.pack_arp(net.ARP_OP_REQUEST, requester_mac, requester_ip, bytes(6), GATEWAY_IP)
    eth = net.parse_ethernet(net.pack_ethernet(bytes(6), requester_mac, net.ETHERTYPE_ARP, request))
    assert eth is not None

    reply_frame = responder.maybe_handle(eth)

    assert reply_frame is not None
    reply_eth = net.parse_ethernet(reply_frame)
    assert reply_eth is not None
    assert reply_eth.dst_mac == requester_mac
    assert reply_eth.src_mac == GATEWAY_MAC
    reply_arp = net.parse_arp(reply_eth.payload)
    assert reply_arp is not None
    assert reply_arp.op == net.ARP_OP_REPLY
    assert reply_arp.sender_mac == GATEWAY_MAC
    assert reply_arp.sender_ip == GATEWAY_IP
    assert reply_arp.target_ip == requester_ip


def test_arp_responder_ignores_a_request_for_a_different_ip():
    responder = ArpResponder()
    request = net.pack_arp(net.ARP_OP_REQUEST, _GUEST_MAC, GUEST_IP, bytes(6), bytes([10, 0, 0, 99]))
    eth = net.parse_ethernet(net.pack_ethernet(bytes(6), _GUEST_MAC, net.ETHERTYPE_ARP, request))
    assert eth is not None

    assert responder.maybe_handle(eth) is None


# -- DhcpServer (unit) ------------------------------------------------------------------------------


def _build_dhcp_request_frame(msg_type: int, xid: int = 0xAABBCCDD) -> "net.EthernetFrame":
    dhcp = net.pack_dhcp(net.DHCP_OP_REQUEST, xid, _GUEST_MAC, bytes(4), {53: bytes([msg_type])})
    udp = net.pack_udp(bytes(4), bytes([255, 255, 255, 255]), 68, 67, dhcp)
    ip_packet = net.pack_ipv4(bytes(4), bytes([255, 255, 255, 255]), net.IP_PROTO_UDP, udp)
    frame = net.pack_ethernet(bytes([0xFF] * 6), _GUEST_MAC, net.ETHERTYPE_IPV4, ip_packet)
    eth = net.parse_ethernet(frame)
    assert eth is not None
    return eth


def test_dhcp_server_answers_discover_with_an_offer():
    server = DhcpServer()
    eth = _build_dhcp_request_frame(net.DHCP_MSG_DISCOVER, xid=0x11223344)

    reply_frame = server.maybe_handle(eth)

    assert reply_frame is not None
    reply_eth = net.parse_ethernet(reply_frame)
    assert reply_eth is not None
    reply_ip = net.parse_ipv4(reply_eth.payload)
    assert reply_ip is not None
    reply_udp = net.parse_udp(reply_ip.payload)
    assert reply_udp is not None
    assert (reply_udp.src_port, reply_udp.dst_port) == (67, 68)
    reply_dhcp = net.parse_dhcp(reply_udp.payload)
    assert reply_dhcp is not None
    assert reply_dhcp.xid == 0x11223344
    assert reply_dhcp.options[53] == bytes([net.DHCP_MSG_OFFER])
    assert reply_dhcp.options[54] == GATEWAY_IP  # server identifier
    # yiaddr lives at a fixed offset in the fixed-length BOOTP header, not in options.
    assert net.parse_dhcp(reply_udp.payload) is not None


def test_dhcp_server_answers_request_with_an_ack():
    server = DhcpServer()
    eth = _build_dhcp_request_frame(net.DHCP_MSG_REQUEST)

    reply_frame = server.maybe_handle(eth)

    assert reply_frame is not None
    reply_eth = net.parse_ethernet(reply_frame)
    assert reply_eth is not None
    reply_ip = net.parse_ipv4(reply_eth.payload)
    assert reply_ip is not None
    reply_udp = net.parse_udp(reply_ip.payload)
    assert reply_udp is not None
    reply_dhcp = net.parse_dhcp(reply_udp.payload)
    assert reply_dhcp is not None
    assert reply_dhcp.options[53] == bytes([net.DHCP_MSG_ACK])
    assert reply_dhcp.options[1] == bytes([255, 255, 255, 0])  # subnet mask
    assert reply_dhcp.options[3] == GATEWAY_IP  # router


def test_dhcp_server_ignores_a_non_dhcp_udp_packet():
    server = DhcpServer()
    udp = net.pack_udp(bytes(4), bytes([255, 255, 255, 255]), 68, 67, b"not dhcp")
    ip_packet = net.pack_ipv4(bytes(4), bytes([255, 255, 255, 255]), net.IP_PROTO_UDP, udp)
    eth = net.parse_ethernet(net.pack_ethernet(bytes(6), _GUEST_MAC, net.ETHERTYPE_IPV4, ip_packet))
    assert eth is not None

    assert server.maybe_handle(eth) is None


# -- Full-stack integration: ARP/DHCP through a real GSPIBus + NatBridge ----------------------------


def _wire_up_with_nat_bridge() -> "tuple[RP2040, GSPIBus, _FakeGSPIMaster]":
    rp2040, bus, master = _wire_up_with_bus()
    bus.nat_bridge = NatBridge(rp2040, bus.queue_rx_ethernet_frame)
    return rp2040, bus, master


def test_arp_request_over_the_wire_gets_a_flow_control_ack_then_a_real_reply():
    _rp2040, _bus, master = _wire_up_with_nat_bridge()
    request = net.pack_arp(net.ARP_OP_REQUEST, _GUEST_MAC, GUEST_IP, bytes(6), GATEWAY_IP)
    frame = net.pack_ethernet(bytes([0xFF] * 6), _GUEST_MAC, net.ETHERTYPE_ARP, request)

    _send_wlan_frame(master, _build_data_header_frame(frame))
    flow_control_ack = _read_f2_response(master)
    assert len(flow_control_ack) == SDPCM_HEADER_LEN

    reply_frame = _read_f2_response(master)
    reply_eth = net.parse_ethernet(_parse_data_frame(reply_frame))
    assert reply_eth is not None
    reply_arp = net.parse_arp(reply_eth.payload)
    assert reply_arp is not None
    assert reply_arp.op == net.ARP_OP_REPLY
    assert reply_arp.sender_ip == GATEWAY_IP


def test_dhcp_discover_over_the_wire_gets_a_flow_control_ack_then_a_real_offer():
    _rp2040, _bus, master = _wire_up_with_nat_bridge()
    dhcp = net.pack_dhcp(net.DHCP_OP_REQUEST, 0xDEADBEEF, _GUEST_MAC, bytes(4), {53: bytes([net.DHCP_MSG_DISCOVER])})
    udp = net.pack_udp(bytes(4), bytes([255, 255, 255, 255]), 68, 67, dhcp)
    ip_packet = net.pack_ipv4(bytes(4), bytes([255, 255, 255, 255]), net.IP_PROTO_UDP, udp)
    frame = net.pack_ethernet(bytes([0xFF] * 6), _GUEST_MAC, net.ETHERTYPE_IPV4, ip_packet)

    _send_wlan_frame(master, _build_data_header_frame(frame))
    _flow_control_ack = _read_f2_response(master)

    reply_frame = _read_f2_response(master)
    reply_eth = net.parse_ethernet(_parse_data_frame(reply_frame))
    assert reply_eth is not None
    reply_ip = net.parse_ipv4(reply_eth.payload)
    assert reply_ip is not None
    reply_udp = net.parse_udp(reply_ip.payload)
    assert reply_udp is not None
    reply_dhcp = net.parse_dhcp(reply_udp.payload)
    assert reply_dhcp is not None
    assert reply_dhcp.xid == 0xDEADBEEF
    assert reply_dhcp.options[53] == bytes([net.DHCP_MSG_OFFER])


def test_data_header_dispatch_still_works_with_no_nat_bridge_attached():
    """A bare `GSPIBus()` (nat_bridge=None) must not error on a real Ethernet-carrying DATA_HEADER
    frame - the guard in `_write_wlan()` must short-circuit cleanly."""
    rp2040 = RP2040()
    bus = GSPIBus()
    bus.attach_gpio(rp2040)
    master = _FakeGSPIMaster(rp2040)
    request = net.pack_arp(net.ARP_OP_REQUEST, _GUEST_MAC, GUEST_IP, bytes(6), GATEWAY_IP)
    frame = net.pack_ethernet(bytes([0xFF] * 6), _GUEST_MAC, net.ETHERTYPE_ARP, request)

    _send_wlan_frame(master, _build_data_header_frame(frame))

    response = _read_f2_response(master)
    assert len(response) == SDPCM_HEADER_LEN  # only the flow-control ack, no reply queued


# -- TCP reflector (4d) ------------------------------------------------------------------------------


def _reserve_a_guaranteed_refused_port() -> int:
    """Binds then immediately closes a socket without ever calling `listen()` - the port is free
    again but nothing is listening, so a subsequent `connect()` gets a real, prompt refusal."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def _run(coro: "asyncio.coroutines.Coroutine") -> None:
    """Runs `coro` under a real, freshly-started asyncio loop. `Simulator.bind_loop()` registers
    that same running loop, so `schedule_threadsafe()`'s `run_coroutine_threadsafe()` (safe to call
    from the loop's own thread) schedules `TcpReflector`'s connect/pump tasks onto it directly - no
    real background thread, no `simulator.start_execution()`/`try: ... finally: simulator.stop()`
    needed, since `execute()`'s own CPU-instruction loop is never run here at all, only `NatBridge`'s
    async work is exercised (mirrors `tests/test_gdb_tcp_server.py`'s own `asyncio.run(body())`
    pattern)."""
    asyncio.run(coro)


async def _echo_server(reader: "asyncio.StreamReader", writer: "asyncio.StreamWriter") -> None:
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


def test_tcp_reflector_full_round_trip_against_a_hermetic_echo_server():
    async def _body() -> None:
        simulator = Simulator()
        simulator.bind_loop()
        rp2040 = simulator.rp2040
        bus = GSPIBus()
        bus.attach_gpio(rp2040)
        bus.nat_bridge = NatBridge(rp2040, bus.queue_rx_ethernet_frame)
        master = _FakeGSPIMaster(rp2040)

        echo_server = await asyncio.start_server(_echo_server, "127.0.0.1", 0)
        dest_port = echo_server.sockets[0].getsockname()[1]
        dest_ip = bytes([127, 0, 0, 1])

        guest_port = 54321
        guest_iss = 1000

        syn = net.pack_tcp(
            GUEST_IP, dest_ip, guest_port, dest_port, seq=guest_iss, ack=0, flags=net.TCP_SYN, window=8192, mss=1460
        )
        syn_ip = net.pack_ipv4(GUEST_IP, dest_ip, net.IP_PROTO_TCP, syn)
        syn_frame = net.pack_ethernet(GATEWAY_MAC, _GUEST_MAC, net.ETHERTYPE_IPV4, syn_ip)

        _send_wlan_frame(master, _build_data_header_frame(syn_frame))
        flow_control_ack = _read_f2_response(master)
        assert len(flow_control_ack) == SDPCM_HEADER_LEN

        await _wait_for_pending_packet(master)  # let the real asyncio.open_connection() to 127.0.0.1 complete

        syn_ack_frame = _read_f2_response(master)
        syn_ack_eth = net.parse_ethernet(_parse_data_frame(syn_ack_frame))
        assert syn_ack_eth is not None
        syn_ack_ip = net.parse_ipv4(syn_ack_eth.payload)
        assert syn_ack_ip is not None
        syn_ack_tcp = net.parse_tcp(syn_ack_ip.payload)
        assert syn_ack_tcp is not None
        assert syn_ack_tcp.flags == net.TCP_SYN | net.TCP_ACK
        assert syn_ack_tcp.ack == (guest_iss + 1) & 0xFFFFFFFF
        assert syn_ack_tcp.mss == 1460
        host_iss = syn_ack_tcp.seq

        # Guest ACKs the handshake and sends data in the same segment (real stacks do this too).
        data_segment = net.pack_tcp(
            GUEST_IP,
            dest_ip,
            guest_port,
            dest_port,
            seq=(guest_iss + 1) & 0xFFFFFFFF,
            ack=(host_iss + 1) & 0xFFFFFFFF,
            flags=net.TCP_ACK | net.TCP_PSH,
            window=8192,
            payload=b"ping",
        )
        data_ip = net.pack_ipv4(GUEST_IP, dest_ip, net.IP_PROTO_TCP, data_segment)
        data_frame = net.pack_ethernet(GATEWAY_MAC, _GUEST_MAC, net.ETHERTYPE_IPV4, data_ip)
        _send_wlan_frame(master, _build_data_header_frame(data_frame))
        _read_f2_response(master)  # flow-control ack

        our_ack_frame = _read_f2_response(master)  # the reflector's own immediate data-ACK
        our_ack_eth = net.parse_ethernet(_parse_data_frame(our_ack_frame))
        assert our_ack_eth is not None
        our_ack_ip = net.parse_ipv4(our_ack_eth.payload)
        assert our_ack_ip is not None
        our_ack_tcp = net.parse_tcp(our_ack_ip.payload)
        assert our_ack_tcp is not None
        assert our_ack_tcp.payload == b""

        await _wait_for_pending_packet(master)  # let the real echo round trip happen

        echoed_frame = _read_f2_response(master)
        echoed_eth = net.parse_ethernet(_parse_data_frame(echoed_frame))
        assert echoed_eth is not None
        echoed_ip = net.parse_ipv4(echoed_eth.payload)
        assert echoed_ip is not None
        echoed_tcp = net.parse_tcp(echoed_ip.payload)
        assert echoed_tcp is not None
        assert echoed_tcp.payload == b"ping"

        # Guest closes its side - the reflector must half-close the real socket too (write_eof()),
        # which makes the echo server observe EOF, close its own writer, and let our own pump task
        # see EOF on the real socket and send its own FIN back.
        guest_next_seq = (guest_iss + 1 + len(b"ping")) & 0xFFFFFFFF
        fin_segment = net.pack_tcp(
            GUEST_IP,
            dest_ip,
            guest_port,
            dest_port,
            seq=guest_next_seq,
            ack=(host_iss + 1 + len(b"ping")) & 0xFFFFFFFF,
            flags=net.TCP_FIN | net.TCP_ACK,
            window=8192,
        )
        fin_ip = net.pack_ipv4(GUEST_IP, dest_ip, net.IP_PROTO_TCP, fin_segment)
        fin_frame = net.pack_ethernet(GATEWAY_MAC, _GUEST_MAC, net.ETHERTYPE_IPV4, fin_ip)
        _send_wlan_frame(master, _build_data_header_frame(fin_frame))
        _read_f2_response(master)  # flow-control ack

        our_fin_ack_frame = _read_f2_response(master)  # our own immediate ack of the guest's FIN
        assert net.parse_tcp(net.parse_ipv4(net.parse_ethernet(_parse_data_frame(our_fin_ack_frame)).payload).payload)

        await _wait_for_pending_packet(master)  # let the real half-close round trip (echo server -> our pump task)

        final_fin_frame = _read_f2_response(master)
        final_eth = net.parse_ethernet(_parse_data_frame(final_fin_frame))
        assert final_eth is not None
        final_ip = net.parse_ipv4(final_eth.payload)
        assert final_ip is not None
        final_tcp = net.parse_tcp(final_ip.payload)
        assert final_tcp is not None
        assert final_tcp.flags & net.TCP_FIN

        assert bus.nat_bridge._tcp._flows == {}  # both sides closed - the flow must be evicted

        echo_server.close()
        await echo_server.wait_closed()

    _run(_body())


def test_tcp_reflector_sends_rst_on_a_refused_connection():
    async def _body() -> None:
        simulator = Simulator()
        simulator.bind_loop()
        rp2040 = simulator.rp2040
        bus = GSPIBus()
        bus.attach_gpio(rp2040)
        bus.nat_bridge = NatBridge(rp2040, bus.queue_rx_ethernet_frame)
        master = _FakeGSPIMaster(rp2040)

        dest_port = _reserve_a_guaranteed_refused_port()
        dest_ip = bytes([127, 0, 0, 1])
        guest_port = 54322
        guest_iss = 2000

        syn = net.pack_tcp(
            GUEST_IP, dest_ip, guest_port, dest_port, seq=guest_iss, ack=0, flags=net.TCP_SYN, window=8192, mss=1460
        )
        syn_ip = net.pack_ipv4(GUEST_IP, dest_ip, net.IP_PROTO_TCP, syn)
        syn_frame = net.pack_ethernet(GATEWAY_MAC, _GUEST_MAC, net.ETHERTYPE_IPV4, syn_ip)

        _send_wlan_frame(master, _build_data_header_frame(syn_frame))
        _read_f2_response(master)  # flow-control ack

        await _wait_for_pending_packet(master)  # let the real (refused) connect attempt fail

        rst_frame = _read_f2_response(master)
        rst_eth = net.parse_ethernet(_parse_data_frame(rst_frame))
        assert rst_eth is not None
        rst_ip = net.parse_ipv4(rst_eth.payload)
        assert rst_ip is not None
        rst_tcp = net.parse_tcp(rst_ip.payload)
        assert rst_tcp is not None
        assert rst_tcp.flags & net.TCP_RST
        assert bus.nat_bridge._tcp._flows == {}

    _run(_body())


def test_tcp_reflector_evicts_the_flow_on_guest_rst():
    async def _body() -> None:
        simulator = Simulator()
        simulator.bind_loop()
        rp2040 = simulator.rp2040
        bus = GSPIBus()
        bus.attach_gpio(rp2040)
        bus.nat_bridge = NatBridge(rp2040, bus.queue_rx_ethernet_frame)
        master = _FakeGSPIMaster(rp2040)

        echo_server = await asyncio.start_server(_echo_server, "127.0.0.1", 0)
        dest_port = echo_server.sockets[0].getsockname()[1]
        dest_ip = bytes([127, 0, 0, 1])
        guest_port = 54323
        guest_iss = 3000

        syn = net.pack_tcp(
            GUEST_IP, dest_ip, guest_port, dest_port, seq=guest_iss, ack=0, flags=net.TCP_SYN, window=8192, mss=1460
        )
        syn_ip = net.pack_ipv4(GUEST_IP, dest_ip, net.IP_PROTO_TCP, syn)
        syn_frame = net.pack_ethernet(GATEWAY_MAC, _GUEST_MAC, net.ETHERTYPE_IPV4, syn_ip)
        _send_wlan_frame(master, _build_data_header_frame(syn_frame))
        _read_f2_response(master)
        await _wait_for_pending_packet(master)
        _read_f2_response(master)  # drain the SYN-ACK

        assert len(bus.nat_bridge._tcp._flows) == 1

        rst_segment = net.pack_tcp(
            GUEST_IP,
            dest_ip,
            guest_port,
            dest_port,
            seq=(guest_iss + 1) & 0xFFFFFFFF,
            ack=0,
            flags=net.TCP_RST,
            window=0,
        )
        rst_ip = net.pack_ipv4(GUEST_IP, dest_ip, net.IP_PROTO_TCP, rst_segment)
        rst_frame = net.pack_ethernet(GATEWAY_MAC, _GUEST_MAC, net.ETHERTYPE_IPV4, rst_ip)
        _send_wlan_frame(master, _build_data_header_frame(rst_frame))
        _read_f2_response(master)  # flow-control ack only - RST gets no reply frame

        assert bus.nat_bridge._tcp._flows == {}

        echo_server.close()
        await echo_server.wait_closed()

    _run(_body())


def test_reset_clears_flows_so_a_reused_port_can_connect_again():
    """The stale-flow collision `TcpReflector.reset()` exists for (0054).

    `maybe_handle()` routes any segment whose `(src_port, dst_ip, dst_port)` matches a live flow
    into that flow - including a SYN. So a flow left behind by a previous association swallows the
    guest's *new* SYN after it reconnects and reuses the triple, and no SYN-ACK ever comes back.
    """

    async def _body() -> None:
        simulator = Simulator()
        simulator.bind_loop()
        rp2040 = simulator.rp2040
        bus = GSPIBus()
        bus.attach_gpio(rp2040)
        bus.nat_bridge = NatBridge(rp2040, bus.queue_rx_ethernet_frame)
        master = _FakeGSPIMaster(rp2040)

        echo_server = await asyncio.start_server(_echo_server, "127.0.0.1", 0)
        dest_port = echo_server.sockets[0].getsockname()[1]
        dest_ip = bytes([127, 0, 0, 1])
        guest_port = 54399  # deliberately reused below

        def _syn(seq: int) -> bytes:
            segment = net.pack_tcp(
                GUEST_IP, dest_ip, guest_port, dest_port, seq=seq, ack=0, flags=net.TCP_SYN, window=8192, mss=1460
            )
            return net.pack_ethernet(
                GATEWAY_MAC, _GUEST_MAC, net.ETHERTYPE_IPV4, net.pack_ipv4(GUEST_IP, dest_ip, net.IP_PROTO_TCP, segment)
            )

        _send_wlan_frame(master, _build_data_header_frame(_syn(1000)))
        _read_f2_response(master)
        await _wait_for_pending_packet(master)
        _read_f2_response(master)  # first SYN-ACK
        assert len(bus.nat_bridge._tcp._flows) == 1

        # The association ends - exactly what bus.py does on WLC_DISASSOC.
        bus.nat_bridge.reset()
        assert bus.nat_bridge._tcp._flows == {}

        # Reconnect and reuse the same triple. Without the reset this SYN would be swallowed by the
        # stale flow and the guest would wait for a SYN-ACK that never comes.
        _send_wlan_frame(master, _build_data_header_frame(_syn(7000)))
        _read_f2_response(master)
        await _wait_for_pending_packet(master)
        reply = net.parse_tcp(
            net.parse_ipv4(net.parse_ethernet(_parse_data_frame(_read_f2_response(master))).payload).payload
        )
        assert reply is not None
        assert reply.flags & net.TCP_SYN and reply.flags & net.TCP_ACK
        assert reply.ack == 7001  # acknowledges the *new* SYN, not the old flow's sequence space

        echo_server.close()
        await echo_server.wait_closed()

    _run(_body())


class _ImmediatelyResetReader:
    """Fake `asyncio.StreamReader` whose `read()` always raises `ConnectionResetError` - used to
    test `_pump_host_to_guest()`'s exception handling directly, decoupled from actually provoking
    a real OS-level RST. A prior version of this test tried forcing a real RST via `SO_LINGER`
    against a real hermetic server; that's documented to request a hard/abortive close on Windows
    too, but didn't reliably surface as `ConnectionResetError` through Python's asyncio there in
    practice (confirmed: flaked on `windows-latest` CI, observed a clean FIN instead) - testing the
    code path directly, not the OS's own socket-close semantics, is both more portable and more
    precisely targeted at what this test actually cares about."""

    async def read(self, n: int) -> bytes:
        raise ConnectionResetError


class _NoOpWriter:
    def __init__(self) -> None:
        self.closed = False

    def write(self, data: bytes) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def can_write_eof(self) -> bool:
        return True

    def write_eof(self) -> None:
        pass


async def _connect_with_immediate_reset(host: str, port: int) -> "tuple[_ImmediatelyResetReader, _NoOpWriter]":
    return _ImmediatelyResetReader(), _NoOpWriter()


def test_tcp_reflector_propagates_a_real_reset_as_rst_not_a_clean_fin():
    async def _body() -> None:
        simulator = Simulator()
        simulator.bind_loop()
        rp2040 = simulator.rp2040
        bus = GSPIBus()
        bus.attach_gpio(rp2040)
        bus.nat_bridge = NatBridge(rp2040, bus.queue_rx_ethernet_frame)
        bus.nat_bridge._tcp = TcpReflector(
            rp2040, bus.queue_rx_ethernet_frame, connect_fn=_connect_with_immediate_reset
        )
        master = _FakeGSPIMaster(rp2040)

        dest_ip = bytes([127, 0, 0, 1])
        guest_port = 54324
        guest_iss = 4000

        syn = net.pack_tcp(
            GUEST_IP, dest_ip, guest_port, 80, seq=guest_iss, ack=0, flags=net.TCP_SYN, window=8192, mss=1460
        )
        syn_ip = net.pack_ipv4(GUEST_IP, dest_ip, net.IP_PROTO_TCP, syn)
        syn_frame = net.pack_ethernet(GATEWAY_MAC, _GUEST_MAC, net.ETHERTYPE_IPV4, syn_ip)
        _send_wlan_frame(master, _build_data_header_frame(syn_frame))
        _read_f2_response(master)  # flow-control ack

        # The fake connect_fn "succeeds" immediately, so _open_and_pump() queues a SYN-ACK first
        # (as always), then _pump_host_to_guest() starts pumping and hits the reader's
        # ConnectionResetError on its very first read - no real handshake/data exchange needed to
        # provoke it here, unlike the real-socket version this replaced.
        await _wait_for_pending_packet(master)
        _read_f2_response(master)  # the SYN-ACK, queued before the reset is even noticed

        await _wait_for_pending_packet(master)
        rst_frame = _read_f2_response(master)
        rst_eth = net.parse_ethernet(_parse_data_frame(rst_frame))
        assert rst_eth is not None
        rst_ip = net.parse_ipv4(rst_eth.payload)
        assert rst_ip is not None
        rst_tcp = net.parse_tcp(rst_ip.payload)
        assert rst_tcp is not None
        assert rst_tcp.flags & net.TCP_RST
        assert not rst_tcp.flags & net.TCP_FIN  # must NOT look like a clean close
        assert bus.nat_bridge._tcp._flows == {}

    _run(_body())


async def _never_connects(host: str, port: int) -> "tuple[asyncio.StreamReader, asyncio.StreamWriter]":
    await asyncio.Event().wait()  # never resolves - simulates a black-holed SYN, no RST/refusal ever
    raise AssertionError("unreachable")


def test_tcp_reflector_times_out_a_connect_that_never_resolves():
    async def _body() -> None:
        simulator = Simulator()
        simulator.bind_loop()
        rp2040 = simulator.rp2040
        bus = GSPIBus()
        bus.attach_gpio(rp2040)
        bus.nat_bridge = NatBridge(rp2040, bus.queue_rx_ethernet_frame)
        bus.nat_bridge._tcp = TcpReflector(
            rp2040, bus.queue_rx_ethernet_frame, connect_timeout=0.3, connect_fn=_never_connects
        )
        master = _FakeGSPIMaster(rp2040)

        dest_ip = bytes([203, 0, 113, 1])  # TEST-NET-3 (RFC 5737) - never actually dialed, see connect_fn
        guest_port = 54325
        guest_iss = 5000

        syn = net.pack_tcp(
            GUEST_IP, dest_ip, guest_port, 80, seq=guest_iss, ack=0, flags=net.TCP_SYN, window=8192, mss=1460
        )
        syn_ip = net.pack_ipv4(GUEST_IP, dest_ip, net.IP_PROTO_TCP, syn)
        syn_frame = net.pack_ethernet(GATEWAY_MAC, _GUEST_MAC, net.ETHERTYPE_IPV4, syn_ip)
        _send_wlan_frame(master, _build_data_header_frame(syn_frame))
        _read_f2_response(master)  # flow-control ack

        await _wait_for_pending_packet(master, timeout=5.0)  # past connect_timeout=0.3

        rst_frame = _read_f2_response(master)
        rst_eth = net.parse_ethernet(_parse_data_frame(rst_frame))
        assert rst_eth is not None
        rst_tcp = net.parse_tcp(net.parse_ipv4(rst_eth.payload).payload)
        assert rst_tcp is not None
        assert rst_tcp.flags & net.TCP_RST
        assert bus.nat_bridge._tcp._flows == {}  # the flow-table entry didn't leak

    _run(_body())


# -- UDP relay (4e - DNS + general) --------------------------------------------------------------


class _CannedUdpServerProtocol(asyncio.DatagramProtocol):
    """Hermetic stand-in for a real upstream server (DNS resolver or otherwise) - replies to *any*
    datagram with a fixed canned response, so tests don't depend on real internet or real message
    parsing (neither does `UdpRelay` itself - it never inspects the bytes)."""

    def __init__(self, response: bytes) -> None:
        self._response = response
        self.transport: asyncio.DatagramTransport | None = None
        self.received: list[bytes] = []

    def connection_made(self, transport: "asyncio.DatagramTransport") -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: "tuple[str, int]") -> None:
        self.received.append(data)
        assert self.transport is not None
        self.transport.sendto(self._response, addr)


def _reserve_a_free_udp_port() -> int:
    """Binds then immediately closes a UDP socket - the port is free again but nothing is bound
    there, so a subsequent send typically gets a prompt ICMP port-unreachable on loopback."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_udp_relay_forwards_a_dns_query_and_relays_the_response_back():
    async def _body() -> None:
        simulator = Simulator()
        simulator.bind_loop()
        rp2040 = simulator.rp2040
        bus = GSPIBus()
        bus.attach_gpio(rp2040)
        bus.nat_bridge = NatBridge(rp2040, bus.queue_rx_ethernet_frame)
        master = _FakeGSPIMaster(rp2040)

        canned_response = b"not a real DNS message - UdpRelay never parses this"
        server_protocol = _CannedUdpServerProtocol(canned_response)
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(lambda: server_protocol, local_addr=("127.0.0.1", 0))
        server_port = transport.get_extra_info("sockname")[1]
        # Override the default real-internet DNS upstream (1.1.1.1:53) with the hermetic fake server.
        bus.nat_bridge._udp = UdpRelay(rp2040, bus.queue_rx_ethernet_frame, dns_upstream=("127.0.0.1", server_port))

        query = b"a fake DNS query - opaque bytes as far as UdpRelay is concerned"
        guest_port = 33333
        # Addressed to the gateway's own IP on port 53 - the DNS-specific addressing mode.
        udp_query = net.pack_udp(GUEST_IP, GATEWAY_IP, guest_port, 53, query)
        ip_query = net.pack_ipv4(GUEST_IP, GATEWAY_IP, net.IP_PROTO_UDP, udp_query)
        frame = net.pack_ethernet(GATEWAY_MAC, _GUEST_MAC, net.ETHERTYPE_IPV4, ip_query)

        _send_wlan_frame(master, _build_data_header_frame(frame))
        flow_control_ack = _read_f2_response(master)
        assert len(flow_control_ack) == SDPCM_HEADER_LEN

        await _wait_for_pending_packet(master)  # let the real (loopback) UDP round trip happen

        reply_frame = _read_f2_response(master)
        reply_eth = net.parse_ethernet(_parse_data_frame(reply_frame))
        assert reply_eth is not None
        reply_ip = net.parse_ipv4(reply_eth.payload)
        assert reply_ip is not None
        assert reply_ip.src_ip == GATEWAY_IP  # DNS replies always appear to come from the gateway
        assert reply_ip.dst_ip == GUEST_IP
        reply_udp = net.parse_udp(reply_ip.payload)
        assert reply_udp is not None
        assert (reply_udp.src_port, reply_udp.dst_port) == (53, guest_port)
        assert reply_udp.payload == canned_response
        assert server_protocol.received == [query]

        transport.close()

    _run(_body())


def test_udp_relay_forwards_general_udp_directly_to_its_own_real_destination():
    """4e's own generalization: a UDP packet NOT addressed to the gateway's DNS port (e.g. NTP,
    port 123, the way `ntptime` uses it) is relayed straight to whatever real destination the
    guest itself addressed - not to the fixed DNS upstream - and the reply appears to come from
    that same real destination, not from the gateway."""

    async def _body() -> None:
        simulator = Simulator()
        simulator.bind_loop()
        rp2040 = simulator.rp2040
        bus = GSPIBus()
        bus.attach_gpio(rp2040)
        bus.nat_bridge = NatBridge(rp2040, bus.queue_rx_ethernet_frame)
        master = _FakeGSPIMaster(rp2040)

        canned_response = b"\x00" * 48  # NTP-shaped placeholder - UdpRelay never inspects it
        server_protocol = _CannedUdpServerProtocol(canned_response)
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(lambda: server_protocol, local_addr=("127.0.0.1", 0))
        server_ip = bytes([127, 0, 0, 1])
        server_port = transport.get_extra_info("sockname")[1]

        query = b"a fake NTP request"
        guest_port = 44444
        udp_query = net.pack_udp(GUEST_IP, server_ip, guest_port, server_port, query)
        ip_query = net.pack_ipv4(GUEST_IP, server_ip, net.IP_PROTO_UDP, udp_query)
        frame = net.pack_ethernet(GATEWAY_MAC, _GUEST_MAC, net.ETHERTYPE_IPV4, ip_query)

        _send_wlan_frame(master, _build_data_header_frame(frame))
        _read_f2_response(master)  # flow-control ack

        await _wait_for_pending_packet(master)

        reply_frame = _read_f2_response(master)
        reply_eth = net.parse_ethernet(_parse_data_frame(reply_frame))
        assert reply_eth is not None
        reply_ip = net.parse_ipv4(reply_eth.payload)
        assert reply_ip is not None
        assert reply_ip.src_ip == server_ip  # NOT the gateway - the real destination itself
        assert reply_ip.dst_ip == GUEST_IP
        reply_udp = net.parse_udp(reply_ip.payload)
        assert reply_udp is not None
        assert (reply_udp.src_port, reply_udp.dst_port) == (server_port, guest_port)
        assert reply_udp.payload == canned_response
        assert server_protocol.received == [query]

        transport.close()

    _run(_body())


def test_udp_relay_stays_silent_on_no_response():
    async def _body() -> None:
        simulator = Simulator()
        simulator.bind_loop()
        rp2040 = simulator.rp2040
        bus = GSPIBus()
        bus.attach_gpio(rp2040)
        bus.nat_bridge = NatBridge(rp2040, bus.queue_rx_ethernet_frame)
        master = _FakeGSPIMaster(rp2040)
        dead_port = _reserve_a_free_udp_port()
        bus.nat_bridge._udp = UdpRelay(
            rp2040, bus.queue_rx_ethernet_frame, dns_upstream=("127.0.0.1", dead_port), timeout=1.0
        )

        udp_query = net.pack_udp(GUEST_IP, GATEWAY_IP, 33334, 53, b"query")
        ip_query = net.pack_ipv4(GUEST_IP, GATEWAY_IP, net.IP_PROTO_UDP, udp_query)
        frame = net.pack_ethernet(GATEWAY_MAC, _GUEST_MAC, net.ETHERTYPE_IPV4, ip_query)

        _send_wlan_frame(master, _build_data_header_frame(frame))
        _read_f2_response(master)  # flow-control ack

        await asyncio.sleep(2.5)  # comfortably past the relay's own 1s timeout on a slow CI runner too

        status = master.read_register(BUS_FUNCTION, SPI_STATUS_REGISTER, 4)
        assert not status & STATUS_F2_PKT_AVAILABLE  # no reply was ever queued

    _run(_body())


def test_dhcp_still_takes_priority_over_the_general_udp_relay():
    """DHCP (port 67) must be handled by `DhcpServer`, not fall through to `UdpRelay` - a DHCP
    packet is broadcast to 255.255.255.255, which isn't the gateway's own IP, so without this
    ordering it would incorrectly match `UdpRelay`'s general (non-DNS) addressing path instead -
    a real DHCPDISCOVER (not just a malformed payload DhcpServer would reject anyway) is used here
    so the assertion actually proves DhcpServer won, not merely that nothing crashed."""
    _rp2040, bus, master = _wire_up_with_nat_bridge()
    dhcp = net.pack_dhcp(net.DHCP_OP_REQUEST, 0x1234, _GUEST_MAC, bytes(4), {53: bytes([net.DHCP_MSG_DISCOVER])})
    udp = net.pack_udp(bytes(4), bytes([255, 255, 255, 255]), 68, 67, dhcp)
    ip_packet = net.pack_ipv4(bytes(4), bytes([255, 255, 255, 255]), net.IP_PROTO_UDP, udp)
    frame = net.pack_ethernet(bytes([0xFF] * 6), _GUEST_MAC, net.ETHERTYPE_IPV4, ip_packet)

    _send_wlan_frame(master, _build_data_header_frame(frame))
    _read_f2_response(master)  # flow-control ack

    reply_frame = _read_f2_response(master)
    reply_eth = net.parse_ethernet(_parse_data_frame(reply_frame))
    assert reply_eth is not None
    reply_ip = net.parse_ipv4(reply_eth.payload)
    assert reply_ip is not None
    reply_udp = net.parse_udp(reply_ip.payload)
    assert reply_udp is not None
    reply_dhcp = net.parse_dhcp(reply_udp.payload)
    assert reply_dhcp is not None
    assert reply_dhcp.options[53] == bytes([net.DHCP_MSG_OFFER])  # DhcpServer answered, not UdpRelay
    assert bus.nat_bridge._udp is not None  # sanity: the relay exists and simply wasn't the one used

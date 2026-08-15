"""CYW43 step 4 NAT bridge - `docs/records/0048-cyw43-nat-reflector.md`. `NatBridge` is what
`Cyw43439.attach()` wires onto `GSPIBus.nat_bridge`; `GSPIBus._write_wlan()` calls
`handle_outbound_ethernet_frame()` synchronously for every outbound `DATA_HEADER` (real Ethernet)
frame the guest sends, and gets replies back asynchronously via the `queue_ethernet_frame` callback
(`GSPIBus.queue_rx_ethernet_frame`) - never a synchronous return value, matching how 3g's own
scripted scan/join events are always a separate, later, independently-framed inbound packet rather
than tied to the triggering write.

Three responders, composed behind one entry point:

- `ArpResponder` - answers only an ARP request for the fixed gateway IP.
- `DhcpServer` - a single fixed lease (DISCOVER->OFFER, REQUEST->ACK), no address pool.
- `TcpReflector` - the actual NAT bridge. Real destination reachability (retransmission,
  congestion control) is entirely the real host-side `asyncio` socket's/OS's job; the guest-facing
  leg only needs handshake spoofing, seq/ack bookkeeping, guest-window flow control, and FIN/RST
  propagation - see the record's "Decision" section for why: `GSPIBus.queue_rx_packet()` is already
  an in-process, lossless, in-order FIFO, not a real lossy network.
- `DnsRelay` (step 4e) - a one-shot UDP relay to a fixed public resolver, opaque to DNS message
  content (no parsing needed - unlike TCP, UDP has no connection/sequence state to spoof at all,
  so this is just "forward the query bytes, forward the response bytes back," addressed to look
  like it came from the gateway).

Fixed addressing (hardcoded, matching this bus's existing fixed-fake-AP precedent - no config
surface added this pass, see the record's "Deferred" section).
"""

import asyncio
import enum
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from rp2040py.external.cyw43 import net

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = (
    "GATEWAY_IP",
    "GATEWAY_MAC",
    "GUEST_IP",
    "SUBNET_MASK",
    "ArpResponder",
    "DhcpServer",
    "DnsRelay",
    "NatBridge",
    "TcpFlow",
    "TcpFlowKey",
    "TcpReflector",
)

GUEST_IP = bytes([10, 0, 0, 2])
GATEWAY_IP = bytes([10, 0, 0, 1])  # also used as DNS-in-options and DHCP server-identifier
SUBNET_MASK = bytes([255, 255, 255, 0])
GATEWAY_MAC = bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x01])  # locally-administered bit set

_BROADCAST_IP = bytes([255, 255, 255, 255])
_BROADCAST_MAC = bytes([0xFF] * 6)
_LEASE_SECONDS = 86400
_DEFAULT_MSS = 536  # RFC 879 fallback when the guest's SYN carries no MSS option
_MAX_MSS = 1460
_INBOUND_WINDOW = 65535  # fixed, generous - comfortably above the guest's own default TCP_WND,
# so the guest's own advertised window is always the binding constraint, not ours.


def _ip_to_str(ip: bytes) -> str:
    return ".".join(str(b) for b in ip)


class ArpResponder:
    def __init__(self, *, gateway_ip: bytes = GATEWAY_IP, gateway_mac: bytes = GATEWAY_MAC) -> None:
        self._gateway_ip = gateway_ip
        self._gateway_mac = gateway_mac

    def maybe_handle(self, eth: "net.EthernetFrame") -> "bytes | None":
        """`None` unless `eth` is an ARP request for the gateway IP - the only case this bus's
        guest ever needs answered (real firmware ARPs the gateway for every off-subnet
        destination, never the far IP itself - see the record's own cited `etharp_output()`
        behavior). Returns a full inbound Ethernet frame, ready for `queue_ethernet_frame`."""
        if eth.ethertype != net.ETHERTYPE_ARP:
            return None
        arp = net.parse_arp(eth.payload)
        if arp is None or arp.op != net.ARP_OP_REQUEST or arp.target_ip != self._gateway_ip:
            return None
        reply = net.pack_arp(net.ARP_OP_REPLY, self._gateway_mac, self._gateway_ip, arp.sender_mac, arp.sender_ip)
        return net.pack_ethernet(arp.sender_mac, self._gateway_mac, net.ETHERTYPE_ARP, reply)


class DhcpServer:
    def __init__(
        self,
        *,
        guest_ip: bytes = GUEST_IP,
        gateway_ip: bytes = GATEWAY_IP,
        subnet_mask: bytes = SUBNET_MASK,
        gateway_mac: bytes = GATEWAY_MAC,
    ) -> None:
        self._guest_ip = guest_ip
        self._gateway_ip = gateway_ip
        self._subnet_mask = subnet_mask
        self._gateway_mac = gateway_mac

    def maybe_handle(self, eth: "net.EthernetFrame") -> "bytes | None":
        """`None` unless `eth` is a DHCPDISCOVER/DHCPREQUEST (option 53) addressed to UDP port 67
        - answered unconditionally with the one fixed lease (OFFER/ACK respectively). Always
        broadcast: the guest has no usable source IP yet at either point in the exchange."""
        if eth.ethertype != net.ETHERTYPE_IPV4:
            return None
        ip = net.parse_ipv4(eth.payload)
        if ip is None or ip.proto != net.IP_PROTO_UDP:
            return None
        udp = net.parse_udp(ip.payload)
        if udp is None or udp.dst_port != 67:
            return None
        request = net.parse_dhcp(udp.payload)
        if request is None or request.op != net.DHCP_OP_REQUEST:
            return None
        msg_type = request.options.get(53)
        if msg_type == bytes([net.DHCP_MSG_DISCOVER]):
            reply_type = net.DHCP_MSG_OFFER
        elif msg_type == bytes([net.DHCP_MSG_REQUEST]):
            reply_type = net.DHCP_MSG_ACK
        else:
            return None
        options = {
            53: bytes([reply_type]),
            1: self._subnet_mask,
            3: self._gateway_ip,
            6: self._gateway_ip,
            51: _LEASE_SECONDS.to_bytes(4, "big"),
            54: self._gateway_ip,
        }
        dhcp_reply = net.pack_dhcp(net.DHCP_OP_REPLY, request.xid, request.chaddr, self._guest_ip, options)
        udp_reply = net.pack_udp(self._gateway_ip, _BROADCAST_IP, 67, 68, dhcp_reply)
        ip_reply = net.pack_ipv4(self._gateway_ip, _BROADCAST_IP, net.IP_PROTO_UDP, udp_reply)
        return net.pack_ethernet(_BROADCAST_MAC, self._gateway_mac, net.ETHERTYPE_IPV4, ip_reply)


class _OneShotDatagramProtocol(asyncio.DatagramProtocol):
    """Captures exactly one inbound UDP datagram into `on_response` - `DnsRelay`'s own upstream
    query has no session/multiplexing needs (one real socket per guest query, closed right after),
    so a full-featured protocol class isn't needed."""

    def __init__(self, on_response: "asyncio.Future[bytes]") -> None:
        self._on_response = on_response

    def datagram_received(self, data: bytes, addr: "tuple[str, int]") -> None:
        if not self._on_response.done():
            self._on_response.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self._on_response.done():
            self._on_response.set_exception(exc)


class DnsRelay:
    """Step 4e - a one-shot UDP relay to a fixed public resolver. No DNS message parsing at all:
    the query/response bytes are opaque, forwarded verbatim - the same principle the TCP reflector
    uses for TLS/HTTP payload, just one datagram instead of a byte stream. No connection table
    either - UDP has no sequence/ack state to spoof, so each query gets its own short-lived real
    socket, independent of any other in-flight query."""

    def __init__(
        self,
        rp2040: "RP2040",
        queue_ethernet_frame: "Callable[[bytes], None]",
        *,
        gateway_ip: bytes = GATEWAY_IP,
        gateway_mac: bytes = GATEWAY_MAC,
        upstream: "tuple[str, int]" = ("1.1.1.1", 53),
        timeout: float = 5.0,
    ) -> None:
        self._rp2040 = rp2040
        self._queue_ethernet_frame = queue_ethernet_frame
        self._gateway_ip = gateway_ip
        self._gateway_mac = gateway_mac
        self._upstream = upstream
        self._timeout = timeout

    def maybe_handle(self, eth: "net.EthernetFrame") -> bool:
        """`True` if `eth` was a UDP packet addressed to port 53 (handled asynchronously,
        fire-and-forget - no synchronous reply, same shape as a new TCP SYN). `False` otherwise,
        so `NatBridge` can still try other UDP-addressed handlers."""
        if eth.ethertype != net.ETHERTYPE_IPV4:
            return False
        ip = net.parse_ipv4(eth.payload)
        if ip is None or ip.proto != net.IP_PROTO_UDP:
            return False
        udp = net.parse_udp(ip.payload)
        if udp is None or udp.dst_port != 53:
            return False
        self._rp2040.schedule_threadsafe(self._relay(eth.src_mac, ip.src_ip, udp.src_port, udp.payload))
        return True

    async def _relay(self, guest_mac: bytes, guest_ip: bytes, guest_port: int, query: bytes) -> None:
        loop = asyncio.get_running_loop()
        on_response: asyncio.Future[bytes] = loop.create_future()
        try:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _OneShotDatagramProtocol(on_response), remote_addr=self._upstream
            )
        except OSError:
            return
        try:
            transport.sendto(query)
            response = await asyncio.wait_for(on_response, timeout=self._timeout)
        except (OSError, TimeoutError):
            return
        finally:
            transport.close()
        udp_reply = net.pack_udp(self._gateway_ip, guest_ip, 53, guest_port, response)
        ip_reply = net.pack_ipv4(self._gateway_ip, guest_ip, net.IP_PROTO_UDP, udp_reply)
        frame = net.pack_ethernet(guest_mac, self._gateway_mac, net.ETHERTYPE_IPV4, ip_reply)
        self._queue_ethernet_frame(frame)


class _FlowState(enum.Enum):
    CONNECTING = enum.auto()  # SYN seen, real socket connect in flight
    SYN_RCVD = enum.auto()  # our SYN-ACK sent, waiting for the guest's final ACK
    ESTABLISHED = enum.auto()


class TcpFlowKey(NamedTuple):
    guest_src_port: int
    dst_ip: bytes
    dst_port: int


@dataclass
class TcpFlow:
    key: TcpFlowKey
    guest_mac: bytes
    guest_ip: bytes
    guest_iss: int
    guest_next_seq: int  # next seq expected from the guest == our own ack value
    guest_window: int
    mss: int
    host_iss: int = 0  # chosen once the real socket connects
    bytes_relayed_to_guest: int = 0  # host-stream offset -> guest-visible seq translation
    bytes_acked_by_guest: int = 0
    state: _FlowState = _FlowState.CONNECTING
    reader: "asyncio.StreamReader | None" = None
    writer: "asyncio.StreamWriter | None" = None
    pump_task: "asyncio.Task[None] | None" = None
    window_opened: "asyncio.Event" = field(default_factory=asyncio.Event)
    guest_fin_seen: bool = False
    host_fin_sent: bool = False


class TcpReflector:
    def __init__(self, rp2040: "RP2040", queue_ethernet_frame: "Callable[[bytes], None]") -> None:
        self._rp2040 = rp2040
        self._queue_ethernet_frame = queue_ethernet_frame
        self._flows: dict[TcpFlowKey, TcpFlow] = {}

    def maybe_handle(self, eth: "net.EthernetFrame", ip: "net.Ipv4Packet") -> bool:
        """`True` if this TCP segment was consumed (always, once `ip.proto` is TCP - this bridge
        only ever sees frames the guest addressed to the gateway MAC for an off-subnet
        destination, so anything reaching here is meant to be NATted). A new SYN starts a
        connection asynchronously and returns immediately; a segment for an unknown flow (already
        evicted, or spurious) is silently ignored, matching this bus's existing no-op stance."""
        if ip.proto != net.IP_PROTO_TCP:
            return False
        tcp = net.parse_tcp(ip.payload)
        if tcp is None:
            return True
        key = TcpFlowKey(tcp.src_port, ip.dst_ip, tcp.dst_port)
        flow = self._flows.get(key)
        if flow is None:
            if tcp.flags & net.TCP_SYN and not (tcp.flags & net.TCP_ACK):
                self._begin_new_connection(eth, ip, tcp, key)
            return True
        self._on_guest_segment(flow, tcp)
        return True

    def _begin_new_connection(
        self, eth: "net.EthernetFrame", ip: "net.Ipv4Packet", tcp: "net.TcpSegment", key: TcpFlowKey
    ) -> None:
        flow = TcpFlow(
            key=key,
            guest_mac=eth.src_mac,
            guest_ip=ip.src_ip,
            guest_iss=tcp.seq,
            guest_next_seq=(tcp.seq + 1) & 0xFFFFFFFF,
            guest_window=tcp.window,
            mss=min(tcp.mss or _DEFAULT_MSS, _MAX_MSS),
        )
        self._flows[key] = flow
        self._rp2040.schedule_threadsafe(self._open_and_pump(flow))

    async def _open_and_pump(self, flow: TcpFlow) -> None:
        """Runs as a real `asyncio.Task` on the engine-room loop itself (self-registered via
        `asyncio.current_task()` so a later, synchronous RST handler on the same loop can cancel
        it). On connect failure, synthesizes an immediate RST instead of letting the guest wait
        out its own SYN retransmit timeout for something already known to have failed."""
        flow.pump_task = asyncio.current_task()
        try:
            reader, writer = await asyncio.open_connection(host=_ip_to_str(flow.key.dst_ip), port=flow.key.dst_port)
        except OSError:
            self._queue_ethernet_frame(self._build_tcp_frame(flow, flags=net.TCP_RST | net.TCP_ACK, seq=0))
            self._flows.pop(flow.key, None)
            return
        flow.reader, flow.writer = reader, writer
        flow.host_iss = random.getrandbits(32)
        flow.state = _FlowState.SYN_RCVD
        self._queue_ethernet_frame(
            self._build_tcp_frame(flow, flags=net.TCP_SYN | net.TCP_ACK, seq=flow.host_iss, mss=flow.mss)
        )
        await self._pump_host_to_guest(flow)

    async def _pump_host_to_guest(self, flow: TcpFlow) -> None:
        assert flow.reader is not None
        try:
            while True:
                available = flow.guest_window - (flow.bytes_relayed_to_guest - flow.bytes_acked_by_guest)
                if available <= 0:
                    flow.window_opened.clear()
                    await flow.window_opened.wait()
                    continue
                chunk = await flow.reader.read(min(flow.mss, available))
                if not chunk:
                    break
                self._queue_ethernet_frame(self._build_tcp_frame(flow, flags=net.TCP_ACK | net.TCP_PSH, payload=chunk))
                flow.bytes_relayed_to_guest += len(chunk)
        except OSError:
            pass
        flow.host_fin_sent = True
        self._queue_ethernet_frame(self._build_tcp_frame(flow, flags=net.TCP_FIN | net.TCP_ACK))
        self._maybe_evict(flow)

    def _on_guest_segment(self, flow: TcpFlow, tcp: "net.TcpSegment") -> None:
        if tcp.flags & net.TCP_RST:
            if flow.pump_task is not None:
                flow.pump_task.cancel()
            if flow.writer is not None:
                flow.writer.close()
            self._flows.pop(flow.key, None)
            return

        if flow.state != _FlowState.CONNECTING:
            acked_through = (tcp.ack - flow.host_iss - 1) & 0xFFFFFFFF
            if acked_through <= flow.bytes_relayed_to_guest:
                flow.bytes_acked_by_guest = acked_through
            flow.guest_window = tcp.window
            if flow.guest_window - (flow.bytes_relayed_to_guest - flow.bytes_acked_by_guest) > 0:
                flow.window_opened.set()
            if flow.state == _FlowState.SYN_RCVD and tcp.flags & net.TCP_ACK:
                flow.state = _FlowState.ESTABLISHED

        if tcp.payload and tcp.seq == flow.guest_next_seq and flow.writer is not None:
            flow.writer.write(tcp.payload)
            flow.guest_next_seq = (flow.guest_next_seq + len(tcp.payload)) & 0xFFFFFFFF

        if tcp.flags & net.TCP_FIN:
            flow.guest_next_seq = (flow.guest_next_seq + 1) & 0xFFFFFFFF
            flow.guest_fin_seen = True
            # Half-close the real socket too - otherwise the real destination never learns the
            # guest is done sending, even though we've stopped forwarding its bytes.
            if flow.writer is not None and flow.writer.can_write_eof():
                flow.writer.write_eof()

        # Always ACK immediately on real content - no retransmit timer is needed to guarantee this
        # eventually gets seen, since the guest-facing FIFO (queue_rx_packet()) is lossless/in-order.
        if tcp.payload or (tcp.flags & net.TCP_FIN):
            self._queue_ethernet_frame(self._build_tcp_frame(flow, flags=net.TCP_ACK))

        self._maybe_evict(flow)

    def _maybe_evict(self, flow: TcpFlow) -> None:
        if flow.guest_fin_seen and flow.host_fin_sent:
            if flow.writer is not None:
                flow.writer.close()
            self._flows.pop(flow.key, None)

    def _build_tcp_frame(
        self,
        flow: TcpFlow,
        *,
        flags: int,
        seq: "int | None" = None,
        ack: "int | None" = None,
        payload: bytes = b"",
        mss: "int | None" = None,
    ) -> bytes:
        seq_value = (flow.host_iss + 1 + flow.bytes_relayed_to_guest) & 0xFFFFFFFF if seq is None else seq
        ack_value = flow.guest_next_seq if ack is None else ack
        tcp_segment = net.pack_tcp(
            flow.key.dst_ip,
            flow.guest_ip,
            flow.key.dst_port,
            flow.key.guest_src_port,
            seq=seq_value,
            ack=ack_value,
            flags=flags,
            window=_INBOUND_WINDOW,
            payload=payload,
            mss=mss,
        )
        ip_packet = net.pack_ipv4(flow.key.dst_ip, flow.guest_ip, net.IP_PROTO_TCP, tcp_segment)
        return net.pack_ethernet(flow.guest_mac, GATEWAY_MAC, net.ETHERTYPE_IPV4, ip_packet)


class NatBridge:
    """Top-level orchestrator `Cyw43439.attach()` constructs and wires onto `GSPIBus.nat_bridge`."""

    def __init__(self, rp2040: "RP2040", queue_ethernet_frame: "Callable[[bytes], None]") -> None:
        self._arp = ArpResponder()
        self._dhcp = DhcpServer()
        self._dns = DnsRelay(rp2040, queue_ethernet_frame)
        self._tcp = TcpReflector(rp2040, queue_ethernet_frame)
        self._queue_ethernet_frame = queue_ethernet_frame

    def handle_outbound_ethernet_frame(self, frame: bytes) -> None:
        """Called synchronously from `GSPIBus._write_wlan()` - must never block. Tries
        ARP -> DHCP -> DNS -> TCP in order (mutually exclusive by EtherType/protocol/port); any
        other UDP traffic is silently ignored, matching this bus's existing no-op-rather-than-raise
        stance."""
        eth = net.parse_ethernet(frame)
        if eth is None:
            return
        if eth.ethertype == net.ETHERTYPE_ARP:
            reply = self._arp.maybe_handle(eth)
            if reply is not None:
                self._queue_ethernet_frame(reply)
            return
        if eth.ethertype != net.ETHERTYPE_IPV4:
            return
        ip = net.parse_ipv4(eth.payload)
        if ip is None:
            return
        if ip.proto == net.IP_PROTO_UDP:
            reply = self._dhcp.maybe_handle(eth)
            if reply is not None:
                self._queue_ethernet_frame(reply)
                return
            self._dns.maybe_handle(eth)
            return
        if ip.proto == net.IP_PROTO_TCP:
            self._tcp.maybe_handle(eth, ip)

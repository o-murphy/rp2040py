# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 USB controller: a Python-facing shell over the C++ `UsbBlock` of `core/usb.hpp` (docs/tasks/usb-port.md).
`peripherals/_usb.py` is the pure-Python reference, kept as the oracle (tests/test_usb_diff.py); `peripherals/usb.py` is the facade that picks between them.

What a firmware sees is the C++ block: when the chip adopts this object (`RP2040.peripherals[0x50110] = usb`) it registers the block's own C++ read/write functions in the bus's
window table (`_native_window`), so a register access never enters Python. The DPRAM is the bus's memory region; the block is handed a pointer to it. What stays Python is the other
end of the wire, reached through trampolines whose failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in: the interrupt line, the host shortcut's
callbacks (`on_endpoint_write` / `on_endpoint_read` / `on_usb_enabled` / `on_reset_received`), the Python `USBDevice` of host mode (setup / IN / OUT), the logger - and the *data* of
the transfers in flight: the bytes an IN buffer carried and the bytes an OUT buffer waits for sit in per-endpoint queues here, while the alarms that deliver them (and everything after
the delivery) are the block's, on the chip's C++ clock (it needs the native `SimulationClock`, like the UART's).

The API is the reference's, private attributes included (the registers, `_buff_done`, `_buf_select`, ... that the oracle and the tests read; read-only views for the lists).
"""

from libc.stdint cimport int64_t, uint8_t, uint32_t
from libcpp cimport bool as cppbool

from cpython.bytes cimport PyBytes_AsString, PyBytes_FromStringAndSize

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.usb.usb_device import StandardRequest, parse_setup_packet

__all__ = ("RPUSBController",)

cdef enum:
    ENDPOINTS = 16


cdef cppbool _enabled_trampoline(void* ctx) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    try:
        callback = usb.on_usb_enabled
        if callback:
            callback()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _reset_received_trampoline(void* ctx) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    try:
        callback = usb.on_reset_received
        if callback:
            callback()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _endpoint_read_trampoline(void* ctx, uint32_t endpoint, uint32_t length) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    try:
        callback = usb.on_endpoint_read
        if callback:
            callback(endpoint, length)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _write_queued_trampoline(void* ctx, uint32_t endpoint, uint32_t half, const uint8_t* data, uint32_t length) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    try:
        usb._write_queue[endpoint].append((PyBytes_FromStringAndSize(<const char*> data, length), half))
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _drain_write_trampoline(void* ctx, uint32_t endpoint) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    try:
        queued = usb._write_queue[endpoint]
        usb._write_queue[endpoint] = []
        for buffer, half in queued:  # every buffer that arrived before the alarm ran
            if not usb._block.finish_write(endpoint, half):
                return False  # (the failure is parked; the rest of the batch is lost, as the reference's exception loses it)
            usb._reaction_queue[endpoint].append(buffer)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _drain_reaction_trampoline(void* ctx, uint32_t endpoint) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    try:
        buffers = usb._reaction_queue[endpoint]
        usb._reaction_queue[endpoint] = []
        callback = usb.on_endpoint_write
        for buffer in buffers:  # the host sees every buffer that was acknowledged before the alarm ran
            if callback:
                callback(endpoint, buffer)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _drain_read_trampoline(void* ctx, uint32_t endpoint) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    cdef bytes buffer
    try:
        queued = usb._read_queue[endpoint]
        usb._read_queue[endpoint] = []
        for item in queued:  # every answer that arrived before the alarm ran
            buffer = bytes(item)
            if not usb._block.finish_read(endpoint, <const uint8_t*> PyBytes_AsString(buffer), len(buffer)):
                return False
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _device_reset_trampoline(void* ctx) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    try:
        usb._device.on_reset()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _device_address_trampoline(void* ctx, uint32_t* address) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    try:
        address[0] = <uint32_t> (<int64_t> usb._device.address)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _device_setup_trampoline(void* ctx, const uint8_t* setup, cppbool* has_response) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    try:
        packet = PyBytes_FromStringAndSize(<const char*> setup, 8)
        parsed = parse_setup_packet(packet)
        device = usb._device
        result = device.handle_setup_packet(packet)
        if parsed.b_request == StandardRequest.SET_ADDRESS and parsed.type == 0:  # SET_ADDRESS is handled specially
            new_addr = parsed.w_value & 0x7F
            device.address = new_addr
            on_address_assigned = getattr(device, "on_address_assigned", None)
            if on_address_assigned:
                on_address_assigned(new_addr)
        if parsed.direction == "in" and result.data:  # kept for the IN transaction that follows
            usb._pending_response = result.data
            has_response[0] = True
        else:
            usb._pending_response = None
            has_response[0] = False
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef uint32_t _status_code(status):
    if status == "ack":
        return kUsbAck
    if status == "nak":
        return kUsbNak
    if status == "stall":
        return kUsbStall
    return kUsbOther


cdef cppbool _pending_trampoline(void* ctx, uint32_t max_length, cppbool* present, const uint8_t** data, uint32_t* length) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    try:
        response = usb._pending_response
        if response:
            present[0] = True
            if len(response) > max_length:
                response = response[:max_length]
            usb._pending_response = None  # (whatever was cut off is forgotten with it, as in the reference)
            usb._scratch = bytes(response)
            data[0] = <const uint8_t*> PyBytes_AsString(usb._scratch)
            length[0] = len(usb._scratch)
        else:
            present[0] = False
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _device_in_trampoline(void* ctx, uint32_t endpoint_address, uint32_t* status, const uint8_t** data, uint32_t* length) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    try:
        result = usb._device.handle_data_in(endpoint_address)
        status[0] = _status_code(result.status)
        usb._scratch = bytes(result.data or b"")
        data[0] = <const uint8_t*> PyBytes_AsString(usb._scratch)
        length[0] = len(usb._scratch)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _device_out_trampoline(void* ctx, uint32_t endpoint, const uint8_t* data, uint32_t length, uint32_t* status) noexcept:
    cdef RPUSBController usb = <RPUSBController> (<object> ctx)._usb_wire_view
    try:
        result = usb._device.handle_data_out(endpoint, PyBytes_FromStringAndSize(<const char*> data, length))
        status[0] = _status_code(result.status)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef void usb_wire_fill(UsbHost* wire, void* chip) noexcept:
    """The host's end of the USB wire, for `McuHost.usb_wire`: the callbacks into the Python device and endpoint machinery of `chip`'s USB view (`chip._usb_wire_view`)."""
    wire.usb_enabled = _enabled_trampoline
    wire.reset_received = _reset_received_trampoline
    wire.endpoint_read = _endpoint_read_trampoline
    wire.write_queued = _write_queued_trampoline
    wire.drain_write = _drain_write_trampoline
    wire.drain_reaction = _drain_reaction_trampoline
    wire.drain_read = _drain_read_trampoline
    wire.device_reset = _device_reset_trampoline
    wire.device_address = _device_address_trampoline
    wire.device_setup = _device_setup_trampoline
    wire.pending_response = _pending_trampoline
    wire.device_in = _device_in_trampoline
    wire.device_out = _device_out_trampoline
    wire.ctx = chip


cdef class RPUSBController(NativeBlock):
    def __init__(self, rp2040, name):
        native_usb = getattr(rp2040, "_native_usb_address", None)
        if native_usb is None:
            raise TypeError(
                f"the native USB controller is a view of a block of the native chip's C++ core; {type(rp2040).__name__} is not that "
                "(use the pure-Python peripherals/_usb.RPUSBController with it)"
            )
        self.clock = rp2040.clock
        self.rp2040 = rp2040
        self.name = name
        self._device = None
        self._pending_response = None
        self._scratch = b""
        self._read_queue = [[] for _ in range(ENDPOINTS)]
        self._write_queue = [[] for _ in range(ENDPOINTS)]
        self._reaction_queue = [[] for _ in range(ENDPOINTS)]
        self.on_usb_enabled = None
        self.on_reset_received = None
        self.on_reset = None
        self.on_endpoint_write = None
        self.on_endpoint_read = None
        # The DPRAM is the chip's own 4 kB bytearray (a real memory region of the bus); this view keeps it from being resized under the block's pointer.
        self._dpram_view = rp2040.usb_dpram
        # The chip's `Mcu` builds the block (its window, its line, its DPRAM pointer, its alarms on the chip's clock and the host's end of the wire, `McuHost.usb_wire`); this object is the Python view of it.
        self._from_mcu = True
        self._block = <UsbBlock*> <size_t> native_usb()
        self._ops = register_ops(self._block)
        rp2040._usb_wire_view = self  # (the wire's callbacks find this view through the chip; the chip keeps it alive)

    # --- the reference's attributes --------------------------------------------------------------

    @property
    def read_delay_microseconds(self):
        return self._block.read_delay_microseconds

    @read_delay_microseconds.setter
    def read_delay_microseconds(self, value):
        self._block.read_delay_microseconds = <int64_t> value

    @property
    def write_delay_microseconds(self):
        return self._block.write_delay_microseconds

    @write_delay_microseconds.setter
    def write_delay_microseconds(self, value):
        self._block.write_delay_microseconds = <int64_t> value

    @property
    def host_transaction_delay_microseconds(self):
        return self._block.host_transaction_delay_microseconds

    @host_transaction_delay_microseconds.setter
    def host_transaction_delay_microseconds(self, value):
        self._block.host_transaction_delay_microseconds = <int64_t> value

    @property
    def host_reaction_microseconds(self):
        return self._block.host_reaction_microseconds

    @host_reaction_microseconds.setter
    def host_reaction_microseconds(self, value):
        self._block.host_reaction_microseconds = <int64_t> value

    @property
    def connected_device(self):
        return self._device

    @connected_device.setter
    def connected_device(self, device):
        self._device = device
        self._block.has_device = device is not None

    def snapshot(self):
        """The controller's state in one call: the interrupt words, the SIE, the mode, the buffer bookkeeping and the host-mode control transfer in progress."""
        cdef UsbBlock* b = self._block
        cdef uint32_t i, j
        cdef uint32_t slots = 2 * ENDPOINTS
        return {
            "int_raw": b.int_raw,
            "int_enable": b.int_enable,
            "int_force": b.int_force,
            "int_status": b.int_status(),
            "sie_status": b.sie_status,
            "sie_ctrl": b.sie_ctrl,
            "connected": bool(b.connected),
            "connect_event": bool(b.connect_event),
            "host_connect_event": bool(b.host_connect_event),
            "host_mode": bool(b.host_mode),
            "sof_enabled": bool(b.sof_enabled),
            "device_attached": self._device is not None,
            "sof_frame_number": b.sof_frame_number,
            "ep_status_stall_nak": b.ep_status_stall_nak,
            "buff_done": tuple([tuple([b.done_at(i, j) for j in range(b.done_count(i))]) for i in range(slots)]),
            "buf_select": tuple([b.buf_select[i] for i in range(slots)]),
            "two_buffer_count": tuple([b.two_buffer_count[i] for i in range(slots)]),
            "pending_reads": tuple([tuple([b.pending_at(i, j) for j in range(b.pending_count(i))]) for i in range(ENDPOINTS)]),
            "expecting_status_phase": bool(b.expecting_status_phase),
            "control_data_pid": b.control_data_pid,
            "pending_setup_response": bytes(self._pending_response or b""),
            "device_address": None if self._device is None else self._device.address,
        }

    def raise_sie_status(self, mask):
        """Raise SIE_STATUS bits no modelled event sets (see the reference)."""
        self._block.sie_status |= <uint32_t> (<int64_t> mask)
        self._sie_status_updated()

    def raise_stall_nak(self, mask):
        """Raise EP_STATUS_STALL_NAK bits, which only a handshake from the host sets."""
        self._block.ep_status_stall_nak |= <uint32_t> (<int64_t> mask)
        self._stall_nak_updated()

    def raise_interrupt(self, mask):
        """Raise INTR bits for which the model has no event."""
        self._block.int_raw |= <uint32_t> (<int64_t> mask)
        if not self._block.check_interrupts():
            raise_if_pending()

    # --- internals the oracle and the tests drive -------------------------------------------------

    def _sie_status_updated(self):
        if not self._block.sie_status_updated():
            raise_if_pending()

    def _stall_nak_updated(self):
        if not self._block.stall_nak_updated():
            raise_if_pending()

    def _abort_done_updated(self):
        if not self._block.abort_done_updated():
            raise_if_pending()

    def _buff_status_updated(self):
        if not self._block.buff_status_updated():
            raise_if_pending()

    def _set_connected(self, connected):
        if not self._block.set_connected(bool(connected)):
            raise_if_pending()

    # --- BasePeripheral's surface ----------------------------------------------------------------

    def dpram_updated(self, offset, value):
        if not self._block.dpram_updated(<uint32_t> offset):
            raise_if_pending()

    def _wire_reset(self):
        """The host's end of the wire back to nothing: the connected device and every transfer queued for it (the chip's `Mcu` calls it just before it resets the block)."""
        self._device = None
        self._pending_response = None
        for queue in self._read_queue:
            del queue[:]
        for queue in self._write_queue:
            del queue[:]
        for queue in self._reaction_queue:
            del queue[:]

    def reset(self):
        """Registers and the transaction state back to power-on. Callbacks, the delay tuning and the alarms' wiring are not touched."""
        self._wire_reset()
        if not self._block.reset():
            raise_if_pending()

    def reset_device(self):
        self._block.reset_device()

    def send_setup_packet(self, setup_packet):
        cdef bytes packet = bytes(setup_packet)
        if not self._block.send_setup_packet(<const uint8_t*> PyBytes_AsString(packet), len(packet)):
            raise_if_pending()

    def endpoint_read_done(self, endpoint, buffer, delay=None):
        if delay is None:
            delay = self._block.read_delay_microseconds
        self._read_queue[endpoint].append(buffer)
        self._block.schedule_read(<uint32_t> endpoint, <double> delay)

    def connect_device(self, device):
        """Connect a simulated USB device to the host controller."""
        self._device = device
        self._block.has_device = device is not None
        if not self._block.connect_device():
            raise_if_pending()

    def disconnect_device(self):
        """Disconnect the simulated USB device from the host controller."""
        self._device = None
        if not self._block.disconnect_device():
            raise_if_pending()

    # --- the native bus protocol -----------------------------------------------------------------

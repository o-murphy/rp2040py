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

from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.usb.usb_device import StandardRequest, parse_setup_packet

__all__ = ("RPUSBController",)

cdef enum:
    ENDPOINTS = 16


cdef cppbool _enabled_trampoline(void* ctx) noexcept:
    cdef RPUSBController usb = <RPUSBController> ctx
    try:
        callback = usb.on_usb_enabled
        if callback:
            callback()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _reset_received_trampoline(void* ctx) noexcept:
    cdef RPUSBController usb = <RPUSBController> ctx
    try:
        callback = usb.on_reset_received
        if callback:
            callback()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _endpoint_read_trampoline(void* ctx, uint32_t endpoint, uint32_t length) noexcept:
    cdef RPUSBController usb = <RPUSBController> ctx
    try:
        callback = usb.on_endpoint_read
        if callback:
            callback(endpoint, length)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _write_queued_trampoline(void* ctx, uint32_t endpoint, uint32_t half, const uint8_t* data, uint32_t length) noexcept:
    cdef RPUSBController usb = <RPUSBController> ctx
    try:
        usb._write_queue[endpoint].append((PyBytes_FromStringAndSize(<const char*> data, length), half))
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _drain_write_trampoline(void* ctx, uint32_t endpoint) noexcept:
    cdef RPUSBController usb = <RPUSBController> ctx
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
    cdef RPUSBController usb = <RPUSBController> ctx
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
    cdef RPUSBController usb = <RPUSBController> ctx
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
    cdef RPUSBController usb = <RPUSBController> ctx
    try:
        usb._device.on_reset()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _device_address_trampoline(void* ctx, uint32_t* address) noexcept:
    cdef RPUSBController usb = <RPUSBController> ctx
    try:
        address[0] = <uint32_t> (<int64_t> usb._device.address)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _device_setup_trampoline(void* ctx, const uint8_t* setup, cppbool* has_response) noexcept:
    cdef RPUSBController usb = <RPUSBController> ctx
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
    cdef RPUSBController usb = <RPUSBController> ctx
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
    cdef RPUSBController usb = <RPUSBController> ctx
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
    cdef RPUSBController usb = <RPUSBController> ctx
    try:
        result = usb._device.handle_data_out(endpoint, PyBytes_FromStringAndSize(<const char*> data, length))
        status[0] = _status_code(result.status)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef class _HalfList:
    """One of the reference's Python lists of buffer halves (a `_buff_done[slot]` or a `_pending_reads[endpoint]`), as a live view of the block's bounded array."""

    cdef RPUSBController _usb
    cdef uint32_t _index
    cdef bint _pending

    def __len__(self):
        return self._usb._block.pending_count(self._index) if self._pending else self._usb._block.done_count(self._index)

    def __getitem__(self, position):
        if position < 0:
            position += len(self)
        if position < 0 or position >= len(self):
            raise IndexError("list index out of range")
        if self._pending:
            return self._usb._block.pending_at(self._index, position)
        return self._usb._block.done_at(self._index, position)

    def __iter__(self):
        return iter([self[i] for i in range(len(self))])

    def __eq__(self, other):
        return list(self) == list(other)

    def __repr__(self):
        return repr(list(self))

    def append(self, half):
        if self._pending:
            raise AttributeError("the OUT requests in flight are the block's own")
        self._usb._block.done_push(self._index, <uint32_t> half)


cdef class _HalfLists:
    """`_buff_done` (32 slots) or `_pending_reads` (16 endpoints): a sequence of `_HalfList` views."""

    cdef RPUSBController _usb
    cdef bint _pending

    def __len__(self):
        return ENDPOINTS if self._pending else 2 * ENDPOINTS

    def __getitem__(self, index):
        cdef _HalfList view
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError("list index out of range")
        view = _HalfList.__new__(_HalfList)
        view._usb = self._usb
        view._index = index
        view._pending = self._pending
        return view

    def __iter__(self):
        return iter([self[i] for i in range(len(self))])


cdef class _Words:
    """`_buf_select` / `_two_buffer_count`: 32 small integers, readable and writable in place."""

    cdef RPUSBController _usb
    cdef bint _counts

    def __len__(self):
        return 2 * ENDPOINTS

    def __getitem__(self, index):
        if index < 0:
            index += 2 * ENDPOINTS
        if index < 0 or index >= 2 * ENDPOINTS:
            raise IndexError("list index out of range")
        return self._usb._block.two_buffer_count[index] if self._counts else self._usb._block.buf_select[index]

    def __setitem__(self, index, value):
        if index < 0:
            index += 2 * ENDPOINTS
        if index < 0 or index >= 2 * ENDPOINTS:
            raise IndexError("list assignment index out of range")
        if self._counts:
            self._usb._block.two_buffer_count[index] = <uint32_t> (<int64_t> value)
        else:
            self._usb._block.buf_select[index] = <uint32_t> (<int64_t> value)

    def __iter__(self):
        return iter([self[i] for i in range(2 * ENDPOINTS)])

    def __eq__(self, other):
        return list(self) == list(other)

    def __repr__(self):
        return repr(list(self))


cdef class RPUSBController:
    def __init__(self, rp2040, name):
        cdef UsbHost host
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
        # The chip's `Mcu` builds the block (its window, its line, its DPRAM pointer and its alarms on the chip's clock); this object is the other end of the wire: the callbacks below are the host's.
        self._from_mcu = True
        self._block = <UsbBlock*> <size_t> native_usb()
        host.usb_enabled = _enabled_trampoline
        host.reset_received = _reset_received_trampoline
        host.endpoint_read = _endpoint_read_trampoline
        host.write_queued = _write_queued_trampoline
        host.drain_write = _drain_write_trampoline
        host.drain_reaction = _drain_reaction_trampoline
        host.drain_read = _drain_read_trampoline
        host.device_reset = _device_reset_trampoline
        host.device_address = _device_address_trampoline
        host.device_setup = _device_setup_trampoline
        host.pending_response = _pending_trampoline
        host.device_in = _device_in_trampoline
        host.device_out = _device_out_trampoline
        host.ctx = <void*> self
        self._block.set_wire(host)
        rp2040._usb_wire_view = self  # (the block keeps a raw pointer to this object: the chip keeps the object alive)

    def _warn_kind(self, kind, offset, value):
        """A register access the block does not implement, as the logger's message (the chip's host calls this for the block it built)."""
        if kind == kUsbWarnRead:
            self.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kUsbWarnReadAtomicArea:
            self.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            self.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")

    # --- the reference's attributes --------------------------------------------------------------

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

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

    @property
    def _pending_setup_response(self):
        return self._pending_response

    @_pending_setup_response.setter
    def _pending_setup_response(self, value):
        self._pending_response = value

    @property
    def _addr_endp(self):
        return self._block.addr_endp

    @_addr_endp.setter
    def _addr_endp(self, value):
        self._block.addr_endp = <uint32_t> (<int64_t> value)

    @property
    def _dev_addr_ctrl(self):
        return self._block.addr_endp

    @_dev_addr_ctrl.setter
    def _dev_addr_ctrl(self, value):
        self._block.addr_endp = <uint32_t> (<int64_t> value) & 0x000F007F

    @property
    def _main_ctrl(self):
        return self._block.main_ctrl

    @_main_ctrl.setter
    def _main_ctrl(self, value):
        self._block.main_ctrl = <uint32_t> (<int64_t> value)

    @property
    def _int_raw(self):
        return self._block.int_raw

    @_int_raw.setter
    def _int_raw(self, value):
        self._block.int_raw = <uint32_t> (<int64_t> value)

    @property
    def _int_enable(self):
        return self._block.int_enable

    @_int_enable.setter
    def _int_enable(self, value):
        self._block.int_enable = <uint32_t> (<int64_t> value)

    @property
    def _int_force(self):
        return self._block.int_force

    @_int_force.setter
    def _int_force(self, value):
        self._block.int_force = <uint32_t> (<int64_t> value)

    @property
    def int_status(self):
        return self._block.int_status()

    @property
    def _sie_status(self):
        return self._block.sie_status

    @_sie_status.setter
    def _sie_status(self, value):
        self._block.sie_status = <uint32_t> (<int64_t> value)

    @property
    def _sie_ctrl(self):
        return self._block.sie_ctrl

    @_sie_ctrl.setter
    def _sie_ctrl(self, value):
        self._block.sie_ctrl = <uint32_t> (<int64_t> value)

    @property
    def _connected(self):
        return self._block.connected

    @_connected.setter
    def _connected(self, value):
        self._block.connected = bool(value)

    @property
    def _connect_event(self):
        return self._block.connect_event

    @_connect_event.setter
    def _connect_event(self, value):
        self._block.connect_event = bool(value)

    @property
    def _host_connect_event(self):
        return self._block.host_connect_event

    @_host_connect_event.setter
    def _host_connect_event(self, value):
        self._block.host_connect_event = bool(value)

    @property
    def _sof_frame_number(self):
        return self._block.sof_frame_number

    @_sof_frame_number.setter
    def _sof_frame_number(self, value):
        self._block.sof_frame_number = <uint32_t> (<int64_t> value)

    @property
    def _int_ep_addr_ctrl(self):
        return [self._block.int_ep_addr_ctrl[i] for i in range(15)]

    @property
    def _int_ep_ctrl(self):
        return self._block.int_ep_ctrl

    @_int_ep_ctrl.setter
    def _int_ep_ctrl(self, value):
        self._block.int_ep_ctrl = <uint32_t> (<int64_t> value)

    @property
    def _usb_pwr(self):
        return self._block.usb_pwr

    @property
    def _usb_muxing(self):
        return self._block.usb_muxing

    @property
    def _usbphy_direct(self):
        return self._block.usbphy_direct

    @property
    def _usbphy_direct_override(self):
        return self._block.usbphy_direct_override

    @property
    def _usbphy_trim(self):
        return self._block.usbphy_trim

    @property
    def _nak_poll(self):
        return self._block.nak_poll

    @property
    def _ep_abort(self):
        return self._block.ep_abort

    @property
    def _ep_abort_done(self):
        return self._block.ep_abort_done

    @_ep_abort_done.setter
    def _ep_abort_done(self, value):
        self._block.ep_abort_done = <uint32_t> (<int64_t> value)

    @property
    def _ep_stall_arm(self):
        return self._block.ep_stall_arm

    @property
    def _ep_status_stall_nak(self):
        return self._block.ep_status_stall_nak

    @_ep_status_stall_nak.setter
    def _ep_status_stall_nak(self, value):
        self._block.ep_status_stall_nak = <uint32_t> (<int64_t> value)

    @property
    def _host_mode(self):
        return self._block.host_mode

    @_host_mode.setter
    def _host_mode(self, value):
        self._block.host_mode = bool(value)

    @property
    def _sof_enabled(self):
        return self._block.sof_enabled

    @property
    def _expecting_status_phase(self):
        return self._block.expecting_status_phase

    @_expecting_status_phase.setter
    def _expecting_status_phase(self, value):
        self._block.expecting_status_phase = bool(value)

    @property
    def _control_data_pid(self):
        return self._block.control_data_pid

    @_control_data_pid.setter
    def _control_data_pid(self, value):
        self._block.control_data_pid = <uint32_t> (<int64_t> value)

    @property
    def _buff_done(self):
        cdef _HalfLists view = _HalfLists.__new__(_HalfLists)
        view._usb = self
        view._pending = False
        return view

    @property
    def _buff_status(self):
        return self._block.buff_status()

    @property
    def _buf_select(self):
        cdef _Words view = _Words.__new__(_Words)
        view._usb = self
        view._counts = False
        return view

    @property
    def _two_buffer_count(self):
        cdef _Words view = _Words.__new__(_Words)
        view._usb = self
        view._counts = True
        return view

    @property
    def _pending_reads(self):
        cdef _HalfLists view = _HalfLists.__new__(_HalfLists)
        view._usb = self
        view._pending = True
        return view

    # --- internals the oracle and the tests drive -------------------------------------------------

    def check_interrupts(self):
        if not self._block.check_interrupts():
            raise_if_pending()

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

    def read_uint32(self, offset):
        cdef uint32_t value = self._block.read(<uint32_t> offset)
        raise_if_pending()
        return value

    def write_uint32(self, offset, value):
        # A direct write keeps whatever raw_write_value the last atomic write left, as the pure-Python block does.
        if not self._block.write(<uint32_t> offset, <int64_t> value):
            raise_if_pending()

    def write_uint32_atomic(self, offset, value, atomic_type):
        if not self._block.write_atomic(<uint32_t> offset, <int64_t> value, <uint32_t> atomic_type):
            raise_if_pending()
        raise_if_pending()

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

    def debug(self, msg):
        self.rp2040.logger.debug(self.name, msg)

    def info(self, msg):
        self.rp2040.logger.info(self.name, msg)

    def warn(self, msg):
        self.rp2040.logger.warning(self.name, msg)

    def error(self, msg):
        self.rp2040.logger.error(self.name, msg)

    # --- the native bus protocol -----------------------------------------------------------------

    def _native_window(self):
        """What `RP2040` registers in its C++ window table when it adopts this block, instead of a Python trampoline (see `_uart.pyx`)."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)

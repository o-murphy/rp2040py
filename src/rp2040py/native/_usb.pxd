# Cython view of core/usb.hpp (docs/tasks/usb-port.md) and the declaration of the native USB controller shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint8_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._clock cimport Clock
from rp2040py.native._core_host cimport ChipPorts
from rp2040py.native._native_block cimport NativeBlock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "usb.hpp" namespace "rp2040core":
    cdef const uint32_t kUsbAck
    cdef const uint32_t kUsbNak
    cdef const uint32_t kUsbStall
    cdef const uint32_t kUsbOther

    ctypedef cppbool (*UsbVoidFn)(void* ctx)
    ctypedef cppbool (*UsbEndpointReadFn)(void* ctx, uint32_t endpoint, uint32_t length)
    ctypedef cppbool (*UsbWriteQueuedFn)(void* ctx, uint32_t endpoint, uint32_t half, const uint8_t* data, uint32_t length)
    ctypedef cppbool (*UsbEndpointFn)(void* ctx, uint32_t endpoint)
    ctypedef cppbool (*UsbAddressFn)(void* ctx, uint32_t* address)
    ctypedef cppbool (*UsbSetupFn)(void* ctx, const uint8_t* setup, cppbool* has_response)
    ctypedef cppbool (*UsbPendingFn)(void* ctx, uint32_t max_length, cppbool* present, const uint8_t** data, uint32_t* length)
    ctypedef cppbool (*UsbDeviceInFn)(void* ctx, uint32_t endpoint_address, uint32_t* status, const uint8_t** data, uint32_t* length)
    ctypedef cppbool (*UsbDeviceOutFn)(void* ctx, uint32_t endpoint, const uint8_t* data, uint32_t length, uint32_t* status)

    cdef cppclass UsbHost:
        UsbHost() noexcept
        UsbVoidFn usb_enabled
        UsbVoidFn reset_received
        UsbEndpointReadFn endpoint_read
        UsbWriteQueuedFn write_queued
        UsbEndpointFn drain_write
        UsbEndpointFn drain_reaction
        UsbEndpointFn drain_read
        UsbVoidFn device_reset
        UsbAddressFn device_address
        UsbSetupFn device_setup
        UsbPendingFn pending_response
        UsbDeviceInFn device_in
        UsbDeviceOutFn device_out
        void* ctx

    cdef cppclass UsbBlock:
        void set_wire(const UsbHost& wire) noexcept
        uint32_t addr_endp
        uint32_t main_ctrl
        uint32_t int_raw
        uint32_t int_enable
        uint32_t int_force
        uint32_t sie_status
        uint32_t sie_ctrl
        uint32_t sof_frame_number
        cppbool connected
        cppbool connect_event
        cppbool host_connect_event
        uint32_t int_ep_addr_ctrl[15]
        uint32_t int_ep_ctrl
        uint32_t usb_pwr
        uint32_t usb_muxing
        uint32_t usbphy_direct
        uint32_t usbphy_direct_override
        uint32_t usbphy_trim
        uint32_t nak_poll
        uint32_t ep_abort
        uint32_t ep_abort_done
        uint32_t ep_stall_arm
        uint32_t ep_status_stall_nak
        cppbool host_mode
        cppbool sof_enabled
        cppbool has_device
        cppbool expecting_status_phase
        uint32_t control_data_pid
        uint32_t buf_select[32]
        uint32_t two_buffer_count[32]
        int64_t read_delay_microseconds
        int64_t write_delay_microseconds
        int64_t host_transaction_delay_microseconds
        int64_t host_reaction_microseconds
        UsbBlock() noexcept
        void detach() noexcept
        int64_t raw_write_value() noexcept
        cppbool has_scheduled_alarms() noexcept
        uint32_t done_count(uint32_t slot) noexcept
        uint32_t done_at(uint32_t slot, uint32_t index) noexcept
        uint32_t pending_count(uint32_t endpoint) noexcept
        uint32_t pending_at(uint32_t endpoint, uint32_t index) noexcept
        void done_push(uint32_t slot, uint32_t half) noexcept
        uint32_t buff_status() noexcept
        uint32_t cpu_should_handle() noexcept
        uint32_t int_status() noexcept
        cppbool check_interrupts() noexcept
        cppbool buff_status_updated() noexcept
        cppbool abort_done_updated() noexcept
        cppbool stall_nak_updated() noexcept
        cppbool sie_status_updated() noexcept
        cppbool set_connected(cppbool value) noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        cppbool dpram_updated(uint32_t offset) noexcept
        cppbool finish_read(uint32_t endpoint, const uint8_t* data, uint32_t size) noexcept
        cppbool finish_write(uint32_t endpoint, uint32_t half) noexcept
        void schedule_read(uint32_t endpoint, double delay_microseconds) noexcept
        cppbool send_setup_packet(const uint8_t* packet, uint32_t size) noexcept
        cppbool reset_device() noexcept
        cppbool reset() noexcept
        cppbool connect_device() noexcept
        cppbool disconnect_device() noexcept
        WindowHandler window_handler() noexcept

cdef void usb_wire_fill(UsbHost* wire, void* chip) noexcept


cdef class RPUSBController(NativeBlock):
    cdef dict __dict__  # tests and boards replace methods (`dpram_updated`) with instance attributes, as on the pure-Python class
    cdef UsbBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view and the host's end of the wire
    cdef public object clock
    cdef object _dpram_view
    cdef object _device
    cdef object _pending_response
    cdef list _read_queue
    cdef list _write_queue
    cdef list _reaction_queue
    cdef object _scratch
    cdef public object on_usb_enabled
    cdef public object on_reset_received
    cdef public object on_reset
    cdef public object on_endpoint_write
    cdef public object on_endpoint_read

# Cython view of core/mcu.hpp (docs/records/0096-cpp-mcu-core.md, step 2a): the chip as one object. Header-only C++17, compiled into every extension that cimports it.

from libc.stdint cimport int64_t, uint8_t, uint32_t, uint64_t

from libcpp cimport bool

from rp2040py.native._batch cimport BatchParams, BatchResult
from rp2040py.native._bus cimport Bus
from rp2040py.native._clock cimport Clock
from rp2040py.native._cpu cimport Cpu
from rp2040py.native._io cimport IoBlock
from rp2040py.native._pads cimport PadsBlock
from rp2040py.native._pin cimport PinBank
from rp2040py.native._dma cimport DmaBlock
from rp2040py.native._pio cimport PioBlock
from rp2040py.native._adc cimport AdcBlock
from rp2040py.native._busctrl cimport BusctrlBlock
from rp2040py.native._psm cimport PsmBlock
from rp2040py.native._reset cimport ResetsBlock
from rp2040py.native._clocks cimport ClocksBlock
from rp2040py.native._i2c cimport I2cBlock
from rp2040py.native._pll cimport PllBlock
from rp2040py.native._ppb cimport PpbBlock
from rp2040py.native._pwm cimport PwmBlock
from rp2040py.native._rosc cimport RoscBlock
from rp2040py.native._rtc cimport RtcBlock
from rp2040py.native._sio cimport SioBlock
from rp2040py.native._ssi cimport SsiBlock
from rp2040py.native._usb cimport UsbBlock, UsbHost
from rp2040py.native._xip_ctrl cimport XipBlock
from rp2040py.native._spi cimport SpiBlock
from rp2040py.native._syscfg cimport SyscfgBlock
from rp2040py.native._sysinfo cimport SysInfoBlock
from rp2040py.native._tbman cimport TbmanBlock
from rp2040py.native._timer cimport TimerBlock
from rp2040py.native._uart cimport UartBlock
from rp2040py.native._vreg_and_chip_reset cimport VregBlock
from rp2040py.native._watchdog cimport WatchdogBlock
from rp2040py.native._xosc cimport XoscBlock

cdef extern from "mcu.hpp" namespace "rp2040core":
    ctypedef BatchParams McuLimits
    ctypedef BatchResult McuResult
    ctypedef double (*McuMonotonicFn)(void* chip) noexcept
    ctypedef bool (*McuIrqHook)(void* ctx, uint32_t irq, bool level) noexcept
    ctypedef void (*McuWarnFn)(void* ctx, uint32_t block, uint32_t kind, uint32_t offset, int64_t value) noexcept
    ctypedef bool (*McuTreeHook)(void* ctx) noexcept
    ctypedef bool (*McuDreqHook)(void* ctx, uint32_t number, bool asserted) noexcept

    cdef const uint32_t kMcuBlockRtc
    cdef const uint32_t kMcuBlockTbman
    cdef const uint32_t kMcuBlockBusctrl
    cdef const uint32_t kMcuBlockVreg
    cdef const uint32_t kMcuBlockSysinfo
    cdef const uint32_t kMcuBlockSyscfg
    cdef const uint32_t kMcuBlockTimer
    cdef const uint32_t kMcuBlockAdc
    cdef const uint32_t kMcuBlockUart0
    cdef const uint32_t kMcuBlockUart1
    cdef const uint32_t kMcuBlockSpi0
    cdef const uint32_t kMcuBlockSpi1
    cdef const uint32_t kMcuBlockI2c0
    cdef const uint32_t kMcuBlockI2c1
    cdef const uint32_t kMcuBlockClocks
    cdef const uint32_t kMcuBlockPllSys
    cdef const uint32_t kMcuBlockPllUsb
    cdef const uint32_t kMcuBlockXosc
    cdef const uint32_t kMcuBlockRosc
    cdef const uint32_t kMcuBlockPpb
    cdef const uint32_t kMcuBlockWatchdog
    cdef const uint32_t kMcuBlockDma
    cdef const uint32_t kMcuBlockIoBank0
    cdef const uint32_t kMcuBlockIoQspi
    cdef const uint32_t kMcuBlockPadsBank0
    cdef const uint32_t kMcuBlockPadsQspi
    cdef const uint32_t kMcuBlockSio
    cdef const uint32_t kMcuBlockPwm
    cdef const uint32_t kMcuBlockPio0
    cdef const uint32_t kMcuBlockPio1
    cdef const uint32_t kMcuBlockSsi
    cdef const uint32_t kMcuBlockXip
    cdef const uint32_t kMcuBlockUsb
    cdef const uint32_t kMcuBlockPsm
    cdef const uint32_t kMcuBlockResets

    cdef cppclass McuMemory:
        McuMemory() noexcept
        uint8_t* rom
        uint32_t rom_size
        uint8_t* flash
        uint32_t flash_size
        uint8_t* sram
        uint32_t sram_size
        uint8_t* dpram
        uint32_t dpram_size

    cdef cppclass McuHost:
        McuHost() noexcept
        McuWarnFn warn
        void* ctx
        const int* failed
        bool (*uart_byte)(void* ctx, uint32_t instance, uint32_t byte) noexcept
        bool (*uart_baud)(void* ctx, uint32_t instance) noexcept
        bool (*spi_transmit)(void* ctx, uint32_t instance, uint32_t value) noexcept
        bool (*i2c_start)(void* ctx, uint32_t instance, bool repeated) noexcept
        bool (*i2c_connect)(void* ctx, uint32_t instance, uint32_t address, uint32_t mode) noexcept
        bool (*i2c_write)(void* ctx, uint32_t instance, uint32_t value) noexcept
        bool (*i2c_read)(void* ctx, uint32_t instance, bool ack) noexcept
        bool (*i2c_stop)(void* ctx, uint32_t instance) noexcept
        bool (*pin_source)(void* ctx, uint32_t bank, uint32_t source, uint32_t* value) noexcept
        bool (*pin_changed)(void* ctx, uint32_t bank, uint32_t pin, int new_state, int old_state) noexcept
        bool (*pin_input_changed)(void* ctx, uint32_t bank, uint32_t pin, bool pwm, uint32_t pio_mask) noexcept
        bool (*pin_inputs)(void* ctx, uint32_t bank, uint32_t* value) noexcept
        bool (*pin_update)(void* ctx, uint32_t bank, uint32_t pin_mask) noexcept
        void (*system_reset_request)(void* ctx) noexcept
        bool (*watchdog_trigger)(void* ctx) noexcept
        bool (*watchdog_tick)(void* ctx, double tick_hz) noexcept
        double (*xosc_hz)(void* ctx) noexcept
        double (*rosc_hz)(void* ctx) noexcept
        bool (*adc_read)(void* ctx, uint32_t channel) noexcept
        bool (*clk_sys)(void* ctx, double* hz) noexcept
        const UsbHost* usb_wire
        bool (*reset_block)(void* ctx, uint32_t block) noexcept
        bool (*usb_reset)(void* ctx) noexcept
        bool (*bus_reset)(void* ctx) noexcept
        bool (*record_cause)(void* ctx, uint32_t flag) noexcept
        bool (*pio_started)(void* ctx, uint32_t index) noexcept
        bool (*adc_channel_value)(void* ctx, uint32_t channel, int64_t* value) noexcept

    cdef cppclass Mcu:
        Mcu() noexcept
        Cpu cpu
        Bus bus
        Clock& clock() noexcept
        void use_clock(Clock* clock) noexcept
        bool set_interrupt(uint32_t irq, bool level) noexcept
        bool set_interrupt_unhooked(uint32_t irq, bool level) noexcept
        Cpu& core(uint8_t index) noexcept
        void set_core1_enabled(bool on) noexcept
        bool core1_running() noexcept
        void set_irq_hook(McuIrqHook hook, void* ctx) noexcept
        bool set_dreq(uint32_t number, bool asserted) noexcept
        void attach_dma(DmaBlock* dma) noexcept
        void set_dreq_hook(McuDreqHook hook, void* ctx) noexcept
        bool owns_clock() noexcept
        void request_stop(bool stop) noexcept
        bool stop_requested() noexcept
        void set_real_io(bool waiting) noexcept
        bool real_io() noexcept
        uint8_t* stop_address() noexcept
        uint8_t* real_io_address() noexcept
        PioBlock* pio_block(int index) noexcept
        void build(const McuHost& host) noexcept
        RtcBlock& rtc() noexcept
        TbmanBlock& tbman() noexcept
        BusctrlBlock& busctrl() noexcept
        ResetsBlock& resets() noexcept
        PsmBlock& psm() noexcept
        VregBlock& vreg() noexcept
        SysInfoBlock& sysinfo() noexcept
        SyscfgBlock& syscfg() noexcept
        PinBank& pins(uint32_t bank) noexcept
        IoBlock& io(uint32_t bank) noexcept
        PadsBlock& pads(uint32_t bank) noexcept
        SioBlock& sio() noexcept
        SsiBlock& ssi() noexcept
        XipBlock& xip() noexcept
        UsbBlock& usb() noexcept
        void set_usb_dpram(uint8_t* data) noexcept
        void set_memory(const McuMemory& memory) noexcept
        void set_flash(uint8_t* data, uint32_t size) noexcept
        bool reset(bool from_watchdog, bool preserve_flash, uint32_t psm_wdsel, uint32_t resets_wdsel, uint64_t world) noexcept
        bool enter_reset_code(uint32_t cause, uint32_t psm_wdsel, uint32_t resets_wdsel, uint64_t world) noexcept
        void leave_reset() noexcept
        PwmBlock& pwm() noexcept
        PpbBlock& ppb() noexcept
        PpbBlock& ppb(uint8_t core) noexcept
        WatchdogBlock& watchdog() noexcept
        DmaBlock& dma() noexcept
        ClocksBlock& clocks() noexcept
        PllBlock& pll(uint32_t i) noexcept
        XoscBlock& xosc() noexcept
        RoscBlock& rosc() noexcept
        void set_crystal_hz(double hz) noexcept
        void set_pin_bank_world(uint32_t bank, bool world) noexcept
        void set_pwm_world(bool world) noexcept
        void set_pio_world(uint32_t index, bool world) noexcept
        void set_tree_hook(McuTreeHook hook, void* ctx) noexcept
        TimerBlock& timer() noexcept
        AdcBlock& adc() noexcept
        UartBlock& uart(uint32_t i) noexcept
        SpiBlock& spi(uint32_t i) noexcept
        I2cBlock& i2c(uint32_t i) noexcept
        void set_clk_changed_flag(const uint8_t* flag) noexcept
        void set_monotonic(McuMonotonicFn monotonic) noexcept
        int run(const McuLimits& limits, McuResult* result) noexcept

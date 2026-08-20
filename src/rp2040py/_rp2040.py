from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from rp2040py.clock.clock import IClock
from rp2040py.clock.simulation_clock import SimulationClock
from rp2040py.cortex_m0_core import CortexM0Core
from rp2040py.gpio_pin import GPIOPin
from rp2040py.irq import IRQ
from rp2040py.memory_map import (
    APB_START_ADDRESS,
    DPRAM_START_ADDRESS,
    FLASH_END_ADDRESS,
    FLASH_START_ADDRESS,
    RAM_START_ADDRESS,
    SIO_START_ADDRESS,
)
from rp2040py.peripherals.adc import RPADC
from rp2040py.peripherals.busctrl import RPBUSCTRL
from rp2040py.peripherals.clocks import RPClocks
from rp2040py.peripherals.dma import RPDMA, DREQChannel
from rp2040py.peripherals.i2c import RPI2C
from rp2040py.peripherals.io import RPIO
from rp2040py.peripherals.pads import RPPADS
from rp2040py.peripherals.peripheral import Peripheral, UnimplementedPeripheral
from rp2040py.peripherals.pio import RPPIO
from rp2040py.peripherals.ppb import RPPPB
from rp2040py.peripherals.psm import RPPSM
from rp2040py.peripherals.pwm import RPPWM
from rp2040py.peripherals.reset import RPReset
from rp2040py.peripherals.rtc import RP2040RTC
from rp2040py.peripherals.spi import RPSPI, ISPIDMAChannels
from rp2040py.peripherals.ssi import RPSSI
from rp2040py.peripherals.syscfg import RP2040SysCfg
from rp2040py.peripherals.sysinfo import RP2040SysInfo
from rp2040py.peripherals.tbman import RPTBMAN
from rp2040py.peripherals.timer import RPTimer
from rp2040py.peripherals.uart import RPUART, IUARTDMAChannels
from rp2040py.peripherals.usb import RPUSBController
from rp2040py.peripherals.vreg_and_chip_reset import RPVREGAndChipReset
from rp2040py.peripherals.watchdog import RPWatchdog
from rp2040py.peripherals.xip_ctrl import RPXIPCtrl
from rp2040py.peripherals.xosc import RPXOSC
from rp2040py.qspi_pads import QSPI_PAD_RESET_VALUES
from rp2040py.sio import RPSIO
from rp2040py.utils.bit import (
    read_uint16_le,
    read_uint32_le,
    u32,
    write_uint16_le,
    write_uint32_le,
)
from rp2040py.utils.logging import ConsoleLogger, Logger, LogLevel

if TYPE_CHECKING:
    from rp2040py.simulator import Simulator

__all__ = (
    "APB_START_ADDRESS",
    "DPRAM_START_ADDRESS",
    "FLASH_END_ADDRESS",
    "FLASH_START_ADDRESS",
    "RAM_START_ADDRESS",
    "RP2040",
    "SIO_START_ADDRESS",
)

LOG_NAME = "RP2040"

KB = 1024
MB = 1024 * KB
MHZ = 1_000_000


class RP2040:
    def __init__(self, clock: IClock | None = None):
        # NOTE: must be set before constructing any peripheral below - several of them
        # (RPPPB, RPWatchdog, RPADC, RPPWM, RPPIO, RPUSBController, RPDMA) call
        # self.clock.create_alarm(...) from their own __init__.
        self.clock = clock if clock is not None else SimulationClock()

        # A plain list, not Uint32Array (same rationale as CortexM0Core.registers - see
        # docs/PORTING.md's performance section): bootrom is on the hot read_uint32 path (real
        # ROM routines like the bootrom's own memcpy helper get called frequently during flash/USB
        # operations), and a raw list index is a C-level bytecode op versus Uint32Array's
        # Python-level __getitem__/__setitem__. The two write sites (load_bootrom() below,
        # write_uint32()'s bootrom branch) mask explicitly with `& 0xFFFFFFFF` instead of relying
        # on the wrapper for it.
        self.bootrom: list[int] = [0] * (4 * KB)
        # Cached rather than recomputed via len(self.bootrom) on every bus access: bootrom size
        # never changes after construction, and this matters on the hot read_uint32/write_uint32
        # path regardless of container type.
        self.bootrom_byte_size = len(self.bootrom) * 4
        self.sram = bytearray(264 * KB)
        # Same reasoning as bootrom_byte_size above: sram/usb_dpram sizes never change after
        # construction, and read_uint16()/read_uint8()/write_uint8()/write_uint16() are all on the
        # hot bus-access path (read_uint16() alone runs on essentially every instruction fetch).
        self.ram_byte_size = len(self.sram)
        self.flash = bytearray(16 * MB)
        # NOT the same as FLASH_END_ADDRESS - FLASH_START_ADDRESS (that spans all four XIP mirror
        # regions read_uint32() folds back onto this same backing array; this is just the base
        # region's own size, matching what read_uint16()/read_uint8()/write_uint32() already check).
        self.flash_byte_size = len(self.flash)
        self.usb_dpram = bytearray(4 * KB)
        self.dpram_byte_size = len(self.usb_dpram)

        self.core = CortexM0Core(self)

        # Clocks
        self.clk_sys = 125 * MHZ
        self.clk_peri = 125 * MHZ

        self.ppb = RPPPB(self, "PPB")
        self.sio = RPSIO(self)

        self.uart = [
            RPUART(
                self, "UART0", IRQ.UART0, IUARTDMAChannels(rx=DREQChannel.DREQ_UART0_RX, tx=DREQChannel.DREQ_UART0_TX)
            ),
            RPUART(
                self, "UART1", IRQ.UART1, IUARTDMAChannels(rx=DREQChannel.DREQ_UART1_RX, tx=DREQChannel.DREQ_UART1_TX)
            ),
        ]
        self.i2c = [RPI2C(self, "I2C0", IRQ.I2C0), RPI2C(self, "I2C1", IRQ.I2C1)]
        self.pwm = RPPWM(self, "PWM_BASE")
        self.adc = RPADC(self, "ADC")

        self.gpio = [GPIOPin(self, i) for i in range(30)]

        self.qspi = [
            GPIOPin(self, 0, "SCLK", always_output_enabled=True),
            GPIOPin(self, 1, "SS", always_output_enabled=True),
            GPIOPin(self, 2, "SD0", always_output_enabled=True),
            GPIOPin(self, 3, "SD1", always_output_enabled=True),
            GPIOPin(self, 4, "SD2", always_output_enabled=True),
            GPIOPin(self, 5, "SD3", always_output_enabled=True),
        ]
        # GPIOPin's own constructor default is PADS_BANK0's reset value, which is wrong for this
        # bank: PADS_QSPI resets each pad differently (RP2040 datasheet 2.19.6.3), and the
        # difference is load-bearing. GPIO_QSPI_SS is the only pad with a pull-up, which is what
        # holds the flash deselected and what makes the BOOTSEL button readable (the button pulls
        # SS down). Left at the bank0 default, an undriven SS reads low forever, so firmware that
        # polls it - CircuitPython 10.x does, from RAM, during boot - never sees it go high.
        # See docs/records/0050-qspi-pad-reset-values.md.
        for _qspi_pin, _pad_reset in zip(self.qspi, QSPI_PAD_RESET_VALUES):
            _qspi_pin.pad_value = _pad_reset

        self.dma = RPDMA(self, "DMA")
        self.pio = [
            RPPIO(self, "PIO0", IRQ.PIO0_IRQ0, 0),
            RPPIO(self, "PIO1", IRQ.PIO1_IRQ0, 1),
        ]
        self.usb_ctrl = RPUSBController(self, "USB")
        self.watchdog = RPWatchdog(self, "WATCHDOG_BASE")
        # Named, not just an entry in `peripherals` below: BaseDevice.hard_reset() has to
        # record *which* reset just happened in CHIP_RESET (0089 Phase 1), the same way it
        # reaches self.watchdog for REASON - a dict lookup by base address would work but
        # would be the only such reach-in in the tree.
        self.vreg_and_chip_reset = RPVREGAndChipReset(self, "VREG_AND_CHIP_RESET_BASE")
        self.spi = [
            RPSPI(self, "SPI0", IRQ.SPI0, ISPIDMAChannels(rx=DREQChannel.DREQ_SPI0_RX, tx=DREQChannel.DREQ_SPI0_TX)),
            RPSPI(self, "SPI1", IRQ.SPI1, ISPIDMAChannels(rx=DREQChannel.DREQ_SPI1_RX, tx=DREQChannel.DREQ_SPI1_TX)),
        ]

        self.logger: Logger = ConsoleLogger(LogLevel.DEBUG, True)

        self.peripherals: dict[int, Peripheral] = {
            0x14000: RPXIPCtrl(self, "XIP_CTRL_BASE"),
            0x18000: RPSSI(self, "SSI"),
            0x40000: RP2040SysInfo(self, "SYSINFO_BASE"),
            0x40004: RP2040SysCfg(self, "SYSCFG"),
            0x40008: RPClocks(self, "CLOCKS_BASE"),
            0x4000C: RPReset(self, "RESETS_BASE"),
            0x40010: RPPSM(self, "PSM_BASE"),
            0x40014: RPIO(self, "IO_BANK0_BASE"),
            0x40018: RPIO(self, "IO_QSPI_BASE", pins=self.qspi),
            0x4001C: RPPADS(self, "PADS_BANK0_BASE", "bank0"),
            0x40020: RPPADS(self, "PADS_QSPI_BASE", "qspi"),
            0x40024: RPXOSC(self, "XOSC_BASE"),
            0x40028: UnimplementedPeripheral(self, "PLL_SYS_BASE"),
            0x4002C: UnimplementedPeripheral(self, "PLL_USB_BASE"),
            0x40030: RPBUSCTRL(self, "BUSCTRL_BASE"),
            0x40034: self.uart[0],
            0x40038: self.uart[1],
            0x4003C: self.spi[0],
            0x40040: self.spi[1],
            0x40044: self.i2c[0],
            0x40048: self.i2c[1],
            0x4004C: self.adc,
            0x40050: self.pwm,
            0x40054: RPTimer(self, "TIMER_BASE"),
            0x40058: self.watchdog,
            0x4005C: RP2040RTC(self, "RTC_BASE"),
            0x40060: UnimplementedPeripheral(self, "ROSC_BASE"),
            0x40064: self.vreg_and_chip_reset,
            0x4006C: RPTBMAN(self, "TBMAN_BASE"),
            0x50000: self.dma,
            0x50110: self.usb_ctrl,
            0x50200: self.pio[0],
            0x50300: self.pio[1],
        }

        # Debugging
        self.on_break: Callable[[int], None] = self._default_on_break
        # Set via the `simulator` property below, by the owning Simulator's own __init__ - a bare
        # RP2040() constructed without one (tests, `rp2040py bench`'s synthetic mode driving
        # execute_instruction() directly, ...) simply has none, and schedule_threadsafe() raises
        # in that case rather than silently doing nothing.
        self._simulator: Simulator | None = None

        # RUN pin / RESET button (docs/records/0089-one-reset-for-every-trigger.md Phase 4, which
        # is docs/records/0057-run-pin-reset-hook.md in full). RUN is neither a GPIO nor
        # memory-mapped, so it gets no pad and no register - it is a plain level, held high by the
        # board's own external pull-up (0057's addendum has a real schematic: `3V3 -[R12 10k]- RUN`
        # with the switch grounding RUN directly). That makes a press a *level*, not a pulse: the
        # chip stays in reset for as long as the button is down, and the release is what boots it.
        # `run_pin_low` is what execute_batch() reads to skip execution entirely while held;
        # `on_run_pin_reset` is the hook `BaseDevice` installs its own hard_reset() into - the same
        # downward installation `watchdog.on_watchdog_trigger` already uses, so an ExternalDevice
        # holding nothing but `attach(rp2040)`'s RP2040 can still reach the full device-level
        # sequence (which includes `cdc.reset()`, unreachable from here).
        self._run_pin_low = False
        self.on_run_pin_reset: Callable[[], None] = self._default_run_pin_reset

        self.reset()

    def _default_on_break(self, code: int) -> None:
        # TODO: raise HardFault exception
        pass

    def _default_run_pin_reset(self) -> None:
        self.logger.warning(LOG_NAME, "RUN pin released, but no reset handler provided")

    @property
    def run_pin_low(self) -> bool:
        """True while RUN is held low - i.e. the chip is in reset and executes nothing at all.
        Read once per batch by `execute_batch()` (see `set_run_pin()` for the edge semantics)."""
        return self._run_pin_low

    def set_run_pin(self, *, low: bool) -> None:
        """Drive the RUN pin, as a RESET button does. `low=True` holds the chip in reset (nothing
        executes, no simulated time passes); `low=False` releases it, which is the edge that
        actually performs the reset-and-boot sequence via `on_run_pin_reset`.

        Idempotent per level - re-driving the level already present is not an edge and fires
        nothing, matching a switch that is simply still held. With no hook installed (a bare
        `RP2040` outside a `BaseDevice`) a release is logged and ignored rather than half-resetting
        the chip: the USB half of a real reset lives on the device layer and is not reachable from
        here.

        Not thread-safe, and deliberately not made so: call it on the engine-room loop, which is
        what `external/reset_button.py` does via `schedule_threadsafe()` (0030's rule for an
        ExternalDevice pressed while the simulation is running)."""
        if low == self._run_pin_low:
            return
        self._run_pin_low = low
        if not low:
            self.on_run_pin_reset()

    @property
    def simulator(self) -> "Simulator | None":
        return self._simulator

    @simulator.setter
    def simulator(self, value: "Simulator") -> None:
        self._simulator = value

    def schedule_threadsafe(self, fn_or_coro: "Callable[[], None] | Coroutine[Any, Any, Any]") -> None:
        """Hands `fn_or_coro` off to the engine-room loop of whichever Simulator owns this RP2040 -
        see Simulator.schedule_threadsafe() for the full contract (docs/CYW43_WIFI_BACKLOG.md's
        "Concurrency model" section). Raises RuntimeError if this RP2040 has no owning Simulator."""
        if self._simulator is None:
            raise RuntimeError("RP2040.schedule_threadsafe() called on an RP2040 with no owning Simulator")
        self._simulator.schedule_threadsafe(fn_or_coro)

    def load_bootrom(self, bootrom_data: list[int]) -> None:
        self.bootrom[: len(bootrom_data)] = (value & 0xFFFFFFFF for value in bootrom_data)
        self.reset()

    def reset(self, *, preserve_flash: bool = False) -> None:
        """`preserve_flash=True` is for a live reset (RPWatchdog.on_watchdog_trigger, via a real
        machine.reset()/machine.bootloader()) - unlike the construction-time/load_bootrom() calls
        below, that must not erase the firmware/filesystem currently running from flash."""
        self.core.reset()
        self.pwm.reset()
        self.dma.reset()
        self.ppb.reset()
        if not preserve_flash:
            self.flash[:] = b"\xff" * len(self.flash)

    def read_uint32(self, address: int) -> int:
        address = u32(address)
        if address & 0x3:
            self.logger.warning(LOG_NAME, f"read from address {address:x}, which is not 32 bit aligned")

        bootrom = self.bootrom
        if address < self.bootrom_byte_size:
            return bootrom[address // 4]
        if FLASH_START_ADDRESS <= address < FLASH_END_ADDRESS:
            # Flash is mirrored four times:
            # - 0x10000000 XIP
            # - 0x11000000 XIP_NOALLOC
            # - 0x12000000 XIP_NOCACHE
            # - 0x13000000 XIP_NOCACHE_NOALLOC
            offset = address & 0x00FFFFFF
            return read_uint32_le(self.flash, offset)
        if RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            return read_uint32_le(self.sram, address - RAM_START_ADDRESS)
        if DPRAM_START_ADDRESS <= address < DPRAM_START_ADDRESS + self.dpram_byte_size:
            return read_uint32_le(self.usb_dpram, address - DPRAM_START_ADDRESS)
        if address >> 12 == 0xE000E:
            return self.ppb.read_uint32(address & 0xFFF)
        if SIO_START_ADDRESS <= address < SIO_START_ADDRESS + 0x10000000:
            # int(): SIO's DIV_QUOTIENT can be a fractional JS number (see sio.py) - any real JS
            # consumer (typed-array store, bitwise op) truncates it immediately via ToUint32, so
            # truncate here rather than letting a float leak into the rest of the bus/CPU/DMA
            # read path.
            return int(self.sio.read_uint32(address - SIO_START_ADDRESS)) & 0xFFFFFFFF

        peripheral = self.find_peripheral(address)
        if peripheral:
            return peripheral.read_uint32(address & 0x3FFF)

        self.logger.warning(LOG_NAME, f"Read from invalid memory address: {address:x}")
        return 0xFFFFFFFF

    def find_peripheral(self, address: int) -> Peripheral | None:
        return self.peripherals.get((u32(address) >> 14) << 2)

    def read_uint16(self, address: int) -> int:
        """We assume the address is 16-bit aligned."""
        if FLASH_START_ADDRESS <= address < FLASH_START_ADDRESS + self.flash_byte_size:
            return read_uint16_le(self.flash, address - FLASH_START_ADDRESS)
        if RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            return read_uint16_le(self.sram, address - RAM_START_ADDRESS)

        value = self.read_uint32(address & 0xFFFFFFFC)
        return (value & 0xFFFF0000) >> 16 if address & 0x2 else value & 0xFFFF

    def read_uint8(self, address: int) -> int:
        if FLASH_START_ADDRESS <= address < FLASH_START_ADDRESS + self.flash_byte_size:
            return self.flash[address - FLASH_START_ADDRESS]
        if RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            return self.sram[address - RAM_START_ADDRESS]

        value = self.read_uint16(address & 0xFFFFFFFE)
        return u32((value & 0xFF00) >> 8 if address & 0x1 else value & 0xFF)

    def write_uint32(self, address: int, value: int) -> None:
        address = u32(address)
        bootrom = self.bootrom
        # Same range order as read_uint32() - RAM/flash/bootrom checked first via cheap integer
        # comparisons, find_peripheral() (a dict lookup) only as the fallback for what's left.
        # This branch used to check find_peripheral() unconditionally *first*, so every RAM write
        # (the overwhelmingly common case - stack spills, GC, locals) paid for a dict .get() that
        # was always going to miss; write_uint8()/write_uint16() never had this problem.
        if address < self.bootrom_byte_size:
            bootrom[address // 4] = value & 0xFFFFFFFF
        elif FLASH_START_ADDRESS <= address < FLASH_START_ADDRESS + self.flash_byte_size:
            write_uint32_le(self.flash, address - FLASH_START_ADDRESS, value)
        elif RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            write_uint32_le(self.sram, address - RAM_START_ADDRESS, value)
        elif DPRAM_START_ADDRESS <= address < DPRAM_START_ADDRESS + self.dpram_byte_size:
            offset = address - DPRAM_START_ADDRESS
            write_uint32_le(self.usb_dpram, offset, value)
            self.usb_ctrl.dpram_updated(offset, value)
        elif SIO_START_ADDRESS <= address < SIO_START_ADDRESS + 0x10000000:
            self.sio.write_uint32(address - SIO_START_ADDRESS, value)
        elif address >> 12 == 0xE000E:
            self.ppb.write_uint32(address & 0xFFF, value)
        else:
            peripheral = self.find_peripheral(address)
            if peripheral:
                atomic_type = (address & 0x3000) >> 12
                offset = address & 0xFFF
                peripheral.write_uint32_atomic(offset, value, atomic_type)
            else:
                self.logger.warning(LOG_NAME, f"Write to undefined address: {address:x}")

    def write_uint8(self, address: int, value: int) -> None:
        if RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            self.sram[address - RAM_START_ADDRESS] = value & 0xFF
            return

        aligned_address = u32(address & 0xFFFFFFFC)
        offset = address & 0x3
        peripheral = self.find_peripheral(address)
        if peripheral:
            atomic_type = (aligned_address & 0x3000) >> 12
            peripheral_offset = aligned_address & 0xFFF
            peripheral.write_uint32_atomic(
                peripheral_offset,
                (value & 0xFF) | ((value & 0xFF) << 8) | ((value & 0xFF) << 16) | ((value & 0xFF) << 24),
                atomic_type,
            )
            return
        original_value = self.read_uint32(aligned_address)
        patched = bytearray(u32(original_value).to_bytes(4, "little"))
        patched[offset] = value & 0xFF
        self.write_uint32(aligned_address, int.from_bytes(patched, "little"))

    def write_uint16(self, address: int, value: int) -> None:
        # we assume that address is 16-bit aligned.
        # Ideally we should generate a fault if not!

        if RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            write_uint16_le(self.sram, address - RAM_START_ADDRESS, value)
            return

        aligned_address = u32(address & 0xFFFFFFFC)
        offset = address & 0x3
        peripheral = self.find_peripheral(address)
        if peripheral:
            atomic_type = (aligned_address & 0x3000) >> 12
            peripheral_offset = aligned_address & 0xFFF
            peripheral.write_uint32_atomic(peripheral_offset, (value & 0xFFFF) | ((value & 0xFFFF) << 16), atomic_type)
            return
        original_value = self.read_uint32(aligned_address)
        patched = bytearray(u32(original_value).to_bytes(4, "little"))
        write_uint16_le(patched, offset, value)
        self.write_uint32(aligned_address, int.from_bytes(patched, "little"))

    @property
    def gpio_values(self) -> int:
        result = 0
        for gpio_index in range(len(self.gpio)):
            if self.gpio[gpio_index].input_value:
                result |= 1 << gpio_index
        return result

    def set_interrupt(self, irq: int, value: bool) -> None:
        self.core.set_interrupt(irq, value)

    def update_io_interrupt(self) -> None:
        interrupt_value = False
        for pin in self.gpio:
            if pin.irq_value:
                interrupt_value = True
        self.set_interrupt(IRQ.IO_BANK0, interrupt_value)

    def step(self) -> None:
        self.core.execute_instruction()

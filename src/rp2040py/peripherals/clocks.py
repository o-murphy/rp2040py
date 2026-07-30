from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPClocks",)

CLK_GPOUT0_CTRL = 0x00
CLK_GPOUT0_DIV = 0x04
CLK_GPOUT0_SELECTED = 0x8
CLK_GPOUT1_CTRL = 0x0C
CLK_GPOUT1_DIV = 0x10
CLK_GPOUT1_SELECTED = 0x14
CLK_GPOUT2_CTRL = 0x18
CLK_GPOUT2_DIV = 0x01C
CLK_GPOUT2_SELECTED = 0x20
CLK_GPOUT3_CTRL = 0x24
CLK_GPOUT3_DIV = 0x28
CLK_GPOUT3_SELECTED = 0x2C
CLK_REF_CTRL = 0x30
CLK_REF_DIV = 0x34
CLK_REF_SELECTED = 0x38
CLK_SYS_CTRL = 0x3C
CLK_SYS_DIV = 0x40
CLK_SYS_SELECTED = 0x44
CLK_PERI_CTRL = 0x48
CLK_PERI_DIV = 0x4C
CLK_PERI_SELECTED = 0x50
CLK_USB_CTRL = 0x54
CLK_USB_DIV = 0x58
CLK_USB_SELECTED = 0x5C
CLK_ADC_CTRL = 0x60
CLK_ADC_DIV = 0x64
CLK_ADC_SELECTED = 0x68
CLK_RTC_CTRL = 0x6C
CLK_RTC_DIV = 0x70
CLK_RTC_SELECTED = 0x74
CLK_SYS_RESUS_CTRL = 0x78
CLK_SYS_RESUS_STATUS = 0x7C


class RPClocks(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self.gpout0_ctrl = 0
        self.gpout0_div = 0x100
        self.gpout1_ctrl = 0
        self.gpout1_div = 0x100
        self.gpout2_ctrl = 0
        self.gpout2_div = 0x100
        self.gpout3_ctrl = 0
        self.gpout3_div = 0x100
        self.ref_ctrl = 0
        self.ref_div = 0x100
        self.peri_ctrl = 0
        self.peri_div = 0x100
        self.usb_ctrl = 0
        self.usb_div = 0x100
        self.sys_ctrl = 0
        self.sys_div = 0x100
        self.adc_ctrl = 0
        self.adc_div = 0x100
        self.rtc_ctrl = 0
        self.rtc_div = 0x100

    def read_uint32(self, offset: int) -> int:
        if offset == CLK_GPOUT0_CTRL:
            return self.gpout0_ctrl & 0b100110001110111100000
        if offset == CLK_GPOUT0_DIV:
            return self.gpout0_div
        if offset == CLK_GPOUT0_SELECTED:
            return 1
        if offset == CLK_GPOUT1_CTRL:
            return self.gpout1_ctrl & 0b100110001110111100000
        if offset == CLK_GPOUT1_DIV:
            return self.gpout1_div
        if offset == CLK_GPOUT1_SELECTED:
            return 1
        if offset == CLK_GPOUT2_CTRL:
            return self.gpout2_ctrl & 0b100110001110111100000
        if offset == CLK_GPOUT2_DIV:
            return self.gpout2_div
        if offset == CLK_GPOUT2_SELECTED:
            return 1
        if offset == CLK_GPOUT3_CTRL:
            return self.gpout3_ctrl & 0b100110001110111100000
        if offset == CLK_GPOUT3_DIV:
            return self.gpout3_div
        if offset == CLK_GPOUT3_SELECTED:
            return 1
        if offset == CLK_REF_CTRL:
            return self.ref_ctrl & 0b000001100011
        if offset == CLK_REF_DIV:
            return self.ref_div & 0x30  # b8..9 = int divisor. no frac divisor present
        if offset == CLK_REF_SELECTED:
            return 1 << (self.ref_ctrl & 0x03)
        if offset == CLK_SYS_CTRL:
            return self.sys_ctrl & 0b000011100001
        if offset == CLK_SYS_DIV:
            return self.sys_div
        if offset == CLK_SYS_SELECTED:
            return 1 << (self.sys_ctrl & 0x01)
        if offset == CLK_PERI_CTRL:
            return self.peri_ctrl & 0b110011100000
        if offset == CLK_PERI_DIV:
            return self.peri_div
        if offset == CLK_PERI_SELECTED:
            return 1
        if offset == CLK_USB_CTRL:
            return self.usb_ctrl & 0b100110000110011100000
        if offset == CLK_USB_DIV:
            return self.usb_div
        if offset == CLK_USB_SELECTED:
            return 1
        if offset == CLK_ADC_CTRL:
            return self.adc_ctrl & 0b100110000110011100000
        if offset == CLK_ADC_DIV:
            return self.adc_div & 0x30
        if offset == CLK_ADC_SELECTED:
            return 1
        if offset == CLK_RTC_CTRL:
            return self.rtc_ctrl & 0b100110000110011100000
        if offset == CLK_RTC_DIV:
            return self.rtc_div & 0x30
        if offset == CLK_RTC_SELECTED:
            return 1
        if offset == CLK_SYS_RESUS_CTRL:
            return 0xFF
        if offset == CLK_SYS_RESUS_STATUS:
            return 0  # clock resus not implemented
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == CLK_GPOUT0_CTRL:
            self.gpout0_ctrl = value
        elif offset == CLK_GPOUT0_DIV:
            self.gpout0_div = value
        elif offset == CLK_GPOUT1_CTRL:
            self.gpout1_ctrl = value
        elif offset == CLK_GPOUT1_DIV:
            self.gpout1_div = value
        elif offset == CLK_GPOUT2_CTRL:
            self.gpout2_ctrl = value
        elif offset == CLK_GPOUT2_DIV:
            self.gpout2_div = value
        elif offset == CLK_GPOUT3_CTRL:
            self.gpout3_ctrl = value
        elif offset == CLK_GPOUT3_DIV:
            self.gpout3_div = value
        elif offset == CLK_REF_CTRL:
            self.ref_ctrl = value
        elif offset == CLK_REF_DIV:
            self.ref_div = value
        elif offset == CLK_SYS_CTRL:
            self.sys_ctrl = value
        elif offset == CLK_SYS_DIV:
            self.sys_div = value
        elif offset == CLK_PERI_CTRL:
            self.peri_ctrl = value
        elif offset == CLK_PERI_DIV:
            self.peri_div = value
        elif offset == CLK_USB_CTRL:
            self.usb_ctrl = value
        elif offset == CLK_USB_DIV:
            self.usb_div = value
        elif offset == CLK_ADC_CTRL:
            self.adc_ctrl = value
        elif offset == CLK_ADC_DIV:
            self.adc_div = value
        elif offset == CLK_RTC_CTRL:
            self.rtc_ctrl = value
        elif offset == CLK_RTC_DIV:
            self.rtc_div = value
        elif offset == CLK_SYS_RESUS_CTRL:
            return  # clock resus not implemented
        else:
            super().write_uint32(offset, value)

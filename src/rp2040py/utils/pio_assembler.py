PIO_SRC_PINS = 0
PIO_SRC_X = 1
PIO_SRC_Y = 2
PIO_SRC_NULL = 3
PIO_SRC_STATUS = 5
PIO_SRC_ISR = 6
PIO_SRC_OSR = 7

PIO_DEST_PINS = 0
PIO_DEST_X = 1
PIO_DEST_Y = 2
PIO_DEST_NULL = 3
PIO_DEST_PINDIRS = 4
PIO_DEST_PC = 5
PIO_DEST_ISR = 6
PIO_DEST_EXEC = 7

PIO_MOV_DEST_PINS = 0
PIO_MOV_DEST_X = 1
PIO_MOV_DEST_Y = 2
PIO_MOV_DEST_EXEC = 4
PIO_MOV_DEST_PC = 5
PIO_MOV_DEST_ISR = 6
PIO_MOV_DEST_OSR = 7

PIO_OP_NONE = 0
PIO_OP_INVERT = 1
PIO_OP_BITREV = 2

PIO_WAIT_SRC_GPIO = 0
PIO_WAIT_SRC_PIN = 1
PIO_WAIT_SRC_IRQ = 2

PIO_COND_ALWAYS = 0
PIO_COND_NOTX = 1
PIO_COND_XDEC = 2
PIO_COND_NOTY = 3
PIO_COND_YDEC = 4
PIO_COND_XNEY = 5
PIO_COND_PIN = 6
PIO_COND_NOTEMPTYOSR = 7


def pio_jmp(address: int, cond: int = 0, delay: int = 0) -> int:
    return ((delay & 0x1F) << 8) | ((cond & 0x7) << 5) | (address & 0x1F)


def pio_wait(polarity: bool, src: int, index: int, delay: int = 0) -> int:
    return (1 << 13) | ((delay & 0x1F) << 8) | ((1 if polarity else 0) << 7) | ((src & 0x3) << 5) | (index & 0x1F)


def pio_in(src: int, bit_count: int, delay: int = 0) -> int:
    return (2 << 13) | ((delay & 0x1F) << 8) | ((src & 0x7) << 5) | (bit_count & 0x1F)


def pio_out(dest: int, bit_count: int, delay: int = 0) -> int:
    return (3 << 13) | ((delay & 0x1F) << 8) | ((dest & 0x7) << 5) | (bit_count & 0x1F)


def pio_push(if_full: bool, no_block: bool, delay: int = 0) -> int:
    return (4 << 13) | ((delay & 0x1F) << 8) | ((1 if if_full else 0) << 6) | ((1 if no_block else 0) << 5)


def pio_pull(if_empty: bool, no_block: bool, delay: int = 0) -> int:
    return (4 << 13) | ((delay & 0x1F) << 8) | (1 << 7) | ((1 if if_empty else 0) << 6) | ((1 if no_block else 0) << 5)


def pio_mov(dest: int, src: int, op: int = 0, delay: int = 0) -> int:
    return (5 << 13) | ((delay & 0x1F) << 8) | ((dest & 0x7) << 5) | ((op & 0x3) << 3) | (src & 0x7)


def pio_irq(clear: bool, wait: bool, index: int, delay: int = 0) -> int:
    return (6 << 13) | ((delay & 0x1F) << 8) | ((1 if clear else 0) << 6) | ((1 if wait else 0) << 5) | (index & 0x1F)


def pio_set(dest: int, data: int, delay: int = 0) -> int:
    return (7 << 13) | ((delay & 0x1F) << 8) | ((dest & 0x7) << 5) | (data & 0x1F)

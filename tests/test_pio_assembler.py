from rp2040py.utils.pio_assembler import (
    PIO_COND_PIN,
    PIO_DEST_Y,
    PIO_MOV_DEST_X,
    PIO_OP_INVERT,
    PIO_SRC_STATUS,
    PIO_SRC_X,
    PIO_WAIT_SRC_GPIO,
    pio_in,
    pio_irq,
    pio_jmp,
    pio_mov,
    pio_out,
    pio_pull,
    pio_push,
    pio_set,
    pio_wait,
)


def test_jmp_pin():
    assert pio_jmp(5, PIO_COND_PIN) == 0xC5


def test_wait_gpio():
    assert pio_wait(True, PIO_WAIT_SRC_GPIO, 12) == 0x208C


def test_in_x():
    assert pio_in(PIO_SRC_X, 12) == 0x402C


def test_out_y():
    assert pio_out(PIO_DEST_Y, 30) == 0x605E


def test_push_iffull_noblock():
    assert pio_push(True, True, 12) == 0x8C60


def test_pull_block():
    assert pio_pull(True, False) == 0x80C0


def test_mov_invert_status():
    assert pio_mov(PIO_MOV_DEST_X, PIO_SRC_STATUS, PIO_OP_INVERT) == 0xA02D


def test_irq_set():
    assert pio_irq(False, False, 4) == 0xC004


def test_set_x():
    assert pio_set(PIO_MOV_DEST_X, 12) == 0xE02C

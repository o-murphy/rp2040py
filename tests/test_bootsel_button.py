"""BOOTSEL button - see docs/records/0051-bootsel-button.md.

Wired to `GPIO_QSPI_SS`, not to any ordinary GPIO, and active-low: pressed shorts the line to
ground, released hands it back to that pad's pull-up (0050).
"""

from rp2040py.boards import BOARDS, build_rp2040
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.sio import GPIO_HI_IN

SS_BIT = 1 << 1


def _ss_reads_high(rp2040) -> bool:
    return bool(rp2040.sio.read_uint32(GPIO_HI_IN) & SS_BIT)


def test_press_pulls_qspi_ss_low_and_release_lets_the_pull_up_win(rp2040_factory):
    rp2040 = rp2040_factory()
    button = BootselButton()
    button.attach(rp2040)

    assert button.is_pressed is False
    assert _ss_reads_high(rp2040), "an untouched button is high-Z; the pad's pull-up decides"

    button.press()
    assert button.is_pressed is True
    assert not _ss_reads_high(rp2040)

    button.release()
    assert button.is_pressed is False
    assert _ss_reads_high(rp2040)


def test_release_stops_driving_rather_than_forcing_the_line_high(rp2040_factory):
    """The distinction matters: forcing it high would read the same but would mask a regression in
    the pad defaults this device exists to exercise."""
    rp2040 = rp2040_factory()
    button = BootselButton()
    button.attach(rp2040)

    button.press()
    assert rp2040.qspi[1]._driven is True
    button.release()
    assert rp2040.qspi[1]._driven is False


def test_click_leaves_the_button_released(rp2040_factory):
    rp2040 = rp2040_factory()
    button = BootselButton()
    button.attach(rp2040)
    button.click()
    assert button.is_pressed is False
    assert _ss_reads_high(rp2040)


def test_using_the_button_before_attach_is_an_error():
    button = BootselButton()
    try:
        button.press()
    except RuntimeError as exc:
        assert "not attached" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_both_boards_include_one_in_their_spec():
    """Identical wiring on every RP2040 board that boots from QSPI flash, so both get it.

    Asserted against `BOARDS` rather than by reading the pad afterwards: an undriven SS reads high
    from its own pull-up whether or not a button is attached, so a pad-level assertion here would
    pass even with `BootselButton` removed from both boards - it would guard nothing.
    """
    for board in ("pico", "pico_w"):
        assert any(extra is BootselButton for extra in BOARDS[board].extras), board


def test_a_board_built_from_the_spec_still_reads_ss_high(rp2040_factory):
    """Companion to the above: attaching the button at board-build time must not itself drive the
    line (see `attach()`), so a freshly built board still sees BOOTSEL as released."""
    for board in ("pico", "pico_w"):
        mcu = build_rp2040(board)
        assert bool(mcu.sio.read_uint32(GPIO_HI_IN) & SS_BIT), board

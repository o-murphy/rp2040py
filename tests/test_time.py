from datetime import datetime

from rp2040py.utils.time import format_time


def test_format_time_pads_microseconds_with_spaces():
    assert format_time(datetime(2020, 11, 10, 4, 55, 2, 12000)) == "04:55:02.12 "

from rp2040py.utils.fifo import FIFO


def test_push_and_pull_4_items():
    fifo = FIFO(3)
    assert fifo.empty is True
    fifo.push(1)
    assert fifo.empty is False
    fifo.push(2)
    assert fifo.item_count == 2
    assert fifo.full is False
    fifo.push(3)
    assert fifo.full is True
    assert fifo.pull() == 1
    assert fifo.full is False
    fifo.push(4)
    assert fifo.full is True
    assert fifo.pull() == 2
    assert fifo.pull() == 3
    assert fifo.empty is False
    assert fifo.item_count == 1
    assert fifo.pull() == 4
    assert fifo.full is False
    assert fifo.empty is True


def test_peek_returns_next_item_without_affecting_content():
    fifo = FIFO(3)
    assert fifo.empty is True
    fifo.push(10)
    assert fifo.empty is False
    fifo.push(20)
    assert fifo.peek() == 10
    assert fifo.item_count == 2
    assert fifo.pull() == 10


def test_items_returns_all_fifo_content():
    fifo = FIFO(3)
    assert fifo.empty is True
    fifo.push(10)
    fifo.push(20)
    fifo.push(30)
    fifo.pull()
    assert fifo.items == [20, 30]

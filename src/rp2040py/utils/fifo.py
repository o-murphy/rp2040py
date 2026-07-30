class FIFO:
    def __init__(self, size: int):
        self.buffer = [0] * size
        self._start = 0
        self._used = 0

    @property
    def size(self) -> int:
        return len(self.buffer)

    @property
    def item_count(self) -> int:
        return self._used

    def push(self, value: int) -> None:
        length = len(self.buffer)
        start, used = self._start, self._used
        if self._used < length:
            self.buffer[(start + used) % length] = value & 0xFFFFFFFF
            self._used += 1

    def pull(self) -> int:
        start, used = self._start, self._used
        length = len(self.buffer)
        if used:
            self._start = (start + 1) % length
            self._used -= 1
            return self.buffer[start]
        return 0

    def peek(self) -> int:
        return self.buffer[self._start] if self._used else 0

    def reset(self) -> None:
        self._used = 0

    @property
    def empty(self) -> bool:
        return self._used == 0

    @property
    def full(self) -> bool:
        return self._used == len(self.buffer)

    @property
    def items(self) -> list[int]:
        start, used, buffer = self._start, self._used, self.buffer
        length = len(buffer)
        return [buffer[(start + i) % length] for i in range(used)]

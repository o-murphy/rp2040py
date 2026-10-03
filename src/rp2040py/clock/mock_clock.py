from rp2040py.clock.simulation_clock import SimulationClock

__all__ = ("MockClock",)


class MockClock(SimulationClock):
    def advance(self, delta_micros: float) -> None:
        # tick() takes a *delta*; passing `self.nanos + ...` here advanced the clock by its whole current
        # reading again on every call (advance(1) twice gave 1 us, then 3 us).
        self.tick(delta_micros * 1000)

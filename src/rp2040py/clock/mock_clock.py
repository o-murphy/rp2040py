from rp2040py.clock.simulation_clock import SimulationClock

__all__ = ("MockClock",)


class MockClock(SimulationClock):
    def advance(self, delta_micros: float) -> None:
        self.tick(self.nanos + delta_micros * 1000)

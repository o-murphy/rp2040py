// The batch loop of the simulator in C++ (docs/records/0096-cpp-mcu-core.md, Phase 2 step 4c): what `Simulator._execute_batch()` does
// between two yields to the event loop - run CPU instructions (or jump an idle core straight to the next alarm), keep simulated time
// in step by ticking the clock, step the PIO blocks, and stop at the instruction ceiling, the real-time budget or a stop request. A
// translation of `native/_simulator.pyx`'s loop, itself a translation of `_execute_batch.py` (the pure-Python reference).
//
// Per iteration the loop touches no Python at all unless something it drives does: the stop request is one byte that `Simulator.stopped`
// (a property over a one-byte buffer) writes; each PIO's `stopped` is read through the address of its field; only a PIO that is running
// costs a host call. A clock alarm, a peripheral window or `on_break` that runs Python does so through the host functions those already
// have, and parks any error (see cpu.hpp's failure model).
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract, except that the stop byte may be written by
// another thread (it is read as volatile).
#ifndef RP2040PY_CORE_BATCH_HPP
#define RP2040PY_CORE_BATCH_HPP

#include <cstdint>
#include <limits>

#include "clock.hpp"
#include "cpu.hpp"

namespace rp2040core {

constexpr int kBatchFault = -1;

struct BatchHost {
    const volatile uint8_t* stopped = nullptr;  // nonzero: leave at the next iteration
    // Optional. Nonzero: some device is waiting on the *real* world (a relayed DNS/NTP reply, a TCP connect in flight), so an idle jump may not outrun the wall clock -
    // it is capped at `BatchParams::paced_idle_nanos` and ends the batch, and the caller sleeps for the simulated time the batch covered (`Simulator.execute()`).
    // Null: never paced.
    const volatile uint8_t* real_io = nullptr;
    // PIO blocks stepped once per iteration, by the system clocks that iteration covered. `pio_stopped[i]` points at block i's own
    // "stopped" flag; `pio_advance` is called only for a block that is not stopped and returns false if it failed (parked).
    static constexpr int kMaxPio = 4;
    int pio_count = 0;
    const volatile int* pio_stopped[kMaxPio] = {nullptr, nullptr, nullptr, nullptr};
    bool (*pio_advance)(void* ctx, int index, int64_t cycles) = nullptr;
    double (*monotonic)(void* ctx) = nullptr;  // seconds; the real-time budget is checked every `check_interval` iterations
    void* ctx = nullptr;
};

struct BatchParams {
    int tick_batch = 1;                 // instructions whose time is accumulated before the clock is ticked (1: tick after each)
    double cycle_nanos = 1e9 / 125e6;   // one system clock
    long instruction_ceiling = 1000000;
    double yield_budget_seconds = 0.005;
    int check_interval = 256;
    double paced_idle_nanos = 1e6;      // the longest idle jump while `real_io` is set (1 ms of simulated time)
};

// Runs one batch. Returns 0, or kBatchFault when a clock alarm or the CPU failed (the Python error is parked; the pending tick time
// is *not* flushed, as an exception would not have flushed it).
inline int run_batch(Cpu& cpu, Clock& clock, const BatchHost& host, const BatchParams& params) noexcept {
    constexpr double kInfinity = std::numeric_limits<double>::infinity();
    long i = 0;
    int ticks_since_check = 0;
    const double batch_start = host.monotonic(host.ctx);
    double pending_nanos = 0.0;
    int pending_count = 0;
    double nanos_budget = clock.has_alarm() ? clock.nanos_to_next_alarm() : kInfinity;
    // The flag addresses in registers instead of reloaded from the host struct on every iteration (the flags themselves are still
    // read every time: a firmware write that starts a PIO must make it step on the very next instruction).
    const volatile int* const pio_flag0 = host.pio_count > 0 ? host.pio_stopped[0] : nullptr;
    const volatile int* const pio_flag1 = host.pio_count > 1 ? host.pio_stopped[1] : nullptr;
    const int pio_rest = host.pio_count > 2 ? host.pio_count : 0;

    while (i < params.instruction_ceiling && !*host.stopped) {
        ticks_since_check += 1;
        if (ticks_since_check >= params.check_interval) {
            ticks_since_check = 0;
            if (host.monotonic(host.ctx) - batch_start > params.yield_budget_seconds) break;
        }
        int64_t cycles;
        bool paced = false;
        if (cpu.waiting) {
            if (pending_nanos != 0.0) {
                if (!clock.tick(pending_nanos)) return kBatchFault;
                pending_nanos = 0.0;
                pending_count = 0;
            }
            double idle_nanos = clock.nanos_to_next_alarm();
            if (host.real_io != nullptr && *host.real_io != 0) {
                if (idle_nanos > params.paced_idle_nanos) idle_nanos = params.paced_idle_nanos;
                paced = true;
            }
            if (!clock.tick(idle_nanos)) return kBatchFault;
            // The system clocks that jump covered, floored at one: an idle jump with no alarm advances time by nothing, and a PIO
            // frozen whenever the CPU parks in WFI with an empty alarm queue could never make the progress that wakes it again.
            cycles = static_cast<int64_t>(idle_nanos / params.cycle_nanos);
            if (cycles == 0) cycles = 1;
            if (params.tick_batch > 1) nanos_budget = clock.has_alarm() ? clock.nanos_to_next_alarm() : kInfinity;
        } else {
            const int executed = cpu.execute();
            if (executed < 0) return kBatchFault;
            cycles = executed;
            const double delta_nanos = static_cast<double>(cycles) * params.cycle_nanos;
            if (params.tick_batch <= 1) {
                if (!clock.tick(delta_nanos)) return kBatchFault;
            } else {
                pending_nanos += delta_nanos;
                pending_count += 1;
                nanos_budget -= delta_nanos;
                if (nanos_budget <= 0 || pending_count >= params.tick_batch) {
                    if (!clock.tick(pending_nanos)) return kBatchFault;
                    pending_nanos = 0.0;
                    pending_count = 0;
                    nanos_budget = clock.has_alarm() ? clock.nanos_to_next_alarm() : kInfinity;
                }
            }
        }
        if (pio_flag0 != nullptr && !*pio_flag0 && !host.pio_advance(host.ctx, 0, cycles)) return kBatchFault;
        if (pio_flag1 != nullptr && !*pio_flag1 && !host.pio_advance(host.ctx, 1, cycles)) return kBatchFault;
        for (int p = 2; p < pio_rest; ++p) {
            if (!*host.pio_stopped[p] && !host.pio_advance(host.ctx, p, cycles)) return kBatchFault;
        }
        i += 1;
        if (paced) break;  // one capped idle jump per batch: the caller sleeps for the simulated time it covered
    }
    if (pending_nanos != 0.0) {
        if (!clock.tick(pending_nanos)) return kBatchFault;
    }
    return 0;
}

}  // namespace rp2040core

#endif  // RP2040PY_CORE_BATCH_HPP

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
#include <cmath>
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
    // Optional. Nonzero: `clk_sys` changed since the batch began, so the cycle period in `BatchParams::cycle_nanos` is stale - the batch ends at the next iteration (whatever time is pending is flushed
    // at the old rate, which is what the instructions that accrued it cost) and the caller starts the next one at the new rate. Null: the period is fixed for the batch.
    const volatile uint8_t* clk_changed = nullptr;
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
    int quantum_cycles = 16;            // two cores only (batch_dual.hpp): the cycles a core runs ahead of the other between synchronisation points; 1 is the exact scheduler
};

// Why a batch ended (`BatchResult::reason`). Exactly one holds; they are checked in this order.
enum BatchStop : int {
    kStopFault = -1,         // a clock alarm, the CPU or a host call failed (the host has parked its error)
    kStopCeiling = 0,        // `instruction_ceiling` iterations done
    kStopRequested = 1,      // the stop byte was set
    kStopClockChanged = 2,   // `clk_sys` changed: the cycle period in the params is stale, start the next batch at the new rate
    kStopBudget = 3,         // the real-time budget (`yield_budget_seconds`) was used up
    kStopPaced = 4,          // one capped idle jump while a device waits on the real world: the caller sleeps for the simulated time it covered
};

// What a batch did, for a host that wants more than "it ran" (the Python `Simulator` does not read it; a wasm or FFI host paces and reports from it).
struct BatchResult {
    int reason = kStopCeiling;
    long iterations = 0;          // loop iterations: an instruction, or an idle jump
    int64_t ticks_advanced = 0;   // simulated time the batch covered, in clock ticks (1/16 ns)
};

// Runs one batch. Returns 0, or kBatchFault when a clock alarm or the CPU failed (the Python error is parked; the pending tick time
// is *not* flushed, as an exception would not have flushed it). `result`, if given, says why it ended and what it covered.
inline int run_batch(Cpu& cpu, Clock& clock, const BatchHost& host, const BatchParams& params, BatchResult* result = nullptr) noexcept {
    constexpr int64_t kNoAlarm = std::numeric_limits<int64_t>::max();
    // One system clock in ticks: not a whole number in general (133 MHz is 120.30), so time is advanced in CYCLES with the fraction carried by the clock.
    const double ticks_per_cycle = params.cycle_nanos * static_cast<double>(kTicksPerNano);
    // The common case (125 MHz: 128 ticks) is a whole number of ticks per cycle, and then time is plain integer arithmetic - no remainder to carry.
    const bool whole_ticks = ticks_per_cycle == std::floor(ticks_per_cycle) && ticks_per_cycle < 1e12;
    const int64_t cycle_ticks = whole_ticks ? static_cast<int64_t>(ticks_per_cycle) : 0;
    auto advance = [&](int64_t n) noexcept { return whole_ticks ? clock.defer_ticks(n * cycle_ticks) : clock.advance_cycles(n, ticks_per_cycle); };
    long i = 0;
    const int64_t start_ticks = clock.ticks();
    int stop_reason = kStopCeiling;
    // Every exit reports through here: the reason, the iterations and the simulated time covered (stores only; nothing in the loop reads them).
    auto done = [&](int status, int reason) noexcept {
        if (result != nullptr) {
            result->reason = reason;
            result->iterations = i;
            result->ticks_advanced = clock.ticks() - start_ticks;
        }
        return status;
    };
    int ticks_since_check = 0;
    const double batch_start = host.monotonic(host.ctx);
    int64_t pending_cycles = 0;
    int pending_count = 0;
    double idle_carry = 0.0;  // idle time, in ticks, not yet handed to the PIO as whole cycles
    int64_t tick_budget = clock.has_alarm() ? clock.ticks_to_next_alarm() : kNoAlarm;
    // The flag addresses in registers instead of reloaded from the host struct on every iteration (the flags themselves are still
    // read every time: a firmware write that starts a PIO must make it step on the very next instruction).
    const volatile int* const pio_flag0 = host.pio_count > 0 ? host.pio_stopped[0] : nullptr;
    const volatile int* const pio_flag1 = host.pio_count > 1 ? host.pio_stopped[1] : nullptr;
    const int pio_rest = host.pio_count > 2 ? host.pio_count : 0;

    while (i < params.instruction_ceiling && !*host.stopped && !(host.clk_changed != nullptr && *host.clk_changed != 0)) {
        ticks_since_check += 1;
        if (ticks_since_check >= params.check_interval) {
            ticks_since_check = 0;
            if (host.monotonic(host.ctx) - batch_start > params.yield_budget_seconds) {
                stop_reason = kStopBudget;
                break;
            }
        }
        int64_t cycles;
        bool paced = false;
        if (cpu.waiting) {
            if (pending_cycles != 0) {
                if (!advance(pending_cycles)) return done(kBatchFault, kStopFault);
                pending_cycles = 0;
                pending_count = 0;
            }
            int64_t idle_ticks = clock.ticks_to_next_alarm();
            if (host.real_io != nullptr && *host.real_io != 0) {
                const int64_t cap = nanos_to_ticks(params.paced_idle_nanos);
                if (idle_ticks > cap) idle_ticks = cap;
                paced = true;
            }
            if (!clock.tick_ticks(idle_ticks)) return done(kBatchFault, kStopFault);
            // The system clocks that jump covered, floored at one: an idle jump with no alarm advances time by nothing, and a PIO
            // frozen whenever the CPU parks in WFI with an empty alarm queue could never make the progress that wakes it again.
            // The fraction of a cycle a jump leaves is CARRIED to the next jump (as the CPU's own time carries it): flooring each jump on its own dropped it every time, and on a clock that is not a whole
            // number of ticks per cycle (133 MHz) a PIO behind many short jumps ran slow against the clock (record 0105, finding 2).
            idle_carry += static_cast<double>(idle_ticks);
            cycles = static_cast<int64_t>(idle_carry / ticks_per_cycle);
            idle_carry -= static_cast<double>(cycles) * ticks_per_cycle;
            if (cycles == 0) cycles = 1;
            if (params.tick_batch > 1) tick_budget = clock.has_alarm() ? clock.ticks_to_next_alarm() : kNoAlarm;
        } else {
            const int executed = cpu.execute();
            if (executed < 0) return done(kBatchFault, kStopFault);
            cycles = executed;
            if (params.tick_batch <= 1) {
                if (!advance(cycles)) return done(kBatchFault, kStopFault);
            } else {
                pending_cycles += cycles;
                pending_count += 1;
                // Rounded UP: an alarm is never flushed later than it is due, only (by under a tick) earlier.
                if (tick_budget != kNoAlarm) tick_budget -= static_cast<int64_t>(std::ceil(static_cast<double>(cycles) * ticks_per_cycle));
                if (tick_budget <= 0 || pending_count >= params.tick_batch) {
                    if (!advance(pending_cycles)) return done(kBatchFault, kStopFault);
                    pending_cycles = 0;
                    pending_count = 0;
                    tick_budget = clock.has_alarm() ? clock.ticks_to_next_alarm() : kNoAlarm;
                }
            }
        }
        if (pio_flag0 != nullptr && !*pio_flag0 && !host.pio_advance(host.ctx, 0, cycles)) return done(kBatchFault, kStopFault);
        if (pio_flag1 != nullptr && !*pio_flag1 && !host.pio_advance(host.ctx, 1, cycles)) return done(kBatchFault, kStopFault);
        for (int p = 2; p < pio_rest; ++p) {
            if (!*host.pio_stopped[p] && !host.pio_advance(host.ctx, p, cycles)) return done(kBatchFault, kStopFault);
        }
        i += 1;
        if (paced) {  // one capped idle jump per batch: the caller sleeps for the simulated time it covered
            stop_reason = kStopPaced;
            break;
        }
    }
    if (stop_reason == kStopCeiling && i < params.instruction_ceiling) {  // the loop condition ended it
        stop_reason = *host.stopped ? kStopRequested : kStopClockChanged;
    }
    if (pending_cycles != 0) {
        if (!advance(pending_cycles)) return done(kBatchFault, kStopFault);
    }
    return done(0, stop_reason);
}

}  // namespace rp2040core

#endif  // RP2040PY_CORE_BATCH_HPP

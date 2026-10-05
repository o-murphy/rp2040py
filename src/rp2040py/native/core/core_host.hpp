// What every peripheral block of the C++ core shares (docs/records/0096-cpp-mcu-core.md, "Core host contract"): so that a new block states its registers and its quirks and
// nothing else, instead of repeating the same fifty lines of glue.
//
// The contract, which the blocks follow and the wasm/FFI hosts will implement:
//
//   * A block never throws, never allocates, never calls a function it was not handed. Everything outside it - an interrupt line, a DREQ, a logger, a callback of the
//     embedding program - is reached through a `...Host` struct of plain function pointers plus one `void* ctx`, filled in by the embedder before `init()`.
//   * A host function that can fail (a Python callback may raise, a JS callback may throw) returns `false`, or a block checks `host_failed()` after it: the host
//     has parked its own error object and raised the shared failure flag. The block returns `false` at once, leaving *exactly* the state the reference implementation's
//     exception would have left - what had been done before the failing call stays done, nothing after it happens. Entry points return `false` (or `kBatchFault`); the
//     embedder then re-raises its parked error. The core knows nothing about what the error is.
//   * Registers are reached through `read(offset)` / `write(offset, value)` / `write_atomic(offset, raw, alias)`, and the bus calls the block through the `WindowHandler` that
//     `BlockWindow<Block>` builds, so a peripheral that lives in C++ costs a function-pointer call and one that lives in the host costs a host call - the block cannot tell.
//   * A block that must tell the host something that is not a register access (an unimplemented register, say) reports it as data: a small `kind` and the numbers, never a
//     formatted string - the host owns the logger.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation.
#ifndef RP2040PY_CORE_CORE_HOST_HPP
#define RP2040PY_CORE_CORE_HOST_HPP

#include <cstdint>

#include "window_map.hpp"

namespace rp2040core {

// The messages a block reports for a register it does not implement; the host formats them (it owns the logger). The per-block names (`kDmaWarnRead`, ...) are aliases.
enum RegWarn : uint32_t {
    kRegWarnRead = 0,            // "Unimplemented peripheral read from 0x{offset:x}"
    kRegWarnReadAtomicArea = 1,  // "Unimplemented read from peripheral in the atomic operation region" (offset > 0x1000)
    kRegWarnWrite = 2,           // "Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}"
};
using RegWarnFn = void (*)(void* ctx, uint32_t kind, uint32_t offset, int64_t value);

// True when a host function has parked an error (the shared failure flag, see native/_pending.pyx). A null flag means the block's host cannot fail.
inline bool host_failed(const int* flag) noexcept { return flag != nullptr && *flag != 0; }

// The value an atomic alias write ends up writing: `raw` is what the bus passed, `current` a *read* of the register (with that read's side effects). The reference
// (`BasePeripheral.write_uint32_atomic`) does exactly this; a normal write, and an alias that is not one of the three, write `raw` as it is.
inline int64_t decode_atomic(uint32_t atomic_type, int64_t current, int64_t raw) noexcept {
    switch (atomic_type) {
        case kAtomicXor: return current ^ raw;
        case kAtomicSet: return current | raw;
        case kAtomicClear: return current & ~raw;
        default: return raw;
    }
}

// The bus's window entry points for any block with `uint32_t read(uint32_t)` and `write_atomic(uint32_t, int64_t, uint32_t)` (the return value, if any, is dropped: a
// failure is parked with the host and the block's next caller sees it).
template <class Block>
struct BlockWindow {
    static uint32_t read32(void* ctx, uint32_t offset) { return static_cast<Block*>(ctx)->read(offset); }
    static void write32(void* ctx, uint32_t offset, int64_t raw_value, uint32_t atomic_type) {
        (void)static_cast<Block*>(ctx)->write_atomic(offset, raw_value, atomic_type);
    }
    static WindowHandler handler(Block* block) noexcept { return WindowHandler{&BlockWindow::read32, &BlockWindow::write32, block}; }
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_CORE_HOST_HPP

// What every peripheral block of the C++ core shares (docs/records/0096-cpp-mcu-core.md, "Core host contract"): so that a new block states its registers and its quirks and
// nothing else, instead of repeating the same fifty lines of glue.
//
// The contract, which the blocks follow and the wasm/FFI hosts will implement:
//
//   * A block never throws, never allocates, never calls a function it was not handed. What the chip gives every block - an interrupt line, a DREQ, the warnings, the failure flag - is
//     the chip's one `ChipPorts` (below), with the block's own id, line and first request as data; what is really the world's - a byte on a wire, a device on a bus, an oscillator's
//     frequency - is a small `...Host` struct of plain function pointers plus one `void* ctx`, filled in by the embedder before `init()`.
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

// The messages a block reports for a register it does not implement; the host formats them (it owns the logger). The per-block names (`kRegWarnRead`, ...) are aliases.
enum RegWarn : uint32_t {
    kRegWarnRead = 0,            // "Unimplemented peripheral read from 0x{offset:x}"
    kRegWarnReadAtomicArea = 1,  // "Unimplemented read from peripheral in the atomic operation region" (offset > 0x1000)
    kRegWarnWrite = 2,           // "Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}"
};
using RegWarnFn = void (*)(void* ctx, uint32_t kind, uint32_t offset, int64_t value);

// True when a host function has parked an error (the shared failure flag, see native/_pending.pyx). A null flag means the block's host cannot fail.
inline bool host_failed(const int* flag) noexcept { return flag != nullptr && *flag != 0; }

// What the chip gives every block (docs/records/0096-cpp-mcu-core.md, the clean-up before wasm): ONE set of lines owned by the `Mcu`, instead of a `...Host` of adapters per block. A block
// is handed `&ports` and *data* - its `McuBlock` id (and, for a block with a line, its IRQ line) - and calls through it; the `Mcu` writes no per-block adapter. The block's own `...Host` keeps
// only what is really the world's (a UART's `on_byte`, an SPI's `transmit`, ...). `warn` is the host's: it gets the block id and the numbers, never a string. `failed` is the shared parked-failure flag.
struct ChipPorts {
    void (*warn)(void* chip, uint32_t block, uint32_t kind, uint32_t offset, int64_t value) = nullptr;
    void* chip = nullptr;
    const int* failed = nullptr;
    bool (*irq)(void* chip, uint32_t line, bool level) = nullptr;       // an NVIC line (false: a failure is parked)
    bool (*dreq)(void* chip, uint32_t number, bool asserted) = nullptr;  // a DMA request
};

// A block's end of `ChipPorts`: the pointer and the block's own id, and the two messages every register-only block sends for an offset it does not implement.
class BlockPort {
public:
    // `irq_line` is the block's NVIC line (the first of them, for a block with several), `dreq_base` its first DMA request: data the chip gives the block, not an adapter.
    void init(const ChipPorts* ports, uint32_t block, uint32_t irq_line = 0, uint32_t dreq_base = 0) noexcept {
        ports_ = ports;
        block_ = block;
        irq_line_ = irq_line;
        dreq_base_ = dreq_base;
    }
    bool failed() const noexcept { return ports_ != nullptr && host_failed(ports_->failed); }
    void warn(uint32_t kind, uint32_t offset, int64_t value) const noexcept {
        if (ports_ != nullptr && ports_->warn != nullptr) ports_->warn(ports_->chip, block_, kind, offset, value);
    }
    // "Unimplemented peripheral read" and, above the alias window (offset > 0x1000), the atomic-area message as well.
    void unimplemented_read(uint32_t offset) const noexcept {
        warn(kRegWarnRead, offset, 0);
        if (offset > 0x1000) warn(kRegWarnReadAtomicArea, offset, 0);
    }
    void unimplemented_write(uint32_t offset, int64_t value) const noexcept { warn(kRegWarnWrite, offset, value); }
    // The block's line (`index` counts from the first) and its DMA request (`index` from the first). True when the chip has no such port: a block on its own raises nothing.
    bool irq(bool level, uint32_t index = 0) const noexcept { return ports_ == nullptr || ports_->irq == nullptr || ports_->irq(ports_->chip, irq_line_ + index, level); }
    bool dreq(uint32_t index, bool asserted) const noexcept { return ports_ == nullptr || ports_->dreq == nullptr || ports_->dreq(ports_->chip, dreq_base_ + index, asserted); }

private:
    const ChipPorts* ports_ = nullptr;
    uint32_t block_ = 0, irq_line_ = 0, dreq_base_ = 0;
};

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

// The entry points of a `RegisterBlock` (core_host.hpp) with the block's type erased: what the Cython shells' common base (`native/_native_block.pyx`) calls, so that a shell does not repeat
// `read_uint32` / `write_uint32` / `write_uint32_atomic` / `_native_window` / `reset` per block (docs/records/0096-cpp-mcu-core.md, Phase 5b, "fewer abstractions").
//
// A host-side convenience, not part of the core's contract: a call through it is one function-pointer hop, on the Python-facing path only (a firmware access goes through the bus's window).
// `write` and `reset` drop their result, a failure being parked with the host (core_host.hpp) and re-raised by the shell right after the call, whichever way the block reports it.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation.
#ifndef RP2040PY_CORE_REGISTER_OPS_HPP
#define RP2040PY_CORE_REGISTER_OPS_HPP

#include <cstdint>

#include "core_host.hpp"

namespace rp2040core {

struct RegisterOps {
    void* block = nullptr;
    uint32_t (*read)(void* block, uint32_t offset) = nullptr;
    void (*write)(void* block, uint32_t offset, int64_t value) = nullptr;
    void (*write_atomic)(void* block, uint32_t offset, int64_t raw, uint32_t atomic_type) = nullptr;
    int64_t (*raw_write_value)(void* block) = nullptr;
    void (*reset)(void* block) = nullptr;
    WindowHandler (*window)(void* block) = nullptr;
};

template <class Block>
struct RegisterOpsOf {
    static uint32_t read(void* b, uint32_t offset) { return static_cast<Block*>(b)->read(offset); }
    static void write(void* b, uint32_t offset, int64_t value) { (void)static_cast<Block*>(b)->write(offset, value); }
    static void write_atomic(void* b, uint32_t offset, int64_t raw, uint32_t atomic_type) { (void)static_cast<Block*>(b)->write_atomic(offset, raw, atomic_type); }
    static int64_t raw_write_value(void* b) { return static_cast<Block*>(b)->raw_write_value(); }
    static void reset(void* b) { reset_if_any(static_cast<Block*>(b), 0); }
    static WindowHandler window(void* b) { return static_cast<Block*>(b)->window_handler(); }

private:
    // A block's `reset` returns bool or nothing, and a block with no state has none: then this does nothing.
    template <class B>
    static auto reset_if_any(B* block, int) -> decltype((void)block->reset()) {
        (void)block->reset();
    }
    template <class B>
    static void reset_if_any(B*, long) {}
};

template <class Block>
RegisterOps register_ops(Block* block) noexcept {
    RegisterOps ops;
    ops.block = block;
    ops.read = &RegisterOpsOf<Block>::read;
    ops.write = &RegisterOpsOf<Block>::write;
    ops.write_atomic = &RegisterOpsOf<Block>::write_atomic;
    ops.raw_write_value = &RegisterOpsOf<Block>::raw_write_value;
    ops.reset = &RegisterOpsOf<Block>::reset;
    ops.window = &RegisterOpsOf<Block>::window;
    return ops;
}

}  // namespace rp2040core

#endif  // RP2040PY_CORE_REGISTER_OPS_HPP

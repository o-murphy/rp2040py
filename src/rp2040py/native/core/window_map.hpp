// The peripheral window registry of the C++ MCU core (docs/records/0096-cpp-mcu-core.md, Phase 1, D2).
//
// Peripherals live in 16 KiB windows of the address space (the bus has always looked them up by
// `address >> 14`). A window is served by a HANDLER: a pair of plain function pointers plus an opaque
// `ctx`. Today every handler is a trampoline into a Python peripheral object; when a block moves to C++
// (Phase 2) it registers its own functions here instead, and a Python window can still be attached over
// the same address to replace it - attach() on an occupied window REPLACES the handler, so "the last one
// attached wins" is the whole override rule.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation (the table is a fixed array). Not thread-safe:
// the core is single-threaded by contract. Handlers must not throw:
// a Python trampoline catches its exception and the Cython caller re-raises it after the lookup returns.
#ifndef RP2040PY_CORE_WINDOW_MAP_HPP
#define RP2040PY_CORE_WINDOW_MAP_HPP

#include <cstdint>

namespace rp2040core {

constexpr uint32_t kWindowShift = 14;                        // 16 KiB windows
constexpr uint32_t kWindowOffsetMask = (1u << kWindowShift) - 1;
constexpr uint32_t kAtomicShift = 12;                        // address bits 12-13 select the alias
constexpr uint32_t kAtomicMask = 0x3u << kAtomicShift;
constexpr uint32_t kRegisterOffsetMask = 0xFFFu;             // the register offset inside a write

enum AtomicType : uint32_t { kAtomicNormal = 0, kAtomicXor = 1, kAtomicSet = 2, kAtomicClear = 3 };

// A read gets the offset inside the 16 KiB window *including* the alias bits (so a block can still
// complain about a read in the atomic-alias region). A write gets the register offset (low 12 bits), the
// alias as a separate `atomic_type`, and the value exactly as the caller supplied it: `raw_value` is a
// full 64-bit integer, not a uint32, because blocks written against the Python bus (TIMER, IO, UART, DMA,
// USB read `raw_write_value`; SIO's divider needs the sign) have always seen the unmasked number. A native
// block simply uses the low 32 bits.
//
// Handlers must not throw. The pointer types are deliberately NOT declared `noexcept`: Cython does not emit
// that specifier, and a Cython trampoline has to be assignable here. A C++ handler may (and should) be
// `noexcept`, which converts to these types; one that throws anyway reaches the `noexcept` WindowMap
// methods below and terminates, which is the same outcome the specifier would have forced.
using Read32Fn = uint32_t (*)(void* ctx, uint32_t offset);
using Write32Fn = void (*)(void* ctx, uint32_t offset, int64_t raw_value, uint32_t atomic_type);

struct WindowHandler {
    Read32Fn read32;
    Write32Fn write32;
    void* ctx;
};

constexpr int kNoWindow = -1;

class WindowMap {
public:
    static constexpr int kMaxWindows = 128;

    // Attaches (or replaces) the handler of the window containing `address`. Returns the window's slot, or
    // kNoWindow if the handler is unusable (a null function) or the table is full. The slot of a window is
    // stable until it is detached.
    int attach(uint32_t address, const WindowHandler& handler) noexcept {
        if (handler.read32 == nullptr || handler.write32 == nullptr) return kNoWindow;
        const uint32_t key = address >> kWindowShift;
        int slot = find(key);
        if (slot == kNoWindow) {
            if (count_ >= kMaxWindows) return kNoWindow;
            slot = count_++;
            keys_[slot] = key;
        }
        handlers_[slot] = handler;
        return slot;
    }

    // Removes the window containing `address`; returns whether there was one.
    bool detach(uint32_t address) noexcept {
        const int slot = find(address >> kWindowShift);
        if (slot == kNoWindow) return false;
        --count_;
        keys_[slot] = keys_[count_];  // keep the table dense: move the last window into the hole
        handlers_[slot] = handlers_[count_];
        last_ = 0;
        return true;
    }

    void clear() noexcept {
        count_ = 0;
        last_ = 0;
    }

    int count() const noexcept { return count_; }
    bool has(uint32_t address) const noexcept { return find(address >> kWindowShift) != kNoWindow; }

    // Returns the slot that served the access, or kNoWindow (the caller then tries whatever else it has).
    int read32(uint32_t address, uint32_t* out) const noexcept {
        const int slot = find(address >> kWindowShift);
        if (slot == kNoWindow) return kNoWindow;
        const WindowHandler& h = handlers_[slot];
        *out = h.read32(h.ctx, address & kWindowOffsetMask);
        return slot;
    }

    int write32(uint32_t address, int64_t raw_value) const noexcept {
        const int slot = find(address >> kWindowShift);
        if (slot == kNoWindow) return kNoWindow;
        const WindowHandler& h = handlers_[slot];
        h.write32(h.ctx, address & kRegisterOffsetMask, raw_value, (address & kAtomicMask) >> kAtomicShift);
        return slot;
    }

private:
    // Peripheral accesses are strongly local (a firmware polls one block in a loop), so the last window hit
    // is checked first; the fallback is a short linear scan of a dense array (a chip has ~35 windows).
    int find(uint32_t key) const noexcept {
        if (last_ < count_ && keys_[last_] == key) return last_;
        for (int i = 0; i < count_; ++i) {
            if (keys_[i] == key) {
                last_ = i;
                return i;
            }
        }
        return kNoWindow;
    }

    uint32_t keys_[kMaxWindows]{};
    WindowHandler handlers_[kMaxWindows]{};
    int count_ = 0;
    mutable int last_ = 0;  // a cache, so lookups stay const
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_WINDOW_MAP_HPP

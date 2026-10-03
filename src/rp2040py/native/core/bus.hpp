// The RP2040 system bus in C++ (docs/records/0096-cpp-mcu-core.md, Phase 2 step 4a): the address decoding that used to live in
// `RP2040.read_uint32` & co. of `_rp2040.pyx`, one object so the CPU (step 4b) can call it directly.
//
// Decode order and every quirk are the Cython bus's, which is the pure `_rp2040.py` bus's: boot ROM / flash (four mirrors) /
// SRAM / USB DPRAM through the caller-owned `MemoryMap`; the PPB (0xE000E000, window of 4 KiB) and SIO (0xD0000000, 256 MiB)
// through a handler each; the 16 KiB peripheral windows through `WindowMap`; anything else is a warning, not an error.
//   - a 32-bit read of an unaligned address warns and is then served as if the address were what it is;
//   - a sub-word access that no memory region serves is a read-modify-write of the aligned word - except that a write first
//     tries the window of that word with the value *replicated* across the word (a block's own sub-word semantics);
//   - a write to USB DPRAM tells the host (`dpram_written`) with the caller's unmasked value;
//   - every write hands its handler the caller's value unmasked as an int64 (SIO's divider needs the sign), a PPB/SIO read hands
//     back the 32-bit word.
// What the bus cannot do itself (log, tell the USB controller) goes through a `BusHost`. Handlers and host functions must not
// throw; a failure inside one is the *callee's* to park (Python trampolines park the exception, see _pending.pyx) and the caller
// of the bus checks for after the access returns.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_BUS_HPP
#define RP2040PY_CORE_BUS_HPP

#include <cstdint>

#include "memory_map.hpp"
#include "window_map.hpp"

namespace rp2040core {

enum BusWarn : uint32_t {
    kBusWarnUnalignedRead = 0,  // "read from address {addr:x}, which is not 32 bit aligned"
    kBusWarnInvalidRead = 1,    // "Read from invalid memory address: {addr:x}"
    kBusWarnUndefinedWrite = 2, // "Write to undefined address: {addr:x}"
};

struct BusHost {
    void (*warn)(void* ctx, uint32_t kind, uint32_t address) = nullptr;
    void (*dpram_written)(void* ctx, uint32_t offset, int64_t value) = nullptr;  // a write that landed in a kNotifyOnWrite region
    void* ctx = nullptr;
};

constexpr uint32_t kSioBase = 0xD0000000u, kSioSpan = 0x10000000u;
constexpr uint32_t kPpbKey = 0xE000Eu;  // address >> 12

class Bus {
public:
    Bus() noexcept = default;
    Bus(const Bus&) = delete;
    Bus& operator=(const Bus&) = delete;

    MemoryMap mem;
    WindowMap windows;

    void init(const BusHost& host) noexcept { host_ = host; }
    // The handlers get the offset inside their block: the address minus 0xD0000000 for SIO, `address & 0xFFF` for the PPB.
    void set_sio(const WindowHandler& handler) noexcept { sio_ = handler; has_sio_ = handler.read32 && handler.write32; }
    void set_ppb(const WindowHandler& handler) noexcept { ppb_ = handler; has_ppb_ = handler.read32 && handler.write32; }

    uint32_t read32(uint32_t addr) noexcept {
        if (addr & 0x3) warn(kBusWarnUnalignedRead, addr);
        uint32_t word;
        if (mem.read32(addr, &word) != kNotHandled) return word;
        if (has_ppb_ && (addr >> 12) == kPpbKey) return ppb_.read32(ppb_.ctx, addr & 0xFFF);
        if (has_sio_ && addr - kSioBase < kSioSpan) return sio_.read32(sio_.ctx, addr - kSioBase);
        if (windows.read32(addr, &word) != kNoWindow) return word;
        warn(kBusWarnInvalidRead, addr);
        return 0xFFFFFFFFu;
    }

    // 16- and 8-bit reads assume natural alignment, as the Python bus does.
    uint32_t read16(uint32_t addr) noexcept {
        uint32_t value;
        if (mem.read16(addr, &value) != kNotHandled) return value;
        value = read32(addr & 0xFFFFFFFCu);
        return (addr & 0x2) ? (value & 0xFFFF0000u) >> 16 : (value & 0xFFFFu);
    }

    uint32_t read8(uint32_t addr) noexcept {
        uint32_t value;
        if (mem.read8(addr, &value) != kNotHandled) return value;
        value = read16(addr & 0xFFFFFFFEu);
        return (addr & 0x1) ? (value & 0xFF00u) >> 8 : (value & 0xFFu);
    }

    void write32(uint32_t addr, int64_t value) noexcept {
        const int region = mem.write32(addr, static_cast<uint32_t>(value));
        if (region != kNotHandled) {
            if ((mem.region(region).flags & kNotifyOnWrite) && host_.dpram_written) {
                host_.dpram_written(host_.ctx, addr - mem.region(region).base, value);
            }
        } else if (has_sio_ && addr - kSioBase < kSioSpan) {
            sio_.write32(sio_.ctx, addr - kSioBase, value, kAtomicNormal);
        } else if (has_ppb_ && (addr >> 12) == kPpbKey) {
            ppb_.write32(ppb_.ctx, addr & 0xFFF, value, kAtomicNormal);
        } else if (windows.write32(addr, value) == kNoWindow) {
            warn(kBusWarnUndefinedWrite, addr);
        }
    }

    void write16(uint32_t addr, uint32_t value) noexcept {
        value &= 0xFFFFu;
        if (mem.write16(addr, value) != kNotHandled) return;
        const uint32_t aligned = addr & 0xFFFFFFFCu;
        if (windows.write32(aligned, static_cast<int64_t>(value | (value << 16))) != kNoWindow) return;
        const uint32_t shift = (addr & 0x3) * 8;
        const uint32_t word = read32(aligned);
        write32(aligned, static_cast<int64_t>((word & ~(0xFFFFu << shift)) | (value << shift)));
    }

    void write8(uint32_t addr, uint32_t value) noexcept {
        value &= 0xFFu;
        if (mem.write8(addr, value) != kNotHandled) return;
        const uint32_t aligned = addr & 0xFFFFFFFCu;
        if (windows.write32(aligned, static_cast<int64_t>(value | (value << 8) | (value << 16) | (value << 24))) != kNoWindow) return;
        const uint32_t shift = (addr & 0x3) * 8;
        const uint32_t word = read32(aligned);
        write32(aligned, static_cast<int64_t>((word & ~(0xFFu << shift)) | (value << shift)));
    }

private:
    void warn(uint32_t kind, uint32_t address) noexcept {
        if (host_.warn) host_.warn(host_.ctx, kind, address);
    }

    BusHost host_;
    WindowHandler sio_{}, ppb_{};
    bool has_sio_ = false, has_ppb_ = false;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_BUS_HPP

// The caller-owned memory map of the C++ MCU core (docs/records/0096-cpp-mcu-core.md, Phase 1, D2).
//
// A small table of regions, each a window of the 32-bit address space backed by memory that the
// CALLER allocated and keeps alive - flash, boot ROM, SRAM, USB DPRAM - so this code never
// allocates and never copies a buffer. It serves only the accesses whose answer is "read/write
// these bytes"; anything else (a peripheral, SIO, PPB, an unmapped address) is reported as "not
// handled" and stays with the caller's dispatch. Header-only, C++17, no exceptions, no RTTI, no
// STL: it must compile unchanged for the Cython extension and (record 0096, Phase 6) for a bare
// wasm32 module. Little-endian host assumed (as is every host this project builds for).
//
// The semantics here are a *translation*, not a design: they reproduce, quirks included, what
// `RP2040.read_uint32()/write_uint32()/...` did before this existed (see the per-flag notes), and
// tests/test_memory_map_parity.py holds the two implementations to the same answers.
#ifndef RP2040PY_CORE_MEMORY_MAP_HPP
#define RP2040PY_CORE_MEMORY_MAP_HPP

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace rp2040core {

enum RegionFlags : uint32_t {
    // Addresses are word-indexed: an access at `a` touches the word at `a & ~3`, never a byte-wise
    // unaligned span (the boot ROM, which is stored as 32-bit words).
    kWordIndexed = 1u << 0,
    // 8- and 16-bit accesses are served directly (flash, SRAM). Without it they are "not handled"
    // and the caller composes them from 32-bit accesses, exactly as before (boot ROM, DPRAM).
    kSubWord = 1u << 1,
    // A completed 32-bit write must be reported back to the caller, which has to run a hook (USB
    // DPRAM -> `usb_ctrl.dpram_updated`). 8/16-bit writes to such a region are never handled here:
    // the caller's read-modify-write path already reaches the hook through its 32-bit write.
    kNotifyOnWrite = 1u << 2,
};

struct Region {
    uint32_t base;    // first address of the window
    uint32_t window;  // bytes of address space 32-bit READS cover (>= size when the memory is mirrored)
    uint32_t size;    // bytes of backing memory; WRITES and sub-word accesses are only served below this
    uint32_t mask;    // a read at `a` touches data[(a - base) & mask] (0xFFFFFFFF: no mirroring)
    uint8_t* data;    // caller-owned; must stay valid and unmoved while attached
    uint32_t flags;
};

constexpr int kNotHandled = -1;

class MemoryMap {
public:
    static constexpr int kMaxRegions = 8;

    // Returns the region's index (stable until clear()), or kNotHandled if the table is full or the
    // region is unusable (null data, zero size, or a window smaller than its memory).
    int attach(const Region& region) noexcept {
        if (count_ >= kMaxRegions || region.data == nullptr || region.size == 0 || region.window < region.size) {
            return kNotHandled;
        }
        regions_[count_] = region;
        return count_++;
    }

    void clear() noexcept { count_ = 0; }
    int count() const noexcept { return count_; }
    const Region& region(int index) const noexcept { return regions_[index]; }

    // Every accessor returns the index of the region that served the access, or kNotHandled.
    // Reads fill *out; writes return the index so the caller can test kNotifyOnWrite.

    int read32(uint32_t address, uint32_t* out) const noexcept {
        for (int i = 0; i < count_; ++i) {
            const Region& r = regions_[i];
            const uint32_t rel = address - r.base;  // wraps below base into a huge value: one compare is enough
            if (rel >= r.window) continue;
            uint32_t offset = rel & r.mask;
            if (r.flags & kWordIndexed) offset &= ~3u;
            if (offset > r.size || r.size - offset < 4) return kNotHandled;
            *out = load32(r.data + offset);
            return i;
        }
        return kNotHandled;
    }

    int read16(uint32_t address, uint32_t* out) const noexcept {
        const int i = find_sub_word(address, 2);
        if (i == kNotHandled) return kNotHandled;
        uint16_t v;
        std::memcpy(&v, regions_[i].data + (address - regions_[i].base), sizeof v);
        *out = v;
        return i;
    }

    int read8(uint32_t address, uint32_t* out) const noexcept {
        const int i = find_sub_word(address, 1);
        if (i == kNotHandled) return kNotHandled;
        *out = regions_[i].data[address - regions_[i].base];
        return i;
    }

    int write32(uint32_t address, uint32_t value) const noexcept {
        for (int i = 0; i < count_; ++i) {
            const Region& r = regions_[i];
            uint32_t offset = address - r.base;
            if (offset >= r.size) continue;  // the mirror is read-only: a write above `size` is the caller's problem
            if (r.flags & kWordIndexed) offset &= ~3u;
            if (r.size - offset < 4) return kNotHandled;
            std::memcpy(r.data + offset, &value, sizeof value);
            return i;
        }
        return kNotHandled;
    }

    int write16(uint32_t address, uint32_t value) const noexcept {
        const int i = find_sub_word(address, 2);
        if (i == kNotHandled || (regions_[i].flags & kNotifyOnWrite)) return kNotHandled;
        const uint16_t v = static_cast<uint16_t>(value);
        std::memcpy(regions_[i].data + (address - regions_[i].base), &v, sizeof v);
        return i;
    }

    int write8(uint32_t address, uint32_t value) const noexcept {
        const int i = find_sub_word(address, 1);
        if (i == kNotHandled || (regions_[i].flags & kNotifyOnWrite)) return kNotHandled;
        regions_[i].data[address - regions_[i].base] = static_cast<uint8_t>(value);
        return i;
    }

private:
    static uint32_t load32(const uint8_t* p) noexcept {
        uint32_t v;
        std::memcpy(&v, p, sizeof v);
        return v;
    }

    // The region a `width`-byte sub-word access falls entirely inside, or kNotHandled. Sub-word
    // accesses never use the mirror: only the first `size` bytes of a window.
    int find_sub_word(uint32_t address, uint32_t width) const noexcept {
        for (int i = 0; i < count_; ++i) {
            const Region& r = regions_[i];
            const uint32_t offset = address - r.base;
            if (offset >= r.size) continue;
            if (!(r.flags & kSubWord) || r.size - offset < width) return kNotHandled;
            return i;
        }
        return kNotHandled;
    }

    Region regions_[kMaxRegions]{};
    int count_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_MEMORY_MAP_HPP

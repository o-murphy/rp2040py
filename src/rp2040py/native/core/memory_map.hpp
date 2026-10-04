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
        const int index = count_++;
        reindex();
        return index;
    }

    // For tests: with the fast paths off every access takes the generic walk, so the two can be compared access by access.
    void set_fast_paths(bool enabled) noexcept {
        fast_enabled_ = enabled;
        index_fast();
    }

    void clear() noexcept {
        count_ = 0;
        reindex();
    }
    int count() const noexcept { return count_; }
    const Region& region(int index) const noexcept { return regions_[index]; }

    // Every accessor returns the index of the region that served the access, or kNotHandled.
    // Reads fill *out; writes return the index so the caller can test kNotifyOnWrite.

    int read32(uint32_t address, uint32_t* out) const noexcept {
        {  // the two places the firmware lives, tried first with the bases as constants, as the pre-0096 Cython bus's chain of compares did
            const uint32_t rel = address - kFlashBase;
            if (rel < flash_.window) {
                const uint32_t offset = rel & flash_.mask;
                if (static_cast<uint64_t>(offset) + 4 <= flash_.size) {
                    *out = load32(flash_.data + offset);
                    return flash_.index;
                }
            }
            const uint32_t off = address - kRamBase;
            if (static_cast<uint64_t>(off) + 4 <= ram_.size) {
                *out = load32(ram_.data + off);
                return ram_.index;
            }
        }
        int lo, hi;
        candidates(address, &lo, &hi);
        for (int i = lo; i < hi; ++i) {
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
        {  // the fast regions hand out their own data pointer: no second lookup through the region table
            const uint32_t offset = address - kFlashBase;
            if (static_cast<uint64_t>(offset) + 2 <= flash_.size) {
                uint16_t v;
                std::memcpy(&v, flash_.data + offset, sizeof v);
                *out = v;
                return flash_.index;
            }
            const uint32_t off = address - kRamBase;
            if (static_cast<uint64_t>(off) + 2 <= ram_.size) {
                uint16_t v;
                std::memcpy(&v, ram_.data + off, sizeof v);
                *out = v;
                return ram_.index;
            }
        }
        const int i = find_sub_word(address, 2);
        if (i == kNotHandled) return kNotHandled;
        uint16_t v;
        std::memcpy(&v, regions_[i].data + (address - regions_[i].base), sizeof v);
        *out = v;
        return i;
    }

    int read8(uint32_t address, uint32_t* out) const noexcept {
        {
            const uint32_t offset = address - kFlashBase;
            if (static_cast<uint64_t>(offset) + 1 <= flash_.size) {
                *out = flash_.data[offset];
                return flash_.index;
            }
            const uint32_t off = address - kRamBase;
            if (static_cast<uint64_t>(off) + 1 <= ram_.size) {
                *out = ram_.data[off];
                return ram_.index;
            }
        }
        const int i = find_sub_word(address, 1);
        if (i == kNotHandled) return kNotHandled;
        *out = regions_[i].data[address - regions_[i].base];
        return i;
    }

    int write32(uint32_t address, uint32_t value) const noexcept {
        {
            const uint32_t off = address - kRamBase;
            if (static_cast<uint64_t>(off) + 4 <= ram_.size) {
                std::memcpy(ram_.data + off, &value, sizeof value);
                return ram_.index;
            }
            const uint32_t offset = address - kFlashBase;
            if (static_cast<uint64_t>(offset) + 4 <= flash_.size) {
                std::memcpy(flash_.data + offset, &value, sizeof value);
                return flash_.index;
            }
        }
        int lo, hi;
        candidates(address, &lo, &hi);
        for (int i = lo; i < hi; ++i) {
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
        {
            const uint16_t v = static_cast<uint16_t>(value);
            const uint32_t off = address - kRamBase;
            if (static_cast<uint64_t>(off) + 2 <= ram_.size) {
                std::memcpy(ram_.data + off, &v, sizeof v);
                return ram_.index;
            }
            const uint32_t offset = address - kFlashBase;
            if (static_cast<uint64_t>(offset) + 2 <= flash_.size) {
                std::memcpy(flash_.data + offset, &v, sizeof v);
                return flash_.index;
            }
        }
        const int i = find_sub_word(address, 2);
        if (i == kNotHandled || (regions_[i].flags & kNotifyOnWrite)) return kNotHandled;
        const uint16_t v = static_cast<uint16_t>(value);
        std::memcpy(regions_[i].data + (address - regions_[i].base), &v, sizeof v);
        return i;
    }

    int write8(uint32_t address, uint32_t value) const noexcept {
        {
            const uint32_t off = address - kRamBase;
            if (static_cast<uint64_t>(off) + 1 <= ram_.size) {
                ram_.data[off] = static_cast<uint8_t>(value);
                return ram_.index;
            }
            const uint32_t offset = address - kFlashBase;
            if (static_cast<uint64_t>(offset) + 1 <= flash_.size) {
                flash_.data[offset] = static_cast<uint8_t>(value);
                return flash_.index;
            }
        }
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
        {
            const uint32_t offset = address - kFlashBase;
            if (static_cast<uint64_t>(offset) + width <= flash_.size) return flash_.index;
            const uint32_t off = address - kRamBase;
            if (static_cast<uint64_t>(off) + width <= ram_.size) return ram_.index;
        }
        int lo, hi;
        candidates(address, &lo, &hi);
        for (int i = lo; i < hi; ++i) {
            const Region& r = regions_[i];
            const uint32_t offset = address - r.base;
            if (offset >= r.size) continue;
            if (!(r.flags & kSubWord) || r.size - offset < width) return kNotHandled;
            return i;
        }
        return kNotHandled;
    }

    // Which regions can possibly serve an address, found from its top four bits instead of by walking the table: the RP2040's regions
    // sit in different 256 MiB blocks (boot ROM 0x0, flash 0x1, SRAM 0x2, DPRAM 0x5), so an instruction fetch looks at one entry
    // instead of testing the boot ROM and then the flash. A block no region touches serves nothing; a block more than one region
    // touches falls back to the ordered walk, which is what decides ties ("the first region attached wins").
    static constexpr uint8_t kNoRegion = 0xFE, kSeveral = 0xFF;

    void candidates(uint32_t address, int* lo, int* hi) const noexcept {
        const uint8_t block = block_[address >> 28];
        if (block < kMaxRegions) {
            *lo = block;
            *hi = block + 1;
        } else {
            *lo = 0;
            *hi = block == kNoRegion ? 0 : count_;
        }
    }

    void reindex() noexcept {
        for (uint8_t& b : block_) b = kNoRegion;
        for (int i = 0; i < count_; ++i) {
            const uint64_t first = regions_[i].base >> 28;
            const uint64_t last = (static_cast<uint64_t>(regions_[i].base) + regions_[i].window - 1) >> 28;
            for (uint64_t n = first; n <= last && n < 16; ++n) block_[n] = block_[n] == kNoRegion ? static_cast<uint8_t>(i) : kSeveral;
        }
        index_fast();
    }

    // The RP2040's address map is fixed by the silicon: XIP flash at 0x10000000 and SRAM at 0x20000000. A region attached there as plain sub-word
    // memory with no hook, and that no other region shares a 256 MiB block with (so serving it first cannot change which region the ordered
    // walk would have picked), gets a fast path with the base as a compile-time constant. Every address these miss takes the generic walk,
    // which decides exactly what it always did.
    static constexpr uint32_t kFlashBase = 0x10000000u, kRamBase = 0x20000000u;
    struct Fast {
        uint32_t window = 0, size = 0, mask = 0;  // size 0: no fast path
        uint8_t* data = nullptr;
        int index = kNotHandled;
    };

    void index_fast() noexcept {
        flash_ = Fast{};
        ram_ = Fast{};
        for (int i = 0; i < count_ && fast_enabled_; ++i) {
            const Region& r = regions_[i];
            if (r.flags != kSubWord || (r.base != kFlashBase && r.base != kRamBase)) continue;
            const uint64_t first = r.base >> 28, last = (static_cast<uint64_t>(r.base) + r.window - 1) >> 28;
            bool alone = true;
            for (int j = 0; j < count_; ++j) {
                if (j == i) continue;
                const uint64_t jf = regions_[j].base >> 28, jl = (static_cast<uint64_t>(regions_[j].base) + regions_[j].window - 1) >> 28;
                if (jf <= last && first <= jl) alone = false;
            }
            if (alone) (r.base == kFlashBase ? flash_ : ram_) = Fast{r.window, r.size, r.mask, r.data, i};
        }
    }

    Region regions_[kMaxRegions]{};
    Fast flash_, ram_;
    bool fast_enabled_ = true;
    uint8_t block_[16] = {kNoRegion, kNoRegion, kNoRegion, kNoRegion, kNoRegion, kNoRegion, kNoRegion, kNoRegion,
                          kNoRegion, kNoRegion, kNoRegion, kNoRegion, kNoRegion, kNoRegion, kNoRegion, kNoRegion};
    int count_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_MEMORY_MAP_HPP

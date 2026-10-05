// A fixed-size FIFO of 32-bit words, the C++ twin of `utils/fifo.py`'s `FIFO` that the Python peripherals (UART, SPI, I2C, the CDC host) use: a push on a full FIFO drops the value, a pull
// from an empty one reads 0, `peek` of an empty one reads 0, `reset` empties it and leaves the storage alone. Header-only, C++17, no exceptions/RTTI/STL, no allocation: the storage is
// part of the object.
#ifndef RP2040PY_CORE_FIFO_HPP
#define RP2040PY_CORE_FIFO_HPP

#include <cstdint>

namespace rp2040core {

template <uint32_t N>
class Fifo {
public:
    static constexpr uint32_t kCapacity = N;

    uint32_t size() const noexcept { return N; }
    uint32_t count() const noexcept { return used_; }
    bool empty() const noexcept { return used_ == 0; }
    bool full() const noexcept { return used_ == N; }
    // The i-th oldest value (0 is the next to be pulled); only meaningful below `count()`.
    uint32_t at(uint32_t index) const noexcept { return buffer_[(start_ + index) % N]; }

    void push(uint32_t value) noexcept {
        if (used_ < N) {
            buffer_[(start_ + used_) % N] = value;
            ++used_;
        }
    }
    uint32_t pull() noexcept {
        if (used_ == 0) return 0;
        const uint32_t value = buffer_[start_];
        start_ = (start_ + 1) % N;
        --used_;
        return value;
    }
    uint32_t peek() const noexcept { return used_ ? buffer_[start_] : 0; }
    void reset() noexcept { used_ = 0; }

private:
    uint32_t buffer_[N] = {};
    uint32_t start_ = 0, used_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_FIFO_HPP

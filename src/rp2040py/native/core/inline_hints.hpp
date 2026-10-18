// Inlining hints for the hot path of the C++ core (docs/records/0103-wasm-backend.md, the measurement): the per-instruction path - the batch loop, the clock tick, the memory-map reads - is called once
// for every emulated instruction, and clang's default inlining leaves those calls in (gcc inlines them): the same source runs at 38 Minstr/s built by clang and 53 built by gcc. wasm can only be
// built by clang, so the hot functions say what they want. The hints change no behaviour.
#ifndef RP2040PY_CORE_INLINE_HINTS_HPP
#define RP2040PY_CORE_INLINE_HINTS_HPP

#if defined(__GNUC__) || defined(__clang__)
#define RP2040PY_ALWAYS_INLINE inline __attribute__((always_inline))
#define RP2040PY_NOINLINE __attribute__((noinline))
#elif defined(_MSC_VER)
#define RP2040PY_ALWAYS_INLINE __forceinline
#define RP2040PY_NOINLINE __declspec(noinline)
#else
#define RP2040PY_ALWAYS_INLINE inline
#define RP2040PY_NOINLINE
#endif

#endif  // RP2040PY_CORE_INLINE_HINTS_HPP

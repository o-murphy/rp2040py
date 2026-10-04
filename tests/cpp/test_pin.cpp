// Standalone checks of src/rp2040py/native/core/pin.hpp (see tests/test_core_cpp.py for the flags).
// The comparison with the Python/Cython pin of a real session is tests/test_pin_trace.py; these are known-value checks plus a
// differential run of the level function against an independent transcription of _gpio_pin.py's semantics.
#include <cstdio>

#include "pin.hpp"

using namespace rp2040core;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Recorder {
    static const int kMax = 512;
    uint32_t sources[kPinSources] = {};
    int source_calls = 0;
    int fail_source_on_call = -1;
    uint32_t change_pin[kMax];
    int change_new[kMax];
    int change_old[kMax];
    int changes = 0;
    int fail_change_on_call = -1;
    int io_irqs = 0;
    int fail_io_irq_on_call = -1;
    uint32_t input_pin[kMax];
    bool input_pwm[kMax];
    int inputs = 0;
};

static bool on_source(void* ctx, uint32_t source, uint32_t* out) {
    Recorder* r = static_cast<Recorder*>(ctx);
    const int call = r->source_calls++;
    if (call == r->fail_source_on_call) return false;
    *out = r->sources[source];
    return true;
}
static bool on_change(void* ctx, uint32_t pin, int new_state, int old_state) {
    Recorder* r = static_cast<Recorder*>(ctx);
    const int call = r->changes;
    if (r->changes < Recorder::kMax) {
        r->change_pin[r->changes] = pin;
        r->change_new[r->changes] = new_state;
        r->change_old[r->changes] = old_state;
        ++r->changes;
    }
    return call != r->fail_change_on_call;
}
static bool on_io_irq(void* ctx) {
    Recorder* r = static_cast<Recorder*>(ctx);
    return r->io_irqs++ != r->fail_io_irq_on_call;
}
static bool on_input(void* ctx, uint32_t pin, bool pwm) {
    Recorder* r = static_cast<Recorder*>(ctx);
    if (r->inputs < Recorder::kMax) {
        r->input_pin[r->inputs] = pin;
        r->input_pwm[r->inputs] = pwm;
        ++r->inputs;
    }
    return true;
}

static PinHost host_for(Recorder* r) {
    PinHost h;
    h.source = on_source;
    h.on_change = on_change;
    h.io_interrupt = on_io_irq;
    h.input_changed = on_input;
    h.ctx = r;
    return h;
}

// --- an independent transcription of _gpio_pin.py (names and structure differ on purpose) ------------------------------

struct RefPin {
    uint32_t ctrl, pad;
    uint32_t irq_enable, irq_force, irq_status;
    bool raw_input, driven, always_oe;
};

static bool ov(bool v, uint32_t t) { return t == 0 ? v : t == 1 ? !v : t == 2 ? false : true; }

static bool ref_source_bit(const uint32_t* src, uint32_t fsel, bool want_value, uint32_t index) {
    // FUNCSEL 4 = PWM, 5 = SIO, 6 = PIO0, 7 = PIO1 (the Python tuple order is oe-source then value-source per function)
    static const int oe_src[8] = {-1, -1, -1, -1, kSrcPwmDirection, kSrcSioOe, kSrcPio0Oe, kSrcPio1Oe};
    static const int val_src[8] = {-1, -1, -1, -1, kSrcPwmValue, kSrcSioValue, kSrcPio0Value, kSrcPio1Value};
    const int s = (want_value ? val_src : oe_src)[fsel & 7];
    if ((fsel & 0x18) != 0 || s < 0) return false;  // only FUNCSEL 4..7 are connected (fsel <= 31)
    return ((src[s] >> index) & 1u) != 0;
}

static bool ref_oe(const RefPin& p, const uint32_t* src, uint32_t index) {
    if (p.always_oe) return true;
    return ref_source_bit(src, p.ctrl & 0x1F, false, index);
}

static int ref_state(const RefPin& p, const uint32_t* src, uint32_t index) {
    if (ov(ref_oe(p, src, index), (p.ctrl >> 12) & 3)) return ov(ref_source_bit(src, p.ctrl & 0x1F, true, index), (p.ctrl >> 8) & 3) ? 1 : 0;
    const bool pd = p.pad & 4, pu = p.pad & 8;
    if (pd && pu) return 5;
    if (pd) return 4;
    if (pu) return 3;
    return 2;
}

static bool ref_eff_input(const RefPin& p) {
    if (p.driven) return p.raw_input;
    if ((p.pad & 8) && !(p.pad & 4)) return true;
    if ((p.pad & 4) && !(p.pad & 8)) return false;
    return p.raw_input;
}

static uint32_t ref_status(const RefPin& p, const uint32_t* src, uint32_t index) {
    const bool raw_int = ((p.irq_status & p.irq_enable) | p.irq_force) != 0;
    const bool irq_v = ov(raw_int, (p.ctrl >> 28) & 3);
    const bool eff_in = ref_eff_input(p);
    const bool in_v = ov(eff_in && (p.pad & 0x40), (p.ctrl >> 16) & 3);
    const bool roe = ref_oe(p, src, index);
    const bool oe = ov(roe, (p.ctrl >> 12) & 3);
    const bool rov = ref_source_bit(src, p.ctrl & 0x1F, true, index);
    const bool o = ov(rov, (p.ctrl >> 8) & 3);
    uint32_t s = 0;
    if (irq_v) s |= 1u << 26;
    if (raw_int) s |= 1u << 24;
    if (in_v) s |= 1u << 19;
    if (eff_in) s |= 1u << 17;
    if (oe) s |= 1u << 13;
    if (roe) s |= 1u << 12;
    if (o) s |= 1u << 9;
    if (rov) s |= 1u << 8;
    return s;
}

static uint64_t rng_state = 0x9E3779B97F4A7C15ull;
static uint32_t rnd() {
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    return static_cast<uint32_t>(rng_state >> 16);
}

// --- tests -----------------------------------------------------------------------------------------------------------

static void test_defaults_and_last_state() {
    Recorder r;
    PinBank bank;
    const bool always[3] = {false, true, false};
    CHECK(bank.init(3, host_for(&r), always));
    CHECK(bank.pin(0).ctrl == 0x1F && bank.pin(0).pad_value == 0b0110110);
    // last_state is computed with ctrl = 0 / pad = 0 before the defaults: INPUT for an ordinary pin, LOW for an always-output one.
    CHECK(bank.pin(0).last_state == kPinInput);
    CHECK(bank.pin(1).last_state == kPinLow);
    CHECK(bank.check_for_updates(0));  // an untouched pin's first check announces INPUT -> PULL_DOWN
    CHECK(r.changes == 1 && r.change_new[0] == kPinPullDown && r.change_old[0] == kPinInput);
    int s = -1;
    // The default pad word 0b0110110 has the pull-down bit (bit 2) set, so once the defaults are in the level is PULL_DOWN even though
    // `last_state` was captured as INPUT: the first check_for_updates() of an untouched pin announces INPUT -> PULL_DOWN (as the Cython does).
    CHECK(bank.state_code(0, &s) && s == kPinPullDown);
    CHECK(bank.state_code(1, &s) && s == kPinLow);    // always output enabled, FUNCSEL 0x1F connects no value source -> LOW
    CHECK(r.changes == 1);  // only the one announcement above
}

static void test_sio_driven_level_and_announcement() {
    Recorder r;
    PinBank bank;
    CHECK(bank.init(8, host_for(&r)));
    bank.pin(2).ctrl = kFuncSio;
    r.sources[kSrcSioOe] = 1u << 2;
    r.sources[kSrcSioValue] = 1u << 2;
    CHECK(bank.check_for_updates(2));
    CHECK(r.changes == 1 && r.change_pin[0] == 2 && r.change_new[0] == kPinHigh && r.change_old[0] == kPinInput);
    CHECK(bank.check_for_updates(2));  // unchanged: no second announcement
    CHECK(r.changes == 1);
    r.sources[kSrcSioValue] = 0;
    CHECK(bank.check_for_updates(2));
    CHECK(r.changes == 2 && r.change_new[1] == kPinLow && r.change_old[1] == kPinHigh);
    uint32_t status = 0;
    CHECK(bank.status(2, &status));
    CHECK((status & (1u << 13)) && (status & (1u << 12)) && !(status & (1u << 9)) && !(status & (1u << 8)));
}

static void test_direct_source_replaces_the_host_call() {
    Recorder r;
    PinBank bank;
    CHECK(bank.init(4, host_for(&r)));
    uint32_t oe = 1u << 1, value = 1u << 1;
    bank.bind_source(kSrcSioOe, &oe);
    bank.bind_source(kSrcSioValue, &value);
    bank.pin(1).ctrl = kFuncSio;
    int s = -1;
    CHECK(bank.state_code(1, &s) && s == kPinHigh);
    CHECK(r.source_calls == 0);
    value = 0;
    CHECK(bank.state_code(1, &s) && s == kPinLow);
    bank.bind_source(kSrcSioOe, nullptr);  // back to the host (which says 0): not an output, so the pad's (default pull-down) decides
    CHECK(bank.state_code(1, &s) && s == kPinPullDown && r.source_calls == 1);
}

static void test_pulls_and_bus_keeper() {
    Recorder r;
    PinBank bank;
    CHECK(bank.init(1, host_for(&r)));
    int s = -1;
    bank.pin(0).pad_value = 0b1000;
    CHECK(bank.state_code(0, &s) && s == kPinPullUp && bank.eff_raw_input(0));
    bank.pin(0).pad_value = 0b0100;
    CHECK(bank.state_code(0, &s) && s == kPinPullDown && !bank.eff_raw_input(0));
    bank.pin(0).pad_value = 0b1100;
    CHECK(bank.state_code(0, &s) && s == kPinBusKeeper);
    bank.pin(0).raw_input_value = true;
    CHECK(bank.eff_raw_input(0));  // both pulls: the raw level wins
}

static void test_input_edges_and_the_io_interrupt() {
    Recorder r;
    PinBank bank;
    CHECK(bank.init(4, host_for(&r)));
    bank.pin(3).pad_value |= pin_bits::kPadInputEnable;
    bank.pin(3).irq_enable_mask = pin_bits::kIrqEdgeHigh;
    CHECK(!bank.any_irq_value());
    CHECK(bank.set_input_value(3, true));
    CHECK(bank.pin(3).driven && bank.pin(3).raw_input_value);
    CHECK(bank.pin(3).irq_status == (pin_bits::kIrqEdgeHigh | pin_bits::kIrqLevelHigh));
    CHECK(bank.any_irq_value() && r.io_irqs == 1);
    CHECK(r.inputs == 1 && r.input_pin[0] == 3 && !r.input_pwm[0]);
    // Acknowledging the edge clears it and re-evaluates the interrupt.
    CHECK(bank.update_irq_value(3, pin_bits::kIrqEdgeHigh));
    CHECK(!bank.any_irq_value() && r.io_irqs == 2);
    CHECK(bank.update_irq_value(3, pin_bits::kIrqEdgeHigh));  // already clear: nothing to tell
    CHECK(r.io_irqs == 2);
    // Releasing hands the pad back to its pull (none here: the raw level stays what it was driven to).
    CHECK(bank.release_input(3));
    CHECK(!bank.pin(3).driven);
}

static void test_level_bits_follow_the_input_and_the_interrupt_only_when_it_changes() {
    Recorder r;
    PinBank bank;
    CHECK(bank.init(2, host_for(&r)));
    bank.pin(0).pad_value = pin_bits::kPadInputEnable;  // no pulls
    // No interrupt enabled: input changes alter irq_status but never the pin's irq value, so the IO interrupt is not re-evaluated.
    CHECK(bank.set_input_value(0, false));
    CHECK(bank.pin(0).irq_status == (pin_bits::kIrqEdgeLow | pin_bits::kIrqLevelLow));
    CHECK(bank.set_input_value(0, true));
    CHECK(bank.pin(0).irq_status == (pin_bits::kIrqEdgeLow | pin_bits::kIrqEdgeHigh | pin_bits::kIrqLevelHigh));  // LEVEL_LOW cleared, edges latch
    CHECK(bank.set_input_value(0, false));
    CHECK(bank.pin(0).irq_status == (pin_bits::kIrqEdgeLow | pin_bits::kIrqEdgeHigh | pin_bits::kIrqLevelLow));   // LEVEL_HIGH cleared
    CHECK(r.io_irqs == 0 && r.inputs == 3);
    // A pad with input disabled treats a high level as low.
    bank.pin(1).pad_value = 0;
    CHECK(bank.set_input_value(1, true));
    CHECK(bank.pin(1).irq_status == (pin_bits::kIrqEdgeLow | pin_bits::kIrqLevelLow));
}

static void test_a_pwm_pin_reports_it_to_the_host() {
    Recorder r;
    PinBank bank;
    CHECK(bank.init(2, host_for(&r)));
    bank.pin(1).ctrl = kFuncPwm;
    CHECK(bank.set_input_value(1, true));
    CHECK(r.inputs == 1 && r.input_pwm[0]);
}

static void test_reset_keeps_what_is_wired_and_recomputes_last_state() {
    Recorder r;
    PinBank bank;
    CHECK(bank.init(2, host_for(&r)));
    bank.pin(0).ctrl = kFuncSio;
    bank.pin(0).pad_value = 0b1000 | pin_bits::kPadInputEnable;
    bank.pin(0).irq_enable_mask = 0xF;
    bank.pin(0).irq_force_mask = 0x1;
    bank.pin(0).irq_status = 0x5;
    bank.pin(0).raw_input_value = true;
    bank.pin(0).driven = true;
    r.sources[kSrcSioOe] = 1;
    r.sources[kSrcSioValue] = 1;
    CHECK(bank.check_for_updates(0));
    CHECK(r.changes == 1);
    CHECK(bank.reset(0, /*io=*/true, /*pads=*/false));
    CHECK(bank.pin(0).ctrl == 0x1F && bank.pin(0).irq_enable_mask == 0 && bank.pin(0).irq_force_mask == 0 && bank.pin(0).irq_status == 0);
    CHECK(bank.pin(0).pad_value == (0b1000 | pin_bits::kPadInputEnable));  // pads half untouched
    CHECK(bank.pin(0).raw_input_value && bank.pin(0).driven);                // what is wired survives
    CHECK(bank.pin(0).last_state == kPinPullUp);                            // recomputed after the registers came back
    CHECK(bank.check_for_updates(0) && r.changes == 1);                     // so no spurious edge
    CHECK(bank.reset(0, false, true));
    CHECK(bank.pin(0).pad_value == pin_bits::kDefaultPad);
}

static void test_failures_stop_at_once_and_leave_the_pin_updated() {
    Recorder r;
    PinBank bank;
    CHECK(bank.init(2, host_for(&r)));
    bank.pin(0).ctrl = kFuncSio;
    r.sources[kSrcSioOe] = 1;
    r.sources[kSrcSioValue] = 1;
    r.fail_change_on_call = 0;  // the listener raises
    CHECK(!bank.check_for_updates(0));
    CHECK(bank.pin(0).last_state == kPinHigh);  // already recorded: a retry does not re-announce
    r.fail_change_on_call = -1;
    CHECK(bank.check_for_updates(0) && r.changes == 1);
    r.fail_source_on_call = r.source_calls;  // the next source read fails
    int s = 0;
    CHECK(!bank.state_code(0, &s));
    uint32_t status = 0;
    r.fail_source_on_call = r.source_calls + 1;  // fails on the value read, after the enable read
    CHECK(!bank.status(0, &status));
    bank.pin(1).pad_value |= pin_bits::kPadInputEnable;
    bank.pin(1).irq_enable_mask = pin_bits::kIrqEdgeHigh;
    r.fail_io_irq_on_call = r.io_irqs;
    CHECK(!bank.set_input_value(1, true));
    CHECK(bank.pin(1).raw_input_value);  // the state was updated before the call out
}

static void test_input_values_mask() {
    Recorder r;
    PinBank bank;
    CHECK(bank.init(30, host_for(&r)));
    for (uint32_t i = 0; i < 30; ++i) bank.pin(i).pad_value = pin_bits::kPadInputEnable;  // no pulls: the raw level shows
    bank.pin(0).raw_input_value = true;
    bank.pin(7).raw_input_value = true;
    bank.pin(29).raw_input_value = true;
    bank.pin(7).pad_value &= ~pin_bits::kPadInputEnable;  // input disabled: reads 0
    CHECK(bank.input_values() == ((1u << 0) | (1u << 29)));
}

static void test_level_function_against_the_reference_transcription() {
    Recorder r;
    PinBank bank;
    bool always[32];
    for (int i = 0; i < 32; ++i) always[i] = (i % 5) == 0;
    CHECK(bank.init(32, host_for(&r), always));
    for (int trial = 0; trial < 200000; ++trial) {
        const uint32_t i = rnd() % 32;
        for (uint32_t s = 0; s < kPinSources; ++s) r.sources[s] = rnd();
        RefPin ref;
        Pin& p = bank.pin(i);
        p.ctrl = (rnd() % 4 == 0) ? (rnd() & 0x1F) : (4 + rnd() % 4) | (static_cast<uint32_t>(rnd() % 4) << 8) | (static_cast<uint32_t>(rnd() % 4) << 12) |
                                                       (static_cast<uint32_t>(rnd() % 4) << 16) | (static_cast<uint32_t>(rnd() % 4) << 28);
        p.pad_value = rnd() & 0xFF;
        p.irq_enable_mask = rnd() & 0xF;
        p.irq_force_mask = rnd() & 0xF;
        p.irq_status = rnd() & 0xF;
        p.raw_input_value = rnd() & 1;
        p.driven = rnd() & 1;
        ref.ctrl = p.ctrl;
        ref.pad = p.pad_value;
        ref.irq_enable = p.irq_enable_mask;
        ref.irq_force = p.irq_force_mask;
        ref.irq_status = p.irq_status;
        ref.raw_input = p.raw_input_value;
        ref.driven = p.driven;
        ref.always_oe = p.always_output_enabled;
        int code = -1;
        uint32_t status = 0;
        CHECK(bank.state_code(i, &code));
        CHECK(code == ref_state(ref, r.sources, i));
        CHECK(bank.status(i, &status));
        CHECK(status == ref_status(ref, r.sources, i));
        CHECK(bank.eff_raw_input(i) == ref_eff_input(ref));
        if (failures > 0) break;
    }
}

int main() {
    test_defaults_and_last_state();
    test_sio_driven_level_and_announcement();
    test_direct_source_replaces_the_host_call();
    test_pulls_and_bus_keeper();
    test_input_edges_and_the_io_interrupt();
    test_level_bits_follow_the_input_and_the_interrupt_only_when_it_changes();
    test_a_pwm_pin_reports_it_to_the_host();
    test_reset_keeps_what_is_wired_and_recomputes_last_state();
    test_failures_stop_at_once_and_leave_the_pin_updated();
    test_input_values_mask();
    test_level_function_against_the_reference_transcription();
    if (failures == 0) std::printf("test_pin: all checks passed\n");
    return failures == 0 ? 0 : 1;
}

// Standalone checks of src/rp2040py/native/core/gspi.hpp (see tests/test_core_cpp.py for the flags).
// The shifter is compared, on long random CS/CLK/word/response sequences, with an independent transcription of the edge methods of
// external/cyw43/bus.py (`_on_cs_change`, `_on_clock_rising`, `_on_clock_falling`); the end-to-end comparison with the real chip and firmware is
// tests/test_cyw43_bus.py and tests/test_cyw43_nat.py, run on both the Python listeners and this shifter.
#include <cstdio>

#include "gspi.hpp"

using namespace rp2040core;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

static const uint32_t kClkPin = 29, kDataPin = 24, kCsPin = 25;
static const int kLog = 1 << 16;

// --- the protocol stub both sides use: a deterministic answer to a word -----------------------------------------------------

static uint32_t stub_length(uint32_t word) {
    if ((word & 3u) == 0) return 0;  // nothing to say
    return ((word >> 2) % 70u) + 1u;  // 1..70 bytes
}
static uint8_t stub_byte(uint32_t word, uint32_t i) { return static_cast<uint8_t>((word * (i + 1u)) ^ (word >> 8)); }

// --- the reference: bus.py's three methods, transcribed with different structure --------------------------------------------

struct RefBus {
    bool selected = false;
    uint32_t shift = 0;
    int bits = 0;
    uint8_t response[128];
    uint32_t response_len = 0;  // bytes; 0 = none (an empty bytes object is falsy)
    uint32_t bit_index = 0;
    uint32_t words[kLog];
    int word_count = 0;
    uint8_t cs_calls[kLog];
    int cs_count = 0;
    uint8_t drives[kLog * 4];
    int drive_count = 0;

    void on_cs_change(bool sel) {
        selected = sel;
        shift = 0;
        bits = 0;
        response_len = 0;
        bit_index = 0;
        cs_calls[cs_count++ % kLog] = sel;
    }
    void on_clock_rising(bool sampled) {
        if (!selected || response_len != 0) return;
        shift = static_cast<uint32_t>((static_cast<uint64_t>(shift) << 1 | (sampled ? 1u : 0u)) & 0xFFFFFFFFull);
        bits += 1;
        if (bits < 32) return;
        const uint32_t word = shift;
        shift = 0;
        bits = 0;
        words[word_count++ % kLog] = word;
        const uint32_t n = stub_length(word);
        for (uint32_t i = 0; i < n; ++i) response[i] = stub_byte(word, i);
        response_len = n;
        bit_index = 0;
    }
    bool on_clock_falling(bool* bit) {
        if (!selected || response_len == 0) return false;
        const uint32_t byte_index = bit_index / 8, bit_in_byte = bit_index % 8;
        if (byte_index >= response_len) return false;
        *bit = (response[byte_index] & (0x80 >> bit_in_byte)) != 0;
        bit_index += 1;
        if (bit_index >= response_len * 8) {
            response_len = 0;
            bit_index = 0;
        }
        return true;
    }
};

// --- the C++ side: three real pin banks driven by a fake SIO, the shifter attached to them ----------------------------------

struct Rig {
    uint32_t sio_oe = 0, sio_value = 0;  // the "chip" drives CLK, DATA and CS as SIO outputs
    uint32_t words[kLog];
    int word_count = 0;
    uint8_t cs_calls[kLog];
    int cs_count = 0;
    uint8_t drives[kLog * 4];
    int drive_count = 0;
    int fail_word_on_call = -1;
    int fail_cs_on_call = -1;
    int fail_drive_on_call = -1;
    int fail_data_source_on_call = -1;
    int data_source_reads = 0;
    int data_inputs = 0;
    PinBank clk, data, cs;
    GspiShifter shifter;

    static bool source(void* ctx, uint32_t which, uint32_t* out) {
        Rig* r = static_cast<Rig*>(ctx);
        *out = which == kSrcSioOe ? r->sio_oe : which == kSrcSioValue ? r->sio_value : 0u;
        return true;
    }
    // The data pin is the only one the shifter asks the level of: its source reads can be made to fail.
    static bool data_source(void* ctx, uint32_t which, uint32_t* out) {
        Rig* r = static_cast<Rig*>(ctx);
        if (r->data_source_reads++ == r->fail_data_source_on_call) return false;
        return source(ctx, which, out);
    }
    // `input_changed` runs on every set_input_value the shifter makes on the data pin: record the level driven.
    static bool data_input_changed(void* ctx, uint32_t, bool) {
        Rig* r = static_cast<Rig*>(ctx);
        r->drives[r->drive_count++ % (kLog * 4)] = r->data.pin(0).raw_input_value ? 1 : 0;
        return r->data_inputs++ != r->fail_drive_on_call;
    }
    static bool host_word(void* ctx, uint32_t word) {
        Rig* r = static_cast<Rig*>(ctx);
        const int call = r->word_count;
        r->words[r->word_count++ % kLog] = word;
        const uint32_t n = stub_length(word);
        uint8_t buffer[128];
        for (uint32_t i = 0; i < n; ++i) buffer[i] = stub_byte(word, i);
        r->shifter.start_response(buffer, n);
        return call != r->fail_word_on_call;
    }
    static bool host_cs(void* ctx, bool selected) {
        Rig* r = static_cast<Rig*>(ctx);
        const int call = r->cs_count;
        r->cs_calls[r->cs_count++ % kLog] = selected;
        return call != r->fail_cs_on_call;
    }

    void setup() {
        PinHost plain;
        plain.source = &Rig::source;
        plain.ctx = this;
        PinHost data_host;
        data_host.source = &Rig::data_source;
        data_host.input_changed = &Rig::data_input_changed;
        data_host.ctx = this;
        CHECK(clk.init(1, plain, nullptr, kClkPin));
        CHECK(data.init(1, data_host, nullptr, kDataPin));
        CHECK(cs.init(1, plain, nullptr, kCsPin));
        clk.pin(0).ctrl = data.pin(0).ctrl = cs.pin(0).ctrl = kFuncSio;
        sio_oe = (1u << kClkPin) | (1u << kDataPin) | (1u << kCsPin);
        sio_value = 1u << kCsPin;  // CS deasserted (HIGH), CLK low, DATA low
        CHECK(clk.check_for_updates(0) && data.check_for_updates(0) && cs.check_for_updates(0));  // settle last_state before anything listens
        data_source_reads = 0;
        GspiHost host;
        host.on_word = &Rig::host_word;
        host.on_cs = &Rig::host_cs;
        host.ctx = this;
        CHECK(shifter.attach(&clk, &data, &cs, host));
    }

    bool set_pin(uint32_t pin, PinBank* bank, bool level) {
        if (level) sio_value |= 1u << pin;
        else sio_value &= ~(1u << pin);
        return bank->check_for_updates(0);
    }
    bool set_cs(bool selected) { return set_pin(kCsPin, &cs, !selected); }  // active low
    bool set_data(bool level) { return set_pin(kDataPin, &data, level); }
    bool set_clk(bool level) { return set_pin(kClkPin, &clk, level); }
};

static uint64_t rng_state = 0x243F6A8885A308D3ull;
static uint32_t rnd() {
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    return static_cast<uint32_t>(rng_state >> 16);
}

static bool same_state(const Rig& rig, const RefBus& ref) {
    return rig.shifter.selected() == ref.selected && rig.shifter.bits_in_word() == static_cast<uint32_t>(ref.bits) &&
           rig.shifter.shift_register() == ref.shift && rig.shifter.responding() == (ref.response_len != 0) &&
           rig.drive_count == ref.drive_count && rig.word_count == ref.word_count && rig.cs_count == ref.cs_count;
}

static void test_random_sequences_match_the_reference() {
    int total_words = 0, total_response_bits = 0;
    for (int trial = 0; trial < 30; ++trial) {
        Rig* rig = new Rig();
        RefBus* ref = new RefBus();
        rig->setup();
        bool cs_selected = false, clk_level = false, data_level = false;
        // A transaction is mostly well formed (select, words, clock cycles) but the random mix also deselects mid-word, leaves the clock high
        // across operations and clocks while idle.
        for (int op = 0; op < 6000 && failures == 0; ++op) {
            const uint32_t kind = rnd() % 100;
            if (kind < 6) {  // CS change (or a no-change set, which must do nothing on both sides)
                const bool target = (rnd() & 1) != 0;
                CHECK(rig->set_cs(target));
                if (target != cs_selected) ref->on_cs_change(target);
                cs_selected = target;
            } else if (kind < 90) {  // a full clock cycle with the host driving a random data bit
                data_level = (rnd() & 1) != 0;
                CHECK(rig->set_data(data_level));
                for (int half = 0; half < 2; ++half) {
                    const bool level = half == 0;
                    CHECK(rig->set_clk(level));
                    if (level == clk_level) continue;
                    clk_level = level;
                    if (level) {
                        ref->on_clock_rising(data_level);
                    } else {
                        bool bit = false;
                        if (ref->on_clock_falling(&bit)) ref->drives[ref->drive_count++ % (kLog * 4)] = bit ? 1 : 0;
                    }
                }
            } else {  // only a rising edge: the clock stays high into the next operation
                data_level = (rnd() & 1) != 0;
                CHECK(rig->set_data(data_level));
                CHECK(rig->set_clk(true));
                if (!clk_level) ref->on_clock_rising(data_level);
                clk_level = true;
            }
            CHECK(same_state(*rig, *ref));
        }
        for (int i = 0; i < ref->word_count && i < kLog; ++i) CHECK(rig->words[i] == ref->words[i]);
        for (int i = 0; i < ref->cs_count && i < kLog; ++i) CHECK(rig->cs_calls[i] == ref->cs_calls[i]);
        for (int i = 0; i < ref->drive_count && i < kLog * 4; ++i) CHECK(rig->drives[i] == ref->drives[i]);
        total_words += ref->word_count;
        total_response_bits += ref->drive_count;
        delete rig;
        delete ref;
    }
    // The random runs must actually have exercised the machinery they claim to.
    CHECK(total_words > 500);
    CHECK(total_response_bits > 5000);
}

static void send_word(Rig& rig, uint32_t word) {
    for (int i = 31; i >= 0; --i) {
        CHECK(rig.set_data(((word >> i) & 1u) != 0));
        CHECK(rig.set_clk(true));
        CHECK(rig.set_clk(false));
    }
}

static void test_a_word_reaches_the_host_and_its_response_is_shifted_out_msb_first() {
    Rig* rig = new Rig();
    rig->setup();
    CHECK(rig->set_cs(true));
    CHECK(rig->cs_count == 1 && rig->cs_calls[0] == 1);
    const uint32_t header = (0x7u << 2) | 1u;  // 8 bytes of response (stub: length = (w >> 2) % 70 + 1)
    send_word(*rig, header);
    CHECK(rig->word_count == 1 && rig->words[0] == header);
    CHECK(rig->shifter.responding());
    // While answering, rising edges sample nothing; each falling edge drives the next bit.
    const uint32_t n = stub_length(header);
    for (uint32_t bit = 0; bit < n * 8; ++bit) {
        CHECK(rig->set_clk(true));
        CHECK(rig->set_clk(false));
    }
    CHECK(rig->drive_count == static_cast<int>(n * 8));
    uint8_t expect = stub_byte(header, 0);
    for (int b = 0; b < 8; ++b) CHECK(rig->drives[b] == ((expect >> (7 - b)) & 1));
    CHECK(!rig->shifter.responding());  // released after its last bit
    delete rig;
}

static void test_deselect_discards_a_partial_word_and_a_response() {
    Rig* rig = new Rig();
    rig->setup();
    CHECK(rig->set_cs(true));
    for (int i = 0; i < 5; ++i) {
        CHECK(rig->set_data(true));
        CHECK(rig->set_clk(true));
        CHECK(rig->set_clk(false));
    }
    CHECK(rig->shifter.bits_in_word() == 5);
    CHECK(rig->set_cs(false));
    CHECK(rig->shifter.bits_in_word() == 0 && rig->shifter.shift_register() == 0 && !rig->shifter.selected());
    CHECK(rig->cs_count == 2 && rig->cs_calls[1] == 0);
    delete rig;
}

static void test_idle_clocking_does_nothing() {
    Rig* rig = new Rig();
    rig->setup();
    for (int i = 0; i < 40; ++i) {  // not selected: edges are ignored, nothing driven, no host call
        CHECK(rig->set_data(true));
        CHECK(rig->set_clk(true));
        CHECK(rig->set_clk(false));
    }
    CHECK(rig->word_count == 0 && rig->drive_count == 0 && rig->shifter.bits_in_word() == 0);
    delete rig;
}

static void test_a_clock_pin_that_is_released_counts_as_a_falling_edge() {
    // HIGH -> anything else is "falling" and anything else -> HIGH is "rising": the Python compares against GPIOPinState.HIGH only.
    Rig* rig = new Rig();
    rig->setup();
    CHECK(rig->set_cs(true));
    CHECK(rig->set_data(true));
    CHECK(rig->set_clk(true));                // rising: one bit in
    CHECK(rig->shifter.bits_in_word() == 1);
    rig->sio_oe &= ~(1u << kClkPin);          // the chip releases the pad: state HIGH -> PULL_DOWN (default pad word)
    CHECK(rig->clk.check_for_updates(0));
    CHECK(rig->shifter.bits_in_word() == 1);  // a falling edge with nothing to say changes nothing
    rig->sio_oe |= 1u << kClkPin;             // driven again, value still 1: PULL_DOWN -> HIGH is a rising edge
    CHECK(rig->clk.check_for_updates(0));
    CHECK(rig->shifter.bits_in_word() == 2);
    delete rig;
}

static void test_a_change_between_two_non_high_states_is_not_an_edge() {
    Rig* rig = new Rig();
    rig->setup();
    CHECK(rig->set_cs(true));
    send_word(*rig, (0x7u << 2) | 1u);  // a response is now being shifted out
    const int driven = rig->drive_count;
    CHECK(driven >= 1 && rig->shifter.responding());
    rig->sio_oe &= ~(1u << kClkPin);    // the clock pad is released: LOW -> PULL_DOWN, neither is HIGH
    CHECK(rig->clk.check_for_updates(0));
    CHECK(rig->drive_count == driven);  // not a falling edge: nothing is driven
    delete rig;
}

static void test_failures_stop_at_once() {
    {   // the host fails on a word: the failing edge reports it, the shifter is already past it
        Rig* rig = new Rig();
        rig->setup();
        rig->fail_word_on_call = 0;
        CHECK(rig->set_cs(true));
        for (int i = 31; i >= 1; --i) {
            CHECK(rig->set_data(true));
            CHECK(rig->set_clk(true));
            CHECK(rig->set_clk(false));
        }
        CHECK(rig->set_data(true));
        CHECK(!rig->set_clk(true));
        CHECK(rig->word_count == 1 && rig->shifter.bits_in_word() == 0);
        delete rig;
    }
    {   // the host fails on CS
        Rig* rig = new Rig();
        rig->setup();
        rig->fail_cs_on_call = 0;
        CHECK(!rig->set_cs(true));
        CHECK(rig->shifter.selected());  // the shifter had already taken the new state
        delete rig;
    }
    {   // driving the data pin fails on a falling edge
        Rig* rig = new Rig();
        rig->setup();
        CHECK(rig->set_cs(true));
        send_word(*rig, (0x7u << 2) | 1u);
        rig->fail_drive_on_call = rig->data_inputs;  // the first response bit was driven on the header's own last falling edge; fail the next
        CHECK(rig->set_clk(true));
        CHECK(!rig->set_clk(false));
        delete rig;
    }
    {   // reading the data pin's level fails on a rising edge: nothing is shifted in
        Rig* rig = new Rig();
        rig->setup();
        CHECK(rig->set_cs(true));
        rig->fail_data_source_on_call = rig->data_source_reads;
        CHECK(!rig->set_clk(true));
        CHECK(rig->shifter.bits_in_word() == 0);
        delete rig;
    }
}

static void test_capacity_and_detach() {
    Rig* rig = new Rig();
    rig->setup();
    static uint8_t big[GspiShifter::kResponseCapacity + 1];
    CHECK(rig->shifter.start_response(big, GspiShifter::kResponseCapacity));
    CHECK(!rig->shifter.start_response(big, GspiShifter::kResponseCapacity + 1));
    CHECK(rig->shifter.start_response(big, 0));  // empty means "nothing to say"
    CHECK(!rig->shifter.responding());
    rig->shifter.detach();
    CHECK(rig->set_cs(true));
    CHECK(rig->cs_count == 0);  // detached: the pin no longer tells it anything
    delete rig;
}

int main() {
    test_random_sequences_match_the_reference();
    test_a_word_reaches_the_host_and_its_response_is_shifted_out_msb_first();
    test_deselect_discards_a_partial_word_and_a_response();
    test_idle_clocking_does_nothing();
    test_a_clock_pin_that_is_released_counts_as_a_falling_edge();
    test_a_change_between_two_non_high_states_is_not_an_edge();
    test_failures_stop_at_once();
    test_capacity_and_detach();
    if (failures == 0) std::printf("test_gspi: all checks passed\n");
    return failures == 0 ? 0 : 1;
}

#include <stdint.h>
typedef struct { double ns_per_call; uint64_t checksum; uint32_t pin_toggles; } result_t;
result_t run_native(int n, int cb); result_t run_lua(int n, int cb); result_t run_qjs(int n, int cb); result_t run_mp(int n, int cb);

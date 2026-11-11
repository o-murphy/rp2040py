#include <stdint.h>
typedef struct { double ns_per_event; uint32_t flushes; uint32_t fnv; } lres_t;
typedef lres_t (*lfn)(const uint8_t*, int, int);
lres_t lcd_native(const uint8_t*, int, int); lres_t lcd_lua(const uint8_t*, int, int); lres_t lcd_qjs(const uint8_t*, int, int); lres_t lcd_mp(const uint8_t*, int, int);
double now(void);

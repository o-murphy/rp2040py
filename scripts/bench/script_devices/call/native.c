#include "common.h"
#include <time.h>
static uint32_t s; static uint32_t pins;
static void __attribute__((noinline)) set_pin(int p, int v){ pins += (v & 1) + p; }
static int __attribute__((noinline)) on_write(int64_t t, int64_t b, int cb){ s = (uint32_t)(s*31u + (uint32_t)(b>>1) + (uint32_t)t); if (cb && (b & 1)) set_pin(3, s & 1); return s & 1; }
double now(void){ struct timespec ts; clock_gettime(CLOCK_MONOTONIC,&ts); return ts.tv_sec*1e9+ts.tv_nsec; }
result_t run_native(int n, int cb){ s=0; pins=0; uint64_t sum=0; double t0=now(); int (*volatile f)(int64_t,int64_t,int)=on_write; for(int i=0;i<n;i++) sum+=f(i,i&0xFF,cb); double t1=now(); result_t r={(t1-t0)/n,sum,pins}; return r; }

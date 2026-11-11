#include "common.h"
#include <stdio.h>
typedef result_t(*fn_t)(int,int);
int main(void){ const char*names[]={"native C","Lua 5.4.8","QuickJS-ng 0.18","MicroPython 1.29"}; fn_t fns[]={run_native,run_lua,run_qjs,run_mp}; int N=1000000;
 for(int cb=0;cb<=1;cb++){ printf("\n%s\n", cb? "device calls back into host (set_pin) on odd bytes":"device only computes and returns an int");
  for(int k=0;k<4;k++){ double best=1e18; result_t last={0}; for(int rep=0;rep<5;rep++){ last=fns[k](N,cb); if(last.ns_per_call<best) best=last.ns_per_call; }
   printf("  %-18s %8.1f ns/call   checksum=%llu pins=%u\n",names[k],best,(unsigned long long)last.checksum,last.pin_toggles); } }
 return 0; }

#include "lcd.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc,char**argv){ FILE*f=fopen(argv[1],"rb"); fseek(f,0,SEEK_END); long sz=ftell(f); fseek(f,0,SEEK_SET); uint8_t*t=malloc(sz); fread(t,1,sz,f); fclose(f);
 setvbuf(stdout,NULL,_IOLBF,0); int n=sz/2; unsigned exp_fl=atoi(argv[2]); unsigned exp_h=strtoul(argv[3],0,10); int passes=atoi(argv[4]);
 const char*names[]={"native C","Lua 5.4.8","QuickJS-ng 0.18","MicroPython 1.29"}; lfn fns[]={lcd_native,lcd_lua,lcd_qjs,lcd_mp};
 printf("%d events per pass, %d passes\n",n,passes);
 for(int k=0;k<4;k++){ double best=1e18; lres_t r={0}; int reps=3; for(int i=0;i<reps;i++){ r=fns[k](t,n,passes); if(r.ns_per_event<best) best=r.ns_per_event; }
  int ok=(r.flushes==exp_fl*passes && r.fnv==exp_h);
  printf("  %-18s %8.1f ns/event  %7.2f ms/pass   flushes=%u fnv=%u %s\n",names[k],best,best*n/1e6,r.flushes,r.fnv,ok?"OK (matches reference)":"MISMATCH"); }
 return 0; }

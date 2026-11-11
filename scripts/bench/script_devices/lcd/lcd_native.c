#include "lcd.h"
#include <string.h>
#include <time.h>
double now(void){ struct timespec ts; clock_gettime(CLOCK_MONOTONIC,&ts); return ts.tv_sec*1e9+ts.tv_nsec; }
static uint8_t fb[25600]; static int cmd, pc, cs0, ce, rs, re, pidx, pbn, dirty, madctl, colmod, display_on, inverted; static uint8_t p[4], pb0; static uint32_t flushes;
static void reset(void){ cmd=-1; pc=0; pidx=0; pbn=0; dirty=0; cs0=0; ce=131; rs=0; re=161; madctl=0; colmod=5; display_on=0; inverted=0; memset(fb,0,sizeof fb); }
static void flush(void){ if(dirty){ dirty=0; flushes++; } }
static void on_command(int op){ cmd=op; pc=0; pbn=0; if(op==0x2C) pidx=0; else if(op==0x01) reset(); else if(op==0x29) display_on=1; else if(op==0x28) display_on=0; else if(op==0x21) inverted=1; else if(op==0x20) inverted=0; }
static void store_pixel(int hi,int lo){
  int ww=ce-cs0+1; if(ww<=0) return;
  int col=cs0+pidx%ww, row=rs+pidx/ww;
  if(madctl&0x20){ int t=col; col=row; row=t; }
  int nx=col-26, ny=row-1;
  if(!(nx>=0 && nx<80 && ny>=0 && ny<160)) return;
  if(madctl&0x40) nx=79-nx; if(madctl&0x80) ny=159-ny;
  int x=159-ny, y=nx; int off=(y*160+x)*2; fb[off]=hi; fb[off+1]=lo; dirty=1; }
static void on_pixel(int b){
  if(colmod!=5) return;
  if(pbn==0){ pb0=b; pbn=1; return; }
  int hi=pb0, lo=b; pbn=0;
  store_pixel(hi,lo); pidx++;
  int wp=(ce-cs0+1>0?ce-cs0+1:0)*(re-rs+1>0?re-rs+1:0);
  if(pidx>=wp){ pidx=0; flush(); } }
static void on_data(int b){
  if(cmd==0x2C){ on_pixel(b); return; }
  pc++; if(pc<=4) p[pc-1]=b;
  if(cmd==0x2A && pc==4){ cs0=(p[0]<<8)|p[1]; ce=(p[2]<<8)|p[3]; }
  else if(cmd==0x2B && pc==4){ rs=(p[0]<<8)|p[1]; re=(p[2]<<8)|p[3]; }
  else if(cmd==0x36 && pc==1) madctl=b; else if(cmd==0x3A && pc==1) colmod=b; }
static void __attribute__((noinline)) ev(int f,int b){
  if(f&0x80){ pbn=0; flush(); } else if(f&0x40) reset(); else if(f&1){ if(f&2) on_data(b); else on_command(b); } }
lres_t lcd_native(const uint8_t*t,int n,int passes){
  flushes=0; void(*volatile fn)(int,int)=ev; double t0=now();
  for(int k=0;k<passes;k++){ fn(0x40,0); for(int i=0;i<n;i++) fn(t[2*i],t[2*i+1]); }
  double t1=now(); uint32_t h=2166136261u; for(int i=0;i<25600;i++) h=(h^fb[i])*16777619u;
  lres_t r={(t1-t0)/((double)passes*n),flushes,h}; return r; }

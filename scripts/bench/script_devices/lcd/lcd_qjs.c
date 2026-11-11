#include "lcd.h"
#include <string.h>
#include "quickjs.h"
static uint32_t flushes;
static JSValue js_frame_done(JSContext*c, JSValueConst th, int argc, JSValueConst*argv){ flushes++; return JS_UNDEFINED; }
static const char*SRC =
"var ev, checksum;\n(function(){\n"
"var fb=new Uint8Array(25600);\n"
"var cmd=-1,pc=0,cs0=0,ce=131,rs=0,re=161,pidx=0,pbn=0,pb0=0,dirty=false,madctl=0,colmod=5,display_on=false,inverted=false;\n"
"var p1=0,p2=0,p3=0,p4=0;\n"
"function reset(){ cmd=-1;pc=0;pidx=0;pbn=0;dirty=false;cs0=0;ce=131;rs=0;re=161;madctl=0;colmod=5;display_on=false;inverted=false;fb.fill(0); }\n"
"function flush(){ if(dirty){ dirty=false; frame_done(); } }\n"
"function on_command(op){ cmd=op;pc=0;pbn=0;\n"
" if(op===0x2C) pidx=0; else if(op===0x01) reset(); else if(op===0x29) display_on=true; else if(op===0x28) display_on=false; else if(op===0x21) inverted=true; else if(op===0x20) inverted=false; }\n"
"function store_pixel(hi,lo){\n"
" var ww=ce-cs0+1; if(ww<=0) return;\n"
" var col=cs0+(pidx%ww), row=rs+((pidx/ww)|0);\n"
" if(madctl&0x20){ var t=col; col=row; row=t; }\n"
" var nx=col-26, ny=row-1;\n"
" if(!(nx>=0 && nx<80 && ny>=0 && ny<160)) return;\n"
" if(madctl&0x40) nx=79-nx; if(madctl&0x80) ny=159-ny;\n"
" var x=159-ny, y=nx, off=(y*160+x)*2; fb[off]=hi; fb[off+1]=lo; dirty=true; }\n"
"function on_pixel(b){\n"
" if(colmod!==5) return;\n"
" if(pbn===0){ pb0=b; pbn=1; return; }\n"
" pbn=0; store_pixel(pb0,b); pidx++;\n"
" var cw=ce-cs0+1; if(cw<0) cw=0; var rh=re-rs+1; if(rh<0) rh=0;\n"
" if(pidx>=cw*rh){ pidx=0; flush(); } }\n"
"function on_data(b){\n"
" if(cmd===0x2C){ on_pixel(b); return; }\n"
" pc++; if(pc===1) p1=b; else if(pc===2) p2=b; else if(pc===3) p3=b; else if(pc===4) p4=b;\n"
" if(cmd===0x2A && pc===4){ cs0=(p1<<8)|p2; ce=(p3<<8)|p4; }\n"
" else if(cmd===0x2B && pc===4){ rs=(p1<<8)|p2; re=(p3<<8)|p4; }\n"
" else if(cmd===0x36 && pc===1) madctl=b; else if(cmd===0x3A && pc===1) colmod=b; }\n"
"ev=function(f,b){ if(f&0x80){ pbn=0; flush(); } else if(f&0x40) reset(); else if(f&1){ if(f&2) on_data(b); else on_command(b); } };\n"
"checksum=function(){ var h=2166136261; for(var i=0;i<25600;i++) h=Math.imul(h^fb[i],16777619)>>>0; return h; };\n"
"})();\n";
lres_t lcd_qjs(const uint8_t*t,int n,int passes){
  JSRuntime*rt=JS_NewRuntime(); JSContext*c=JS_NewContext(rt); flushes=0; JSValue g=JS_GetGlobalObject(c);
  JS_SetPropertyStr(c,g,"frame_done",JS_NewCFunction(c,js_frame_done,"frame_done",0));
  JSValue v=JS_Eval(c,SRC,strlen(SRC),"lcd.js",JS_EVAL_TYPE_GLOBAL); if(JS_IsException(v)){ lres_t e={-1,0,0}; return e; } JS_FreeValue(c,v);
  JSValue fn=JS_GetPropertyStr(c,g,"ev");
  double t0=now();
  for(int k=0;k<passes;k++){
    JSValue a[2]={JS_NewInt32(c,0x40),JS_NewInt32(c,0)}; JS_FreeValue(c,JS_Call(c,fn,JS_UNDEFINED,2,a));
    for(int i=0;i<n;i++){ JSValue a2[2]={JS_NewInt32(c,t[2*i]),JS_NewInt32(c,t[2*i+1])}; JS_FreeValue(c,JS_Call(c,fn,JS_UNDEFINED,2,a2)); } }
  double t1=now(); JSValue cf=JS_GetPropertyStr(c,g,"checksum"); JSValue r=JS_Call(c,cf,JS_UNDEFINED,0,NULL); uint32_t h; JS_ToUint32(c,&h,r);
  lres_t res={(t1-t0)/((double)passes*n),flushes,h}; return res; }

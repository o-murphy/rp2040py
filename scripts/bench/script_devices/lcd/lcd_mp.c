#include "lcd.h"
#include "port/micropython_embed.h"
#include "py/obj.h"
#include "py/runtime.h"
#include "py/qstr.h"
static uint32_t flushes; static char heap[2*1024*1024];
static mp_obj_t py_frame_done(void){ flushes++; return mp_const_none; }
static MP_DEFINE_CONST_FUN_OBJ_0(frame_done_obj, py_frame_done);
static const char*SRC =
"fb=bytearray(25600)\n"
"cmd=-1;pc=0;cs0=0;ce=131;rs=0;re=161;pidx=0;pbn=0;pb0=0;dirty=False;madctl=0;colmod=5;display_on=False;inverted=False\n"
"p1=p2=p3=p4=0\n"
"def reset():\n"
"    global cmd,pc,pidx,pbn,dirty,cs0,ce,rs,re,madctl,colmod,display_on,inverted,fb\n"
"    cmd=-1;pc=0;pidx=0;pbn=0;dirty=False;cs0=0;ce=131;rs=0;re=161;madctl=0;colmod=5;display_on=False;inverted=False\n"
"    fb=bytearray(25600)\n"
"def flush():\n"
"    global dirty\n"
"    if dirty:\n"
"        dirty=False\n"
"        frame_done()\n"
"def on_command(op):\n"
"    global cmd,pc,pbn,pidx,display_on,inverted\n"
"    cmd=op;pc=0;pbn=0\n"
"    if op==0x2C: pidx=0\n"
"    elif op==0x01: reset()\n"
"    elif op==0x29: display_on=True\n"
"    elif op==0x28: display_on=False\n"
"    elif op==0x21: inverted=True\n"
"    elif op==0x20: inverted=False\n"
"def store_pixel(hi,lo):\n"
"    global dirty\n"
"    ww=ce-cs0+1\n"
"    if ww<=0: return\n"
"    col=cs0+pidx%ww; row=rs+pidx//ww\n"
"    if madctl&0x20: col,row=row,col\n"
"    nx=col-26; ny=row-1\n"
"    if not (0<=nx<80 and 0<=ny<160): return\n"
"    if madctl&0x40: nx=79-nx\n"
"    if madctl&0x80: ny=159-ny\n"
"    off=((nx)*160+(159-ny))*2\n"
"    fb[off]=hi; fb[off+1]=lo; dirty=True\n"
"def on_pixel(b):\n"
"    global pbn,pb0,pidx\n"
"    if colmod!=5: return\n"
"    if pbn==0:\n"
"        pb0=b; pbn=1; return\n"
"    pbn=0; store_pixel(pb0,b); pidx+=1\n"
"    cw=max(0,ce-cs0+1); rh=max(0,re-rs+1)\n"
"    if pidx>=cw*rh:\n"
"        pidx=0; flush()\n"
"def on_data(b):\n"
"    global pc,p1,p2,p3,p4,cs0,ce,rs,re,madctl,colmod\n"
"    if cmd==0x2C:\n"
"        on_pixel(b); return\n"
"    pc+=1\n"
"    if pc==1: p1=b\n"
"    elif pc==2: p2=b\n"
"    elif pc==3: p3=b\n"
"    elif pc==4: p4=b\n"
"    if cmd==0x2A and pc==4: cs0=(p1<<8)|p2; ce=(p3<<8)|p4\n"
"    elif cmd==0x2B and pc==4: rs=(p1<<8)|p2; re=(p3<<8)|p4\n"
"    elif cmd==0x36 and pc==1: madctl=b\n"
"    elif cmd==0x3A and pc==1: colmod=b\n"
"def ev(f,b):\n"
"    global pbn\n"
"    if f&0x80:\n"
"        pbn=0; flush()\n"
"    elif f&0x40: reset()\n"
"    elif f&1:\n"
"        if f&2: on_data(b)\n"
"        else: on_command(b)\n"
"def checksum():\n"
"    h=2166136261\n"
"    for x in fb: h=((h^x)*16777619)&0xFFFFFFFF\n"
"    return h\n";
lres_t lcd_mp(const uint8_t*t,int n,int passes){
  int top; static int inited=0; flushes=0;
  if(!inited){ mp_embed_init(heap,sizeof(heap),&top); inited=1; }
  mp_store_global(qstr_from_str("frame_done"), MP_OBJ_FROM_PTR(&frame_done_obj));
  mp_embed_exec_str(SRC);
  mp_obj_t fn=mp_load_global(qstr_from_str("ev"));
  double t0=now();
  for(int k=0;k<passes;k++){
    mp_obj_t a[2]={MP_OBJ_NEW_SMALL_INT(0x40),MP_OBJ_NEW_SMALL_INT(0)}; mp_call_function_n_kw(fn,2,0,a);
    for(int i=0;i<n;i++){ mp_obj_t b[2]={MP_OBJ_NEW_SMALL_INT(t[2*i]),MP_OBJ_NEW_SMALL_INT(t[2*i+1])}; mp_call_function_n_kw(fn,2,0,b); } }
  double t1=now(); mp_obj_t cf=mp_load_global(qstr_from_str("checksum")); uint32_t h=(uint32_t)mp_obj_get_int(mp_call_function_0(cf));
  lres_t r={(t1-t0)/((double)passes*n),flushes,h}; return r; }

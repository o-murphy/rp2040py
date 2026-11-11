#include "common.h"
#include "port/micropython_embed.h"
#include "py/obj.h"
#include "py/runtime.h"
#include "py/qstr.h"
double now(void);
static uint32_t pins; static char heap[512*1024];
static mp_obj_t py_set_pin(mp_obj_t a, mp_obj_t b){ pins += (mp_obj_get_int(b)&1)+mp_obj_get_int(a); return mp_const_none; }
static MP_DEFINE_CONST_FUN_OBJ_2(set_pin_obj, py_set_pin);
result_t run_mp(int n, int cb){
  int top; static int inited=0; pins=0;
  if(!inited){ mp_embed_init(heap,sizeof(heap),&top); inited=1; }
  mp_store_global(qstr_from_str("set_pin"), MP_OBJ_FROM_PTR(&set_pin_obj));
  mp_embed_exec_str("s=0\ndef on_write(t,b,cb):\n    global s\n    s=(s*31+(b>>1)+t)&0xFFFFFFFF\n    if cb!=0 and (b&1)==1:\n        set_pin(3,s&1)\n    return s&1\ns=0\n");
  mp_obj_t fn=mp_load_global(qstr_from_str("on_write"));
  uint64_t sum=0; double t0=now();
  for(int i=0;i<n;i++){ mp_obj_t args[3]={MP_OBJ_NEW_SMALL_INT(i),MP_OBJ_NEW_SMALL_INT(i&0xFF),MP_OBJ_NEW_SMALL_INT(cb)}; mp_obj_t r=mp_call_function_n_kw(fn,3,0,args); sum+=mp_obj_get_int(r); }
  double t1=now(); result_t r={(t1-t0)/n,sum,pins}; return r; }

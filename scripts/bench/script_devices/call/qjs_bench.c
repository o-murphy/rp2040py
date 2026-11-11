#include "common.h"
#include "quickjs.h"
double now(void);
static uint32_t pins;
static JSValue js_set_pin(JSContext*c, JSValueConst th, int argc, JSValueConst*argv){ int a,b; JS_ToInt32(c,&a,argv[0]); JS_ToInt32(c,&b,argv[1]); pins += (b&1)+a; return JS_UNDEFINED; }
result_t run_qjs(int n, int cb){
  JSRuntime*rt=JS_NewRuntime(); JSContext*c=JS_NewContext(rt); pins=0;
  JSValue g=JS_GetGlobalObject(c);
  JS_SetPropertyStr(c,g,"set_pin",JS_NewCFunction(c,js_set_pin,"set_pin",2));
  const char*src="var s=0;\nfunction on_write(t,b,cb){ s=(s*31+(b>>1)+t)>>>0; if(cb!==0 && (b&1)===1) set_pin(3,s&1); return s&1; }\n";
  JSValue v=JS_Eval(c,src,strlen(src),"dev.js",JS_EVAL_TYPE_GLOBAL); if(JS_IsException(v)) return (result_t){-1,0,0}; JS_FreeValue(c,v);
  JSValue fn=JS_GetPropertyStr(c,g,"on_write");
  uint64_t sum=0; double t0=now();
  for(int i=0;i<n;i++){ JSValue a[3]={JS_NewInt32(c,i),JS_NewInt32(c,i&0xFF),JS_NewInt32(c,cb)}; JSValue r=JS_Call(c,fn,JS_UNDEFINED,3,a); int32_t x; JS_ToInt32(c,&x,r); sum+=x; JS_FreeValue(c,r); }
  double t1=now(); JS_FreeValue(c,fn); JS_FreeValue(c,g); JS_FreeContext(c); JS_FreeRuntime(rt); result_t r={(t1-t0)/n,sum,pins}; return r; }

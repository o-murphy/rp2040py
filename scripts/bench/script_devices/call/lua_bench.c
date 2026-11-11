#include "common.h"
#include <lua.h>
#include <lauxlib.h>
#include <lualib.h>
double now(void);
static uint32_t pins;
static int l_set_pin(lua_State*L){ pins += (lua_tointeger(L,2) & 1) + lua_tointeger(L,1); return 0; }
result_t run_lua(int n, int cb){
  lua_State*L=luaL_newstate(); luaL_openlibs(L); pins=0;
  lua_pushcfunction(L,l_set_pin); lua_setglobal(L,"set_pin");
  const char*src="local s=0\nfunction on_write(t,b,cb)\n s=(s*31+(b>>1)+t)&0xFFFFFFFF\n if cb~=0 and (b&1)==1 then set_pin(3,s&1) end\n return s&1\nend\n";
  if(luaL_dostring(L,src)){ return (result_t){-1,0,0}; }
  lua_getglobal(L,"on_write"); int ref=luaL_ref(L,LUA_REGISTRYINDEX);
  uint64_t sum=0; double t0=now();
  for(int i=0;i<n;i++){ lua_rawgeti(L,LUA_REGISTRYINDEX,ref); lua_pushinteger(L,i); lua_pushinteger(L,i&0xFF); lua_pushinteger(L,cb); lua_call(L,3,1); sum+=lua_tointeger(L,-1); lua_pop(L,1); }
  double t1=now(); lua_close(L); result_t r={(t1-t0)/n,sum,pins}; return r; }

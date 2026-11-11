#include "lcd.h"
#include <lua.h>
#include <lauxlib.h>
#include <lualib.h>
static uint32_t flushes;
static int l_frame_done(lua_State*L){ (void)L; flushes++; return 0; }
static const char*SRC =
"local fb={} for i=1,25600 do fb[i]=0 end\n"
"local cmd,pc,cs0,ce,rs,re,pidx,pbn,pb0,dirty,madctl,colmod=-1,0,0,131,0,161,0,0,0,false,0,5\n"
"local display_on,inverted=false,false\n"
"local p1,p2,p3,p4=0,0,0,0\n"
"local function reset() cmd=-1 pc=0 pidx=0 pbn=0 dirty=false cs0=0 ce=131 rs=0 re=161 madctl=0 colmod=5 display_on=false inverted=false for i=1,25600 do fb[i]=0 end end\n"
"local function flush() if dirty then dirty=false frame_done() end end\n"
"local function on_command(op) cmd=op pc=0 pbn=0\n"
" if op==0x2C then pidx=0 elseif op==0x01 then reset() elseif op==0x29 then display_on=true elseif op==0x28 then display_on=false elseif op==0x21 then inverted=true elseif op==0x20 then inverted=false end end\n"
"local function store_pixel(hi,lo)\n"
" local ww=ce-cs0+1 if ww<=0 then return end\n"
" local col=cs0+pidx%ww local row=rs+pidx//ww\n"
" if madctl&0x20~=0 then col,row=row,col end\n"
" local nx=col-26 local ny=row-1\n"
" if not (nx>=0 and nx<80 and ny>=0 and ny<160) then return end\n"
" if madctl&0x40~=0 then nx=79-nx end if madctl&0x80~=0 then ny=159-ny end\n"
" local x=159-ny local y=nx local off=(y*160+x)*2\n"
" fb[off+1]=hi fb[off+2]=lo dirty=true end\n"
"local function on_pixel(b)\n"
" if colmod~=5 then return end\n"
" if pbn==0 then pb0=b pbn=1 return end\n"
" pbn=0 store_pixel(pb0,b) pidx=pidx+1\n"
" local cw=ce-cs0+1 if cw<0 then cw=0 end local rh=re-rs+1 if rh<0 then rh=0 end\n"
" if pidx>=cw*rh then pidx=0 flush() end end\n"
"local function on_data(b)\n"
" if cmd==0x2C then on_pixel(b) return end\n"
" pc=pc+1 if pc==1 then p1=b elseif pc==2 then p2=b elseif pc==3 then p3=b elseif pc==4 then p4=b end\n"
" if cmd==0x2A and pc==4 then cs0=(p1<<8)|p2 ce=(p3<<8)|p4\n"
" elseif cmd==0x2B and pc==4 then rs=(p1<<8)|p2 re=(p3<<8)|p4\n"
" elseif cmd==0x36 and pc==1 then madctl=b elseif cmd==0x3A and pc==1 then colmod=b end end\n"
"function ev(f,b)\n"
" if f&0x80~=0 then pbn=0 flush() elseif f&0x40~=0 then reset() elseif f&1~=0 then if f&2~=0 then on_data(b) else on_command(b) end end end\n"
"function checksum() local h=2166136261 for i=1,25600 do h=((h~fb[i])*16777619)&0xFFFFFFFF end return h end\n";
lres_t lcd_lua(const uint8_t*t,int n,int passes){
  lua_State*L=luaL_newstate(); luaL_openlibs(L); flushes=0;
  lua_pushcfunction(L,l_frame_done); lua_setglobal(L,"frame_done");
  if(luaL_dostring(L,SRC)){ lres_t e={-1,0,0}; return e; }
  lua_getglobal(L,"ev"); int ref=luaL_ref(L,LUA_REGISTRYINDEX);
  double t0=now();
  for(int k=0;k<passes;k++){
    lua_rawgeti(L,LUA_REGISTRYINDEX,ref); lua_pushinteger(L,0x40); lua_pushinteger(L,0); lua_call(L,2,0);
    for(int i=0;i<n;i++){ lua_rawgeti(L,LUA_REGISTRYINDEX,ref); lua_pushinteger(L,t[2*i]); lua_pushinteger(L,t[2*i+1]); lua_call(L,2,0); } }
  double t1=now(); lua_getglobal(L,"checksum"); lua_call(L,0,1); uint32_t h=(uint32_t)lua_tointeger(L,-1); lua_close(L);
  lres_t r={(t1-t0)/((double)passes*n),flushes,h}; return r; }

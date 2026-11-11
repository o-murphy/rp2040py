typedef int (*cb_t)(void*, unsigned);
int noop(int a){return a;}
int call_cb(cb_t cb, void* ctx, int n){int s=0;for(int i=0;i<n;i++)s+=cb(ctx,(unsigned)i);return s;}

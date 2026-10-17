#define STR(x) #x
#define D(x) STR(x)
#define O(c) D(omp c)
const char *a = D(a   b);
const char *b = O(parallel for);
const char *c = O(  parallel   /* x */   for  );

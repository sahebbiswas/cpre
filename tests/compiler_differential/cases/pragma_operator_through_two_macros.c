#define DO_PRAGMA(x) _Pragma(#x)
#define OMP(c) DO_PRAGMA(omp c)
OMP(parallel for)
int x;

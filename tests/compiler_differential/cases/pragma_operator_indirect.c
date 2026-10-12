#define DO_PRAGMA(x) _Pragma(#x)
#define OMP(clause) DO_PRAGMA(omp clause)
OMP(parallel for)

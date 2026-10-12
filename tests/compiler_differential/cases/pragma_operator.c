#define DO_PRAGMA(x) _Pragma(#x)
int before;
_Pragma("message(\"hi\")")
DO_PRAGMA(omp parallel for)
int after;

#define A 1
#if A
int a;
#  if B
int never_b;
#  elif defined(A)
int a_not_b;
#    ifdef C
int never_c;
#    else
int deepest;
#    endif
#  endif
#else
#  if A
int never_inner;
#  endif
#endif

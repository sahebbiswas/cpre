#define A 0
#if A
int a;
#elif 1 /* the condition
           continues here */ && 0
int b;
#elif 1 /* another comment
  that spans lines */ && 1
int c;
#else
int d;
#endif

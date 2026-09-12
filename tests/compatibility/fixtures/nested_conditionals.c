#define FEATURE 1
#define INNER 1

#if FEATURE
int outer = 1;
#if INNER
int inner = 2;
#else
int inner = 0;
#endif
#else
int outer = 0;
#endif

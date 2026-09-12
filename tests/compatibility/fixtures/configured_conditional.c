#if FEATURE
#define SCALE(x) ((x) * 2)
int configured = SCALE(21);
#else
int configured = 0;
#endif

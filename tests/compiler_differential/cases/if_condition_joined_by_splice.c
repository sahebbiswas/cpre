// A condition continued onto a second line reads as one expression.
#define A 1
#define B 0
#if A\
B
int joined = 1;
#else
int joined = 0;
#endif

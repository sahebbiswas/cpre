// A macro that refers to itself: the inner name is left alone and counts as 0.
#define A A + 1
#if A > 0
int yes = 1;
#else
int yes = 0;
#endif

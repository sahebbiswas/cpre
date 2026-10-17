// A self-referential name that evaluates to 0 selects the #else branch.
#define A A
#if A
int branch = 1;
#else
int branch = 2;
#endif

// defined does not expand its operand, so a self-referential macro is simply defined.
#define A A + 1
#if defined(A) && defined A
int yes = 1;
#else
int yes = 0;
#endif

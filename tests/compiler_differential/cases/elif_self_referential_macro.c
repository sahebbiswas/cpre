// The self-referential name is 0 in an #elif as well.
#define A A + 1
#define Z 0
#if Z
int branch = 1;
#elif A == 1
int branch = 2;
#else
int branch = 3;
#endif

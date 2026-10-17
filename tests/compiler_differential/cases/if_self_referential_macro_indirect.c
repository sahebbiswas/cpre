// Two macros that refer to each other: the name that comes back around counts as 0.
#define B C + 1
#define C B
#if B > 0
int yes = 1;
#else
int yes = 0;
#endif

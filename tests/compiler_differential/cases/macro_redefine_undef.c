#define LEVEL 1
int first = LEVEL;
#undef LEVEL
int second = LEVEL;
#define LEVEL 3
int third = LEVEL;
#undef NEVER_DEFINED

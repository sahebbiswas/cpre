#define VERSION 3
#define AT_LEAST(v) (VERSION >= (v))
#if AT_LEAST(2) && !AT_LEAST(4)
int in_range;
#endif
#define EMPTY_FLAG
#if defined(EMPTY_FLAG) && VERSION
int both;
#endif

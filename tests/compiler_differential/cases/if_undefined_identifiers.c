#if UNDEFINED_NAME
int never;
#endif
#if UNDEFINED_NAME == 0 && !UNDEFINED_NAME
int undefined_is_zero;
#endif
#define ZERO 0
#if ZERO || UNDEFINED_NAME
int never_either;
#else
int both_false;
#endif

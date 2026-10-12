#define TEMP 1
#undef TEMP
#ifdef TEMP
int never;
#else
int undefined_again;
#endif
#if defined(TEMP) || TEMP
int never_either;
#endif

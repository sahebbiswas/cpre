// cpre-differential: -std=c2x
#define M(a, ...) (a __VA_OPT__(+ __VA_ARGS__))
#if M(2) == 2 && M(1, 4) == 5
yes
#else
no
#endif

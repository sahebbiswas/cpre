// cpre-differential: -std=c2x
#define STR(...) #__VA_OPT__(a b   c)
#define XSTR(...) #__VA_ARGS__
const char *s = STR(1);
    const char *t = STR(1);
const char *u = XSTR(a   b,c ,  d);

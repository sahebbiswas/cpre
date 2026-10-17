#define STR(x) #x
#define CAT(a, b) a ## b
#define D(x) STR(x)
const char *s = D(CAT(x, y) CAT(p, q));
const char *t = D(CAT(x, y)CAT(p, q));

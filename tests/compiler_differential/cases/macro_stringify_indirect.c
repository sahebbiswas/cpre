#define STR(x) #x
#define XSTR(x) STR(x)
const char *s = XSTR(a b);

#define STR(x) #x
#define D(x) STR(x)
#define PAIR one two
#define EMPTY
const char *a = D(PAIR);
const char *b = D(EMPTY a EMPTY b);
const char *c = D(PAIR+PAIR);
const char *d = D(-PAIR-);

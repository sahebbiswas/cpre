#define STR(x) #x
#define XSTR(x) STR(x)
#define VALUE 42
const char *a = STR(VALUE);
const char *b = XSTR(VALUE);
const char *c = STR(  a   +    b  );
const char *d = STR("quoted \n" 'c');
const char *e = STR();

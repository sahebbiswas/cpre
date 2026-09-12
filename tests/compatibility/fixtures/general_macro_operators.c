#define CAT(a, b) a ## b
#define STRINGIFY(x) #x
#define EXPAND_STRING(x) STRINGIFY(x)
#define VALUE 41
#define TWICE(x) ((x) + (x))

int CAT(ans, wer) = TWICE(VALUE);
const char *label = EXPAND_STRING(CAT(ans, wer));

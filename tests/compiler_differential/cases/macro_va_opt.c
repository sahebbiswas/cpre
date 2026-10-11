// cpre-differential: -std=c2x
#define LOG(fmt, ...) printf(fmt __VA_OPT__(,) __VA_ARGS__)
#define WRAP(...) (__VA_OPT__(1 +) 0)
LOG("empty", );
LOG("args %d", 1);
int a = WRAP();
int b = WRAP(x);

#define WRAP(...) __VA_OPT__(__VA_ARGS__)
int value = WRAP(1);

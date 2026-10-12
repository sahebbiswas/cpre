#define CALL(f, ...) f(__VA_ARGS__)
#define COUNT(...) count(__VA_ARGS__)
#define SHOW(...) #__VA_ARGS__
int a = CALL(g, 1, 2, 3);
int b = COUNT();
const char *c = SHOW(x, y,   z);

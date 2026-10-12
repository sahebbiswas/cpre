#define foo (4 + foo)
#define x (4 + y)
#define y (2 * x)
int a = foo;
int b = x;
int c = y;

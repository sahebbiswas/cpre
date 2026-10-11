#define f(a) a*g
#define g(a) f(a)
#define AB x
#define CAT(a, b) a ## b
#define XCAT(a, b) CAT(a, b)
#define A A_EXPANDED
int r = f(2)(9);
int s = XCAT(A, B);
int t = CAT(A, B);

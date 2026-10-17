// cpre-differential: -std=c2x
#define M(a, ...) [a|__VA_ARGS__|#__VA_ARGS__] a __VA_OPT__(<__VA_ARGS__> #a a ## a) end
#define P(a, ...) a ## __VA_ARGS__ ## a
#define Q(a, b, ...) a-b-__VA_ARGS__-
#define E
int one = M(1);
int empty = M(1, );
int some = M(1, x y);
int paste = P(x);
int pasted = P(x, E);
int several = Q(1, 2);
int several_more = Q(1, 2, 3);
int none = M();

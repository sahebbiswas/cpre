#define CAT(a, b) a ## b
#define CAT3(a, b, c) a ## b ## c
#define MEMBER(n) field_ ## n
#define EMPTY
int CAT(var, 1) = CAT(0x, 1f);
int CAT3(x, y, z);
int MEMBER(count);
int CAT(, tail) = CAT(head, );
int CAT(<, <) ;

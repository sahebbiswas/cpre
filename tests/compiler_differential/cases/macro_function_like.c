#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define SQUARE(x) ((x) * (x))
int m = MAX(1, SQUARE(2 + 3));
int n = MAX((1, 2), 3);
int not_invoked = MAX;
int spaced = MAX   (4, 5);

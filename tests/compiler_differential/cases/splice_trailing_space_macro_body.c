// A backslash followed by blanks and a newline splices, as in GCC and Clang.
#define SUM(a, b) \ 
    ((a) + (b))
int total = SUM(1, 2);

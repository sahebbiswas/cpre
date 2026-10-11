#if 1 + 2 * 3 == 7 && (1 << 4) == 16 && 7 / 2 == 3 && 7 % 3 == 1
int precedence;
#endif
#if -1 < 0 && ~0 == -1 && (5 & 3) == 1 && (5 | 3) == 7 && (5 ^ 3) == 6
int bitwise;
#endif
#if (1 ? 2 : 3) == 2 && (0 ? 2 : 3) == 3 && !0 && !!5
int ternary;
#endif
#if 0
int never;
#elif 2 > 1
int elif_taken;
#else
int never_else;
#endif

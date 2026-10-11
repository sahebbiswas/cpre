#define LONG_MACRO(a, \
                   b) \
    ((a) + \
     (b))
int x = LONG_MACRO(1, 2);
#if 1 /* comment */ && 1 // trailing
int after_comment;
#endif
# /* null directive with comment */
#
int after_null;

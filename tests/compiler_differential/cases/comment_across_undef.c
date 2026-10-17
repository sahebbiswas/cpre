#define X 1
#undef X /* comment
            spanning lines */
#ifdef X
int still_defined;
#else
int undefined;
#endif
int after = __LINE__;

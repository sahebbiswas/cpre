#pragma message("two  spaces")
#define DO_PRAGMA(x) _Pragma(#x)
DO_PRAGMA(message("built   by  a macro"))
int after;

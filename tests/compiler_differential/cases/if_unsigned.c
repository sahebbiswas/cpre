#if -1 > 0u
int unsigned_conversion;
#else
int signed_comparison;
#endif
#if 0xFFFFFFFFFFFFFFFF == -1
int wraps;
#endif
#if 18446744073709551615u / 2 == 9223372036854775807
int uintmax_division;
#endif

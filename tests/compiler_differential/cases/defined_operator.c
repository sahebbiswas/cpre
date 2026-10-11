#define FEATURE
#define OTHER 0
#if defined FEATURE && defined(FEATURE) && defined ( OTHER )
int all_defined;
#endif
#if !defined(MISSING) && !defined MISSING
int missing;
#endif
#ifdef FEATURE
int ifdef_taken;
#endif
#ifndef MISSING
int ifndef_taken;
#endif
#ifdef MISSING
int never;
#else
int else_taken;
#endif

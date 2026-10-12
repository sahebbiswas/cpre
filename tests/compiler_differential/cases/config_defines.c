// cpre-differential: -DFEATURE -DLEVEL=2 -DNAME=widget -USTALE -DSCALE(x)=((x)*10)
#ifdef FEATURE
int feature_on;
#endif
#if LEVEL > 1
int level = LEVEL;
#endif
#ifdef STALE
int never;
#endif
const char *n = "NAME";
int NAME;
int scaled = SCALE(3);

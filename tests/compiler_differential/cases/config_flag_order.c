// cpre-differential: -DREMOVED=1 -UREMOVED -UADDED -DADDED=2 -DTWICE=1 -DTWICE=3
#ifdef REMOVED
int never;
#endif
int added = ADDED;
int twice = TWICE;

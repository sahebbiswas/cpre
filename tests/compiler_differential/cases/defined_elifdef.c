// cpre-differential: -std=c2x -DSECOND
#ifdef FIRST
int first;
#elifdef SECOND
int second;
#elifndef THIRD
int third;
#endif
#ifdef FIRST
#elifndef FIRST
int not_first;
#endif

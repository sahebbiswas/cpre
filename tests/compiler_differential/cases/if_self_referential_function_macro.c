// A function-like macro whose replacement names itself: the name is not replaced again and counts as 0.
#define F(x) x + F
#if F(1) == 1
int yes = 1;
#else
int yes = 0;
#endif

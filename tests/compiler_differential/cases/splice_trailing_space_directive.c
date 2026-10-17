// cpre-differential: -DWANT=1
#if defined(WANT) && \ 	
    WANT == 1
int wanted = 1;
#else
int wanted = 0;
#endif

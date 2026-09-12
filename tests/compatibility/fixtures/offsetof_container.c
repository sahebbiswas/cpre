typedef unsigned long size_t;

struct item {
    int value;
};

#define offsetof(T, m) ((size_t)&(((T *)0)->m))
#define container_of(p, T, m) ((T *)((char *)(p) - offsetof(T, m)))

struct item *recover(int *member)
{
    return container_of(member, struct item, value);
}

#define ADD(x, y) ((x) + (y))
#define TWICE(v) ADD(v, v)
#define VALUE \
    3

int compute(void)
{
    return TWICE(VALUE);
}

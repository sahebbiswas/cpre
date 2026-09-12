
#define PROJECT_LIMIT 4
static int header_value(int value) { return value + PROJECT_LIMIT; }

int run(void) {
    return header_value(2);
}

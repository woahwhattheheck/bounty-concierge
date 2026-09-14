#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

#define MAX_CFG_RECURSION_DEPTH 128U

struct block { struct block *jt, *jf; unsigned mark; unsigned level; };
struct icode { unsigned cur_mark; struct block *root; };
#define JT(p) ((p)->jt)
#define JF(p) ((p)->jf)
#define isMarked(icp,p) ((p)->mark == (icp)->cur_mark)
#define unMarkAll(icp) ((icp)->cur_mark += 1)
#define Mark(icp,p) ((p)->mark = (icp)->cur_mark)

struct depth_item { struct block *block; unsigned depth; };

static int check_cfg_depth(struct icode *ic, struct block *root, unsigned limit) {
    size_t cap = 64, count = 0;
    struct depth_item *stack = malloc(cap * sizeof(*stack));
    if (!stack) return -1;
    unMarkAll(ic);
    stack[count++] = (struct depth_item){ root, 1 };
    while (count != 0) {
        struct depth_item item = stack[--count];
        struct block *p = item.block;
        if (!p) continue;
        if (item.depth > limit) { free(stack); return 0; }
        if (isMarked(ic, p)) {
            if (p->level >= item.depth) continue;
            p->level = item.depth;
        } else {
            Mark(ic, p);
            p->level = item.depth;
        }
        if (JT(p) || JF(p)) {
            if (count + 2 > cap) {
                if (cap > SIZE_MAX / 2 / sizeof(*stack)) { free(stack); return -1; }
                size_t new_cap = cap * 2;
                struct depth_item *new_stack = realloc(stack, new_cap * sizeof(*stack));
                if (!new_stack) { free(stack); return -1; }
                stack = new_stack; cap = new_cap;
            }
            if (JF(p)) stack[count++] = (struct depth_item){ JF(p), item.depth + 1 };
            if (JT(p)) stack[count++] = (struct depth_item){ JT(p), item.depth + 1 };
        }
    }
    free(stack);
    return 1;
}

/* Exact recursive shape from upstream optimize.c #27. */
static int count_blocks(struct icode *ic, struct block *p) {
    if (p == 0 || isMarked(ic, p)) return 0;
    Mark(ic, p);
    return count_blocks(ic, JT(p)) + count_blocks(ic, JF(p)) + 1;
}

static struct block *chain(size_t n) {
    struct block *b = calloc(n, sizeof(*b));
    if (!b) return NULL;
    for (size_t i = 0; i + 1 < n; ++i) b[i].jt = &b[i+1];
    return b;
}

static int expect_unpatched_stack_failure(void) {
    pid_t pid = fork();
    if (pid < 0) return 0;
    if (pid == 0) {
        struct rlimit rl = { 64 * 1024, 64 * 1024 };
        if (setrlimit(RLIMIT_STACK, &rl) != 0) _exit(100);
        struct block *b = chain(50000);
        if (!b) _exit(101);
        struct icode ic = { .cur_mark = 1, .root = b };
        unMarkAll(&ic);
        volatile int n = count_blocks(&ic, ic.root);
        (void)n;
        _exit(0);
    }
    int status = 0;
    if (waitpid(pid, &status, 0) < 0) return 0;
    if (WIFSIGNALED(status)) return WTERMSIG(status) == SIGSEGV || WTERMSIG(status) == SIGBUS;
    return 0;
}

static int test_guard_rejects_deep(void) {
    struct block *b = chain(50000); if (!b) return 0;
    struct icode ic = { .cur_mark = 1, .root = b };
    int ok = check_cfg_depth(&ic, b, MAX_CFG_RECURSION_DEPTH) == 0;
    free(b); return ok;
}

static int test_guard_accepts_boundary_and_count(void) {
    struct block *b = chain(MAX_CFG_RECURSION_DEPTH); if (!b) return 0;
    struct icode ic = { .cur_mark = 1, .root = b };
    if (check_cfg_depth(&ic, b, MAX_CFG_RECURSION_DEPTH) != 1) { free(b); return 0; }
    unMarkAll(&ic);
    int n = count_blocks(&ic, b);
    free(b); return n == (int)MAX_CFG_RECURSION_DEPTH;
}

static int test_deeper_revisit_is_not_hidden_by_mark(void) {
    struct block b[6]; memset(b, 0, sizeof(b));
    /* root->jt gives shared at depth 3; root->jf gives same shared at depth 5. */
    b[0].jt=&b[1]; b[1].jt=&b[5];
    b[0].jf=&b[2]; b[2].jt=&b[3]; b[3].jt=&b[4]; b[4].jt=&b[5];
    struct icode ic = { .cur_mark = 1, .root = &b[0] };
    if (check_cfg_depth(&ic, &b[0], 4) != 0) return 0;
    memset(b, 0, sizeof(b));
    b[0].jt=&b[1]; b[1].jt=&b[5];
    b[0].jf=&b[2]; b[2].jt=&b[3]; b[3].jt=&b[4]; b[4].jt=&b[5];
    ic.cur_mark = 1; ic.root=&b[0];
    return check_cfg_depth(&ic, &b[0], 5) == 1;
}

static int test_cycle_fails_closed(void) {
    struct block b[2]; memset(b,0,sizeof(b)); b[0].jt=&b[1]; b[1].jt=&b[0];
    struct icode ic = { .cur_mark=1, .root=&b[0] };
    return check_cfg_depth(&ic, &b[0], MAX_CFG_RECURSION_DEPTH) == 0;
}

int main(void) {
    struct { const char *name; int (*fn)(void); } tests[] = {
        {"unpatched_low_stack_crashes", expect_unpatched_stack_failure},
        {"guard_rejects_deep", test_guard_rejects_deep},
        {"guard_accepts_boundary", test_guard_accepts_boundary_and_count},
        {"guard_tracks_deeper_revisit", test_deeper_revisit_is_not_hidden_by_mark},
        {"guard_cycle_fail_closed", test_cycle_fails_closed},
    };
    int failures=0;
    for (size_t i=0;i<sizeof(tests)/sizeof(tests[0]);++i) {
        int ok=tests[i].fn(); printf("%s: %s\n", tests[i].name, ok?"PASS":"FAIL"); failures += !ok;
    }
    return failures ? 1 : 0;
}

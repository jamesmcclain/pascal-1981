#include <stdint.h>

static int32_t scan_impl(int32_t L, char P, const char *S, int32_t I, int32_t stop_on_equal) {
    if (!S || I < 1) return 0;
    int32_t len = (int32_t)(unsigned char)S[0];
    if (I > len) return 0;
    if (L == 0) return 0;
    int32_t skipped = 0;
    if (L > 0) {
        for (int32_t idx = I; idx <= len && skipped < L; ++idx, ++skipped) {
            char ch = S[idx];
            if ((stop_on_equal && ch == P) || (!stop_on_equal && ch != P)) break;
        }
        return skipped;
    }
    for (int32_t idx = I; idx >= 1 && skipped > L; --idx, --skipped) {
        char ch = S[idx];
        if ((stop_on_equal && ch == P) || (!stop_on_equal && ch != P)) break;
    }
    return skipped;
}

int32_t scaneq(int32_t L, char P, const char *S, int32_t I, int32_t stop_on_equal) {
    return scan_impl(L, P, S, I, stop_on_equal);
}

/*
 * SCANNE and SCANEQ differ only in the stop condition, which the caller
 * already encodes in `stop_on_equal` (codegen passes 1 for SCANEQ, 0 for
 * SCANNE). The previous body re-inverted that flag (`stop_on_equal ? 0 : 1`),
 * which folded SCANNE's 0 back into 1 and made SCANNE behave identically to
 * SCANEQ. Forward the flag unchanged.
 */
int32_t scanne(int32_t L, char P, const char *S, int32_t I, int32_t stop_on_equal) {
    return scan_impl(L, P, S, I, stop_on_equal);
}

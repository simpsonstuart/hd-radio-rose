/* Exercise the telemetry tap using known equalized symbols, no radio required. */
#include <assert.h>
#include "nrsc5.h"
#include "defines.h"

typedef struct { nrsc5_t *radio; } input_stub_t;
typedef struct {
    float complex buffer[FFT_FM][BLKSZ];
    input_stub_t *input;
} sync_t;

static unsigned int expected_modulation, expected_count, reports;
static float expected_lower_i, expected_upper_i;
void nrsc5_report(nrsc5_t *radio, const nrsc5_event_t *evt)
{
    (void)radio;
    assert(evt->event == 0x524f5301u);
    assert(evt->rose_constellation.modulation == expected_modulation);
    assert(evt->rose_constellation.count == expected_count);
    for (unsigned int k = 0; k < expected_count; k++) {
        assert(evt->rose_constellation.lower[2*k] == expected_lower_i);
        assert(evt->rose_constellation.lower[2*k+1] == -0.75f);
        assert(evt->rose_constellation.upper[2*k] == expected_upper_i);
        assert(evt->rose_constellation.upper[2*k+1] == 1.4f);
    }
    reports++;
}
#include "constellation.c"

int main(void)
{
    static sync_t st;
    static input_stub_t input;
    st.input = &input;
    for (int k = 0; k < FFT_FM; k++)
        for (int n = 0; n < BLKSZ; n++) st.buffer[k][n] = CMPLXF(99, 99);
    for (int p = 0; p < 14; p++)
        for (int k = 1; k < PARTITION_WIDTH_FM; k++)
            for (int n = 0; n < BLKSZ; n++) {
                st.buffer[LB_START + p * PARTITION_WIDTH_FM + k][n] = CMPLXF(1.25f, -0.75f);
                st.buffer[UB_END - (p+1) * PARTITION_WIDTH_FM + k][n] = CMPLXF(-1.1f, 1.4f);
            }
    expected_modulation = 4; expected_lower_i = 1.25f; expected_upper_i = -1.1f;
    expected_count = 720; rose_constellation_fm(&st, 10);
    expected_count = 1008; rose_constellation_fm(&st, 14);
    assert(crealf(st.buffer[LB_START + 1][0]) == 1.25f);
    for (int n = 0; n < BLKSZ; n++)
        for (int col = 0; col < PARTITION_WIDTH_AM; col++) {
            int training = n == (5 + 11*col) % 32 || n == (21 + 11*col) % 32;
            st.buffer[CENTER_AM - OUTER_PARTITION_START_AM - col][n] = training ? CMPLXF(99,99) : CMPLXF(3.7f,-0.75f);
            st.buffer[CENTER_AM + OUTER_PARTITION_START_AM + col][n] = training ? CMPLXF(99,99) : CMPLXF(-3.6f,1.4f);
        }
    expected_modulation = 64; expected_count = 750;
    expected_lower_i = 3.7f; expected_upper_i = -3.6f;
    rose_constellation_am(&st, OUTER_PARTITION_START_AM);
    assert(reports == 3);
    puts("Measured constellation tap tests passed");
    return 0;
}

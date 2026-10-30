/* ROSE private constellation telemetry; included in libnrsc5 sync.c.
 * Bounded, decimated copies only. No allocation, I/O or change to decoder data.
 * FM ideal points are (+/-1, +/-1); AM primary levels are +/-0.5..3.5.
 */
#define ROSE_CONSTELLATION_EVENT 0x524f5301u
#define ROSE_CONSTELLATION_MAX 1024

static void rose_constellation_report(sync_t *st, unsigned int modulation,
                                     unsigned int count, float *lower, float *upper)
{
    nrsc5_event_t evt = {0};
    evt.event = ROSE_CONSTELLATION_EVENT;
    evt.rose_constellation.modulation = modulation;
    evt.rose_constellation.count = count;
    evt.rose_constellation.lower = lower;
    evt.rose_constellation.upper = upper;
    nrsc5_report(st->input->radio, &evt);
}

static void rose_constellation_fm(sync_t *st, int partitions)
{
    float lower[ROSE_CONSTELLATION_MAX * 2], upper[ROSE_CONSTELLATION_MAX * 2];
    unsigned int count = 0;
    /* Four OFDM symbols across the block; every data carrier, no references. */
    for (int n = 0; n < BLKSZ; n += 8)
        for (int p = 0; p < partitions; p++)
            for (int k = 1; k < PARTITION_WIDTH_FM && count < ROSE_CONSTELLATION_MAX; k++)
            {
                float complex l = st->buffer[LB_START + p * PARTITION_WIDTH_FM + k][n];
                float complex u = st->buffer[UB_END - (p + 1) * PARTITION_WIDTH_FM + k][n];
                lower[2 * count] = crealf(l); lower[2 * count + 1] = cimagf(l);
                upper[2 * count] = crealf(u); upper[2 * count + 1] = cimagf(u);
                count++;
            }
    rose_constellation_report(st, 4, count, lower, upper);
}

static void rose_constellation_am(sync_t *st, int primary)
{
    float lower[ROSE_CONSTELLATION_MAX * 2], upper[ROSE_CONSTELLATION_MAX * 2];
    unsigned int count = 0;
    for (int n = 0; n < BLKSZ; n++)
        for (int col = 0; col < PARTITION_WIDTH_AM && count < ROSE_CONSTELLATION_MAX; col++)
        {
            /* Exclude known training symbols; show primary payload carriers. */
            if (n == (5 + 11 * col) % 32 || n == (21 + 11 * col) % 32) continue;
            float complex l = st->buffer[CENTER_AM - primary - col][n];
            float complex u = st->buffer[CENTER_AM + primary + col][n];
            lower[2 * count] = crealf(l); lower[2 * count + 1] = cimagf(l);
            upper[2 * count] = crealf(u); upper[2 * count + 1] = cimagf(u);
            count++;
        }
    rose_constellation_report(st, 64, count, lower, upper);
}

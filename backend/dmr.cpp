#include "dsd_decoder.h"

// The SDR worker owns one decoder and consumes its borrowed mono PCM before
// the next call. Drain both slots even when only one is being monitored.
using Decoder = DSDcc::DSDDecoder;

extern "C" Decoder *rose_dmr_new()
{
    auto decoder = new Decoder();
    decoder->setQuiet();
    decoder->setDecodeMode(Decoder::DSDDecodeNone, true);
    decoder->setDecodeMode(Decoder::DSDDecodeDMR, true);
    decoder->enableMbelib(true);
    decoder->setStereo(false);
    decoder->setUpsampling(6);
    return decoder;
}

extern "C" void rose_dmr_delete(Decoder *decoder) { delete decoder; }

extern "C" const short *rose_dmr_run(Decoder *decoder, const short *input,
                                     int size, int slot, int *count, bool *synced)
{
    decoder->resetAudio1();
    decoder->resetAudio2();
    for (int i = 0; i < size; ++i) decoder->run(input[i]);
    *synced = decoder->getSyncType() != Decoder::DSDSyncNone;
    return slot == 1 ? decoder->getAudio1(*count) : decoder->getAudio2(*count);
}

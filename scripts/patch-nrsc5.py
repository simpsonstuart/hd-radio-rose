"""ROSE-local post-equalization telemetry extension for pinned libnrsc5.

This private event is not the proposed upstream API. Keep C and Rust payloads
in sync; pointers are borrowed only for the synchronous callback duration.
"""
from pathlib import Path
import sys


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError(f"nrsc5 changed; expected one anchor: {before[:80]}")
    return text.replace(before, after, 1)


def patch(root, backend=None):
    root = Path(root)
    backend = Path(backend or Path(__file__).resolve().parents[1] / 'backend')
    cmake = root / 'CMakeLists.txt'
    text = cmake.read_text(encoding='utf-8')
    text = replace_once(text, 'https://github.com/knik0/faad2.git',
                        'https://github.com/FreewareAdvancedAudio/faad2.git')
    # FAAD2 2.11.3 contains memory-safety fixes in the decoder.
    text = replace_once(text, 'GIT_TAG 2.11.2', 'GIT_TAG 6918ebb51b8f7e86278da15884bd7114e4b9661e')
    text = replace_once(text, 'PATCH_COMMAND patch -p1 -Ni "${CMAKE_SOURCE_DIR}/support/faad2-hdc-support.patch" || exit 0',
                        'PATCH_COMMAND git apply "${CMAKE_SOURCE_DIR}/support/faad2-hdc-support.patch"')
    text = replace_once(text, '-DBUILD_SHARED_LIBS:BOOL=OFF\n            -DCMAKE_INSTALL_PREFIX:STRING=${FAAD2_PREFIX}',
                        '-DBUILD_SHARED_LIBS:BOOL=OFF\n            -DFAAD_BUILD_CLI:BOOL=OFF\n'
                        '            -DCMAKE_INSTALL_PREFIX:STRING=${FAAD2_PREFIX}')
    cmake.write_text(text, encoding='utf-8')
    hdc = root / 'support/faad2-hdc-support.patch'
    # Only the context changed in 2.11.3. Preserve the corrected spelling.
    text = replace_once(hdc.read_text(encoding='utf-8'),
                        ' /* type definitaions */', ' /* type definitions */')
    hdc.write_text(text, encoding='utf-8', newline='\n')
    header = root / 'include/nrsc5.h'
    text = header.read_text(encoding='utf-8')
    text = replace_once(text, '        } iq;', '''        } iq;
        /* ROSE private event 0x524f5301. Interleaved I/Q, count pairs per side. */
        struct {
            unsigned int modulation; /* 4 = FM QPSK, 64 = AM primary 64-QAM */
            unsigned int count;
            const float *lower;
            const float *upper;
        } rose_constellation;''')
    header.write_text(text, encoding='utf-8')
    source = root / 'src/sync.c'
    text = source.read_text(encoding='utf-8')
    text = replace_once(text, 'void sync_process_fm(sync_t *st)',
                        (backend / 'constellation.c').read_text(encoding='utf-8') + '\nvoid sync_process_fm(sync_t *st)')
    text = replace_once(text, '        // Calculate modulation error', '''        // Already equalized; capture before soft decisions/clipping.
        if (st->bc % 4 == 0) rose_constellation_fm(st, partitions_per_band);

        // Calculate modulation error''')
    text = replace_once(text, '        decode_push_pl_pu_s_t(', '''        if (st->bc % 4 == 0) rose_constellation_am(st, primary_index);

        decode_push_pl_pu_s_t(''')
    source.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    patch(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)

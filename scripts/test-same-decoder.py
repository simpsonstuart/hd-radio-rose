"""Decode synthetic SAME PCM in CI; no SDR or RF transmission involved."""
import array
import math
import subprocess
import sys


def waveform(messages):
    rate = 22050
    audio = array.array('h', [0] * rate)
    phase = 0.0
    for message in messages:
        bits = [(byte >> bit) & 1 for byte in b'\xab' * 16 + message.encode('ascii') for bit in range(8)]
        for sample in range(math.ceil(len(bits) * rate / (3125 / 6))):
            index = min(int(sample * (3125 / 6) / rate), len(bits) - 1)
            frequency = (6250 / 3) if bits[index] else 1562.5
            phase += 2 * math.pi * frequency / rate
            audio.append(round(math.sin(phase) * 12000))
        audio.extend([0] * rate)
    audio.extend([0] * rate)
    if sys.byteorder != 'little':
        audio.byteswap()
    return audio.tobytes()


def main():
    binary = sys.argv[1] if len(sys.argv) > 1 else 'multimon-ng'
    header = 'ZCZC-WXR-TOR-053077+0030-1231200-KXXX/NWS-'
    other = header.replace('053077', '053001')
    def decode(messages):
        result = subprocess.run([binary, '-q', '-v', '0', '-a', 'EAS', '-t', 'raw', '-'],
                                input=waveform(messages), capture_output=True, timeout=20, check=True)
        return [line.strip() for line in result.stdout.decode().splitlines() if line.startswith('EAS: ')]
    assert decode([header]) == [], 'A single unconfirmed header must not alert'
    result = decode([header, other, header, 'NNNN', 'NNNN', 'NNNN'])
    assert result.count('EAS: ' + header) == 1, result
    assert 'EAS: ' + other not in result, result
    assert 'EAS: NNNN' in result, result
    print('SAME waveform: two-of-three consensus, duplicate rejection and EOM passed')


if __name__ == '__main__':
    main()

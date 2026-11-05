"""Render the revised offline mix from the two time-aligned comparison WAVs."""
import math
import wave
from array import array
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parents[1] / "weather-radio-comparison"
RATE = 48000
BLOCK = RATE // 100  # Measure the neural output in 10 ms frames.


def read_clip(name):
    with wave.open(str(DIRECTORY / name), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (RATE, 1, 2):
            raise ValueError("Expected aligned 48 kHz mono 16-bit PCM")
        return array("h", source.readframes(source.getnframes()))


def reduce_whistle(samples):
    # The archive sample has a persistent peak near 1712 Hz with slight drift.
    # A finite-depth cut keeps speech energy even at the center frequency.
    frequency, quality, cut_db = 1712, 16, -18
    angle = 2 * math.pi * frequency / RATE
    amplitude = 10 ** (cut_db / 40)
    alpha = math.sin(angle) / (2 * quality)
    a0 = 1 + alpha / amplitude
    b0 = (1 + alpha * amplitude) / a0
    b1 = -2 * math.cos(angle) / a0
    b2 = (1 - alpha * amplitude) / a0
    a2 = (1 - alpha / amplitude) / a0
    x1 = x2 = y1 = y2 = 0.0
    result = array("h")
    for x in samples:
        y = b0 * x + b1 * x1 + b2 * x2 - b1 * y1 - a2 * y2
        result.append(max(-32768, min(32767, round(y))))
        x2, x1, y2, y1 = x1, x, y1, y
    return result


def write_clip(name, samples):
    with wave.open(str(DIRECTORY / name), "wb") as output:
        output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())


def render():
    neural = read_clip("ai-speech.wav")
    reference = read_clip("hiss-filter.wav")
    if len(neural) != len(reference):
        raise ValueError("Comparison clips must have the same duration")

    # Blend back only the speech-band part of the signal removed by the model.
    bass_alpha = 1 - math.exp(-2 * math.pi * 280 / RATE)
    treble_alpha = 1 - math.exp(-2 * math.pi * 3200 / RATE)
    bass = band = blend = 0.0
    hold = 0
    result = array("h")
    for start in range(0, len(neural), BLOCK):
        frame = neural[start:start + BLOCK]
        rms = math.sqrt(sum(x * x for x in frame) / len(frame)) / 32768
        db = 20 * math.log10(max(rms, 1e-8))
        if db >= -35:
            hold = 45  # Keep helping quiet consonants for 450 ms after strong speech.
        quiet = min(1.0, max(0.0, (-25 - db) / 18))
        target = quiet * (0.32 if hold else 0.06)
        hold = max(0, hold - 1)
        smoothing = 1 - math.exp(-1 / (RATE * (0.020 if target > blend else 0.075)))
        for i, sample in enumerate(frame, start):
            residual = reference[i] - sample
            bass += bass_alpha * (residual - bass)
            band += treble_alpha * (residual - bass - band)
            blend += smoothing * (target - blend)
            result.append(max(-32768, min(32767, round(sample + blend * band))))
    write_clip("ai-speech-revised.wav", result)
    write_clip("ai-speech-whistle-reduced.wav", reduce_whistle(result))


if __name__ == "__main__":
    render()

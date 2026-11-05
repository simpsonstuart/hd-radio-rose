"""Score the real forward decoder and retrospective additions on known MPX samples."""
import argparse
import array
from contextlib import nullcontext
import csv
import math
import random
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

RATE = 1_488_375 / 6
BAUD = 1187.5
OFFSETS = (0x0FC, 0x198, 0x168, 0x1B4)
FADED_GROUPS = (5, 10, 15, 20)


def encode(word, offset):
    reg = word << 10
    for i in range(25, 9, -1):
        if reg & (1 << i):
            reg ^= 0x5B9 << (i - 10)
    return word << 10 | (reg ^ offset)


def fixture(path, mode, seed):
    rng = random.Random(seed)
    words, chips = [], []
    previous = 0
    for group in range(24):
        segment = group % 4
        name = b"ROSE LAB"
        pair = name[2 * segment:2 * segment + 2]
        for pos, word in enumerate((0x524F, segment, 0xE000 + group, int.from_bytes(pair, "big"))):
            words.append(word)
            block = encode(word, OFFSETS[pos])
            for shift in reversed(range(26)):
                previous ^= (block >> shift) & 1
                sign = 2 * previous - 1
                chips.extend((sign, -sign))

    data = array.array("f")
    for n in range(int(len(chips) * RATE / (2 * BAUD))):
        chip = int(n * (2 * BAUD) / RATE)
        phase = math.tau * 19_000 * n / RATE
        group, within = divmod(chip, 208)
        # Deep RDS fade across the A block. The pilot stays available.
        fade = mode in ("fade", "pilot-fade") and group in FADED_GROUPS and within < 52
        if mode == "staggered" and group in FADED_GROUPS:
            start = FADED_GROUPS.index(group) * 52 + 7
            fade = start <= within < start + 38
        amplitude = 0 if mode == "noise" else (0.001 if fade else 0.045)
        noise = rng.uniform(-0.045, 0.045) if fade or mode == "noise" else rng.uniform(-0.002, 0.002)
        pilot = 0.002 if mode in ("pilot-fade", "staggered") and fade else 0.09
        data.append(pilot * math.sin(phase) + amplitude * chips[chip] * math.cos(3 * phase + 0.4) + noise)
    if sys.byteorder != "little":
        data.byteswap()
    path.write_bytes(struct.pack("<8sIII", b"ROSEMPX1", 1_488_375, 6, len(data)) + data.tobytes())
    return words


def score(binary, path, mode, words):
    result = subprocess.run([binary, path], check=True, capture_output=True, text=True)
    hits = {"first": set(), "recovered": set()}
    false = {"first": 0, "recovered": 0}
    for event in csv.DictReader(result.stdout.splitlines()):
        end_bits = int(event["end_sample"]) * BAUD / RATE
        idx = round(end_bits / 26) - 1
        kind = event["pass"]
        if (mode != "noise" and 0 <= idx < len(words) and
                abs(end_bits - (idx + 1) * 26) <= 2 and
                int(event["position"]) == idx % 4 and int(event["word"], 16) == words[idx]):
            hits[kind].add(idx)
        else:
            false[kind] += 1
    added = hits["recovered"] - hits["first"]
    after_fades = [i for i in sorted(added) if i // 4 in FADED_GROUPS]
    print(f"{mode}: first={len(hits['first'])} extra={len(added)} "
          f"false_first={false['first']} false_extra={false['recovered']} "
          f"extra_in_faded_groups={after_fades}")
    if false["recovered"] or (mode == "fade" and not after_fades) or (
            mode == "clean" and len(hits["first"]) < len(words) * 0.9):
        raise SystemExit("experiment acceptance criterion failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--seed", type=int, default=27)
    parser.add_argument("--save", type=Path, help="new directory in which to retain the clips")
    args = parser.parse_args()
    if args.save:
        args.save.mkdir()
    with (nullcontext(args.save) if args.save else tempfile.TemporaryDirectory()) as temp:
        directory = Path(temp)
        for mode in ("clean", "fade", "pilot-fade", "staggered", "noise"):
            path = directory / (mode + ".mpx")
            words = fixture(path, mode, args.seed)
            score(args.binary.resolve(), path, mode, words)


if __name__ == "__main__":
    main()

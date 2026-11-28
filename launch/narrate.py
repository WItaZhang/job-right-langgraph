"""Render the original English voice track locally with Kokoro ONNX and an original music bed."""

import argparse
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
import soundfile as sf
from kokoro_onnx import Kokoro


def render(models, out):
    out.mkdir(parents=True, exist_ok=True)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    session = ort.InferenceSession(
        str(models / "kokoro-v1.0.int8.onnx"), options, providers=["CPUExecutionProvider"]
    )
    voice = Kokoro.from_session(session, str(models / "voices-v1.0.bin"))
    scenes = json.loads(Path(__file__).with_name("film.json").read_text())
    track = []
    cues = []
    start = 0
    for index, scene in enumerate(scenes):
        samples, rate = voice.create(scene["voice"], voice="af_heart", speed=1.04, lang="en-us")
        active = np.flatnonzero(abs(samples) > 0.004)
        samples = samples[max(0, active[0] - 500) : active[-1] + 750]
        lead = 0.48 if index == 0 else 0.24
        end_pad = 1.3 if index == len(scenes) - 1 else 0.44
        length = np.ceil((lead + len(samples) / rate + end_pad) * 30) / 30
        sound = np.zeros(round(length * rate))
        begin = round(lead * rate)
        sound[begin : begin + len(samples)] = samples
        track.extend(sound)
        cues.append(
            dict(start=start, duration=length, text=scene["voice"], lead=lead, speech=len(samples) / rate)
        )
        start += length
    narration = np.array(track)
    total = len(narration)
    t = np.arange(total) / rate
    bed = np.zeros((total, 2))
    chords = [(50, 57, 62, 65, 69), (46, 53, 58, 62, 65), (53, 60, 65, 69, 72), (48, 55, 60, 64, 67)]
    beat = 60 / 98
    for i, when in enumerate(np.arange(0.08, t[-1], beat / 2)):
        chord = chords[(i // 16) % len(chords)]
        note = chord[(0, 2, 4, 1, 3, 2, 1, 3)[i % 8]]
        hz = 440 * 2 ** ((note - 69) / 12)
        pos = round(when * rate)
        tt = np.arange(min(round(rate * 1.4), total - pos)) / rate
        env = (1 - np.exp(-tt * 70)) * np.exp(-tt * 4)
        tone = (np.sin(2 * np.pi * hz * tt) + 0.21 * np.sin(4 * np.pi * hz * tt)) * env
        pan = 0.5 + 0.23 * np.sin(i * 1.8)
        bed[pos : pos + len(tt)] += np.column_stack((tone * (1 - pan), tone * pan)) * 0.05
        if i % 8 == 0:
            bass = hz / 2 if i % 16 == 0 else 440 * 2 ** ((chord[0] - 81) / 12)
            bed[pos : pos + len(tt)] += np.sin(2 * np.pi * bass * tt)[:, None] * env[:, None] * 0.029
    fade = np.clip((t[-1] - t) / 1.7, 0, 1) * np.clip(t / 0.32, 0, 1)
    mix = (bed + narration[:, None] * 0.9) * fade[:, None]
    sf.write(out / "mix.wav", mix / np.max(abs(mix)) * 0.88, rate)
    (out / "timing.json").write_text(json.dumps(cues, indent=2))
    print(f"{len(cues)} narrated scenes, {start:.2f} seconds")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("models", type=Path)
    p.add_argument("out", type=Path)
    a = p.parse_args()
    render(a.models, a.out)

"""roformer_worker.py MODEL SONG.wav OUT PART...: a song's parts by BS-RoFormer SW (MODEL: jonkubis' Core ML port of
jarredou's BS-Rofo-SW-Fixed, .mlmodelc or .mlpackage), each to OUT/PART.raw: 16-bit, interleaved, as long as the
song and at its rate and channels. OUT/progress says the chunks done ("k n") as it goes. Runs in BS-RoFormer SW's own
Python (tools/roformer-setup.sh: coremltools, scipy), not rs-keylights': rs_keylights.roformer starts it.

As the port's driver does it (its results, the ones listened to): the song at 44.1 kHz, chunks of CHUNK samples,
each half a chunk on from the last, each one's spectrogram (n_fft 2048, hop 512, Hann) through the model on the
Mac's GPU, each part's back to sound and cross-faded (raised cosine) with the chunk before. Its memory stays small
however long the song: each part's kept only where chunks still overlap (half of it done each chunk, written out),
not all of it at once (6 parts of 10 minutes: 1.4 GB), then brought back to the song's rate a part at a time."""
from __future__ import annotations

import sys
import wave
from pathlib import Path
from typing import Any, Callable

import numpy as np

RATE = 44100
N_FFT, HOP = 2048, 512
DIM_T = 256  # frames a chunk
CHUNK = (DIM_T - 1) * HOP  # 130 560 samples
STEP = CHUNK // 2  # (each half a chunk on from the last)
SOURCES = ("bass", "drums", "other", "vocals", "guitar", "piano")  # (the model's order)


def window() -> Any:
    """Each chunk's cross-fade: raised cosine over its first half, back down over its second (two overlapping: 1)."""
    fade = CHUNK - STEP
    w = np.ones(CHUNK, dtype=np.float32)
    ramp = (0.5 * (1.0 - np.cos(np.pi * np.arange(fade) / max(1, fade - 1)))).astype(np.float32)
    w[:fade] = ramp
    w[CHUNK - fade:] = ramp[::-1]
    return w


def starts(frames: int) -> list[int]:
    """Where each chunk starts (as the driver plans them)."""
    out, at = [], 0
    while at < frames:
        out.append(at)
        if at + CHUNK >= frames + STEP:
            break
        at += STEP
    return out or [0]


def separate(audio: Any, parts: list[int], run: Callable[[Any], list[Any]],
             written: Callable[[int, Any], None], done: Callable[[int, int], None] = lambda _k, _n: None) -> None:
    """A song's parts (audio: [2, frames], at RATE): run takes a chunk ([2, CHUNK]) to each of parts' sound for it
    ([2, CHUNK] each, in parts' order). Each part's written as it's done (written(i, [2, n]), in order: all of it,
    once), done(k, n) after each chunk."""
    frames = audio.shape[1]
    w = window()
    acc = np.zeros((len(parts), 2, CHUNK), dtype=np.float32)  # (from base on: the chunks over it so far)
    weights = np.zeros(CHUNK, dtype=np.float32)
    base, at = 0, starts(frames)

    def finish(upto: int) -> None:  # (from base to upto done: written, the rest moved up)
        nonlocal base
        n = upto - base
        if n <= 0:
            return
        scale = np.where(weights[:n] > 1e-8, 1.0 / np.maximum(weights[:n], 1e-8), 1.0).astype(np.float32)  # (the
        # first few samples, faded in to nothing yet: left so, as the driver has them)
        for i in range(len(parts)):
            written(i, acc[i, :, :n] * scale)
        acc[:, :, :CHUNK - n] = acc[:, :, n:]
        acc[:, :, CHUNK - n:] = 0
        weights[:CHUNK - n] = weights[n:]
        weights[CHUNK - n:] = 0
        base = upto

    for k, start in enumerate(at):
        chunk = np.zeros((2, CHUNK), dtype=np.float32)
        take = min(CHUNK, frames - start)
        chunk[:, :take] = audio[:, start:start + take]
        for i, sound in enumerate(run(chunk)):
            acc[i] += sound * w
        weights += w
        finish(min(start + STEP, frames) if k + 1 < len(at) else frames)  # (the next starts STEP on: before it, done)
        done(k + 1, len(at))


def _model(path: Path, wanted: list[int]) -> Callable[[Any], list[Any]]:
    """A chunk to the sound of each source wanted (by SOURCES' index), by the Core ML model (on the GPU: the Neural
    Engine and the CPU alone can't run it), each a [2, CHUNK]."""
    import coremltools as ct  # pyright: ignore[reportMissingImports]
    from scipy.signal import istft, stft  # pyright: ignore[reportMissingImports]
    units = ct.ComputeUnit.CPU_AND_GPU
    model = ct.models.CompiledMLModel(str(path), compute_units=units) if path.suffix == ".mlmodelc" else \
        ct.models.MLModel(str(path), compute_units=units)
    scale = float(N_FFT) / 2.0  # (scipy's spectrum divided by the window's sum: torch.stft's has it whole)

    def run(chunk: Any) -> list[Any]:
        _, _, spec = stft(chunk, fs=RATE, window="hann", nperseg=N_FFT, noverlap=N_FFT - HOP, nfft=N_FFT,
                          boundary="zeros", padded=True, return_onesided=True, axis=-1)
        spec = (spec * scale).astype(np.complex64)[:, :, :DIM_T]
        x = np.stack([spec[0].real, spec[0].imag, spec[1].real, spec[1].imag]).astype(np.float32)[None]
        y = model.predict({"spectrogram": x})["stems_spectrogram"][0]  # [6, 4, 1025, 256]
        out = []
        for source in (y[i] for i in wanted):
            z = np.stack([source[0] + 1j * source[1], source[2] + 1j * source[3]]).astype(np.complex64) / scale
            _, sound = istft(z, fs=RATE, window="hann", nperseg=N_FFT, noverlap=N_FFT - HOP, nfft=N_FFT,
                             boundary=True, time_axis=-1, freq_axis=-2)
            sound = sound[:, :CHUNK].astype(np.float32)
            if sound.shape[1] < CHUNK:
                sound = np.pad(sound, ((0, 0), (0, CHUNK - sound.shape[1])))
            out.append(sound)
        return out

    return run


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: roformer_worker.py MODEL SONG.wav OUT PART...", file=sys.stderr)
        return 2
    model_path, song, out, names = Path(argv[0]), Path(argv[1]), Path(argv[2]), argv[3:]
    if any(name not in SOURCES for name in names):
        print(f"The parts: {', '.join(SOURCES)}", file=sys.stderr)
        return 2
    from scipy.signal import resample_poly  # pyright: ignore[reportMissingImports]
    with wave.open(str(song)) as w:
        rate, channels, frames = w.getframerate(), w.getnchannels(), w.getnframes()
        pcm = np.frombuffer(w.readframes(frames), dtype=np.int16).reshape(-1, channels)
    audio = (pcm.T[:2] if channels > 1 else np.repeat(pcm.T, 2, axis=0)).astype(np.float32) / 32768.0
    del pcm
    divisor = np.gcd(rate, RATE)
    if rate != RATE:  # (to the model's rate, and back after: zero-phase, so each part's where the song is)
        audio = resample_poly(audio, RATE // divisor, rate // divisor, axis=-1).astype(np.float32)
    wanted = [SOURCES.index(name) for name in names]
    run = _model(model_path, wanted)
    files = {i: open(out / f"{SOURCES[s]}.f32", "wb") for i, s in enumerate(wanted)}

    def written(i: int, sound: Any) -> None:
        files[i].write(np.ascontiguousarray(sound.T, dtype=np.float32).tobytes())  # (interleaved)

    def done(k: int, n: int) -> None:
        (out / "progress.new").write_text(f"{k} {n}\n")
        (out / "progress.new").replace(out / "progress")

    separate(audio, wanted, run, written, done)
    del audio
    for f in files.values():
        f.close()
    for i, s in enumerate(wanted):  # (each back at the song's rate and channels, 16-bit)
        raw = out / f"{SOURCES[s]}.f32"
        part = np.fromfile(raw, dtype=np.float32).reshape(-1, 2).T
        raw.unlink()
        if rate != RATE:
            part = resample_poly(part, rate // divisor, RATE // divisor, axis=-1)
        part = part[:, :frames]
        if part.shape[1] < frames:
            part = np.pad(part, ((0, 0), (0, frames - part.shape[1])))
        if channels == 1:
            part = part.mean(axis=0, keepdims=True)
        pcm = np.clip(np.rint(part.T * 32768.0), -32768, 32767).astype(np.int16)
        pcm.tofile(out / f"{SOURCES[s]}.raw")
        del part, pcm
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

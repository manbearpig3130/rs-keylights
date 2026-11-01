from __future__ import annotations

import math
import threading
from typing import Any, Callable, Sequence

# Hearing the guitar: its notes from an audio input (the Rocksmith USB Guitar Adapter, shared with the game), to
# light the keys played. The pitch played (YIN: the most periodic one); while a song expects a chord, which of its
# notes sound (each one's harmonics against its neighbours', a semitone either side); and a chord with none to go
# by, from its harmonics: the strongest note's, then, those taken away, the next's, and so on. Needs numpy and
# sounddevice (pip install -e ".[listen]").

DEVICE = "Rocksmith USB Guitar Adapter"
RATE = 48000
HOP = 512  # samples between looks (about 11 ms)
WINDOW = 4096  # samples looked at (about 85 ms: a few periods of the lowest notes)
GATE = 0.003  # rms (the last few hops) below this: nothing played (the cable's own noise is about 0.0001)
CLEAR = 0.15  # YIN's threshold: how far from periodic a pitch can be (0: perfectly)
LOWEST, HIGHEST = 30.0, 1400.0  # Hz: a guitar's range, tuned down and dropped too (B standard an octave down: B0,
# 31 Hz) (a bass's: BASS_LOWEST)
BASS_LOWEST = 24.0  # (G0: a 5-string's low B dropped 4)
HARMONICS = 6  # a note's harmonics weighed, hearing a chord's notes
STANDS_OUT = 1.15  # a chord's note heard: its harmonics this much over a semitone either side's
FAINTEST = 0.2  # and at least this much of the loudest note's
ON, OFF = 2, 3  # looks in a row a note must be heard to light, and missing to go out
CHORD_WINDOW = 8192  # samples a chord with none to go by is heard over (170 ms: its low notes' harmonics apart)
MOST = 6  # notes heard at once at most
WEAKEST = 0.4  # a chord's further note: its harmonics (what's left of them) at least this much of the first's
HIGHEST_PITCH = 88  # E6: the highest note a chord's looked for in
NEAR_HZ = (3.0, 3.0, 3.0, 12.0, 12.0, 12.0)  # Hz each harmonic's peak can be off its note's either side (the higher
# ones a little sharp, a string's stiffness)
TUNE = 20, 40  # but cents at least (a string a little out of tune) and at most (a semitone's 100: low down, less
# than a Hz)
CLOSEST = 12.0  # Hz: a note found, no other this close (a peak's width: two this close sound as one)
FUNDAMENTAL = 0.2  # a note's fundamental: at least this much of its strongest harmonic (else it's another's)
HARMONIC_STEPS = (0, 12, 19, 24, 28, 31, 36)  # semitones over a note its harmonics are (a note with them: one)
UNDER = 0.75  # a note found: one it's a harmonic of instead, with its own fundamental and this much of its salience


def frequency(pitch: float) -> float:
    return 440.0 * 2 ** ((pitch - 69) / 12)


def pitch_of(hertz: float) -> float:
    return 69 + 12 * math.log2(hertz / 440.0)


class Detector:
    """A guitar's notes from its sound, a hop of samples at a time (feed): the pitches it plays now, steady."""

    def __init__(self, rate: int = RATE, lowest: float = LOWEST, highest: float = HIGHEST) -> None:
        import numpy as np

        self._np = np
        self.rate, self.lowest, self.highest = rate, lowest, highest
        self._buffer = np.zeros(CHORD_WINDOW, dtype=np.float64)  # (the last WINDOW of it, for a note)
        self._window = np.hanning(WINDOW)
        self._long_window = np.hanning(CHORD_WINDOW)
        self._runs: dict[int, int] = {}  # each pitch: looks heard in a row (and, lit, missing)
        self._lit: set[int] = set()
        self._fundamentals: set[int] = set()  # the pitches with something at their fundamental, the last look
        self.level = 0.0  # the sound's rms, the last look
        self.pitch: float | None = None  # the pitch played (YIN's), the last look

    def feed(self, samples: Any, expected: Sequence[int] = ()) -> tuple[int, ...]:
        """The pitches heard now, with the new samples (a song's notes expected then: a chord's heard as such)."""
        np = self._np
        samples = np.asarray(samples, dtype=np.float64)[-CHORD_WINDOW:]
        self._buffer = np.concatenate((self._buffer[len(samples):], samples))
        recent = self._buffer[-4 * HOP:]
        self.level = float(np.sqrt(np.mean(recent * recent)))
        heard = self._hear(expected) if self.level >= GATE else set()
        for pitch in heard | set(self._runs):
            run = self._runs.get(pitch, 0)
            self._runs[pitch] = max(run, 0) + 1 if pitch in heard else min(run, 0) - 1
        self._lit |= {p for p, run in self._runs.items() if run >= ON}
        self._lit -= {p for p in self._lit if self._runs.get(p, 0) <= -OFF}
        self._runs = {p: run for p, run in self._runs.items() if run > -OFF or p in self._lit}
        return tuple(sorted(self._lit))

    def _hear(self, expected: Sequence[int]) -> set[int]:
        self.pitch = self._yin()
        played = round(self.pitch) if self.pitch is not None else None
        if len(set(expected)) >= 2:  # a chord expected: which of its notes sound
            spectrum = self._spectrum()
            salience = {p: self._salience(spectrum, p) for p in {q + d for q in expected for d in (-1, 0, 1)}}
            loudest = max(salience[p] for p in expected) or 1.0
            heard = {p for p in set(expected) if salience[p] >= FAINTEST * loudest
                     and salience[p] >= STANDS_OUT * max(salience[p - 1], salience[p + 1])}
            if heard:
                return heard
        notes = self._chord()
        if played is not None and played in self._fundamentals and all(n - played in HARMONIC_STEPS for n in notes):
            # (a note: what's left of its harmonics aside, the pitch played; but a chord's notes can seem one, lower,
            # with nothing at its fundamental)
            return {played}
        return notes  # (a chord's notes; or, no pitch clear, what's there)

    def _chord(self) -> set[int]:
        """The notes sounding with none expected (a chord's, or one): the strongest note's (its harmonics summed,
        with something at its fundamental; or a lower one's, nearly as strong, it's a harmonic of), then, its
        harmonics taken away (as much as its neighbouring ones have: what's left is another note's) and its
        neighbours left out, the next's, and so on, while they're at least WEAKEST of the first's (MOST at most)."""
        np = self._np
        step = self.rate / (4 * CHORD_WINDOW)
        pitches = np.arange(max(math.ceil(pitch_of(self.lowest)), 0), HIGHEST_PITCH + 1)
        hertz = 440.0 * 2 ** ((pitches - 69) / 12)
        harmonics = np.arange(1, HARMONICS + 1)
        spectrum = np.sqrt(np.abs(np.fft.rfft(self._buffer * self._long_window, 4 * CHORD_WINDOW)))
        spectrum = spectrum[:int(hertz[-1] * HARMONICS * 1.1 / step)]
        # Its peaks: each one's Hz (between bins) and how high; a harmonic's, those near it (so what's at a pitch is
        # a peak there, not the side of a neighbour's: low down, a semitone's less than a peak's width)
        k = np.nonzero((spectrum[1:-1] >= spectrum[:-2]) & (spectrum[1:-1] > spectrum[2:]))[0] + 1
        before, at, after = spectrum[k - 1], spectrum[k], spectrum[k + 1]
        curve = before - 2 * at + after
        peak_hz = (k + np.where(curve < 0, 0.5 * (before - after) / np.where(curve < 0, curve, -1.0), 0.0)) * step
        heights = np.append(at, 0.0)  # (and one more: none)
        wanted = hertz[:, None] * harmonics[None, :]
        least, most = (wanted * (2 ** (cents / 1200) - 1) for cents in TUNE)
        off = np.clip(np.array(NEAR_HZ)[None, :HARMONICS], least, most)
        lows = np.searchsorted(peak_hz, wanted - off).ravel()
        highs = np.searchsorted(peak_hz, wanted + off, "right").ravel()
        empty = (lows >= highs).reshape(wanted.shape)
        ranges = np.stack((lows, highs), axis=1).ravel()
        weights = 1 / np.sqrt(harmonics)
        found: list[int] = []
        first = 0.0
        for look in range(MOST):
            peaks = np.where(empty, 0.0, np.maximum.reduceat(heights, ranges)[::2].reshape(wanted.shape))
            salience = (peaks * weights).sum(axis=1)
            fundamental = peaks[:, 0] >= FUNDAMENTAL * peaks.max(axis=1)
            if look == 0:
                self._fundamentals = {int(p) for p in pitches[fundamental]}
            near = np.zeros(len(pitches), dtype=bool)  # (a note found: what's close to it can't be another)
            for f in found:
                near |= (np.abs(pitches - f) <= 1) | (np.abs(hertz - frequency(f)) <= CLOSEST)
            salience = np.where(fundamental & ~near, salience, 0.0)
            i = int(salience.argmax())
            if salience[i] <= 0 or salience[i] < WEAKEST * (first or salience[i]):
                break
            under = [j for j in (i - up for up in HARMONIC_STEPS[1:HARMONICS]) if j >= 0
                     and salience[j] >= UNDER * salience[i]]
            i = min(under, default=i)  # (the lowest: a note's sound found from the bottom up)
            first = first or float(salience[i])
            found.append(int(pitches[i]))
            amplitudes = peaks[i]
            for h in range(HARMONICS):  # (taken away: as much as its neighbouring harmonics have)
                around = amplitudes[max(h - 1, 0):h + 2]
                part = slice(lows[i * HARMONICS + h], highs[i * HARMONICS + h])
                heights[part] = np.maximum(heights[part] - min(float(amplitudes[h]), float(around.mean())), 0.0)
        return set(found)

    def _yin(self) -> float | None:
        """The pitch played (YIN), or None if it's not periodic enough."""
        np = self._np
        x = self._buffer[-WINDOW:] - self._buffer[-WINDOW:].mean()
        tau_max = min(int(self.rate / self.lowest), WINDOW // 2)
        tau_min = max(int(self.rate / self.highest), 2)
        width = WINDOW - tau_max
        size = 1 << (WINDOW + width).bit_length()
        correlation = np.fft.irfft(np.fft.rfft(x, size) * np.conj(np.fft.rfft(x[:width], size)), size)[:tau_max + 1]
        energy = np.concatenate(([0.0], np.cumsum(x * x)))
        difference = energy[width] + energy[width:width + tau_max + 1] - energy[:tau_max + 1] - 2 * correlation
        difference[0] = 0.0
        normal = np.ones(tau_max + 1)
        running = np.cumsum(difference[1:])
        normal[1:] = difference[1:] * np.arange(1, tau_max + 1) / np.where(running > 0, running, 1.0)
        below = np.nonzero(normal[tau_min:] < CLEAR)[0]
        if not len(below):
            return None
        tau = tau_min + int(below[0])
        while tau + 1 <= tau_max and normal[tau + 1] < normal[tau]:
            tau += 1
        if 0 < tau < tau_max:  # (between samples)
            a, b, c = normal[tau - 1], normal[tau], normal[tau + 1]
            bend = (a - c) / (2 * (a - 2 * b + c)) if a - 2 * b + c else 0.0
            tau_f = tau + max(min(bend, 1.0), -1.0)
        else:
            tau_f = float(tau)
        return pitch_of(self.rate / tau_f)

    def _spectrum(self) -> Any:
        np = self._np
        return np.abs(np.fft.rfft(self._buffer[-WINDOW:] * self._window, 4 * WINDOW))

    def _salience(self, spectrum: Any, pitch: int) -> float:
        """How much a pitch's harmonics are there (the first weighed most)."""
        step = self.rate / (4 * WINDOW)
        total = 0.0
        for h in range(1, HARMONICS + 1):
            k = int(round(frequency(pitch) * h / step))
            if k + 1 >= len(spectrum):
                break
            total += float(spectrum[max(k - 1, 0):k + 2].max()) / h ** 0.5
        return total


class Listener:
    """An audio input heard in a thread of its own: each time what it hears changes, heard(pitches) (from that
    thread); expected(), asked each look, the notes a song expects then (None: none); lowest(), the lowest Hz to
    listen for (a bass's lower)."""

    def __init__(self, heard: Callable[[tuple[int, ...]], None], expected: Callable[[], Sequence[int]] | None = None,
                 device: str = DEVICE, lowest: Callable[[], float] | None = None) -> None:
        self.device = device
        self._heard, self._expected = heard, expected
        self._lowest = lowest
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None
        self.now: tuple[int, ...] = ()  # what it hears
        self.level = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._quit.clear()
        self._thread = threading.Thread(target=self._run, name="rs-keylights listen", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._quit.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        if self.now:
            self.now = ()
            self._heard(())

    def _run(self) -> None:
        try:
            import sounddevice
        except ImportError:
            self.error = 'Needs numpy and sounddevice: pip install -e ".[listen]"'
            return
        try:
            detector = Detector()
            with sounddevice.InputStream(samplerate=RATE, channels=1, device=self.device, blocksize=HOP,
                                         dtype="float32") as stream:
                self.error = None
                while not self._quit.is_set():
                    samples, _overflowed = stream.read(HOP)
                    if self._lowest is not None:
                        detector.lowest = self._lowest()
                    expected = self._expected() if self._expected is not None else ()
                    now = detector.feed(samples[:, 0], expected or ())
                    self.level = detector.level
                    if now != self.now:
                        self.now = now
                        self._heard(now)
        except Exception as exc:  # no such input (unplugged): said, and listening stops
            self.error = str(exc) or type(exc).__name__

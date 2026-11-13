from __future__ import annotations

import time
import wave
from collections import deque
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

# The keyboard's drums sampled (rs-keylights sample-drums): each drum of its kit struck in turn (General MIDI's notes,
# on the drum channel) and recorded from an audio input its output is plugged into, until it dies away; trimmed to
# where it starts and ends, the kit's loudest brought to full (the same gain for them all: the kit's own balance
# kept), and saved as WAV files: mono for a sampler (a Novation Circuit's Components takes them), and in stereo as
# they were heard.

GM_DRUMS = {35: "Kick 2", 36: "Kick 1", 37: "Side Stick", 38: "Snare 1", 39: "Hand Clap", 40: "Snare 2",
            41: "Low Floor Tom", 42: "Closed Hi-Hat", 43: "High Floor Tom", 44: "Pedal Hi-Hat", 45: "Low Tom",
            46: "Open Hi-Hat", 47: "Low-Mid Tom", 48: "Hi-Mid Tom", 49: "Crash 1", 50: "High Tom", 51: "Ride 1",
            52: "China", 53: "Ride Bell", 54: "Tambourine", 55: "Splash", 56: "Cowbell", 57: "Crash 2",
            58: "Vibraslap", 59: "Ride 2"}
DRUM_CHANNEL = 10
RATE = 48000
TEST_NOTE = 38  # the snare: struck first, to find the inputs the keyboard is heard on
STEP = 0.05  # seconds between looks at what's been heard
BEFORE = 0.15  # seconds of quiet recorded before each strike
LONGEST = 6.0  # seconds a drum's recorded at most (a cymbal's tail)
QUIET = 0.4  # seconds of quiet that end a drum
HEARD = 0.02  # a level this much over the quiet's: the keyboard's heard there (of full scale)
STARTS, ENDS = 0.02, 0.002  # of a drum's loudest: where it starts, and where it's died away (-54 dB)
LEAD, FADE_IN, FADE_OUT = 0.002, 0.001, 0.02  # seconds kept before it starts, and its ends smoothed over
FULL = 0.89  # the kit's loudest sample: -1 dB
REVERB, REVERB_USUAL = 91, 40  # the Control Change of a channel's reverb, and where General MIDI starts it
SAME = 0.98  # two samples this alike (their correlation): the same sound


class SamplerError(Exception):
    pass


class Strikes(Protocol):
    def sound(self, channel: int, note: int, velocity: int) -> None:
        ...


class Recorder:
    """An audio input, recorded: what it's heard since it was last asked (take), every channel of it."""

    def __init__(self, device: str | None = None, rate: int = RATE, stream_factory: Callable[..., Any] | None = None):
        self.device, self.rate = device, rate
        self._factory = stream_factory
        self._heard: deque[Any] = deque()
        self._stream: Any = None
        self.channels = 0

    def start(self) -> None:
        import sounddevice
        try:
            found = sounddevice.query_devices(self.device, "input")
        except (ValueError, sounddevice.PortAudioError) as exc:
            raise SamplerError(f"No audio input '{self.device}': {exc}") from exc
        self.channels = int(found["max_input_channels"])
        if self.channels < 1:
            raise SamplerError(f"'{found['name']}' has no inputs")
        factory = self._factory or sounddevice.InputStream
        self._stream = factory(device=self.device, samplerate=self.rate, channels=self.channels, dtype="float32",
                               callback=lambda data, _frames, _time, _status: self._heard.append(data.copy()))
        self._stream.start()

    def take(self) -> Any:
        """What's been heard since the last time (samples x channels; none: 0 samples)."""
        import numpy as np
        chunks = []
        while self._heard:
            chunks.append(self._heard.popleft())
        return np.concatenate(chunks) if chunks else np.zeros((0, max(self.channels, 1)), dtype=np.float32)

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()


def _listen(recorder: Any, seconds: float, sleep: Callable[[float], None]) -> Any:
    import numpy as np
    chunks, waited = [], 0.0
    while waited < seconds:
        sleep(STEP)
        waited += STEP
        chunks.append(recorder.take())
    return np.concatenate(chunks)


def find_channels(keyboard: Strikes, recorder: Any, velocity: int = 110,
                  sleep: Callable[[float], None] = time.sleep) -> tuple[tuple[int, ...], float]:
    """The input's channels the keyboard is heard on (a snare struck: the loudest over the quiet before it, and the
    one beside it if it's heard there too: a stereo pair), and how loud the quiet is on them. A SamplerError if it
    isn't heard at all."""
    import numpy as np
    recorder.take()
    quiet = np.abs(_listen(recorder, 0.5, sleep)).max(axis=0)
    keyboard.sound(DRUM_CHANNEL, TEST_NOTE, velocity)
    struck = np.abs(_listen(recorder, 0.6, sleep)).max(axis=0)
    keyboard.sound(DRUM_CHANNEL, TEST_NOTE, 0)
    _listen(recorder, 1.0, sleep)  # (its tail away)
    over = struck - quiet
    best = int(np.argmax(over))
    if over[best] < HEARD:
        raise SamplerError("The keyboard's drums weren't heard on the audio input: is its output plugged into it, "
                           "and its volume up? (--channels says which inputs, --list which devices there are)")
    beside = best ^ 1  # (inputs come in pairs: 1+2, 3+4...)
    chosen = (min(best, beside), max(best, beside)) if beside < len(over) and over[beside] > over[best] * 0.2 \
        else (best,)
    return chosen, float(max(quiet[c] for c in chosen))


def strike(keyboard: Strikes, recorder: Any, note: int, velocity: int, channels: Sequence[int], quiet: float,
           sleep: Callable[[float], None] = time.sleep, longest: float = LONGEST) -> Any:
    """A drum struck and recorded until it's died away (QUIET of quiet, or longest): the channels chosen of what was
    heard, from BEFORE before it."""
    import numpy as np
    recorder.take()
    chunks = [_listen(recorder, BEFORE, sleep)[:, list(channels)]]
    keyboard.sound(DRUM_CHANNEL, note, velocity)
    waited, loudest, since = 0.0, 0.0, 0.0
    while waited < longest:
        sleep(STEP)
        waited += STEP
        chunk = recorder.take()[:, list(channels)]
        chunks.append(chunk)
        level = float(np.abs(chunk).max()) if len(chunk) else 0.0
        loudest = max(loudest, level)
        since = since + STEP if level <= max(loudest * ENDS, quiet * 2) else 0.0
        if loudest > max(quiet * 4, HEARD / 4) and since >= QUIET:
            break
    keyboard.sound(DRUM_CHANNEL, note, 0)
    return np.concatenate(chunks)


def trimmed(heard: Any, rate: int, quiet: float) -> Any:
    """A drum as recorded, from just before it starts (STARTS of its loudest) to where it's died away (ENDS), its
    ends smoothed; none (0 samples) if nothing was heard over the quiet."""
    import numpy as np
    level = np.abs(heard).max(axis=1)
    loudest = float(level.max()) if len(level) else 0.0
    if loudest <= max(quiet * 4, HEARD / 4):
        return heard[:0]
    over = np.flatnonzero(level > max(loudest * STARTS, quiet * 3))
    start = max(int(over[0]) - round(LEAD * rate), 0)
    still = np.flatnonzero(level > max(loudest * ENDS, quiet * 2))
    end = min(int(still[-1]) + round(FADE_OUT * rate), len(heard))
    out = heard[start:end].astype(np.float32).copy()
    rise, fall = min(round(FADE_IN * rate), len(out)), min(round(FADE_OUT * rate), len(out))
    out[:rise] *= np.linspace(0.0, 1.0, rise, dtype=np.float32)[:, None]
    out[len(out) - fall:] *= np.linspace(1.0, 0.0, fall, dtype=np.float32)[:, None]
    return out


def sample_kit(keyboard: Strikes, recorder: Any, notes: Sequence[int], velocity: int = 120,
               channels: Sequence[int] | None = None, sleep: Callable[[float], None] = time.sleep,
               say: Callable[[str], None] = lambda _text: None, longest: float = LONGEST) -> dict[int, Any]:
    """Each drum's sample (by its note: samples x 1 or 2 channels), trimmed, as it was heard; those not heard at all
    (the kit has none there) left out."""
    quiet = 0.0
    if channels is None:
        channels, quiet = find_channels(keyboard, recorder, sleep=sleep)
        say(f"Hearing the keyboard on input{'s' if len(channels) > 1 else ''} "
            f"{' + '.join(str(c + 1) for c in channels)}")
    else:
        import numpy as np
        recorder.take()
        quiet = float(np.abs(_listen(recorder, 0.5, sleep)[:, list(channels)]).max())
    out: dict[int, Any] = {}
    for note in notes:
        sample = trimmed(strike(keyboard, recorder, note, velocity, channels, quiet, sleep, longest), recorder.rate,
                         quiet)
        name = GM_DRUMS.get(note, f"Note {note}")
        if not len(sample):
            say(f"  {note} {name}: nothing heard (not in this kit)")
            continue
        out[note] = sample
        say(f"  {note} {name}: {len(sample) / recorder.rate:.2f} s")
    return out


def save_kit(samples: dict[int, Any], rate: int, folder: Path) -> list[Path]:
    """The kit's samples as WAV files (16-bit): mono in the folder ("36 Kick 1.wav": a sampler's), and as they were
    heard in its stereo folder; each set made as loud as its loudest sample allows (FULL), by the same amount."""
    import numpy as np
    if not samples:
        raise SamplerError("No drums were recorded")
    folder.mkdir(parents=True, exist_ok=True)
    stereo = any(s.shape[1] > 1 for s in samples.values())
    if stereo:
        (folder / "stereo").mkdir(exist_ok=True)
    mono = {note: s.mean(axis=1, keepdims=True) for note, s in samples.items()}
    written = []
    for kit, where in ((mono, folder), (samples, folder / "stereo")) if stereo else ((mono, folder),):
        gain = FULL / max(float(np.abs(s).max()) for s in kit.values())
        for note, sample in sorted(kit.items()):
            path = where / f"{note} {GM_DRUMS.get(note, f'Note {note}')}.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(sample.shape[1])
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(np.round(np.clip(sample * gain, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
            if where is folder:
                written.append(path)
    return written


def same_sound(a: Any, b: Any) -> bool:
    """Whether two samples are the same sound (as a kit that wasn't switched to gives the one before's): alike, from
    their starts, over the shorter's length."""
    import numpy as np
    n = min(len(a), len(b))
    if n < 16:
        return False
    x, y = a[:n].mean(axis=1), b[:n].mean(axis=1)
    size = float(np.sqrt(np.dot(x, x) * np.dot(y, y)))
    return size > 0 and float(np.dot(x, y)) / size > SAME


def inputs() -> list[tuple[str, int]]:
    """The audio inputs there are: each one's name and how many channels it has."""
    import sounddevice
    return [(str(d["name"]), int(d["max_input_channels"])) for d in sounddevice.query_devices()
            if d["max_input_channels"] > 0]

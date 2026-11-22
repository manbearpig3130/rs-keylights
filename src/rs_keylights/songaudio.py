from __future__ import annotations

import tempfile
import threading
import time
import wave
from collections import deque
from pathlib import Path
from typing import Any, Callable

from .library import ArrangementRef

# The song's own audio (from its archive: drums.decode, a fraction of a second), played on the Mac with a song
# rs-keylights plays, where the song is: it follows the song's position (paused, sought, looped, at its speed), the
# speed slowed or quickened with its pitch kept (WSOLA: frames of it overlapped, each where it fits the last best).
# At the song's own speed it's the audio as it is, frame after frame. Each block's timed by the sound device's own
# clock for when it's heard (exact: no jitter to chase), and only a real drift, DRIFT or more, is caught up (stretched
# a little meanwhile, which roughens it). Blocks the device missed (Python busy elsewhere: its sound broken there
# already) are gone over at once, not caught up.

FRAME = 2048  # samples a frame of it (46 ms at 44.1 kHz), overlapped by half
HOP = FRAME // 2
SEEK = 512  # samples either way each frame's searched for where it fits best...
DECIMATE = 4  # ...on every 4th sample of it, mono
JUMP = 0.15  # seconds the audio's off where the song is to jump there (a seek, a loop); less, from DRIFT on...
DRIFT = 0.02  # (the sound device's clock and the Mac's drift a few ms a minute apart; a block it missed, more)
CAUGHT = 0.002  # ...is caught up to this close by playing up to MOST_CATCH faster or slower: this much faster a
CATCH = 1.0  # second it's behind
MOST_CATCH = 0.02
SKIPPED = 0.001  # seconds later than the last block's end a block's heard: blocks between missed
BLOCK = 512  # samples the sound device's asked for at once (12 ms: steady, the delay made up for where it's heard)


class Stretcher:
    """Audio read at a speed with its pitch kept: each frame of FRAME samples taken about HOP x speed on from the
    last, where it best continues it (SEEK either way), windowed and overlapped by half. At speed 1 it's the
    audio as it is. The audio: parts (each samples x channels, int16 or -1 to 1, as long), mixed as each frame is
    made at weights' volumes (0 to 1, by part: changed as it plays), the first part (the whole song, or another
    the search is done on: search) with them."""

    def __init__(self, samples: Any, weights: dict[str, float] | None = None, search: Any = None):
        import numpy as np
        parts = samples if isinstance(samples, dict) else {"song": samples}
        self._parts = {name: _frames(part) for name, part in parts.items()}
        first = next(iter(self._parts.values()))
        self._length, self._channels = first.shape
        self.weights = weights if weights is not None else {name: 1.0 for name in self._parts}
        self._search = _frames(search) if search is not None else first
        self._window = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(FRAME) / FRAME)).astype(np.float32)[:, None]
        self.seek(0.0)

    @property
    def length(self) -> int:
        return self._length

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def position(self) -> float:
        """The sample of the audio the next one read is (as heard: before its frames' search moves it)."""
        if self._chunks:
            chunk, at, speed = self._chunks[0]
            return at + self._used * speed
        return self._nominal

    def seek(self, sample: float) -> None:
        import numpy as np
        self._nominal = float(sample) - HOP  # (a frame before: the first heard then has the one before it under it)
        self._sum = np.zeros((FRAME, self._channels), dtype=np.float32)
        self._follows: int | None = None  # where the last frame goes on, in the audio (none: nothing to fit)
        self._chunks: deque[tuple[Any, float, float]] = deque()  # (made, not read yet: samples, where, speed)
        self._used = 0  # (of the first)
        self._frame(1.0)
        self._chunks.clear()

    def _segment(self, start: int) -> Any:
        """The parts from start, FRAME samples, mixed at their weights (silence past either end)."""
        import numpy as np
        lo, hi = max(start, 0), min(start + FRAME, self._length)
        out = np.zeros((FRAME, self._channels), dtype=np.float32)
        weights = dict(self.weights)  # (as they are now: changed meanwhile, the next frame has them, all together)
        if hi > lo:
            for name, part in self._parts.items():
                weight = weights.get(name, 1.0)
                if weight:
                    out[lo - start:hi - start] += _float(part[lo:hi]) * weight
        return out

    def _mono(self, start: int, count: int) -> Any:
        """The searched part, mono, every DECIMATE'th sample from start (silence past either end)."""
        import numpy as np
        out = np.zeros(-(-count // DECIMATE), dtype=np.float32)
        first = max(0, -(-(-start) // DECIMATE))  # (the first of them at or after 0)
        lo = start + first * DECIMATE
        hi = min(start + count, self._length)
        if hi > lo:
            got = _float(self._search[lo:hi:DECIMATE]).mean(axis=1)
            out[first:first + len(got)] = got
        return out

    def _best(self, start: int) -> int:
        """Where near start a frame best continues the last (its cross-correlation with what followed it, mono)."""
        import numpy as np
        assert self._follows is not None
        template = self._mono(self._follows, FRAME)
        region = self._mono(start - SEEK, 2 * SEEK + FRAME)
        if not template.any():
            return start
        fit = np.correlate(region, template, "valid")
        return start - SEEK + int(np.argmax(fit)) * DECIMATE

    def _frame(self, speed: float) -> None:
        import numpy as np
        start = int(round(self._nominal))
        if self._follows is not None and speed == 1.0:  # (as it is: on from the last, where it's heard from now)
            start = self._follows
            self._nominal = float(start)
        elif self._follows is not None and -SEEK <= start <= self._length + SEEK:
            start = self._best(start)
        self._sum += self._segment(start) * self._window
        self._chunks.append((self._sum[:HOP].copy(), self._nominal, speed))
        self._sum = np.concatenate([self._sum[HOP:], np.zeros_like(self._sum[:HOP])])
        self._follows = start + HOP
        self._nominal += HOP * speed

    def read(self, count: int, speed: float) -> Any:
        """The next count samples (count x channels), at a speed (1: as it is); past its end, silence."""
        import numpy as np
        out = np.zeros((count, self._channels), dtype=np.float32)
        done = 0
        while done < count:
            if not self._chunks:
                self._frame(speed)
            chunk, at, chunk_speed = self._chunks[0]
            take = min(count - done, len(chunk) - self._used)
            out[done:done + take] = chunk[self._used:self._used + take]
            done += take
            self._used += take
            if self._used == len(chunk):
                self._chunks.popleft()
                self._used = 0
        return out


class SongAudio:
    """A song's audio played where the song is: where(at) says where it is at a time on time.monotonic's clock
    (seconds; None: not playing) and how fast, each time it's asked for more, for when that's heard. Too far off
    (JUMP), it goes there; from DRIFT off, it catches up (MOST_CATCH at most); not playing, silence. volume: 0 to 1."""

    def __init__(self, samples: Any, rate: int, where: Callable[[float], tuple[float | None, float]],
                 volume: float = 1.0, stream_factory: Callable[..., Any] | None = None,
                 weights: dict[str, float] | None = None, search: Any = None):
        self.rate = rate
        self.volume = volume
        self._where = where
        self._stretcher = Stretcher(samples, weights, search)
        self._factory = stream_factory
        self._stream: Any = None
        self._lock = threading.Lock()
        self._catching = False
        self._next: float | None = None  # (when the next block's heard, on the sound device's clock)
        self._silent = True  # (the last block: silence, the song not playing)

    @property
    def channels(self) -> int:
        return self._stretcher.channels

    @property
    def weights(self) -> dict[str, float]:
        """Each part's volume (0 to 1): changed, heard at once."""
        return self._stretcher.weights

    def fill(self, frames: int, at: float | None = None, exact: bool = False) -> Any:
        """The next frames of sound (frames x channels), heard at a time on time.monotonic's clock (None: now; exact:
        the sound device's own, block after block)."""
        import numpy as np
        position, speed = self._where(time.monotonic() if at is None else at)
        missed = exact and at is not None and self._next is not None and at - self._next > SKIPPED
        self._next = at + frames / self.rate if exact and at is not None else None
        silent, self._silent = self._silent, position is None
        if position is None:
            return np.zeros((frames, self.channels), dtype=np.float32)
        with self._lock:
            wanted = position * self.rate
            off = (wanted - self._stretcher.position) / self.rate
            if silent or abs(off) > JUMP * max(speed, 1.0) or (missed and abs(off) > CAUGHT):  # (from silence: from
                self._stretcher.seek(wanted)  # just where it is)
                off = 0.0
            self._catching = abs(off) > (CAUGHT if self._catching else DRIFT)
            catch = min(max(off * CATCH, -MOST_CATCH), MOST_CATCH) if self._catching else 0.0
            return self._stretcher.read(frames, speed * (1 + catch)) * self.volume

    def start(self, device: str | None = None) -> None:
        """Played on an audio output (by name; none: the Mac's own, as it is)."""
        import sounddevice
        factory = self._factory or sounddevice.OutputStream

        def callback(out: Any, frames: int, time_info: Any, _status: Any) -> None:
            at, exact = _heard(time_info)
            out[:] = self.fill(frames, at, exact)

        chosen = {} if device is None else {"device": device}
        self._stream = factory(samplerate=self.rate, channels=self.channels, dtype="float32", callback=callback,
                               blocksize=BLOCK, latency="low", **chosen)
        self._stream.start()

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()


def _heard(time_info: Any) -> tuple[float, bool]:
    """When a block's first sample is heard, on time.monotonic's clock, and whether that's exact: the sound device's
    own time for it (on the Mac, its clock: block after block), else now and the delay it says there is."""
    now = time.monotonic()
    try:
        dac, current = float(time_info.outputBufferDacTime), float(time_info.currentTime)
    except (AttributeError, TypeError):
        return now, False
    if 0.0 <= dac - now < 1.0:  # (the same clock)
        return dac, True
    return now + max(dac - current, 0.0), False


def _frames(samples: Any) -> Any:
    """Samples as samples x channels (int16 kept so: half the memory)."""
    import numpy as np
    x = np.asarray(samples)
    if x.dtype != np.int16:
        x = x.astype(np.float32)
    return x[:, None] if x.ndim == 1 else x


def _float(samples: Any) -> Any:
    import numpy as np
    return samples.astype(np.float32) / 32768.0 if samples.dtype == np.int16 else samples


def song_samples(ref: ArrangementRef, game_dir: Path) -> tuple[Any, int]:
    """A song's audio from its archive (decoded: drums.decode), as samples x channels (int16) and its rate."""
    import numpy as np

    from . import drums
    with tempfile.TemporaryDirectory(prefix="rs-keylights-song-") as work:
        out = Path(work) / "song.wav"
        drums.decode(ref, game_dir, out)
        with wave.open(str(out)) as w:
            rate, channels = w.getframerate(), w.getnchannels()
            raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return raw.reshape(-1, channels).copy(), rate

from __future__ import annotations

import struct
import threading
import time
from collections import deque
from typing import Any, Callable

# The song's audio, and the keyboard's sound coming into the Mac (an interface's input), sent to the pages listening
# on other devices (a computer or phone on the network): each block as it's made, with when the Mac hears it (or
# heard it: an input's) on time.monotonic's clock, for each page to play it in time, a little after the Mac. Each
# page mixes and times what it's sent itself (lanes: the song's and the keyboard's, each at its own rate), so its
# volumes are its own and the Mac's sound is as it was.
#
# A block, as it's sent: HEADER (its lane, channels, frames, rate, when it's heard and when it was sent, on the Mac's
# clock), then its samples, 16-bit, interleaved. A block of no frames (KEEPALIVE) says only that the Mac's there.

MAGIC = b"RSKA"
HEADER = struct.Struct("<4sBBHIdd")  # magic, lane, channels, frames, rate, heard at, sent at (28 bytes)
LANES = {"song": 0, "keys": 1}
KEEPALIVE_LANE = 255
KEEPALIVE = 2.0  # seconds a page is sent nothing before it's told the Mac's there (and found gone, if it is)
MOST_QUEUED = 400  # blocks kept for a page that's slow to take them (a few seconds): older ones dropped
BLOCK = 512  # frames an input's read in at a time (about 11 ms)


def encode(lane: str, at: float, rate: int, samples: Any, sent: float | None = None) -> bytes:
    """A block (frames x channels, -1 to 1) as it's sent."""
    import numpy as np
    block = np.asarray(samples, dtype=np.float32)
    if block.ndim == 1:
        block = block[:, None]
    frames, channels = block.shape
    pcm = (np.clip(block, -1.0, 1.0) * 32767.0).astype("<i2")
    return HEADER.pack(MAGIC, LANES[lane], channels, frames, int(rate), float(at),
                       time.monotonic() if sent is None else sent) + pcm.tobytes()


def decode(data: bytes) -> list[tuple[int, int, int, int, float, float, bytes]]:
    """Blocks as sent (a whole number of them): each (lane, channels, frames, rate, at, sent, samples' bytes)."""
    out, i = [], 0
    while i < len(data):
        magic, lane, channels, frames, rate, at, sent = HEADER.unpack_from(data, i)
        if magic != MAGIC:
            raise ValueError("Not a listening stream")
        i += HEADER.size
        size = frames * channels * 2
        out.append((lane, channels, frames, rate, at, sent, data[i:i + size]))
        i += size
    return out


class ListeningPage:
    """A page listening: the blocks for it, waiting to be sent (from the threads making them: taken by its own)."""

    def __init__(self, lanes: set[str]):
        self.lanes = frozenset(lanes)
        self._blocks: deque[bytes] = deque()
        self._ready = threading.Condition()
        self.dropped = 0  # (blocks it was too slow for)

    def put(self, block: bytes) -> None:
        with self._ready:
            self._blocks.append(block)
            while len(self._blocks) > MOST_QUEUED:
                self._blocks.popleft()
                self.dropped += 1
            self._ready.notify()

    def take(self, timeout: float = KEEPALIVE) -> bytes:
        """The blocks waiting, together (waiting up to timeout for one: none then, a keepalive)."""
        with self._ready:
            if not self._blocks:
                self._ready.wait(timeout)
            blocks = list(self._blocks)
            self._blocks.clear()
        return b"".join(blocks) or HEADER.pack(MAGIC, KEEPALIVE_LANE, 0, 0, 0, 0.0, time.monotonic())


class Broadcast:
    """The pages listening, and what's sent to them. changed: called (outside its lock) when one comes or goes."""

    def __init__(self, changed: Callable[[], None] | None = None):
        self._listeners: tuple[ListeningPage, ...] = ()
        self._lock = threading.Lock()
        self._changed = changed

    def add(self, lanes: set[str]) -> ListeningPage:
        listener = ListeningPage(lanes)
        with self._lock:
            self._listeners = (*self._listeners, listener)
        if self._changed:
            self._changed()
        return listener

    def remove(self, listener: ListeningPage) -> None:
        with self._lock:
            self._listeners = tuple(x for x in self._listeners if x is not listener)
        if self._changed:
            self._changed()

    def wanted(self, lane: str) -> bool:
        """Whether a page listening wants a lane."""
        return any(lane in x.lanes for x in self._listeners)

    def count(self) -> int:
        return len(self._listeners)

    def send(self, lane: str, at: float, rate: int, samples: Any) -> None:
        """A block (frames x channels, -1 to 1), heard on the Mac at a time, to the pages wanting its lane (from the
        sound device's thread: nothing waited for but a page's queue)."""
        listeners = [x for x in self._listeners if lane in x.lanes]  # (the tuple as it is now: no lock)
        if not listeners:
            return
        block = encode(lane, at, rate, samples)
        for listener in listeners:
            listener.put(block)

    def song(self, at: float, rate: int, samples: Any) -> None:
        """The song's audio, as SongAudio's tap."""
        self.send("song", at, rate, samples)


class InputTap:
    """An audio input's channel and the one after it (an interface's stereo pair: the keyboard's sound, say), sent to
    the pages listening as a lane, each block with when it was heard. A device with no channel after it: that one
    alone."""

    def __init__(self, send: Callable[[str, float, int, Any], None], device: str, channel: int, lane: str = "keys",
                 stream_factory: Callable[..., Any] | None = None, query: Callable[[str], Any] | None = None):
        self.device, self.channel, self.lane = device, channel, lane
        self._send = send
        self._factory = stream_factory
        self._query = query  # (a device's channels and rate: sounddevice's; tests: their own)
        self._stream: Any = None

    def start(self) -> None:
        import sounddevice
        factory = self._factory or sounddevice.InputStream
        info = (self._query or (lambda name: sounddevice.query_devices(name, "input")))(self.device)
        most = int(info["max_input_channels"])
        if self.channel > most:
            raise ValueError(f"{self.device} has {most} inputs")
        rate = int(info["default_samplerate"])  # (the device's own: another would change it for everything on it)
        opened = min(self.channel + 1, most)
        first = self.channel - 1

        def callback(data: Any, frames: int, time_info: Any, _status: Any) -> None:
            self._send(self.lane, _heard_in(time_info, frames, rate), rate, data[:, first:opened])

        self._stream = factory(device=self.device, samplerate=rate, channels=opened, dtype="float32",
                               blocksize=BLOCK, latency="low", callback=callback)
        self._stream.start()

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()


def _heard_in(time_info: Any, frames: int, rate: int) -> float:
    """When an input block's first sample was heard, on time.monotonic's clock: the sound device's own time for it
    (on the Mac, its clock), else now less the block."""
    now = time.monotonic()
    try:
        adc = float(time_info.inputBufferAdcTime)
    except (AttributeError, TypeError):
        return now - frames / rate
    if 0.0 <= now - adc < 1.0:  # (the same clock)
        return adc
    return now - frames / rate

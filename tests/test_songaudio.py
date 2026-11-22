import numpy as np

from rs_keylights.songaudio import CAUGHT, DRIFT, HOP, JUMP, MOST_CATCH, SEEK, SongAudio, Stretcher, _heard

RATE = 44100


def tones(seconds=4.0):
    t = np.arange(int(RATE * seconds)) / RATE
    mono = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 660 * t)
    return np.stack([mono, mono * 0.5], axis=1).astype(np.float32)


def peak(samples):
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.fft.rfftfreq(len(samples), 1 / RATE)[np.argmax(spectrum)])


def test_at_speed_1_the_audio_is_as_it_is_from_where_it_goes():
    x = tones()
    stretcher = Stretcher(x)
    stretcher.seek(10000)
    assert np.allclose(stretcher.read(20000, 1.0), x[10000:30000], atol=1e-5)
    assert stretcher.position == 30000
    stretcher.seek(len(x) - 100)  # (past its end: silence)
    assert not stretcher.read(2000, 1.0)[200:].any()


def test_slowed_down_its_pitch_is_kept_and_it_goes_on_more_slowly():
    x = tones()
    stretcher = Stretcher(x)
    slow = stretcher.read(RATE * 2, 0.5)
    assert abs(peak(slow[RATE // 2:, 0]) - 440) < 3  # (440 Hz still: not 220)
    assert abs(stretcher.position - RATE) < HOP  # (2 seconds of it: 1 of the song)
    fast = Stretcher(x).read(RATE, 1.5)
    assert abs(peak(fast[RATE // 4:, 0]) - 440) < 3


class Song:
    """Where a song is: at position when the clock's at origin (None: paused), going on at its speed."""

    def __init__(self, origin=0.0):
        self.position, self.speed, self.origin = 0.0, 1.0, origin

    def where(self, at):
        return (None if self.position is None else self.position + (at - self.origin) * self.speed), self.speed


def test_it_plays_where_the_song_is_jumping_there_or_catching_up():
    x = tones()
    song = Song()
    audio = SongAudio(x, RATE, song.where, volume=0.5)
    first = audio.fill(4410, at=0.0)
    assert np.allclose(first, x[:4410] * 0.5, atol=1e-5)
    song.position = 2.0  # (sought: far off, it goes there)
    audio.fill(441, at=0.01)
    assert abs(audio._stretcher.position - (2.01 * RATE + 441)) < 2
    song.position = 2.0 + DRIFT / 2  # (a little ahead, less than a drift: played on as it is, not caught up)
    before = audio._stretcher.position
    played = audio.fill(4410, at=0.01 + 441 / RATE)
    assert audio._stretcher.position - before == 4410 and not audio._catching
    song.position = 2.0 + DRIFT * 2  # (further: caught up, not jumped)
    before = audio._stretcher.position
    audio.fill(4410, at=0.01 + 4851 / RATE)
    gone = audio._stretcher.position - before
    assert 4410 < gone <= 4410 * (1 + MOST_CATCH) + HOP and JUMP > DRIFT * 2 and audio._catching
    song.position = None  # (paused: silence, where it was)
    at = audio._stretcher.position
    assert not audio.fill(1000, at=1.0).any() and audio._stretcher.position == at
    assert played.any() and CAUGHT < DRIFT
    song.position = at / RATE - 1.0 + DRIFT / 4  # (on again, a little off: from just where it is, out of silence)
    audio.fill(441, at=1.0)
    assert abs(audio._stretcher.position - (at + DRIFT / 4 * RATE + 441)) < 2


def test_at_its_own_speed_it_plays_on_frame_after_frame_as_it_is():
    t = np.arange(RATE * 3) / RATE
    swell = (0.05 + 0.9 * t / 3) * np.sin(2 * np.pi * 220 * t)  # (louder a period on: searched, a frame went there)
    x = np.stack([swell, swell], axis=1).astype(np.float32)
    stretcher = Stretcher(x)
    stretcher.seek(1000)
    assert np.allclose(stretcher.read(RATE * 2, 1.0), x[1000:1000 + RATE * 2], atol=1e-5)
    stretcher.read(HOP * 7 + 300, 1.003)  # (caught up a while: frames fitted where they go best)
    at = int(stretcher.position) + HOP * 2
    later = stretcher.read(RATE // 2, 1.0)[HOP * 2:]  # (then on as it is, from where it's got to: within a search)
    start = next(k for k in range(at - SEEK - HOP, at + SEEK + HOP) if np.allclose(later[:64], x[k:k + 64], atol=1e-5))
    assert np.allclose(later, x[start:start + len(later)], atol=1e-5)


def test_a_block_is_timed_by_the_sound_devices_clock_for_when_its_heard():
    import time

    class Time:
        def __init__(self, dac, current):
            self.outputBufferDacTime, self.currentTime = dac, current

    now = time.monotonic()
    assert _heard(Time(now + 0.011, now)) == (now + 0.011, True)  # (the Mac's clock: its own time, exact)
    at, exact = _heard(Time(5.011, 5.0))  # (another clock: now and its delay)
    assert abs(at - (time.monotonic() + 0.011)) < 0.005 and not exact
    assert abs(_heard(None)[0] - time.monotonic()) < 0.005 and not _heard(None)[1]


def test_blocks_the_sound_device_missed_are_gone_over_at_once_not_caught_up():
    x = tones()
    song = Song()
    audio = SongAudio(x, RATE, song.where)
    audio.fill(441, at=0.0, exact=True)
    audio.fill(441, at=441 / RATE, exact=True)
    audio.fill(441, at=(441 * 3) / RATE, exact=True)  # (a block missed: 10 ms gone, less than a drift)
    assert abs(audio._stretcher.position - 441 * 4) < 2 and not audio._catching
    audio.fill(441, at=(441 * 4) / RATE + DRIFT / 4)  # (not exact: the same gap's just played on from)
    assert abs(audio._stretcher.position - 441 * 5) < 2


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs, self.started, self.closed = kwargs, False, False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


def test_it_plays_on_a_stream_of_the_audios_rate_and_channels():
    import time
    streams = []
    audio = SongAudio(tones(1.0), RATE, Song(origin=time.monotonic()).where,
                      stream_factory=lambda **kw: streams.append(FakeStream(**kw)) or streams[-1])
    audio.start()
    stream = streams[0]
    assert stream.started and stream.kwargs["samplerate"] == RATE and stream.kwargs["channels"] == 2
    out = np.zeros((512, 2), dtype=np.float32)

    class Time:
        outputBufferDacTime, currentTime = 1.01, 1.0

    stream.kwargs["callback"](out, 512, Time(), None)
    assert out.any()
    audio.close()
    assert stream.closed and "device" not in stream.kwargs  # (the Mac's own output)
    audio.start("Speakers")
    assert streams[-1].kwargs["device"] == "Speakers"


def test_parts_are_mixed_at_their_weights_as_it_plays():
    x = tones(1.0)
    low, high = x * 0.5, x * 0.25
    weights = {"low": 1.0, "high": 0.0}
    stretcher = Stretcher({"low": low, "high": high}, weights)
    assert np.allclose(stretcher.read(4410, 1.0), low[:4410], atol=1e-5)
    weights["high"] = 1.0  # (heard from the next frame on)
    later = stretcher.read(8820, 1.0)
    assert np.allclose(later[-2205:], (low + high)[4410 + 8820 - 2205:4410 + 8820], atol=1e-5)
    as_ints = Stretcher({"song": (x * 32767).astype(np.int16)})  # (int16: kept so, read as -1 to 1)
    assert np.allclose(as_ints.read(1000, 1.0), x[:1000], atol=1e-3)

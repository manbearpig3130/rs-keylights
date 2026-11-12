import numpy as np

from rs_keylights.songaudio import HOP, JUMP, MOST_CATCH, SongAudio, Stretcher

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
    def __init__(self):
        self.position, self.speed = 0.0, 1.0

    def where(self):
        return self.position, self.speed


def test_it_plays_where_the_song_is_jumping_there_or_catching_up():
    x = tones()
    song = Song()
    audio = SongAudio(x, RATE, song.where, volume=0.5)
    first = audio.fill(4410)
    assert np.allclose(first, x[:4410] * 0.5, atol=1e-5)
    song.position = 2.0  # (sought: far off, it goes there)
    audio.fill(441, latency=0.01)
    assert abs(audio._stretcher.position - (2.01 * RATE + 441)) < 2
    song.position = 2.02 + 441 / RATE  # (a little ahead: caught up, not jumped)
    before = audio._stretcher.position
    audio.fill(4410)
    gone = audio._stretcher.position - before
    assert 4410 < gone <= 4410 * (1 + MOST_CATCH) + HOP and JUMP > 0.02
    song.position = None  # (paused: silence, where it was)
    at = audio._stretcher.position
    assert not audio.fill(1000).any() and audio._stretcher.position == at


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
    streams = []
    audio = SongAudio(tones(1.0), RATE, Song().where, stream_factory=lambda **kw: streams.append(FakeStream(**kw))
                      or streams[-1])
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

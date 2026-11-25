import time

import numpy as np
import pytest

from rs_keylights.listeners import (HEADER, KEEPALIVE_LANE, LANES, MOST_QUEUED, Broadcast, InputTap, ListeningPage,
                                    decode, encode)


def test_a_block_is_sent_with_its_lane_rate_and_when_its_heard_its_samples_16_bit():
    block = np.array([[0.5, -0.5], [1.5, -2.0], [0.0, 0.25]], dtype=np.float32)
    data = encode("keys", 12.5, 48000, block, sent=12.4)
    assert len(data) == HEADER.size + 3 * 2 * 2
    [(lane, channels, frames, rate, at, sent, samples)] = decode(data)
    assert (lane, channels, frames, rate, at, sent) == (LANES["keys"], 2, 3, 48000, 12.5, 12.4)
    assert np.frombuffer(samples, "<i2").tolist() == [16383, -16383, 32767, -32767, 0, 8191]  # (clipped: never wraps)
    assert decode(encode("song", 1.0, 44100, np.zeros(4)))[0][1:3] == (1, 4)  # (mono: one channel)
    with pytest.raises(ValueError):
        decode(b"XXXX" + data[4:])


def test_each_page_gets_the_lanes_it_wants_and_coming_or_going_is_said():
    said = []
    broadcast = Broadcast(lambda: said.append(broadcast.count()))
    song_only, both = broadcast.add({"song"}), broadcast.add({"song", "keys"})
    assert said == [1, 2] and broadcast.wanted("keys") and broadcast.wanted("song")
    broadcast.send("keys", 3.0, 48000, np.zeros((8, 2)))
    broadcast.song(2.0, 44100, np.zeros((8, 2)))
    assert [b[0] for b in decode(both.take(0))] == [LANES["keys"], LANES["song"]]
    assert [b[0] for b in decode(song_only.take(0))] == [LANES["song"]]
    broadcast.remove(both)
    assert said == [1, 2, 1] and not broadcast.wanted("keys")
    broadcast.remove(song_only)
    broadcast.song(2.0, 44100, np.zeros((8, 2)))  # (no one listening: nothing made)
    assert said[-1] == 0


def test_a_page_too_slow_loses_its_oldest_blocks_and_one_sent_nothing_is_told_the_macs_there():
    page = ListeningPage({"song"})
    for i in range(MOST_QUEUED + 5):
        page.put(encode("song", float(i), 44100, np.zeros((1, 1))))
    blocks = decode(page.take(0))
    assert len(blocks) == MOST_QUEUED and blocks[0][4] == 5.0 and page.dropped == 5
    started = time.monotonic()
    [(lane, _c, frames, *_rest)] = decode(page.take(0.05))
    assert lane == KEEPALIVE_LANE and frames == 0 and time.monotonic() - started >= 0.04


class FakeInput:
    def __init__(self, **kwargs):
        self.kwargs, self.started, self.closed = kwargs, False, False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


def test_an_inputs_pair_is_sent_each_block_with_when_it_was_heard():
    sent, streams = [], []
    tap = InputTap(lambda *block: sent.append(block), "Blender", 9,
                   stream_factory=lambda **kw: streams.append(FakeInput(**kw)) or streams[-1],
                   query=lambda name: {"max_input_channels": 12, "default_samplerate": 48000.0})
    tap.start()
    stream = streams[0]
    assert stream.started and stream.kwargs["channels"] == 10 and stream.kwargs["samplerate"] == 48000
    assert stream.kwargs["device"] == "Blender"
    data = np.tile(np.arange(10, dtype=np.float32), (512, 1)) / 10

    class Time:
        inputBufferAdcTime = time.monotonic() - 0.02

    stream.kwargs["callback"](data, 512, Time(), None)
    [(lane, at, rate, block)] = sent
    assert lane == "keys" and rate == 48000 and at == Time.inputBufferAdcTime
    assert block.shape == (512, 2) and block[0].tolist() == pytest.approx([0.8, 0.9])  # (its 9 and 10)
    tap.close()
    assert stream.closed


def test_an_inputs_last_channel_is_sent_alone_and_one_it_hasnt_is_refused():
    sent, streams = [], []

    def factory(**kw):
        streams.append(FakeInput(**kw))
        return streams[-1]

    query = lambda name: {"max_input_channels": 3, "default_samplerate": 44100.0}  # noqa: E731
    tap = InputTap(lambda *block: sent.append(block), "Mixer", 3, stream_factory=factory, query=query)
    tap.start()
    assert streams[0].kwargs["channels"] == 3
    streams[0].kwargs["callback"](np.ones((4, 3), dtype=np.float32), 4, object(), None)  # (no device time: now)
    assert sent[0][3].shape == (4, 1) and abs(sent[0][1] - (time.monotonic() - 4 / 44100)) < 0.5
    with pytest.raises(ValueError, match="has 3 inputs"):
        InputTap(print, "Mixer", 4, stream_factory=factory, query=query).start()

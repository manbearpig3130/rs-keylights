from __future__ import annotations

import time
from typing import Callable, Protocol, cast

# The keyboard's MIDI input. The LK-170 lights keys only for notes on its navigate channels
# (FUNCTION > Navi. Ch: 4 for the right hand and 3 for the left by default), and still lights them
# with the channel's volume at 0, which makes silent lights.

DEFAULT_PORT = "CASIO USB-MIDI"
DEFAULT_CHANNEL = 4
BANK_SELECT = 0
VOLUME = 7
ALL_NOTES_OFF = 123
NORMAL_VOLUME = 100
VELOCITY = 100
SILENCE_EVERY = 5.0  # seconds; the keyboard forgets the volume when it's switched off and on


class MidiOutputProtocol(Protocol):
    def get_ports(self) -> list[str]:
        ...

    def open_port(self, port: int = 0, name: str | None = None) -> None:
        ...

    def send_message(self, message: list[int]) -> None:
        ...

    def close_port(self) -> None:
        ...


class KeyboardError(RuntimeError):
    pass


class Keyboard:
    def __init__(
        self,
        port_name: str = DEFAULT_PORT,
        channel: int = DEFAULT_CHANNEL,
        sound: bool = False,
        program: int | None = None,
        bank: int | None = None,
        midi_out_factory: Callable[[], MidiOutputProtocol] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not 1 <= channel <= 16:
            raise ValueError("The channel must be 1 to 16")
        if program is not None and not 0 <= program <= 127:
            raise ValueError("The program must be 0 to 127")
        if bank is not None and not 0 <= bank <= 127:
            raise ValueError("The bank must be 0 to 127")
        self.port_name = port_name
        self.channel = channel
        self.sound = sound
        self.program = program
        self.bank = bank
        self._factory = midi_out_factory or _default_midi_out
        self._clock = clock
        self._out: MidiOutputProtocol | None = None
        self._lit: set[int] = set()
        self._silenced_at = float("-inf")

    @property
    def lit(self) -> frozenset[int]:
        return frozenset(self._lit)

    def find_port(self) -> int | None:
        out = self._out or self._factory()
        self._out = out
        wanted = self.port_name.casefold()
        return next((i for i, port in enumerate(out.get_ports()) if wanted in port.casefold()), None)

    def open(self) -> None:
        index = self.find_port()
        if index is None:
            ports = self._out.get_ports() if self._out else []
            raise KeyboardError(f"No MIDI output named '{self.port_name}' (outputs: {', '.join(ports) or 'none'})")
        assert self._out is not None
        self._out.open_port(index, "rs-keylights")
        if self.program is not None:
            self.set_tone(self.program, self.bank)
        self._set_volume()

    def set_tone(self, program: int, bank: int | None = None) -> None:
        """Bank Select (when given), then Program Change."""
        if bank is not None:
            self._send([0xB0 | self._status, BANK_SELECT, bank])
        self._send([0xC0 | self._status, program])
        self.program, self.bank = program, bank

    def close(self) -> None:
        if self._out is None:
            return
        self.all_off()
        if not self.sound:
            self._send([0xB0 | self._status, VOLUME, NORMAL_VOLUME])
        self._out.close_port()

    def light(self, keys: tuple[int, ...]) -> None:
        if not self.sound and self._clock() - self._silenced_at >= SILENCE_EVERY:
            self._set_volume()
        for key in keys:
            self._send([0x90 | self._status, key, VELOCITY])
            self._lit.add(key)

    def unlight(self, keys: tuple[int, ...]) -> None:
        for key in keys:
            if key in self._lit:
                self._send([0x80 | self._status, key, 0])
                self._lit.discard(key)

    def all_off(self) -> None:
        for key in sorted(self._lit):
            self._send([0x80 | self._status, key, 0])
        self._lit.clear()
        self._send([0xB0 | self._status, ALL_NOTES_OFF, 0])

    @property
    def _status(self) -> int:
        return self.channel - 1

    def _set_volume(self) -> None:
        self._send([0xB0 | self._status, VOLUME, NORMAL_VOLUME if self.sound else 0])
        self._silenced_at = self._clock()

    def _send(self, message: list[int]) -> None:
        if self._out is None:
            raise KeyboardError("The keyboard isn't open")
        self._out.send_message(message)


def _default_midi_out() -> MidiOutputProtocol:
    try:
        import rtmidi
    except ImportError as exc:
        raise KeyboardError("python-rtmidi is required: pip install python-rtmidi") from exc
    midi_out = getattr(rtmidi, "MidiOut", None)
    if not callable(midi_out):
        raise KeyboardError("python-rtmidi has no MidiOut on this platform")
    return cast(MidiOutputProtocol, midi_out())

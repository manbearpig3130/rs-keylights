from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from . import dnafxtone
from .pedal import remember_names
from .rig import tone_def

# A song's tones as presets on the DNAfx, written as it loads (song-tones, for dnafx-preset's listener to switch to):
# each tone of the arrangement playing whose amp the DNAfx has (dnafxtone) made a preset, in the slots for song
# presets (dnafxtone.SONG_SLOTS), in the order the song plays them. What each slot has is remembered (a file), so a
# preset that's there already isn't written again: a song played again writes nothing. The slots' presets before the
# first write are saved (~/Music/DNAfx backups). No pedal, or one that can't be written: nothing (the rules choose).

STATE = Path.home() / "Library" / "Application Support" / "rs-keylights" / "pedal-song-presets.json"
BACKUPS = Path.home() / "Music" / "DNAfx backups"
ACK = 0x43  # (the device's answer to a preset written)
RETRY = 60.0  # seconds before trying a pedal again that couldn't be written


def _client() -> Any:
    from dnafx_preset.cli import build_client, build_parser
    return build_client(build_parser().parse_args(["show", "1"]))


class PedalWriter:
    def __init__(self, client_factory: Callable[[], Any] = _client, state_file: Path | None = None,
                 backups: Path | None = None, status: Callable[[str], None] = print,
                 clock: Callable[[], float] = time.monotonic):
        self._client_factory = client_factory
        self.state_file, self.backups = state_file or STATE, backups or BACKUPS
        self.status, self.clock = status, clock
        self._failed_at = -RETRY * 2

    def _state(self) -> dict[str, str] | None:
        try:
            data = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return None
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else None

    def _keep(self, state: dict[str, str]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=1, sort_keys=True))
        temp.replace(self.state_file)

    def forget(self) -> None:
        """What the slots have isn't known (something else wrote them): each written again when it's wanted."""
        state = self._state()
        if state is not None:
            self._keep({})

    def write(self, rig: Any, arrangement: str, keys: Sequence[str]) -> dict[str, int]:
        """The arrangement's tones (keys: in the order it plays them) the DNAfx has the amps of, as presets in the
        song slots: each tone's slot (none: no such tone, a bass part, no pedal)."""
        a = rig.arrangement(arrangement)
        if a is None or a.path.lower() == "bass" or self.clock() - self._failed_at < RETRY:
            return {}
        made = []
        for key in keys:
            tone = tone_def(a, key)
            amp = tone.gear.get("Amp") if tone is not None else None
            if tone is not None and amp is not None and amp.key in dnafxtone.TWIN_AMPS:
                made.append((key, dnafxtone.convert(tone)))
        if not made:
            return {}
        slots, names = {}, set()
        try:
            client = self._client_factory()
            state = self._state()
            if state is None:  # (the first write: the slots' presets saved first)
                self._save_slots(client)
                state = {}
            for (key, converted), slot in zip(made, dnafxtone.SONG_SLOTS):
                name = dnafxtone.short_name(key, names)
                names.add(name)
                preset = dnafxtone.pedal_preset(converted, slot, name)
                digest = hashlib.sha1(preset.to_bytes()).hexdigest()
                if state.get(str(slot)) != digest:
                    said = client.upload(preset)
                    if not any(command == ACK for command, _data in said):
                        self.status(f"Pedal: slot {slot} not written ({name}): the DNAfx didn't answer")
                        continue
                    state[str(slot)] = digest
                    self._keep(state)
                    remember_names({slot: name})  # (the page's and Launchkey's names: as the pedal has them)
                    self.status(f"Pedal: slot {slot} is {name} now")
                slots[key] = slot
        except Exception as exc:  # (no pedal, or the HID transport missing: the rules choose)
            self._failed_at = self.clock()
            self.status(f"Pedal: presets not written ({exc})")
            return slots
        return slots

    def _save_slots(self, client: Any) -> None:
        presets = {p.slot: p for p in client.read_presets()}
        if len(presets) != 200:
            raise RuntimeError("the DNAfx didn't send its presets")
        folder = self.backups / time.strftime("%Y-%m-%d %H%M%S song slots")
        folder.mkdir(parents=True, exist_ok=True)
        for slot in dnafxtone.SONG_SLOTS:
            (folder / f"{slot:03d}-{presets[slot].name or 'unnamed'}.bin").write_bytes(presets[slot].to_bytes())
        self.status(f"Pedal: slots {dnafxtone.SONG_SLOTS[0]} to {dnafxtone.SONG_SLOTS[-1]} saved in {folder}")

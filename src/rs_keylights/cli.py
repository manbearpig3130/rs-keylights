from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .follow import DEFAULT_OVERLAY_PORT, Follower, OverlayClient
from .keyboard import DEFAULT_CHANNEL, DEFAULT_PORT, Keyboard, KeyboardError
from .library import ArrangementRef, SongIndex, read_arrangements
from .lights import KeyRange, schedule
from .player import Player, play
from .psarc import ArchiveError
from .sng import SngError
from .tones import TONES, Tone, find_tones, tone

GAME_DIR_ENV = "RS_KEYLIGHTS_GAME_DIR"
REARMED_GAME_DIR = Path.home() / "Library" / "Application Support" / "RS Re-ARMed" / "game-dir"
STEAM_GAME_DIR = Path.home() / "Library" / "Application Support" / "Steam" / "steamapps" / "common" / "Rocksmith2014"
PART_ORDER = ("lead", "rhythm", "combo", "bass")
NO_NOTES = ("vocals", "jvocals", "showlights")
GUITAR_TONE = 79  # DISTORTION GT
BASS_TONE = 85  # FINGERED BASS


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (KeyboardError, ArchiveError, SngError, ValueError, OSError) as exc:
        print(f"rs-keylights: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rs-keylights",
        description="Light a keyboard's keys with Rocksmith 2014's notes.",
    )
    parser.add_argument("--midi-port", default=DEFAULT_PORT, help=f"MIDI output, or part of its name (default: {DEFAULT_PORT})")
    parser.add_argument("--channel", type=int, default=DEFAULT_CHANNEL,
                        help=f"the keyboard's navigate channel, whose notes light keys (default: {DEFAULT_CHANNEL})")
    parser.add_argument("--game-dir", type=Path, help=f"the Rocksmith2014 folder (default: RS Re-ARMed's, or ${GAME_DIR_ENV})")
    commands = parser.add_subparsers(dest="command", required=True)

    follow = commands.add_parser("follow", help="light the keys with the song Rocksmith plays (silently)")
    follow.add_argument("--overlay-port", type=int, default=DEFAULT_OVERLAY_PORT,
                        help=f"RS Companion's stream overlay port (default: {DEFAULT_OVERLAY_PORT})")
    follow.add_argument("--offset-ms", type=float, default=0.0, help="light the keys this much earlier (negative: later)")
    follow.add_argument("--sound", action="store_true", help="play the notes too, not just light the keys")
    follow.add_argument("--tone", help=f"with --sound, the keyboard's tone (default: {GUITAR_TONE}, or {BASS_TONE} for bass)")
    follow.add_argument("--trace", type=Path, metavar="FILE",
                        help="write what RS Companion reports to FILE (JSON lines), to look into timing problems")
    follow.set_defaults(handler=run_follow)

    play_parser = commands.add_parser("play", help="play an arrangement on the keyboard, with lights and sound")
    play_parser.add_argument("song", help="a .psarc file, or part of a song's name, artist or file name")
    play_parser.add_argument("part", nargs="?", help="lead, rhythm, bass, ... (default: the first of lead, rhythm, bass)")
    play_parser.add_argument("--from", dest="start", type=float, default=0.0, metavar="SECONDS", help="start at this song position")
    play_parser.add_argument("--speed", type=float, default=1.0, help="playback speed (default: 1)")
    play_parser.add_argument("--silent", action="store_true", help="lights only")
    play_parser.add_argument("--tone", help=f"the keyboard's tone, by number or name (default: {GUITAR_TONE} "
                             f"{tone(GUITAR_TONE).name}, or {BASS_TONE} {tone(BASS_TONE).name} for bass); see 'tones'")
    play_parser.set_defaults(handler=run_play)

    tones = commands.add_parser("tones", help="list the keyboard's tones, for play --tone")
    tones.add_argument("text", nargs="?", help="only tones whose name or group contains this (e.g. organ, lead)")
    tones.set_defaults(handler=run_tones)

    index = commands.add_parser("index", help="update the index of the game's songs")
    index.set_defaults(handler=run_index)
    return parser


def game_dir(args: argparse.Namespace) -> Path:
    if args.game_dir is not None:
        found = args.game_dir.expanduser()
    elif os.environ.get(GAME_DIR_ENV):
        found = Path(os.environ[GAME_DIR_ENV]).expanduser()
    elif REARMED_GAME_DIR.is_file():
        found = Path(REARMED_GAME_DIR.read_text().strip())
    else:
        found = STEAM_GAME_DIR
    if not (found / "dlc").is_dir() and not (found / "songs.psarc").is_file():
        raise ValueError(f"No Rocksmith songs in {found}; is its drive connected? Choose another with --game-dir")
    return found


def open_index(args: argparse.Namespace) -> SongIndex:
    index = SongIndex(game_dir(args))
    first = len(index) == 0

    def progress(done: int, total: int) -> None:
        if first and (done % 25 == 0 or done == total):
            print(f"\rIndexing songs: {done}/{total}", end="" if done < total else "\n", flush=True)

    index.refresh(progress)
    return index


def run_index(args: argparse.Namespace) -> int:
    index = open_index(args)
    print(f"{len(index)} arrangements in {index.game_dir}")
    return 0


def run_follow(args: argparse.Namespace) -> int:
    fixed = None
    if args.tone is not None:
        if not args.sound:
            print("rs-keylights: --tone needs --sound", file=sys.stderr)
            return 1
        fixed = choose_tone(args.tone, bass=False)
        if fixed is None:
            return 1
    index = open_index(args)
    keyboard = Keyboard(args.midi_port, args.channel, sound=args.sound)
    keyboard.open()
    trace = open(args.trace, "a") if args.trace else None
    print(f"{len(index)} arrangements. Lighting keys{' with sound' if args.sound else ''} on '{args.midi_port}', "
          f"channel {args.channel}. Ctrl-C stops.")

    def tone_for(ref: ArrangementRef) -> Tone:
        return fixed or tone(BASS_TONE if ref.bass else GUITAR_TONE)

    def record(entry: dict[str, object]) -> None:
        if trace is not None:
            trace.write(json.dumps(entry) + "\n")
            trace.flush()

    follower = Follower(
        OverlayClient(args.overlay_port), index, keyboard, offset=args.offset_ms / 1000,
        status=lambda message: print(message, flush=True),
        tone_for=tone_for if args.sound else None,
        trace=record if trace is not None else None,
    )
    try:
        follower.run()
    except KeyboardInterrupt:
        pass
    finally:
        keyboard.close()
        if trace is not None:
            trace.close()
    return 0


def run_tones(args: argparse.Namespace) -> int:
    found = find_tones(args.text) if args.text else list(TONES)
    if not found:
        print(f"rs-keylights: no tone matches '{args.text}'", file=sys.stderr)
        return 1
    group = None
    for t in found:
        if t.group != group:
            group = t.group
            print(group)
        print(f"  {t.number:03d}  {t.name}")
    return 0


def choose_tone(text: str | None, bass: bool) -> Tone | None:
    if text is None:
        return tone(BASS_TONE if bass else GUITAR_TONE)
    found = find_tones(text)
    if len(found) == 1:
        return found[0]
    if not found:
        print(f"rs-keylights: no tone matches '{text}'; 'rs-keylights tones' lists them", file=sys.stderr)
    else:
        print(f"'{text}' matches {len(found)} tones; say which:", file=sys.stderr)
        for t in found:
            print(f"  {t.number:03d}  {t.name}", file=sys.stderr)
    return None


def run_play(args: argparse.Namespace) -> int:
    chosen = None if args.silent else choose_tone(args.tone, bass=False)
    if not args.silent and chosen is None:
        return 1
    refs = find_song(args)
    if refs is None:
        return 1
    ref = choose_part(refs, args.part)
    if ref is None:
        parts = ", ".join(r.part for r in refs if r.part not in NO_NOTES)
        print(f"rs-keylights: no part '{args.part}'; there are {parts}", file=sys.stderr)
        return 1

    arrangement = ref.load()
    key_range = KeyRange()
    shift = key_range.octave_shift(arrangement.notes)
    lights = schedule(arrangement.notes, key_range, shift)
    others = [r.part for r in refs if r is not ref and r.part not in NO_NOTES]
    moved = ", an octave up" if shift == 12 else f", {shift // 12} octaves up" if shift else ""
    tuning = " ".join(f"{t:+d}" if t else "0" for t in arrangement.tuning)
    print(f"{ref.title} ({ref.name}): {len(lights)} notes, tuning {tuning}{moved}")
    if others:
        print(f"Other parts: {', '.join(others)}")

    if not args.silent and args.tone is None:
        chosen = choose_tone(None, ref.bass)
    keyboard = Keyboard(args.midi_port, args.channel, sound=not args.silent,
                        program=chosen.program if chosen else None, bank=chosen.bank if chosen else None)
    keyboard.open()
    print(f"Tone {chosen.number:03d} {chosen.name}. Ctrl-C stops." if chosen else "Ctrl-C stops.")
    try:
        play(Player(lights, keyboard), start=args.start, speed=args.speed)
    except KeyboardInterrupt:
        pass
    finally:
        keyboard.close()
    return 0


def find_song(args: argparse.Namespace) -> list[ArrangementRef] | None:
    path = Path(args.song).expanduser()
    if path.suffix.lower() == ".psarc" or path.is_file():
        refs = read_arrangements(path)
        if not refs:
            print(f"rs-keylights: {path.name} has no Mac arrangements", file=sys.stderr)
            return None
        return refs

    matches = open_index(args).search(args.song)
    archives: dict[str, list[ArrangementRef]] = {}
    for ref in matches:
        archives.setdefault(ref.archive, []).append(ref)
    if not archives:
        print(f"rs-keylights: no song matches '{args.song}'", file=sys.stderr)
        return None
    if len(archives) > 1:
        print(f"'{args.song}' matches {len(archives)} songs; say which (part of the file name works too):", file=sys.stderr)
        for refs in archives.values():
            named = max(refs, key=lambda r: bool(r.artist))  # some custom songs leave names out of a manifest
            print(f"  {named.title}  ({Path(named.archive).name})", file=sys.stderr)
        return None
    return next(iter(archives.values()))


def choose_part(refs: list[ArrangementRef], wanted: str | None) -> ArrangementRef | None:
    playable = [r for r in refs if r.part not in NO_NOTES]
    if wanted:
        wanted = wanted.casefold()
        return next((r for r in playable if wanted in (r.part, r.name.casefold())), None)
    for part in PART_ORDER:
        for ref in playable:
            if ref.part == part:
                return ref
    return playable[0] if playable else None


if __name__ == "__main__":
    raise SystemExit(main())

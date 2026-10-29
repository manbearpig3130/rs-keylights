from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .follow import DEFAULT_OVERLAY_PORT, Follower, OverlayClient
from .gui import DEFAULT_GUI_PORT
from .keyboard import DEFAULT_CHANNEL, DEFAULT_PORT, Keyboard, KeyboardError
from .lights import LIGHTS_EARLY, MIN_PERIOD
from .library import NO_NOTES, ArrangementRef, SongIndex, read_arrangements
from .player import play
from .psarc import ArchiveError
from .session import Part, plan, player_for
from .sng import SngError, note_name
from .sound import BASS_TONE, CHANNEL_SETS, DEFAULT_SPLIT, GUITAR_TONE, SoundChoice, parse_note
from .tones import TONES, Tone, find_tones, tone

GAME_DIR_ENV = "RS_KEYLIGHTS_GAME_DIR"
REARMED_GAME_DIR = Path.home() / "Library" / "Application Support" / "RS Re-ARMed" / "game-dir"
STEAM_GAME_DIR = Path.home() / "Library" / "Application Support" / "Steam" / "steamapps" / "common" / "Rocksmith2014"
PART_ORDER = ("lead", "rhythm", "combo", "bass")


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
    add_light_options(follow)
    add_sound_options(follow, "with --sound, ")
    follow.add_argument("--trace", type=Path, metavar="FILE",
                        help="write what RS Companion reports to FILE (JSON lines), to look into timing problems")
    follow.set_defaults(handler=run_follow)

    play_parser = commands.add_parser("play", help="play an arrangement on the keyboard, with lights and sound")
    play_parser.add_argument("song", help="a .psarc file, or part of a song's name, artist or file name")
    play_parser.add_argument("parts", nargs="*", metavar="part",
                             help="lead, rhythm, bass, ...: one or more, played together (default: the first of lead, "
                                  "rhythm, bass)")
    play_parser.add_argument("--from", dest="start", type=float, default=0.0, metavar="SECONDS", help="start at this song position")
    play_parser.add_argument("--speed", type=float, default=1.0, help="playback speed (default: 1)")
    play_parser.add_argument("--silent", action="store_true", help="lights only")
    add_light_options(play_parser)
    play_parser.add_argument("--lights", metavar="PART[=N],...",
                             help="which parts light keys, and how many each (4 in all): e.g. lead=2,rhythm=2, or "
                                  "lead,rhythm (2 each), or none (default: the first part, 4)")
    add_sound_options(play_parser, "")
    play_parser.set_defaults(handler=run_play)

    tones = commands.add_parser("tones", help="list the keyboard's tones, for play --tone")
    tones.add_argument("text", nargs="?", help="only tones whose name or group contains this (e.g. organ, lead)")
    tones.set_defaults(handler=run_tones)

    index = commands.add_parser("index", help="update the index of the game's songs")
    index.set_defaults(handler=run_index)

    gui = commands.add_parser("gui", help="a page in the browser to pick songs, parts and tones, and play or follow")
    gui.add_argument("--port", type=int, default=DEFAULT_GUI_PORT, help=f"its port on 127.0.0.1 (default: {DEFAULT_GUI_PORT})")
    gui.add_argument("--overlay-port", type=int, default=DEFAULT_OVERLAY_PORT,
                     help=f"RS Companion's stream overlay port, for following (default: {DEFAULT_OVERLAY_PORT})")
    gui.add_argument("--no-browser", action="store_true", help="don't open the page in the browser")
    gui.set_defaults(handler=run_gui)
    return parser


def add_light_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lights-early", type=float, default=LIGHTS_EARLY * 1000, metavar="MS",
                        help=f"light the keys this much before the notes (default: {LIGHTS_EARLY * 1000:g}; "
                             f"the LK-170's lights look late without)")
    parser.add_argument("--fastest-flash", type=float, default=MIN_PERIOD * 1000, metavar="MS",
                        help=f"a key's repeats closer than this flash on every other note (default: "
                             f"{MIN_PERIOD * 1000:g}; the LK-170 can't show faster ones apart)")


def add_sound_options(parser: argparse.ArgumentParser, when: str) -> None:
    each = "; PART=... for one part only (repeat the option for others)"
    parser.add_argument("--tone", action="append", metavar="[PART=]TONE",
                        help=f"{when}the keyboard's tone, by number or name (default: {GUITAR_TONE} "
                             f"{tone(GUITAR_TONE).name}, or {BASS_TONE} {tone(BASS_TONE).name} for bass){each}; see 'tones'")
    parser.add_argument("--mute-tone", action="append", metavar="[PART=]TONE",
                        help=f"{when}a tone for palm-muted notes, e.g. 077 MUTE GUITAR{each}")
    parser.add_argument("--low-tone", action="append", metavar="[PART=]TONE",
                        help=f"{when}a tone for notes below --split{each}")
    parser.add_argument("--split", action="append", metavar="[PART=]NOTE",
                        help=f"with --low-tone, the lowest note of the other tones (default: {note_name(DEFAULT_SPLIT)}; "
                             f"a name such as B1 or F#2, or a MIDI note number){each}")
    parser.add_argument("--real-pitch", action="store_true",
                        help=f"{when}sound the notes at their own pitch, not the lit keys' (moved into the keyboard's range)")


def for_part(value: str) -> tuple[str | None, str]:
    """'lead=178' -> ('lead', '178'); '178' -> (None, '178')."""
    if "=" in value:
        part, text = value.split("=", 1)
        return part.strip().lower() or None, text.strip()
    return None, value.strip()


def sound_choice(args: argparse.Namespace, sound: bool, without: str) -> SoundChoice | None:
    """The checked sound options, or None after printing why they can't be used."""
    given = [name for name, value in (("--tone", args.tone), ("--mute-tone", args.mute_tone), ("--low-tone", args.low_tone),
                                      ("--split", args.split), ("--real-pitch", args.real_pitch)) if value]
    if not sound:
        if given:
            print(f"rs-keylights: {given[0]} {without}", file=sys.stderr)
            return None
        return SoundChoice({}, {}, {}, {}, False)
    if args.split and not args.low_tone:
        print("rs-keylights: --split needs --low-tone", file=sys.stderr)
        return None
    tables: list[dict[str | None, Tone]] = []
    for values in (args.tone, args.low_tone, args.mute_tone):
        table: dict[str | None, Tone] = {}
        for value in values or []:
            part, text = for_part(value)
            chosen = choose_tone(text, bass=False)
            if chosen is None:
                return None
            table[part] = chosen
        tables.append(table)
    splits: dict[str | None, int] = {}
    try:
        for value in args.split or []:
            part, text = for_part(value)
            splits[part] = parse_note(text)
    except ValueError as exc:
        print(f"rs-keylights: {exc}", file=sys.stderr)
        return None
    return SoundChoice(tables[0], tables[1], tables[2], splits, args.real_pitch)


def lights_for(spec: str | None, parts: list[str]) -> dict[str, int] | None:
    """How many keys each part lights (4 in all), from --lights; None after printing why not."""
    if not spec:
        return {parts[0]: 4}
    if spec.strip().lower() == "none":
        return {}
    counts: dict[str, int | None] = {}
    for item in (x.strip() for x in spec.split(",") if x.strip()):
        part, _, count = item.partition("=")
        part = part.strip().lower()
        if part not in parts:
            print(f"rs-keylights: --lights {part}: not one of the parts playing ({', '.join(parts)})", file=sys.stderr)
            return None
        if count and not (count.strip().isdigit() and 1 <= int(count) <= 4):
            print(f"rs-keylights: --lights {item}: 1 to 4 keys", file=sys.stderr)
            return None
        counts[part] = int(count) if count else None
    given = sum(c for c in counts.values() if c)
    unset = [p for p, c in counts.items() if c is None]
    if unset:
        left = 4 - given
        if left < len(unset):
            print("rs-keylights: --lights: the keyboard lights 4 keys at most", file=sys.stderr)
            return None
        for i, part in enumerate(unset):
            counts[part] = left // len(unset) + (1 if i < left % len(unset) else 0)
    if sum(c for c in counts.values() if c) > 4:
        print("rs-keylights: --lights: the keyboard lights 4 keys at most", file=sys.stderr)
        return None
    return {p: c for p, c in counts.items() if c}


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
    choice = sound_choice(args, args.sound, "needs --sound")
    if choice is None:
        return 1
    index = open_index(args)
    keyboard = Keyboard(args.midi_port, args.channel)
    keyboard.open()
    trace = open(args.trace, "a") if args.trace else None
    print(f"{len(index)} arrangements. Lighting keys{' with sound' if args.sound else ''} on '{args.midi_port}', "
          f"channel {args.channel}. Ctrl-C stops.")

    def record(entry: dict[str, object]) -> None:
        if trace is not None:
            trace.write(json.dumps(entry) + "\n")
            trace.flush()

    follower = Follower(
        OverlayClient(args.overlay_port), index, keyboard, offset=args.offset_ms / 1000,
        status=lambda message: print(message, flush=True),
        voices_for=choice.voices if args.sound else None,
        trace=record if trace is not None else None,
        lights_early=args.lights_early / 1000,
        fastest_flash=args.fastest_flash / 1000,
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


def run_gui(args: argparse.Namespace) -> int:
    import webbrowser

    from .gui import Gui, GuiServer

    index = open_index(args)
    overrides: dict[str, object] = {}
    if args.midi_port != DEFAULT_PORT:
        overrides["midi_port"] = args.midi_port
    if args.channel != DEFAULT_CHANNEL:
        overrides["channel"] = args.channel
    gui = Gui(index, overlay_port=args.overlay_port, overrides=overrides)
    try:
        server = GuiServer(gui, args.port)
    except OSError as exc:
        raise OSError(f"Port {args.port} is in use ({exc.strerror}): is rs-keylights gui already running? "
                      f"Or choose another with --port") from exc
    print(f"{len(index)} arrangements. rs-keylights is at {server.url} . Ctrl-C stops.", flush=True)
    if not args.no_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        gui.close()
        server.server_close()
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
    choice = sound_choice(args, not args.silent, "plays sound: leave out --silent")
    if choice is None:
        return 1
    refs = find_song(args)
    if refs is None:
        return 1
    chosen: list[ArrangementRef] = []
    for wanted in args.parts or [None]:
        ref = choose_part(refs, wanted)
        if ref is None:
            parts = ", ".join(r.part for r in refs if r.part not in NO_NOTES)
            print(f"rs-keylights: no part '{wanted}'; there are {parts}", file=sys.stderr)
            return 1
        if ref not in chosen:
            chosen.append(ref)
    names = [ref.part for ref in chosen]
    if len(chosen) > len(CHANNEL_SETS):
        print(f"rs-keylights: {len(CHANNEL_SETS)} parts at most", file=sys.stderr)
        return 1
    unknown = sorted(choice.parts - set(names) - {ref.name.lower() for ref in chosen})
    if unknown:
        print(f"rs-keylights: {unknown[0]}= is not one of the parts playing ({', '.join(names)})", file=sys.stderr)
        return 1
    counts = lights_for(args.lights, names)
    if counts is None:
        return 1
    used = [c for channels in CHANNEL_SETS[: len(chosen)] for c in channels]
    if not args.silent and args.channel in used:
        print(f"rs-keylights: channel {args.channel} is for the sound of these parts; light another one", file=sys.stderr)
        return 1

    parts = [Part(ref, ref.load(), counts.get(ref.part, 0)) for ref in chosen]
    planned = plan(parts, None if args.silent else choice)
    print(chosen[0].title)
    for p in planned:
        lit = p.part.lights
        moved = ", an octave up" if p.shift == 12 else f", {p.shift // 12} octaves up" if p.shift else ""
        tuning = " ".join(f"{t:+d}" if t else "0" for t in p.part.arrangement.tuning)
        shows = f", {lit} {'key' if lit == 1 else 'keys'} lit" if lit else ", not lit"
        print(f"  {p.part.ref.name}: {len(p.lights)} notes, tuning {tuning}{moved}{shows}")
    others = [r.part for r in refs if r not in chosen and r.part not in NO_NOTES]
    if others:
        print(f"Other parts: {', '.join(others)}")

    keyboard = Keyboard(args.midi_port, args.channel)
    keyboard.open()
    player = player_for(planned, keyboard, lead=args.lights_early / 1000, min_period=args.fastest_flash / 1000)
    for p in planned:
        if p.voices:
            print(f"  {p.part.ref.name} sound: {p.voices.describe()}")
    print("Ctrl-C stops.")
    try:
        play(player, start=args.start, speed=args.speed)
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

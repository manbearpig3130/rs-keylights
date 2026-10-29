# rs-keylights

Lights the keys of a light-up keyboard with the notes of Rocksmith 2014, so you can watch how a song
would go on a piano.

- **`follow`**: lights the keys, silently, along with the song Rocksmith is playing. It follows the
  game through [RS Companion](https://github.com/glebb/rs-mac-companion)'s stream overlay API.
- **`play`**: plays an arrangement from a song file on the keyboard, with lights and sound, without
  the game.

Made for the Casio LK-170 on macOS, with [RS Re-ARMed](https://github.com/bbelgg/rs-rearmed) or
Rocksmith 2014 Remastered. Other Casio keyboards with key lights and "navigate channels" (LK-175,
LK-260 and similar) probably work too.

## Setup

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
```

Connect the keyboard with a USB cable; it shows up as the MIDI port `CASIO USB-MIDI`.

For `follow`, turn on **Stream overlays** in RS Companion's Settings. RS Companion then serves the
song timer, the song and the selected arrangement on `127.0.0.1:9938` (only on this Mac).

The first run indexes the game's song files (about 30 s for 2,000 archives on a USB drive). Later
runs only read new or changed archives. The game folder is RS Re-ARMed's, unless you give
`--game-dir` or set `RS_KEYLIGHTS_GAME_DIR`.

## Use

```sh
.venv/bin/rs-keylights follow                      # Ctrl-C stops
.venv/bin/rs-keylights follow --sound --tone 178   # play the notes too, on a saw lead
.venv/bin/rs-keylights play "pursuit of vikings"   # the lead part, with lights and sound
.venv/bin/rs-keylights play amonpurs bass --from 60 --speed 0.5
.venv/bin/rs-keylights play ~/Downloads/Some-Song_m.psarc rhythm --silent
.venv/bin/rs-keylights play alicroos --tone "saw lead 1"  # one of the keyboard's tones
.venv/bin/rs-keylights tones organ                 # list tones (all of them without a word)
.venv/bin/rs-keylights play amonpurs --mute-tone 77 --low-tone 80 --split E2   # a tone per kind of note
.venv/bin/rs-keylights play amonpurs lead rhythm --tone lead=178 --lights lead=2,rhythm=2   # two parts together
.venv/bin/rs-keylights index                       # update the index now
```

`play` takes a `.psarc` file, or part of a song's name, artist or file name. With no part it plays
the lead, else the rhythm, else the bass. Name more parts to play them together (up to four), each on
channels of its own. The keyboard lights 4 keys at most in all (on both navigate channels together), so
by default only the first part lights them; `--lights lead=2,rhythm=2` shares them (each part's lowest
notes), `--lights lead,rhythm` shares them evenly, `--lights rhythm` lights another part, and
`--lights none` none. Lit parts share an octave shift so their keys line up, and a key two parts hold
stays lit until both let go.

The sound is the keyboard's tone 079 DISTORTION GT, or 085 FINGERED BASS for bass parts. `--tone` picks
another of its 426 tones by number or name: `--tone 178`, `--tone "saw lead 1"`, `--tone koto`. Tones
401-426 play the sounds you sampled on the keyboard. The tone you pick on the keyboard's panel is
only for the keys you play; the song plays on its own channel.

Some notes can have tones of their own. `--mute-tone` plays palm-muted notes with another tone (for
example 077 MUTE GUITAR), and `--low-tone` the notes below `--split` (E2 by default: a note name such
as B1 or F#2, or a MIDI note number), for when a tone sounds muddy low down. A palm mute takes the mute
tone wherever it is. In The Pursuit of Vikings, every low B is a palm-muted chug, so `--mute-tone` alone
changes all of them. Accented notes play louder. `--real-pitch` sounds the notes at their own pitch,
which the keyboard plays over MIDI even below its lowest key, instead of the lit keys' pitch (moved up
into the keys' range); each tone has a range of its own, though, and some (077 MUTE GUITAR) don't go as
low as a B standard guitar. These work in `follow --sound` too.

Each tone option can be for one part: `--tone lead=178 --tone rhythm=79 --mute-tone rhythm=77`. Without
`PART=` it is for every part. In `follow`, it is for the part you play, e.g. `--tone bass=85`.

| Option | Default | |
|---|---|---|
| `--midi-port` | `CASIO USB-MIDI` | the keyboard's MIDI port, or part of its name |
| `--channel` | 4 | the keyboard's navigate channel |
| `--game-dir` | RS Re-ARMed's game folder | the `Rocksmith2014` folder |
| `follow --offset-ms` | 0 | light the keys this much earlier (negative: later) |
| `follow --overlay-port` | 9938 | RS Companion's overlay port |
| `follow --sound` | off | play the notes too (with the sound options below) |
| `follow --trace FILE` | | write what RS Companion reports (JSON lines), to look into timing problems |
| `play --from`, `--speed` | 0, 1 | where to start (seconds) and how fast |
| `play --silent` | | lights only |
| `play PARTS...` | lead, else rhythm, else bass | the parts to play, together |
| `play --lights` | the first part, 4 keys | which parts light keys, and how many each |
| `--tone` | 079, or 085 for bass | the keyboard's tone, by number or name (`tones` lists them); `PART=` for one part |
| `--mute-tone` | the main tone | a tone for palm-muted notes |
| `--low-tone`, `--split` | the main tone; E2 | a tone for notes below the split note |
| `--real-pitch` | off | sound the notes' own pitch, not the lit keys' |
| `--lights-early` | 15 ms | light the keys this much before the notes (`play` and `follow`) |
| `--fastest-flash` | 70 ms | a key's repeats closer than this flash on every other note (`play` and `follow`) |

## How it works

**The keys.** The LK-170 lights keys only for notes on its *navigate channels* (FUNCTION > `Navi. Ch`:
4 for the right hand, 3 for the left). Notes on other channels sound but don't light anything. It
still lights them with the channel's volume at 0, so the lights are always silent. At most 4 keys light
at once, and the keys run from C2 to C7.

**The sound.** The keyboard plays all 16 MIDI channels at once, each with a tone of its own. The first
part's notes sound on channel 11 (the main tone), 12 (the low tone) and 13 (palm mutes), the second
part's on 14 to 16, the third's on 5 to 7 and the fourth's on 8, 9 and 2: clear of the navigate channels,
the keyboard's own part (1) and the drums (10). A chord sounds all its notes, even when only some of them
light.

**The notes.** Each arrangement's notes are in its `.sng` file in the song's archive. The note's
string and fret, the song's tuning and the capo give its pitch. Each phrase is taken at its hardest
level, so you see the full part, not your current difficulty level. A part that goes below C2 (B
standard guitar, bass) is moved up whole octaves if it still fits; any note still outside the keys
is folded in. A note or chord stays lit until the next one, at least 80 ms, and chords light their
lowest 4 notes.

**The flashing.** On the LK-170 a key flashing every 80 ms or slower looks like flashes, and every 60
ms almost like a steady light; it looked best every ~115 ms with 40-55% of it dark. So before a key's
next strike it goes dark for 45% of the time to it (30 to 60 ms), and repeats faster than 70 ms
(`--fastest-flash`) flash on every other note. These are times you see, so at a slower speed (or in
a slowed Riff Repeater) notes have more room. The keyboard's lights also look a little late next to
its sound, so the keys light 15 ms before the notes (`--lights-early`: 10-20 ms looked in time, 40
too early).

**The timing.** RS Companion updates the song timer about 4 times a second. `follow` reads it 10
times a second and keeps its own clock between the updates. Each value is seen a little late, never
early, so the clock goes by the freshest of the last 2.5 s. It also measures the timer's rate, for
Riff Repeater's slower speeds, and treats a timer that stops as a pause and a jump as a seek. In a
test with a simulated timer, the lights were a median 9 ms late (95% within 15 ms).

The game's own habits, as a trace (`follow --trace`) showed them: when you pause, the timer's last
step is very short, so the keys go dark at that update instead of waiting for the timer to stay
still. Riff Repeater rewinds the timer quickly to the section's start, and the keys stay dark while
it does. After you pick a new speed, three updates in a row at the new speed are enough to measure it.

**Riff Repeater below about 65% speed needs a fixed RS Companion.** RS Companion 1.4.2 checks that
the song is still playing by watching the game's play record advance at normal speed; slower, it
decides another song is playing after 10 s of song time and stops reporting the song (and its timer)
until you start a song again. The fix is on the
[`fix/slow-riff-repeater`](https://github.com/manbearpig3130/rs-mac-companion/tree/fix/slow-riff-repeater)
branch of a fork, until it is in a release: install it with `app/build.sh --install`, then click Update in its
window (with the game closed).

## The keyboard's quirks

- **It ignores MIDI while its idle light show runs.** After about 6 minutes without a key or button
  press on the AC adaptor, the keys start lighting by themselves, and the keyboard stops reading USB
  MIDI until you touch it. When you'll just watch for a while, turn it off: FUNCTION until `Demo2`,
  then [–].
- **It switches off after 30 minutes without a key or button press.** Hold TONE while switching it on
  to keep it on.
- **Settings reset when it's switched on**, including the above. Channel volume resets too;
  `follow` sends it again every few seconds, so the lights stay silent.
- If no keys light, check that `Navi. Ch` is 04 (or use `--channel` with yours) and that the PART
  SELECT button hasn't turned the navigate channels off.

## Development

```sh
uv pip install --python .venv/bin/python -e ".[test]"
.venv/bin/python -m pytest
.venv/bin/pyright src
```

The tests build small song files in the game's formats, so they need no game files.

## Credits

- The tone list (with each tone's Bank Select and Program Change) is from Casio's LK-170/LK-175/LK-260
  appendix.
- The `.sng` layout follows [Rocksmith2014.NET](https://github.com/iminashi/Rocksmith2014.NET) by
  iminashi.
- [RS Companion](https://github.com/glebb/rs-mac-companion) by glebb finds the song timer in the
  game. Its overlay API is [RockSniffer](https://github.com/kokolihapihvi/RockSniffer)'s, and its
  docs describe the Mac song files.
- The archive and song file keys are the well-known Rocksmith 2014 ones.

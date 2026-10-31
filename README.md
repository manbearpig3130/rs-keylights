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
.venv/bin/rs-keylights gui                       # a page in the browser for all of the below
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
| `follow --trace FILE` | | write what RS Companion reports (JSON lines), to look into timing problems (`gui --trace FILE` too, while the page follows) |
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

## The page (`gui`)

`rs-keylights gui` serves a page at `http://127.0.0.1:8040/` and opens it in the browser. It does what
the commands do:

- **Library**: every song in the game folder, searchable by song, artist, album or file name. Clicking one
  shows it (its parts, with their notes and tuning, and its key) without changing what plays; its **⏵**
  switches to it: it becomes the active song, the one Play plays and the Launchkey shows (another one
  playing stops). The active song is marked ▶; the bar above a song shown that isn't active switches to it
  or goes back to the active one, and so does a click on the status at the top.
- **Key**: worked out from all its parts' notes: its scale, notes outside the scale it uses a lot, a key
  nearly as likely, and, for a guitar tuned down (or with a capo), the key your fingers play
  ("C# minor, played as F# minor in B standard"). It names a mode when one fits clearly better than major
  or minor (E Phrygian, D Mixolydian). **Show on the keys** climbs its scale up the whole keyboard and
  back, holding the tonic chord at each end, over and over until it's stopped, a key is pressed on the
  keyboard, or the song playing needs the keys. On well-known songs it gets the usual tonic 14 times in
  16; riffs that sit on another note than home (Iron Man) fool it.
- **Parts**: a block each: how many keys each lights (− and +, 4 in all: + is greyed when none are free; a
  part unmuted takes only the keys still free), its volume on the keyboard (100% as it starts, up to 127%;
  it changes at once, the notes going on, and it's the volume of the arrangement in its place, first,
  second…, in every song, playing or following), its mute (🔇: it doesn't play, sound or lit keys; the
  Launchkey's pad on top for it, in the mixer or not, is the same switch; a song with every part muted still
  plays, so they can be unmuted as it goes), and under them its
  tone, palm-mute tone, low tone and split note (from the keyboard's tone list). Until you change them, every
  part plays and lights a key, the keys left over going to the lead and rhythm parts (lead, rhythm and
  bass: 2, 1 and 1; lead and bass: 3 and 1). A song with more than 4 parts starts with its first lead,
  rhythm and bass.
- **Playback**: Play (Pause while it plays, Resume while it's paused), Stop and Mute (the sound on the
  keyboard, playing or following: muted, just the lights), the speed (25% to 150%), real pitch, a start
  time, and a slider that shows where the song is and moves it while it plays.
- **Follow Rocksmith**: start and stop following the game (with sound unless muted). While following, the
  song you play is the one shown in the middle (once it ends, until the next; the library can still be
  gone through, but other songs can't be picked meanwhile), with each of its arrangements; the speed and where it starts are the game's. The one you
  play plays on every key; unmute others to play along (the backing band), and choose the keys each lights
  (4 in all). Those choices are kept for the song and the arrangement you play: playing the bass instead
  starts from just the bass again. Any arrangement can have tones of its own (tone, palm mutes, low notes
  and split), or stay on the default tones. It says when another
  `rs-keylights follow` (such as the RS Re-ARMed sidekick) is running, since both would light the keys.
- **Default tones**: one set each for lead, rhythm and bass parts (combos count as lead), for parts
  without tones of their own, playing a song or following the game. Greyed choices are defaults.
- **Keyboard**: the MIDI output, the lights' channel, lights early and fastest flash (kept in
  `~/Library/Application Support/rs-keylights/gui.json`), and a button to look for new songs. The two
  timings apply at once, to what plays or is followed.

Changes to the parts, their tones and the lights apply at once while a song plays or is followed, from
where it is.

Everything chosen is kept on the Mac, so every page (and the Launchkey) shows and changes the same: each
song's parts, lit keys, tones and start time in `songs.json`, the rest in `gui.json` (both in
`~/Library/Application Support/rs-keylights/`). Without `--lan` the server answers
only on 127.0.0.1, to pages it served. `--port` changes its port, `--no-browser` doesn't open the page, and
`--midi-port`/`--channel` before `gui` override the saved keyboard settings for that run.

### A Launchkey as a remote

With a Novation Launchkey MK4 (Mini or not) plugged in, the page takes over its DAW controls while it
runs (and gives them back when it stops, so a DAW can have them). It works on the song followed, else the
song playing, else the active one:

- **The arrangements** (▲ beside the knobs, lit): **pads 1–6 on top** are the song's arrangements,
  orange for lead parts, green for rhythm, blue for bass. Bright ones play, muted ones are as dim as their
  colour goes, and the one you play in the game pulses. A tap mutes one or plays it again (the page's mute
  for it, and the mixer's pad: one switch). **Pads 1–6 below**: each one's lit keys, brighter for
  more. A tap gives one more (round to none), within the keyboard's 4. The page shows each at once; the
  song takes them once the taps stop (each change plays the parts again, stopping the notes sounding).
  **Knobs 1–6**: each steps through its arrangement's tones; the screen (that tone, and what the knobs do)
  and the page show each, and the song plays with the one it stops on. **Func** (right of the pads) goes
  from tones to palm-mute tones to low-note tones and back, three squares at the top right of the tone's
  screen showing which. Palm-mute and low-note tones start with "Same tone" (the tone the rest plays with,
  even over a default) and, when their kind has a default, "Default", a group by themselves, so knob 8
  gets back to them in a step. The page's palm-mute and low-note choices have "Same tone" under the
  default too, when there is one. ▲ again shows what the knobs do (again: away). While a tone shows, **knob 8** steps through the groups of tones (piano, organ, guitar…).
- **Its keys** (column 7, purple): they play the Casio on a part of their own (channel 1: no song's part or
  light uses it, and the Casio's own keys keep their tone), playing or following, a song or not. The top
  pad turns them on or off (off: dim), the bottom one whether they light the keys they play first (the Casio lights 4
  keys at most, so while you hold keys the song's lights get the room left, their lowest, and come back as
  you let go), and **knob 7** steps through their tone (knob 8 its group, while it shows; or choose it
  under **Launchkey keys** on the page). Mute mutes them too. The octave buttons, a sustain pedal, the
  modulation strip and its own Scale mode (snapping to the song's key) work as usual.
- **Playing the song** (▼ beside the knobs, lit): **knob 1** moves through the song (or, while nothing
  plays, where the active song starts: a handle moves along the song screen's progress line and its time
  with it, keeping up with the song as it plays, until 3 seconds after), **knob 2** sets the speed, 5% a
  step from 25% to 150% (the progress line turns into it, 100% in the middle, the speed said in the scale's
  place; the song takes it once it stops), and **knob 3** goes a section at a time (its name in the
  scale's place; back, to the start of the one it's in first). These knobs and the pads below take the
  screen back from the library (stopping a flick through it). ▼ again shows what the knobs do (again:
  away). The sections are the chart's (intro,
  verse, chorus…), so they're as good as its charter made them.
  - **The pads below** (1–7): a kind of section each (verse blue, chorus orange, solo red, bridge purple,
    riff green, intro and outro grey…), the one it's in bright; a tap goes to the next section of that kind
    (after the last, the first), so tapping the chorus again goes on to the chorus after. A song with more
    than 7 kinds keeps its most common.
  - **An A-B loop**, on the pads on top: **A** (pad 1) puts its start where the song is, **B** (pad 2) its
    end, and loops; pad 3 turns it on and off (pulsing green while it's on), and pad 4 loops the section the
    song is in (again: off). Each lands on the song's nearest beat, and **knobs 4 and 5** move the start and
    end a beat at a time. Reaching the end, the song goes back to a second before the start. Brackets on
    the progress line show it (joined underneath while it's on); the page shows it under its slider, with
    Loop and Clear. Moving past the end leaves the loop until the song is back before it. With the speed
    (knob 2), it's for practising a part slowly. Following, the game's Riff Repeater does this.
- **The mixer**: **Track** ▷ (lit) opens it until ▷ again, and **knob 8** while no tone shows opens it for a
  few seconds (longer each time it's used, however it's used). Knobs 1–6 are the arrangements' volumes, knob 7 its keys', knob 8 all of them
  together, 1% a step, at once (the notes going on), the screen showing a slider for each with its volume
  over it; the pads on top mute each one or play it again, as outside the mixer (muted: red, its slider
  greyed with a crossed-out speaker), its keys' pad too. Each is the volume of the arrangement in that place
  in every song. The **pitch strip** moves the slider last used (a knob's, or a pad's on top; after knob 8 or
  Mute, all of them), a step a step as you slide; letting go doesn't move it. The pads below give lit
  keys as outside it, shown as rails either side of each slider, as high as it's lit (its keys': lighting
  first). Nothing's said for the pads (the sliders show them). Opened by ▷, it stays until ▷ again or
  anything else is done (Mute leaves it open, the speaker in its corner; the pads below, Follow and real
  pitch don't close it). Follow and real pitch say what they did over the names for a few seconds, or
  until the mixer is used again. While it's open the pads are on the arrangements, even after ▼; they go back to playing the
  song as it closes.
- **The right column**: the top pad is Mute, as on the page (green: sound on; red: muted, just the lights;
  every screen shows a crossed-out speaker in its corner), the bottom one following the game (on or off).
- **The upper arrow** (right of the pads): real pitch, lit while it's on. **Play** plays and pauses the
  song (the screen's ▶ or ❚❚ says which), or plays the active one.
- **The pitch strip** (left): goes through the library, the song it's on (until it's used, the active one)
  boxed in the middle of the page's list and its details (album art, key, tuning, length, tempo, parts)
  under it, as for a song the mouse points at. Slid slowly it moves a song at a time; flicked, it keeps
  going, slowing, until it's touched again; while it shows, **knob 8** goes a letter at a time (the artists':
  on, to the first of the next; back, to the first of this one, then the one before); let go slowly, it stays on the song it was held on (a finger
  coming off moves it a step or two: that's undone), and it springs back without moving anything. **Record**
  switches to it, or, on the song already, stops it (■), though not while following (the game picks the
  song). **Track** ◁ is home: the screen back on the song (or, following, on waiting for one), and the page's
  list back to it (to start from the start, knob 1 after ▼).
  Scrolling or clicking the list puts it away (just pointing at it doesn't). The song it's on takes the
  screen while it's in use (its name, the line its place in the library, "● = select" and "▲ = return", ⑧
  A-Z, and its number in the corner, "882/2212"), and gives it back 5 seconds after.
- **The modulation strip**: modulation for its keys' sound (the Casio's vibrato, or a synth's), the
  Launchkey showing its own readout of it as it moves.
- **Its Scale button**: its own Scale mode takes each song's key, so its screen shows the song's scale and
  its keys can snap to it; while it's on, the pads show the scale as a piano octave from C (the tonic red,
  notes it uses outside the scale purple) and it climbs the keys (again as each song starts). Only the
  button turns it off: told to by a message, the Launchkey says it did, but its light and scale screen stay,
  so the pads stay with them. A key pressed on the keyboard, the song needing the keys, or the page's
  **Stop showing** only stops the climb.
- **The screen**: a picture of the song (its name over two lines if it needs them; following Rocksmith with
  no song yet, whether the game's reached), its artist, key and
  scale, the line between them filling as it plays, after a ▶ (❚❚ paused, ■ stopped), and the time
  beside the key (where it is, and its length). After a knob, what it's on takes the screen for a few
  seconds (the speed or section in the scale's place, the tone they're on). What a tap turned on or off is
  said at the bottom left of whatever the screen shows, for a few seconds ("Real pitch: on", "Lead: off",
  "Lead: 2 lit keys", "Loop 1:05.0-1:12.5: on", or why it can't be done). Muted, every screen shows a
  crossed-out speaker in its corner.

`rs-keylights launchkey` shows what the Launchkey sends as the remote, to check how it answers (quit the
page first: both would take it over).

As each song starts, its scale climbs the keys too, while the song doesn't need them (turn it off in
Follow Rocksmith). The Launchkey's keys go on playing as usual. Turn the remote off under Keyboard.

### On a phone

The game pauses when it loses focus, so changing tones on the Mac stops the song. With
`rs-keylights gui --lan` the page answers on the network too, and a phone on the same Wi-Fi can be the
remote. It prints a link with a key (the page on the Mac shows it too, under Keyboard): open it once on the
phone, and after that the plain address (`http://NAME.local:8040/`) works, on the home screen too. On the
network the server answers only to the Mac's own names and addresses, and only to browsers with the key
(a cookie keeps it). If the Mac's firewall is on, allow Python's incoming connections when macOS asks.

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

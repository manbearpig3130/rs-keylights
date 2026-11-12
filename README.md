# rs-keylights

Lights the keys of a light-up keyboard with the notes of Rocksmith 2014, so you can watch how a song
would go on a piano.

- **`follow`**: lights the keys, silently, along with the song Rocksmith is playing. It follows the
  game itself through rearmed-sidekicks' riffloop mod in RS Re-ARMed, or else through
  [RS Companion](https://github.com/glebb/rs-mac-companion)'s stream overlay API.
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

For `follow`, RS Re-ARMed with rearmed-sidekicks' riffloop mod is all it needs: the game itself says which
arrangement is playing and where it is. Without the mod, turn on **Stream overlays** in RS Companion's Settings.
RS Companion then serves the song timer, the song and the selected arrangement on `127.0.0.1:9938` (only on this
Mac).

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
.venv/bin/rs-keylights play amonpurs --mute-tone 77 --pinch-tone 298   # a tone per kind of note
.venv/bin/rs-keylights play amonpurs lead rhythm --tone lead=178 --lights lead=2,rhythm=2   # two parts together
.venv/bin/rs-keylights index                       # update the index now
.venv/bin/rs-keylights midi amontwil --audio -o ~/Music   # a MIDI file and its audio, for a DAW
```

`play` takes a `.psarc` file, or part of a song's name, artist or file name. With no part it plays
the lead, else the rhythm, else the bass. Name more parts to play them together, each on
channels of its own: as many as its voices need (one, and one more each for palm mutes and pinch harmonics with
tones of their own: pinch harmonics only for a part that has them), from the keyboard's 12 (none for a part on another MIDI device), so five or more fit. The keyboard lights 4 keys at most in all (on both navigate channels together), so
by default only the first part lights them; `--lights lead=2,rhythm=2` shares them (each part's lowest
notes), `--lights lead,rhythm` shares them evenly, `--lights rhythm` lights another part, and
`--lights none` none. Lit parts share an octave shift so their keys line up, and a key two parts hold
stays lit until both let go.

The sound is the keyboard's tone 079 DISTORTION GT, or 085 FINGERED BASS for bass parts. `--tone` picks
another of its 426 tones by number or name: `--tone 178`, `--tone "saw lead 1"`, `--tone koto`. Tones
401-426 play the sounds you sampled on the keyboard. The tone you pick on the keyboard's panel is
only for the keys you play; the song plays on its own channel.

Some notes can have tones of their own. `--mute-tone` plays palm-muted notes with another tone (for
example 077 MUTE GUITAR), and `--pinch-tone` pinch harmonics' squeals (298 GM GT HARMONICS by default; 0
for the part's own tone). A palm mute takes the mute tone wherever it is. In The Pursuit of Vikings, every low B is a palm-muted chug, so `--mute-tone` alone
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
| `--pinch-tone` | 298 GM GT HARMONICS | a tone for pinch harmonics' squeals (0: the part's own) |
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
- **Fretboard** (under the song): that key's scale on the guitar the song's played on, in its tuning (its
  first guitar part's; a bass's four strings with no guitar part): the open strings and every fret to the
  24th, the thin string on top as in tab (or the thick one, as you see the neck looking down at the guitar:
  **Thick string on top**, kept in the browser), each note named, the root notes red, and the notes it uses a
  lot outside the scale ringed. A capo is drawn on its fret, the notes behind it faded. "B minor (natural)
  · guitar in B standard" for most Amon Amarth songs. While the song plays (or, following, while the game
  plays it), an arrangement's notes light up blue where the chart plays them (string and fret), the scale
  fading behind them, each lit until it's struck again on its string or rings out, and the notes in the next
  0.6 s ringed; a chord's shape shows while it's held, each string marked by its place in the chord (R red,
  b3, 3, 5, b7…), lit as it's struck, and the chord's name beside the title. Whose notes: the dropdown on the
  card (following, the arrangement the game plays; else the first guitar part that plays), its own tuning.
- **Parts**: a block each: how many keys each lights (− and +, 4 in all: + is greyed when none are free; a
  part unmuted takes only the keys still free), its volume on the keyboard (100% as it starts, up to 127%;
  it changes at once, the notes going on, and it's the volume of the arrangement in its place, first,
  second…, in every song, playing or following), its mute (🔇: it doesn't play, sound or lit keys; the
  Launchkey's pad on top for it, in the mixer or not, is the same switch; a song with every part muted still
  plays, so they can be unmuted as it goes), and under them its
  tone, palm-mute tone and harmonics tone (from the keyboard's tone list; pinch harmonics GM GT
  HARMONICS unless you choose another, or "Same tone"). Until you change them, every
  part plays and lights a key, the keys left over going to the lead and rhythm parts (lead, rhythm and
  bass: 2, 1 and 1; lead and bass: 3 and 1). A song with more than 4 parts starts with its first lead,
  rhythm and bass; the others can play too, each taking the keyboard's channels its tones need (12 in all:
  one a part, and one more each for palm-mute and harmonics tones of their own (pinch harmonics only
  for a part that has them; short of channels, they squeal on the part's own tone); none for a part on another
  MIDI device). Too many, and it says so.
- **Tone changes**: when a song switches tone (as the game does: its arrangements have up to four tones,
  named and with their gear), the part switches with it, to the keyboard tone for that kind of tone (clean,
  drive, distortion, lead, acoustic, piano, organ, synth, the bass kinds…; from the tone's name, else its
  pedals), chosen in the **Tone changes** card (as they start, the keyboard's General MIDI tones, 294 GM
  CLEAN GUITAR, 296 GM OVERDRIVE GT, 297 GM DISTORTION GT and so on: the LK-170's own guitar tones sound
  an octave below the keys); back to the song's first tone, to the part's own tone (so
  the part's tone is the one for the song's start). Kinds it doesn't know, or set to "No change", leave the
  tone as it is; palm-mute and harmonics tones stay. The Launchkey says each change as it comes ("Lead:
  distortion", the arrangement whose chords show) and keeps the tone it's on lit up at the right of the
  artist's line ("Clean", "Dist"; in the lyrics, before the time). On the page, each part with tone changes
  lists them under its tones ("Song's tones: clean · 0:54 distortion · 1:23 clean", tones of no kind it
  knows dashed), the one it's on lit while it plays or is followed. Unticked, the parts keep their tones.
- **Guitar** (needs `uv pip install --python .venv/bin/python -e ".[listen]"`): ticked, the guitar's notes,
  heard on its input (the Rocksmith USB Guitar Adapter, which the game uses at the same time), light their keys
  first, the song's lights getting the room left (the keyboard lights 4 keys at most). A note is heard about 30
  to 60 ms after it's played (its pitch, as a tuner finds it); while a song plays or is followed, its chords are
  heard note by note (each of the notes it expects then, its harmonics against a semitone either side's). A
  chord with no song to go by, or one the song doesn't expect, is heard from its harmonics: the strongest
  note's, then, those taken away, the next's, and so on (a note doubled an octave up can go unlit, its
  harmonics the lower one's). It listens down to 30 Hz, a guitar tuned down and dropped too (B standard with a
  drop pedal an octave down: B0, 31 Hz); a bass arrangement played, lower still. The card says what it hears.
  `rs-keylights listen` prints the notes it hears as you play (`--bass` for a bass), to check it; `--record
  FILE` saves what it hears to a WAV file too, and `--replay FILE` hears that file again instead of the input
  (to tune how it hears, from chords that were heard wrong).
- **Other MIDI devices**: lead, rhythm or bass parts can sound on another MIDI device (a synth, such as a
  Korg monologue, with its own speakers) instead of the keyboard, which still lights their keys: the **Other
  MIDI devices** card chooses its output, its channel, and whether it plays a note at a time (each note
  stopping as the next sounds, as a monophonic synth needs; on by default), its program (empty: the one it's
  on; a Korg monologue takes them with its Global MIDI program change receive on), and octaves up or down. No
  tones or tone changes are sent to it; notes, bends, vibrato, its program and the mixer's volume (Control
  Change 7, if it takes it) are. On the Launchkey, the arrangement's knob steps through its programs (knob 8,
  ten at a time). Not connected, the keyboard plays the part. Knob 7 picking an arrangement with no chords (a
  bass) shows the note it plays instead ("G1").
- **Techniques** as the charts have them: bends (pitch bend, following the chart's bend: held, then moved to
  each point; as far as the chart bends: each channel's bend range set to the part's biggest bend), slides (a semitone at a time over the note's sustain,
  chords too; the last left to the note it slides into), slides off (quickly, at the note's end, fading, a smear of 6 steps at most), vibrato
  (Modulation), tremolo picking (struck again every 75 ms), hammer-ons, pull-offs and taps (softer), slaps and pops
  (as hard as accents), natural harmonics (their own pitch: a 7th-fret harmonic an octave and a fifth over the
  open string, lit there too, and on the part's harmonics tone: GM GT HARMONICS unless chosen otherwise), pinch
  harmonics (the fretted note, softer, under its squeal: an octave and a
  fifth over it, struck at once half a semitone flat and scooped up to its pitch, then a fast, wide vibrato (pitch bend,
  over the chart's bend) growing in, on the part's harmonics tone: GM GT HARMONICS unless chosen otherwise;
  the **Pinch harmonics** card sets how high, how loud, the scoop and the vibrato, on every one or only where the
  chart has vibrato, and **Try it** plays a riff with a quick one and a held one), dead notes (a quiet click on the palm-mute tone, or a softer tick on the part's
  own without one; each stops what's ringing on its string, and the fretted note before it), palm mutes a little
  softer, and strums: a
  chord's strings one after another, down (low to high) on the beat and its eighths, up (high to low, a little
  softer) on the sixteenths between (the charts don't say which way: their pick direction is never set), 7 ms
  a string, tighter when the next chord comes quickly. **Keyboard** › Sound turns them off (accents alone).
- **Notes ring** as a guitar's strings do: each one sounds until its string is played again, a chord's or
  arpeggio's until its hand shape ends (as the chart has them) or the fretting hand moves (its fretted strings:
  the chart's hand positions), but not single notes in a chord's shape with palm mutes in it too (a riff: the
  shape is its fingering, as in Guardians of Asgaard's lead), an open string on under the notes after it until a chord or a palm mute stops
  it (a drone under a riff), else until the part's next note, and on a
  little after it, overlapping (120 ms as it starts, set beside it; a note played again stops as it is); 4 seconds
  at most (longer if the chart holds it), its own length at least, and palm mutes stay short: a chug, 0.6 of
  the time to the next note (50 to 200 ms), or as long as the chart holds it. The tone's own
  decay does the rest. **Keyboard** › Sound turns it off (each note as long as its key is lit), playing and
  following at once.
- **The header**: the song's time slider (where it is, moved while it plays; else where it starts) and its
  speed (25% to 200%), ▶ (❚❚ while it plays; ▶ again resumes), ■, Mute 🔊 (the sound on the keyboard,
  playing or following: muted 🔇, just the lights), 🎵 the song's own audio (decoded from its archive in a
  fraction of a second, played from the Mac's output with a song you play: paused, moved and looped with it, and
  slowed or sped up at its own pitch, a time-stretch; off each time rs-keylights starts, and not while following;
  how loud and on which audio output: **Song's audio** in the Keyboard card),
  **Follow Rocksmith** (red while following), ⚙ to fold the
  settings column away (the middle gets its room) or bring it back, and full screen ⛶. They keep their
  widths, so nothing moves as they change.
- **The song** in the middle: its art, title and artist, its key and scale in the top right, and a line for each arrangement (lit
  keys, volume, mute); its **Tones** button opens its tones (folded away at first, as during a song: the
  Launchkey has them). The fretboard under it shows what it lights; its legend shows as the mouse is on it.
- **The tab**, under the fretboard: an arrangement's notes as tab, two rows of bars (1 to 4 a row; 2 at
  first): the row the song's in, a line where it is, and the next; reaching the second, it goes up, as a page
  turns. The notes being played light up, the ones played are dimmed, and the chords' names are over them;
  techniques as tab has them (h, p and t before the fret, / and \ for slides, b for bends, ~ for vibrato,
  <7> for harmonics, x for fret-hand mutes, palm mutes in italics). Its strings are the fretboard's way up,
  and its arrangement the fretboard's (following: the one played in the game), or one picked. Not playing,
  it shows where the song starts.
- **The settings column**: each card folds into its title (the setup ones folded at first; remembered).
- **Follow Rocksmith** (its button in the header): start and stop following the game (with sound unless
  muted); full screen while following, if wanted (as it's started on the page: a browser only goes full
  screen as it's clicked). While following, the
  song you play is the one shown in the middle (once it ends, until the next; the library can still be
  gone through, but other songs can't be picked meanwhile), with each of its arrangements; the speed and where it starts are the game's. The one you
  play plays on every key; unmute others to play along (the backing band), and choose the keys each lights
  (4 in all). Those choices are kept for the song and the arrangement you play: playing the bass instead
  starts from just the bass again. Any arrangement can have tones of its own (tone, palm mutes, pinch
  harmonics), or stay on the default tones. It says when another
  `rs-keylights follow` (such as the RS Re-ARMed sidekick) is running, since both would light the keys.
- **Default tones**: one set each for lead, rhythm and bass parts (combos count as lead), for parts
  without tones of their own, playing a song or following the game. Greyed choices are defaults.
- **Keyboard**: the MIDI output, the lights' channel, lights early and fastest flash, notes ringing, **most
  lights** (the lit parts share the 4 lit keys a moment at a time: as many different keys as they play, a note
  two parts play taking one and a resting part leaving its keys to the others; the arrangement you play in the
  game first, then the song's order, each part's lowest notes first; a part's lit keys then just say whether it
  lights keys: an On/Off button on the page, and the Launchkey's pad below it turns them on or off, "Lead:
  lights on", any number of parts lit) and real pitch
  (the notes at their own pitch, not the lit keys', playing and following; on at first), kept in
  `~/Library/Application Support/rs-keylights/gui.json`, and a button to look for new songs. The two
  timings apply at once, to what plays or is followed.

Changes to the parts, their tones and the lights apply at once while a song plays or is followed, from
where it is.

Everything chosen is kept on the Mac, so every page (and the Launchkey) shows and changes the same: each
song's parts, lit keys, tones and start (where the slider is, not playing) in `songs.json`, the rest in `gui.json` (both in
`~/Library/Application Support/rs-keylights/`). A song is kept by its file's place in the game folder (`dlc/x_m.psarc`),
so its choices stay with it when the game folder moves (choices kept by an earlier version, by the whole path, are found
in the new folder too), and by its song key when its file has another name there. Without `--lan` the server answers
only on 127.0.0.1, to pages it served. `--port` changes its port, `--no-browser` doesn't open the page, and
`--midi-port`/`--channel` before `gui` override the saved keyboard settings for that run.

### A Launchkey as a remote

With a Novation Launchkey MK4 (Mini or not) plugged in, the page takes over its DAW controls while it
runs (and gives them back when it stops, so a DAW can have them). It works on the song followed, else the
song playing, else the active one:

- **The arrangements** (▲ beside the knobs, lit): **pads 1–5 on top** are the song's arrangements (a
  sixth, rare, is the page's only), and its drum part after them (white; in a song with five, the fifth place, that arrangement on the
  page only; not made yet, with the tools there, dim: a tap makes it, pulsing meanwhile, then it plays): its pad on top mutes it, the mixer's knob is its volume (no tones), and
  its pad below turns **beat lights** on or off: that pad flashes white on each of the song's beats as it plays (or the
  game's, following), brighter on a bar's first, dim between, and the other lit pads below pulse with it (dim between
  beats, as they were on each one; the pads on top and the buttons stay steady),
  orange for lead parts, green for rhythm, blue for bass. Bright ones play, muted ones are as dim as their
  colour goes, and the one you play in the game pulses. A tap mutes one or plays it again (the page's mute
  for it, and the mixer's pad: one switch). **Pads 1–5 below**: each one's lit keys, brighter for
  more. A tap gives one more (round to none), within the keyboard's 4. The page shows each at once; the
  song takes them once the taps stop (each change plays the parts again, stopping the notes sounding).
  **Knobs 1–5**: each steps through its arrangement's tones; the screen (that tone, and what the knobs do)
  and the page show each, and the song plays with the one it stops on. **▲ again** (beside the knobs) goes
  from tones to palm-mute tones to harmonics tones and back, three squares at the top right of the tone's
  screen showing which. Palm-mute and harmonics tones start with "Same tone" (the tone the rest plays with,
  even over a default) and, when their kind has a default, "Default", a group by themselves, so knob 8
  gets back to them in a step. The page's palm-mute and pinch-harmonic choices have "Same tone" under the
  default too, when there is one. Switching to the arrangements (▲) shows what the knobs do for a few
  seconds. While a tone shows, **knob 8** steps through the groups of tones (piano, organ, guitar…).
- **The guitar** (column 6, yellow): **knob 6** steps through the guitar pedal's presets (a Harley Benton
  DNAfx GiT, see [Guitar pedal](#guitar-pedal)), the screen showing the one it's on, its name, and the
  favourites; the pedal switches once the knob rests on one (so turning past a few doesn't switch through them
  all). While it shows, **knob 8** goes ten at a time, round from the last to the first. The **top pad**
  goes through your favourite presets (51 and 187 to start with; change them on the page's **Guitar pedal**
  card), the next each tap, the first when it's on none of them, bright while it's on one: from there,
  knob 6 reaches the ones near it. The **bottom pad** keeps the pedal on the tone it's on, or lets it switch
  with the song's tones again (bright while it follows them; "Pedal: holding this tone" on the screen): it
  makes dnafx-preset's hold file, and the listener switches to the song's tone once it's let go. Following,
  while the pedal shows, **Record** keeps the preset shown for the song tone playing, for this song ("Rec:
  keep for dist" on the screen; on its own preset already, "Rec: dist automatic" puts it back to the rules):
  hold the tone, try presets with knob 6, and keep the one you like. The page's
  **Guitar pedal** card has it too ("Switch with the song's tones"); the guitar lighting the keys is on the
  page's **Guitar** card.
- **Its keys** (column 7, purple): they play the Casio on a part of their own (channel 1: no song's part or
  light uses it, and the Casio's own keys keep their tone), playing or following, a song or not. The top
  pad turns them on or off (off: dim), the bottom one whether they light the keys they play first (the Casio lights 4
  keys at most, so while you hold keys the song's lights get the room left, their lowest, and come back as
  you let go), and **knob 7** steps through their tone (knob 8 its group, while it shows; or choose it
  under **Launchkey keys** on the page). Mute mutes them too. The octave buttons, a sustain pedal, the
  modulation strip and its own Scale mode (snapping to the song's key) work as usual. **Plays on** (on the page)
  sends them to another MIDI output instead, on a channel you choose (IAC Driver Bus 1: an app on this Mac, such
  as Komplete Kontrol with a guitar into Guitar Rig), with its own sound; they still light the Casio's keys.
- **Playing the song** (▼ beside the knobs, lit): **knobs 1 and 2** move the A-B loop's start and end
  (above pads 1 and 2, which set them; see below), **knob 3** moves through the song (or, while nothing
  plays, where the active song starts: a handle moves along the song screen's progress line and its time
  with it, keeping up with the song as it plays, until 3 seconds after), **knob 5** sets the speed, 1% a
  step from 25% to 200% (the progress line turns into it, 100% in the middle, the speed said in the scale's
  place; the song takes it as it turns), and **knob 4** goes a section at a time (above pad 4, which loops one; its name in the
  scale's place; back, to the start of the one it's in first). These knobs and the pads below take the
  screen back from the library (stopping a flick through it). ▼ again shows what the knobs do (again:
  away). Following, with Rocksmith paused (Play), **knob 3** steps through the game's song as its pause
  screen's ← and → do (a key a step, 8 at most a turn; turned faster than the game takes them, the extra
  steps are dropped so it doesn't fall behind) and **knob 4** a phrase at a time (its PageDown and
  PageUp); the game's own timeline shows where (the Launchkey's doesn't follow it yet). The sections are the chart's (intro,
  verse, chorus…), so they're as good as its charter made them.
  - **The pads below** (1–7): a kind of section each (verse blue, chorus orange, solo red, bridge purple,
    riff green, intro and outro grey…), the one it's in bright; a tap goes to the next section of that kind
    (after the last, the first), so tapping the chorus again goes on to the chorus after. A song with more
    than 7 kinds keeps its most common.
  - **An A-B loop**, on the pads on top: **A** (pad 1) puts its start where the song is, **B** (pad 2) its
    end, and loops; pad 1 tapped twice clears it (A and B), pad 3 turns it on and off (pulsing green while it's on), and pad 4 loops the section the
    song is in (again: off). Each lands on the song's nearest beat, and **knobs 1 and 2** move the start and
    end a beat at a time. Reaching the end, the song goes back to a second before the start. Brackets on
    the progress line show it (joined underneath while it's on); the page shows it under its slider, with
    Loop and Clear. Moving past the end leaves the loop until the song is back before it. With the speed
    (knob 5), it's for practising a part slowly. Following, it's for Rocksmith's Riff Repeater: set A and B where the game is
    (the song screen shows its time, exactly), or the section, and with RS Re-ARMed's riffloop mod (in
    [rearmed-sidekicks](https://github.com/manbearpig3130/rearmed-sidekicks)) the range becomes a phrase of its
    own in the song, picked in Riff Repeater already (else pick it: it's between the usual ones), and it loops
    just that, count-in and Accelerator as ever ("Pick it in Riff Repeater", the screen says; the page's loop
    line too). Moved, pick it again. Without the mod the screen says so. Following another song, or stopping,
    the song's phrases are as they were. Following, **knob 5** is Riff Repeater's speed (1% a step, to 200%, set as it turns, its menu's Speed slider moving with it; moved in the game, the Launchkey goes on from there) and the
    **pad below on the left** its Accelerator (green, bright while it's on): a little faster each time
    through.
  - **Bars:** while the loop shows (after knob 1 or 2), **knob 8** moves that end a bar at a time, to the
    bar lines (the chart's first beat of each bar).
  - **Lyrics**: pad 6 on top (magenta, bright while they show; following too) puts the song's lyrics on
    the screen instead of the song: the line before, the line being sung (the syllable sung lit up), and the
    line after, with the progress line and time above and the section and chord below. **Knob 6** and the
    pitch strip (while they show) go through the lines; Record then takes the song to a second before the
    line shown ("● = go here"), and after 4 seconds they're back with the song. **Track** ◁ (home) takes
    them back to the line being sung at once (and the page's list to the song); pressed again, it's the
    song's screen. Pad 6 again: the song too. Playing the song's knobs and pads work as ever while they
    show, and show there: knob 3, knob 4 and the section pads take the lyrics where the song's going (a
    handle on their progress line), knob 5 says the speed, and the loop's brackets are on the line (knobs 1
    and 2 saying where its ends are).
    They come from the song's vocals part (not every song has one).
- **The mixer**: **Func** (right of the pads, bottom; lit) opens it until Func again, and **knob 8** while no tone shows opens it for a
  few seconds (longer each time it's used, however it's used). Knobs 1–5 are the arrangements' volumes, knob 7 its keys', knob 8 all of them
  together, 1% a step, at once (the notes going on); following, **knob 6** is the game's Music volume (its mixer's: the
  song; a slider named Music, from where the game has it, 0 to 100%, through RS Re-ARMed's riffloop mod: the game's own
  mixer setting, as its mixer menu sets it, kept with your profile), the screen showing a slider for each with its volume
  over it; the pads on top mute each one or play it again, as outside the mixer (muted: red, its slider
  greyed with a crossed-out speaker), its keys' pad too. Each is the volume of the arrangement in that place
  in every song. The **pitch strip** moves the slider last used (a knob's, or a pad's on top; after knob 8 or
  Mute, all of them), a step a step as you slide; letting go doesn't move it. The pads below give lit
  keys as outside it, shown as rails either side of each slider, as high as it's lit (its keys': lighting
  first). Nothing's said for the pads (the sliders show them). Opened by Func, it stays until Func again or
  anything else is done (Mute leaves it open, the speaker in its corner; the pads below, Follow and
  Rocksmith's menu keys don't close it). Follow says what it did over the names for a few seconds, or
  until the mixer is used again. While it's open the pads are on the arrangements, even after ▼; they go back to playing the
  song as it closes.
- **The right column**: the top pad is Mute, as on the page (green: sound on; red: muted, just the lights;
  every screen shows a crossed-out speaker in its corner), the bottom one following the game (on or off).
- **Rocksmith's menus**, following (lit; with RS Re-ARMed's riffloop mod, which types the game's keys):
  **Track** ◁ and ▷ are its up and down arrows, the **upper arrow** right of the pads its Escape (back),
  and **Record** its Enter. Not following, Track ◁ is home and the others say to follow first. (Real pitch
  is on the page.)
- **Rocksmith's song list**, following, while Learn a Song's list shows: the **pitch strip** (slid or
  flicked) and **knob 8** (a letter at a time) go through the game's list, in its order, its highlight
  moving to the song, and the page's library going with it; however the game's highlight moves (its keys,
  Track ◁ ▷), the page's library and the Launchkey's screen show the song; a song clicked in the page's
  library is highlighted there too. Record picks it (Enter), and the
  song's yours once it starts, as ever. The riffloop mod reads the game's list and moves its highlight.
- **Play** plays and pauses the
  song (the screen's ▶ or ❚❚ says which), or plays the active one. Following, it pauses and resumes
  Rocksmith's song (through RS Re-ARMed's riffloop mod, which sees the game's screen): lit and ▶ while it
  plays, ❚❚ while it's paused, however it was paused (the computer's keyboard too).
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
  beside the key (where it is, and its length). The chord being played is in the bottom right corner (the
  scale making room), a moment early: the chart's name for it, else one made from its notes ("E5", "G/B"),
  held for its hand shape (strummed, or picked as an arpeggio) and until the next, with whose it is lit up
  before it (L, R or B: lead, rhythm or bass, numbered when a song has two of a kind, L1 and L2). It's the
  chord of the arrangement lighting the most keys (rhythm, lead, then bass, lighting as many), or, where that one has
  none, of the next that does: with the lead lit for a solo, the rhythm's chords show under it. Following,
  it keeps up with the game (and stays while it's paused). After a knob, what it's on takes the screen for a few
  seconds (the speed or section in the scale's place, the tone they're on). What a tap turned on or off is
  said at the bottom left of whatever the screen shows, for a few seconds ("Real pitch: on", "Lead: off",
  "Lead: 2 lit keys", "Loop 1:05.0-1:12.5: on", or why it can't be done). Muted, every screen shows a
  crossed-out speaker in its corner.

`rs-keylights launchkey` shows what the Launchkey sends as the remote, to check how it answers (quit the
page first: both would take it over).

As each song starts, its scale climbs the keys too, while the song doesn't need them (turn it off in
Follow Rocksmith). The Launchkey's keys go on playing as usual. Turn the remote off under Keyboard.

### Drums

A song can have a drum part, transcribed from its own audio, that plays along with its arrangements, playing or
following: on the Casio's drum channel (10, General MIDI drums), or on another device (**Other MIDI devices** ›
Drums: its port and channel, and its drums' notes, General MIDI's or an original Novation Circuit's 4 drums, on
notes 60, 62, 64 and 65 of its channel 10). On a Circuit, the Drums card says which of its drums plays each of the
part's (Drum 1 the kick, 2 the snare, 3 the hi-hats and ride, 4 the crash and toms, as it starts) and a sample of its
own there (1 to 64; empty: the sample that drum has): it's chosen with that drum's patch select (Control Change 8,
18, 44 or 50, its Programmer's Reference Guide 1.3) just before its hits, as it's needed, so the Circuit plays more
than 4 sounds; two of them on one of its drums at once, the kick, snare, crash, ride, hi-hats, then toms first. Under the song's parts, **Drums**
has **Make drum part** (a minute or so, in the background; then it plays), its volume and its mute; or
`rs-keylights drums SONG` makes one (`--again` makes it again; `rs-keylights drums --made` makes every part made
before again, as an update to how they're made needs: they show as not made until then).

The page's **Drums** card (under the keys) draws a drum kit: each drum lights as the part strikes it (brighter the
harder), playing or following, and a click strikes it where the drums play (the keyboard's drum channel, or their
device: its note for it, on hover), to hear it and tune its sound; under it, how many of each the part has.

The **Theory** card (under the drums) follows the song as it plays, from its charts: the chord now (the part with the
most chords') as a Roman numeral in the song's key, written against the major scale whatever the key's mode
(i–♭VI–♭VII, as rock's are, so songs compare), with what it does there (home, near home, away, pulls home) and
what's borrowed or outside the key (a major V from harmonic minor, ♭II's Phrygian sound...); the section's chords as
the phrases that repeat in it ("i – ♭VI – ♭VII ×4"), the one playing outlined and well-known progressions named
(the pop one, the Andalusian cadence...), and the scale its riffs use ("E minor pentatonic + ♭5"); and a strip of a
part's notes (the lead's, or another chosen) a second back and three ahead, each one green as a note of the chord
under it (R its root, ♭3, 5...), blue as one of the key's scale (from its tonic: 2, 4, ♭6...) or orange outside the
key. The key is the one found from the song's notes, or one nearly as likely when the chords say so (the chord it's
on longest, starts or ends on).

**The page's layout** (✎ in the header): each card gets a bar to move it (drag ⠿, or ▲ ▼) and remove it (✕), and
a bar over them adds cards: more than one **Fretboard**, **Tab** or **Keys** if you like (a tab of the bass beside
the lead's, say), each with its own part (Auto: the game's arrangement following, else the one playing; or one
picked, kept for every song that has it), its own bars a row or seconds ahead; Drums and Theory once, or not at all.
The settings column's cards move and go away the same way (back from the bar). **Reset layout** puts it as it was.
It's kept in the browser, so a phone keeps its own.

How it's made: the song's audio taken from its archive and decoded, its drums separated from the rest (the Demucs
model rearmed-mods' Song Stems runs on the Neural Engine), transcribed with [ADTOF](https://github.com/MZehren/ADTOF)
(its [PyTorch port](https://github.com/xavriley/ADTOF-pytorch): kick, snare, tom, hi-hat, cymbal), then refined: each
hit's velocity from how loud it is, the toms low, mid or high by their pitch, the cymbals crash or ride (a ride's hits
come in steady runs), and each hit kept where the record has it, but moved with the rest onto the chart: a chart's
beats can be tens of milliseconds off its audio (custom ones especially: Rime of the Ancient Mariner's are 48 ms
late), and the other parts play by the chart, so the whole drum part is moved by as much as most of its kicks and
snares are off the chart's 16ths (half a 16th, 80 ms, at most). Then one within 12 ms of a 16th is put on it. A part
made before this is moved the first time it's used. It plays on the song's clock, so it keeps time with the game (Riff Repeater and speed changes too). Hi-hats are all closed: open ones can't be told from the cymbals' wash. Parts are kept in
`~/Library/Caches/rs-keylights/drums/`.

The tools: `tools/drums-setup.sh` (once) builds Song Stems' helper with the drums as a part it takes out, from
rearmed-mods' source (`~/git/rearmed-mods`, or the folder given), and makes a Python with ADTOF's port, both in
`~/Library/Application Support/rs-keylights/drums/`. It needs the Xcode command line tools, uv, and rearmed-mods' Song
Stems installed (its model). rs-keylights itself needs numpy for it: `uv pip install --python .venv/bin/python -e ".[drums]"`.

### Hits and misses

The **Hits and misses** card shows what you hit and missed each time you played the song in Rocksmith (with the page
running), as you play:

- **Your accuracy:** the game's own count of hits and misses (Learn a Song's numbers).
- **Each section's** hits out of the notes counted in it.
- **The notes you missed**, by time and section: a chord's name and frets, or a note's name, string (1 the thinnest)
  and fret, and how it's played (bend, slide, palm mute…). With Riff Repeater's passes, how often of how many. Not
  following, a click goes there.
- **Earlier runs** in the menu at the top, newest first.

How: RS Re-ARMed's riffloop mod reads the game's note counts every frame (the note statistics, where RS Companion
finds them) and keeps each change with the song's time. The page asks for them twice a second and keeps each run in
`~/Library/Application Support/rs-keylights/plays/` (a file per run). Matched to the chart, each hit counted is the
note nearest its time, each miss the earliest note not counted yet, up to a second before it: the game counts a miss
once its note's gone by. The game scores the notes at your level, which can be fewer than the chart's hardest; a note
it doesn't count isn't listed either way.

### Rocksmith's tones on a DNAfx

`rs-keylights dnafx-tone SONG` makes presets for a Harley Benton DNAfx GiT Core from a song's tones, for the tones
whose amps the DNAfx has: Rocksmith's Marshall JCM800, Plexi and Orange Rockerverb are its J800, PLX100 and ROCK VRB,
and HG100 ("a custom high-gain amp") its PV 5050, the nearest; Marshall 1960 and Orange cabinets its 1960 412 and
CITRUS, with EL34s (6L6s for the 5150) and the same mic. The amp's knobs are Rocksmith's (the JCM800's gain made as
distorted by ear: 65 is the DNAfx's 100); reverbs, delays, modulation and a 5-band EQ the DNAfx's nearest; a noise
gate (NOISE KILLER, 16) from a gain of 80; the expression pedal on the amp's gain. Drives, wahs and studio EQs are
left out, and said. Each tone a `.phb` for the DNAfx editor (8 letters, as it takes them), and with `--to-pedal`
written to the pedal too, from slot 194 on.

**As a song loads**, `song-tones` (the sidekick) writes its arrangement's tones that way to slots 194 to 199, before
it sends the song's tone message, which then says each tone's slot: dnafx-preset's listener switches to them as the
song plays. A preset that's there already isn't written again (`~/Library/Application Support/rs-keylights/
pedal-song-presets.json` remembers them); the slots' presets before the first write are saved in
`~/Music/DNAfx backups`. No other slot is written. `song-tones --no-pedal` turns it off.

### PC songs in the Mac game

`rs-keylights pc-songs` makes the PC's song archives (`*_p.psarc`) Mac ones, for the songs that have no Mac archive:
they show in RS Re-ARMed's song list and on the page like any other. It looks in the Steam game's `dlc` folder that
RS Re-ARMed's copy is made from, and in the game's own `dlc` (`--from DIR` for others), and puts what it makes in the
game's `dlc/from-pc/`, which RS Re-ARMed's updates leave alone.

- **No duplicates.** A PC archive is skipped when any other archive has its song: the same arrangements (their
  persistent IDs), or the same artist and title. Of two PC archives of one song, the newer is used. One made before
  is removed when its PC archive's gone, or a Mac archive of its song turns up.
- **What changes.** A song's PC and Mac archives hold the same files, the audio byte for byte: only the folders'
  names (`songs/bin/generic` and `audio/windows`, against `songs/bin/macos` and `audio/mac`), the `.nt` graph that
  names them and the platform (`dx9`, against `macos`), and the charts, encrypted with a key for each platform. So the
  folders are renamed, the graph changed, and each chart encrypted again with the Mac's key (and checked: decrypted,
  the same as the PC's); the rest is copied block for block. Its AppID is kept if the game lists it (Rocksmith's own,
  or one RS Re-ARMed's copy has), else it gets the free one custom songs carry (248750), or the game wouldn't load it.
- **Quick.** A run reads only the archives that are new or changed since the last (`dlc/from-pc/.from-pc.json`
  remembers the rest): a few seconds. `--dry-run` says what it would make and remove.

To have it run as the game starts, add it to RS Re-ARMed's `sidekicks.conf` (rearmed-sidekicks): new PC songs show
the next time the game starts.

### The song list's scroll bar

In Learn a Song's song list, the scroll bar's thumb can be dragged only from the top of the list: once it's further
down, a click on it does nothing. The list's menu (a Scaleform movie, `las_songslist.gfx`, in `cache.psarc`) tells the
game where it can be clicked: its buttons, each row of songs, and the thumb, where it's drawn, each with its depth, and
the game takes the one drawn on top of those the mouse is in. The rows reach over the scroll bar, and those in the
list's lower panel are drawn above the thumb, so it gets no click in the lower half of the screen. And the game asks
only now and then, not as the list scrolls (the rows stay where they are), so the thumb can be clicked only where it
was then. `rs-keylights song-list-fix` makes that part the scroll bar's whole track, which doesn't move, above
everything else (a few bytes of the menu's ActionScript, the same length: nothing else in it moves). A click anywhere
on the bar grabs the thumb, and the list follows the mouse from there.

`cache.psarc` isn't changed (RS Re-ARMed's Metal renderer loads more slowly with a changed one): the fixed archive
(`cache8.7z`) goes into RS Re-ARMed's menu overlay, `Update/patcharchives.psarc`, which the game reads first, next to
RS Re-ARMed's own archives there. RS Re-ARMed makes its overlay again when it copies or refreshes the game files, so
running the command from `sidekicks.conf` keeps the fix (quick when it's there; the first start after a refresh is
without it). `--undo` gives the list back as the game has it. Needs RS Re-ARMed's overlay hook (on unless
`RS_MODS_OVERLAY=0`).

### A song in a DAW

**MIDI file** under the song's title saves it as a MIDI file for Logic or GarageBand (`Artist - Title.mid`), or
`rs-keylights midi SONG` (`-o` a file or folder; `--audio` the WAV beside it):

- **Its tempo map is the record's.** Each of the chart's beats gets its own tempo, and its time signatures come from
  where the bars start, so the bars line up with the band as it speeds up and slows down. The time before the chart's
  first beat is made into a bar or so of its own, so the file starts when the song does.
- **A track for each part**, at its hardest difficulty, at the pitch it sounds (a song for a pedal tuned down sounds
  down), played as the keyboard plays it (the same player, recorded): notes ringing as a guitar's strings do,
  chords strummed, palm mutes a chug long, accents harder, dead notes a quiet tick, slides a semitone at a time,
  slides off fading, tremolo picking. Bends are pitch bends; vibrato is the mod wheel. Pinch harmonics squeal on a
  channel of their own in the part's track (GM GT HARMONICS, scooped and with their vibrato as pitch bends), when
  one's free. The bend range is sent as RPN 0: 2 semitones, unless a part bends further. Then the track's name says
  it, "Lead (bend range 3)", for an instrument that ignores RPN and needs its own setting changed. Its tones
  ("Tone: …") and chord names are text events.
- **The sections** are markers (Intro 1, Verse 1…). **The key** is a key signature: a mode's is its major's.
- **The vocals:** their lyrics as lyric events, and their melody as notes when the chart has one (about half of
  them; the rest give every syllable a pitch or two as placeholders).
- **The drum part**, if it's made, on channel 10 (General MIDI drums).

**Song audio** saves the song's audio as a WAV (a few seconds: the drum tools decode it, so it needs
`tools/drums-setup.sh` once). It starts when the MIDI file does: put both in at the start of a Logic project and
they line up.

### Guitar pedal

A Harley Benton DNAfx GiT (the Core too) plugged into the Mac by USB has its presets switched by the
Launchkey's knob 6 and the pad above it (above) or the page's **Guitar pedal** card (‹ ›, a list of all 200,
Back to the one before, and the favourites the pad goes through). It needs
[dnafx-preset](https://github.com/manbearpig3130/dnafx-preset), which talks to the pedal over its HID port
(no sudo), installed beside rs-keylights:

```bash
uv pip install --python .venv/bin/python -e ~/git/dnafx-preset
```

The presets' names come from a backup the DNAfx editor saved (a `.bhb` file): the newest in Music,
Documents, Desktop or Downloads, or the one named on the card. A preset renamed on the pedal since keeps
its old name here until the next backup. The pedal can't be asked which preset it's on, so rs-keylights
goes by the last it switched to, or the last dnafx-preset's own listener switched to (RS Re-ARMed's
sidekick, following the song's tones: its log says), whichever was later; a footswitch on the
pedal itself isn't known. Both switching it is fine: each opens the pedal just to send. Each switch runs
dnafx-preset in a process of its own (`python -m dnafx_preset set N`), given 3 seconds: hidapi holds up
every thread of the process it's in while it talks to the pedal, and on macOS it's only safe from one
thread, so in rs-keylights itself a pedal slow to answer could freeze the Launchkey and the page.

### Song tones, without RS Companion

`rs-keylights song-tones` does for a pedal what RS Companion's song tone messages do, from the game
itself (its riffloop mod says which arrangement plays, and where): when an arrangement loads, one SysEx
describes its tones (each one's category, Clean, Crunch, Heavy or Lead, and its delay and modulation, and
the song's style), then each tone plays on its own Program Change (8 for the first, 9 for the next...),
100 ms before the game switches to it. They go out on a MIDI source of its own, **Rocksmith Tones**, so
dnafx-preset's listener follows it just as it follows RS Companion:

```bash
~/git/dnafx-preset/.venv/bin/dnafx-preset listen --midi-source "Rocksmith Tones" --config ~/.config/dnafx-preset/tones.toml
```

The categories and styles are RS Companion's own rules (its rig.py and presets.py, ported in `rig.py`),
with its `presets.json` in `~/Library/Application Support/rs-mac-companion` (or `$RS_COMPANION_DATA`):
your rules, overrides, artist sections and AI answers, read again when they change. So are its settings
there (`rscompanion.json`: the MIDI channel, the first program, the lead time, or a category's program
instead of each tone's). Checked against RS Companion's log, its 31 song tone messages, each one the same.
Without RS Companion's files every tone is Clean, its default. A song's tones take a few milliseconds to
read (each part's tone changes, read without its notes). Run it as an RS Re-ARMed sidekick (see
rearmed-sidekicks), beside dnafx-preset's listener.

### Pedal tones, changed on the page

The **Pedal tones** card shows the song's tones (an arrangement's: the one the game plays, following it, or
the one picked), the one playing lit, each with how long it plays and its effects, its category, and the
pedal's preset for it with the rule that picks it. Each can be Auto (the rules for every song) or the
song's own: a category of its own goes into RS Companion's `presets.json` (as its app's overrides do, for
each of the song's parts that has the tone), a preset of its own into dnafx-preset's tone map as a song
rule. **Try** switches the pedal to a tone's preset; **Keep** gives the tone the preset the pedal's on. The
song's style can be its own too. The **Pedal tone rules** card (in the settings column) has the rules for
every song: each category's preset, artists', effects' and styles' presets, the songs' own, and, folded,
RS Companion's artist categories, song and artist styles, and styles by heaviness.

The files are the ones RS Re-ARMed's `sidekicks.conf` gives dnafx-preset's listener (its `--config`, and
its `--midi-program-map` for a category without a preset), and the presets are picked by dnafx-preset's own
code, so the page shows what the pedal does. A change keeps the rest of the file as it was (comments too;
the line changed gets the preset's name as its comment, and the file before is kept as `.bak`).
`song-tones` reads `presets.json` again within a second, and dnafx-preset's listener its tone map, so the
pedal switches to the tone's new preset at once, if it's playing.

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
part's notes sound on channel 11 (the main tone), 12 (pinch harmonics' squeals) and 13 (palm mutes), the second
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

**With the riffloop mod, the game itself.** When RS Re-ARMed has rearmed-sidekicks' riffloop mod (with its
`time` command), following asks the game itself, 50 times a second, where the song is and which arrangement is
playing: its playback's own time, and its own copy of the arrangement (named as its `.sng` file is, e.g.
`amonamarthwayofvikings_lead`), answered over a local socket in under a tenth of a millisecond. The keys and the parts
playing along go by that (within a frame of the game's), with Riff Repeater's speed measured from it, the pause
screen going dark at once, and its rewind dark while it goes back. The song starts as soon as the mod finds the
song's playback (near where the last one was, then everywhere), and stops as you leave it. RS Companion isn't read at
all then. Without the mod (or a mod that doesn't say the arrangement), RS Companion says which song and arrangement,
and its timer as above, read on a thread of its own, so its answers (about 100 KB, album art and all, now and then
tens of milliseconds) never hold up the notes. A trace (`--trace`) says which it played by ("from", and "game"
for the arrangement the game said).

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

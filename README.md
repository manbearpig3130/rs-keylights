# rs-keylights

Rocksmith 2014 songs on a light-up keyboard and the gear around it. rs-keylights lights a Casio's keys with a
song's notes and plays its parts on the Casio or other MIDI devices. It can do this along with the game, or on its
own from the song files. Around that it adds:

- a page (in the browser, or a window of its own) with the song's fretboard, tab, chords, theory and lyrics;
- a Novation Launchkey as a remote for rs-keylights and for Rocksmith itself;
- drum parts transcribed from the song's audio, for the Casio or a Novation Circuit;
- the song's own audio, split into parts you can turn up and down;
- a DNAfx guitar pedal that switches to each song's tones;
- MIDI export, a hits-and-misses record, and fixes for the Mac game.

It's made for the Casio LK-170 on macOS, with [RS Re-ARMed](https://github.com/bbelgg/rs-rearmed) (the native
Apple Silicon game) or Rocksmith 2014 Remastered. Other Casio keyboards with key lights and "navigate channels"
(LK-175, LK-260 and similar) probably work too.

## Contents

- [Setup](#setup)
- [The page](#the-page)
- [Warm-up](#warm-up)
- [Following Rocksmith](#following-rocksmith)
- [The Launchkey](#the-launchkey)
- [Drums](#drums)
- [The song's own audio](#the-songs-own-audio)
- [Hits and misses](#hits-and-misses)
- [Guitar pedal (DNAfx)](#guitar-pedal-dnafx)
- [A song in a DAW](#a-song-in-a-daw)
- [Fixes for the Mac game](#fixes-for-the-mac-game)
- [Commands](#commands)
- [How it works](#how-it-works)
- [The keyboard's quirks](#the-keyboards-quirks)
- [Development](#development)

## Setup

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
```

Some features need extras:

| Extra | For |
|---|---|
| `listen` | hearing the guitar, the song's own audio, `sample-drums` |
| `drums` | drum parts, and splitting songs into parts |

Install both with `uv pip install --python .venv/bin/python -e ".[drums,listen]"`. Drum parts and splitting also
need `tools/drums-setup.sh` run once (see [Drums](#drums)).

Connect the keyboard with a USB cable. It shows up as the MIDI port `CASIO USB-MIDI`.

The first run indexes the game's songs (about 30 s for 2,000 archives on a USB drive). Later runs only read new or
changed archives. The game folder is RS Re-ARMed's, unless you give `--game-dir` or set `RS_KEYLIGHTS_GAME_DIR`.

Then start the page:

```sh
.venv/bin/rs-keylights gui
```

Or run `tools/dock-app.sh` once to make `~/Applications/rs-keylights.app` and put it in the Dock after RS Re-ARMed
(it takes Xcode's Command Line Tools, `xcode-select --install`). Clicking it starts the server (with `--lan`; give
other arguments to the script to change that) and shows the page in a window of its own, apart from the browser
(WebKit, as Safari has it). Closing the window leaves the server running, so the Launchkey and the game's menu go on
working, and clicking the app shows it again; quitting the app gives the Launchkey back. Exports save to Downloads,
and the View menu zooms the page (⌘+, ⌘-, ⌘0). Its output goes to `~/Library/Logs/rs-keylights.log`. macOS asks once
for the microphone, which the Guitar card and warm-ups use.

## The page

`rs-keylights gui` serves a page at `http://127.0.0.1:8040/` and opens it in the browser. Everything below is on
it, unless it says otherwise.

### Layout

- **The header**: the song's time slider and speed (25% to 200%), ▶/❚❚, ■, Mute 🔊 (just the lights, no
  sound), 🎵 the song's own audio, **Follow Rocksmith**, ⚙ to fold the settings column away, and full screen ⛶.
- **The middle**: the song and its cards.
- **The settings column**: setup cards. Each folds into its title, and the setup ones start folded.
- **✎ in the header** edits the layout. Move cards (drag ⠿, or ▲ ▼), remove them (✕), or add them from the bar
  above. You can have several **Fretboard**, **Tab** and **Keys** cards, each showing its own part (for example a
  bass tab beside the lead's). **Reset layout** puts it back. The layout is kept per browser, so a phone keeps its
  own.

The **Library** lists every song in the game folder, searchable by song, artist, album or file name. Clicking a
song shows it without changing what plays. Its **⏵** makes it the *active* song: the one Play plays and the
Launchkey shows.

### The song's cards

- **Song**: its art, title, artist and key, and a block for each part:
  - lit keys, on or off: the lit parts share the keyboard's 4 lit keys a moment at a time, as many different keys as
    they play, the part you play in the game first, then the song's order;
  - volume (0 to 100%, 100% at first; it's the volume of the part in that place, first, second…, in every song);
  - mute 🔇;
  - its tones under **Tones**: the main tone, a palm-mute tone and a harmonics tone.

  Until you change them, every part plays and lights a key. Lead, rhythm and bass light 2, 1 and 1. A song with
  more than 4 parts starts with its first lead, rhythm and bass.
- **Key**: worked out from all the parts' notes. You get the key and scale, notes outside the scale that it uses a
  lot, and a key nearly as likely. For a guitar tuned down, it also says the key your fingers play ("C# minor,
  played as F# minor in B standard"). It names a mode when one fits clearly better (E Phrygian). On well-known
  songs it finds the usual key 14 times in 16. Riffs that sit on another note than home (Iron Man) fool it.
  **Show on the keys** climbs the scale up the keyboard and back until you stop it or the song needs the keys.
- **Fretboard**: the key's scale on the song's guitar, in its tuning, to the 24th fret, with the root notes red. A
  capo is drawn on its fret. While the song plays, the part's notes light where the chart plays them, and the next
  0.6 s of notes are ringed. A chord's shape shows while it's held, each string marked by its place in the chord
  (R, b3, 3, 5…), and the chord's name shows beside the title. **Thick string on top** flips it.
- **Tab**: two rows of bars, the one the song's in and the next, turning like a page. Notes light as they're
  played, with chord names above and techniques as tab writes them (h, p, t, / \, b, ~, <7>, x; palm mutes in
  italics).
- **Keys**: the notes falling onto a drawing of the Casio's 61 keys, 2 to 8 seconds ahead. It shows every part
  playing, or one part. Its keyboard plays: click a key (or drag across them) and it sounds as the Launchkey's keys
  do, where they play (Plays on, in Launchkey keys). Keys held down show red, the Launchkey's too.
- **Drums**: a drum kit that lights as the drum part strikes it. Click a drum to hear it (it sounds as you press, and
  fast clicks don't select the page's text). See [Drums](#drums).
- **Song's audio**: the song's own audio and its parts. See [The song's own audio](#the-songs-own-audio).
- **Theory**: follows the song from its charts:
  - the chord now as a Roman numeral (written against the major scale whatever the mode, so i–♭VI–♭VII, as rock
    songs are usually written), with what it does there (home, away, pulls home) and anything borrowed;
  - the section's chords as repeating phrases ("i – ♭VI – ♭VII ×4"), with well-known progressions named;
  - the scale its riffs use ("E minor pentatonic + ♭5");
  - a strip of a part's notes, coloured by whether they're in the chord, in the key, or outside it.
- **Lyrics**: the song's lines as they're sung, under their sections. Click a line to go there. Only songs with a
  vocals part have lyrics.
- **Hits and misses**: see [Hits and misses](#hits-and-misses).
- **Pedal tones**: see [Guitar pedal (DNAfx)](#guitar-pedal-dnafx).

### How the parts sound

**Notes ring** as a guitar's strings do:

- A note sounds until its string is played again.
- A chord or arpeggio rings until its hand shape ends, or until the fretting hand moves.
- In a shape with palm mutes in it (a riff, as in Guardians of Asgaard's lead), single notes don't ring on.
- An open string drones under the notes after it, until a chord or a palm mute stops it.
- Each note overlaps the next a little (120 ms at first).
- A note rings for 4 seconds at most, unless the chart holds it longer.
- Palm mutes stay short: a chug of 0.6 of the time to the next note (50 to 200 ms).

**Techniques** are played as the charts have them:

| Technique | How it sounds |
|---|---|
| Bends | pitch bend, following the chart's bend curve, to its full size, on a channel of its own (below) |
| Slides, slides off | a semitone at a time over the note (slides off fade out) |
| Vibrato | modulation, on a channel of its own |
| Tremolo picking | struck again every 75 ms |
| Hammer-ons, pull-offs, taps | softer, legato |
| Slaps, pops, accents | harder |
| Natural harmonics | at their own pitch (a 7th-fret harmonic sounds an octave and a fifth up), on the harmonics tone |
| Pinch harmonics | the note, softer, under a squeal an octave and a fifth up: struck slightly flat, scooped up to pitch, with a fast, wide vibrato (the squeal on a channel of its own) |
| Dead notes | a quiet click on the palm-mute tone (or a soft tick without one), stopping what rings on that string |
| Palm mutes | a little softer, short |
| Strums | a chord's strings one after another: down on beats and eighths, up (softer) on sixteenths, 7 ms a string |

**A channel of its own**: a pitch bend or vibrato moves every note on its channel. So a note that bends or wobbles
borrows one of the keyboard's spare channels (those no part uses) while something else of its part sounds meanwhile,
made like its part's: the same tone, volume and bend range, its volume following the part's fader. The notes struck
with it go along and bend with it, as a bent chord's strings. The rest ring on in tune. When no spare channel is free,
it stays on the part's channel: it stops as the part's next note sounds, and the part's notes still ringing stop as it
starts to bend.

The **Pinch harmonics** card sets the squeal's pitch, loudness, scoop and vibrato, on every pinch harmonic or only
where the chart has vibrato. **Try it** plays a riff with a quick and a held one.

The **Keyboard** card turns ringing and techniques off.

**Tone changes**: when a song switches tone, the part switches to the keyboard tone for that kind of tone (clean,
drive, distortion, lead, acoustic, bass…), chosen on the **Tone changes** card. It starts on the Casio's General MIDI
guitar tones (294 GM CLEAN GUITAR, 296 GM OVERDRIVE GT, 297 GM DISTORTION GT…), because the LK-170's own guitar
tones sound an octave below the keys. Kinds it doesn't know, or set to "No change", keep the tone. The Launchkey says
each change ("Lead: distortion"). Each part lists its song's tones under its own, with the current one lit.

**Default tones**: a set each for lead, rhythm and bass parts, for parts without tones of their own.

**Channels**: each part takes the keyboard channels its tones need: one, plus one each for a palm-mute or harmonics
tone of its own (the harmonics one only if the part has harmonics). The keyboard has 12 to give, so five or more parts
fit. Short of channels, harmonics squeal on the part's own tone. A part on another MIDI device takes none.

### Settings cards

- **Keyboard**: the MIDI output, the lights' channel, **Lights early** (15 ms) and **Fastest flash** (70 ms); notes
  ringing and their overlap; **Real pitch**; techniques; the Launchkey remote; and **Look for new songs**.
  - **Real pitch** (on at first): the notes sound at their own pitch, not the lit keys' pitch (moved into the
    keys' range). The keyboard plays MIDI notes below its lowest key, but some tones don't reach as low as a B
    standard guitar.
- **Follow Rocksmith**: an offset, whether each song's key climbs the keys as it starts, and full screen while
  following.
- **Guitar** (needs the `listen` extra): the notes you play, heard on the Rocksmith USB Guitar Adapter, light their
  keys first. It hears a note 30 to 60 ms after you play it, down to 30 Hz (B standard with a drop pedal an octave
  down). During a song it checks each chord's expected notes; without one, it works chords out from their
  harmonics.
- **Other MIDI devices**: a lead, rhythm, bass or drum part can play on another MIDI device (a Korg monologue, a
  Novation Circuit, an app on the Mac through the IAC Driver). The Casio still lights the keys. You choose:
  - the output and channel;
  - whether it plays one note at a time (for a monophonic synth; on by default);
  - its program, and octaves up or down;
  - how many programs it has (128 if left empty; a Korg monologue has 100): the Launchkey's knob goes round them,
    past the last to the first and back.

  Notes, bends, vibrato, the program and volume go to the device, but tones don't. If it isn't connected, the Casio
  plays the part.
- **Launchkey keys**: the Launchkey's own keys play the Casio on channel 1 with a tone of their own. **Plays on**
  sends them to another MIDI output instead (IAC Driver Bus 1 for an app on the Mac, such as Komplete Kontrol). They
  still light the Casio's keys.
- **Tone changes**, **Default tones**, **Pinch harmonics**: above. **Guitar pedal**, **Pedal tone rules**: see
  [Guitar pedal (DNAfx)](#guitar-pedal-dnafx).

Changes apply at once, from where the song is, while it plays or is followed.

Settings are kept on the Mac in `~/Library/Application Support/rs-keylights/`: each song's parts, lit keys, tones
and start in `songs.json`, the rest in `gui.json`. Every page and the Launchkey show the same thing. A song is kept
by its file's place in the game folder (`dlc/x_m.psarc`), so its settings survive the game folder moving.

### On a phone

The game pauses when it loses focus, so changing things on the Mac stops the song. With `rs-keylights gui --lan` the
page also answers on the network, and a phone on the same Wi-Fi can be the remote. It prints a link with a key (the
Keyboard card shows it too). Open it once on the phone, and after that the plain address
(`http://NAME.local:8040/`) works, on the home screen too. On the network the server only answers browsers with the
key. If the Mac's firewall is on, allow Python's incoming connections when macOS asks.

Without `--lan` the server answers only on 127.0.0.1, to pages it served. `--port` changes its port and
`--no-browser` doesn't open the page.

## Warm-up

The **Warm-up** card runs six short drills with a click on the Casio: about 8 minutes at the slowest, less as you
speed up. Play them with nothing between the guitar and the amp.

| Drill | Works on |
|---|---|
| Chromatic 1-2-3-4 | alternate picking, both hands in time; a fret higher each pass |
| Spider (1-3, 2-4) | finger independence and stretch |
| String skipping (octaves) | picking the right string every time |
| Legato trills | hammer-ons and pull-offs |
| Pentatonic | the scale of the song shown (as its fingers play it, if it's tuned down), else A minor |
| Gallops and tremolo picking | picking-hand stamina and palm muting |

Each drill goes like this:

1. A few seconds to read it.
2. The Casio shows it: it plays the drill's first notes on a clean guitar, at your speed, with a click, while the tab
   and neck light up. **Your turn** skips the rest, and **Show each drill first** turns the showing off.
3. A count-in of 4 clicks, then you play it for about 30 seconds, finishing the pass you're on (the chromatic: up the
   strings and back down).

The card shows:

- the drill's tab, with the note being played lit;
- the neck, with each note's finger (the scale's root ringed red);
- a bar showing how far the drill has gone.

The Casio lights each note's key as it comes, and the first note's key while you read the drill and count in.

### What to practise

Before you start, the card's choices:

- **Practise:** the whole warm-up, or one drill on its own. A drill on its own goes round until you stop it.
- **Tuning:** every standard tuning from E down to A, and the drop tunings from Drop D down to Drop A. The tab, the
  neck and what it listens for follow it. The default is E standard.
- **Scale**, **Key** and **Where:** the scale drill's. The defaults play the pentatonic of the song shown (else A
  minor). Choose from major, natural minor, harmonic minor, minor and major pentatonic, blues, Dorian, Phrygian,
  Lydian and Mixolydian, in any key. Auto starts from the root on the lowest string (open position when that's an
  open string); or pick a fret to start from.

The choices are kept for next time.

### Speed

Speed is counted in notes a minute. Every drill starts at the slowest, 40 (one note every 1.5 seconds), with a click on
each note. **−** and **+** change it by 4 clicks a minute. Past 120 clicks a minute it changes to 2 notes a click and
then 4, at half the click, so the click stays easy to follow. Each drill keeps the speed you leave it at. ◀ starts
the drill again (just after it starts, it goes back to the one before), ▶ skips it, and ❚❚ pauses.

### Your playing, heard

rs-keylights listens to the guitar through the Rocksmith cable (the input chosen in the Guitar card) and judges each
note you play. It needs the `listen` extra. On the tab, a note turns:

- **green:** the right note, in time;
- **amber:** the right note, early or late;
- **red:** a wrong note, or one it didn't hear.

"In time" is measured from your own usual timing. A low note under another string still ringing can't be told from
its neighbours, so a note heard in time with no clear pitch counts as right.

Notes are judged in rounds of 8 clicks. The line under the drill says how the last round went: how many notes were
right and in time, and how far after (or before) the click you play. With **Speed up as I get it right** on:

- a clean round (9 notes in 10 right and in time) goes a step faster;
- two sloppy rounds in a row (fewer than 6 in 10) go a step slower;
- a round where it heard nothing doesn't count.

The change starts at the next group of clicks, and each drill keeps its speed. At the end, the card shows each drill's
speed and how many of its notes were right and in time.

### Progress

Each drill you play is logged in `~/Library/Application Support/rs-keylights/warmups.jsonl`: its speed as it started
and as you left it, its notes right and in time, and how long you played. Before a warm-up and after one, the card
shows:

- how many days in a row you've warmed up, and your minutes today;
- a tile for each drill played in the last 30 days: its speed now, how far it's come since your first day (▲), and a
  line of its speed each day. Point at a day for its speed and how much was right. A scale has a tile for each
  scale and tuning.

### On the Launchkey

While a warm-up runs, it takes over the Launchkey:

| Control | Does |
|---|---|
| **Play** | pause and resume |
| **Record** | Your turn, while a drill is being shown |
| **Track ◀ ▶** | back to the drill's start (just after it starts: the drill before), and on to the next |
| Any knob | speed, a step a click |
| Pads on top | the drills: the one going pulses, those done are dim green; tap one to go to it |
| Pads below | your last 7 notes: green right and in time, amber early or late, red wrong or missed |
| Right column | top: the speed ladder on or off (green: on); bottom (red): stop |

The screen shows the drill, its speed, how far it's gone, and what's happening: reading it, being shown it, the
count-in, or how your last round went.

Starting the warm-up stops a song playing or following, and playing a song stops the warm-up.

## Following Rocksmith

**Follow Rocksmith** (in the header) plays along with the game:

- The song you play in the game shows in the middle, with each of its arrangements.
- The arrangement you play lights the keys and sounds (unless muted).
- Unmute the others to have a backing band. Your choices are kept for each song and arrangement.
- Muting an arrangement (or the drum part) while it plays takes effect at once, here or playing a song: its notes
  stop and its keys go out, and nothing else is cut off or struck again. It stays in what plays, muted, so unmuting
  it is just as quick: its notes still ringing are struck again (only its own), in its tone.
- The speed, loops and position are the game's.

It knows where the game is in one of two ways:

- **With RS Re-ARMed's riffloop mod** ([rearmed-sidekicks](https://github.com/manbearpig3130/rearmed-sidekicks)), it
  asks the game itself, 50 times a second, where the song is and which arrangement is playing. This is the best
  way, and the mod also lets the Launchkey drive the game (see [The Launchkey](#the-launchkey)).
- **Without the mod**, it reads [RS Companion](https://github.com/glebb/rs-mac-companion)'s stream overlay. Turn on
  **Stream overlays** in RS Companion's Settings.

The page warns if another `rs-keylights follow` (such as an RS Re-ARMed sidekick) is running, since both would light
the keys.

## The Launchkey

With a Novation Launchkey MK4 (Mini or full size) plugged in, the page takes over its DAW controls while it runs and
gives them back when it stops. It works on the song followed, else the song playing, else the active song.
`rs-keylights launchkey` shows what the Launchkey sends, to check it (quit the page first). The **Keyboard** card
turns the remote off.

The buttons beside the knobs switch what the knobs and pads do: **▲** the arrangements, **▼** playing the song.
While a warm-up runs, it has the Launchkey instead (see [On the Launchkey](#on-the-launchkey)).

### The arrangements (▲)

| Control | Does |
|---|---|
| Pads 1–5, top | an arrangement each (orange lead, green rhythm, blue bass; dim when muted; the one you play in the game pulses; dimmed while Mute has the sound off). Tap to mute or unmute. |
| Drum part's pad, top | after the arrangements, white. Tap to mute. Not made yet: dim, and a tap makes it. |
| Pads 1–5, bottom | each arrangement's lit keys, on or off. While its tones show on the screen: whether its tone follows the song's tone changes (bright), or keeps the one chosen (dim), as the page's Follow box; off for an arrangement without tone changes |
| Drum part's bottom pad | **beat lights**: it flashes on each beat (brighter on a bar's first) and the other lit pads pulse with it |
| Knobs 1–5 | each arrangement's tone. The drum part's knob goes through the Casio's drum kits. |
| ▲ again | switches the knobs between tones, palm-mute tones and harmonics tones (squares at the top right of the screen show which) |
| Knob 8, while a tone shows | a group of tones at a time (piano, organ, guitar…) |

Palm-mute and harmonics tones start with "Same tone" and, where their kind has one, "Default", so knob 8 gets back
to them in a step. For a part on another MIDI device, its knob steps through the device's programs instead (knob 8:
ten at a time), round from the last to the first (as many as Other MIDI devices says it has: 128 unless told).

### The guitar and the keys (columns 6 and 7)

| Control | Does |
|---|---|
| Knob 6 | the guitar pedal's presets; it switches once the knob rests (knob 8 while it shows: ten at a time) |
| Pad 6, top | the next of your favourite presets (set on the **Guitar pedal** card) |
| Pad 6, bottom | hold the pedal on its tone, or let it follow the song's tones again |
| Record, while the pedal shows (following) | keep the preset shown for the song tone playing, for this song |
| Pad 7, top | the Launchkey's keys on or off |
| Pad 7, bottom | whether the keys you play light first (the song's lights get the room left) |
| Knob 7 | the Launchkey keys' tone (knob 8 while it shows: its group) |

The Launchkey's octave buttons, a sustain pedal, the modulation strip and its own Scale mode work as usual.

### Playing the song (▼)

| Control | Does |
|---|---|
| Knob 1, 2 | move the A-B loop's start and end, a beat at a time (knob 8 while it shows: a bar at a time) |
| Knob 3 | move through the song (stopped: where it starts) |
| Knob 4 | a section at a time |
| Knob 5 | speed, 1% a step, 25% to 200% |
| Pad 1, top | the loop: its start (**A**) where the song is, then its end (**B**), looping; a third press clears it |
| Pad 3, top | loop on or off |
| Pad 4, top | loop the section it's in |
| Pad 6, top | lyrics on the screen (knob 6 and the pitch strip go through the lines; Record goes to the one shown) |
| Pads 1–7, bottom | a kind of section each (verse blue, chorus orange, solo red…). Tap to go to the next of that kind. |

Loop points land on the nearest beat. Reaching the end, the song goes back to a second before the start.

**Following**, these work on the game instead:

- Knob 5 is Riff Repeater's speed (up to 200%).
- The bottom-left pad is its Accelerator.
- Paused, knob 3 steps through the song as the pause screen's ← → do, and knob 4 a phrase at a time.
- The A-B loop becomes a phrase of its own in Riff Repeater (riffloop mod). Its end set with pad 1, Riff Repeater
  opens with it picked: press Start, and it loops just that range. (Set another way, pick it in Riff Repeater,
  between the usual ones.)

### The mixer

**Func** opens the mixer until Func again. Knob 8, while no tone shows, opens it for a few seconds.

| Control | Does |
|---|---|
| Knobs 1–6 | the arrangements' volumes (the volume of the part in that place, in every song: knob 6 for a song with a 6th; outside the mixer it's the guitar's) |
| Knob 6, following, five arrangements or fewer | nothing: the game's Music volume (its own mixer setting) is knob 7 on the second page |
| Knob 7 | the Launchkey keys' volume |
| Knob 8 | all of them together |
| Pads, top | mute each (red), as outside the mixer; the right one is Mute, the keyboard's sound off (all of them greyed, their pads dimmed, their volumes kept) |
| Pitch strip | moves the slider last used |

The screen shows a slider for each. The pads below still give lit keys, shown as rails beside the sliders.

**The second page.** While Func keeps the mixer up, **▼** turns it to the song's parts and **▲** back. What it
controls depends on whether you're following:

| | Playing a song | Following |
|---|---|---|
| What it mixes | the song's own audio from the Mac (🎵) | a song split here: its parts, in the game's own song (riffloop mod), as when playing it. Otherwise the game's own song's bass and guitar (rearmed-mods' Song Stems mod; without it, just its Music) |
| Knobs | 1–6: drums, bass, guitar, vocals, keys, other | a split song: 1–6 as when playing it. Otherwise 2: bass, 3: guitar |
| Pads, top | mute each part, its volume kept (greyed, its pad dimmed; a song not split yet: a tap splits it) | mute the bass or guitar, its level kept (greyed, dimmed), and back to it |
| Knob 7, and its pad on top | the Music: the song audio's volume, and its audio on or off | the game's Music volume, and muted or back to its level |
| Knob 8 | the song's parts together (the Music apart) | the bass and guitar together (the Music apart) |
| Right pad, top | the page's master mute: all the parts muted, or back (the Music apart; not the keyboard's sound, while this page shows) | the same, for the bass and guitar |

A thin line on the screen sets the Music apart from the parts.

While a song is being split (from a pad here or from the page), the bottom of the screen shows how far it is, a
small bar with its percent, on the song's screen and the stems page alike, until it's done; then its drum part being
made, if a BS-RoFormer SW split makes it again. Anything said meanwhile shows over it for a few seconds as ever.

**While following a song split here** (either separator), its parts play in the game's own song: the riffloop mod
mixes them into it as it plays, at this page's levels, the same levels as playing the song here. As the song starts,
rs-keylights writes its parts into one file the mod maps (`~/Library/Caches/rs-keylights/game-parts`, one song's kept:
a few seconds, about 58 MB a minute of song; a part the song hasn't, a silent one, is left out). Until then the song
plays as it is. A change is heard within 50 ms: the mod mixes each block of the song as the game decodes it, before
Riff Repeater's speed and the game's own sliders. Song Stems' bass and guitar levels go to 100 while the parts play
(it changes the song too), and back after. The game's MIXER's stem page has the six parts too.

**While following a song not split**, the Song Stems mod takes the part you're playing out of the game's song: the bass with a bass
tone, else the guitar. Only that part's level is heard; the other's pad is dim and its level waits for its turn. The
levels are the mod's own settings in the game's `RSMods.ini`, the same as its in-game slider, and the mod reads them
about once a second.

### Driving Rocksmith (following, riffloop mod)

| Control | Does |
|---|---|
| Play | pause and resume the game's song |
| Track ◁ ▷ | the game's up and down arrows |
| Upper arrow (right of the pads) | Escape |
| Record | Enter |
| Pitch strip, knob 8 (in Learn a Song's list) | move through the game's song list (knob 8: a letter at a time); the page's library follows |

A song clicked in the page's library is highlighted in the game's list too.

### In the game's pause menu (riffloop mod)

Learn a Song's pause menu gets an **RS-KEYLIGHTS** row, which opens these screens:

| Screen | Does |
|---|---|
| MIDI MIXER | the Launchkey's mixer page, the keyboard's: a column for each arrangement (up to 6), the drum part, the Launchkey keys and all of them, each with a fader and two pads (mute; lit keys, or the drums' beat lights). ← → pick a column, ↑ ↓ move its fader 5%, Return and Space press its pads, and the mouse drags and clicks. A drum part not made yet has its fader greyed: its pad makes it. The Launchkey's mixer and the page follow, both ways, and the Launchkey's mixer opens with it. ▲ and ▼ at its top right (or Page Up and Page Down) turn it to its stem mixer page and back, as the Launchkey's ▲ and ▼ by its knobs do, and each follows the other. The stem mixer page is the Launchkey's second page while following: the game's song's bass and guitar (columns 2 and 3, rearmed-mods' Song Stems: the one you're not playing dim, waiting its turn; greyed with Song Stems off), each with a pad to mute it and back (its level kept, greyed, the pad dimmed), the game's Music (column 7, with its own mute pad: red, its level kept, greyed), and MASTER (column 8: the bass and guitar together, as the first page's ALL, the Music apart; its pad both muted, or back, as the Launchkey's right pad on its stems page). On the first page, ALL's pad is the sound on or off, and the columns grey with it |
| STEM MIXER | the MIDI MIXER, opened on its stem mixer page |
| TONES | laid out as the Launchkey's knobs: a column for each arrangement's tone, the drum kit (on the keyboard) and the keys' (7th), on the knobs' page (tone, palm mutes or harmonics: three squares at its top right show which, and its ▲ or Page Up goes to the next, as the Launchkey's ▲ does, each following the other). ← → pick a column, ↑ ↓ (or its ▲ ▼) step its tone, as a knob does, and Space (or its pad below) is whether it follows the song's tone changes. The Launchkey's screen shows the tones while it's open |

### Everything else

- **Right column**: the top pad is Mute (green: sound on; red: just the lights), the bottom pad starts and stops
  following.
- **Play** (not following): plays and pauses the song, or plays the active one.
- **The pitch strip** (not in the mixer): goes through the library. Slide for a song at a time, flick to scroll.
  Knob 8 goes a letter at a time while it shows. **Record** switches to the song (on the song already: stops it).
  **Track ◁** is home.
- **Scale button**: the Launchkey's Scale mode takes each song's key. The pads show the scale as a piano octave, and
  its keys can snap to it.
- **The screen** shows the song: name, artist, key and scale, a progress line, the time, and the chord being played
  with whose it is (L, R or B). After a knob turns, the screen shows that setting for a few seconds. Taps are
  confirmed at the bottom left ("Lead: off"). Muted, every screen shows a crossed-out speaker.

## Drums

A song can have a **drum part**, transcribed from its own audio, that plays along with its arrangements, playing or
following. It plays in time with the game, Riff Repeater and speed changes included.

**To make one**, use **Make drum part** in the song's Drums block (a minute or so, in the background), or
`rs-keylights drums SONG` (`--again` to remake it; `--made` remakes every part made before, after an update to how
they're made). Parts are kept in `~/Library/Caches/rs-keylights/drums/`. A song already split into its parts (see
[The song's own audio](#the-songs-own-audio)) takes its drums from there instead of separating them again.

It needs `tools/drums-setup.sh` run once. The script:

- builds rearmed-mods' Song Stems helper with more parts to take out, from its source (`~/git/rearmed-mods`, or the
  folder given);
- makes a Python with [ADTOF](https://github.com/MZehren/ADTOF)'s
  [PyTorch port](https://github.com/xavriley/ADTOF-pytorch).

Both go in `~/Library/Application Support/rs-keylights/drums/`. It needs the Xcode command line tools, uv, and
rearmed-mods' Song Stems installed (for its model).

### Where it plays

- **On the Casio** (by default): its drum channel, 10. The **Drums** card's **Drum kit** picks the Casio's kit
  (Standard, Room, Power, Synth, Brush or Orchestra) for every song; the drum part's Launchkey knob does the same.
- **On another device**: **Other MIDI devices** › Drums, with its drums' notes either General MIDI's or an
  original Novation Circuit's.

**On a Circuit**, the Drums card says, for each of the part's drums, which of the Circuit's 4 drums plays it and
with which sample (1 to 64):

- **Drum 1–4**: always that drum. Empty sample: the sample that drum has.
- **Shared**: each hit goes on whichever of the Circuit's drums no other drum is fixed on (normally 3 and 4),
  choosing as it goes:
  1. one already on its sample;
  2. else the one whose sound dies away first, so a crash rings on while hats and toms take turns on the other;
  3. never one struck at the same moment.

  A shared drum needs a sample.
- **Casio kit** fills in the whole kit for the Casio's sampled drums (below): the kick on Drum 1, the snare on 2,
  the rest shared. Choose Standard (page 1) or Power (page 2), for samples loaded in Novation Components in
  file-number order from slot 1, 32 to a page.

The sample is chosen with the drum's patch select (Control Change 8, 18, 44 or 50) just before a hit that needs it.
When two drums land on one fixed Circuit drum at once, the first of kick, snare, crash, ride, hi-hats, toms plays.

### How a drum part is made

1. The song's audio is decoded from its archive.
2. Its drums are separated out with Song Stems' Demucs model, on the Neural Engine.
3. ADTOF transcribes them into kick, snare, tom, hi-hat and cymbal.
4. The part is refined:
   - each hit's velocity comes from how loud it is;
   - toms become low, mid or high by pitch;
   - cymbals become crash or ride (a ride's hits come in steady runs).
5. The whole part is moved onto the chart, by as much as most of its kicks and snares are off the chart's 16ths (80
   ms at most). A chart's beats can be tens of milliseconds off its audio: Rime of the Ancient Mariner's are 48 ms
   late.
6. Hits within 12 ms of a 16th are put on it.

The separator and the transcriber run at macOS's background priority, on the efficiency cores, so a drum part can be
made while the game plays: about 45 s for a 5-minute song. The transcriber works on 10 s of the song at a time, which
gives the same result in 1.2 GB of memory, where ADTOF's own way takes 6 GB.

The model hears only those 5 kinds of drum, so a part has 8 at most. Hi-hats are all closed: open ones can't be told
from cymbal wash.

**From the song's parts, and the kit's pieces.** A song split into its parts has its drum part made from their drums
(the better separator's) instead of separating them again. Split with BS-RoFormer SW, its drum part is made again
from them as soon as the split's done (any made before, from Demucs' drums, replaced). With a split server that has
DrumSep, its drums come back split into the kit's pieces too, and the part is refined from those (as Riley and
Dixon's ADTOF+DrumSep does it; ADTOF still finds the hits):
- a cymbal is a crash or a ride by which of the two pieces rises more as it's struck (a ride struck while a crash
  still rings is a ride);
- a hi-hat is open if its piece stays at 75% of its peak for 150 ms after it's struck (or until the next hi-hat);
- the toms' pitch comes from the toms' piece;
- each hit's velocity comes from its own piece, against that piece's loudest.

A song already split with BS-RoFormer SW but without a drum part sends just its drums to the split server for their
pieces (a minute or so). A song not split keeps the quick way: the drum part's pad (dim on the Launchkey) separates
its drums alone with Demucs, about 45 s for 5 minutes, refined from the drum track as above.

### The Casio's drums as samples

`rs-keylights sample-drums` records the Casio's drums for a sampler, such as a Circuit (through Novation
Components). Plug the Casio's audio output into an audio input on the Mac. It then:

1. strikes the snare to find which inputs it's heard on (`--channels 3,4` to say; `--input NAME` for another
   device; `--list` shows them);
2. strikes each drum (General MIDI notes 35 to 59) and records it until it dies away;
3. trims each one and makes the kit as loud as its loudest drum allows, keeping its balance;
4. saves `36 Kick 1.wav`… in `~/Music/Casio drums/` (`-o` elsewhere): mono, 48 kHz, 16-bit, plus stereo copies in
   `stereo/`.

Options:

- `--kit power` records another kit (into a folder of its name);
- `--all-kits` records all six, about a minute each, warning if one sounds like the first (the keyboard didn't
  switch);
- `--reverb` keeps the keyboard's reverb;
- `--velocity` sets how hard each drum is struck (120);
- `--notes 36,38,42` records just some.

Afterwards the Casio is back on its Standard kit. A song's drum part sets the kit you chose as it plays.

## The song's own audio

🎵 in the header plays the song's own audio from the Mac along with a song you play:

- It's paused, moved and looped with the song.
- It's slowed or sped up without changing pitch. At the song's own speed it plays exactly as it is, each block timed
  by the sound device's own clock for when it's heard, so it never needs stretching to keep up.
- It's decoded from the song's archive in a fraction of a second (a split song with its parts: about a second).
  It stays decoded for a minute after it stops, so playing it again starts at once.
- It's off each time rs-keylights starts, and it doesn't play while following.

The **Song's audio** card sets its volume and output.

**Split into parts** separates it into drums, bass, guitar, vocals, keys and other (ambience, synths, effects), once,
with the **separator** chosen beside it:

- **BS-RoFormer SW** (jarredou's 6-stem model, as jonkubis' Core ML port has it): much the cleaner parts, the guitar
  most of all, in a little longer than the song itself on the Mac's GPU (in the background, out of the game's way).
  It needs `tools/roformer-setup.sh` run once (it downloads the model, 330 MB, and its own Python), and Song Stems'
  decoder from `tools/drums-setup.sh`. The model's weights are jarredou's, their licence unstated: for your own use.
- **Demucs** (Song Stems' htdemucs_6s): quick, about 35 s for a 4-minute song on the Neural Engine, all five parts
  from one pass of the model, but rougher (a separator built before that takes one pass a part, about two minutes:
  run `tools/drums-setup.sh` again).

A song can have both; the chosen separator's parts play (✓ beside a separator: its parts are made), else the other's.

**Split on another computer.** BS-RoFormer SW runs many times quicker on a PC's NVIDIA GPU. Copy
`tools/rs-keylights-split-server.py` to it and run it there (`python3 rs-keylights-split-server.py`). Read it first:
the first time, it sets itself up in its own folder (`~/.local/share/rs-keylights-split`: its own Python with PyTorch
for CUDA, the model's code and the model, about 5 GB, nothing system-wide, no root), checks the model runs on the GPU
(printing how long a 10-minute song will take), then prints its address and a key. Put those in the Song's audio
card's **Split on another computer**. Songs split with BS-RoFormer SW are then sent to it (about 60 MB up for 10
minutes of song, its parts back as lossless FLAC); if it doesn't answer, they're split on the Mac instead (if
`tools/roformer-setup.sh` has been run). It listens on your network on port 8042, answering only requests with its
key. Its models are let go of 10 minutes after the last song, so the GPU's memory is free for games. It also has
DrumSep (aufr33 and jarredou's MDX23C model, 440 MB more on its first run): with it, a BS-RoFormer SW split brings
back the drums' kit pieces too (kick, snare, toms, hi-hat, ride, crash, split from its drums in the same job), and
the song's drum part is made again from them as soon as the parts are in (see
[How a drum part is made](#how-a-drum-part-is-made)). An older copy of the script on the PC just splits the parts.
In its terminal, the song being split has a progress bar; **Its output**, under the address and key in the Song's
audio card, shows what it's printed (its last 300 lines) and how far the song it's splitting is, so you needn't
keep a terminal open on the PC.
The parts are kept as AAC in
`~/Library/Caches/rs-keylights/stems/`, at most 2 GB, the least used going first. Each part then gets its own volume
and mute, the same for every song. This needs the `drums` and `listen` extras and `tools/drums-setup.sh`.

Other isn't kept: it's the song less the five parts, so it also has whatever the separator left of them, often the
drums' cymbals and some of their hits. With the drums muted and Other up, a faint, choppy ghost of the drums comes
through; mute Other too for a part on its own (the vocals alone, say).

The Launchkey's mixer has a page for the parts (see [The mixer](#the-mixer)). While following, that page controls
the game's own song instead.

## Hits and misses

The **Hits and misses** card shows what you hit and missed each time you played the song in Rocksmith with the page
running:

- your accuracy, as the game counts it;
- each section's hits out of its notes;
- the notes you missed, by time and section, with how they're played. Not following, a click goes there.

Earlier runs are in the menu at the top. It needs the riffloop mod, which reads the game's note counts as you play.
Each run is kept in `~/Library/Application Support/rs-keylights/plays/`. The game only counts the notes at your
difficulty level, so a note it doesn't count isn't listed.

## Guitar pedal (DNAfx)

rs-keylights works with a Harley Benton DNAfx GiT (or GiT Core) plugged into the Mac by USB, through
[dnafx-preset](https://github.com/manbearpig3130/dnafx-preset):

```sh
uv pip install --python .venv/bin/python -e ~/git/dnafx-preset
```

### Switching presets

The Launchkey's knob 6 and pads (above), or the **Guitar pedal** card (‹ ›, a list of all 200, and your favourites),
switch the pedal's presets. Preset names come from the newest `.bhb` backup the DNAfx editor saved (in Music,
Documents, Desktop or Downloads), or the one named on the card.

The pedal can't be asked which preset it's on. rs-keylights goes by the last one it switched to, or the last one
dnafx-preset's listener switched to, whichever was later. A footswitch press on the pedal isn't known. Each switch
runs dnafx-preset in its own process, so a slow pedal can't freeze the page.

### Song tones without RS Companion

`rs-keylights song-tones` does what RS Companion's song tone messages do, but from the game itself (riffloop mod).
When an arrangement loads, it sends:

- one SysEx describing its tones (each one's category: Clean, Crunch, Heavy or Lead, its effects, and the song's
  style);
- a Program Change for each tone, 100 ms before the game switches to it (8 for the first, 9 for the next…).

They go out on a MIDI source of its own, **Rocksmith Tones**, which dnafx-preset's listener follows:

```sh
~/git/dnafx-preset/.venv/bin/dnafx-preset listen --midi-source "Rocksmith Tones" --config ~/.config/dnafx-preset/tones.toml
```

It uses RS Companion's own rules (ported in `rig.py`) and its `presets.json` and `rscompanion.json`, if they're in
`~/Library/Application Support/rs-mac-companion` (or `$RS_COMPANION_DATA`). Without them every tone is Clean. It has
been checked against 31 of RS Companion's messages. Run it as an RS Re-ARMed sidekick, beside dnafx-preset's
listener.

### Rocksmith's own tones on the pedal

`rs-keylights dnafx-tone SONG` makes DNAfx presets from a song's tones, for tones whose amps the DNAfx has:

| Rocksmith | DNAfx |
|---|---|
| Marshall JCM800, Plexi | J800, PLX100 |
| Orange Rockerverb | ROCK VRB |
| HG100 ("custom high-gain") | PV 5050, the nearest |
| Marshall 1960 and Orange cabinets | 1960 412 and CITRUS |

- The amp's knobs are Rocksmith's. The JCM800's gain is matched by ear: Rocksmith's 65 is the DNAfx's 100.
- Reverbs, delays, modulation and EQ become the DNAfx's nearest.
- A gain of 80 or more adds a noise gate.
- The expression pedal goes on the amp's gain.
- Drives, wahs and studio EQs are left out, and it says so.

It saves a `.phb` per tone for the DNAfx editor. `--to-pedal` writes them to the pedal from slot 194.

As each song loads, `song-tones` writes its arrangement's tones this way to slots 194 to 199, and its tone messages
then point the listener at those slots. A preset already there isn't written again. The slots' first contents are
backed up in `~/Music/DNAfx backups`, and no other slot is written. `song-tones --no-pedal` turns this off.

### Pedal tones on the page

The **Pedal tones** card shows the song's tones, the current one lit. For each one it shows how long it plays, its
effects and category, and the pedal preset it gets, with the rule that picks it.

- Each tone can be **Auto** (the rules for every song) or the song's own. Its own category goes into RS
  Companion's `presets.json`; its own preset goes into dnafx-preset's tone map as a song rule.
- **Try** switches the pedal to a tone's preset, and **Keep** gives the tone the preset the pedal is on.
- The **Pedal tone rules** card (settings column) has the rules for every song.

Changes keep the rest of the file, comments included, and save the old one as `.bak`. The pedal switches at once if
the tone is playing.

## A song in a DAW

**MIDI file** under the song's title saves the song as a MIDI file for Logic or GarageBand (`Artist - Title.mid`).
From the command line it's `rs-keylights midi SONG` (`-o` a file or folder, `--audio` for the song's WAV beside it).
The file has:

- **The record's tempo map**: each beat of the chart gets its own tempo, so the bars line up with the band. The file
  starts when the song does.
- **A track per part**, at its hardest difficulty and real pitch, played exactly as the Casio plays it (ringing,
  strums, chugs, slides, pinch harmonics…). Bends are pitch bends and vibrato is the mod wheel. The bend range is
  sent as RPN 0. If a part bends further than 2 semitones, its track's name says so ("Lead (bend range 3)"), for
  instruments that ignore RPN. Tones and chord names are text events.
- **Sections** as markers, and **the key** as a key signature.
- **The vocals**: lyrics as lyric events, and the melody as notes when the chart has one (about half of them do).
- **The drum part**, if it's made, on channel 10.

**Song audio** saves the song's audio as a WAV that starts when the MIDI file does. Put both at the start of a
project and they line up. It needs `tools/drums-setup.sh`, since its tools decode the audio.

## Fixes for the Mac game

### PC songs in the Mac game

`rs-keylights pc-songs` converts PC song archives (`*_p.psarc`) for songs that have no Mac archive, so they show in
RS Re-ARMed and on the page. It looks in the Steam game's `dlc` folder and the game's own (`--from DIR` for others),
and writes to the game's `dlc/from-pc/`, which RS Re-ARMed's updates leave alone.

- **No duplicates**: a PC archive is skipped when another archive has the same song (the same arrangement IDs, or
  artist and title). It keeps the newer of two PC archives. A converted song is removed when its PC archive goes, or
  a Mac archive of it appears.
- **What changes**: the two platforms' archives hold the same files, the audio byte for byte. Only the folder names,
  the `.nt` graph that names them, and the charts' encryption differ. So folders are renamed, the graph changed, and
  each chart re-encrypted with the Mac key (and checked). Songs keep their AppID if the game knows it, else get the
  free one custom songs use (248750).
- **Quick**: a run only reads archives that are new or changed (`dlc/from-pc/.from-pc.json` remembers the rest).
  `--dry-run` says what it would do.

Add it to RS Re-ARMed's `sidekicks.conf` and new PC songs show each time the game starts.

### The song list's scroll bar

In Learn a Song's list, the scroll bar's thumb can only be dragged near the top of the list. The list's menu
(`las_songslist.gfx` in `cache.psarc`) tells the game where it can be clicked. The rows of songs reach over the
scroll bar and are drawn above the thumb, so the thumb gets no clicks in the lower half of the screen.

`rs-keylights song-list-fix` makes the scroll bar's whole track clickable, above everything else, by changing a few
bytes of the menu's ActionScript. Click anywhere on the bar and the list follows the mouse.

`cache.psarc` itself isn't changed (RS Re-ARMed's Metal renderer loads more slowly with a changed one). The fixed
menu goes into RS Re-ARMed's menu overlay, `Update/patcharchives.psarc`, which the game reads first. RS Re-ARMed
rebuilds the overlay when it refreshes the game files, so run the command from `sidekicks.conf` to keep the fix.
`--undo` removes it. It needs RS Re-ARMed's overlay hook (on unless `RS_MODS_OVERLAY=0`).

## Commands

The page does most of this. The commands are for scripting, sidekicks, and using it without the page.

```sh
rs-keylights gui                                  # the page
rs-keylights follow                               # light the keys along with the game (Ctrl-C stops)
rs-keylights follow --sound --tone 178            # ...and play the notes, on 178 SAW LEAD 1
rs-keylights play "pursuit of vikings"            # the lead part, with lights and sound
rs-keylights play amonpurs bass --from 60 --speed 0.5
rs-keylights play ~/Downloads/Some-Song_m.psarc rhythm --silent
rs-keylights play amonpurs lead rhythm --tone lead=178 --lights lead=2,rhythm=2
rs-keylights tones organ                          # list the keyboard's tones (all, without a word)
rs-keylights midi amontwil --audio -o ~/Music     # a MIDI file and the song's audio
rs-keylights drums amontwil                       # make a drum part
rs-keylights sample-drums --all-kits              # record the Casio's drum kits
rs-keylights listen                               # print the notes heard on the guitar input
rs-keylights index                                # update the song index now
```

(From the repo, run them as `.venv/bin/rs-keylights`.)

`play` takes a `.psarc` file, or part of a song's name, artist or file name. With no part named it plays the lead,
else the rhythm, else the bass. Name several parts to play them together. The keyboard lights 4 keys at most, so by
default only the first part lights them:

| `--lights` | Lights |
|---|---|
| (none given) | the first part, 4 keys |
| `lead=2,rhythm=2` | each part's lowest notes, as many as given |
| `lead,rhythm` | shared evenly |
| `rhythm` | another part |
| `none` | none |

The sound is 079 DISTORTION GT, or 085 FINGERED BASS for bass parts. Tones 401–426 play sounds you sampled on the
keyboard. The tone picked on the keyboard's panel is only for the keys you play.

| Option | Default | |
|---|---|---|
| `--midi-port` | `CASIO USB-MIDI` | the keyboard's MIDI port, or part of its name (before the command) |
| `--channel` | 4 | the keyboard's navigate channel (before the command) |
| `--game-dir` | RS Re-ARMed's game folder | the `Rocksmith2014` folder (before the command) |
| `--tone` | 079, or 085 for bass | the keyboard's tone, by number or name; `PART=` for one part |
| `--mute-tone` | the main tone | a tone for palm-muted notes (e.g. 077 MUTE GUITAR) |
| `--pinch-tone` | 298 GM GT HARMONICS | a tone for pinch harmonics (0: the part's own) |
| `--real-pitch` | off | sound the notes' own pitch, not the lit keys' |
| `--lights-early` | 15 ms | light the keys this much before the notes |
| `--fastest-flash` | 70 ms | a key's repeats closer than this flash on every other note |
| `play --from`, `--speed` | 0, 1 | where to start (seconds) and how fast |
| `play --silent` | | lights only |
| `follow --sound` | off | play the notes too, not just light the keys |
| `follow --offset-ms` | 0 | light the keys this much earlier (negative: later) |
| `follow --overlay-port` | 9938 | RS Companion's overlay port |
| `follow --trace FILE` | | log following's timing, to look into it (`gui --trace` too): what plays and stops, RS Companion's reads, and the game's own time for 2 s after it jumps (a pause gone, a seek); a file past 20 MB is kept as `FILE.1` and begun again |

Tone options can be for one part (`--tone lead=178 --mute-tone rhythm=77`), or, without `PART=`, for every part. In
`follow`, they're for the part you play.

`rs-keylights listen` prints the notes it hears (`--bass` for a bass). `--record FILE` saves what it hears to a WAV,
and `--replay FILE` listens to that file instead of the input.

## How it works

**The keys.** The LK-170 lights keys only for notes on its *navigate channels* (FUNCTION > `Navi. Ch`: 4 for the
right hand, 3 for the left). It lights them even with the channel's volume at 0, so the lights are silent. At most 4
keys light at once, from C2 to C7.

**The sound.** The keyboard plays all 16 MIDI channels at once, each with its own tone. The parts sound on these
channels (main tone, harmonics, palm mutes):

| Part | Channels |
|---|---|
| 1st | 11, 12, 13 |
| 2nd | 14, 15, 16 |
| 3rd | 5, 6, 7 |
| 4th | 8, 9, 2 |

That keeps clear of the navigate channels (3, 4), the Launchkey's keys (1) and the drums (10). A chord sounds all its
notes, even when only some of them light. The channels no part takes are spare: a note that bends or wobbles borrows
one while it sounds ([How the parts sound](#how-the-parts-sound)).

**The notes.** Each arrangement's notes are in its `.sng` file in the song's archive. A note's string and fret, the
tuning and the capo give its pitch. Each phrase is taken at its hardest level, so you see the full part, not your
difficulty level. A part that goes below C2 (B standard guitar, bass) is moved up whole octaves if it fits, and any
note still outside the keys is folded in. A note stays lit until the next one, for 80 ms at least, and chords light
their lowest 4 notes.

**The flashing.** On the LK-170 a key flashing every 80 ms or slower looks like flashes, and every 60 ms almost like
a steady light. It looked best every ~115 ms, dark for 40–55% of the time. So before a key's next strike it goes
dark for 45% of the time to it (30 to 60 ms), and repeats faster than 70 ms (`--fastest-flash`) flash on every
other note. The lights also look a little late next to the sound, so the keys light 15 ms early
(`--lights-early`: 10–20 ms looked in time, 40 too early).

**The timing, with the riffloop mod.** Following asks the game itself, 50 times a second, over a local socket, where
the song is and which arrangement is playing (named like its `.sng` file, e.g. `amonamarthwayofvikings_lead`). It
answers in under a tenth of a millisecond, so the keys are within a frame of the game. The pause screen goes dark at
once, and Riff Repeater's speed is measured from it. RS Companion isn't read at all then.

**On time while the Mac is busy.** The thread that plays the notes is macOS's most urgent kind (user-interactive:
the performance cores first), and while a song plays the process tells macOS it's latency-critical work, so App Nap
doesn't slow its timers (the Dock app's window may be closed or hidden). Python hands between its threads every
millisecond instead of every 5. What's kept for long (the songs' index, songs read) is left out of Python's full
garbage collections, which hold up every thread while they go over it all (about 15 ms with the index: longer than a
block of the song's audio, so a bit of it was lost each time a part was changed as it played). The song's audio asks
where the song is without waiting for the song's thread. Following, the trace (`--trace`) sums up every 30 s how
many steps came more than 10, 20, 50 and 100 ms late, and notes any step held up longer than 150 ms.

**The timing, with RS Companion.** RS Companion updates the song timer about 4 times a second. `follow` reads it 10
times a second and keeps its own clock in between. Each value arrives a little late, never early, so the clock goes
by the freshest of the last 2.5 s. It measures the timer's rate for Riff Repeater's slower speeds, and treats a timer
that stops as a pause and a jump as a seek. In a test with a simulated timer, the lights were a median 9 ms late
(95% within 15 ms). RS Companion is read on its own thread, so its larger answers (about 100 KB, album art and all)
never hold up the notes.

**Riff Repeater below about 65% speed needs a fixed RS Companion** (only without the riffloop mod). RS Companion
1.4.2 decides another song is playing after 10 s of slowed song, and stops reporting. The fix is on the
[`fix/slow-riff-repeater`](https://github.com/manbearpig3130/rs-mac-companion/tree/fix/slow-riff-repeater) branch of
a fork: install it with `app/build.sh --install`, then click Update in its window (with the game closed).

## The keyboard's quirks

- **It ignores MIDI while its idle light show runs.** After about 6 minutes without a key or button press on the AC
  adaptor, the keys start lighting by themselves and it stops reading USB MIDI until you touch it. To turn the show
  off: FUNCTION until `Demo2`, then [–].
- **It switches off after 30 minutes without a key or button press.** Hold TONE while switching it on to keep it on.
- **Settings reset when it's switched on**, including the above, and channel volumes. rs-keylights sends the lights'
  volume again every few seconds, so they stay silent.
- **No keys light?** Check that `Navi. Ch` is 04 (or give `--channel`), and that the PART SELECT button hasn't turned
  the navigate channels off.

## Development

```sh
uv pip install --python .venv/bin/python -e ".[test]"
.venv/bin/python -m pytest
.venv/bin/pyright src
```

The tests build small song files in the game's formats, so they need no game files.

## Credits

- The tone list (with each tone's Bank Select and Program Change) is from Casio's LK-170/LK-175/LK-260 appendix.
- The `.sng` layout follows [Rocksmith2014.NET](https://github.com/iminashi/Rocksmith2014.NET) by iminashi.
- [RS Companion](https://github.com/glebb/rs-mac-companion) by glebb finds the song timer in the game. Its overlay
  API is [RockSniffer](https://github.com/kokolihapihvi/RockSniffer)'s, and its docs describe the Mac song files.
- Drum transcription is [ADTOF](https://github.com/MZehren/ADTOF) by MZehren, through xavriley's
  [PyTorch port](https://github.com/xavriley/ADTOF-pytorch).
- Song separation is Demucs (htdemucs_6s), as rearmed-mods' Song Stems runs it.
- The archive and song file keys are the well-known Rocksmith 2014 ones.

#!/bin/sh
# drums-setup.sh [REARMED_MODS]: the tools rs-keylights makes a song's drum part with (rs-keylights drums SONG, or
# the page's Make drum part), in ~/Library/Application Support/rs-keylights/drums ($RS_KEYLIGHTS_DRUMS_TOOLS):
#
#   rs-stems-drums  rearmed-mods' Song Stems helper (from its source, REARMED_MODS: default ~/git/rearmed-mods), built
#                   with three more parts to take out, the drums, the vocals and the piano: it decodes the song's audio
#                   and separates it with Song Stems' Demucs model (installed with rearmed-mods: ~/Library/Application
#                   Support/RS Re-ARMed/stems), for a drum part and for a song's parts (stems: rs-keylights stems)
#   venv            a Python with ADTOF's PyTorch port (github.com/xavriley/ADTOF-pytorch, at the commit below), which
#                   transcribes the drums
#
# Needs the Xcode command line tools, uv, and rearmed-mods' Song Stems installed (its model). Run it again to
# rebuild; the game's codebooks (for decoding its audio) are made from your game as the first drum part is.
set -eu
mods=${1:-$HOME/git/rearmed-mods}
tools=${RS_KEYLIGHTS_DRUMS_TOOLS:-$HOME/Library/Application Support/rs-keylights/drums}
adtof_commit=85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9
model="$HOME/Library/Application Support/RS Re-ARMed/stems/htdemucs_6s.mlmodelc"

[ -f "$mods/stems/rs_stems.m" ] || { echo "No rearmed-mods source at $mods (give its folder)" >&2; exit 1; }
[ -d "$model" ] || echo "Note: Song Stems' model isn't installed ($model): install rearmed-mods' Song Stems first" >&2
command -v uv >/dev/null || { echo "uv is needed (https://docs.astral.sh/uv/)" >&2; exit 1; }

build="$tools/build"
mkdir -p "$build"
for f in rs_stems.m wwise_vorbis.c wwise_vorbis.h stems_dsp.c stems_dsp.h; do cp "$mods/stems/$f" "$build/"; done
# The helper takes out the guitar or the bass; three more parts, drums, vocals and piano (htdemucs_6s's sources 0, 3
# and 5), as they are
/usr/bin/python3 - "$build/rs_stems.m" <<'EOF'
import sys
path = sys.argv[1]
s = open(path).read()
for old, new in (('enum { PART_GUITAR, PART_BASS };',
                  'enum { PART_GUITAR, PART_BASS, PART_DRUMS, PART_VOCALS, PART_PIANO };'),
                 ('static const char *const PART_NAME[] = {"guitar", "bass"};',
                  'static const char *const PART_NAME[] = {"guitar", "bass", "drums", "vocals", "piano"};'),
                 ('static const int PART_SOURCE[] = {4, 1};', 'static const int PART_SOURCE[] = {4, 1, 0, 3, 5};'),
                 ('!strcmp(s, "bass") ? PART_BASS : -1;',
                  '!strcmp(s, "bass") ? PART_BASS : !strcmp(s, "drums") ? PART_DRUMS : !strcmp(s, "vocals") ? '
                  'PART_VOCALS : !strcmp(s, "piano") ? PART_PIANO : -1;')):
    if s.count(old) != 1:
        sys.exit(f"rearmed-mods' rs_stems.m has changed (no {old!r}): rs-keylights' drums-setup.sh needs updating")
    s = s.replace(old, new)
open(path, "w").write(s)
EOF
flags="-arch arm64 -O2 -Wall -mmacosx-version-min=15.0"
# shellcheck disable=SC2086
clang $flags -c "$build/wwise_vorbis.c" -o "$build/wwise_vorbis.o"
# shellcheck disable=SC2086
clang $flags -c "$build/stems_dsp.c" -o "$build/stems_dsp.o"
# shellcheck disable=SC2086
clang $flags -fobjc-arc "$build/rs_stems.m" "$build/wwise_vorbis.o" "$build/stems_dsp.o" -framework Foundation \
  -framework CoreML -framework Accelerate -framework AudioToolbox -o "$tools/rs-stems-drums"
codesign --force -s - "$tools/rs-stems-drums"
echo "built $tools/rs-stems-drums"

uv venv --allow-existing --python 3.12 "$tools/venv"
uv pip install --python "$tools/venv/bin/python" "git+https://github.com/xavriley/ADTOF-pytorch@$adtof_commit"
echo "ready: rs-keylights drums SONG (or the page's Make drum part)"

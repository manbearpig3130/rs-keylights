#!/bin/sh
# roformer-setup.sh [RELEASE.zip]: BS-RoFormer SW, the better separator of a song's parts (the page's Split into parts,
# with BS-RoFormer SW chosen), in ~/Library/Application Support/rs-keylights/roformer ($RS_KEYLIGHTS_ROFORMER_TOOLS):
#
#   bs_roformer_6stem.mlpackage   jarredou's BS-Rofo-SW-Fixed as jonkubis' Core ML port has it (its v0.1.0 release,
#                                 checked by its SHA-256; RELEASE.zip: that release, already downloaded)
#   bs_roformer_6stem.mlmodelc    the same, compiled for this Mac (else Core ML compiles it each time it starts)
#   venv                          a Python with coremltools, numpy and scipy, for rs-keylights' roformer_worker.py
#
# Needs uv and Song Stems' decoder (tools/drums-setup.sh), which decodes the songs. The weights are jarredou's, their
# licence unstated: they're for your own use. Run it again to set it up afresh.
set -eu
tools=${RS_KEYLIGHTS_ROFORMER_TOOLS:-$HOME/Library/Application Support/rs-keylights/roformer}
url=https://github.com/jonkubis/BS-ROFO-SW-Fixed-CoreML/releases/download/v0.1.0/BS-ROFO-SW-Fixed-CoreML-v0.1.0.zip
sha=eb420c4fc20175fbfe9a7f32b8fef3ab1dd3a814915ae5db4b4ca29238519794
model=bs_roformer_6stem

command -v uv >/dev/null || { echo "uv is needed (https://docs.astral.sh/uv/)" >&2; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
zip=${1:-}
if [ -z "$zip" ]; then
    echo "Downloading the Core ML port's release (330 MB)"
    curl -fL --progress-bar -o "$work/release.zip" "$url"
    zip="$work/release.zip"
fi
echo "$sha  $zip" | shasum -a 256 -c - >/dev/null 2>&1 ||
    { echo "$zip isn't the release expected (its SHA-256 isn't $sha)" >&2; exit 1; }
unzip -q "$zip" "$model.mlpackage/*" LICENSE README.md -d "$work/release"

mkdir -p "$tools"
rm -rf "$tools/$model.mlpackage" "$tools/$model.mlmodelc"
mv "$work/release/$model.mlpackage" "$tools/"
cp "$work/release/LICENSE" "$tools/coreml-port-LICENSE"
cp "$work/release/README.md" "$tools/coreml-port-README.md"

uv venv --allow-existing --python 3.12 "$tools/venv"
uv pip install --python "$tools/venv/bin/python" "coremltools==9.0" "numpy==1.26.4" "scipy==1.17.1"
echo "Compiling the model for this Mac"
"$tools/venv/bin/python" -I - "$tools/$model.mlpackage" "$tools/$model.mlmodelc" <<'EOF'
import shutil, sys
import coremltools as ct
made = ct.models.utils.compile_model(sys.argv[1])
shutil.move(str(made), sys.argv[2])
EOF
echo "ready: the page's Split into parts, with BS-RoFormer SW"

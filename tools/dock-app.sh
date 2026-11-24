#!/bin/sh
# dock-app.sh [GUI ARGS...]: makes ~/Applications/rs-keylights.app, for the Dock, and puts it in the Dock after
# RS Re-ARMed (once). Clicking it starts `rs-keylights gui` (by default with --lan; other arguments replace that) and
# shows the page in a window of its own, apart from the browser (tools/dock-app.swift: WebKit, as Safari has it).
# Closing the window leaves the server running; clicking the app again shows it. Quitting it (⌘Q, the Dock's Quit,
# logging out) stops the server as Ctrl-C does, giving the Launchkey back. A server already started in a Terminal is
# left alone: the app just shows its page. The server's output goes to ~/Library/Logs/rs-keylights.log, and the app
# shows its last lines if it stops on an error.
#
# Run it again after moving the repository, or to change the arguments. macOS asks once to let rs-keylights use the
# microphone (the Guitar card and warm-ups hear the guitar through it), and asks again after each rebuild. Building
# it takes Xcode's Command Line Tools (xcode-select --install) for swiftc.
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
app="$HOME/Applications/rs-keylights.app"
url=http://127.0.0.1:8040/
[ $# -gt 0 ] || set -- --lan

[ -x "$repo/.venv/bin/rs-keylights" ] || { echo "No $repo/.venv/bin/rs-keylights: install it first (README)" >&2; exit 1; }
command -v swiftc >/dev/null || { echo "No swiftc: xcode-select --install gives it" >&2; exit 1; }
pgrep -f "$app/Contents/MacOS/" >/dev/null && { echo "rs-keylights.app is running: quit it first" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
new="$work/rs-keylights.app"
mkdir -p "$new/Contents/MacOS" "$new/Contents/Resources"

# The app itself: the window, which starts and stops the server with server.sh. It keeps no state of its own but
# the window's place and zoom (its defaults); the server's pid is in a file, kept by server.sh.
swiftc -O -target "$(uname -m)-apple-macos12.0" -o "$new/Contents/MacOS/rs-keylights" "$repo/tools/dock-app.swift"

gui_args=
for arg in "$@"; do gui_args="$gui_args '$(printf '%s' "$arg" | sed "s/'/'\\\\''/g")'"; done
cat >"$work/server.sh" <<EOF
#!/bin/sh
# rs-keylights.app's server (made by $repo/tools/dock-app.sh): start | check | stop
repo='$repo'
url=$url
EOF
cat >>"$work/server.sh" <<'EOF'
pidfile="$HOME/Library/Application Support/rs-keylights/dock-app.pid"
log="$HOME/Library/Logs/rs-keylights.log"

ours() {  # the server this app started, while it runs (or, for a moment, this script's fork that becomes it)
    pid=$(cat "$pidfile" 2>/dev/null) && [ -n "$pid" ] &&
        ps -p "$pid" -o command= 2>/dev/null | grep -q -E 'rs-keylights( gui|\.app/)' && echo "$pid"
}
answers() { curl -s -o /dev/null -m 2 "$url"; }

case $1 in
start)  # prints started, ours (one it started before, adopted) or elsewhere (one it didn't start answers)
    if ours >/dev/null; then echo ours; exit 0; fi
    if answers; then echo elsewhere; exit 0; fi
    [ -x "$repo/.venv/bin/rs-keylights" ] || { echo "rs-keylights isn't installed in $repo/.venv" >&2; exit 1; }
    mkdir -p "$(dirname "$pidfile")" "$(dirname "$log")"
    cd "$repo"
    export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
EOF
printf '    .venv/bin/rs-keylights gui --no-browser%s >"$log" 2>&1 </dev/null &\n' "$gui_args" >>"$work/server.sh"
cat >>"$work/server.sh" <<'EOF'
    echo $! >"$pidfile"
    echo started ;;
check)  # prints nothing while it runs; once it's stopped, gone, or its error
    ours >/dev/null && exit 0
    rm -f "$pidfile"
    last=$(tail -n 6 "$log" 2>/dev/null)
    if printf '%s\n' "$last" | grep -q -E '^(rs-keylights: |[A-Za-z_.]*(Error|Exception)(: |$))'; then
        printf '%s\n' "$last"
    else
        echo gone
    fi ;;
stop)  # as Ctrl-C does; killed if it hasn't stopped in 5 s
    pid=$(ours) || exit 0
    kill -TERM "$pid"
    i=0
    while kill -0 "$pid" 2>/dev/null && [ $i -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
    kill -KILL "$pid" 2>/dev/null || true
    rm -f "$pidfile" ;;
esac
EOF
install -m 755 "$work/server.sh" "$new/Contents/Resources/server.sh"

# The icon: rs-keylights' pick (its keys in it), tools/rs-keylights-icon.png (1024x1024, its background clear)
mkdir "$work/icon.iconset"
for s in 16 32 128 256 512; do
    sips -z $s $s "$repo/tools/rs-keylights-icon.png" --out "$work/icon.iconset/icon_${s}x${s}.png" >/dev/null
    sips -z $((s * 2)) $((s * 2)) "$repo/tools/rs-keylights-icon.png" --out "$work/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns -o "$new/Contents/Resources/AppIcon.icns" "$work/icon.iconset"

version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$repo/pyproject.toml")
cat >"$new/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key><string>en</string>
	<key>CFBundleExecutable</key><string>rs-keylights</string>
	<key>CFBundleIconFile</key><string>AppIcon</string>
	<key>CFBundleIdentifier</key><string>io.github.rs-keylights.dock</string>
	<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
	<key>CFBundleName</key><string>rs-keylights</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleShortVersionString</key><string>${version:-0}</string>
	<key>CFBundleVersion</key><string>${version:-0}</string>
	<key>LSApplicationCategoryType</key><string>public.app-category.music</string>
	<key>LSMinimumSystemVersion</key><string>12.0</string>
	<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
	<!-- App Nap would slow the server's lights with it -->
	<key>NSAppSleepDisabled</key><true/>
	<key>NSHighResolutionCapable</key><true/>
	<key>NSMicrophoneUsageDescription</key><string>rs-keylights hears your guitar through the Rocksmith cable (the Guitar card and warm-ups).</string>
	<key>NSPrincipalClass</key><string>NSApplication</string>
	<key>RSKeylightsPage</key><string>$url</string>
</dict>
</plist>
EOF
plutil -lint -s "$new/Contents/Info.plist"
codesign --force --sign - "$new"

rm -rf "$app"
mkdir -p "$(dirname "$app")"
mv "$new" "$app"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$app"
echo "Made $app"

# In the Dock, after RS Re-ARMed (else at the end of the apps), unless it's there
/usr/bin/python3 - "$app" <<'EOF'
import plistlib, subprocess, sys
from urllib.parse import unquote, urlparse

app = sys.argv[1].rstrip("/") + "/"
dock = plistlib.loads(subprocess.run(["defaults", "export", "com.apple.dock", "-"], capture_output=True, check=True).stdout)
apps = dock.get("persistent-apps", [])

def path(item):
    return unquote(urlparse(item.get("tile-data", {}).get("file-data", {}).get("_CFURLString", "")).path)

if any(path(item) == app for item in apps):
    print("Already in the Dock")
    sys.exit()
after = next((i for i, item in enumerate(apps) if path(item).endswith("/RS Re-ARMed.app/")), len(apps) - 1)
apps.insert(after + 1, {"tile-data": {"file-data": {"_CFURLString": "file://" + app.replace(" ", "%20"),
                                                    "_CFURLStringType": 15}, "file-label": "rs-keylights"},
                        "tile-type": "file-tile"})
dock["persistent-apps"] = apps
subprocess.run(["defaults", "import", "com.apple.dock", "-"], input=plistlib.dumps(dock), check=True)
subprocess.run(["killall", "Dock"], check=True)
print("Put in the Dock after RS Re-ARMed")
EOF

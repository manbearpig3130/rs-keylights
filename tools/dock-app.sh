#!/bin/sh
# dock-app.sh [GUI ARGS...]: makes ~/Applications/rs-keylights.app, for the Dock, and puts it in the Dock after
# RS Re-ARMed (once). Clicking it starts `rs-keylights gui` (by default with --lan; other arguments replace that),
# which opens its page; clicking it again while it runs shows the page again. Quitting it (the Dock's Quit, ⌘Q,
# logging out) stops the server as Ctrl-C does, giving the Launchkey back. A server already started in a Terminal
# is left alone: the app just opens its page. The server's output goes to ~/Library/Logs/rs-keylights.log, and the
# app shows its last lines if it stops on an error.
#
# Run it again after moving the repository, or to change the arguments. macOS asks once to let rs-keylights use the
# microphone (the Guitar card and warm-ups hear the guitar through it), and asks again after each rebuild.
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
app="$HOME/Applications/rs-keylights.app"
[ $# -gt 0 ] || set -- --lan

[ -x "$repo/.venv/bin/rs-keylights" ] || { echo "No $repo/.venv/bin/rs-keylights: install it first (README)" >&2; exit 1; }
pgrep -f "$app/Contents/MacOS/" >/dev/null && { echo "rs-keylights.app is running: quit it first" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# The app itself, a stay-open AppleScript applet: it keeps no state of its own (an applet that saves its state
# writes into itself, which breaks its signature); the server's pid is in a file, kept by server.sh.
cat >"$work/main.applescript" <<'EOF'
on run
	try
		if sh("start") is "elsewhere" then quit -- a server started in a Terminal: its page is shown
	on error problem
		activate
		display alert "rs-keylights didn't start" message problem as critical
		quit
	end try
end run

on reopen
	sh("show")
end reopen

on idle
	set stopped to sh("check")
	if stopped is "" then return 3
	if stopped is not "gone" then
		activate
		display alert "rs-keylights stopped" message stopped as critical
	end if
	quit
	return 3
end idle

on quit
	try
		sh("stop")
	end try
	continue quit
end quit

on sh(command)
	return do shell script quoted form of (POSIX path of (path to me) & "Contents/Resources/server.sh") & " " & command
end sh
EOF

gui_args=
for arg in "$@"; do gui_args="$gui_args '$(printf '%s' "$arg" | sed "s/'/'\\\\''/g")'"; done
cat >"$work/server.sh" <<EOF
#!/bin/sh
# rs-keylights.app's server (made by $repo/tools/dock-app.sh): start | show | check | stop
repo='$repo'
EOF
cat >>"$work/server.sh" <<'EOF'
url=http://127.0.0.1:8040/
pidfile="$HOME/Library/Application Support/rs-keylights/dock-app.pid"
log="$HOME/Library/Logs/rs-keylights.log"

ours() {  # the server this app started, while it runs (or, for a moment, this script's fork that becomes it)
    pid=$(cat "$pidfile" 2>/dev/null) && [ -n "$pid" ] &&
        ps -p "$pid" -o command= 2>/dev/null | grep -q -E 'rs-keylights( gui|\.app/)' && echo "$pid"
}
answers() { curl -s -o /dev/null -m 2 "$url"; }

case $1 in
start)  # prints started, ours (one it started before, adopted) or elsewhere (one it didn't start answers)
    if ours >/dev/null; then answers && open "$url"; echo ours; exit 0; fi
    if answers; then open "$url"; echo elsewhere; exit 0; fi
    [ -x "$repo/.venv/bin/rs-keylights" ] || { echo "rs-keylights isn't installed in $repo/.venv" >&2; exit 1; }
    mkdir -p "$(dirname "$pidfile")" "$(dirname "$log")"
    cd "$repo"
    export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
EOF
printf '    .venv/bin/rs-keylights gui%s >"$log" 2>&1 </dev/null &\n' "$gui_args" >>"$work/server.sh"
cat >>"$work/server.sh" <<'EOF'
    echo $! >"$pidfile"
    echo started ;;
show)  # while it's still starting (the first run indexes the songs) the page opens itself
    if answers; then open "$url"; fi ;;
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

rm -rf "$app"
mkdir -p "$(dirname "$app")"
osacompile -s -o "$app" "$work/main.applescript"
install -m 755 "$work/server.sh" "$app/Contents/Resources/server.sh"

# The icon
swift "$repo/tools/dock-icon.swift" "$work/icon.png"
mkdir "$work/icon.iconset"
for s in 16 32 128 256 512; do
    sips -z $s $s "$work/icon.png" --out "$work/icon.iconset/icon_${s}x${s}.png" >/dev/null
    sips -z $((s * 2)) $((s * 2)) "$work/icon.png" --out "$work/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns -o "$app/Contents/Resources/applet.icns" "$work/icon.iconset"
rm -f "$app/Contents/Resources/Assets.car"

plist="$app/Contents/Info.plist"
pb() { /usr/libexec/PlistBuddy -c "$1" "$plist" 2>/dev/null || true; }
pb "Delete :CFBundleIconName"
pb "Delete :LSUIElement"
pb "Delete :CFBundleIdentifier"
pb "Add :CFBundleIdentifier string io.github.rs-keylights.dock"
pb "Set :CFBundleName rs-keylights"
pb "Delete :NSMicrophoneUsageDescription"
pb "Add :NSMicrophoneUsageDescription string rs-keylights hears your guitar through the Rocksmith cable (the Guitar card and warm-ups)."
pb "Delete :NSAppSleepDisabled"
pb "Add :NSAppSleepDisabled bool true"  # App Nap would slow the server's lights with it
codesign --force --sign - "$app"
touch "$app"
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

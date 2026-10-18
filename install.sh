#!/bin/sh
# Install the kitty config from this repo on a machine:
# link ~/.config/kitty to this repo, seed the session file, check tools.
# Requirements: kitty >= 0.49 (verified on 0.49.2), python3, a Nerd Font
# ("Hack Nerd Font" is set in kitty.conf). Safe to re-run.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
cfg="$HOME/.config/kitty"
sess="$HOME/.local/share/kitty/sessions/last.kitty-session"

if [ -e "$cfg" ] && [ ! -L "$cfg" ]; then
    backup="$cfg.backup-$(date +%Y%m%d%H%M%S)"
    mv "$cfg" "$backup"
    echo "moved existing $cfg -> $backup"
fi
mkdir -p "$(dirname "$cfg")"
ln -sfn "$here" "$cfg"

mkdir -p "$(dirname "$sess")"
if [ ! -s "$sess" ]; then
    printf 'layout tall\nlaunch\n' > "$sess"
fi
if command -v python3 >/dev/null 2>&1; then
    python3 "$cfg/freshen_sessions.py" "$sess" || true
fi

missing=''
for tool in kitty python3; do
    command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
if [ -n "$missing" ]; then
    echo "missing:$missing"
    echo '  macOS: brew install --cask kitty font-hack-nerd-font && brew install python'
    echo '  Linux: kitty via https://sw.kovidgoyal.net/kitty/binary/ + a Nerd Font'
fi
if command -v kitty >/dev/null 2>&1; then
    kitty --version | awk '{ if ($2+0 < 0.49) print "warning: kitty " $2 " is older than 0.49; most compatible version is 0.49.2 (some options in this config may be rejected)" }'
fi

echo "kitty config: $cfg -> $here"
echo "session file: $sess"
echo "tip: launch kitty with $cfg/session-wrapper.sh for a freshened session restore"
if [ -z "$missing" ]; then
    echo "success: kitty config installed and all checks passed."
else
    echo "finished with warnings: missing tools:$missing"
fi

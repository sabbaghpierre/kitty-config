#!/bin/sh
# Launch kitty with the last session (restored by `startup_session` in
# kitty.conf), after refreshing stale agent-session commands in it: dead
# `claude --resume` ids become `claude --continue`, opencode panes resume
# their project's most recent session (see freshen_sessions.py).
#
# Use this as the entry point (shell alias, desktop launcher) so launches
# always replay a freshened session.
set -u
CFG="$HOME/.config/kitty"
SESS="$HOME/.local/share/kitty/sessions/last.kitty-session"

mkdir -p "$(dirname "$SESS")"
# seed a stub when there is no session yet, and never restore an emptied
# file while a good backup exists: prefer the backup over the stub
if [ ! -s "$SESS" ] && [ -s "$SESS.bak" ]; then
    cp "$SESS.bak" "$SESS"
fi
if [ ! -s "$SESS" ]; then
    printf 'layout tall\nlaunch\n' > "$SESS"
fi
python3 "$CFG/freshen_sessions.py" "$SESS" || true

if command -v kitty >/dev/null 2>&1; then
    exec kitty "$@"
fi
for k in /Applications/kitty.app/Contents/MacOS/kitty "$HOME/Applications/kitty.app/Contents/MacOS/kitty"; do
    if [ -x "$k" ]; then
        exec "$k" "$@"
    fi
done
echo 'kitty not found (brew install --cask kitty)' >&2
exit 1

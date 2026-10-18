#!/usr/bin/env python3
"""Refresh dead agent-session restores in a kitty session file.

Port of the old resurrect.wezterm glue:
  * claude panes: a `--resume <id>` is kept only while its transcript
    (~/.claude/projects/<cwd-slug>/<id>.jsonl) still exists. The first dead
    (or plain) claude pane becomes `claude --continue` (keeping
    --dangerously-skip-permissions); further dead claude panes stay shells
    but echo the wezterm-style "run claude --continue" hint at startup.
  * opencode panes: the first resumes the project's most recent session
    (`opencode --session <id>`, resolved via `opencode session list`); when
    the project has no sessions at all it launches a plain opencode, because
    opencode 2.0.30 shows an error toast for `--continue` with nothing to
    continue. Extras get the same kind of hint, naming a real session id
    when one exists.

Usage:   freshen_sessions.py <session-file>   (rewrites in place, atomically)
Import:  from freshen_sessions import freshen_file
"""

import json
import os
import re
import shlex
import subprocess
import sys

SERIALIZE_FLAG = 'kitty-unserialize-data='
CLAUDE_KEEP = ('--dangerously-skip-permissions',)
RESUME_FLAGS = ('--resume', '-r', '--session-id', '--session', '-s')
OPENCODE_TIMEOUT = 3


def _base(token):
    return os.path.basename(token.replace('\\', '/').rstrip('/'))


def _is_claude(cmd):
    b = _base(cmd[0])
    return bool(re.match(r'^claude(\d[\d.]*)?$', b)) or b.startswith('claude-')


def _is_opencode(cmd):
    b = _base(cmd[0])
    return b == 'opencode' or b.startswith('opencode-')


def _resume_id(cmd):
    for i, a in enumerate(cmd):
        if a in RESUME_FLAGS and i + 1 < len(cmd):
            return cmd[i + 1]
    return None


def _claude_transcript_alive(cwd, cmd):
    sid = _resume_id(cmd)
    if not cwd or not sid or not re.fullmatch(r'[0-9a-fA-F-]+', sid):
        return False
    slug = '/' + cwd.strip('/').replace('/', '-')
    path = os.path.join(os.path.expanduser('~'), '.claude', 'projects', slug, sid + '.jsonl')
    return os.path.isfile(path)


def _opencode_session_ids(binary, cwd, counters):
    """Most-recent-first session ids for the project in cwd, or None on failure.

    One call per project per freshen run: the CLI connects to opencode's
    background service (~0.05 s warm), so the timeout only bounds the cold case
    where the service is starting.
    """
    cache = counters.setdefault('oc_sessions', {})
    key = (binary, cwd)
    if key in cache:
        return cache[key]
    ids = None
    try:
        res = subprocess.run(
            [binary, 'session', 'list', '--format', 'json', '-n', '100'],
            cwd=cwd or None, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=OPENCODE_TIMEOUT,
        )
        if res.returncode == 0:
            sessions = json.loads(res.stdout)
            if isinstance(sessions, list):
                ids = [s['id'] for s in sessions
                       if isinstance(s, dict) and re.fullmatch(r'ses_[A-Za-z0-9]+', str(s.get('id', '')))]
    except (OSError, subprocess.SubprocessError, ValueError):
        ids = None
    cache[key] = ids
    return ids


def _plain_args(cmd):
    """Saved command args minus the session-selection flags."""
    out = []
    skip_next = False
    for a in cmd[1:]:
        if skip_next:
            skip_next = False
        elif a in RESUME_FLAGS:
            skip_next = True
        elif a not in ('--continue', '-c'):
            out.append(a)
    return out


def _hint_cmd(cmd, cwd, counters):
    """Wezterm-style nudge, echoed by the shell kitty restarts for this pane."""
    if _is_claude(cmd):
        return ['echo', '-- previous claude session unavailable; run `claude --continue` to resume']
    ids = _opencode_session_ids(cmd[0], cwd, counters)
    if ids:
        return ['echo', f'-- previous opencode session unavailable; run `opencode --session {ids[0]}` to resume']
    return ['echo', '-- previous opencode session unavailable; run `opencode` to start a new session']


def _rewrite(cmd, cwd, counters):
    """Return the replacement command, or None to let the pane stay a shell."""
    if not cmd:
        return cmd
    if _is_claude(cmd):
        if _claude_transcript_alive(cwd, cmd):
            return cmd  # live session: replay the resume as saved
        counters['claude'] += 1
        if counters['claude'] == 1:
            keep = [a for a in cmd[1:] if a in CLAUDE_KEEP]
            return [cmd[0], '--continue', *keep]
        return None
    if _is_opencode(cmd):
        counters['opencode'] += 1
        if counters['opencode'] != 1:
            return None
        ids = _opencode_session_ids(cmd[0], cwd, counters)
        pinned = _resume_id(cmd)
        if ids is None or (pinned and pinned in ids):
            return cmd  # live session (or opencode unresponsive): replay as saved
        keep = _plain_args(cmd)
        return [cmd[0], *keep, '--session', ids[0]] if ids else [cmd[0], *keep]
    return cmd


def _process_line(line, cwd_ctx, counters):
    stripped = line.strip()
    if stripped.startswith('cd '):
        try:
            parts = shlex.split(stripped)
        except ValueError:
            return line, cwd_ctx
        if len(parts) >= 2:
            cwd_ctx = os.path.expanduser(parts[1])
        return line, cwd_ctx
    if not stripped.startswith('launch'):
        return line, cwd_ctx
    try:
        tokens = shlex.split(line)
    except ValueError:
        return line, cwd_ctx
    if len(tokens) < 2 or tokens[0] != 'launch':
        return line, cwd_ctx
    cwd = cwd_ctx
    for t in tokens[1:]:
        if t.startswith('--cwd='):
            cwd = t[len('--cwd='):]
            break
    ser_idx = next((i for i, t in enumerate(tokens) if t.startswith(SERIALIZE_FLAG)), None)
    if ser_idx is not None:
        try:
            data = json.loads(tokens[ser_idx][len(SERIALIZE_FLAG):])
            cmd = data.get('cmd_at_shell_startup') or []
        except ValueError:
            return line, cwd_ctx
        if isinstance(cmd, str):
            try:
                cmd = shlex.split(cmd)
            except ValueError:
                cmd = cmd.split()
        new_cmd = _rewrite(list(cmd), cwd, counters)
        if new_cmd == cmd:
            return line, cwd_ctx
        if new_cmd:
            data['cmd_at_shell_startup'] = new_cmd
        else:
            # extra agent pane: stay a shell, but echo the hint at startup
            data['cmd_at_shell_startup'] = _hint_cmd(cmd, cwd, counters)
        tokens[ser_idx] = SERIALIZE_FLAG + json.dumps(data)
    else:
        # window launched with an explicit command after the launch options
        i = 1
        while i < len(tokens) and tokens[i].startswith('-'):
            i += 1
        if i >= len(tokens):
            return line, cwd_ctx
        cmd = tokens[i:]
        new_cmd = _rewrite(list(cmd), cwd, counters)
        if new_cmd == cmd:
            return line, cwd_ctx
        # extras: plain shell (there is no shell here to echo a hint into)
        tokens = tokens[:i] + (new_cmd or [])
    return shlex.join(tokens), cwd_ctx


def _ensure_maximized(lines):
    """Make every OS window in the session open maximized (full-sized window)."""
    result = []
    need = True  # current OS-window block has no os_window_state yet
    for line in lines:
        s = line.strip()
        if s == 'new_os_window' or s.startswith('new_os_window '):
            if need:
                result.append('os_window_state maximized')
            result.append(line)
            need = True
            continue
        if s.startswith('os_window_state'):
            if need:
                result.append('os_window_state maximized')
                need = False
            continue  # drop the original either way; ours is normalized
        result.append(line)
    if need and result:
        result.append('os_window_state maximized')
    return result


def freshen_file(path):
    """Rewrite the session file in place if anything changed. Returns True if changed."""
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except OSError:
        return False
    counters = {'claude': 0, 'opencode': 0}
    cwd_ctx = None
    out = []
    for line in lines:
        new_line, cwd_ctx = _process_line(line, cwd_ctx, counters)
        out.append(new_line)
    out = _ensure_maximized(out)
    if out == lines:
        return False
    tmp = path + '.freshen-tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write('\n'.join(out) + '\n')
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    return True


def main(argv):
    if len(argv) != 2:
        print('usage: freshen_sessions.py <session-file>', file=sys.stderr)
        return 2
    freshen_file(argv[1])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))

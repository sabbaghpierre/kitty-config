#!/usr/bin/env python3
"""kitty global watcher: smart tab titles + session autosave.

Loaded via `watcher tab-watcher.py` in kitty.conf and called inside kitty for
every window. Port of the old WezTerm tab labels: icon + the program's own
title when it sets an informative one, otherwise icon + process name, and
duplicate labels are disambiguated with the kitty window id. Tabs renamed by
hand are never touched.

Also autosaves the session (debounced) on activity and on quit, and refreshes
dead agent-session commands right after every save (freshen_sessions.py).
Errors are appended to ~/.local/state/kitty/tab-watcher.log.
"""

import functools
import json
import os
import re
import shlex
import shutil
import sys
import time
import traceback
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kitty.boss import Boss
    from kitty.window import Window

ICONS = {
    'nvim': '\ue6ae',
    'vim': '\ue62b',
    'node': '\ue719',
    'npm': '\ued0e',
    'git': '\uec6f',
    'lazygit': '\uf062c',
    'fdemon': '\uf08c6',
    'claude': '\uf06a9',
    'opencode': '\uf0174',
    'sh': '\uea85',
    'bash': '\uea85',
    'zsh': '\uea85',
    'fish': '\uea85',
}
SHELLS = {'sh', 'bash', 'zsh', 'fish', 'dash', 'ksh', 'tcsh', 'nu', 'pwsh'}
MAX_LABEL = 28
SAVE_PATH = os.path.expanduser('~/.local/share/kitty/sessions/last.kitty-session')
SAVE_INTERVAL = 45.0
SCAN_INTERVAL = 0.5
LOG_PATH = os.path.expanduser('~/.local/state/kitty/tab-watcher.log')
MAX_LOG = 1_000_000

_state = {'frozen': set(), 'applied': {}, 'cmd': {}, 'last_save': float('-inf'), 'last_scan': float('-inf'), 'last_freshen': float('-inf')}


def _log(msg):
    print(f'tab-watcher: {msg}', file=sys.stderr)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > MAX_LOG:
            os.replace(LOG_PATH, LOG_PATH + '.old')
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(f'{datetime.now().isoformat(timespec="seconds")} {msg}\n')
    except OSError:
        pass


def _safe(fn):
    """Log callback crashes to the log file, then let kitty report them too."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as err:
            _log(f'{fn.__name__} failed: {err!r}\n{traceback.format_exc()}')
            raise

    return wrapper


# load the sibling freshen_sessions.py by path (no sys.path pollution in kitty)
_freshen_impl = None
try:
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        'kitty_freshen_sessions', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'freshen_sessions.py')
    )
    if _spec is not None and _spec.loader is not None:
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _freshen_impl = _mod.freshen_file
except Exception as err:
    _log(f'could not load freshen_sessions.py: {err!r}')

try:
    from kitty.constants import version as _kitty_version
except Exception:
    _kitty_version = 'unknown'
_log(f'watcher loaded (kitty {_kitty_version})')


def freshen_file(path):
    if _freshen_impl is None:
        return False
    try:
        return _freshen_impl(path)
    except Exception as err:
        _log(f'freshen failed: {err!r}')
        return False


def _basename(tok):
    return tok.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]


def _clean_name(raw):
    name = raw[:-4] if raw.endswith('.exe') else raw
    base = re.sub(r'-?\d[\d.]*$', '', name)
    if not base:
        return 'claude' if re.match(r'^\d+\.\d', name) else 'shell'
    return base


def _foreground_cmdline(win):
    procs = [p for p in (win.get('foreground_processes') or []) if isinstance(p, dict) and p.get('cmdline')]
    if not procs:
        return []
    return list(max(procs, key=lambda p: p.get('pid') or 0)['cmdline'])


def _proc_name(win):
    cmdline = _foreground_cmdline(win)
    return _clean_name(_basename(cmdline[0])) if cmdline else ''


def _icon(name):
    if name in ICONS:
        return ICONS[name]
    if name.startswith('claude'):
        return ICONS['claude']
    if name.startswith('opencode'):
        return ICONS['opencode']
    return ICONS['sh']


def _label(win):
    name = _proc_name(win) or 'shell'
    cmd = _state['cmd'].get(win.get('id'))
    if cmd and name in SHELLS:
        name = cmd
    title = (win.get('title') or '').strip()
    # kitty titles windows with the command line (wezterm uses the process
    # name), so path/cmdline-shaped titles are not informative app titles
    cmdline = _foreground_cmdline(win)
    if _basename(title) == name or (cmdline and title in (cmdline[0], ' '.join(cmdline))):
        title = ''
    cwd = win.get('cwd') or ''
    dirname = _basename(cwd) if cwd else ''
    text = title if title and title not in (name, cwd, dirname, '~') else name
    label = f'{_icon(name)}  {text}'
    if len(label) > MAX_LABEL:
        label = label[:MAX_LABEL - 1] + '…'
    return label


def _rc(boss, args):
    return boss.call_remote_control(None, args)


def _scan_and_apply(boss):
    now = time.monotonic()
    if now - _state['last_scan'] < SCAN_INTERVAL:
        return
    _state['last_scan'] = now
    try:
        raw = _rc(boss, ('ls',))
        if isinstance(raw, bytes):
            raw = raw.decode()
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as err:
        _log(f'ls failed: {err!r}')
        return
    seen = set()
    for osw in data or []:
        used = set()
        for tab in osw.get('tabs') or []:
            tid = tab.get('id')
            wins = tab.get('windows') or []
            if tid is None or not wins:
                continue
            seen.add(tid)
            current = tab.get('title') or ''
            if tid in _state['frozen']:
                if current:
                    used.add(current)
                continue
            if tid in _state['applied'] and current != _state['applied'][tid]:
                _state['frozen'].add(tid)  # renamed by hand: stop managing this tab
                if current:
                    used.add(current)
                continue
            win = next((w for w in wins if w.get('is_active')), wins[-1])
            want = _label(win)
            if want in used:
                want = f'{want} · #{win.get("id")}'
            used.add(want)
            if current == want:
                _state['applied'][tid] = want
                continue
            try:
                _rc(boss, ('set-tab-title', f'--match=id:{tid}', want))
                _state['applied'][tid] = want
            except Exception as err:
                _log(f'set-tab-title {tid} failed: {err!r}')
    for tid in list(_state['applied']):
        if tid not in seen:
            del _state['applied'][tid]
            _state['frozen'].discard(tid)


def _save(boss, force=False):
    now = time.monotonic()
    if force or now - _state['last_save'] >= SAVE_INTERVAL:
        try:
            os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
            tmp = SAVE_PATH + '.saving'
            try:
                os.unlink(tmp)
            except OSError:
                pass
            # a save can fire before any window exists (early startup, or
            # shutdown after the windows are gone); write aside and only
            # replace a non-empty result so the session is never clobbered
            _rc(boss, ('action', f'save_as_session --save-only --use-foreground-process {tmp}'))
            with open(tmp, encoding='utf-8') as f:
                content = f.read()
            if content.strip():
                # one-generation backup of the previous save (a clobbered
                # empty file must never overwrite a good backup)
                if os.path.exists(SAVE_PATH) and os.path.getsize(SAVE_PATH) > 0:
                    shutil.copyfile(SAVE_PATH, SAVE_PATH + '.bak')
                os.replace(tmp, SAVE_PATH)
            else:
                _log('save produced an empty session; kept the previous file')
            try:
                os.unlink(tmp)
            except OSError:
                pass
            _state['last_save'] = now
        except Exception as err:
            _log(f'save failed: {err!r}')
    if force or now - _state['last_freshen'] >= 10.0:
        _state['last_freshen'] = now
        freshen_file(SAVE_PATH)


@_safe
def on_resize(boss, window, data):
    # also fires once when a window is first created
    _scan_and_apply(boss)


@_safe
def on_title_change(boss, window, data):
    _scan_and_apply(boss)
    _save(boss)


@_safe
def on_cmd_startstop(boss, window, data):
    if data.get('is_start'):
        try:
            toks = shlex.split(data.get('cmdline') or '')
        except ValueError:
            toks = (data.get('cmdline') or '').split()
        _state['cmd'][window.id] = _clean_name(_basename(toks[0])) if toks else ''
    else:
        _state['cmd'].pop(window.id, None)
    _scan_and_apply(boss)
    _save(boss)


@_safe
def on_close(boss, window, data):
    _state['cmd'].pop(window.id, None)


@_safe
def on_tab_bar_dirty(boss, window, data):
    _scan_and_apply(boss)
    _save(boss)


@_safe
def on_quit(boss, window, data):
    if not data.get('confirmed'):
        _save(boss, force=True)

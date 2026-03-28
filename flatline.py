#!/usr/bin/env python3
"""
flatline.py — Flatline Python Process Debugger
================================================
Launch any Python application under Flatline supervision. Monitors
heartbeats, detects freezes, captures crash snapshots, and provides
an interactive crash console the moment something goes wrong.

While the app is running, a live status block shows the heartbeat.
Press D at any time to drop into the debug console without waiting
for a crash — inspect stacks, dump vars, kill, restart, etc.

Heartbeat strategy (auto-selected):
  DB mode  — MariaDB available: child beat() events stored with microtime
  Poll mode — DB unavailable: proc.poll() in async daemon thread,
              lastPolled = microtime of last success

Log files (always written):
  stack_trace.log   stack dumps at crash time
  variables.log     variable dumps at crash time
  error.log         all errors + child stdout + stderr

Authors : Trent Tompkins <trenttompkins@gmail.com>
          GPT-5.4 Plus Thinking
          Claude Sonnet 4.6 (Anthropic)
Version : 1.0b  |  2026-03-28  |  MIT License
Project : https://github.com/tibberous/Flatline
Homepage: https://flatline.triodesktop.com/
Support : (724) 431-5207  |  https://trentontompkins.com/#section-curriculum-vitae
"""
from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from typing import Any

# ── Paths ──────────────────────────────────────────────────────────────────
HERE      = Path(__file__).resolve().parent
CFG_PATH  = HERE / 'config.ini'
LOG_STACK = HERE / 'stack_trace.log'
LOG_VARS  = HERE / 'variables.log'
LOG_ERROR = HERE / 'error.log'

VERSION    = '1.0b'
BUILD_DATE = '2026-03-28'
AUTHOR     = 'Trent Tompkins <trenttompkins@gmail.com>'
PROJECT    = 'https://github.com/tibberous/Flatline'
HOMEPAGE   = 'https://flatline.triodesktop.com/'
SUPPORT    = 'https://trentontompkins.com/#section-curriculum-vitae'
PHONE      = '(724) 431-5207'

MIT_LICENSE = f"""MIT License

Copyright (c) {BUILD_DATE[:4]}  {AUTHOR}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.

Project : {PROJECT}
Homepage: {HOMEPAGE}
Support : {PHONE}  {SUPPORT}""".strip()

HELP_TEXT = f"""Flatline Process Debugger  v{VERSION}  ({BUILD_DATE})
{PROJECT}

USAGE
  python flatline.py                         Launch app from config.ini
  python flatline.py script.py              Launch specific script
  python flatline.py script.py X Y         Pass X Y to script.py
  python flatline.py -a X Y script.py      Explicit args via -a / --args
  python flatline.py --app script.py       Specify app via --app
  python flatline.py --config myapp.ini    Alternate config file
  python flatline.py --version             Print version and exit
  python flatline.py --help                This help
  python flatline.py --license             Print MIT license

ARGUMENTS  (-a / --args / /args)
  Arguments not recognised by Flatline are forwarded to the target script.
  The -a flag makes intent explicit in help output:
    python flatline.py -a "--port 8080" server.py
  is the same as:
    python flatline.py server.py --port 8080

ALWAYS-ON SUPERVISION
  Flatline always runs in supervisor mode. No --debug flag needed.
  A live status block appears while your app runs:

    ╔══════════════════════════════════════════════╗
    ║ /  Flatline[poll]  hb=12:07:13.451234  TICK  ║
    ╠══════════════════════════════════════════════╣
    ║       Press  D  to enter debug console       ║
    ╚══════════════════════════════════════════════╝

  Press D at any time to drop into the full debug console — no crash
  needed. Inspect stacks, dump vars, kill, restart.

HEARTBEAT MODES
  DB mode   (database.enabled=true AND connection OK):
    Child calls beat(reason, caller) via TCP relay → stored in MariaDB.
    Freeze: now() - last_DB_timestamp > freeze_threshold

  Poll mode (DB unavailable or disabled — default):
    proc.poll() in an async daemon thread every poll_interval seconds.
    lastPolled = microtime of last success.
    Freeze: now() - lastPolled > freeze_threshold
    Completely non-blocking. No DB required.

DEBUG CONSOLE KEYS
  1 stack   2 vars    3 close   4 SIGTERM  5 KILL
  6 refresh 7 shell   8 conns   9 monitor
  R restart S status  Q quit

LIVE REPL (app running, no console needed):
  nc localhost 5050    — type any debug key

LOG FILES  (always written)
  stack_trace.log   Stack frames at crash time
  variables.log     Variable dump at crash time
  error.log         Errors + child stdout + child stderr

CONFIG FILE  (config.ini)
  [flatline]
  app = example.py
  freeze_threshold = 2.5
  poll_interval = 1.0
  poll_fail_limit = 3

  [database]
  enabled = false
  host = 127.0.0.1
  port = 3306
  user = root
  password =
  database = flatline

SUPPORT
  {PHONE}
  Custom PHP and Python development: {SUPPORT}
""".strip()


# ══════════════════════════════════════════════════════════════════════════
# Browser detection — prefer Chrome/Firefox over VS Code / system default
# ══════════════════════════════════════════════════════════════════════════

def _find_browser() -> str | None:
    """Find Chrome or Firefox. Checks known paths and Windows registry."""
    candidates = []
    if sys.platform == 'win32':
        candidates += [
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files\Mozilla Firefox\firefox.exe',
            r'C:\Program Files (x86)\Mozilla Firefox\firefox.exe',
        ]
        local = os.environ.get('LOCALAPPDATA', '')
        if local:
            candidates.append(
                os.path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe'))
        try:
            import winreg
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for exe in ('chrome.exe', 'firefox.exe'):
                    key = rf'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}'
                    try:
                        with winreg.OpenKey(root, key) as k:
                            path, _ = winreg.QueryValueEx(k, '')
                            if path:
                                candidates.insert(0, path)
                    except Exception:
                        pass
        except ImportError:
            pass
    elif sys.platform == 'darwin':
        candidates += [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Firefox.app/Contents/MacOS/firefox',
        ]
    else:
        import shutil
        for name in ('google-chrome', 'chromium-browser', 'chromium', 'firefox'):
            p = shutil.which(name)
            if p:
                candidates.append(p)
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def open_in_browser(url: str) -> None:
    """Open URL in Chrome/Firefox; fall back to system default."""
    path = _find_browser()
    if path:
        try:
            subprocess.Popen([path, url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass
    webbrowser.open(url)


# ══════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════

class FlatlineConfig:
    """
    Reads config.ini (INI format). Falls back to safe defaults everywhere.

    [flatline]
      app              — script to launch (default: example.py)
      freeze_threshold — seconds before freeze declared (default: 2.5)
      poll_interval    — proc.poll() interval in seconds (default: 1.0)
      poll_fail_limit  — consecutive poll failures before crash (default: 3)

    [database]
      enabled / host / port / user / password / database
      If enabled=false or connection fails, poll mode is used automatically.
    """
    DEFAULTS = {
        'flatline':  {'app': 'example.py', 'freeze_threshold': '2.5',
                      'poll_interval': '1.0', 'poll_fail_limit': '3'},
        'database':  {'enabled': 'false', 'host': '127.0.0.1', 'port': '3306',
                      'user': 'root', 'password': '', 'database': 'flatline'},
    }

    def __init__(self, path: str | Path = CFG_PATH):
        self._path = Path(path)
        self._cfg  = configparser.ConfigParser()
        for s, kv in self.DEFAULTS.items():
            self._cfg[s] = dict(kv)
        if self._path.exists():
            self._cfg.read(str(self._path), encoding='utf-8')

    def save_defaults(self) -> None:
        if self._path.exists():
            return
        self._path.write_text(
            '; Flatline Debugger Configuration\n'
            '; https://flatline.triodesktop.com/\n\n'
            '[flatline]\n'
            '; Script to launch and supervise\n'
            'app = example.py\n\n'
            '; Seconds of heartbeat silence before declaring a freeze\n'
            'freeze_threshold = 2.5\n\n'
            '; proc.poll() check interval in seconds (poll mode only)\n'
            'poll_interval = 1.0\n\n'
            '; Consecutive poll() failures before crash console opens\n'
            'poll_fail_limit = 3\n\n'
            '[database]\n'
            '; enabled=true to use MariaDB heartbeat logging.\n'
            '; If disabled or connection fails, proc.poll() is used.\n'
            'enabled = false\n'
            'host = 127.0.0.1\nport = 3306\nuser = root\npassword = \ndatabase = flatline\n',
            encoding='utf-8')

    @property
    def app(self) -> Path:
        return Path(self._cfg.get('flatline', 'app', fallback='example.py'))
    @property
    def freeze_threshold(self) -> float:
        return self._cfg.getfloat('flatline', 'freeze_threshold', fallback=2.5)
    @property
    def poll_interval(self) -> float:
        return self._cfg.getfloat('flatline', 'poll_interval', fallback=1.0)
    @property
    def poll_fail_limit(self) -> int:
        return self._cfg.getint('flatline', 'poll_fail_limit', fallback=3)
    @property
    def db_enabled(self) -> bool:
        return self._cfg.getboolean('database', 'enabled', fallback=False)
    @property
    def db_host(self) -> str:
        return self._cfg.get('database', 'host', fallback='127.0.0.1')
    @property
    def db_port(self) -> int:
        return self._cfg.getint('database', 'port', fallback=3306)
    @property
    def db_user(self) -> str:
        return self._cfg.get('database', 'user', fallback='root')
    @property
    def db_password(self) -> str:
        return self._cfg.get('database', 'password', fallback='')
    @property
    def db_name(self) -> str:
        return self._cfg.get('database', 'database', fallback='flatline')


# ══════════════════════════════════════════════════════════════════════════
# Database (optional MariaDB heartbeat logger)
# ══════════════════════════════════════════════════════════════════════════

class FlatlineDatabase:
    """
    Optional MariaDB integration. All methods silently no-op when unavailable.
    Schema is created automatically on first successful connection.
    """
    def __init__(self, cfg: FlatlineConfig):
        self._cfg  = cfg
        self._conn = None
        self._lock = threading.Lock()
        self.available = False
        if cfg.db_enabled:
            self._connect()

    def _connect(self) -> None:
        try:
            import pymysql
            self._conn = pymysql.connect(
                host=self._cfg.db_host, port=self._cfg.db_port,
                user=self._cfg.db_user, password=self._cfg.db_password,
                database=self._cfg.db_name, connect_timeout=2, autocommit=True)
            self.available = True
            self._schema()
            print(f'[Flatline] DB connected  {self._cfg.db_host}:{self._cfg.db_port}', flush=True)
        except Exception as e:
            print(f'[Flatline] DB unavailable ({e}) — using proc.poll()', flush=True)

    def _schema(self) -> None:
        if not (self.available and self._conn):
            return
        try:
            with self._conn.cursor() as c:
                c.execute("""CREATE TABLE IF NOT EXISTS flatline_heartbeat (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    ts DOUBLE NOT NULL,
                    reason VARCHAR(64) DEFAULT 'TICK',
                    caller VARCHAR(128) DEFAULT '',
                    pid INT DEFAULT 0,
                    created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        except Exception:
            self.available = False

    def log(self, reason: str, caller: str, pid: int, ts: float) -> None:
        if not (self.available and self._conn):
            return
        try:
            with self._lock:
                with self._conn.cursor() as c:
                    c.execute(
                        'INSERT INTO flatline_heartbeat(ts,reason,caller,pid) VALUES(%s,%s,%s,%s)',
                        (ts, reason[:64], caller[:128], pid))
        except Exception:
            self.available = False

    def last(self) -> dict | None:
        if not (self.available and self._conn):
            return None
        try:
            with self._lock:
                with self._conn.cursor() as c:
                    c.execute('SELECT ts,reason,caller,pid FROM flatline_heartbeat ORDER BY id DESC LIMIT 1')
                    row = c.fetchone()
                    return {'ts': row[0], 'reason': row[1], 'caller': row[2], 'pid': row[3]} if row else None
        except Exception:
            self.available = False
            return None


# ── Helpers ────────────────────────────────────────────────────────────────

def _log(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8', errors='replace') as f:
            f.write(str(text or '').rstrip('\n') + '\n')
    except Exception:
        pass


def _md5(path) -> str:
    try:
        return hashlib.md5(open(path, 'rb').read()).hexdigest()[:8]
    except Exception:
        return '????????'


def _safe_repr(v: Any, limit: int = 400) -> str:
    try:
        r = repr(v)
    except Exception as e:
        r = f'<repr:{e}>'
    return r[:limit] + '…' if len(r) > limit else r


# ══════════════════════════════════════════════════════════════════════════
# Status block renderer
# ══════════════════════════════════════════════════════════════════════════

_STATUS_W = 50   # width of the static header lines

def _status_header() -> str:
    """
    Two static lines printed once when the child launches.
    Only the spinner line below them is updated in-place.

    ──────────────────────────────────────────────────
         Press  D  to enter debug console
    ──────────────────────────────────────────────────
    [/] Flatline[poll]  hb=12:07:13.451234  TICK
    """
    bar = '─' * _STATUS_W
    return f'{bar}\n{"Press  D  to enter debug console".center(_STATUS_W)}\n{bar}'


def _spinner_line(spinner: str, mode: str, tod: str, reason: str) -> str:
    """Single line updated in-place via \\r."""
    return f'[{spinner}] Flatline[{mode}]  hb={tod}  {reason}'


# ══════════════════════════════════════════════════════════════════════════
# Flatline — main supervisor
# ══════════════════════════════════════════════════════════════════════════

class Flatline:
    """
    Python process supervisor and crash debugger. Always-on supervision.

    The live status block shows heartbeat state while the child runs.
    Press D at any time to open the debug console manually — no crash needed.
    The same console also opens automatically on crash or freeze.

    Heartbeat modes (auto-selected):
      DB mode:   MariaDB available → beat() stored → freeze via DB timestamps
      Poll mode: DB unavailable → proc.poll() async daemon thread →
                 lastPolled = microtime of last success → freeze via lastPolled
    """

    def __init__(self, config: FlatlineConfig | None = None):
        self._cfg  = config or FlatlineConfig()
        self.enabled = True

        # Child state
        self.child:            subprocess.Popen | None = None
        self.childPid:         int = 0
        self.childExitCode:    int | None = None
        self.childArgs:        list[str] = []
        self.childMd5Short:    str = ''
        self.childControlPort: int = 0

        # Heartbeat / poll state
        self.lastHeartbeat:       float = time.time()
        self.lastHeartbeatReason: str   = 'INIT'
        self.lastHeartbeatCaller: str   = ''
        self.lastProcessLoop:     float = time.time()
        self.lastProcessReason:   str   = 'INIT'
        self.lastPolled:          float = time.time()
        self._heartbeatEverFired: bool  = False
        self._pollFailCount:      int   = 0

        # Thresholds
        self._freezeThreshold = self._cfg.freeze_threshold
        self._pollInterval    = self._cfg.poll_interval
        self._pollFailLimit   = self._cfg.poll_fail_limit

        # Threading primitives
        self._lock               = threading.RLock()
        self._crashInfo: dict    = {}
        self._preStackText       = ''
        self._preVarsText        = ''
        self._consoleWake        = threading.Event()
        self._consoleDone        = threading.Event()
        self._consoleStop        = threading.Event()
        self._statusStop         = threading.Event()
        self._pollStop           = threading.Event()
        self._keyStop            = threading.Event()   # stops keyboard reader
        self._inCrashConsole     = False
        self._dismissedSig       = ''
        self._lastOpenedSig      = ''
        self._consoleSnooze      = 0.0
        self._lastFreezeDump     = 0.0

        # Thread handles
        self._watchdogThread     = None
        self._consoleThread      = None
        self._relayThread        = None
        self._replThread         = None
        self._statusThread       = None
        self._keyThread          = None
        self._snapshotThread     = None
        self._actionThread       = None
        self._pollThread         = None
        self._connMonThread      = None
        self._connMonStop        = threading.Event()
        self._connMonEnabled     = False
        self._lastConnDigest     = ''

        # Relay
        self._relayServer  = None
        self._relayPort    = 0
        self._relayToken   = f'{os.getpid()}-{time.time_ns()}'
        self._replServer   = None

        # Status block
        self._spinnerFrames   = ['|', '/', '-', '\\']
        self._spinnerIndex    = 0
        self._statusLineCount = 0   # how many lines the current block occupies

        # Database
        self._db    = FlatlineDatabase(self._cfg)
        self._useDB = self._db.available
        mode = f'DB ({self._cfg.db_host})' if self._useDB else 'proc.poll()'
        print(f'[Flatline] Heartbeat mode: {mode}', flush=True)

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self) -> 'Flatline':
        """Start all background threads. Call before launch()."""
        with self._lock:
            if self._relayThread is None:
                self._startRelay()
            if self._watchdogThread is None:
                self._watchdogThread = threading.Thread(
                    target=self._wrap(self._watchdog, 'Watchdog'),
                    name='FlatlineWatchdog', daemon=False)
                self._watchdogThread.start()
                print(f'[Flatline] Watchdog  freeze={self._freezeThreshold}s  '
                      f'mode={"DB" if self._useDB else "poll"}', flush=True)
            if self._consoleThread is None:
                self._consoleThread = threading.Thread(
                    target=self._wrap(self._consoleLoop, 'Console'),
                    name='FlatlineConsole', daemon=False)
                self._consoleThread.start()
            if self._statusThread is None:
                self._statusStop.clear()
                self._statusThread = threading.Thread(
                    target=self._wrap(self._statusLoop, 'Status'),
                    name='FlatlineStatus', daemon=True)
                self._statusThread.start()
            if self._replThread is None:
                self._startRepl()
        return self

    def launch(self, argv: list[str]) -> subprocess.Popen:
        """
        Launch target script as supervised subprocess.
        stdout/stderr are piped to error.log.
        In poll mode, starts async non-blocking poll daemon thread.
        Also starts the keyboard reader thread (D key → debug console).
        """
        env = os.environ.copy()
        env.update({
            'FLATLINE_ENABLED': '1', 'FLATLINE_HOST': '127.0.0.1',
            'FLATLINE_PORT': str(self._relayPort), 'FLATLINE_TOKEN': self._relayToken,
            # Legacy gtp.py compat
            'TRIO_DEBUGGER_ENABLED': '1', 'TRIO_DEBUGGER_HOST': '127.0.0.1',
            'TRIO_DEBUGGER_PORT': str(self._relayPort), 'TRIO_DEBUGGER_TOKEN': self._relayToken,
            'PYTHONUNBUFFERED': '1',
        })
        self.childArgs = list(argv)
        self._consoleStop.clear()
        self._pollStop.clear()
        self._keyStop.clear()
        self._pollFailCount = 0

        popen_kw = {
            'cwd': str(HERE), 'env': env, 'stdin': subprocess.DEVNULL,
            'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE,
            'text': True, 'encoding': 'utf-8', 'errors': 'replace', 'bufsize': 1,
        }
        proc = subprocess.Popen([sys.executable] + argv, **popen_kw)

        self.child            = proc
        self.childPid         = int(proc.pid)
        self.childControlPort = 0
        self.childExitCode    = None
        self.lastPolled       = time.time()
        self.lastHeartbeat    = time.time()
        self._heartbeatEverFired = False

        if proc.stdout:
            self._pump(proc.stdout, 'stdout')
        if proc.stderr:
            self._pump(proc.stderr, 'stderr')

        if not self._useDB:
            self._startPollThread()

        # Start keyboard reader (catches 'D' for manual debug entry)
        self._keyThread = threading.Thread(
            target=self._wrap(self._keyReader, 'KeyReader'),
            name='FlatlineKeyReader', daemon=True)
        self._keyThread.start()

        _log(LOG_ERROR, f'[Flatline] Child launched  pid={self.childPid}  argv={argv[0]}')
        print(f'[Flatline] Child launched  pid={self.childPid}', flush=True)
        return proc

    def wait(self) -> int:
        """Block until child exits. Opens debug console on non-zero exit."""
        proc = self.child
        if proc is None:
            return 1
        try:
            while True:
                code = proc.poll()
                if code is not None:
                    self.childExitCode = int(code)
                    self._keyStop.set()
                    if int(code) != 0 and not self._inCrashConsole:
                        self._openConsole({
                            'type_name': 'ChildExit',
                            'message':   f'Process exited with code {code}',
                            'thread':    threading.current_thread().name,
                            'timestamp': time.time(),
                        })
                    if int(code) != 0:
                        self._consoleDone.wait()
                    return int(code)
                time.sleep(0.05)
        finally:
            pass

    # ── Heartbeat touch methods ─────────────────────────────────────────────

    def beat(self, reason: str = 'TICK', caller: str = '') -> None:
        """Record a named heartbeat with microtime. Rate-limited 50ms."""
        now    = time.time()
        reason = str(reason or 'TICK').strip().upper()
        if not caller:
            try:
                fr  = sys._getframe(1)
                loc = fr.f_locals
                caller = type(loc['self']).__name__ if 'self' in loc and loc['self'] is not self else fr.f_code.co_name
            except Exception:
                caller = ''
        caller = str(caller or '').strip()
        key = f'{reason}|{caller}'
        if key == getattr(self, '_lastBeatKey', '') and (now - float(getattr(self, '_lastBeatTime', 0))) < 0.05:
            return
        self._lastBeatKey         = key
        self._lastBeatTime        = now
        self.lastHeartbeat        = now
        self.lastHeartbeatReason  = reason
        self.lastHeartbeatCaller  = caller
        self.lastPolled           = now
        self._heartbeatEverFired  = True
        self._db.log(reason, caller, os.getpid(), now)

    def touchHeartbeat(self, reason: str = 'HEARTBEAT',
                       caller: str = '', timestamp: float = 0.0) -> None:
        """Update heartbeat from relay message (thread-safe)."""
        with self._lock:
            ts = float(timestamp or time.time())
            self.lastHeartbeat       = ts
            self.lastHeartbeatReason = str(reason or 'HEARTBEAT')
            self.lastHeartbeatCaller = str(caller or '')
            self.lastPolled          = ts
            self._heartbeatEverFired = True
        self._db.log(reason, caller, self.childPid, float(timestamp or time.time()))

    def touchProcessLoop(self, reason: str = 'PROCESS') -> None:
        with self._lock:
            self.lastProcessLoop   = time.time()
            self.lastProcessReason = str(reason or 'PROCESS')

    # ── Process control ─────────────────────────────────────────────────────

    def kill(self) -> str:
        return self._term(force=True)

    def terminate(self) -> str:
        return self._term(force=False)

    def restart(self) -> str:
        proc = self.child
        if proc is not None and proc.poll() is None:
            self._term(force=True)
            time.sleep(0.3)
        self._heartbeatEverFired = False
        self._pollFailCount      = 0
        self.childExitCode       = None
        self._dismissedSig       = ''
        self._lastOpenedSig      = ''
        self.lastPolled          = time.time()
        try:
            self.launch(self.childArgs)
            if not (self._watchdogThread and self._watchdogThread.is_alive()):
                self._consoleStop.clear()
                self._watchdogThread = threading.Thread(
                    target=self._wrap(self._watchdog, 'Watchdog'),
                    name='FlatlineWatchdog', daemon=False)
                self._watchdogThread.start()
            return f'[Flatline] Restarted  pid={self.childPid}'
        except Exception as e:
            return f'[Flatline] Restart failed: {e}'

    def shutdown(self) -> None:
        """Stop all background threads cleanly."""
        self._statusStop.set()
        self._clearStatus()
        self._consoleStop.set()
        self._consoleWake.set()
        self._connMonStop.set()
        self._pollStop.set()
        self._keyStop.set()
        for attr in ('_replServer', '_relayServer'):
            s = getattr(self, attr, None)
            if s:
                try: s.shutdown()
                except Exception: pass
                try: s.server_close()
                except Exception: pass
        me = threading.current_thread()
        for attr in ('_replThread', '_relayThread', '_watchdogThread',
                     '_consoleThread', '_statusThread', '_pollThread'):
            t = getattr(self, attr, None)
            if t and t is not me:
                try: t.join(timeout=0.75)
                except Exception: pass

    # ── FLATLINED report ────────────────────────────────────────────────────

    def flatlined(self) -> str:
        """
        Build: [FLATLINED TODAY @ 12:07:13.451234 after RESIZE in Flatline::ThemeModal]
        Uses DB timestamp if available, otherwise lastHeartbeat (which mirrors lastPolled
        in poll mode).
        """
        import datetime as _dt
        ts     = self.lastHeartbeat
        reason = self.lastHeartbeatReason or 'TICK'
        caller = self.lastHeartbeatCaller or ''
        if self._useDB:
            row = self._db.last()
            if row and float(row['ts']) > ts:
                ts, reason, caller = float(row['ts']), row['reason'], row['caller']
        dt  = _dt.datetime.fromtimestamp(float(ts or time.time()))
        tod = dt.strftime('%H:%M:%S') + f'.{dt.microsecond:06d}'
        csuf = f' in Flatline::{caller}' if caller else ''
        return f'[FLATLINED TODAY @ {tod} after {reason}{csuf}]'

    # ══════════════════════════════════════════════════════════════════════
    # Keyboard reader — catches D key while child is running
    # ══════════════════════════════════════════════════════════════════════

    def _keyReader(self) -> None:
        """
        Daemon thread. Reads single keypresses from stdin without blocking
        the status loop. When 'D' or 'd' is pressed, opens the debug console.

        Windows:  uses msvcrt.kbhit() / msvcrt.getch() — no deps
        Linux/Mac: reads stdin one char at a time in raw mode via select
        """
        if sys.platform == 'win32':
            self._keyReaderWindows()
        else:
            self._keyReaderPosix()

    def _keyReaderWindows(self) -> None:
        """Windows keyboard reader using msvcrt (no tty manipulation needed)."""
        try:
            import msvcrt
        except ImportError:
            return
        while not self._keyStop.is_set():
            try:
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    # Handle extended/arrow keys (two-byte sequences starting with 0 or 0xe0)
                    if ch in (b'\x00', b'\xe0'):
                        msvcrt.getch()  # consume second byte, ignore
                        continue
                    try:
                        key = ch.decode('utf-8', 'replace').lower()
                    except Exception:
                        continue
                    if key == 'd' and not self._inCrashConsole:
                        self._openManualDebug()
                else:
                    time.sleep(0.05)
            except Exception:
                time.sleep(0.1)

    def _keyReaderPosix(self) -> None:
        """
        Linux/Mac keyboard reader. Uses select() to poll stdin non-blockingly
        so the terminal stays in normal (cooked) mode — no tty.setraw() needed.
        One character is read at a time. Works when stdin is a TTY.
        """
        import select
        while not self._keyStop.is_set():
            try:
                if not sys.stdin.isatty():
                    time.sleep(0.2)
                    continue
                ready, _, _ = select.select([sys.stdin], [], [], 0.05)
                if ready:
                    ch = sys.stdin.read(1)
                    if ch.lower() == 'd' and not self._inCrashConsole:
                        self._openManualDebug()
            except Exception:
                time.sleep(0.1)

    def _openManualDebug(self) -> None:
        """
        Called when user presses D. Opens the debug console with a
        'ManualDebug' info dict (not a crash) so they can inspect freely.
        """
        self._openConsole({
            'type_name': 'ManualDebug',
            'message':   'Manually entered debug console (D key)',
            'thread':    'KeyReader',
            'timestamp': time.time(),
        })

    # ══════════════════════════════════════════════════════════════════════
    # Poll-mode async thread
    # ══════════════════════════════════════════════════════════════════════

    def _startPollThread(self) -> None:
        """Start async non-blocking proc.poll() daemon thread (poll mode only)."""
        self._pollStop.clear()
        self._pollThread = threading.Thread(
            target=self._wrap(self._pollLoop, 'Poll'),
            name='FlatlinePoll', daemon=True)
        self._pollThread.start()

    def _pollLoop(self) -> None:
        """
        Non-blocking poll daemon. On success: lastPolled = microtime.now().
        On child exit: record and open crash console if non-zero.
        """
        while not self._pollStop.wait(self._pollInterval):
            proc = self.child
            if proc is None:
                continue
            code = proc.poll()
            if code is None:
                # Child alive — update microtime
                self.lastPolled          = time.time()
                self.lastHeartbeat       = self.lastPolled
                self._heartbeatEverFired = True
                self._pollFailCount      = 0
            else:
                if self.childExitCode is None:
                    self.childExitCode = int(code)
                    _log(LOG_ERROR, f'[Flatline] Child exited code={code}')
                    if int(code) != 0:
                        self._openConsole({
                            'type_name': 'ChildExit',
                            'message':   f'Process exited with code {code}',
                            'thread':    'FlatlinePoll',
                            'timestamp': time.time(),
                        })
                break

    # ══════════════════════════════════════════════════════════════════════
    # Relay server
    # ══════════════════════════════════════════════════════════════════════

    def _startRelay(self) -> None:
        """TCP relay — child connects and sends JSON heartbeat events."""
        dbg = self
        class _H(socketserver.StreamRequestHandler):
            def handle(self):
                while True:
                    try: raw = self.rfile.readline()
                    except (ConnectionResetError, OSError): break
                    if not raw: break
                    try: payload = json.loads(raw.decode('utf-8', 'replace'))
                    except Exception: continue
                    dbg._relayMsg(payload)
        class _S(socketserver.ThreadingTCPServer):
            allow_reuse_address = True; daemon_threads = True
        self._relayServer = _S(('127.0.0.1', 0), _H)
        self._relayPort   = int(self._relayServer.server_address[1])
        self._relayThread = threading.Thread(
            target=self._wrap(lambda: self._relayServer.serve_forever(poll_interval=0.5), 'Relay'),
            name='FlatlineRelay', daemon=True)
        self._relayThread.start()
        print(f'[Flatline] Relay  127.0.0.1:{self._relayPort}', flush=True)

    def _relayMsg(self, p: dict) -> None:
        try:
            if self._consoleStop.is_set(): return
            if str(p.get('token') or '') != self._relayToken: return
            kind = str(p.get('kind') or '').strip().lower()
            d    = p.get('payload') or {}
            if kind == 'attach':
                self.childPid         = int(d.get('pid') or self.childPid)
                self.childControlPort = int(d.get('control_port') or 0)
                print(f'[Flatline] Attached pid={self.childPid}', flush=True)
            elif kind == 'heartbeat':
                self.touchHeartbeat(str(d.get('reason') or 'HB'),
                    caller=str(d.get('caller') or ''),
                    timestamp=float(d.get('timestamp') or time.time()))
            elif kind == 'process':
                self.touchProcessLoop(str(d.get('reason') or 'PROCESS'))
            elif kind == 'warn':
                _log(LOG_ERROR, str(d.get('text') or ''))
            elif kind in {'die', 'fault', 'exception'}:
                _log(LOG_ERROR, str(d.get('text') or ''))
                self._openConsole({'type_name': str(d.get('type_name') or 'Fault'),
                    'message': str(d.get('message') or 'Remote fault'),
                    'traceback_text': str(d.get('traceback_text') or ''),
                    'thread': str(d.get('thread') or 'child'),
                    'timestamp': float(d.get('timestamp') or time.time())})
            elif kind == 'freeze':
                self._openConsole({'type_name': 'Freeze',
                    'message': str(d.get('message') or self.flatlined()),
                    'traceback_text': str(d.get('traceback_text') or ''),
                    'thread': str(d.get('thread') or 'child'),
                    'timestamp': float(d.get('timestamp') or time.time())})
        except Exception as e:
            _log(LOG_ERROR, f'[Flatline] Relay error: {e}')

    # ══════════════════════════════════════════════════════════════════════
    # Socket REPL
    # ══════════════════════════════════════════════════════════════════════

    def _startRepl(self) -> None:
        """TCP REPL on port 5050. Connect: nc localhost 5050"""
        dbg = self
        class _H(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b'Flatline REPL  (1=stack 2=vars s=status d=debug r=restart q=quit)\n')
                self.wfile.flush()
                while True:
                    try: raw = self.rfile.readline()
                    except (ConnectionResetError, OSError): break
                    if not raw: break
                    cmd = raw.decode('utf-8', 'replace').strip()
                    if not cmd: continue
                    out = dbg._dispatch(cmd, interactive=False)
                    self.wfile.write((out + '\n').encode('utf-8', 'replace'))
                    self.wfile.flush()
        class _S(socketserver.ThreadingTCPServer):
            allow_reuse_address = True; daemon_threads = True
        try:
            self._replServer = _S(('127.0.0.1', 5050), _H)
            self._replThread = threading.Thread(
                target=self._wrap(lambda: self._replServer.serve_forever(poll_interval=0.5), 'REPL'),
                name='FlatlineREPL', daemon=True)
            self._replThread.start()
            print('[Flatline] REPL  127.0.0.1:5050  (nc localhost 5050)', flush=True)
        except Exception as e:
            _log(LOG_ERROR, f'[Flatline] REPL disabled: {e}')

    # ══════════════════════════════════════════════════════════════════════
    # Watchdog
    # ══════════════════════════════════════════════════════════════════════

    def _watchdog(self) -> None:
        """Non-daemon watchdog. DB mode checks DB timestamps; poll mode checks lastPolled."""
        while not self._consoleStop.wait(self._pollInterval):
            proc = self.child
            if self._useDB and proc is not None:
                code = proc.poll()
                if code is not None and self.childExitCode is None:
                    self.childExitCode = int(code)
                    _log(LOG_ERROR, f'[Flatline] Child exited code={code}')
                    if int(code) != 0:
                        self._openConsole({'type_name': 'ChildExit',
                            'message': f'Process exited with code {code}',
                            'thread': 'FlatlineWatchdog', 'timestamp': time.time()})
                    continue

            if self.childExitCode is not None:
                self._consoleStop.set(); return

            if not self._heartbeatEverFired:
                continue

            now = time.time()
            stale = (now - max((self._db.last() or {}).get('ts', self.lastHeartbeat),
                               self.lastProcessLoop)) if self._useDB else (now - self.lastPolled)

            if stale >= self._freezeThreshold and (now - self._lastFreezeDump) >= self._freezeThreshold:
                if self._inCrashConsole:
                    continue
                self._lastFreezeDump = now
                _log(LOG_ERROR, f'[Flatline] Freeze  stale={stale:.1f}s')
                self._openConsole({'type_name': 'Freeze',
                    'message': self.flatlined() + f'  stalled={stale:.1f}s',
                    'thread': 'FlatlineWatchdog', 'timestamp': now})

    # ══════════════════════════════════════════════════════════════════════
    # Debug console
    # ══════════════════════════════════════════════════════════════════════

    def _openConsole(self, info: dict | None, block: bool = False) -> None:
        """Open the debug console. Always non-blocking unless block=True."""
        payload = dict(info or {})
        sig     = self._sig(payload)
        with self._lock:
            now = time.time()
            if sig and self._inCrashConsole and sig == self._lastOpenedSig:
                self._crashInfo = payload; return
            # ManualDebug: never deduplicated or snoozed
            if payload.get('type_name') != 'ManualDebug':
                if sig and sig == self._dismissedSig and now < self._consoleSnooze:
                    return
            self._crashInfo      = payload
            self._lastOpenedSig  = sig
            self._consoleDone.clear()
            self._inCrashConsole = True
            self._snapshotAsync(payload)
            self._consoleWake.set()
        if block:
            self._consoleDone.wait()

    def _consoleLoop(self) -> None:
        """Non-daemon thread owning the interactive debug console."""
        while not self._consoleStop.is_set():
            self._consoleWake.wait()
            self._consoleWake.clear()
            if self._consoleStop.is_set():
                break
            self._banner()
            while self._inCrashConsole and not self._consoleStop.is_set():
                try:
                    self._statusLineCount = 0
                    cmd = input('[Flatline] > ')
                except (EOFError, KeyboardInterrupt):
                    cmd = 'q'
                cmd = self._norm(cmd)
                if not cmd:
                    continue
                out = self._dispatch(cmd, interactive=True)
                if out:
                    self._emit(out)

    def _dispatch(self, cmd: str, interactive: bool = True) -> str:
        """Route a command to its handler."""
        low = self._norm(cmd).lower()
        if low in {'1', 'stacks', 'stack'}:
            return self._preStackText or self._frames()
        if low in {'2', 'vars', 'variables', 'locals'}:
            return self._preVarsText or self._locals()
        if low in {'3', 'close'}:
            return '[Flatline] Close requested' if self._sendCmd('close') else '[Flatline] Unavailable'
        if low in {'4', 'sigterm', 'term'}:
            self._async('SIGTERM', self.terminate); return '[Flatline] SIGTERM requested'
        if low in {'5', 'kill', 'force'}:
            self._async('KILL', self.kill); return '[Flatline] KILL requested'
        if low in {'6', 'refresh'}:
            self._banner(); return ''
        if low in {'7', 'cmd', 'shell'} and interactive:
            while True:
                try: sub = input('[Flatline:shell] > ').strip()
                except (EOFError, KeyboardInterrupt): sub = ''
                if not sub or sub.lower() in {'q', 'quit', 'exit', 'back'}:
                    return '[Flatline] Shell closed'
                return self._shell(sub)
        if low.startswith('7 ') or low.startswith('shell '):
            return self._shell(cmd.split(' ', 1)[1])
        if low in {'8', 'connections', 'net'}:
            return self._conns()
        if low in {'9', 'monitor', 'connwatch'}:
            return self._toggleMon()
        if low in {'d', 'debug'} and not self._inCrashConsole:
            self._openManualDebug(); return ''
        if low in {'r', 'restart', 'relaunch'}:
            if self.child is not None and self.child.poll() is None:
                return '[Flatline] Still running — kill first (5)'
            self._inCrashConsole = False; self._consoleDone.set()
            self._async('Restart', self.restart); return '[Flatline] Restarting...'
        if low in {'s', 'status'}:
            return self._statusText()
        if low in {'h', '?', 'help', 'menu'}:
            self._banner(); return ''
        if low in {'q', 'quit'}:
            is_manual = self._crashInfo.get('type_name') == 'ManualDebug'
            if not is_manual:
                self._dismissedSig  = self._sig(self._crashInfo)
                self._consoleSnooze = time.time() + max(self._freezeThreshold * 4, 12.0)
            self._inCrashConsole = False
            self._consoleDone.set()
            self._consoleWake.clear()
            return '[Flatline] Console closed'
        return f'[Flatline] Unknown: {cmd!r}  (h=help)'

    def _banner(self) -> None:
        """Print the debug console banner."""
        self._clearStatus()
        info   = self._crashInfo or {}
        mode   = f'DB ({self._cfg.db_host})' if self._useDB else 'proc.poll()'
        is_manual = info.get('type_name') == 'ManualDebug'
        ts_line   = (f'  lastPolled : {self.lastPolled:.6f}'
                     if not self._useDB else f'  lastHB     : {self.lastHeartbeat:.6f}')

        title = '  Flatline Debug Console  ' if is_manual else '  Flatline — Child Process Event  '

        lines = [
            '', '╔' + '═' * 62 + '╗',
            '║' + title.center(62) + '║',
            '╠' + '═' * 62 + '╣',
            f"║  {self.flatlined():<60}║",
            f"║  {'Exception : ' + info.get('type_name','?') + ': ' + info.get('message',''):<60}║",
            f"║  {'Parent PID: ' + str(os.getpid()) + '   Child PID: ' + str(self.childPid):<60}║",
            f"║  {'Mode      : ' + mode:<60}║",
            f"║  {ts_line.strip():<60}║",
            f"║  {'Time      : ' + time.strftime('%Y-%m-%d %H:%M:%S'):<60}║",
            '╠' + '═' * 62 + '╣',
            '║  1 stack   2 vars    3 close   4 SIGTERM  5 KILL        ║',
            '║  6 refresh 7 shell   8 conns   9 monitor                ║',
            '║  R restart S status  Q quit                              ║',
            '╚' + '═' * 62 + '╝',
        ]
        out = '\n'.join(lines)
        self._emit(out)
        _log(LOG_ERROR, out)

    # ══════════════════════════════════════════════════════════════════════
    # Status block (live spinner with D-key prompt)
    # ══════════════════════════════════════════════════════════════════════

    def _statusLoop(self) -> None:
        """
        Daemon thread.
        Prints the static header (separator + D-key prompt + separator) once,
        then updates only the spinner line in-place with \\r — no multi-line
        cursor gymnastics, no scroll spam.

        Layout (printed once then spinner updates last line):
            ──────────────────────────────────────────────────
                 Press  D  to enter debug console
            ──────────────────────────────────────────────────
            [/] Flatline[poll]  hb=12:07:13.451234  TICK
        """
        header_printed  = False
        last_spinner    = ''

        while not self._statusStop.wait(0.10):
            try:
                active = (
                    not self._inCrashConsole
                    and not self._consoleStop.is_set()
                    and self.child is not None
                    and self.child.poll() is None
                )

                if not active:
                    if header_printed:
                        # Erase spinner line then leave cursor on clean line
                        sys.stdout.write('\r' + ' ' * max(len(last_spinner), _STATUS_W) + '\r')
                        sys.stdout.flush()
                        header_printed = False
                        last_spinner   = ''
                    continue

                import datetime as _dt
                fr     = self._spinnerFrames[self._spinnerIndex % len(self._spinnerFrames)]
                self._spinnerIndex += 1
                ts     = _dt.datetime.fromtimestamp(float(self.lastHeartbeat or time.time()))
                tod    = ts.strftime('%H:%M:%S') + f'.{ts.microsecond:06d}'
                mode   = 'DB' if self._useDB else 'poll'
                reason = self.lastHeartbeatReason or 'INIT'
                line   = _spinner_line(fr, mode, tod, reason)

                if not header_printed:
                    # Print static header once, then drop to spinner line
                    sys.stdout.write(_status_header() + '\n')
                    sys.stdout.flush()
                    header_printed = True

                # Update spinner in-place on the same line
                pad = max(0, len(last_spinner) - len(line))
                sys.stdout.write('\r' + line + ' ' * pad)
                sys.stdout.flush()
                last_spinner = line

            except Exception:
                pass

    def _clearStatus(self) -> None:
        """Erase the spinner line (single \\r overwrite — no cursor movement needed)."""
        try:
            sys.stdout.write('\r' + ' ' * (_STATUS_W + 10) + '\r')
            sys.stdout.flush()
            self._statusLineCount = 0
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    # Stack / var snapshots
    # ══════════════════════════════════════════════════════════════════════

    def _snapshotAsync(self, info: dict) -> None:
        """Capture stack + vars in background thread, write to log files."""
        def _run():
            try:
                header = ['=' * 64, '  SNAPSHOT', '=' * 64,
                    f'  {self.flatlined()}',
                    f"  Exception: {info.get('type_name','?')}: {info.get('message','')}",
                    f'  PID: {self.childPid}  Mode: {"DB" if self._useDB else "poll"}',
                    f'  lastPolled: {self.lastPolled:.6f}', '']
                stack = '\n'.join(header) + self._frames()
                tb    = str(info.get('traceback_text') or '').strip()
                if tb:
                    stack += f'\nChild traceback:\n{tb}\n'
                loc = self._locals()
                self._preStackText = stack
                self._preVarsText  = loc
                _log(LOG_STACK, stack)
                _log(LOG_VARS,  loc)
                _log(LOG_ERROR, stack)
            except Exception as e:
                _log(LOG_ERROR, f'[Flatline] Snapshot failed: {e}')
        t = threading.Thread(target=self._wrap(_run, 'Snapshot'),
                             name='FlatlineSnapshot', daemon=True)
        self._snapshotThread = t
        t.start()

    def _frames(self) -> str:
        frames  = dict(sys._current_frames())
        threads = {t.ident: t for t in threading.enumerate()}
        mid     = getattr(threading.main_thread(), 'ident', None)
        out = [f'[Flatline] Stack  PID={os.getpid()}  threads={len(frames)}', '']
        for tid, frame in frames.items():
            thr  = threads.get(tid)
            name = getattr(thr, 'name', f'Thread-{tid}')
            out.append(f'  --- {name}  {tid}{"[MAIN]" if tid == mid else ""}{"[d]" if getattr(thr,"daemon",False) else ""} ---')
            try:
                for item in traceback.extract_stack(frame):
                    out.append(f'    {item.filename}:{item.lineno} in {item.name}')
                    if item.line:
                        out.append(f'      {item.line.strip()}')
            except Exception as e:
                out.append(f'    <stack failed: {e}>')
            out.append('')
        return '\n'.join(out).rstrip() + '\n'

    def _locals(self) -> str:
        lines = ['', '  -- Variables --', '']
        try:
            frame = dict(sys._current_frames()).get(
                getattr(threading.main_thread(), 'ident', None))
        except Exception as e:
            return f'<locals failed: {e}>\n'
        if frame:
            code = getattr(frame, 'f_code', None)
            lines.append(f'  {getattr(code,"co_filename","")}:{getattr(frame,"f_lineno",0)} in {getattr(code,"co_name","")}')
            for k, v in list(getattr(frame, 'f_locals', {}).items())[:120]:
                lines.append(f'    {k} = {_safe_repr(v)}')
        lines += [
            f'  childPid      = {self.childPid}',
            f'  childExit     = {self.childExitCode}',
            f'  mode          = {"DB" if self._useDB else "poll"}',
            f'  lastPolled    = {self.lastPolled:.6f}',
            f'  lastHB        = {self.lastHeartbeatReason} @ {self.lastHeartbeat:.6f}',
            f'  caller        = {self.lastHeartbeatCaller}',
            f'  pollFails     = {self._pollFailCount}',
        ]
        return '\n'.join(lines) + '\n'

    # ══════════════════════════════════════════════════════════════════════
    # Misc internals
    # ══════════════════════════════════════════════════════════════════════

    def _statusText(self) -> str:
        mode = f'DB ({self._cfg.db_host})' if self._useDB else 'proc.poll()'
        return '\n'.join(['', '=' * 64, '  Flatline Status', '=' * 64,
            f'  PID={os.getpid()}  childPid={self.childPid}  exit={self.childExitCode}',
            f'  mode={mode}', f'  lastPolled={self.lastPolled:.6f}',
            f'  lastHB={self.lastHeartbeatReason}@{self.lastHeartbeat:.6f}',
            f'  caller={self.lastHeartbeatCaller or "(none)"}',
            f'  pollFails={self._pollFailCount}  relay={self._relayPort}',
            '=' * 64, ''])

    def _sig(self, info: dict | None = None) -> str:
        p   = dict(info or {})
        tn  = str(p.get('type_name') or '')
        if tn == 'ManualDebug':
            return f'ManualDebug|{time.time()}'   # never deduped
        msg = re.sub(r'stalled=\S+', 'stalled=<t>', str(p.get('message') or '')) if tn.lower() == 'freeze' else str(p.get('message') or '')
        return '|'.join([tn, msg, str(p.get('thread') or ''), str(self.childPid)])

    def _norm(self, cmd: str) -> str:
        try: cmd = cmd.replace('\x00', '').replace('\r', '').replace('\n', '')
        except Exception: pass
        return ''.join(c for c in cmd.strip().strip("'\"`") if c.isprintable()).strip()

    def _emit(self, text: str) -> None:
        self._clearStatus()
        try: sys.stderr.write(str(text or '') + '\n'); sys.stderr.flush()
        except Exception: pass

    def _wrap(self, target, name: str):
        def _r():
            try: target()
            except Exception as e:
                _log(LOG_ERROR, f'[Flatline:{name}] {type(e).__name__}: {e}\n'
                     + ''.join(traceback.format_exception(type(e), e, e.__traceback__)))
        return _r

    def _async(self, label: str, fn) -> threading.Thread:
        def _r():
            try:
                res = fn()
                if res: self._emit(str(res))
            except Exception as e: _log(LOG_ERROR, f'[Flatline:{label}] {e}')
        t = threading.Thread(target=self._wrap(_r, label),
                             name=f'FlatlineAction:{label}', daemon=True)
        self._actionThread = t; t.start(); return t

    def _term(self, force: bool = False) -> str:
        proc = self.child
        if proc is None or proc.poll() is not None: return '[Flatline] No child.'
        try:
            if os.name == 'nt':
                args = ['taskkill', '/F', '/PID', str(proc.pid)] if force else ['taskkill', '/PID', str(proc.pid)]
                r = subprocess.run(args, capture_output=True, text=True, timeout=12)
                return f'[Flatline] {"KILL" if force else "SIGTERM"} rc={r.returncode}'
            if force: proc.kill(); return '[Flatline] KILL'
            proc.terminate(); return '[Flatline] SIGTERM'
        except Exception as e: return f'[Flatline] Error: {e}'

    def _sendCmd(self, command: str) -> bool:
        port = int(self.childControlPort or 0)
        if port <= 0: return False
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.75) as s:
                s.sendall((json.dumps({'token': self._relayToken, 'command': command,
                                       'timestamp': time.time()}) + '\n').encode())
            return True
        except Exception: return False

    def _shell(self, cmd: str) -> str:
        if not cmd.strip(): return '[Flatline] No command.'
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20)
            return f'[rc={r.returncode}] {cmd}\n' + ((r.stdout or '') + (r.stderr or '') or '[no output]')
        except Exception as e: return f'[Flatline] Error: {e}'

    def _conns(self) -> str:
        if os.name == 'nt':
            try:
                r = subprocess.run(['netstat', '-ano'], capture_output=True, text=True, timeout=20)
                return (r.stdout or '') + (r.stderr or '')
            except Exception as e: return f'[Flatline] netstat: {e}'
        for c in (['ss', '-tunap'], ['netstat', '-an']):
            try:
                r = subprocess.run(c, capture_output=True, text=True, timeout=20)
                if r.stdout.strip(): return r.stdout
            except Exception: pass
        return '[Flatline] Unavailable.'

    def _toggleMon(self) -> str:
        if self._connMonEnabled:
            self._connMonEnabled = False; self._connMonStop.set(); return '[Flatline] Monitor OFF'
        self._connMonStop = threading.Event(); self._connMonEnabled = True
        def _loop():
            while not self._connMonStop.wait(2.0):
                text = self._conns(); d = str(hash(text))
                if d != self._lastConnDigest:
                    self._lastConnDigest = d; _log(LOG_ERROR, '[Flatline] Net change\n' + text)
        t = threading.Thread(target=self._wrap(_loop, 'ConnMon'),
                             name='FlatlineConnMon', daemon=True)
        self._connMonThread = t; t.start(); return '[Flatline] Monitor ON'

    def _pump(self, pipe, label: str) -> threading.Thread:
        def _run():
            try:
                for line in pipe:
                    text = str(line or '').rstrip()
                    if text: _log(LOG_ERROR, f'[{label}] {text}')
            except Exception: pass
        t = threading.Thread(target=self._wrap(_run, f'Pump:{label}'),
                             name=f'FlatlinePump:{label}', daemon=True)
        t.start(); return t


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════

def _parse(argv: list[str]) -> dict:
    r = {'help': False, 'version': False, 'license': False,
         'config': str(CFG_PATH), 'app': None, 'app_args': []}
    i = 0
    while i < len(argv):
        tok = argv[i].strip(); low = tok.lower()
        if low in {'--help', '-h', '/?', '/help', 'man', '--man', '-?'}: r['help'] = True
        elif low in {'--version', '--ver', '-v', '/v', '/version'}: r['version'] = True
        elif low in {'--license', '/license'}: r['license'] = True
        elif low in {'--config', '-config', '/config'} and i+1 < len(argv): i += 1; r['config'] = argv[i]
        elif low in {'--app', '-app', '/app'} and i+1 < len(argv): i += 1; r['app'] = argv[i]
        elif low in {'-a', '--args', '/args'} and i+1 < len(argv): r['app_args'] = argv[i+1:]; break
        elif tok == '--': r['app_args'] = argv[i+1:]; break
        elif not tok.startswith('-') and not tok.startswith('/'):
            if r['app'] is None: r['app'] = tok
            else: r['app_args'].append(tok)
        i += 1
    return r


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = _parse(argv)

    if args['version']:
        print(f'Flatline v{VERSION}  ({BUILD_DATE})'); print(PROJECT); return 0
    if args['license']:
        print(MIT_LICENSE); return 0
    if args['help']:
        print(HELP_TEXT); return 0

    cfg = FlatlineConfig(args['config'])
    cfg.save_defaults()

    app_path = Path(args['app']) if args['app'] else (HERE / cfg.app)
    if not app_path.exists():
        print(f'[Flatline] ERROR: not found: {app_path}', file=sys.stderr)
        print(f'  Set app= in config.ini or: python flatline.py script.py', file=sys.stderr)
        print(f'  Help: python flatline.py --help', file=sys.stderr)
        return 2

    print(f'[Flatline] v{VERSION}  {BUILD_DATE}', flush=True)
    print(f'[Flatline] flatline.py  md5={_md5(__file__)}', flush=True)
    print(f'[Flatline] {app_path.name}  md5={_md5(app_path)}', flush=True)

    dbg = Flatline(config=cfg)
    dbg.start()

    def _si(s, f):
        print('\n[Flatline] Ctrl+C', flush=True)
        dbg.terminate(); dbg.shutdown(); sys.exit(0)
    try:
        signal.signal(signal.SIGINT, _si)
    except Exception:
        pass

    try:
        dbg.launch([str(app_path)] + list(args['app_args']))
    except Exception as e:
        print(f'[Flatline] Launch failed: {e}', file=sys.stderr)
        _log(LOG_ERROR, f'[Flatline] Launch failed: {e}')
        dbg.shutdown(); return 1

    try:
        return dbg.wait()
    finally:
        dbg.shutdown()


if __name__ == '__main__':
    raise SystemExit(main())

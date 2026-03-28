#!/usr/bin/env python3
"""
example.py — Flatline Usage Example
======================================
Demonstrates how to launch app.py under Flatline supervision.
Any arguments not consumed by this script are passed through to app.py.

Usage:
  python example.py                  Launch app.py with Flatline
  python example.py --help           Show help
  python example.py --version        Show version
  python example.py --app myapp.py   Supervise a different script
  python example.py -a --port 8080   Pass --port 8080 to the target app

Authors : Trent Tompkins <trenttompkins@gmail.com>
          GPT-5.4 Plus Thinking
          Claude Sonnet 4.6 (Anthropic)
Version : 1.0b  |  MIT License  |  https://github.com/tibberous/Flatline
"""
import sys
import os
import signal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ── Quick CLI ─────────────────────────────────────────────────────────────
_argv = sys.argv[1:]
_low  = [a.strip().lower() for a in _argv]

if any(x in _low for x in ('--help', '-h', '/?', '/help', 'man', '--man')):
    print(__doc__)
    print("Full documentation:  python flatline.py --help")
    print(f"Project:  https://github.com/tibberous/Flatline")
    sys.exit(0)

if any(x in _low for x in ('--version', '--ver', '-v', '/v')):
    from flatline import VERSION, BUILD_DATE, PROJECT
    print(f'Flatline Example v{VERSION}  ({BUILD_DATE})')
    print(PROJECT)
    sys.exit(0)

if any(x in _low for x in ('--license',)):
    from flatline import MIT_LICENSE
    print(MIT_LICENSE)
    sys.exit(0)

# ── Resolve target app ────────────────────────────────────────────────────
target   = HERE / 'app.py'
app_args = []

i = 0
while i < len(_argv):
    arg = _argv[i]
    low = arg.lower()
    if low in ('--app', '-app') and i + 1 < len(_argv):
        target = Path(_argv[i + 1]); i += 2; continue
    if low in ('-a', '--args', '/args') and i + 1 < len(_argv):
        app_args = _argv[i + 1:]; break
    if not arg.startswith('-') and arg.endswith('.py'):
        target = Path(arg)
    elif arg.startswith('-') and low not in ('--app',):
        app_args.append(arg)
    i += 1

if not target.exists():
    print(f"[example] Target not found: {target}", file=sys.stderr)
    print(f"[example] Usage: python example.py [--app script.py] [-a arg1 arg2]")
    sys.exit(2)

# ── Launch via Flatline ───────────────────────────────────────────────────
from flatline import Flatline, FlatlineConfig, _md5, VERSION, BUILD_DATE

print(f"Flatline Example — v{VERSION}  ({BUILD_DATE})")
print(f"Supervising  : {target}")
print(f"MD5          : {_md5(target)}")
print(f"Extra args   : {app_args or '(none)'}")
print()

cfg      = FlatlineConfig()
debugger = Flatline(config=cfg)
debugger.start()

print(f"[example] Relay port : {debugger._relayPort}")
print(f"[example] Socket REPL: nc localhost 5050")
print(f"[example] Ctrl+C to terminate child")
print()


def _sigint(sig, frame):
    print("\n[example] Ctrl+C — terminating...")
    debugger.terminate()
    debugger.shutdown()
    sys.exit(0)

signal.signal(signal.SIGINT, _sigint)

try:
    debugger.launch([str(target)] + app_args)
    exit_code = debugger.wait()
    print(f"\n[example] Child exited: {exit_code}")
    sys.exit(exit_code)
finally:
    debugger.shutdown()

Flatline Debugger — Plain Text README
======================================

FLATLINE - Python Process Debugger
===================================
Version 1.0b  |  2026-03-28  |  MIT License

Flatline supervises any Python script. It monitors heartbeats, detects
freezes, captures crash snapshots, and gives you an interactive console
the moment something goes wrong.

When a crash is detected:

    [FLATLINED TODAY @ 12:07:13.451234 after RESIZE in Flatline::ThemeModal]

Project  : https://github.com/tibberous/Flatline
Homepage : https://flatline.triodesktop.com/
Author   : Trent Tompkins <trenttompkins@gmail.com>
Phone    : (724) 431-5207
Support  : Custom PHP and Python development available
           https://trentontompkins.com/#section-curriculum-vitae


QUICK START
-----------

    python flatline.py app.py

Flatline always runs in supervisor mode. There is no --debug flag.


WHAT IT DOES
------------

  - Launches a Python subprocess (your app)
  - Monitors heartbeats via TCP relay (beat() events from child)
  - Detects freezes when heartbeats go stale (configurable threshold)
  - Opens an interactive crash console on crash or freeze
  - Captures stack trace and variable dump at moment of failure
  - Writes log files: stack_trace.log, variables.log, error.log

  DB mode (database.enabled=true in config.ini):
    Child sends beat() via TCP relay.
    Each beat is stored in MariaDB with microtime timestamp.
    Freeze detection uses DB timestamps.

  Poll mode (DB disabled or unavailable):
    proc.poll() runs in an async non-blocking daemon thread.
    lastPolled = microtime of last successful poll.
    If now() - lastPolled > freeze_threshold, crash console opens.
    No DB, no extra dependencies needed.


INSTALLATION
------------

    pip install PySide6          (for app.py demo only)
    pip install pymysql          (for MariaDB support, optional)


COMMAND LINE
------------

    python flatline.py app.py --debug        (no --debug needed, always on)
    python flatline.py                        reads app from config.ini
    python flatline.py script.py X Y         passes X Y to script.py
    python flatline.py -a X Y script.py      explicit args via -a
    python flatline.py --app script.py       specify target via --app
    python flatline.py --config myapp.ini    alternate config
    python flatline.py --version             print version
    python flatline.py --help                full help
    python flatline.py --license             MIT license


CONFIG FILE  (config.ini)
--------------------------

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


HEARTBEAT MODES
---------------

    DB mode (enabled=true, connection succeeds):
      Child calls beat(reason, caller) via TCP relay.
      Stored in MariaDB with microtime timestamp.
      Freeze detection: now() - DB_timestamp > freeze_threshold

    Poll mode (enabled=false OR connection fails):
      proc.poll() in async daemon thread every poll_interval seconds.
      lastPolled = microtime of last success.
      Freeze: now() - lastPolled > freeze_threshold
      Completely non-blocking. No DB required.


ADDING HEARTBEATS TO YOUR APP
------------------------------

    from source.remote_client import RemoteClient
    flatline = RemoteClient()   # reads env vars set by Flatline

    class MyWindow:
        def __init__(self):
            flatline.beat('INIT', 'MyWindow')

        def resizeEvent(self, event):
            flatline.beat('RESIZE', 'MyWindow')

beat() is rate-limited (same reason+caller within 50ms dropped).


LOG FILES  (always written)
---------------------------

    stack_trace.log   Stack frames captured at crash time
    variables.log     Variable dump captured at crash time
    error.log         All errors + child stdout + child stderr


CRASH CONSOLE KEYS
------------------

    1   Stack trace         2   Variable dump
    3   Graceful close      4   SIGTERM
    5   Force KILL          6   Refresh banner
    7   Shell command       8   Network connections
    9   Net monitor         R   Restart app
    S   Status              Q   Quit console

Live inspection (no crash needed):

    nc localhost 5050


FILE MAP
--------

    flatline.py          Main debugger (single-file distribution)
    app.py               Demo Qt app (code editor + output panel)
    example.py           Programmatic usage example
    config.ini           Configuration file
    help.html            Full HTML documentation
    LICENSE.txt          MIT license
    README.md            This file
    README.txt           Plain text copy

    source/
      remote_client.py   Add to your app to send beat() events
      __init__.py        Package marker

    flatline.jpg         Black background logo (badge use)
    logo.png             Transparent logo (badge use)


DEMO APP  (app.py)
------------------

  Left panel  : white monospaced code editor
  Right panel : black/green output console (full height)
  Run Code    : executes editor contents via exec()
  print()     : redirected to output panel
  About       : black background dialog with logo

  The default code counts down 10 steps at 0.5s intervals.
  Edit and add exceptions to test crash detection.


LICENSE
-------

    MIT License. Free to use, modify, distribute, sell.
    See LICENSE.txt or:  python flatline.py --license

    Copyright (c) 2026  Trent Tompkins <trenttompkins@gmail.com>
                        GPT-5.4 Plus Thinking
                        Claude Sonnet 4.6 (Anthropic)


SUPPORT
-------

    Trent Tompkins
    (724) 431-5207
    trenttompkins@gmail.com
    https://trentontompkins.com/#section-curriculum-vitae

    Available for custom PHP and Python development and support.
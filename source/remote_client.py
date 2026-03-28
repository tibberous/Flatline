#!/usr/bin/env python3
"""
source/remote_client.py — Flatline Child-Side Relay Client
============================================================
Runs inside the supervised child process. Sends beat() events and
error/freeze notifications back to the Flatline supervisor via TCP.

Copy this file into your project and use it like:
    from source.remote_client import RemoteClient
    flatline = RemoteClient()
    flatline.beat('INIT', 'MyWindow')
    flatline.beat('RESIZE', 'MyDialog')

All methods are safe to call when running standalone (no supervisor) —
they silently no-op so your app never needs try/except guards.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import socketserver
import threading
import time
import traceback


class RemoteClient:
    """
    Lightweight client that runs inside the child process.

    Reads connection parameters from environment variables:
      FLATLINE_ENABLED  — '1' to activate
      FLATLINE_HOST     — relay server host
      FLATLINE_PORT     — relay server port
      FLATLINE_TOKEN    — auth token (must match parent)

    Also supports legacy TRIO_DEBUGGER_* env vars for gtp.py compatibility.

    beat() is rate-limited: same reason+caller within 50ms is dropped.
    """

    def __init__(self):
        # Read env vars — FLATLINE_* preferred, TRIO_* as fallback
        self.enabled = (
            os.environ.get('FLATLINE_ENABLED', os.environ.get('TRIO_DEBUGGER_ENABLED', '0'))
        ).strip() == '1'
        self.host  = os.environ.get('FLATLINE_HOST',
                     os.environ.get('TRIO_DEBUGGER_HOST', '127.0.0.1'))
        self.token = os.environ.get('FLATLINE_TOKEN',
                     os.environ.get('TRIO_DEBUGGER_TOKEN', ''))
        try:
            self.port = int(os.environ.get('FLATLINE_PORT',
                            os.environ.get('TRIO_DEBUGGER_PORT', '0')) or '0')
        except ValueError:
            self.port = 0

        self.controlPort = 0
        self._queue      = queue.Queue()
        self._stop       = threading.Event()
        self._sender     = None
        self._lastBeatKey  = ''
        self._lastBeatTime = 0.0

        if self.enabled and self.port > 0 and self.token:
            self._startControl()
            self._sender = threading.Thread(
                target=self._senderLoop, name='FlatlineRemoteSender', daemon=True)
            self._sender.start()
            self._enq('attach', {'pid': os.getpid(), 'control_port': self.controlPort,
                                  'timestamp': time.time()})

    # ── Public ─────────────────────────────────────────────────────────────

    def beat(self, reason: str = 'TICK', caller: str = '') -> None:
        """
        Send a named heartbeat with microtime precision.
        Call this at key UI moments so crash reports are meaningful:
            flatline.beat('INIT',    'MyDialog')
            flatline.beat('RESIZE',  'MainWindow')
            flatline.beat('CLICK',   'SaveButton')
        Rate-limited: same reason+caller within 50ms is dropped.
        """
        if not self.enabled:
            return
        now    = time.time()
        reason = str(reason or 'TICK').strip().upper()
        caller = str(caller or '').strip()
        key    = f'{reason}|{caller}'
        if key == self._lastBeatKey and (now - self._lastBeatTime) < 0.05:
            return
        self._lastBeatKey  = key
        self._lastBeatTime = now
        self._enq('heartbeat', {'reason': reason, 'caller': caller,
                                 'pid': os.getpid(), 'timestamp': now})

    def touchProcessLoop(self, reason: str = 'PROCESS') -> None:
        """Update process-loop timestamp (call from your event loop)."""
        if not self.enabled:
            return
        now = time.time()
        if (now - float(getattr(self, '_lastProcessTime', 0))) < 0.2:
            return
        self._lastProcessTime = now
        self._enq('process', {'reason': reason, 'pid': os.getpid(), 'timestamp': now})

    def warn(self, context: str, error=None) -> None:
        """Send a non-fatal warning to the supervisor."""
        self._enq('warn', self._errPayload(context, error))

    def die(self, context: str, error=None) -> None:
        """Report a fatal error — triggers the crash console on the parent."""
        self._enq('die', self._errPayload(context, error))

    def onFreeze(self, seconds: float = 0.0, phase: str = 'running',
                 stack_text: str = '') -> None:
        """Report a UI freeze to the supervisor."""
        msg = f'UI thread stalled for {seconds:.1f}s ({phase})'
        self._enq('freeze', {'message': msg, 'text': stack_text or msg,
                              'type_name': 'ChildFreeze', 'traceback_text': stack_text,
                              'thread': 'watchdog', 'timestamp': time.time()})

    # ── Internal ───────────────────────────────────────────────────────────

    def _enq(self, kind: str, payload: dict) -> None:
        if not (self.enabled and self.port > 0 and self.token):
            return
        try:
            self._queue.put_nowait({'token': self.token, 'kind': str(kind),
                                    'payload': dict(payload)})
        except queue.Full:
            pass

    def _senderLoop(self) -> None:
        while not self._stop.wait(0.05):
            try:
                msg = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                data = (json.dumps(msg, ensure_ascii=False) + '\n').encode('utf-8', 'replace')
                with socket.create_connection((self.host, self.port), timeout=0.75) as s:
                    s.sendall(data)
            except Exception:
                pass

    def _startControl(self) -> None:
        """Start the control server so parent can send close/ping commands."""
        client = self

        class _H(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    raw = self.rfile.readline()
                    p   = json.loads(raw.decode('utf-8', 'replace'))
                    if str(p.get('token') or '') != client.token:
                        return
                    cmd = str(p.get('command') or '').strip().lower()
                    client._handleCmd(cmd)
                except Exception:
                    pass

        class _S(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads      = True

        try:
            srv = _S(('127.0.0.1', 0), _H)
            self.controlPort = int(srv.server_address[1])
            threading.Thread(target=srv.serve_forever,
                             name='FlatlineRemoteControl', daemon=True).start()
        except Exception:
            self.controlPort = 0

    def _handleCmd(self, command: str) -> None:
        if command == 'close':
            try:
                from PySide6.QtWidgets import QApplication
                from PySide6.QtCore    import QTimer
                app = QApplication.instance()
                if app:
                    QTimer.singleShot(0, app.quit)
            except Exception:
                pass
        elif command == 'ping':
            self.beat('CONTROL_PING', 'RemoteClient')

    def _errPayload(self, context: str, error=None) -> dict:
        p = {'context': str(context or 'runtime'),
             'type_name': type(error).__name__ if isinstance(error, BaseException) else 'Warning',
             'message': str(error or ''),
             'thread': __import__('threading').current_thread().name,
             'timestamp': time.time()}
        if isinstance(error, BaseException):
            tb = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
            p['traceback_text'] = tb.rstrip()
            p['text']           = tb.rstrip()
        else:
            p['traceback_text'] = ''
            p['text']           = f"[{context}] {p['message']}"
        return p

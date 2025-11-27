# refresh.py — periodic shell-based refresh utilities
# This file is part of the batocera distribution (https://batocera.org).
# Copyright (c) 2025 lbrpdx for the Batocera team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License
# as published by the Free Software Foundation, version 3.
#
# YOU MUST KEEP THIS HEADER AS IT IS
import threading
from gi.repository import GLib
from shell import run_shell_capture

DEFAULT_REFRESH_SEC = 0  # no refresh by default (set refresh="1.0" on elements that need updates ever 1sec)

class RefreshTask:
    def __init__(self, widget_update_fn, cmd: str, interval_sec: float):
        self.widget_update_fn = widget_update_fn
        self.cmd = cmd
        self.interval_ms = max(250, int(interval_sec * 1000))
        self._timer_id = None
        self._active = False

    def start(self):
        if self._active:
            return
        self._active = True
        self._schedule_tick(immediate=True)

    def stop(self):
        self._active = False

    def _schedule_tick(self, immediate=False):
        delay = 1 if immediate else self.interval_ms
        self._timer_id = GLib.timeout_add(delay, self._tick)

    def _tick(self):
        def work():
            result = run_shell_capture(self.cmd)
            GLib.idle_add(self.widget_update_fn, result)
        threading.Thread(target=work, daemon=True).start()
        if self._active:
            self._schedule_tick(immediate=False)
        return False

class Debouncer:
    def __init__(self, min_interval_ms: int):
        self.min_ms = max(1, int(min_interval_ms))
        self._last = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        import time
        now = time.monotonic() * 1000.0
        with self._lock:
            last = self._last.get(key, 0.0)
            if (now - last) >= self.min_ms:
                self._last[key] = now
                return True
            return False


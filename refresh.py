# refresh.py — periodic shell-based refresh utilities
# This file is part of the batocera distribution (https://batocera.org).
# Copyright (c) 2025-2026 lbrpdx for the Batocera team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License
# as published by the Free Software Foundation, version 3.
#
# YOU MUST KEEP THIS HEADER AS IT IS
import queue
import threading
from gi.repository import GLib
from shell import run_shell_capture_cached

DEFAULT_REFRESH_SEC = 0  # no refresh by default (set refresh="1.0" on elements that need updates ever 1sec)

# Shared pool of persistent daemon workers for RefreshTask ticks and one-shot
# off-main-thread actions. Workers block on the queue (idle pool costs nothing).
# Queue items: ("__call__", fn) runs fn(); (cmd, callback) runs
# run_shell_capture_cached(cmd) then idle_add(callback, result).
_WORKER_COUNT = 4
_work_queue: "queue.Queue[tuple]" = queue.Queue()

def _worker_loop():
    while True:
        item = _work_queue.get()
        try:
            if len(item) == 2 and item[0] == "__call__":
                _, fn = item
                fn()
            else:
                cmd, callback = item
                result = run_shell_capture_cached(cmd)
                GLib.idle_add(callback, result)
        except Exception:
            pass
        finally:
            _work_queue.task_done()

for _i in range(_WORKER_COUNT):
    threading.Thread(target=_worker_loop, daemon=True).start()


def run_off_main_thread(fn):
    """
    Run *fn* on a shared background worker (replaces ad-hoc
    threading.Thread per action). *fn* must marshal UI work back via
    GLib.idle_add. Fire-and-forget.
    """
    _work_queue.put(("__call__", fn))

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
        _work_queue.put((self.cmd, self.widget_update_fn))
        if self._active:
            self._schedule_tick(immediate=False)
        return False

class ExpandRefreshTask:
    """Like RefreshTask, but re-runs an already-bound update_fn() directly
    on the main loop each tick instead of dispatching a shell command
    through the worker pool (used for ${...} string expansion)."""
    def __init__(self, update_fn, interval_sec: float):
        self.update_fn = update_fn
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
        try:
            GLib.idle_add(self.update_fn)
        except Exception:
            pass
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


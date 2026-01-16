# shell.py — safe shell helpers and display utilities
# This file is part of the batocera distribution (https://batocera.org).
# Copyright (c) 2025-2026 lbrpdx for the Batocera team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License
# as published by the Free Software Foundation, version 3.
#
# YOU MUST KEEP THIS HEADER AS IT IS
import os
import shlex
import subprocess

import gi
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk

# Default to disable AT-SPI DBus chatter for performance/stability
os.environ.setdefault("NO_AT_BRIDGE", "1")

def normalize_bool_str(s: str) -> bool:
    if s is None:
        return False
    s = s.strip().lower()
    return s in ("1", "true", "on", "yes", "enabled")

def expand_command_string(s: str) -> str:
    """
    Expand command substitutions in a string.
    Example: "${batocera-audio getSystemVolume}%" -> "80%"
    Supports multiple ${...} in one string, including nested braces.
    """
    if not s or "${" not in s:
        return s

    result = s
    # Find ${...} patterns with proper brace matching
    i = 0
    while i < len(result):
        if i < len(result) - 1 and result[i:i+2] == "${":
            # Found start of command
            start = i
            i += 2
            depth = 1
            cmd_start = i

            # Find matching closing brace
            while i < len(result) and depth > 0:
                if result[i] == '{':
                    depth += 1
                elif result[i] == '}':
                    depth -= 1
                i += 1

            if depth == 0:
                # Extract and run command
                cmd = result[cmd_start:i-1]
                cmd_result = run_shell_capture(cmd.strip())
                # Replace ${cmd} with result
                result = result[:start] + cmd_result + result[i:]
                i = start + len(cmd_result)
            else:
                # Unmatched braces, skip
                i = start + 2
        else:
            i += 1

    return result

def run_shell_capture(cmd: str, timeout_sec: float = 3.0) -> str:
    """
    Execute a command and capture stdout safely.
    - Uses shell=True only when shell metacharacters are present.
    - Kills child via process group when timing out.
    - Returns decoded UTF-8 text (errors ignored), stripped.
    """
    if not cmd:
        return ""
    use_shell = any(c in cmd for c in ['$', '|', '&', ';', '`', '>', '<'])
    try:
        if use_shell:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
        else:
            proc = subprocess.Popen(
                shlex.split(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
        out, _ = proc.communicate(timeout=timeout_sec)
        return out.decode("utf-8", errors="ignore").strip()
    except subprocess.TimeoutExpired:
        try:
            # Best-effort terminate process group
            os.killpg(os.getpgid(proc.pid), 9)
        except Exception:
            pass
        return ""
    except Exception:
        return ""

def ensure_display() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))

def get_primary_geometry():
    """
    Returns (x, y, width, height) for the primary monitor.
    Falls back to monitor 0, and to 1280x720 if unavailable.
    Ensures landscape orientation width >= height.
    """
    display = Gdk.Display.get_default()
    mon = None
    try:
        mon = display.get_primary_monitor()
    except Exception:
        mon = None
    if mon is None:
        try:
            mon = display.get_monitor(0)
        except Exception:
            mon = None
    if mon and hasattr(mon, "get_geometry"):
        g = mon.get_geometry()
        # Some handhelds report portrait; normalize to landscape for popup sizing
        if g.height > g.width:
            (g.width, g.height) = (g.height, g.width)
        return g.x, g.y, g.width, g.height
    return (0, 0, 1280, 720)


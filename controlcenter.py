#!/usr/bin/env python3
# controlcenter.py — Batocera Control Center
# This file is part of the batocera distribution (https://batocera.org).
# Copyright (c) 2025 lbrpdx for the Batocera team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License
# as published by the Free Software Foundation, version 3.
#
# YOU MUST KEEP THIS HEADER AS IT IS
#
import os
import sys
import signal

# Add script directory to path so imports work from anywhere
script_path = os.path.realpath(__file__)
script_dir = os.path.dirname(script_path)
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

os.environ.setdefault("NO_AT_BRIDGE", "1")

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk

from xml_utils import parse_xml, validate_xml
from ui_core import ControlCenterApp

def ensure_display():
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))

def gtk_init_check():
    try:
        ok, _ = Gtk.init_check(sys.argv)
        return bool(ok)
    except Exception:
        return False

def main():
    # Will be set after app is created
    app_instance = [None]

    def signal_handler(*_):
        if app_instance[0]:
            app_instance[0].core.quit()
            exit(0)
        else:
            Gtk.main_quit()

    # show/hide the main window
    def signal_handler_usr1(*_):
        if app_instance[0]:
            app_instance[0].core.toogle_visibility()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGUSR1, signal_handler_usr1)

    if not ensure_display():
        sys.stderr.write("ERROR: No GUI display detected. Set DISPLAY or WAYLAND_DISPLAY.\n")
        sys.exit(1)
    if not gtk_init_check():
        sys.stderr.write("ERROR: Gtk couldn't be initialized.\n")
        sys.exit(1)

    # Helper function to find files in priority order
    def find_file(filename, default_path):
        """Find file in priority order:
        1. /userdata/system/configs/controlcenter/
        2. /usr/share/batocera/controlcenter/
        3. Same directory as controlcenter.py (default_path)
        """
        search_paths = [
            f"/userdata/system/configs/controlcenter/{filename}",
            f"/usr/share/batocera/controlcenter/{filename}",
            default_path
        ]

        for path in search_paths:
            if os.path.exists(path):
                return path

        # Return default path even if it doesn't exist (for error messages)
        return default_path

    # Get script directory for default paths (follow symlinks)
    script_path = os.path.realpath(__file__)
    script_dir = os.path.dirname(script_path)

    # Paths and parameters
    xml_path = None
    css_path = None
    auto_close_seconds = 0  # 0 = never auto-close
    hidden_at_startup = False

    # Parse command line arguments - any numeric argument is the timeout
    for arg in sys.argv[1:]:
        if arg in ("-h", "--help"):
            continue
        if arg == "--hidden":
            hidden_at_startup = True
            continue
        # Check if it's a number (timeout)
        try:
            auto_close_seconds = int(arg)
            continue
        except ValueError:
            pass
        # Check if it's an XML file
        if arg.endswith('.xml') or xml_path is None:
            if xml_path is None:
                xml_path = arg
        # Otherwise it's a CSS file
        elif arg.endswith('.css') or css_path is None:
            if css_path is None:
                css_path = arg

    # If no XML path specified, search in priority order
    if xml_path is None:
        xml_path = find_file("controlcenter.xml", os.path.join(script_dir, "controlcenter.xml"))

    # If no CSS path specified, search in priority order
    if css_path is None:
        css_path = find_file("style.css", os.path.join(script_dir, "style.css"))

    if not os.path.exists(xml_path):
        sys.stderr.write(f"ERROR: XML file not found: {xml_path}\n")
        sys.exit(1)
    if not os.path.exists(css_path):
        sys.stderr.write(f"WARNING: CSS file not found: {css_path} — running without custom styles.\n")

    xml_root = parse_xml(xml_path)
    errs, warns = validate_xml(xml_root)
    if warns:
        sys.stderr.write("XML warnings:\n")
        for w in warns:
            sys.stderr.write(f" - {w}\n")
    if errs:
        sys.stderr.write("XML errors:\n")
        for e in errs:
            sys.stderr.write(f" - {e}\n")
        sys.exit(2)

    app = ControlCenterApp(xml_root, css_path, auto_close_seconds, hidden_at_startup)
    app_instance[0] = app  # Store for signal handler
    app.run()

if __name__ == "__main__":
    main()


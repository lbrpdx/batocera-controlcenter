# ui_core.py - main UI components for the Control Center
# This file is part of the batocera distribution (https://batocera.org).
# Copyright (c) 2025-2026 lbrpdx for the Batocera team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License
# as published by the Free Software Foundation, version 3.
#
# YOU MUST KEEP THIS HEADER AS IT IS
import os
import threading
import time
import gi
gi.require_version('Gtk', '3.0'); gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
from gamepads import GamePads
from DocViewer import DocViewer

import locale
_ = locale.gettext

# Activate for some verbose message on tricky parts of the code
DEBUG = os.environ.get('CONTROLCENTER_DEBUG', '').lower() in ('1', 'true', 'yes')
ACTION_DEBOUNCE_MS = 100  # Faster response
WINDOW_TITLE = "Batocera Control Center"

# Optional evdev for gamepad
try:
    from evdev import InputDevice
    EVDEV_AVAILABLE = True
except Exception:
    EVDEV_AVAILABLE = False

from refresh import RefreshTask, DEFAULT_REFRESH_SEC, Debouncer
from shell import run_shell_capture, normalize_bool_str, get_primary_geometry, expand_command_string

def evaluate_if_condition(condition: str, rendered_ids: set[str]) -> bool:
    """
    Evaluate an 'if' condition to determine if an element should be rendered.

    Supported formats:
    - if="id(some_id)" - True if element with id="some_id" is rendered
    - if="!id(some_id)" - True if element with id="some_id" is NOT rendered
    - if="${command}" - True if command returns non-empty string

    Returns True if condition is met, False otherwise.
    """
    if not condition:
        return True

    s = condition.strip()
    if not s:
        return True

    # Check for id(xxx) condition
    if s.startswith("id(") and s.endswith(")"):
        # no extra strip inside parentheses
        return s[3:-1] in rendered_ids

    # Check for !id(xxx) condition (negation)
    if s.startswith("!id(") and s.endswith(")"):
        return s[4:-1] not in rendered_ids

    # Check for ${command} condition
    if s.startswith("${") and s.endswith("}"):
        cmd = s[2:-1].strip()
        if not cmd:
            return False
        result = run_shell_capture(cmd)
        # Treat "null" as empty result (common in shell commands)
        result_clean = result.strip()
        if result_clean.lower() == "null":
            result_clean = ""
        return bool(result) and bool(result_clean)

    # Unknown format - default to True to avoid hiding content
    return True

def should_render_element(element, rendered_ids: set[str]) -> bool:
    """
    Check if an element should be rendered based on its 'if' attribute.
    """
    if_condition = element.attrs.get("if", "").strip()
    if not if_condition:
        return True  # No condition = always render
    
    return evaluate_if_condition(if_condition, rendered_ids)

def register_element_id(element, rendered_ids: set[str], core=None):
    """Register an element's ID after it has been rendered with content."""
    element_id = element.attrs.get("id", "").strip()
    if not element_id:
        return
    if element_id in rendered_ids:
        # Already registered; nothing else to do
        return

    rendered_ids.add(element_id)

    # Trigger immediate update of conditional widgets
    if core and hasattr(core, '_conditional_widgets'):
        for widget, condition in core._conditional_widgets:
            try:
                should_show = evaluate_if_condition(condition, rendered_ids)
                widget.set_visible(should_show)
                if DEBUG:
                    print(f"[REGISTER] {condition} -> {should_show}, IDs={self.rendered_ids}")
            except Exception as e:
                pass

def is_cmd(s: str) -> bool:
    s = (s or "").strip()
    return s.startswith("${") and s.endswith("}")

def cmd_of(s: str) -> str:
    s = (s or "").strip()
    return s[2:-1].strip() if is_cmd(s) else ""

def is_empty_or_null(s: str) -> bool:
    s = (s or "").strip()
    return s == "" or s.lower() == "null"

def _focus_widget(widget: Gtk.Widget):
    # Don't use grab_focus() as it conflicts with touchscreen behavior
    # Just add the CSS classes for visual focus
    try:
        ctx = widget.get_style_context()
        ctx.add_class("focused-cell")
    except Exception:
        pass

def _activate_widget(widget: Gtk.Widget):
    if isinstance(widget, Gtk.Button):
        try:
            widget.emit("clicked")
        except Exception:
            pass
    elif isinstance(widget, Gtk.ToggleButton):
        try:
            widget.set_active(not widget.get_active())
        except Exception:
            pass
    elif isinstance(widget, Gtk.Switch):
        try:
            # Toggle the switch state and emit the state-set signal
            new_state = not widget.get_active()
            widget.set_active(new_state)
            widget.set_state(new_state)
            # Emit the state-set signal to trigger our handler
            widget.emit("state-set", new_state)
        except Exception:
            pass

class UICore:
    def __init__(self, css_path: str, fullscreen: bool = False, window_size: tuple[int, int] | None = None):
        self.css_path = css_path
        self.fullscreen = fullscreen
        self.window_size = window_size
        self.window: Gtk.Window | None = None
        self.focus_rows: list[Gtk.EventBox] = []
        self.focus_index: int = 0
        self.refreshers: list[RefreshTask] = []
        self.debouncer = Debouncer(ACTION_DEBOUNCE_MS)
        self._gamepads = GamePads()
        self._inactivity_timer_id = None
        self._inactivity_timeout_seconds = 0
        self.rendered_ids: set[str] = set()  # Track IDs of rendered elements
        self._conditional_widgets = []  # Track widgets with !id() conditions for dynamic updates
        self.quit_mode = "hide" # versus close
        self._handle_gamepad_action = self._handle_gamepad_action_main

    # ---- Window / CSS ----
    def build_window(self):
        display = Gdk.Display.get_default()
        backend = (display.get_name() or "").lower()
        is_wayland = "wayland" in backend

        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.set_title(WINDOW_TITLE)

        # Undecorated on both X11 and Wayland
        win.set_decorated(False)

        if is_wayland:
            win.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        else:
            win.set_type_hint(Gdk.WindowTypeHint.DIALOG)
            win.set_keep_above(True)

        win.set_resizable(True)
        win.set_skip_taskbar_hint(False)
        win.set_accept_focus(True)
        win.set_focus_on_map(True)
        win.set_modal(False)
        win.get_style_context().add_class("popup-root")
        win.set_name("popup-root")

        x0, y0, sw, sh = get_primary_geometry()
        
        # Handle fullscreen mode
        if self.fullscreen:
            width, max_height = sw, sh
            scale_class = "full"
            win.set_decorated(False)  # Remove window decorations for fullscreen
        # Handle custom window size
        elif self.window_size:
            width, max_height = self.window_size
            scale_class = "full" if width >= 1280 and max_height >= 720 else "small"
        else:
            # Default sizing logic
            width, max_height = sw, sh
            # small screens vs full size (>= 1280 x 720)
            scale_class = "small"
            if sw >= 1280:
                width = int(sw * 0.90)
                scale_class = "full"
            if sw >= 1920:
                width = int(sw * 0.70)
                scale_class = "full"
            if sh >= 720:
                max_height = int(sh * 0.95)
                scale_class = "full"
            if sh >= 1080:
                max_height = int(sh * 0.80)
                scale_class = "full"
        
        win.get_style_context().add_class(f"scale-{scale_class}")

        # Store dimensions for positioning
        self._window_width = width
        self._max_height = max_height
        self._screen_x = x0
        self._screen_y = y0
        self._screen_width = sw
        self._screen_height = sh
        self._scale_class = scale_class

        # Set default size - use max_height as starting point
        win.set_default_size(width, max_height)

        # Set geometry hints for both X11 and Wayland - defer to avoid blocking
        def set_geometry_hints():
            if not self.fullscreen:  # Don't set geometry hints for fullscreen
                geom = Gdk.Geometry()
                if self.window_size:
                    # Custom window size - allow resizing
                    geom.min_width = min(640, width)  # Minimum reasonable size
                    geom.min_height = min(480, max_height)
                    # Don't set max constraints for custom sizes
                else:
                    # Default behavior - fixed size
                    geom.min_width = width
                    geom.max_width = width
                    geom.max_height = max_height
                    win.set_geometry_hints(None, geom, Gdk.WindowHints.MIN_SIZE | Gdk.WindowHints.MAX_SIZE)
                    return False
                win.set_geometry_hints(None, geom, Gdk.WindowHints.MIN_SIZE)
            return False
        
        # Defer geometry hints to avoid blocking window creation
        GLib.idle_add(set_geometry_hints)

        # Store for later use
        self._is_wayland = is_wayland

        def on_realize(_w):
            # Handle fullscreen mode
            if self.fullscreen:
                if is_wayland:
                    # For Wayland, we'll handle fullscreen in the sway_commands
                    pass
                else:
                    # For X11, use GTK fullscreen
                    win.fullscreen()
            # On X11, position immediately after realize (non-fullscreen)
            elif not is_wayland:
                center_x = self._screen_x + (self._screen_width - self._window_width) // 2
                if self._screen_height - self._max_height > 10:
                    top_y = self._screen_y + 10
                else:
                    top_y = self._screen_y
                win.move(max(0, center_x), max(0, top_y))

        def on_map(_w):
            # Ensure window is shown
            win.show_all()
            win.present()

            # On Wayland/Sway, use swaymsg to make window visible
            if is_wayland:
                def sway_commands():
                    import subprocess
                    import time
                    import json

                    # Wait for window to appear in Sway's tree and find its app_id
                    app_id = None
                    window_title = WINDOW_TITLE

                    for attempt in range(10):
                        try:
                            result = subprocess.run(['swaymsg', '-t', 'get_tree'],
                                                  capture_output=True, text=True, timeout=1)
                            if result.returncode == 0:
                                tree = json.loads(result.stdout)

                                # Find our window by title
                                def find_window(node):
                                    if isinstance(node, dict):
                                        if node.get('name') == window_title or node.get('app_id', '').endswith('controlcenter'):
                                            return node.get('app_id')
                                        for child in node.get('nodes', []) + node.get('floating_nodes', []):
                                            result = find_window(child)
                                            if result:
                                                return result
                                    return None

                                app_id = find_window(tree)
                                if app_id:
                                    break
                        except Exception:
                            pass
                        time.sleep(0.1)

                    if not app_id:
                        return False

                    # Manipulate the window to make it visible
                    try:
                        if self.fullscreen:
                            # For fullscreen mode on Wayland
                            subprocess.run(['swaymsg', f'[app_id="{app_id}"]', 'fullscreen', 'enable'],
                                         capture_output=True, timeout=1)
                        else:
                            # Make it floating
                            subprocess.run(['swaymsg', f'[app_id="{app_id}"]', 'floating', 'enable'],
                                         capture_output=True, timeout=1)

                            # Remove decorations (border)
                            subprocess.run(['swaymsg', f'[app_id="{app_id}"]', 'border', 'none'],
                                         capture_output=True, timeout=1)

                            # Briefly fullscreen to force visibility, then restore
                            subprocess.run(['swaymsg', f'[app_id="{app_id}"]', 'fullscreen', 'enable'],
                                         capture_output=True, timeout=1)
                            time.sleep(0.05)
                            subprocess.run(['swaymsg', f'[app_id="{app_id}"]', 'fullscreen', 'disable'],
                                         capture_output=True, timeout=1)

                            # Center the window (Sway config can override this if it has positioning rules)
                            subprocess.run(['swaymsg', f'[app_id="{app_id}"]', 'move', 'position', 'center'],
                                         capture_output=True, timeout=1)

                        # Focus the window
                        subprocess.run(['swaymsg', f'[app_id="{app_id}"]', 'focus'],
                                     capture_output=True, timeout=1)
                    except Exception:
                        pass

                    return False

                # Run sway commands in a background thread
                import threading
                threading.Thread(target=sway_commands, daemon=True).start()

            try:
                win.grab_focus()
            except Exception as e:
                print(f"grab_focus failed: {e}")

            # Focus first row - use CSS classes instead of grab_focus
            if self.focus_rows:
                def set_initial_focus():
                    try:
                        first_row = self.focus_rows[0]
                        ctx = first_row.get_style_context()
                        ctx.add_class("focused-cell")
                    except Exception:
                        pass
                    return False
                GLib.timeout_add(10, set_initial_focus)

        # Track if we have an open dialog to prevent closing on dialog focus
        self._dialog_open = False
        # Track if we should suspend the inactivity timer (for document/confirm dialogs, not choice popups)
        self._suspend_inactivity_timer = False
        # Track if dialog allows inactivity timeout (choice popups allow it, document/confirm don't)
        self._dialog_allows_timeout = False
        # Track current dialog for cleanup on timeout
        self._current_dialog = None

        # Track when we're about to show a dialog (set by button callbacks)
        self._about_to_show_dialog = False

        def on_focus_out(_w, ev):
            # If we're about to show a dialog or have one open, ignore this focus-out
            if self._about_to_show_dialog or self._dialog_open:
                return False

            # Otherwise, close after a delay
            def check_and_close():
                # Double-check that no dialog opened during the delay
                if self._dialog_open:
                    return False
                self.quit()
                return False

            GLib.timeout_add(100, check_and_close)
            return False

        # Connect event handlers for user interaction
        def on_button_motion_notify(_w, _ev):
            if self._inactivity_timeout_seconds > 0 and not self._suspend_inactivity_timer:
                self.reset_inactivity_timer()
            return False

        win.connect("realize", on_realize)
        win.connect("map", on_map)
        win.connect("key-press-event", self._on_key_press)
        win.connect("focus-out-event", on_focus_out)
        win.connect("button-press-event", on_button_motion_notify)
        win.connect("motion-notify-event", on_button_motion_notify)

        # Enable events for mouse interaction
        win.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK)

        self.window = win
        return win

    def apply_css(self):
        if not self.css_path:
            print("ERROR: No CSS path provided")
            return
        if not os.path.exists(self.css_path):
            print(f"ERROR: CSS file not found: {self.css_path}")
            return

        print(f"Loading CSS from: {self.css_path}")
        prov = Gtk.CssProvider()
        try:
            with open(self.css_path, "rb") as f:
                css_data = f.read()
                print(f"CSS file size: {len(css_data)} bytes")

                prov.load_from_data(css_data)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(),
                prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            print("CSS loaded successfully")
        except Exception as e:
            print(f"CSS load failed: {e}")
            import traceback
            traceback.print_exc()

    # ---- Keyboard / Focus ----
    def _on_key_press(self, _w, ev: Gdk.EventKey):
        key = Gdk.keyval_name(ev.keyval) or ""
        if key.lower() == "escape":
            self.quit()
        elif key in ("Up", "KP_Up"):
            self.move_focus(-1)
        elif key in ("Down", "KP_Down"):
            self.move_focus(+1)
        elif key in ("Left", "KP_Left"):
            self.row_left()
        elif key in ("Right", "KP_Right"):
            self.row_right()
        elif key in ("Return", "KP_Enter", "space"):
            self.activate_current()
        return True

    def _row_set_focused(self, row: Gtk.EventBox, focused: bool):
        ctx = row.get_style_context()
        if focused:
            ctx.add_class("focused")
        else:
            ctx.remove_class("focused")

    def unhighlight_row(self, prev_row):
        self._row_set_focused(prev_row, False)
        # Clear ALL highlights on the row we leave (vgroup cells and controls)
        try:
            if hasattr(prev_row, "_cells"):
                for ev, controls in prev_row._cells:
                    ev.get_style_context().remove_class("focused-cell")
                    for ctrl in controls:
                        ctx = ctrl.get_style_context()
                        ctx.remove_class("focused-cell")
                        ctx.remove_class("choice-selected")
            # Also clear highlights from feature row items
            if hasattr(prev_row, "_items"):
                for item in prev_row._items:
                    ctx = item.get_style_context()
                    ctx.remove_class("focused-cell")
                    ctx.remove_class("choice-selected")
        except Exception:
            pass

    def move_focus(self, delta: int):
        if not self.focus_rows:
            return
        self.reset_inactivity_timer()  # Reset timer on navigation
        old = self.focus_index
        self.focus_index = (self.focus_index + delta) % len(self.focus_rows)

        prev_row = self.focus_rows[old]
        self.unhighlight_row(prev_row)

        new_row = self.focus_rows[self.focus_index]
        self._row_set_focused(new_row, True)
        _focus_widget(new_row)

        # If new row has items (buttons), select the first one and highlight it
        if hasattr(new_row, "_items") and new_row._items:
            if not hasattr(new_row, "_item_index"):
                new_row._item_index = 0
            item = new_row._items[new_row._item_index]
            # Apply highlight classes
            ctx = item.get_style_context()
            ctx.add_class("focused-cell")
            ctx.add_class("choice-selected")
            _focus_widget(item)

        # Apply vgroup cell highlight on new row (trigger focus-in to apply highlights properly)
        try:
            if hasattr(new_row, "_cells") and new_row._cells:
                # Let the focus-in handler apply the highlights
                pass
        except Exception:
            pass

    def activate_current(self):
        if not self.focus_rows:
            return
        self.reset_inactivity_timer()  # Reset timer on activation
        row = self.focus_rows[self.focus_index]

        # For rows with items (buttons/toggles), do nothing when row is selected
        # User must navigate to the specific button first
        if hasattr(row, "_items") and row._items:
            # Don't activate anything - user needs to use left/right to select button
            return

        # For rows without items (like vgroup cells), use the row's activate callback
        cb = getattr(row, "_on_activate", None)
        if callable(cb):
            cb()

    def get_index_active_tab(self):
        for i, t in enumerate(self.window._tab_row._tabs):
            if t.get_active():
                return i
        return None

    def set_next_tab(self):
        i = self.get_index_active_tab()
        if i is not None:
            i = i+1
            while i < len(self.window._tab_row._tabs):
                if self.window._tab_row._tabs[i].is_visible():
                    self.window._tab_row._tabs[i].set_active(True)
                    return
                i = i+1

    def set_previous_tab(self):
        i = self.get_index_active_tab()
        if i is not None:
            i = i-1
            while i >= 0:
                if self.window._tab_row._tabs[i].is_visible():
                    self.window._tab_row._tabs[i].set_active(True)
                    return
                i = i-1

    def row_left(self):
        row = self.focus_rows[self.focus_index] if self.focus_rows else None
        if not row:
            return

        # If row has items, navigate and activate
        if hasattr(row, "_items") and row._items:
            item_index = getattr(row, "_item_index", 0)
            if item_index > 0:
                # Clear highlight from old item
                old_item = row._items[item_index]
                old_ctx = old_item.get_style_context()
                old_ctx.remove_class("focused-cell")
                old_ctx.remove_class("choice-selected")

                # Move to new item
                # first find the first visible
                tmp_idx = item_index - 1
                while tmp_idx > 0 and row._items[tmp_idx].is_visible() == False:
                    tmp_idx = tmp_idx-1

                # reset to the initial one if none is found
                if row._items[tmp_idx].is_visible() == False:
                    tmp_idx = item_index

                # set the new one
                row._item_index = tmp_idx
                item = row._items[row._item_index]

                # Highlight new item
                ctx = item.get_style_context()
                ctx.add_class("focused-cell")
                ctx.add_class("choice-selected")
                _focus_widget(item)
        else:
            cb = getattr(row, "_on_left", None)
            if callable(cb):
                cb()

    def row_right(self):
        row = self.focus_rows[self.focus_index] if self.focus_rows else None
        if not row:
            return

        # If row has items, navigate and activate
        if hasattr(row, "_items") and row._items:
            item_index = getattr(row, "_item_index", 0)
            if item_index < len(row._items) - 1:
                # Clear highlight from old item
                old_item = row._items[item_index]
                old_ctx = old_item.get_style_context()
                old_ctx.remove_class("focused-cell")
                old_ctx.remove_class("choice-selected")

                # Move to new item
                # first find the first visible
                tmp_idx = item_index + 1
                while tmp_idx < len(row._items)-1 and row._items[tmp_idx].is_visible() == False:
                    tmp_idx = tmp_idx+1

                # reset to the initial one if none is found
                if row._items[tmp_idx].is_visible() == False:
                    tmp_idx = item_index

                # set the new one
                row._item_index = tmp_idx
                item = row._items[row._item_index]

                # Highlight new item
                ctx = item.get_style_context()
                ctx.add_class("focused-cell")
                ctx.add_class("choice-selected")
                _focus_widget(item)
        else:
            cb = getattr(row, "_on_right", None)
            if callable(cb):
                cb()

    def register_row(self, row: Gtk.EventBox):
        row.set_can_focus(True)
        row.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.FOCUS_CHANGE_MASK)
        row.connect("focus-in-event", lambda w, *_: self._row_set_focused(w, True))
        row.connect("focus-out-event", lambda w, *_: self._row_set_focused(w, False))
        row.connect("button-press-event", lambda *_: self._activate_row(row))
        self.focus_rows.append(row)

    def _activate_row(self, row: Gtk.EventBox):
        cb = getattr(row, "_on_activate", None)
        if callable(cb):
            cb()

    def hide(self, *_a):
        self.window.hide()
        self._gamepads.stopThread()
        self.stop_refresh()

    def show(self, *_a):
        self.start_gamepad()
        self.window.present()
        self.reset_inactivity_timer()  # Reset timer on button click
        self.start_refresh()
        self.set_tab_focus()
        # Reset focus to first row to prevent discrepancy after timeout
        _init_focus(self)

    def toogle_visibility(self, *_a):
        if self.window.is_visible():
            self.hide()
        else:
            self.show()

    def set_tab_focus(self):
        if len(self.window._tab_row._tabs) == 0:
            return
        n = 0
        while n < len(self.window._tab_row._tabs) and not self.window._tab_row._tabs[n].is_visible():
            n = n+1
        if n < len(self.window._tab_row._tabs):
            self.window._tab_row._tabs[n].set_active(True)

    def stop_refresh(self):
        for r in self.refreshers:
            r.stop()

    def start_refresh(self):
        # Defer refresh startup to prevent audio glitches during window creation
        def start_refreshers_idle():
            for r in self.refreshers:
                r.start()
            return False  # Don't repeat
        
        # Start refreshers after a short delay to let window settle
        GLib.timeout_add(100, start_refreshers_idle)

        for widget, condition in self._conditional_widgets:
            try:
                should_show = evaluate_if_condition(condition, self.rendered_ids)
                widget.set_visible(should_show)
                if DEBUG:
                        print(f"[START_REF] {condition} -> {should_show}, IDs={self.rendered_ids}")
            except Exception as e:
                pass

    def quit(self, *_a):
        # If there are open dialogs, destroy them first
        if hasattr(self, '_current_dialog') and self._current_dialog:
            try:
                self._current_dialog.destroy()
            except Exception:
                pass
            self._current_dialog = None
        
        if self.quit_mode == "hide":
            self.hide()

        if self.quit_mode == "close":
            try:
                if self.window:
                    self.window.destroy()
            except Exception:
                pass

            # close pads (after window is close to not wait)
            self._gamepads.stopThread()

            try:
                Gtk.main_quit()
            except Exception:
                pass

    def _handle_gamepad_action_call(self, action: str):
        self._handle_gamepad_action(action)

    def start_gamepad(self):
        """Use evdev to read gamepad input with exclusive access (blocks EmulationStation)"""
        if not EVDEV_AVAILABLE:
            return
        
        # Defer gamepad startup to avoid blocking window creation
        def start_gamepad_delayed():
            self._gamepads.startThread(self._handle_gamepad_action_call)
            return False
        
        # Start gamepad after window is shown
        GLib.timeout_add(50, start_gamepad_delayed)

        # give time to the user to release the hotkeys...
        n = 20
        while self._gamepads.can_get_input_focus() is False and n > 0:
            if DEBUG:
                print("please release hotkeys...")
            time.sleep(0.1)
            n = n-1

    def enable_gamepad_continuous_actions(self):
        """Enable continuous gamepad actions (for document viewer)"""
        self._gamepads.enable_continuous_actions()

    def disable_gamepad_continuous_actions(self):
        """Disable continuous gamepad actions (for main window)"""
        self._gamepads.disable_continuous_actions()

    def _handle_gamepad_action_main(self, action: str):
        """Handle gamepad actions - works for both main window and dialogs"""
        # Reset inactivity timer on any gamepad action
        try:
            self.reset_inactivity_timer()
        except Exception as e:
            print(f"Error resetting inactivity timer from gamepad: {e}")

        if action == "activate":
            # Check if we're on a row with items and an item is selected
            if self.focus_rows:
                row = self.focus_rows[self.focus_index]
                if hasattr(row, "_items") and row._items:
                    item_index = getattr(row, "_item_index", 0)
                    if 0 <= item_index < len(row._items):
                        item = row._items[item_index]
                        _activate_widget(item)
                        return False
            self.activate_current()
        elif action == "back":
            self.quit()
        elif action == "axis_up":
            self.move_focus(-1)
        elif action == "axis_down":
            self.move_focus(+1)
        elif action == "axis_left":
            self.row_left()
        elif action == "axis_right":
            self.row_right()
        elif action == "pan_up":
            self.move_focus(-1)  # Right stick up = move focus up
        elif action == "pan_down":
            self.move_focus(+1)  # Right stick down = move focus down
        elif action == "pan_left":
            self.row_left()  # Right stick left = navigate left
        elif action == "pan_right":
            self.row_right()  # Right stick right = navigate right
        elif action == "next_tab":
            self.set_next_tab()
        elif action == "previous_tab":
            self.set_previous_tab()
        return False

    # ---- Rendering helpers for new schema ----
    def reset_inactivity_timer(self):
        """Reset the inactivity timer when user interacts with the window"""
        secs = self._inactivity_timeout_seconds
        if secs <= 0 or self._suspend_inactivity_timer:
            return

        # Cancel existing timer only if present
        tid = self._inactivity_timer_id
        if tid is not None:
            try:
                GLib.source_remove(tid)
            except Exception:
                pass
            self._inactivity_timer_id = None

        # Start new timer - quit if no dialog is open, or if dialog allows timeout
        def timeout_callback():
            self._inactivity_timer_id = None
            # Always quit on timeout - dialogs should prevent timer from running if needed
            self.quit()
            return False

        # Use GLib.timeout_add_seconds directly
        self._inactivity_timer_id = GLib.timeout_add_seconds(secs, timeout_callback)

    def disable_timer(self):
        if self._inactivity_timer_id is not None:
            try:
                GLib.source_remove(self._inactivity_timer_id)
            except:
                pass
            self._inactivity_timer_id = None

    def make_action_cb(self, action: str, key: str):
        def cb(_w=None):
            act = (action or "").strip()
            if not act:
                return
            if self.debouncer.allow(key):
                self.reset_inactivity_timer()  # Reset timer on button click
                threading.Thread(target=lambda: run_shell_capture(act), daemon=True).start()
        return cb

    def _recompute_conditionals(self):
        """Recompute visibility for all widgets with 'if' conditions."""
        if hasattr(self, '_conditional_widgets'):
            for widget, condition in self._conditional_widgets:
                try:
                    should_show = evaluate_if_condition(condition, self.rendered_ids)
                    widget.set_visible(should_show)
                    if DEBUG:
                        print(f"[RECOMPUTE] {condition} -> {should_show}, IDs={self.rendered_ids}")
                except Exception:
                    pass

    # ---- Builders for UI elements ----
    def build_text(self, parent_feat, sub, row_box, align_end=False):
        lbl = Gtk.Label(label="")
        lbl.get_style_context().add_class("value")
        # wrap text by default
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD)
        lbl.set_max_width_chars(80)

        # Apply ID as widget name for CSS
        elem_id = (sub.attrs.get("id", "") or "").strip()
        if elem_id:
            # container gets background, padding, etc.
            row_box.set_name(elem_id)
            # label gets font, color, etc.
            lbl.set_name(elem_id + "_label")

        # Get alignment from attribute (default: center)
        align_attr = (sub.attrs.get("align", "center") or "center").strip().lower()
        if align_attr == "left":
            lbl.set_xalign(0.0)
            lbl.set_halign(Gtk.Align.START)
        elif align_attr == "right":
            lbl.set_xalign(1.0)
            lbl.set_halign(Gtk.Align.END)
        else:  # center (default)
            lbl.set_xalign(0.5)
            lbl.set_halign(Gtk.Align.CENTER)

        (row_box.pack_end if align_end else row_box.pack_start)(lbl, False, False, 6)
        disp = (sub.attrs.get("display", "") or "").strip()
        refresh = float(sub.attrs.get("refresh", parent_feat.attrs.get("refresh", DEFAULT_REFRESH_SEC)))

        # Handle dynamic visibility for id() and !id() conditions
        if_condition = (sub.attrs.get("if", "") or "").strip()
        if if_condition:
            # Track this widget for dynamic visibility updates
            self._conditional_widgets.append((lbl, if_condition))

        def show_and_register(text: str, _lbl=lbl, _sub=sub, _core=self):
            _lbl.set_text(text)
            _lbl.set_visible(True)
            register_element_id(_sub, _core.rendered_ids)

        def hide_and_unregister(_lbl=lbl, _sub=sub, _core=self):
            _lbl.set_text("")
            _lbl.set_visible(False)
            e_id = (_sub.attrs.get("id", "") or "").strip()
            if e_id and e_id in _core.rendered_ids:
                _core.rendered_ids.discard(e_id)

        # Mixed substitution: expand string each tick
        if "${" in disp and not is_cmd(disp):
            def upd_expand(_l=lbl, _disp=disp, _sub=sub, _core=self):
                result = expand_command_string(_disp)
                result_stripped = result.strip()
                element_id = (_sub.attrs.get("id", "") or "").strip()
                
                if result_stripped and result_stripped.lower() != "null":
                    _l.set_text(result)
                    _l.set_visible(True)
                    # Only recompute if this element has an ID (affects conditionals)
                    if element_id:
                        register_element_id(_sub, _core.rendered_ids)
                        _core._recompute_conditionals()
                else:
                    _l.set_text("")
                    _l.set_visible(False)
                    # Only recompute if this element had an ID that's being removed
                    if element_id and element_id in _core.rendered_ids:
                        _core.rendered_ids.discard(element_id)
                        _core._recompute_conditionals()

            class ExpandRefreshTask:
                def __init__(self, update_fn, interval_sec):
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
                    delay = 10 if immediate else self.interval_ms
                    self._timer_id = GLib.timeout_add(delay, self._tick)

                def _tick(self):
                    # schedule update on main loop directly (no per-tick thread)
                    try:
                        GLib.idle_add(self.update_fn)
                    except Exception:
                        pass
                    if self._active:
                        self._schedule_tick(immediate=False)
                    return False

            self.refreshers.append(ExpandRefreshTask(upd_expand, refresh))

            # Initial evaluation
            def set_initial():
                initial_value = expand_command_string(disp)
                element_id = (sub.attrs.get("id", "") or "").strip()
                
                if is_empty_or_null(initial_value):
                    hide_and_unregister()
                    # Only recompute if element had an ID
                    if element_id:
                        self._recompute_conditionals()
                else:
                    show_and_register(initial_value)
                    # Only recompute if element has an ID
                    if element_id:
                        self._recompute_conditionals()
                return False

            GLib.idle_add(set_initial)

        elif is_cmd(disp):
            c = cmd_of(disp)

            # Initial evaluation
            def set_initial():
                initial_val = run_shell_capture(c).strip()
                element_id = (sub.attrs.get("id", "") or "").strip()
                
                if is_empty_or_null(initial_val):
                    hide_and_unregister()
                    # Only recompute if element had an ID
                    if element_id:
                        self._recompute_conditionals()
                else:
                    show_and_register(initial_val)
                    # Only recompute if element has an ID
                    if element_id:
                        self._recompute_conditionals()
                return False

            GLib.idle_add(set_initial)

            def upd(val: str, _l=lbl, _sub=sub, _core=self):
                txt = (val or "").strip()
                element_id = (_sub.attrs.get("id", "") or "").strip()
                
                if txt and txt.lower() != "null":
                    _l.set_text(txt)
                    _l.set_visible(True)
                    # Only recompute if this element has an ID (affects conditionals)
                    if element_id:
                        register_element_id(_sub, _core.rendered_ids)
                        _core._recompute_conditionals()
                else:
                    _l.set_text("")
                    _l.set_visible(False)
                    # Only recompute if this element had an ID that's being removed
                    if element_id and element_id in _core.rendered_ids:
                        _core.rendered_ids.discard(element_id)
                        _core._recompute_conditionals()

            self.refreshers.append(RefreshTask(upd, c, refresh))

        else:
            # Static text
            if is_empty_or_null(disp):
                hide_and_unregister(lbl, sub, self)
            else:
                lbl.set_text(disp)
                lbl.set_visible(True)
                register_element_id(sub, self.rendered_ids)

        return lbl


    def build_button(self, parent_feat, sub, row_box, pack_end=False):
        text = (sub.attrs.get("display", "") or "Button").strip()
        action = sub.attrs.get("action", "")
        btn = Gtk.Button.new_with_label(_(text))
        btn.get_style_context().add_class("cc-button")
        btn.set_can_focus(True)

        # Get alignment from attribute (default: center)
        align_attr = (sub.attrs.get("align", "center") or "center").strip().lower()
        if align_attr == "left":
            btn.set_halign(Gtk.Align.START)
        elif align_attr == "right":
            btn.set_halign(Gtk.Align.END)
        else:  # center (default)
            btn.set_halign(Gtk.Align.CENTER)

        (row_box.pack_end if pack_end else row_box.pack_start)(btn, False, False, 6)
        btn.connect("clicked", self.make_action_cb(action, key=f"btn:{text}:{action}"))

        # Register ID for buttons (they always produce visual content)
        register_element_id(sub, self.rendered_ids)

        return btn

    def build_toggle(self, parent_feat, sub, row_box, pack_end=False):
        parent_label = (parent_feat.attrs.get("display", "") or parent_feat.attrs.get("name", "") or "").strip()
        toggle_display = (sub.attrs.get("display", "") or "").strip()
        toggle_value = (sub.attrs.get("value", "") or "").strip()  # New value parameter
        action_on = sub.attrs.get("action_on", "")
        action_off = sub.attrs.get("action_off", "")
        refresh = float(sub.attrs.get("refresh", parent_feat.attrs.get("refresh", DEFAULT_REFRESH_SEC)))

        # Determine which command to use for status
        status_cmd = ""
        if toggle_value and is_cmd(toggle_value):
            status_cmd = cmd_of(toggle_value)
        elif toggle_display and is_cmd(toggle_display):
            status_cmd = cmd_of(toggle_display)

        status_lbl = None
        if status_cmd and toggle_display and is_cmd(toggle_display):
            # Only show separate label if display is a command
            status_lbl = Gtk.Label(label="")
            status_lbl.get_style_context().add_class("value")
            status_lbl.set_xalign(0.0)
            (row_box.pack_end if pack_end else row_box.pack_start)(status_lbl, False, False, 6)

        # Use parent feature label if display is a ${...}
        tbtn_label = parent_label if (toggle_display and is_cmd(toggle_display)) else (toggle_display or parent_label or "toggle")
        if is_cmd(tbtn_label):
            tbtn_label = ""

        tbtn = Gtk.ToggleButton.new_with_label(_(tbtn_label))
        tbtn.get_style_context().add_class("cc-toggle")
        tbtn.set_focus_on_click(True)

        # Get alignment from attribute (default: center)
        align_attr = (sub.attrs.get("align", "center") or "center").strip().lower()
        if align_attr == "left":
            tbtn.set_halign(Gtk.Align.START)
        elif align_attr == "right":
            tbtn.set_halign(Gtk.Align.END)
        else:  # center (default)
            tbtn.set_halign(Gtk.Align.CENTER)

        (row_box.pack_end if pack_end else row_box.pack_start)(tbtn, False, False, 6)

        # Update toggle label to show ON/OFF status
        def update_toggle_label():
            if tbtn.get_active():
                tbtn.set_label(_("ON"))
            else:
                tbtn.set_label(_("OFF"))

        # Track if we're currently updating from user action to prevent refresh conflicts
        toggle_state = {"updating": False, "last_user_change": 0}

        if status_cmd:
            # Defer initial value to idle for faster startup
            def set_initial():
                initial_val = run_shell_capture(status_cmd)
                initial_active = normalize_bool_str(initial_val)
                tbtn.set_active(initial_active)
                return False
            GLib.idle_add(set_initial)
            update_toggle_label()

            def upd(val: str, _lbl=status_lbl, _tb=tbtn):
                import time
                # Don't update if we just changed it (within 1 second)
                if time.time() - toggle_state["last_user_change"] < 1.0:
                    return

                txt = (val or "").strip()
                if _lbl:
                    _lbl.set_text(txt)
                active = normalize_bool_str(txt)

                # Only update if different and not currently updating
                if not toggle_state["updating"] and _tb.get_active() != active:
                    toggle_state["updating"] = True
                    _tb.set_active(active)
                    update_toggle_label()
                    toggle_state["updating"] = False

            self.refreshers.append(RefreshTask(upd, status_cmd, refresh))
        else:
            # If no status command, just show ON/OFF based on initial state
            update_toggle_label()

        def on_toggled(_w):
            import time
            # Ignore toggle events triggered by refresh updates
            if toggle_state["updating"]:
                return

            update_toggle_label()
            key = f"toggle:{parent_label or 'toggle'}"
            if not self.debouncer.allow(key):
                return

            # Mark that user just changed it
            toggle_state["last_user_change"] = time.time()

            act = action_on if tbtn.get_active() else action_off
            if act:
                threading.Thread(target=lambda: run_shell_capture(act), daemon=True).start()

        tbtn.connect("toggled", on_toggled)

        # Register ID for toggles (they always produce visual content)
        register_element_id(sub, self.rendered_ids)

        return tbtn

    def build_switch(self, parent_feat, sub, row_box, pack_end=False):
        """Build a switch widget (GtkSwitch) - same functionality as toggle but different appearance"""
        
        parent_label = (parent_feat.attrs.get("display", "") or parent_feat.attrs.get("name", "") or "").strip()
        switch_display = (sub.attrs.get("display", "") or "").strip()
        
        # Use switch display if provided, otherwise use parent label
        label_text = switch_display or parent_label
        
        # Create switch widget
        switch = Gtk.Switch()
        switch.get_style_context().add_class("cc-switch")
        switch.set_can_focus(True)
        
        # Apply ID as widget name for CSS
        elem_id = (sub.attrs.get("id", "") or "").strip()
        if elem_id:
            switch.set_name(elem_id)
        
        # Add to row box
        if pack_end:
            row_box.pack_end(switch, False, False, 6)
        else:
            row_box.pack_start(switch, False, False, 6)
        
        # Get actions and value
        action_on = (sub.attrs.get("action_on", "") or "").strip()
        action_off = (sub.attrs.get("action_off", "") or "").strip()
        value_cmd = (sub.attrs.get("value", "") or "").strip()
        refresh = float(sub.attrs.get("refresh", parent_feat.attrs.get("refresh", DEFAULT_REFRESH_SEC)))
        
        # Handle dynamic visibility for id() and !id() conditions
        if_condition = (sub.attrs.get("if", "") or "").strip()
        if if_condition:
            # Track this widget for dynamic visibility updates
            self._conditional_widgets.append((switch, if_condition))
            # Don't initially hide - let the condition be evaluated normally
        
        # Always show switch initially to prevent blinking
        switch.set_visible(True)
        
        # Store last state to avoid unnecessary updates and track user interactions
        last_state = [None]  # Use list to make it mutable in nested function
        switch_state = {"updating": False, "last_user_change": 0}
        
        def update_switch_state(val):
            """Update switch state based on command output"""
            try:
                import time
                # Don't update if we just changed it (within 1 second)
                if time.time() - switch_state["last_user_change"] < 1.0:
                    return
                
                # Handle both string and boolean values
                if isinstance(val, bool):
                    is_on = val
                else:
                    normalized = normalize_bool_str(val)
                    is_on = normalized
                
                # Check if state actually changed to avoid unnecessary updates and blinking
                if last_state[0] is not None and last_state[0] == is_on:
                    return  # No change, don't update
                
                last_state[0] = is_on
                
                # Only update if state actually changed and not currently updating
                if not switch_state["updating"] and switch.get_active() != is_on:
                    switch_state["updating"] = True
                    switch.set_active(is_on)
                    switch.set_state(is_on)  # Also set the state for GtkSwitch
                    switch_state["updating"] = False
                
                # Don't change visibility - keep switch always visible once created
                # Register ID when content is present
                register_element_id(sub, self.rendered_ids)
                
            except Exception as e:
                print(f"Error updating switch state: {e}")
                last_state[0] = None
        
        def on_switch_toggled(_switch, state):
            """Handle switch toggle events"""
            try:
                import time
                # Ignore switch events triggered by refresh updates
                if switch_state["updating"]:
                    return False  # Return False to allow the state change
                
                self.reset_inactivity_timer()  # Reset timer on interaction
                
                # Mark that user just changed it
                switch_state["last_user_change"] = time.time()
                
                if state and action_on:
                    if self.debouncer.allow(f"switch_on:{action_on}"):
                        threading.Thread(target=lambda: run_shell_capture(action_on), daemon=True).start()
                elif not state and action_off:
                    if self.debouncer.allow(f"switch_off:{action_off}"):
                        threading.Thread(target=lambda: run_shell_capture(action_off), daemon=True).start()
                
                return False  # Return False to allow the state change
            except Exception as e:
                print(f"Error in switch toggle handler: {e}")
                return False
        
        # Connect the switch signal - use "state-set" for GtkSwitch
        switch.connect("state-set", on_switch_toggled)
        
        # Set up value monitoring if provided
        if is_cmd(value_cmd):
            c = cmd_of(value_cmd)
            
            # Get initial state
            initial_val = run_shell_capture(c).strip()
            if initial_val:
                update_switch_state(initial_val)
            # Don't hide switch if no initial value - keep it visible
            
            def upd(val: str, _switch=switch, _sub=sub, _core=self):
                txt = (val or "").strip()
                if txt and txt.lower() != "null":
                    update_switch_state(txt)
                    # Don't change visibility - keep switch always visible
                    register_element_id(_sub, _core.rendered_ids)
                # Don't hide switch on empty values to prevent blinking
            
            self.refreshers.append(RefreshTask(upd, c, refresh))
        
        elif value_cmd:
            # Static value
            update_switch_state(value_cmd)
        else:
            # No value command - switch starts in off state but is functional
            switch.set_active(False)
            register_element_id(sub, self.rendered_ids)
        
        return switch

    def build_tab(self, parent_feat, sub, row_box, pack_end=False):
        """Build a tab button that controls content visibility"""
        text = (sub.attrs.get("display", "") or "Tab").strip()
        target = (sub.attrs.get("target", "") or "").strip()

        # Tab is a toggle button that looks selected when active
        tab_btn = Gtk.ToggleButton.new_with_label(_(text))
        tab_btn.get_style_context().add_class("cc-tab")
        tab_btn.set_can_focus(True)

        # Store target ID for content switching
        tab_btn._tab_target = target

        # Get alignment from attribute (default: center)
        align_attr = (sub.attrs.get("align", "center") or "center").strip().lower()
        if align_attr == "left":
            tab_btn.set_halign(Gtk.Align.START)
        elif align_attr == "right":
            tab_btn.set_halign(Gtk.Align.END)
        else:  # center (default)
            tab_btn.set_halign(Gtk.Align.CENTER)

        (row_box.pack_end if pack_end else row_box.pack_start)(tab_btn, False, False, 3)

        # Handle dynamic visibility for id() and !id() conditions
        if_condition = (sub.attrs.get("if", "") or "").strip()
        if if_condition:
            # Track this widget for dynamic visibility updates
            self._conditional_widgets.append((tab_btn, if_condition))
            # Initially hide, will be shown after IDs are registered
            tab_btn.set_visible(False)

        # Register ID for tabs (they always produce visual content)
        register_element_id(sub, self.rendered_ids)

        return tab_btn


    def build_doc(self, parent_feat, sub, row_box, pack_end=False):
        """Build a button that opens a document viewer, without initial flash when content is empty,
        and dynamically integrates with controller focus when added/removed."""
        name = (sub.attrs.get("display", "") or "View").strip()
        content = (sub.attrs.get("content", "") or "").strip()
        refresh = float(sub.attrs.get("refresh", parent_feat.attrs.get("refresh", DEFAULT_REFRESH_SEC)))

        align_attr = (sub.attrs.get("align", "center") or "center").strip().lower()

        # Holds the current button and path; both start absent
        state = {
            "btn": None,     # Gtk.Button or None
            "path": None     # str or None
        }

        # Determine navigation context: are we inside a feature row (left/right with _items),
        # or inside a focusable vgroup/hgroup cell (cell_controls)?
        # We try to find the nearest EventBox (cell) by walking up the parents of row_box.
        def find_cell_eventbox(widget):
            w = widget
            try:
                while w is not None:
                    if isinstance(w, Gtk.EventBox) and hasattr(w, "get_style_context") and "vgroup-cell" in w.get_style_context().list_classes():
                        return w
                    w = w.get_parent()
            except Exception:
                pass
            return None

        # Feature row is two levels up: row_box belongs to a Gtk.EventBox row created by _build_feature_row
        feature_row = None
        try:
            p = row_box.get_parent()
            if isinstance(p, Gtk.EventBox):
                feature_row = p
        except Exception:
            feature_row = None

        cell_ev = find_cell_eventbox(row_box)

        def add_to_navigation(btn: Gtk.Button):
            """Add button to controller navigation structures based on context."""
            try:
                # Feature row: add to _items for left/right selection
                if feature_row is not None:
                    if not hasattr(feature_row, "_items"):
                        feature_row._items = []
                        feature_row._item_index = 0
                    feature_row._items.append(btn)
                # Cell context: add into its controls list so row focus can navigate within the cell
                if cell_ev is not None:
                    if not hasattr(cell_ev, "_control_index"):
                        cell_ev._control_index = 0
                    # Find the tuple (cell_event, controls) in the parent row._cells
                    # The parent row is the EventBox that owns the cell_ev.
                    parent_row = None
                    rp = cell_ev.get_parent()
                    while rp is not None and not isinstance(rp, Gtk.EventBox):
                        rp = rp.get_parent()
                    if isinstance(rp, Gtk.EventBox) and hasattr(rp, "_cells"):
                        parent_row = rp
                    if parent_row:
                        for i, (cev, controls) in enumerate(parent_row._cells):
                            if cev is cell_ev:
                                controls.append(btn)
                                # Mark row as selectable if it wasn’t
                                if not hasattr(parent_row, "_on_activate"):
                                    parent_row._on_activate = None
                                # Ensure row is registered (it already is if _cells exists)
                                break
            except Exception:
                pass

        def remove_from_navigation(btn: Gtk.Button):
            """Remove button from controller navigation structures."""
            try:
                if feature_row is not None and hasattr(feature_row, "_items"):
                    if btn in feature_row._items:
                        feature_row._items.remove(btn)
                        # Clamp index
                        if hasattr(feature_row, "_item_index"):
                            feature_row._item_index = max(0, min(len(feature_row._items) - 1, feature_row._item_index))
                if cell_ev is not None:
                    parent_row = None
                    rp = cell_ev.get_parent()
                    while rp is not None and not isinstance(rp, Gtk.EventBox):
                        rp = rp.get_parent()
                    if isinstance(rp, Gtk.EventBox) and hasattr(rp, "_cells"):
                        parent_row = rp
                    if parent_row:
                        for i, (cev, controls) in enumerate(parent_row._cells):
                            if cev is cell_ev and btn in controls:
                                controls.remove(btn)
                                # Clamp control index
                                if hasattr(cev, "_control_index"):
                                    cev._control_index = max(0, min(len(controls) - 1, cev._control_index))
                                break
            except Exception:
                pass

        def make_button():
            """Create and pack the button with proper styling; do not call show explicitly."""
            btn = Gtk.Button.new_with_label(_(name))
            btn.get_style_context().add_class("cc-button")
            btn.set_can_focus(True)

            if align_attr == "left":
                btn.set_halign(Gtk.Align.START)
            elif align_attr == "right":
                btn.set_halign(Gtk.Align.END)
            else:
                btn.set_halign(Gtk.Align.CENTER)

            (row_box.pack_end if pack_end else row_box.pack_start)(btn, False, False, 6)
            # Integrate into navigation now that it's packed
            add_to_navigation(btn)
            return btn

        def open_doc_viewer(file_path: str):
            if not file_path:
                return
            self._dialog_open = True
            self._about_to_show_dialog = True
            self.disable_timer()
            self._suspend_inactivity_timer = True
            try:
                def docviewer_on_destroy():
                    # Resume inactivity timer
                    self.reset_inactivity_timer()
                    self._suspend_inactivity_timer = False
                    self._dialog_open = False
                    self._handle_gamepad_action = self._handle_gamepad_action_main
                    # Disable continuous actions when returning to main window
                    self.disable_gamepad_continuous_actions()

                def docviewer_on_quit():
                    self.quit()

                docviewer = DocViewer()
                docviewer.open(self.window, file_path, docviewer_on_destroy, docviewer_on_quit)
                self._handle_gamepad_action = docviewer.handle_gamepad_action
                # Enable continuous actions for document viewer navigation
                self.enable_gamepad_continuous_actions()
                self._about_to_show_dialog = False
            except Exception as e:
                self._dialog_open = False
                print(e)

        def connect_click(btn):
            btn.connect("clicked", lambda *_: (open_doc_viewer(state["path"]) if state["path"] else None))

        def register_or_unregister_id(visible: bool):
            if visible:
                register_element_id(sub, self.rendered_ids)
            else:
                elem_id = sub.attrs.get("id", "").strip()
                if elem_id and elem_id in self.rendered_ids:
                    self.rendered_ids.discard(elem_id)

        def ensure_button_visible(path: str):
            """Ensure button exists and is visible with a valid path, and part of navigation."""
            # Create the button if it doesn't exist yet
            if state["btn"] is None:
                btn = make_button()
                state["btn"] = btn
                connect_click(btn)
            # Show and register ID
            state["btn"].set_visible(True)
            register_or_unregister_id(True)
            state["path"] = path

        def ensure_button_absent():
            """Ensure button is removed and ID is unregistered, and navigation is updated."""
            state["path"] = None
            register_or_unregister_id(False)
            if state["btn"] is not None:
                # Remove from navigation first
                remove_from_navigation(state["btn"])
                # Remove the button from the row to avoid any flash on subsequent show_all calls
                try:
                    if state["btn"].get_parent() is row_box:
                        row_box.remove(state["btn"])
                except Exception:
                    pass
                state["btn"] = None

        # Static content
        if content and not is_cmd(content):
            path = content.strip()
            if not path or path.lower() == "null":
                # Do not create or pack any button → no flash and no navigation entry
                return None
            # Valid at startup: create and show button, add to navigation
            ensure_button_visible(path)
            return state["btn"]

        # Dynamic content via ${...}
        if is_cmd(content):
            c = cmd_of(content)

            # Initial evaluation before creating any widget
            initial_path = run_shell_capture(c).strip()
            if not initial_path or initial_path.lower() == "null":
                # Keep absent → no flash, no navigation
                state["path"] = None
            else:
                ensure_button_visible(initial_path)

            # Refresh task toggles presence cleanly
            def upd(val: str):
                path = (val or "").strip()
                if path and path.lower() != "null":
                    # Ensure button exists and is visible with updated path
                    ensure_button_visible(path)
                else:
                    # Remove button and unregister ID
                    ensure_button_absent()

            self.refreshers.append(RefreshTask(lambda v, f=upd: f(v), c, refresh))
            # Return button (may be None if initial path invalid)
            return state["btn"]

        # No content provided → do not render
        return None


    def build_img(self, parent_feat, sub, row_box, pack_end=False):
        """Build an image widget from file path, URL, or ${...} command"""
        import urllib.request
        from gi.repository import GdkPixbuf

        disp = (sub.attrs.get("display", "") or "").strip()
        width = sub.attrs.get("width", "")
        height = sub.attrs.get("height", "")
        refresh = float(sub.attrs.get("refresh", parent_feat.attrs.get("refresh", DEFAULT_REFRESH_SEC)))

        # Parse width/height - handle both pixels and percentages
        def parse_dimension(value: str, reference_size: int = 100) -> int | None:
            """Parse dimension value, supporting both pixels and percentages"""
            if not value:
                return None
            
            value = value.strip()
            if value.endswith('%'):
                # Parse percentage
                try:
                    percentage = float(value[:-1])
                    return int(reference_size * percentage / 100)
                except ValueError:
                    return None
            else:
                # Parse pixels
                try:
                    return int(value)
                except ValueError:
                    return None
        
        # Use actual window dimensions if available, otherwise use reasonable defaults
        if hasattr(self, '_window_width') and hasattr(self, '_max_height'):
            reference_width = self._window_width
            reference_height = self._max_height
        else:
            # Fallback to reasonable defaults
            reference_width = 800
            reference_height = 600
        
        target_width = parse_dimension(width, reference_width)
        target_height = parse_dimension(height, reference_height)

        img = Gtk.Image()

        # Set size request if dimensions are specified to ensure consistent sizing
        if target_width or target_height:
            img.set_size_request(target_width or -1, target_height or -1)

        # Get alignment from attribute (default: center)
        align_attr = (sub.attrs.get("align", "center") or "center").strip().lower()
        if align_attr == "left":
            img.set_halign(Gtk.Align.START)
        elif align_attr == "right":
            img.set_halign(Gtk.Align.END)
        else:  # center (default)
            img.set_halign(Gtk.Align.CENTER)

        (row_box.pack_end if pack_end else row_box.pack_start)(img, False, False, 6)

        # Register ID for img elements (they always produce visual content)
        register_element_id(sub, self.rendered_ids)

        def load_image(path_or_url: str):
            """Load image from file path or URL"""
            try:
                path_or_url = path_or_url.strip()
                if not path_or_url:
                    return None

                pixbuf = None

                # Check if it's a URL
                if path_or_url.startswith(("http://", "https://")):
                    # Download from URL
                    with urllib.request.urlopen(path_or_url, timeout=5) as response:
                        data = response.read()
                        loader = GdkPixbuf.PixbufLoader()
                        loader.write(data)
                        loader.close()
                        pixbuf = loader.get_pixbuf()
                else:
                    # Load from file
                    if os.path.exists(path_or_url):
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path_or_url)

                if pixbuf:
                    # Scale if needed
                    orig_width = pixbuf.get_width()
                    orig_height = pixbuf.get_height()

                    if target_width and target_height:
                        # Both specified
                        pixbuf = pixbuf.scale_simple(target_width, target_height, GdkPixbuf.InterpType.BILINEAR)
                    elif target_width:
                        # Only width specified, maintain aspect ratio
                        aspect = orig_height / orig_width
                        new_height = int(target_width * aspect)
                        pixbuf = pixbuf.scale_simple(target_width, new_height, GdkPixbuf.InterpType.BILINEAR)
                    elif target_height:
                        # Only height specified, maintain aspect ratio
                        aspect = orig_width / orig_height
                        new_width = int(target_height * aspect)
                        pixbuf = pixbuf.scale_simple(new_width, target_height, GdkPixbuf.InterpType.BILINEAR)

                    return pixbuf
            except Exception as e:
                print(f"Error loading image from '{path_or_url}': {e}")
            return None

        def update_image(path_or_url: str):
            """Update the image widget"""
            def do_load():
                pixbuf = load_image(path_or_url)
                if pixbuf:
                    GLib.idle_add(lambda pb=pixbuf: img.set_from_pixbuf(pb) or False)
            # Load in background thread to avoid blocking
            threading.Thread(target=do_load, daemon=True).start()

        # Check if display is a command or static path
        if is_cmd(disp):
            # Dynamic image path from command
            c = cmd_of(disp)
            def upd(val: str, _img=img):
                update_image(val)
            self.refreshers.append(RefreshTask(upd, c, refresh))
        elif disp:
            # Static image path - load immediately
            update_image(disp)

        return img

    def build_qrcode(self, parent_feat, sub, row_box, pack_end=False):
        """Build a QR code image widget from text, URL, or ${...} command"""
        try:
            import qrcode
            from io import BytesIO
            from gi.repository import GdkPixbuf
        except ImportError:
            # If qrcode library is not available, show error message
            lbl = Gtk.Label(label="[qrcode library not installed]")
            lbl.get_style_context().add_class("value")
            (row_box.pack_end if pack_end else row_box.pack_start)(lbl, False, False, 6)
            return lbl

        disp = (sub.attrs.get("display", "") or "").strip()
        width = sub.attrs.get("width", "")
        height = sub.attrs.get("height", "")
        refresh = float(sub.attrs.get("refresh", parent_feat.attrs.get("refresh", DEFAULT_REFRESH_SEC)))
        qrcode_style = sub.attrs.get("style")
        qrcode_logo = sub.attrs.get("logo")
        qrcode_font = sub.attrs.get("font")

        # Parse width/height - handle both pixels and percentages
        def parse_dimension(value: str, reference_size: int = 100) -> int | None:
            """Parse dimension value, supporting both pixels and percentages"""
            if not value:
                return None
            
            value = value.strip()
            if value.endswith('%'):
                # Parse percentage
                try:
                    percentage = float(value[:-1])
                    return int(reference_size * percentage / 100)
                except ValueError:
                    return None
            else:
                # Parse pixels
                try:
                    return int(value)
                except ValueError:
                    return None
        
        # Use actual window dimensions if available, otherwise use reasonable defaults
        if hasattr(self, '_window_width') and hasattr(self, '_max_height'):
            reference_width = self._window_width
            reference_height = self._max_height
        else:
            # Fallback to reasonable defaults
            reference_width = 800
            reference_height = 600

        # Parse width/height - QR codes are square, so if only one dimension is specified, use it for both
        # If neither is specified, default to 160x160
        parsed_width = parse_dimension(width, reference_width)
        parsed_height = parse_dimension(height, reference_height)
        
        if parsed_width and parsed_height:
            target_width = parsed_width
            target_height = parsed_height
        elif parsed_width:
            target_width = parsed_width
            target_height = parsed_width  # Square
        elif parsed_height:
            target_width = parsed_height  # Square
            target_height = parsed_height
        else:
            target_width = 160
            target_height = 160

        img = Gtk.Image()
        # Set size request to ensure consistent sizing with regular images
        img.set_size_request(target_width, target_height)

        # Get alignment from attribute (default: center)
        align_attr = (sub.attrs.get("align", "center") or "center").strip().lower()
        if align_attr == "left":
            img.set_halign(Gtk.Align.START)
        elif align_attr == "right":
            img.set_halign(Gtk.Align.END)
        else:  # center (default)
            img.set_halign(Gtk.Align.CENTER)

        (row_box.pack_end if pack_end else row_box.pack_start)(img, False, False, 6)

        def generate_qrcode(data: str, qrcode_style: str | None, qrcode_logo: str | None, qrcode_font: str | None):
            """Generate QR code from data string"""
            try:
                data = data.strip()
                if not data or data == "null":
                    return None

                bg_hex = (parent_feat.attrs.get("bg", "") or sub.attrs.get("bg", "") or "#ffffff").strip()

                def is_dark(hex_color: str) -> bool:
                    hex_color = hex_color.lstrip('#')
                    r = int(hex_color[0:2], 16) / 255.0
                    g = int(hex_color[2:4], 16) / 255.0
                    b = int(hex_color[4:6], 16) / 255.0

                    def srgb_to_linear(c):
                        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

                    R, G, B = map(srgb_to_linear, (r, g, b))
                    luminance = 0.2126 * R + 0.7152 * G + 0.0722 * B
                    return luminance < 0.5

                dark_bg = is_dark(bg_hex)
                fill_color = "white" if dark_bg else "black"

                # Generate QR code
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=0,
                )
                qr.add_data(data)
                qr.make(fit=True)

                # Create PIL image
                pil_img = qr.make_image(fill_color=fill_color, back_color=bg_hex)

                if qrcode_style == "card":
                    pil_img = enchancementQr_card(pil_img, qrcode_style, qrcode_logo, qrcode_font)

                # Convert PIL image to pixbuf
                buffer = BytesIO()
                pil_img.save(buffer, format='PNG')
                buffer.seek(0)

                loader = GdkPixbuf.PixbufLoader.new_with_type('png')
                loader.write(buffer.read())
                loader.close()
                pixbuf = loader.get_pixbuf()

                # Scale to target size
                if pixbuf:
                    pixbuf = pixbuf.scale_simple(target_width, target_height, GdkPixbuf.InterpType.BILINEAR)
                return pixbuf
            except Exception as e:
                print(f"Error generating QR code for '{data}': {e}")
                return None

        def enchancementQr_card(qr_img, qrcode_style, qrcode_logo, qrcode_font):
            from PIL import Image, ImageDraw, ImageFont

            img_width = 300
            card_radius = int(img_width/14.0)
            color_bg = (0, 0, 0)
            border_size = 4
            qr_border_size = border_size * 2
            qr_width = img_width - border_size*2 - qr_border_size*2
            resample = Image.LANCZOS
            logo_height = qr_width // 4
            footer_height = logo_height // 2
            footer_text = "SCAN"
            footer_border = footer_height // 3
            footer_color = (150, 150, 200)
            img_height = qr_width + border_size*2 + qr_border_size*2

            if qrcode_logo:
                img_height = img_height + logo_height

            # init image
            card_img = Image.new('RGBA', (img_width, img_height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(card_img)

            # background
            draw.rounded_rectangle([0, 0, img_width, img_height], card_radius, fill=color_bg)

            # logo
            if qrcode_logo:
                logo_raw = Image.open(qrcode_logo).convert("RGBA")
                ratio = logo_height / logo_raw.height
                logo_img = logo_raw.resize((int(logo_raw.width*ratio), logo_height), resample)
                card_img.paste(logo_img, ((img_width - logo_img.width) // 2, border_size*2), logo_img)

            # qr code
            qr_img = qr_img.resize((qr_width, qr_width), resample)
            qr_img = qr_img.convert("RGBA")
            pos_x = border_size + qr_border_size
            pos_y = border_size + qr_border_size
            if qrcode_logo:
                pos_y = pos_y + logo_height
            card_img.paste(qr_img, (pos_x, pos_y), qr_img)

            # footer
            if qrcode_font:
                footer_font = ImageFont.truetype(qrcode_font, footer_height)
                footer_width = draw.textlength(footer_text, font=footer_font)
                draw.rounded_rectangle([(img_width-footer_width)//2-footer_border, img_height-footer_height-border_size,
                                        (img_width+footer_width)//2+footer_border, img_height-border_size], 10, fill=(0,0,100))
                draw.text(((img_width-footer_width)//2, img_height-footer_height-border_size), footer_text, fill=footer_color, font=footer_font)

            # border
            for i in range(border_size):
                draw.rounded_rectangle([i, i, img_width-i, img_height-i], card_radius, outline=(200+i*5, 200+i*5, 220), width=1)

            return card_img

        def update_qrcode(data: str, qrcode_style: str | None, qrcode_logo: str | None, qrcode_font: str | None):
            """Update the QR code image widget"""
            def do_generate():
                pixbuf = generate_qrcode(data, qrcode_style, qrcode_logo, qrcode_font)
                if pixbuf:
                    # Show and update image when valid data
                    GLib.idle_add(lambda pb=pixbuf: (img.set_from_pixbuf(pb), img.set_visible(True)) or False)
                else:
                    # Hide when data is empty/null
                    def hide():
                        img.clear()
                        img.set_visible(False)
                        return False
                    GLib.idle_add(hide)
            threading.Thread(target=do_generate, daemon=True).start()

        # Check if display is a command or static text
        if is_cmd(disp):
            # Dynamic QR code from command output
            c = cmd_of(disp)

            # Get initial value to check if we should render at all
            initial_val = run_shell_capture(c).strip()
            element_id = (sub.attrs.get("id", "") or "").strip()
            
            if not initial_val or initial_val.lower() == "null":
                # Keep the widget hidden; allow later refresh to show it when valid
                img.set_visible(False)
            else:
                # Initial render is valid
                if element_id:
                    register_element_id(sub, self.rendered_ids)
                    self._recompute_conditionals()
                update_qrcode(initial_val, qrcode_style, qrcode_logo, qrcode_font)

            def upd(val: str, _img=img, _sub=sub, _core=self, _qrcode_style=qrcode_style, _qrcode_logo=qrcode_logo, _qrcode_font=qrcode_font):
                txt = (val or "").strip()
                element_id = (_sub.attrs.get("id", "") or "").strip()
                
                if txt and txt.lower() != "null":
                    update_qrcode(txt, _qrcode_style, _qrcode_logo, _qrcode_font)
                    # Only recompute if this element has an ID (affects conditionals)
                    if element_id:
                        register_element_id(_sub, _core.rendered_ids)
                        GLib.idle_add(lambda: (_img.set_visible(True), _core._recompute_conditionals(), False)[2])
                    else:
                        GLib.idle_add(lambda: (_img.set_visible(True), False)[1])
                else:
                    # Clear, hide, and unregister ID when content disappears
                    def hide_and_unregister():
                        try:
                            _img.clear()
                        except:
                            pass
                        _img.set_visible(False)
                        # Only recompute if this element had an ID that's being removed
                        if element_id and element_id in _core.rendered_ids:
                            _core.rendered_ids.discard(element_id)
                            _core._recompute_conditionals()
                        return False
                    GLib.idle_add(hide_and_unregister)

            self.refreshers.append(RefreshTask(upd, c, refresh))

            # Generate initial QR code
            update_qrcode(initial_val, qrcode_style, qrcode_logo, qrcode_font)

        elif disp:
            # Static QR code - generate immediately
            if disp.strip() and disp.strip() != "null":
                # Register ID immediately for static QR codes
                register_element_id(sub, self.rendered_ids)
                update_qrcode(disp, qrcode_style, qrcode_logo, qrcode_font)
            else:
                # Don't render if empty
                row_box.remove(img)
                return None
        else:
            # No display value; do not render
            row_box.remove(img)
            return None

        return img

    def build_progressbar(self, parent_feat, sub, row_box, pack_end=False):
        """Build a progress bar widget from value or ${...} command"""
        
        # Create container for progress bar with overlay text
        container = Gtk.Overlay()
        container.set_halign(Gtk.Align.CENTER)
        
        # Create progress bar
        progress = Gtk.ProgressBar()
        progress.get_style_context().add_class("cc-progressbar")
        progress.set_show_text(False)  # We'll overlay our own text
        
        # Create text label for displaying the value (overlaid on progress bar)
        text_label = Gtk.Label()
        text_label.get_style_context().add_class("cc-progressbar-text")
        text_label.set_xalign(0.5)  # Center the text horizontally
        text_label.set_halign(Gtk.Align.CENTER)  # Center the label widget
        text_label.set_valign(Gtk.Align.CENTER)  # Center vertically on the progress bar
        
        # Add progress bar as base layer
        container.add(progress)
        # Add text label as overlay
        container.add_overlay(text_label)
        
        # Add to row box
        if pack_end:
            row_box.pack_end(container, False, False, 6)
        else:
            row_box.pack_start(container, False, False, 6)
        
        # Get min/max values (default to 0-100)
        min_val = float(sub.attrs.get("min", "0"))
        max_val = float(sub.attrs.get("max", "100"))
        
        # Ensure valid range
        if max_val <= min_val:
            max_val = min_val + 100
        
        # Apply ID as widget name for CSS
        elem_id = (sub.attrs.get("id", "") or "").strip()
        if elem_id:
            progress.set_name(elem_id)
            text_label.set_name(f"{elem_id}-text")
        
        # Get display value
        disp = (sub.attrs.get("display", "") or "").strip()
        refresh = float(sub.attrs.get("refresh", parent_feat.attrs.get("refresh", DEFAULT_REFRESH_SEC)))
        
        # Store last value to avoid unnecessary updates
        last_value = [None]  # Use list to make it mutable in nested function
        
        def update_progress(value_str: str):
            """Update the progress bar with a new value"""
            try:
                # Parse the value
                value_str = value_str.strip()
                if not value_str or value_str.lower() == "null":
                    # Hide when no value
                    container.set_visible(False)
                    last_value[0] = None
                    return
                
                # Try to parse as number
                try:
                    value = float(value_str)
                except ValueError:
                    # If not a number, try to extract number from string
                    import re
                    match = re.search(r'[-+]?\d*\.?\d+', value_str)
                    if match:
                        value = float(match.group())
                    else:
                        container.set_visible(False)
                        last_value[0] = None
                        return
                
                # Clamp value to range
                value = max(min_val, min(max_val, value))
                
                # Check if value actually changed to avoid unnecessary updates
                if last_value[0] is not None and abs(value - last_value[0]) < 0.5:  # Less than 0.5% change
                    return
                
                last_value[0] = value
                
                # Calculate fraction (0.0 to 1.0)
                new_fraction = (value - min_val) / (max_val - min_val) if max_val > min_val else 0.0
                
                # Update progress bar
                progress.set_fraction(new_fraction)
                
                # Update text label - show the actual value with % symbol
                if value == int(value):
                    text_label.set_text(f"{int(value)}%")
                else:
                    text_label.set_text(f"{value:.1f}%")
                
                # Show container
                container.set_visible(True)
                
            except Exception as e:
                print(f"Error updating progress bar: {e}")
                container.set_visible(False)
                last_value[0] = None
        
        # Handle dynamic visibility for id() and !id() conditions
        if_condition = (sub.attrs.get("if", "") or "").strip()
        if if_condition:
            # Track this widget for dynamic visibility updates
            self._conditional_widgets.append((container, if_condition))
            # Initially hide, will be shown after IDs are registered
            container.set_visible(False)
        
        # Check if display contains ${...} command substitution
        if is_cmd(disp):
            # Dynamic progress bar from command output
            c = cmd_of(disp)
            
            # Get initial value to check if we should render at all
            initial_val = run_shell_capture(c).strip()
            element_id = (sub.attrs.get("id", "") or "").strip()
            
            if not initial_val or initial_val.lower() == "null":
                # Keep the widget hidden; allow later refresh to show it when valid
                container.set_visible(False)
            else:
                # Initial render is valid
                if element_id:
                    register_element_id(sub, self.rendered_ids)
                    self._recompute_conditionals()
                update_progress(initial_val)
            
            def upd(val: str, _container=container, _sub=sub, _core=self):
                txt = (val or "").strip()
                element_id = (_sub.attrs.get("id", "") or "").strip()
                
                if txt and txt.lower() != "null":
                    update_progress(txt)
                    # Only recompute if this element has an ID (affects conditionals)
                    if element_id:
                        register_element_id(_sub, _core.rendered_ids)
                        GLib.idle_add(lambda: (_container.set_visible(True), _core._recompute_conditionals(), False)[2])
                    else:
                        GLib.idle_add(lambda: (_container.set_visible(True), False)[1])
                else:
                    # Hide and unregister ID when content disappears
                    def hide_and_unregister():
                        _container.set_visible(False)
                        # Only recompute if this element had an ID that's being removed
                        if element_id and element_id in _core.rendered_ids:
                            _core.rendered_ids.discard(element_id)
                            _core._recompute_conditionals()
                        return False
                    GLib.idle_add(hide_and_unregister)
            
            self.refreshers.append(RefreshTask(upd, c, refresh))
            
            # Set initial value
            update_progress(initial_val)
            
        elif disp:
            # Static progress bar - set value immediately
            if disp.strip() and disp.strip() != "null":
                # Register ID immediately for static progress bars
                register_element_id(sub, self.rendered_ids)
                update_progress(disp)
            else:
                # Don't render if empty
                row_box.remove(container)
                return None
        else:
            # No display value; do not render
            row_box.remove(container)
            return None
        
        return container



# ---- Builders for containers per new schema ----
def ui_build_containers(core: UICore, xml_root):
    win = core.build_window()
    core.apply_css()

    # Main container with header and scrollable content
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    outer.set_border_width(10)
    win.add(outer)

    # Header vgroups (role="header") — non-selectable, always visible
    header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    for child in xml_root.children:
        if child.kind == "vgroup" and (child.attrs.get("role", "") or "").strip().lower() == "header":
            row = _build_vgroup_row(core, child, is_header=True)
            if row:
                header_box.pack_start(row, False, False, 0)

    if header_box.get_children():
        outer.pack_start(header_box, False, False, 0)
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.get_style_context().add_class("section-separator")
        outer.pack_start(sep, False, False, 6)

    # Scrollable content area - allow both horizontal and vertical scrolling
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_propagate_natural_width(False)  # Don't let content expand window width
    scrolled.set_propagate_natural_height(True)  # DO propagate height so window sizes correctly
    # Set a reasonable minimum height for the scrolled area (with 80px room for header/footer)
    available = max(300, core._max_height - 80)
    scrolled.set_max_content_height(available)
    outer.pack_start(scrolled, True, True, 0)

    content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    scrolled.add(content_box)

    # First pass: find tab rows and collect hgroup IDs
    tab_targets = set()
    for child in xml_root.children:
        if child.kind == "feature":
            # Check if this feature has tabs
            has_tabs = any(sub.kind == "tab" for sub in child.children)
            if has_tabs:
                # Collect tab targets
                for sub in child.children:
                    if sub.kind == "tab":
                        target = (sub.attrs.get("target", "") or "").strip()
                        if target:
                            tab_targets.add(target)

    # Second pass: build ALL children in XML order (features, vgroups, hgroups)
    tab_row = None
    for child in xml_root.children:
        if child.kind == "feature":
            # Check if feature should be rendered based on 'if' condition
            if should_render_element(child, core.rendered_ids):
                fr = _build_feature_row(core, child)
                if fr:
                    content_box.pack_start(fr, False, False, 3)
                    # Check if this is a tab row
                    if hasattr(fr, '_tabs') and fr._tabs:
                        tab_row = fr
        elif child.kind == "vgroup":
            role = (child.attrs.get("role", "") or "").strip().lower()
            # Skip header and footer vgroups (they're processed separately)
            if role in ("header", "footer"):
                continue
            vg = _build_vgroup_row(core, child, is_header=False)
            if vg:
                content_box.pack_start(vg, False, False, 0)
        elif child.kind == "hgroup":
            hgroup_id = (child.attrs.get("name", "") or child.attrs.get("display", "")).strip()
            is_tab_content = hgroup_id in tab_targets

            title = (child.attrs.get("display", "") or "").strip()
            target = _get_group_container_new(core, content_box, title)

            # If this is tab content, remove it from content_box (we'll add it back when tab is selected)
            if is_tab_content:
                # Get the frame (parent of target)
                frame = target.get_parent()
                if frame and frame.get_parent() == content_box:
                    content_box.remove(frame)
                    target._frame = frame  # Store frame for later re-insertion
                    # Store content_box reference in tab_row for easy access
                    if not hasattr(tab_row, '_content_box'):
                        tab_row._content_box = content_box

            # Process all children
            has_multiple_vgroups_or_hgroups = sum(1 for s in child.children if s.kind in ("vgroup", "hgroup")) > 1

            if has_multiple_vgroups_or_hgroups:
                # For tab content, stack vgroups/hgroups vertically; otherwise arrange horizontally
                if is_tab_content:
                    # Vertical stacking for tab content
                    for sub in child.children:
                        if sub.kind == "vgroup":
                            vg = _build_vgroup_row(core, sub, is_header=False)
                            if vg:
                                target.pack_start(vg, False, False, 0)
                        elif sub.kind == "hgroup":
                            # Nested hgroup in tab content - create vertical container with title
                            vert_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

                            # Add title if present
                            hgroup_title = (sub.attrs.get("display", "") or sub.attrs.get("name", "")).strip()
                            if hgroup_title:
                                title_label = Gtk.Label(label=_(hgroup_title))
                                title_label.get_style_context().add_class("group-title")
                                title_label.set_xalign(0.0)
                                vert_box.pack_start(title_label, False, False, 0)

                            # Process nested hgroup children
                            for nested_sub in sub.children:
                                if nested_sub.kind == "text":
                                    text_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                                    core.build_text(sub, nested_sub, text_box, align_end=False)
                                    vert_box.pack_start(text_box, False, False, 3)
                                elif nested_sub.kind == "img":
                                    img_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                                    core.build_img(sub, nested_sub, img_box, pack_end=False)
                                    vert_box.pack_start(img_box, False, False, 3)
                                elif nested_sub.kind == "feature":
                                    # Check if feature should be rendered based on 'if' condition
                                    if should_render_element(nested_sub, core.rendered_ids):
                                        fr = _build_feature_row(core, nested_sub)
                                        if fr:
                                            vert_box.pack_start(fr, False, False, 3)

                            target.pack_start(vert_box, False, False, 6)
                        elif sub.kind == "img":
                            # Direct img in tab content
                            img_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                            img_box.set_halign(Gtk.Align.CENTER)
                            core.build_img(child, sub, img_box, pack_end=False)
                            target.pack_start(img_box, False, False, 6)
                        elif sub.kind == "text":
                            # Direct text in tab content
                            text_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                            core.build_text(child, sub, text_box, align_end=False)
                            target.pack_start(text_box, False, False, 6)
                        elif sub.kind == "feature":
                            # Direct feature in tab content
                            # Check if feature should be rendered based on 'if' condition
                            if should_render_element(sub, core.rendered_ids):
                                fr = _build_feature_row(core, sub)
                                if fr:
                                    target.pack_start(fr, False, False, 3)
                else:
                    # Horizontal arrangement for non-tab content
                    horiz_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                    horiz_box.set_homogeneous(True)  # Make vgroups equal width for grid alignment
                    horiz_box.set_halign(Gtk.Align.CENTER)
                    horiz_box.set_size_request(int(core._window_width * 0.95), -1)
                    target.pack_start(horiz_box, False, False, 0)

                    for sub in child.children:
                        if sub.kind == "vgroup":
                            vg = _build_vgroup_row(core, sub, is_header=False)
                            if vg:
                                vg_box = vg.get_child()
                                if vg_box:
                                    vg_box.set_size_request(-1, -1)
                                horiz_box.pack_start(vg, True, True, 6)
                        elif sub.kind == "hgroup":
                            # Nested hgroup - create vertical container with title
                            vert_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

                            # Add title if present
                            hgroup_title = (sub.attrs.get("display", "") or sub.attrs.get("name", "")).strip()
                            if hgroup_title:
                                title_label = Gtk.Label(label=_(hgroup_title))
                                title_label.get_style_context().add_class("group-title")
                                title_label.set_xalign(0.0)
                                vert_box.pack_start(title_label, False, False, 0)

                            for nested_sub in sub.children:
                                if nested_sub.kind == "vgroup":
                                    vg = _build_vgroup_row(core, nested_sub, is_header=False)
                                    if vg:
                                        vg_box = vg.get_child()
                                        if vg_box:
                                            vg_box.set_size_request(-1, -1)
                                        vert_box.pack_start(vg, False, False, 0)
                                elif nested_sub.kind == "feature":
                                    # Check if feature should be rendered based on 'if' condition
                                    if should_render_element(nested_sub, core.rendered_ids):
                                        fr = _build_feature_row(core, nested_sub)
                                        if fr:
                                            fr_box = fr.get_child()
                                            if fr_box:
                                                fr_box.set_size_request(-1, -1)
                                        vert_box.pack_start(fr, False, False, 3)
                                elif nested_sub.kind == "text":
                                    # Direct text in nested hgroup
                                    text_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                                    core.build_text(sub, nested_sub, text_box, align_end=False)
                                    vert_box.pack_start(text_box, False, False, 3)
                                elif nested_sub.kind == "img":
                                    img_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                                    core.build_img(sub, nested_sub, img_box, pack_end=False)
                                    vert_box.pack_start(img_box, False, False, 3)
                            horiz_box.pack_start(vert_box, True, True, 6)
                        elif sub.kind == "img":
                            # Direct img in hgroup horizontal layout
                            img_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                            img_box.set_halign(Gtk.Align.CENTER)
                            core.build_img(child, sub, img_box, pack_end=False)
                            horiz_box.pack_start(img_box, True, True, 6)
                        elif sub.kind == "text":
                            # Direct text in hgroup horizontal layout
                            text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                            core.build_text(child, sub, text_box, align_end=False)
                            horiz_box.pack_start(text_box, True, True, 6)
                        elif sub.kind == "feature":
                            # Direct feature in hgroup horizontal layout
                            # Check if feature should be rendered based on 'if' condition
                            if should_render_element(sub, core.rendered_ids):
                                fr = _build_feature_row(core, sub)
                                if fr:
                                    horiz_box.pack_start(fr, True, True, 6)
            else:
                for sub in child.children:
                    if sub.kind == "vgroup":
                        vg = _build_vgroup_row(core, sub, is_header=False)
                        if vg:
                            target.pack_start(vg, False, False, 0)
                    elif sub.kind == "feature":
                        # Check if feature should be rendered based on 'if' condition
                        if should_render_element(sub, core.rendered_ids):
                            fr = _build_feature_row(core, sub)
                            if fr:
                                target.pack_start(fr, False, False, 3)
                    elif sub.kind == "text":
                        text_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                        text_row.set_border_width(4)
                        core.build_text(child, sub, text_row, align_end=False)
                        target.pack_start(text_row, False, False, 3)
                    elif sub.kind == "img":
                        img_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                        img_row.set_border_width(4)
                        img_row.set_halign(Gtk.Align.CENTER)  # Center the row itself
                        core.build_img(child, sub, img_row, pack_end=False)
                        target.pack_start(img_row, False, False, 3)
                    elif sub.kind == "qrcode":
                        qr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                        qr_row.set_border_width(4)
                        core.build_qrcode(child, sub, qr_row, pack_end=False)
                        target.pack_start(qr_row, False, False, 3)
                    elif sub.kind == "progressbar":
                        progress_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                        progress_row.set_border_width(4)
                        core.build_progressbar(child, sub, progress_row, pack_end=False)
                        target.pack_start(progress_row, False, False, 3)
                    elif sub.kind == "doc":
                        doc_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                        doc_row.set_border_width(4)
                        core.build_doc(child, sub, doc_row, pack_end=False)
                        target.pack_start(doc_row, False, False, 3)

            # If this is tab content, store it
            if is_tab_content and tab_row:
                if not hasattr(tab_row, '_tab_contents'):
                    tab_row._tab_contents = {}
                tab_row._tab_contents[hgroup_id] = target

    win.connect("map", lambda *_: _init_focus(core))
    win.show_all()

    # After show_all, find rows in tab content
    if tab_row and hasattr(tab_row, '_tabs') and tab_row._tabs:
        if hasattr(tab_row, '_tab_contents'):
            # Find all rows that belong to each tab content
            for content_id, content_widget in tab_row._tab_contents.items():
                content_widget._tab_rows = []

                # Find all registered rows inside this content
                def find_rows_in_widget(widget, rows_list):
                    if isinstance(widget, Gtk.EventBox) and widget in core.focus_rows:
                        rows_list.append(widget)
                    if hasattr(widget, 'get_children'):
                        try:
                            for child in widget.get_children():
                                find_rows_in_widget(child, rows_list)
                        except:
                            pass

                find_rows_in_widget(content_widget, content_widget._tab_rows)

    win._tab_row = tab_row
    if tab_row and hasattr(tab_row, '_tabs') and tab_row._tabs:
        # Find first visible tab
        first_tab = None
        for t in tab_row._tabs:
            if t.is_visible():
                first_tab = t
                break
        if first_tab is not None:
            # Activate it (this will trigger the existing toggled handler to show content)
            # Block re-entrancy safety is handled in make_switch_handler
            first_tab.set_active(True)
            # Also set keyboard focus on the tab row itself - use CSS classes
            try:
                ctx = first_tab.get_style_context()
                ctx.add_class("focused-cell")
            except Exception:
                pass

    # Footer vgroups at the bottom
    footer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    footer_box.set_halign(Gtk.Align.CENTER)
    for child in xml_root.children:
        if child.kind == "vgroup" and (child.attrs.get("role", "") or "").strip().lower() == "footer":
            row = _build_vgroup_row(core, child, is_header=True)
            if row:
                footer_box.pack_start(row, False, False, 0)

    if footer_box.get_children():
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.get_style_context().add_class("section-separator")
        outer.pack_start(sep, False, False, 6)
        outer.pack_start(footer_box, False, False, 0)
        # Show footer widgets since they were added after win.show_all()
        sep.show()
        footer_box.show_all()

    return win


def _init_focus(core: UICore):
    if not core.focus_rows:
        return
    
    # Properly clear all highlights from all rows (including item-level highlights)
    for r in core.focus_rows:
        core.unhighlight_row(r)
    
    core.focus_index = 0
    first_row = core.focus_rows[0]
    core._row_set_focused(first_row, True)
    _focus_widget(first_row)

    # If first row has items, highlight the first one
    if hasattr(first_row, "_items") and first_row._items:
        if not hasattr(first_row, "_item_index"):
            first_row._item_index = 0
        item = first_row._items[first_row._item_index]
        ctx = item.get_style_context()
        ctx.add_class("focused-cell")
        ctx.add_class("choice-selected")
        _focus_widget(item)


def _get_group_container_new(core: UICore, parent_box: Gtk.Box, display_title: str):
    title = (display_title or "").strip()
    if title == "":
        return parent_box
    frame = Gtk.Frame()
    frame.get_style_context().add_class("group-frame")
    frame.set_shadow_type(Gtk.ShadowType.IN)
    frame.set_halign(Gtk.Align.CENTER)  # Center the frame
    # Set a consistent width for all groups (90% of window width)
    frame.set_size_request(int(core._window_width * 0.95), -1)
    label = Gtk.Label(label=_(title))
    label.get_style_context().add_class("group-title")
    frame.set_label_widget(label)
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    inner.set_border_width(6)
    frame.add(inner)
    parent_box.pack_start(frame, False, False, 0)
    return inner


def _build_vgroup_row(core: UICore, vg, is_header: bool) -> Gtk.EventBox:
    row = Gtk.EventBox()
    row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
    row_box.set_halign(Gtk.Align.CENTER)  # Center the row contents
    # Set consistent width for all rows (90% of window width)
    row_box.set_size_request(int(core._window_width * 0.95), -1)
    row.add(row_box)
    row.set_above_child(False)
    row.get_style_context().add_class("vgroup-row")

    is_header_row = bool(is_header)

    cells = []

    # Group consecutive text children into a single vertical cell
    i = 0
    children_to_process = vg.children

    while i < len(children_to_process):
        child = children_to_process[i]

        # Handle direct <text> children in vgroup
        if child.kind == "text":
            # Collect consecutive text children
            text_children = [child]
            j = i + 1
            while j < len(children_to_process) and children_to_process[j].kind == "text":
                text_children.append(children_to_process[j])
                j += 1

            # Create a single cell for all consecutive text children
            cell_event = Gtk.EventBox()
            cell_event.get_style_context().add_class("vgroup-cell")
            if len(cells) == 0:
                cell_event.get_style_context().add_class("vgroup-cell-first")

            # Use vertical box if multiple text children
            if len(text_children) > 1:
                cell_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            else:
                cell_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

            # Set cell_box alignment based on first text's align attribute
            align_attr = (text_children[0].attrs.get("align", "center") or "center").strip().lower()
            if align_attr == "left":
                cell_box.set_halign(Gtk.Align.START)
            elif align_attr == "right":
                cell_box.set_halign(Gtk.Align.END)
            else:
                cell_box.set_halign(Gtk.Align.CENTER)
            cell_event.add(cell_box)

            # Build all text elements
            for text_child in text_children:
                if len(text_children) > 1:
                    # For vertical stacking, create a horizontal box for each text
                    text_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    core.build_text(vg, text_child, text_box, align_end=False)
                    cell_box.pack_start(text_box, False, False, 0)
                else:
                    core.build_text(vg, text_child, cell_box, align_end=False)

            # Text-only cells have no controls, so they're not interactive
            cells.append((cell_event, []))
            row_box.pack_start(cell_event, True, True, 12)

            i = j  # Skip the text children we just processed
            continue

        i += 1

        # Handle direct <img> and <qrcode> children in vgroup
        if child.kind in ("img", "qrcode"):
            cell_event = Gtk.EventBox()
            cell_event.get_style_context().add_class("vgroup-cell")
            if len(cells) == 0:
                cell_event.get_style_context().add_class("vgroup-cell-first")

            cell_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            cell_event.add(cell_box)

            # Build the img or qrcode element
            if child.kind == "img":
                core.build_img(vg, child, cell_box, pack_end=False)
            else:  # qrcode
                core.build_qrcode(vg, child, cell_box, pack_end=False)

            # Img/qrcode-only cells have no controls, so they're not interactive
            cells.append((cell_event, []))
            row_box.pack_start(cell_event, True, True, 12)
            continue

        # Handle nested <vgroup> children in vgroup - treat as a cell
        if child.kind == "vgroup":
            cell_event = Gtk.EventBox()
            cell_event.get_style_context().add_class("vgroup-cell")
            if len(cells) == 0:
                cell_event.get_style_context().add_class("vgroup-cell-first")

            # Create a horizontal box to hold the nested vgroup's features inline
            cell_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            cell_event.add(cell_box)

            # Process nested vgroup's children inline
            for nested_child in child.children:
                # Handle direct text/img/qrcode children in nested vgroup
                if nested_child.kind == "text":
                    core.build_text(child, nested_child, cell_box, align_end=False)
                    continue
                elif nested_child.kind == "img":
                    core.build_img(child, nested_child, cell_box, pack_end=False)
                    continue
                elif nested_child.kind == "qrcode":
                    core.build_qrcode(child, nested_child, cell_box, pack_end=False)
                    continue
                elif nested_child.kind == "progressbar":
                    core.build_progressbar(child, nested_child, cell_box, pack_end=False)
                    continue

                if nested_child.kind == "feature":
                    # Check if feature should be rendered based on 'if' condition
                    if not should_render_element(nested_child, core.rendered_ids):
                        continue
                        
                    label_text = (nested_child.attrs.get("display", "") or nested_child.attrs.get("name", "") or "").strip()
                    if label_text:
                        lbl = Gtk.Label(label=_(label_text))
                        lbl.get_style_context().add_class("item-text")
                        lbl.set_xalign(0.0)
                        cell_box.pack_start(lbl, False, False, 0)
                        # Register ID for features with labels
                        register_element_id(nested_child, core.rendered_ids)

                    # Add feature children inline
                    for sub in nested_child.children:
                        if sub.kind == "text":
                            core.build_text(nested_child, sub, cell_box, align_end=False)
                        elif sub.kind == "img":
                            core.build_img(nested_child, sub, cell_box, pack_end=False)
                        elif sub.kind == "qrcode":
                            core.build_qrcode(nested_child, sub, cell_box, pack_end=False)
                        elif sub.kind == "progressbar":
                            core.build_progressbar(nested_child, sub, cell_box, pack_end=False)
                        elif sub.kind == "doc":
                            core.build_doc(nested_child, sub, cell_box, pack_end=False)

            cells.append((cell_event, []))
            row_box.pack_start(cell_event, True, True, 12)
            continue

        # Handle nested <hgroup> children in vgroup
        if child.kind == "hgroup":
            cell_event = Gtk.EventBox()
            cell_event.get_style_context().add_class("vgroup-cell")
            if len(cells) == 0:
                cell_event.get_style_context().add_class("vgroup-cell-first")

            cell_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            cell_event.add(cell_box)

            # Track controls for navigation
            cell_controls = []

            # Process hgroup children (features, text, img, etc.)
            for hg_child in child.children:
                if hg_child.kind == "feature":
                    # Check if feature should be rendered based on 'if' condition
                    if not should_render_element(hg_child, core.rendered_ids):
                        continue
                        
                    feat_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

                    label_text = (hg_child.attrs.get("display", "") or hg_child.attrs.get("name", "") or "").strip()
                    if label_text:
                        lbl = Gtk.Label(label=_(label_text))
                        lbl.get_style_context().add_class("item-text")
                        lbl.set_xalign(0.0)
                        feat_box.pack_start(lbl, False, False, 0)
                        # Register ID for features with labels
                        register_element_id(hg_child, core.rendered_ids)

                    # Add feature children
                    for sub in hg_child.children:
                        if sub.kind == "text":
                            core.build_text(hg_child, sub, feat_box, align_end=False)
                        elif sub.kind == "img":
                            core.build_img(hg_child, sub, feat_box, pack_end=False)
                        elif sub.kind == "qrcode":
                            core.build_qrcode(hg_child, sub, feat_box, pack_end=False)
                        elif sub.kind == "progressbar":
                            core.build_progressbar(hg_child, sub, feat_box, pack_end=False)
                        elif sub.kind == "doc":
                            btn = core.build_doc(hg_child, sub, feat_box, pack_end=False)
                            if btn:
                                btn.set_can_focus(True)
                                cell_controls.append(btn)
                        elif sub.kind == "button":
                            btn = core.build_button(hg_child, sub, feat_box, pack_end=False)
                            if btn:
                                btn.set_can_focus(True)
                                cell_controls.append(btn)
                        elif sub.kind == "button_confirm":
                            text = (sub.attrs.get("display", "") or "Confirm?").strip()
                            action = sub.attrs.get("action", "")
                            btn = Gtk.Button.new_with_label(_(text))
                            btn.get_style_context().add_class("cc-button")
                            btn.get_style_context().add_class("cc-button-confirm")
                            btn.set_can_focus(True)
                            feat_box.pack_start(btn, False, False, 6)
                            def on_confirm_click(_w, _core=core, _text=text, _action=action):
                                _core._about_to_show_dialog = True
                                _core._about_to_show_dialog = True; _show_confirm_dialog(_core, _text, _action); _core._about_to_show_dialog = False
                                _core._about_to_show_dialog = False
                            btn.connect("clicked", on_confirm_click)
                            register_element_id(sub, core.rendered_ids)
                            cell_controls.append(btn)

                    cell_box.pack_start(feat_box, False, False, 3)
                elif hg_child.kind == "text":
                    core.build_text(child, hg_child, cell_box, align_end=False)
                elif hg_child.kind == "img":
                    core.build_img(child, hg_child, cell_box, pack_end=False)
                elif hg_child.kind == "qrcode":
                    core.build_qrcode(child, hg_child, cell_box, pack_end=False)
                elif hg_child.kind == "progressbar":
                    core.build_progressbar(child, hg_child, cell_box, pack_end=False)

            # Make cell focusable if it has controls
            if cell_controls:
                cell_event.set_can_focus(True)
                cell_event.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.FOCUS_CHANGE_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
                cell_event._control_index = 0

                def on_cell_click(_w, *_args, _controls=cell_controls):
                    if _controls:
                        _focus_widget(_controls[0])
                        _activate_widget(_controls[0])
                cell_event.connect("button-press-event", on_cell_click)

            cells.append((cell_event, cell_controls))
            row_box.pack_start(cell_event, True, True, 12)
            continue

        if child.kind != "feature":
            continue

        cell_event = Gtk.EventBox()
        cell_event.get_style_context().add_class("vgroup-cell")
        if len(cells) == 0:
            cell_event.get_style_context().add_class("vgroup-cell-first")

        cell_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)  # Reduced spacing
        if core._scale_class == "small":
            cell_box.set_size_request(80, -1)  # Minimum width for grid alignment
        else:
            cell_box.set_size_request(200, -1)  # Minimum width for grid alignment
        cell_event.add(cell_box)

        label_text = (child.attrs.get("display", "") or child.attrs.get("name", "") or "").strip()
        if label_text:
            lbl = Gtk.Label(label=_(label_text))
            lbl.get_style_context().add_class("header" if is_header_row else "item-text")
            # Don't ellipsize - let text show fully
            lbl.set_xalign(0.0)
            cell_box.pack_start(lbl, False, False, 0)
            # Register ID for features with labels (they produce visual content)
            register_element_id(child, core.rendered_ids)

        cell_controls: list[Gtk.Widget] = []
        for sub in child.children:
            if sub.kind == "text":
                core.build_text(child, sub, cell_box, align_end=False)
            elif sub.kind == "img":
                core.build_img(child, sub, cell_box, pack_end=False)
            elif sub.kind == "qrcode":
                core.build_qrcode(child, sub, cell_box, pack_end=False)
            elif sub.kind == "progressbar":
                core.build_progressbar(child, sub, cell_box, pack_end=False)
            elif sub.kind == "hgroup":
                # Nested hgroup in feature - create vertical layout
                nested_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                for hg_child in sub.children:
                    if hg_child.kind == "text":
                        core.build_text(child, hg_child, nested_box, align_end=False)
                    elif hg_child.kind == "button":
                        btn = core.build_button(child, hg_child, nested_box, pack_end=False)
                        btn.set_can_focus(True)
                        cell_controls.append(btn)
                    elif hg_child.kind == "tab":
                        tab = core.build_tab(child, hg_child, nested_box, pack_end=False)
                        tab.set_can_focus(True)
                        cell_controls.append(tab)
                    elif hg_child.kind == "button_confirm":
                        text = (hg_child.attrs.get("display", "") or "Confirm?").strip()
                        action = hg_child.attrs.get("action", "")
                        btn = Gtk.Button.new_with_label(_(text))
                        btn.get_style_context().add_class("cc-button")
                        btn.get_style_context().add_class("cc-button-confirm")
                        btn.set_can_focus(True)
                        nested_box.pack_start(btn, False, False, 3)
                        def on_confirm_click(_w, _core=core, _text=text, _action=action):
                            _core._about_to_show_dialog = True; _show_confirm_dialog(_core, _text, _action); _core._about_to_show_dialog = False
                        btn.connect("clicked", on_confirm_click)
                        cell_controls.append(btn)
                cell_box.pack_start(nested_box, False, False, 3)
            elif sub.kind == "doc":
                btn = core.build_doc(child, sub, cell_box, pack_end=False)
                if btn:
                    btn.set_can_focus(True)
                    cell_controls.append(btn)
            elif sub.kind == "button":
                btn = core.build_button(child, sub, cell_box, pack_end=False)
                btn.set_can_focus(True)
                cell_controls.append(btn)
            elif sub.kind == "tab":
                tab = core.build_tab(child, sub, cell_box, pack_end=False)
                tab.set_can_focus(True)
                cell_controls.append(tab)
            elif sub.kind == "button_confirm":
                text = (sub.attrs.get("display", "") or "Confirm?").strip()
                action = sub.attrs.get("action", "")
                btn = Gtk.Button.new_with_label(_(text))
                btn.get_style_context().add_class("cc-button")
                btn.get_style_context().add_class("cc-button-confirm")
                btn.set_can_focus(True)
                cell_box.pack_start(btn, False, False, 6)

                def on_confirm_click(_w, _core=core, _text=text, _action=action):
                    _core._about_to_show_dialog = True; _show_confirm_dialog(_core, _text, _action); _core._about_to_show_dialog = False

                btn.connect("clicked", on_confirm_click)
                cell_controls.append(btn)
            elif sub.kind == "toggle":
                tog = core.build_toggle(child, sub, cell_box, pack_end=False)
                tog.set_can_focus(True)
                cell_controls.append(tog)
            elif sub.kind == "switch":
                switch = core.build_switch(child, sub, cell_box, pack_end=False)
                switch.set_can_focus(True)
                cell_controls.append(switch)

        # Add choice button if feature has choice children
        choices = [c for c in child.children if c.kind == "choice"]
        if choices:
            feature_label = label_text or "Option"
            def open_choice(_core=core, _label=feature_label, _choices=choices):
                _core._about_to_show_dialog = True
                _open_choice_popup(_core, _label, _choices)
                _core._about_to_show_dialog = False

            choice_btn = Gtk.Button.new_with_label(_("Select"))
            choice_btn.get_style_context().add_class("cc-button")
            choice_btn.get_style_context().add_class("cc-choice")
            choice_btn.set_can_focus(True)
            choice_btn.set_size_request(70, -1)  # Fixed width like other buttons
            choice_btn.set_hexpand(False)  # Prevent horizontal expansion
            choice_btn.set_halign(Gtk.Align.END)  # Align to the right side
            cell_box.pack_start(choice_btn, False, False, 6)
            choice_btn.connect("clicked", lambda *_: open_choice())
            cell_controls.append(choice_btn)

        # Make cell focusable only if it has interactive controls
        if not is_header_row and cell_controls:
            cell_event.set_can_focus(True)
            cell_event.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.FOCUS_CHANGE_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)

            # Store control index for this cell
            cell_event._control_index = 0

            def on_cell_click(_w, *_args, _controls=cell_controls):
                if _controls:
                    _focus_widget(_controls[0])
                    _activate_widget(_controls[0])
            cell_event.connect("button-press-event", on_cell_click)

        # Always add cell to row (even if no controls) for display
        cells.append((cell_event, cell_controls))
        row_box.pack_start(cell_event, True, True, 12)

    # Check if row has any interactive controls
    has_controls = any(controls for _, controls in cells)

    if not is_header_row and has_controls:
        row._cells = cells
        # Find first cell with controls and set as initial index
        row._cell_index = 0
        for i, (cell_ev, controls) in enumerate(cells):
            if controls:
                row._cell_index = i
                cell_ev._control_index = 0
                break

        def _clear_all_highlights():
            """Remove all highlights from all cells and controls"""
            for ev, controls in row._cells:
                ev.get_style_context().remove_class("focused-cell")
                for ctrl in controls:
                    ctx = ctrl.get_style_context()
                    ctx.remove_class("focused-cell")
                    ctx.remove_class("choice-selected")

        def _apply_current_highlight():
            """Apply highlight to current control"""
            cell_ev, controls = row._cells[row._cell_index]
            if controls:
                ctrl_idx = getattr(cell_ev, "_control_index", 0)
                ctrl_idx = max(0, min(len(controls) - 1, ctrl_idx))
                cell_ev._control_index = ctrl_idx

                # Don't highlight the cell background, only the control
                # cell_ev.get_style_context().add_class("focused-cell")

                # Highlight only the current control
                ctrl = controls[ctrl_idx]
                ctx = ctrl.get_style_context()
                ctx.add_class("focused-cell")
                ctx.add_class("choice-selected")
                _focus_widget(ctrl)

        def on_row_left():
            """Navigate to previous control (within cell or previous cell)"""
            cell_ev, controls = row._cells[row._cell_index]
            ctrl_idx = getattr(cell_ev, "_control_index", 0)

            if ctrl_idx > 0:
                # Move to previous control in same cell
                _clear_all_highlights()
                cell_ev._control_index = ctrl_idx - 1
                _apply_current_highlight()
            else:
                # Move to previous cell with controls
                new_cell_idx = row._cell_index - 1
                while new_cell_idx >= 0:
                    _, controls = row._cells[new_cell_idx]
                    if controls:
                        _clear_all_highlights()
                        row._cell_index = new_cell_idx
                        row._cells[new_cell_idx][0]._control_index = len(controls) - 1
                        _apply_current_highlight()
                        return
                    new_cell_idx -= 1

        def on_row_right():
            """Navigate to next control (within cell or next cell)"""
            cell_ev, controls = row._cells[row._cell_index]
            ctrl_idx = getattr(cell_ev, "_control_index", 0)

            if ctrl_idx < len(controls) - 1:
                # Move to next control in same cell
                _clear_all_highlights()
                cell_ev._control_index = ctrl_idx + 1
                _apply_current_highlight()
            else:
                # Move to next cell with controls
                new_cell_idx = row._cell_index + 1
                while new_cell_idx < len(row._cells):
                    _, controls = row._cells[new_cell_idx]
                    if controls:
                        _clear_all_highlights()
                        row._cell_index = new_cell_idx
                        row._cells[new_cell_idx][0]._control_index = 0
                        _apply_current_highlight()
                        return
                    new_cell_idx += 1

        def on_row_activate():
            """Activate current control"""
            cell_ev, controls = row._cells[row._cell_index]
            if controls:
                ctrl_idx = getattr(cell_ev, "_control_index", 0)
                ctrl_idx = max(0, min(len(controls) - 1, ctrl_idx))
                _activate_widget(controls[ctrl_idx])

        def on_row_focus_in(_w, *_args):
            _clear_all_highlights()
            _apply_current_highlight()

        def on_row_focus_out(_w, *_args):
            _clear_all_highlights()

        row._on_left = on_row_left
        row._on_right = on_row_right
        row._on_activate = on_row_activate
        row.connect("focus-in-event", on_row_focus_in)
        row.connect("focus-out-event", on_row_focus_out)

        core.register_row(row)
    else:
        # Headers or rows without controls are not selectable
        row._on_left = None
        row._on_right = None
        row._on_activate = None

    return row


def _build_feature_row(core: UICore, feat) -> Gtk.EventBox:
    row = Gtk.EventBox()
    row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    row_box.set_halign(Gtk.Align.CENTER)  # Center the row contents
    # Set consistent width for all rows (90% of window width)
    row_box.set_size_request(int(core._window_width * 0.90), -1)
    row.add(row_box)
    row.set_above_child(False)

    display_label = (feat.attrs.get("display", "") or feat.attrs.get("name", "") or "").strip()

    # Check if feature contains tabs
    has_tabs = any(child.kind == "tab" for child in feat.children)

    # Only add label and spacer if feature has a display label
    if display_label:
        name_lbl = Gtk.Label(label=_(display_label))
        name_lbl.set_xalign(0.0)
        name_lbl.get_style_context().add_class("item-text")
        name_lbl.set_width_chars(15)  # Fixed width for label
        row_box.pack_start(name_lbl, False, False, 0)

        # Add spacer only for tabs to push them to the right
        if has_tabs:
            spacer = Gtk.Box()
            spacer.set_hexpand(True)
            row_box.pack_start(spacer, True, True, 0)

    # Build children strictly in XML order, center value between buttons
    row._items = []
    row._item_index = 0

    # For choice features, add the Select button right after the label
    choices = [c for c in feat.children if c.kind == "choice"]
    if choices:
        def open_choice():
            core._about_to_show_dialog = True
            _open_choice_popup(core, display_label, choices)
            core._about_to_show_dialog = False

        choice_btn = Gtk.Button.new_with_label(_("Select"))
        choice_btn.get_style_context().add_class("cc-button")
        choice_btn.get_style_context().add_class("cc-choice")
        choice_btn.set_can_focus(True)
        choice_btn.set_size_request(70, -1)  # Fixed width like other buttons
        choice_btn.set_hexpand(False)  # Prevent horizontal expansion
        choice_btn.set_halign(Gtk.Align.START)  # Align normally, not to the end
        row_box.pack_start(choice_btn, False, False, 8)
        choice_btn.connect("clicked", lambda *_: open_choice())

        row._items.append(choice_btn)
        if not hasattr(row, "_on_activate"):
            row._on_activate = open_choice

    for sub in feat.children:
        kind = sub.kind

        if kind == "button":
            text = (sub.attrs.get("display", "") or "Button").strip()
            action = sub.attrs.get("action", "")
            btn = Gtk.Button.new_with_label(_(text))
            btn.get_style_context().add_class("cc-button")
            btn.set_can_focus(True)
            btn.set_size_request(70, -1)  # Fixed width for buttons
            row_box.pack_start(btn, False, False, 8)
            btn.connect("clicked", core.make_action_cb(action, key=f"btn:{text}:{action}"))
            row._items.append(btn)

        elif kind == "button_confirm":
            text = (sub.attrs.get("display", "") or "Confirm?").strip()
            action = sub.attrs.get("action", "")
            btn = Gtk.Button.new_with_label(_(text))
            btn.get_style_context().add_class("cc-button")
            btn.get_style_context().add_class("cc-button-confirm")
            btn.set_can_focus(True)
            btn.set_size_request(70, -1)
            row_box.pack_start(btn, False, False, 8)

            def on_confirm_click(_w):
                core._about_to_show_dialog = True; _show_confirm_dialog(core, text, action); core._about_to_show_dialog = False

            btn.connect("clicked", on_confirm_click)
            row._items.append(btn)

        elif kind == "text":
            lbl = Gtk.Label(label="")
            lbl.get_style_context().add_class("value")
            # Get alignment from attribute (default: center)
            align_attr = (sub.attrs.get("align", "center") or "center").strip().lower()
            if align_attr == "left":
                lbl.set_xalign(0.0)
                lbl.set_halign(Gtk.Align.START)
            elif align_attr == "right":
                lbl.set_xalign(1.0)
                lbl.set_halign(Gtk.Align.END)
            else:  # center (default)
                lbl.set_xalign(0.5)
                lbl.set_halign(Gtk.Align.CENTER)
            # For features with choices, don't set fixed width - let text size naturally
            if not any(c.kind == "choice" for c in feat.children):
                lbl.set_width_chars(40)   # Fixed width for value to prevent shifting (non-choice features only)
            row_box.pack_start(lbl, False, False, 8)
            disp = (sub.attrs.get("display", "") or "").strip()
            refresh = float(sub.attrs.get("refresh", feat.attrs.get("refresh", DEFAULT_REFRESH_SEC)))

            # Handle dynamic visibility for id() and !id() conditions
            if_condition = (sub.attrs.get("if", "") or "").strip()
            if if_condition:
                # Track this widget for dynamic visibility updates
                core._conditional_widgets.append((lbl, if_condition))
                # Initially hide, will be shown after IDs are registered
                lbl.set_visible(False)

            # Check if display contains ${...} command substitution
            if "${" in disp and not is_cmd(disp):
                # Mixed content or multiple commands - use command substitution
                def upd_expand(_l=lbl, _disp=disp):
                    _l.set_text(expand_command_string(_disp))

                class ExpandRefreshTask:
                    def __init__(self, update_fn, interval_sec):
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
                        def work():
                            GLib.idle_add(self.update_fn)
                        threading.Thread(target=work, daemon=True).start()
                        if self._active:
                            self._schedule_tick(immediate=False)
                        return False

                core.refreshers.append(ExpandRefreshTask(upd_expand, refresh))
                initial_val = expand_command_string(disp)
                lbl.set_text(initial_val)
                # Register ID if content is non-empty
                if initial_val.strip():
                    register_element_id(sub, core.rendered_ids)
            elif is_cmd(disp):
                c = cmd_of(disp)
                def upd(val: str, _l=lbl, _sub=sub, _core=core):
                    _l.set_text(val)
                    # Register/unregister ID based on content
                    if val.strip():
                        register_element_id(_sub, _core.rendered_ids)
                    else:
                        elem_id = _sub.attrs.get("id", "").strip()
                        if elem_id and elem_id in _core.rendered_ids:
                            _core.rendered_ids.remove(elem_id)
                core.refreshers.append(RefreshTask(upd, c, refresh))
            else:
                lbl.set_text(disp)
                # Register ID for static text if non-empty
                if disp.strip():
                    register_element_id(sub, core.rendered_ids)

        elif kind == "img":
            core.build_img(feat, sub, row_box, pack_end=False)

        elif kind == "qrcode":
            core.build_qrcode(feat, sub, row_box, pack_end=False)

        elif kind == "progressbar":
            core.build_progressbar(feat, sub, row_box, pack_end=False)

        elif kind == "doc":
            btn = core.build_doc(feat, sub, row_box, pack_end=False)
            if btn:
                row._items.append(btn)

        elif kind == "hgroup":
            # Nested hgroup in feature - create horizontal layout
            nested_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
            row_box.pack_start(nested_box, False, False, 8)
            for hg_child in sub.children:
                if hg_child.kind == "text":
                    core.build_text(feat, hg_child, nested_box, align_end=False)
                elif hg_child.kind == "button":
                    btn = core.build_button(feat, hg_child, nested_box, pack_end=False)
                    row._items.append(btn)
                elif hg_child.kind == "tab":
                    tab = core.build_tab(feat, hg_child, nested_box, pack_end=False)
                    row._items.append(tab)
                elif hg_child.kind == "button_confirm":
                    text = (hg_child.attrs.get("display", "") or "Confirm?").strip()
                    action = hg_child.attrs.get("action", "")
                    btn = Gtk.Button.new_with_label(_(text))
                    btn.get_style_context().add_class("cc-button")
                    btn.get_style_context().add_class("cc-button-confirm")
                    btn.set_can_focus(True)
                    btn.set_size_request(70, -1)
                    nested_box.pack_start(btn, False, False, 3)
                    def on_confirm_click(_w, _core=core, _text=text, _action=action):
                        _core._about_to_show_dialog = True; _show_confirm_dialog(_core, _text, _action); _core._about_to_show_dialog = False
                    btn.connect("clicked", on_confirm_click)
                    row._items.append(btn)

        elif kind == "toggle":
            tog = core.build_toggle(feat, sub, row_box, pack_end=False)
            row._items.append(tog)

        elif kind == "switch":
            switch = core.build_switch(feat, sub, row_box, pack_end=False)
            row._items.append(switch)

        elif kind == "tab":
            tab = core.build_tab(feat, sub, row_box, pack_end=False)
            if tab:
                row._items.append(tab)

    # Only register row if it has interactive items
    if row._items:
        # Check if this row contains tabs - if so, set up tab switching
        tabs = [item for item in row._items if hasattr(item, '_tab_target')]
        if tabs:
            # This is a tab row - set up content switching
            row._tabs = tabs
            row._tab_contents = {}  # Will be populated later when hgroups are processed
            row._core = core  # Store core reference for focus management

            def make_switch_handler(target_tab):
                """Create a switch handler for a specific tab"""
                def switch_to_tab(btn):
                    if not btn.get_active():
                        # User clicked an already-active tab - keep it active
                        # Block signal to prevent recursion
                        btn.handler_block_by_func(switch_to_tab)
                        btn.set_active(True)
                        btn.handler_unblock_by_func(switch_to_tab)
                        return

                    # Uncheck all other tabs (block signals to prevent recursion)
                    for t in row._tabs:
                        if t != target_tab:
                            t.handler_block_by_func(t._switch_handler)
                            t.set_active(False)
                            t.handler_unblock_by_func(t._switch_handler)

                    # Show/hide content based on target
                    target = getattr(target_tab, '_tab_target', '')
                    if target and hasattr(row, '_tab_contents') and hasattr(row, '_content_box'):
                        content_box = row._content_box
                        core = row._core  # Get core reference

                        # Collect all tab content rows to remove from focus list
                        all_tab_rows = []
                        for content_id, content_widget in row._tab_contents.items():
                            if hasattr(content_widget, '_tab_rows'):
                                all_tab_rows.extend(content_widget._tab_rows)

                        # Remove all tab content rows from focus_rows
                        for r in all_tab_rows:
                            if r in core.focus_rows:
                                core.unhighlight_row(r)
                                core.focus_rows.remove(r)
                        # Remove all tab content frames
                        for content_id, content_widget in row._tab_contents.items():
                            if hasattr(content_widget, '_frame'):
                                frame = content_widget._frame
                                if frame.get_parent():
                                    frame.get_parent().remove(frame)
                        # Add back and show the selected content
                        if target in row._tab_contents:
                            content_widget = row._tab_contents[target]
                            if hasattr(content_widget, '_frame'):
                                # Insert frame into content_box
                                content_box.pack_start(content_widget._frame, False, False, 0)
                                content_widget._frame.show_all()
                            # Add this tab's rows back to focus_rows
                            if hasattr(content_widget, '_tab_rows'):
                                for r in content_widget._tab_rows:
                                    if r not in core.focus_rows:
                                        core.focus_rows.append(r)
                        # Reset focus index if needed
                        if core.focus_index >= len(core.focus_rows):
                            core.focus_index = max(0, len(core.focus_rows) - 1)
                return switch_to_tab
            # Connect tab buttons to switch content
            for tab in tabs:
                handler = make_switch_handler(tab)
                tab._switch_handler = handler
                tab.connect("toggled", handler)
            # Don't activate first tab yet - will be done after content is linked
        # Left/Right selection within row
        def _set_item_focus(idx: int):
            if not row._items:
                return
            row._item_index = max(0, min(len(row._items) - 1, idx))
            _focus_widget(row._items[row._item_index])

        def on_left():
            _set_item_focus(row._item_index - 1)

        def on_right():
            _set_item_focus(row._item_index + 1)

        def on_activate():
            if not row._items:
                return
            item = row._items[row._item_index]
            # For tabs, toggle them to trigger content switching
            if hasattr(item, '_tab_target'):
                item.set_active(True)
            else:
                _activate_widget(item)

        row._on_left = on_left
        row._on_right = on_right
        if not hasattr(row, "_on_activate"):
            row._on_activate = on_activate

        core.register_row(row)
    else:
        # Row without interactive items is not selectable
        row._on_left = None
        row._on_right = None
        row._on_activate = None

    # Register feature ID if it has one and was successfully built
    register_element_id(feat, core.rendered_ids, core)

    return row

def _hide_dialog_action_area(dialog):
    """Completely hide and remove the dialog action area"""
    action_area = dialog.get_action_area()
    if action_area:
        # Remove all children first
        for child in action_area.get_children():
            action_area.remove(child)
        # Hide it completely
        action_area.set_visible(False)
        action_area.set_size_request(-1, 0)
        action_area.set_no_show_all(True)
        # Try to remove it from parent entirely
        parent = action_area.get_parent()
        if parent:
            try:
                parent.remove(action_area)
            except:
                pass
    # Also make content area fill the entire dialog
    content = dialog.get_content_area()
    if content:
        content.set_vexpand(True)
        content.set_hexpand(True)

def _show_confirm_dialog(core: UICore, message: str, action: str):
    """Show a confirmation dialog before executing an action"""
    core._dialog_open = True  # Prevent main window from closing
    core._suspend_inactivity_timer = True  # Suspend timer for confirm dialog

    # Use Gtk.Window instead of Gtk.Dialog to avoid action area issues
    dialog = Gtk.Window()
    
    # Track this dialog so it can be destroyed on timeout
    core._current_dialog = dialog
    dialog.set_transient_for(core.window)
    dialog.set_modal(True)
    if core._scale_class == "small":
        dialog.set_default_size(240, 120)
    else:
        dialog.set_default_size(400, 200)
    dialog.set_decorated(False)
    dialog.set_resizable(False)
    dialog.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    dialog.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)

    # Close dialog if main window is destroyed
    def on_parent_destroy(*_):
        try:
            dialog.destroy()
        except:
            pass
    core.window.connect("destroy", on_parent_destroy)

    # Close everything if dialog loses focus to external app
    def on_dialog_focus_out(*_):
        def check_and_close():
            if not dialog.is_active():
                core.quit()
            return False
        GLib.timeout_add(100, check_and_close)
        return False
    dialog.connect("focus-out-event", on_dialog_focus_out)

    # Style the dialog window itself
    dialog.get_style_context().add_class("popup-root")
    dialog.get_style_context().add_class("confirm-dialog")

    # Add frame for inner content (no action area with Gtk.Window!)
    frame = Gtk.Frame()
    frame.set_shadow_type(Gtk.ShadowType.NONE)
    dialog.add(frame)

    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    if core._scale_class == "small":
        inner.set_border_width(8)
    else:
        inner.set_border_width(20)
    frame.add(inner)

    label = Gtk.Label(label=_(message))
    label.set_xalign(0.5)
    label.set_line_wrap(True)
    label.get_style_context().add_class("item-text")
    inner.pack_start(label, True, True, 15)

    # Button box
    button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
    button_box.set_halign(Gtk.Align.CENTER)
    inner.pack_start(button_box, False, False, 10)

    buttons = []
    current_btn = [0]  # 0 = Cancel (default), 1 = Confirm

    cancel_btn = Gtk.Button.new_with_label(_("Cancel"))
    cancel_btn.get_style_context().add_class("cc-button")
    if core._scale_class == "small":
        cancel_btn.set_size_request(80, -1)
    else:
        cancel_btn.set_size_request(100, -1)
    cancel_btn.set_can_focus(True)
    button_box.pack_start(cancel_btn, False, False, 0)
    cancel_btn.connect("clicked", lambda _: dialog.destroy())
    buttons.append(cancel_btn)

    confirm_btn = Gtk.Button.new_with_label(_("Confirm"))
    confirm_btn.get_style_context().add_class("cc-button")
    if core._scale_class == "small":
        cancel_btn.set_size_request(80, -1)
    else:
        cancel_btn.set_size_request(100, -1)
    confirm_btn.set_can_focus(True)
    button_box.pack_start(confirm_btn, False, False, 0)
    buttons.append(confirm_btn)

    def update_button_focus():
        for i, btn in enumerate(buttons):
            ctx = btn.get_style_context()
            if i == current_btn[0]:
                ctx.add_class("focused-cell")
                ctx.add_class("choice-selected")
                # Don't use grab_focus() - conflicts with touchscreen
            else:
                ctx.remove_class("focused-cell")
                ctx.remove_class("choice-selected")

    def on_confirm(_w):
        if action:
            threading.Thread(target=lambda: run_shell_capture(action), daemon=True).start()
        dialog.destroy()

    # Override gamepad handler for dialog
    original_handler = core._handle_gamepad_action_main

    def dialog_gamepad_handler(action_key: str):
        core.reset_inactivity_timer()  # Reset timer on dialog interaction
        if action_key == "activate":
            if current_btn[0] == 1:
                on_confirm(None)
            else:
                dialog.destroy()
        elif action_key == "back":
            dialog.destroy()
        elif action_key in ("axis_left", "axis_right", "pan_left", "pan_right"):
            current_btn[0] = 1 - current_btn[0]  # Toggle between 0 and 1
            update_button_focus()
        return False

    core._handle_gamepad_action = dialog_gamepad_handler

    def on_key_press(_w, ev: Gdk.EventKey):
        core.reset_inactivity_timer()  # Reset timer on keyboard interaction
        key = Gdk.keyval_name(ev.keyval) or ""
        if key.lower() == "escape":
            dialog.destroy()
            return True
        elif key in ("Left", "KP_Left"):
            current_btn[0] = 0
            update_button_focus()
            return True
        elif key in ("Right", "KP_Right"):
            current_btn[0] = 1
            update_button_focus()
            return True
        elif key in ("Return", "KP_Enter", "space"):
            if current_btn[0] == 1:
                on_confirm(None)
            else:
                dialog.destroy()
            return True
        return False

    def on_button_click(_w):
        core.reset_inactivity_timer()  # Reset timer on button click

    cancel_btn.connect("clicked", lambda _: (on_button_click(_), dialog.destroy()))
    confirm_btn.connect("clicked", lambda _: (on_button_click(_), on_confirm(_)))
    dialog.connect("key-press-event", on_key_press)

    dialog.show_all()
    current_btn[0] = 0  # Default to Cancel
    GLib.idle_add(update_button_focus)

    # Connect destroy handler to clean up
    def on_dialog_destroy(*_):
        # Clear the dialog reference
        if hasattr(core, '_current_dialog') and core._current_dialog == dialog:
            core._current_dialog = None
        # Restore original handler
        core._handle_gamepad_action = original_handler
        core._dialog_open = False  # Allow main window to close again
        core._suspend_inactivity_timer = False  # Resume timer
        # Resume inactivity timer
        core.reset_inactivity_timer()
    dialog.connect("destroy", on_dialog_destroy)
    dialog.show_all()

def _open_choice_popup(core: UICore, feature_label: str, choices):
    """Open a popup dialog to select from available choices"""
    core._dialog_open = True  # Prevent main window from closing on focus loss
    core._dialog_allows_timeout = True  # Allow inactivity timer to close window
    # Use Gtk.Window instead of Gtk.Dialog to avoid action area issues
    dialog = Gtk.Window()
    
    # Track this dialog so it can be destroyed on timeout
    core._current_dialog = dialog
    dialog.set_transient_for(core.window)
    dialog.set_modal(True)
    if core._scale_class == "small":
        dialog.set_default_size(400, 300)
    else:
        dialog.set_default_size(600, 500)
    dialog.set_decorated(False)
    dialog.set_resizable(False)
    dialog.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    dialog.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)

    # Close dialog if main window is destroyed
    def on_parent_destroy(*_):
        try:
            dialog.destroy()
        except:
            pass
    core.window.connect("destroy", on_parent_destroy)

    # Close everything if dialog loses focus to external app
    def on_dialog_focus_out(*_):
        def check_and_close():
            if not dialog.is_active():
                core.quit()
            return False
        GLib.timeout_add(100, check_and_close)
        return False
    dialog.connect("focus-out-event", on_dialog_focus_out)

    # Style the dialog window itself
    dialog.get_style_context().add_class("popup-root")
    dialog.get_style_context().add_class("confirm-dialog")

    # Add frame for inner content (no action area with Gtk.Window!)
    frame = Gtk.Frame()
    frame.set_shadow_type(Gtk.ShadowType.NONE)
    dialog.add(frame)

    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    if core._scale_class == "small":
        inner.set_border_width(10)
    else:
        inner.set_border_width(20)
    frame.add(inner)

    label = Gtk.Label(label=_(feature_label) + ":")
    label.set_xalign(0.5)  # Center the label
    label.get_style_context().add_class("group-title")
    inner.pack_start(label, False, False, 15)

    # Create a scrolled window for the choices
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_min_content_height(250)
    inner.pack_start(scrolled, True, True, 0)

    # Box to hold choice buttons
    choice_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    choice_box.set_border_width(6)
    scrolled.add(choice_box)

    choice_buttons = []
    current_choice = [0]  # Always start with first choice

    def on_choice_selected(action: str):
        import threading
        dialog.destroy()
        if action:
            threading.Thread(target=lambda: run_shell_capture(action), daemon=True).start()

    def update_choice_focus():
        for i, btn in enumerate(choice_buttons):
            ctx = btn.get_style_context()
            # First, remove all focus-related classes from all buttons
            ctx.remove_class("focused-cell")
            ctx.remove_class("choice-selected")
            
        # Then add the classes only to the selected button
        if 0 <= current_choice[0] < len(choice_buttons):
            selected_btn = choice_buttons[current_choice[0]]
            selected_ctx = selected_btn.get_style_context()
            selected_ctx.add_class("focused-cell")
            selected_ctx.add_class("choice-selected")

    # Override core's gamepad handler temporarily for dialog navigation
    original_handler = core._handle_gamepad_action

    def dialog_gamepad_handler(action: str):
        core.reset_inactivity_timer()  # Reset timer on dialog interaction
        if action == "activate":
            if choice_buttons:
                choice_buttons[current_choice[0]].emit("clicked")
        elif action == "back":
            dialog.destroy()
        elif action in ("axis_up", "pan_up"):
            current_choice[0] = max(0, current_choice[0] - 1)
            update_choice_focus()
        elif action in ("axis_down", "pan_down"):
            current_choice[0] = min(len(choice_buttons) - 1, current_choice[0] + 1)
            update_choice_focus()
        return False

    core._handle_gamepad_action = dialog_gamepad_handler

    # Create a button for each choice
    for choice in choices:
        display = (choice.attrs.get("display", "") or "Option").strip()
        action = choice.attrs.get("action", "")

        btn = Gtk.Button.new_with_label(_(display))
        btn.set_can_focus(True)
        btn.get_style_context().add_class("choice-option")
        choice_box.pack_start(btn, False, False, 0)

        def on_choice_click(_w, a=action):
            core.reset_inactivity_timer()  # Reset timer on button click
            on_choice_selected(a)

        btn.connect("clicked", on_choice_click)
        choice_buttons.append(btn)

    # Add keyboard navigation
    def on_key_press(_w, ev: Gdk.EventKey):
        core.reset_inactivity_timer()  # Reset timer on keyboard interaction
        key = Gdk.keyval_name(ev.keyval) or ""
        if key.lower() == "escape":
            dialog.destroy()
            return True
        elif key in ("Up", "KP_Up"):
            current_choice[0] = max(0, current_choice[0] - 1)
            update_choice_focus()
            return True
        elif key in ("Down", "KP_Down"):
            current_choice[0] = min(len(choice_buttons) - 1, current_choice[0] + 1)
            update_choice_focus()
            return True
        elif key in ("Return", "KP_Enter", "space"):
            if choice_buttons:
                choice_buttons[current_choice[0]].emit("clicked")
            return True
        return False

    dialog.connect("key-press-event", on_key_press)

    dialog.show_all()

    # Apply initial focus after dialog is shown - always start with first choice
    if choice_buttons:
        current_choice[0] = 0  # Explicitly reset to first choice
        GLib.idle_add(update_choice_focus)

    # On Wayland, remove dialog decorations
    if core._is_wayland:
        def remove_dialog_decorations():
            import subprocess
            try:
                # Wait a moment for dialog to appear in Sway tree
                import time
                time.sleep(0.1)
                # Remove border from any dialog window
                subprocess.run(['swaymsg', '[title="^$"]', 'border', 'none'],
                             capture_output=True, timeout=1)
            except Exception:
                pass
        import threading
        threading.Thread(target=remove_dialog_decorations, daemon=True).start()

    # Connect destroy handler to clean up
    def on_dialog_destroy(*_):
        # Clear the dialog reference
        if hasattr(core, '_current_dialog') and core._current_dialog == dialog:
            core._current_dialog = None
        # Restore original handler
        core._handle_gamepad_action = original_handler
        core._dialog_open = False  # Allow main window to close again
        core._dialog_allows_timeout = False  # Reset flag
        # Resume inactivity timer
        core.reset_inactivity_timer()
    dialog.connect("destroy", on_dialog_destroy)
    dialog.show_all()

# ---- Application wrapper ----
class ControlCenterApp:
    def __init__(self, xml_root, css_path: str, auto_close_seconds: int = 0, hidden_at_startup = False, 
                 fullscreen: bool = False, window_size: tuple[int, int] | None = None):
        self.core = UICore(css_path, fullscreen, window_size)
        self.auto_close_seconds = auto_close_seconds
        self.core._inactivity_timeout_seconds = auto_close_seconds
        self.hidden_at_startup = hidden_at_startup
        self.window = ui_build_containers(self.core, xml_root)
        if hidden_at_startup:
            self.window.hide()
        else:
            self.window.present()

    def run(self):
        self.core.start_refresh()
        self.core.set_tab_focus()

        if not self.hidden_at_startup:
            self.core.start_gamepad()

        # Set up inactivity timer if specified (resets on user interaction)
        if self.auto_close_seconds > 0:
            self.core.reset_inactivity_timer()

        Gtk.main()

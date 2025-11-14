# ui_core.py - main UI components for the Control Center
# This file is part of the batocera distribution (https://batocera.org).
# Copyright (c) 2025 lbrpdx for the Batocera team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License
# as published by the Free Software Foundation, version 3.
#
# YOU MUST KEEP THIS HEADER AS IT IS
import os
import threading
import gi
gi.require_version('Gtk', '3.0'); gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

# Optional evdev for gamepad
try:
    from evdev import InputDevice, categorize, ecodes, list_devices
    EVDEV_AVAILABLE = True
except Exception:
    EVDEV_AVAILABLE = False

# Optional gtk-layer-shell for Wayland compositors (Sway/labwc)
LAYER_SHELL_AVAILABLE = False
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    LAYER_SHELL_AVAILABLE = True
except Exception:
    LAYER_SHELL_AVAILABLE = False

from refresh import RefreshTask, DEFAULT_REFRESH_SEC, Debouncer
from shell import run_shell_capture, normalize_bool_str, get_primary_geometry, expand_command_string

ACTION_DEBOUNCE_MS = 100  # Faster response
WINDOW_TITLE = "Batocera Control Center"

def is_cmd(s: str) -> bool:
    s = (s or "").strip()
    return s.startswith("${") and s.endswith("}")


def cmd_of(s: str) -> str:
    s = (s or "").strip()
    return s[2:-1].strip() if is_cmd(s) else ""


def _focus_widget(widget: Gtk.Widget):
    try:
        widget.grab_focus()
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


class UICore:
    def __init__(self, css_path: str):
        self.css_path = css_path
        self.window: Gtk.Window | None = None
        self.focus_rows: list[Gtk.EventBox] = []
        self.focus_index: int = 0
        self.refreshers: list[RefreshTask] = []
        self.debouncer = Debouncer(ACTION_DEBOUNCE_MS)
        self._gamepad_devices = []
        self._gamepad_running = False
        self._inactivity_timer_id = None
        self._inactivity_timeout_seconds = 0

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
        # Set width to 64% and max height to 70%
        width = int(sw * 0.64)
        max_height = int(sh * 0.70)

        # Store dimensions for positioning
        self._window_width = width
        self._max_height = max_height
        self._screen_x = x0
        self._screen_y = y0
        self._screen_width = sw
        self._screen_height = sh

        # Set default size - use max_height as starting point
        win.set_default_size(width, max_height)

        # Set geometry hints for both X11 and Wayland
        geom = Gdk.Geometry()
        geom.min_width = width
        geom.max_width = width
        geom.max_height = max_height
        win.set_geometry_hints(None, geom, Gdk.WindowHints.MIN_SIZE | Gdk.WindowHints.MAX_SIZE)

        # Store for later use
        self._is_wayland = is_wayland

        def on_realize(_w):
            # On X11, position immediately after realize
            if not is_wayland:
                center_x = self._screen_x + (self._screen_width - self._window_width) // 2
                top_y = self._screen_y + 20
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

            # Focus first row
            if self.focus_rows:
                GLib.timeout_add(10, lambda: (self.focus_rows[0].grab_focus(), False)[1])

        # Track if we have an open dialog to prevent closing on dialog focus
        self._dialog_open = False

        def on_focus_out(_w, ev):
            # Don't close if we have a dialog open
            if self._dialog_open:
                return False

            # Close window when it loses focus to another application
            def check_and_close():
                # Check if we still have focus after a brief delay
                if not win.is_active() and not self._dialog_open:
                    self.quit()
                return False
            GLib.timeout_add(100, check_and_close)
            return False

        win.connect("realize", on_realize)
        win.connect("map", on_map)
        win.connect("key-press-event", self._on_key_press)
        win.connect("focus-out-event", on_focus_out)
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

    def move_focus(self, delta: int):
        if not self.focus_rows:
            return
        self.reset_inactivity_timer()  # Reset timer on navigation
        old = self.focus_index
        self.focus_index = (self.focus_index + delta) % len(self.focus_rows)

        prev_row = self.focus_rows[old]
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

    def row_left(self):
        row = self.focus_rows[self.focus_index] if self.focus_rows else None
        if not row:
            return

        # If row has items, navigate and activate
        if hasattr(row, "_items") and row._items:
            item_index = getattr(row, "_item_index", 0)
            if item_index > 0:
                row._item_index = item_index - 1
                item = row._items[row._item_index]
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
                row._item_index = item_index + 1
                item = row._items[row._item_index]
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

    def start_refresh(self):
        for r in self.refreshers:
            r.start()

    def quit(self, *_a):
        # Stop gamepad thread and release devices
        self._gamepad_running = False

        # Give the thread a moment to exit cleanly
        import time
        time.sleep(0.1)

        self._release_gamepads()

        try:
            if self.window:
                self.window.destroy()
        except Exception:
            pass
        try:
            Gtk.main_quit()
        except Exception:
            pass

    # ---- SDL gamepad ----
    def start_gamepad(self):
        """Start gamepad input handling using evdev"""
        if EVDEV_AVAILABLE:
            self.start_evdev_gamepad()
        else:
            print("Evdev not available - gamepad support disabled")

    def start_evdev_gamepad(self):
        """Use evdev to read gamepad input with exclusive access (blocks EmulationStation)"""
        if not EVDEV_AVAILABLE:
            return

        self._gamepad_running = True

        def evdev_loop():
            import select
            import time

            last_action = {}
            debounce_time = 0.15  # Faster gamepad response

            try:
                # Find all gamepad/joystick devices
                for path in list_devices():
                    try:
                        device = InputDevice(path)
                        caps = device.capabilities()
                        # Check if it's a gamepad (has ABS_X, ABS_Y and BTN_GAMEPAD or BTN_SOUTH)
                        if ecodes.EV_ABS in caps and ecodes.EV_KEY in caps:
                            abs_events = caps[ecodes.EV_ABS]
                            key_events = caps[ecodes.EV_KEY]
                            has_axes = any(ax[0] in [ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y] for ax in abs_events)
                            has_buttons = any(btn in key_events for btn in [ecodes.BTN_SOUTH, ecodes.BTN_A, ecodes.BTN_GAMEPAD])
                            if has_axes and has_buttons:
                                print(f"Found gamepad: {device.name} at {path}")
                                # Grab exclusive access to prevent EmulationStation from receiving events
                                try:
                                    device.grab()
                                    print(f"Grabbed exclusive access to {device.name}")
                                except Exception as e:
                                    print(f"Could not grab {device.name}: {e}")
                                self._gamepad_devices.append(device)
                    except Exception as e:
                        print(f"Error checking device {path}: {e}")

                if not self._gamepad_devices:
                    print("No gamepad devices found via evdev")
                    return

                # Track axis states to detect movement
                axis_states = {}
                for dev in self._gamepad_devices:
                    axis_states[dev.fd] = {}

                while self._gamepad_running:
                    # Use select to wait for events from any device
                    r, w, x = select.select(self._gamepad_devices, [], [], 0.1)
                    current_time = time.time()

                    for device in r:
                        try:
                            for event in device.read():
                                if event.type == ecodes.EV_KEY:
                                    # Button press
                                    if event.value == 1:  # Button down
                                        action_key = f"btn_{event.code}"
                                        last_time = last_action.get(action_key, 0)
                                        if current_time - last_time > debounce_time:
                                            last_action[action_key] = current_time

                                            # Map common gamepad buttons
                                            if event.code in [ecodes.BTN_SOUTH, ecodes.BTN_A]:  # A button
                                                GLib.idle_add(self._handle_gamepad_action, "activate")
                                            elif event.code in [ecodes.BTN_EAST, ecodes.BTN_B, ecodes.BTN_START, ecodes.BTN_SELECT]:
                                                GLib.idle_add(self._handle_gamepad_action, "back")
                                            # D-pad buttons (some controllers like PS3 use these)
                                            elif event.code == ecodes.BTN_DPAD_UP:
                                                GLib.idle_add(self._handle_gamepad_action, "axis_up")
                                            elif event.code == ecodes.BTN_DPAD_DOWN:
                                                GLib.idle_add(self._handle_gamepad_action, "axis_down")
                                            elif event.code == ecodes.BTN_DPAD_LEFT:
                                                GLib.idle_add(self._handle_gamepad_action, "axis_left")
                                            elif event.code == ecodes.BTN_DPAD_RIGHT:
                                                GLib.idle_add(self._handle_gamepad_action, "axis_right")

                                elif event.type == ecodes.EV_ABS:
                                    # Analog stick or D-pad
                                    fd = device.fd
                                    code = event.code
                                    value = event.value

                                    # Get axis info for normalization
                                    abs_info = device.absinfo(code)
                                    center = (abs_info.max + abs_info.min) // 2
                                    threshold = (abs_info.max - abs_info.min) // 4

                                    # Determine direction - support multiple axis types
                                    action_key = None
                                    # Vertical axes (left stick Y, right stick Y, D-pad Y)
                                    if code in [ecodes.ABS_Y, ecodes.ABS_RY, ecodes.ABS_HAT0Y]:
                                        if value < center - threshold:
                                            action_key = "axis_up"
                                        elif value > center + threshold:
                                            action_key = "axis_down"
                                    # Horizontal axes (left stick X, right stick X, D-pad X)
                                    elif code in [ecodes.ABS_X, ecodes.ABS_RX, ecodes.ABS_HAT0X]:
                                        if value < center - threshold:
                                            action_key = "axis_left"
                                        elif value > center + threshold:
                                            action_key = "axis_right"

                                    if action_key:
                                        # Check if this is a new movement (debounce)
                                        last_time = last_action.get(action_key, 0)
                                        if current_time - last_time > debounce_time:
                                            last_action[action_key] = current_time
                                            GLib.idle_add(self._handle_gamepad_action, action_key)
                        except Exception as e:
                            print(f"Error reading event: {e}")

            except Exception as e:
                print(f"Evdev gamepad error: {e}")
            finally:
                self._release_gamepads()

        # Store the thread so we can track it
        gamepad_thread = threading.Thread(target=evdev_loop, daemon=True)
        gamepad_thread.start()

    def _release_gamepads(self):
        """Release exclusive access to gamepad devices"""
        for dev in self._gamepad_devices:
            try:
                dev.ungrab()
                print(f"Released {dev.name}")
            except Exception:
                pass
            try:
                dev.close()
            except Exception:
                pass
        self._gamepad_devices = []

    def _handle_gamepad_action(self, action: str):
        """Handle gamepad actions - works for both main window and dialogs"""
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
        return False

    def start_sdl(self):
        if not SDL_AVAILABLE:
            print("SDL not available")
            return

        def sdl_loop():
            controllers = []
            axis_state = {}  # Track axis states to debounce
            axis_debounce_time = 0.3  # seconds
            last_axis_action = {}

            try:
                if sdl2.SDL_Init(sdl2.SDL_INIT_EVENTS | sdl2.SDL_INIT_GAMECONTROLLER | sdl2.SDL_INIT_JOYSTICK) != 0:
                    print(f"SDL_Init failed: {sdl2.SDL_GetError()}")
                    return

                # Open all available game controllers
                num_joysticks = sdl2.SDL_NumJoysticks()
                print(f"Found {num_joysticks} joystick(s)")

                for i in range(num_joysticks):
                    if sdl2.SDL_IsGameController(i):
                        controller = sdl2.SDL_GameControllerOpen(i)
                        if controller:
                            name = sdl2.SDL_GameControllerName(controller)
                            print(f"Opened controller {i}: {name}")
                            controllers.append(controller)
                        else:
                            print(f"Failed to open controller {i}: {sdl2.SDL_GetError()}")
                    else:
                        print(f"Joystick {i} is not a game controller")

                if not controllers:
                    print("No game controllers opened")

                running = True
                import time

                while running:
                    ev = sdl2.SDL_Event()
                    while sdl2.SDL_PollEvent(ev):
                        t = ev.type
                        if t == sdl2.SDL_QUIT:
                            running = False
                            break
                        elif t == sdl2.SDL_CONTROLLERDEVICEADDED:
                            # New controller connected
                            idx = ev.cdevice.which
                            if sdl2.SDL_IsGameController(idx):
                                controller = sdl2.SDL_GameControllerOpen(idx)
                                if controller:
                                    controllers.append(controller)
                        elif t == sdl2.SDL_CONTROLLERDEVICEREMOVED:
                            # Controller disconnected
                            instance_id = ev.cdevice.which
                            controllers = [c for c in controllers if c and sdl2.SDL_JoystickInstanceID(sdl2.SDL_GameControllerGetJoystick(c)) != instance_id]
                        elif t == sdl2.SDL_CONTROLLERBUTTONDOWN:
                            b = ev.cbutton.button
                            if b == sdl2.SDL_CONTROLLER_BUTTON_A:
                                GLib.idle_add(self.activate_current)
                            elif b in (sdl2.SDL_CONTROLLER_BUTTON_B, sdl2.SDL_CONTROLLER_BUTTON_START):
                                GLib.idle_add(self.quit)
                            elif b == sdl2.SDL_CONTROLLER_BUTTON_DPAD_UP:
                                GLib.idle_add(self.move_focus, -1)
                            elif b == sdl2.SDL_CONTROLLER_BUTTON_DPAD_DOWN:
                                GLib.idle_add(self.move_focus, +1)
                            elif b == sdl2.SDL_CONTROLLER_BUTTON_DPAD_LEFT:
                                GLib.idle_add(self.row_left)
                            elif b == sdl2.SDL_CONTROLLER_BUTTON_DPAD_RIGHT:
                                GLib.idle_add(self.row_right)
                        elif t == sdl2.SDL_CONTROLLERAXISMOTION:
                            axis, val, thr = ev.caxis.axis, ev.caxis.value, 12000
                            current_time = time.time()

                            # Debounce axis motion
                            action_key = None
                            if axis == sdl2.SDL_CONTROLLER_AXIS_LEFTY:
                                if val < -thr:
                                    action_key = "axis_up"
                                elif val > thr:
                                    action_key = "axis_down"
                            elif axis == sdl2.SDL_CONTROLLER_AXIS_LEFTX:
                                if val < -thr:
                                    action_key = "axis_left"
                                elif val > thr:
                                    action_key = "axis_right"

                            if action_key:
                                last_time = last_axis_action.get(action_key, 0)
                                if current_time - last_time > axis_debounce_time:
                                    last_axis_action[action_key] = current_time
                                    if action_key == "axis_up":
                                        GLib.idle_add(self.move_focus, -1)
                                    elif action_key == "axis_down":
                                        GLib.idle_add(self.move_focus, +1)
                                    elif action_key == "axis_left":
                                        GLib.idle_add(self.row_left)
                                    elif action_key == "axis_right":
                                        GLib.idle_add(self.row_right)

                    sdl2.SDL_Delay(8)
            except Exception as e:
                print(f"SDL error: {e}")
            finally:
                # Close all controllers
                for controller in controllers:
                    try:
                        if controller:
                            sdl2.SDL_GameControllerClose(controller)
                    except Exception:
                        pass
                try:
                    sdl2.SDL_Quit()
                except Exception:
                    pass

        threading.Thread(target=sdl_loop, daemon=True).start()

    # ---- Rendering helpers for new schema ----
    def reset_inactivity_timer(self):
        """Reset the inactivity timer when user interacts with the window"""
        if self._inactivity_timeout_seconds <= 0:
            return

        # Cancel existing timer
        if self._inactivity_timer_id is not None:
            GLib.source_remove(self._inactivity_timer_id)

        # Start new timer - only quit if no dialog is open
        def timeout_callback():
            if not self._dialog_open:
                self.quit()
            return False

        self._inactivity_timer_id = GLib.timeout_add_seconds(
            self._inactivity_timeout_seconds,
            timeout_callback
        )

    def make_action_cb(self, action: str, key: str):
        def cb(_w=None):
            act = (action or "").strip()
            if not act:
                return
            if self.debouncer.allow(key):
                self.reset_inactivity_timer()  # Reset timer on button click
                threading.Thread(target=lambda: run_shell_capture(act), daemon=True).start()
        return cb

    def build_text(self, parent_feat, sub, row_box, align_end=False):
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

        (row_box.pack_end if align_end else row_box.pack_start)(lbl, False, False, 6)
        disp = (sub.attrs.get("display", "") or "").strip()
        refresh = int(sub.attrs.get("refresh", parent_feat.attrs.get("refresh", DEFAULT_REFRESH_SEC)))

        # Check if display contains ${...} command substitution
        # Use expansion if: has ${, OR doesn't match pure ${...} format
        if "${" in disp and not is_cmd(disp):
            # Mixed content or multiple commands - use command substitution
            def upd_expand(_l=lbl, _disp=disp):
                _l.set_text(expand_command_string(_disp))
            # Create a dummy refresh task that calls our update function
            class ExpandRefreshTask:
                def __init__(self, update_fn, interval_sec):
                    self.update_fn = update_fn
                    self.interval_ms = max(250, int(interval_sec * 1000))
                    self._timer_id = None

                def start(self):
                    self._schedule_tick(immediate=True)

                def _schedule_tick(self, immediate=False):
                    delay = 10 if immediate else self.interval_ms
                    self._timer_id = GLib.timeout_add(delay, self._tick)

                def _tick(self):
                    def work():
                        GLib.idle_add(self.update_fn)
                    threading.Thread(target=work, daemon=True).start()
                    self._schedule_tick(immediate=False)
                    return False

            self.refreshers.append(ExpandRefreshTask(upd_expand, refresh))
            # Set initial value
            lbl.set_text(expand_command_string(disp))
        elif is_cmd(disp):
            c = cmd_of(disp)
            def upd(val: str, _l=lbl): _l.set_text(val)
            self.refreshers.append(RefreshTask(upd, c, refresh))
        else:
            lbl.set_text(disp)

    def build_button(self, parent_feat, sub, row_box, pack_end=False):
        text = (sub.attrs.get("display", "") or "Button").strip()
        action = sub.attrs.get("action", "")
        btn = Gtk.Button.new_with_label(text)
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
        return btn

    def build_toggle(self, parent_feat, sub, row_box, pack_end=False):
        parent_label = (parent_feat.attrs.get("display", "") or parent_feat.attrs.get("name", "") or "").strip()
        toggle_display = (sub.attrs.get("display", "") or "").strip()
        toggle_value = (sub.attrs.get("value", "") or "").strip()  # New value parameter
        action_on = sub.attrs.get("action_on", "")
        action_off = sub.attrs.get("action_off", "")
        refresh = int(sub.attrs.get("refresh", parent_feat.attrs.get("refresh", DEFAULT_REFRESH_SEC)))

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

        tbtn = Gtk.ToggleButton.new_with_label(tbtn_label)
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
                tbtn.set_label("ON")
            else:
                tbtn.set_label("OFF")

        # Track if we're currently updating from user action to prevent refresh conflicts
        toggle_state = {"updating": False, "last_user_change": 0}

        if status_cmd:
            # Get initial value immediately
            initial_val = run_shell_capture(status_cmd)
            initial_active = normalize_bool_str(initial_val)
            tbtn.set_active(initial_active)
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
        return tbtn

    def build_img(self, parent_feat, sub, row_box, pack_end=False):
        """Build an image widget from file path, URL, or ${...} command"""
        import urllib.request
        from gi.repository import GdkPixbuf

        disp = (sub.attrs.get("display", "") or "").strip()
        width = sub.attrs.get("width", "")
        height = sub.attrs.get("height", "")
        refresh = int(sub.attrs.get("refresh", parent_feat.attrs.get("refresh", DEFAULT_REFRESH_SEC)))

        # Parse width/height
        target_width = int(width) if width else None
        target_height = int(height) if height else None

        img = Gtk.Image()

        # Get alignment from attribute (default: center)
        align_attr = (sub.attrs.get("align", "center") or "center").strip().lower()
        if align_attr == "left":
            img.set_halign(Gtk.Align.START)
        elif align_attr == "right":
            img.set_halign(Gtk.Align.END)
        else:  # center (default)
            img.set_halign(Gtk.Align.CENTER)

        (row_box.pack_end if pack_end else row_box.pack_start)(img, False, False, 6)

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
    # Set a reasonable minimum height for the scrolled area
    scrolled.set_min_content_height(400)
    outer.pack_start(scrolled, True, True, 0)

    content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    scrolled.add(content_box)

    # hgroup blocks
    for child in xml_root.children:
        if child.kind == "hgroup":
            title = (child.attrs.get("display", "") or "").strip()
            target = _get_group_container_new(core, content_box, title)

            # Process all children normally - vgroups create rows
            for sub in child.children:
                if sub.kind == "vgroup":
                    vg = _build_vgroup_row(core, sub, is_header=False)
                    if vg:
                        target.pack_start(vg, False, False, 0)
                elif sub.kind == "feature":
                    fr = _build_feature_row(core, sub)
                    if fr:
                        target.pack_start(fr, False, False, 3)
                elif sub.kind == "text":
                    # Direct text element in hgroup - create a non-selectable row
                    text_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    text_row.set_border_width(4)
                    core.build_text(child, sub, text_row, align_end=False)
                    target.pack_start(text_row, False, False, 3)
                elif sub.kind == "img":
                    # Direct img element in hgroup - create a non-selectable row
                    img_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    img_row.set_border_width(4)
                    core.build_img(child, sub, img_row, pack_end=False)
                    target.pack_start(img_row, False, False, 3)

    # Non-header vgroups at root
    for child in xml_root.children:
        if child.kind == "vgroup" and (child.attrs.get("role", "") or "").strip().lower() != "header":
            vg = _build_vgroup_row(core, child, is_header=False)
            if vg:
                content_box.pack_start(vg, False, False, 0)

    # Standalone features at root
    for child in xml_root.children:
        if child.kind == "feature":
            fr = _build_feature_row(core, child)
            if fr:
                content_box.pack_start(fr, False, False, 3)

    win.connect("map", lambda *_: _init_focus(core))
    win.show_all()
    win.present()
    return win


def _init_focus(core: UICore):
    if not core.focus_rows:
        return
    for r in core.focus_rows:
        core._row_set_focused(r, False)
    core.focus_index = 0
    core._row_set_focused(core.focus_rows[0], True)
    _focus_widget(core.focus_rows[0])


def _get_group_container_new(core: UICore, parent_box: Gtk.Box, display_title: str):
    title = (display_title or "").strip()
    if title == "":
        return parent_box
    frame = Gtk.Frame()
    frame.get_style_context().add_class("group-frame")
    frame.set_shadow_type(Gtk.ShadowType.IN)
    frame.set_halign(Gtk.Align.CENTER)  # Center the frame
    # Set a consistent width for all groups (90% of window width)
    frame.set_size_request(int(core._window_width * 0.90), -1)
    label = Gtk.Label(label=title)
    label.get_style_context().add_class("group-title")
    frame.set_label_widget(label)
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    inner.set_border_width(6)
    frame.add(inner)
    parent_box.pack_start(frame, False, False, 0)
    return inner


def _build_vgroup_row(core: UICore, vg, is_header: bool) -> Gtk.EventBox:
    row = Gtk.EventBox()
    row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    row_box.set_halign(Gtk.Align.CENTER)  # Center the row contents
    # Set consistent width for all rows (90% of window width)
    row_box.set_size_request(int(core._window_width * 0.90), -1)
    row.add(row_box)
    row.set_above_child(False)
    row.get_style_context().add_class("vgroup-row")

    is_header_row = bool(is_header)

    cells = []
    for child in vg.children:
        # Handle direct <text> children in vgroup
        if child.kind == "text":
            cell_event = Gtk.EventBox()
            cell_event.get_style_context().add_class("vgroup-cell")
            if len(cells) == 0:
                cell_event.get_style_context().add_class("vgroup-cell-first")

            cell_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            cell_event.add(cell_box)

            # Build the text element
            core.build_text(vg, child, cell_box, align_end=False)

            # Text-only cells have no controls, so they're not interactive
            cells.append((cell_event, []))
            row_box.pack_start(cell_event, True, True, 0)
            continue

        # Handle direct <img> children in vgroup
        if child.kind == "img":
            cell_event = Gtk.EventBox()
            cell_event.get_style_context().add_class("vgroup-cell")
            if len(cells) == 0:
                cell_event.get_style_context().add_class("vgroup-cell-first")

            cell_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            cell_event.add(cell_box)

            # Build the img element
            core.build_img(vg, child, cell_box, pack_end=False)

            # Img-only cells have no controls, so they're not interactive
            cells.append((cell_event, []))
            row_box.pack_start(cell_event, True, True, 0)
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
                if nested_child.kind == "feature":
                    label_text = (nested_child.attrs.get("display", "") or nested_child.attrs.get("name", "") or "").strip()
                    if label_text:
                        lbl = Gtk.Label(label=label_text)
                        lbl.get_style_context().add_class("item-text")
                        lbl.set_xalign(0.0)
                        cell_box.pack_start(lbl, False, False, 0)

                    # Add feature children inline
                    for sub in nested_child.children:
                        if sub.kind == "text":
                            core.build_text(nested_child, sub, cell_box, align_end=False)
                        elif sub.kind == "img":
                            core.build_img(nested_child, sub, cell_box, pack_end=False)

            cells.append((cell_event, []))
            row_box.pack_start(cell_event, True, True, 0)
            continue

        # Handle nested <hgroup> children in vgroup
        if child.kind == "hgroup":
            cell_event = Gtk.EventBox()
            cell_event.get_style_context().add_class("vgroup-cell")
            if len(cells) == 0:
                cell_event.get_style_context().add_class("vgroup-cell-first")

            cell_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            cell_event.add(cell_box)

            # Process hgroup children (features, text, img, etc.)
            for hg_child in child.children:
                if hg_child.kind == "feature":
                    feat_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

                    label_text = (hg_child.attrs.get("display", "") or hg_child.attrs.get("name", "") or "").strip()
                    if label_text:
                        lbl = Gtk.Label(label=label_text)
                        lbl.get_style_context().add_class("item-text")
                        lbl.set_xalign(0.0)
                        feat_box.pack_start(lbl, False, False, 0)

                    # Add feature children
                    for sub in hg_child.children:
                        if sub.kind == "text":
                            core.build_text(hg_child, sub, feat_box, align_end=False)
                        elif sub.kind == "img":
                            core.build_img(hg_child, sub, feat_box, pack_end=False)

                    cell_box.pack_start(feat_box, False, False, 3)
                elif hg_child.kind == "text":
                    core.build_text(child, hg_child, cell_box, align_end=False)
                elif hg_child.kind == "img":
                    core.build_img(child, hg_child, cell_box, pack_end=False)

            cells.append((cell_event, []))
            row_box.pack_start(cell_event, True, True, 0)
            continue

        if child.kind != "feature":
            continue

        cell_event = Gtk.EventBox()
        cell_event.get_style_context().add_class("vgroup-cell")
        if len(cells) == 0:
            cell_event.get_style_context().add_class("vgroup-cell-first")

        cell_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)  # Reduced spacing
        cell_event.add(cell_box)

        label_text = (child.attrs.get("display", "") or child.attrs.get("name", "") or "").strip()
        if label_text:
            lbl = Gtk.Label(label=label_text)
            lbl.get_style_context().add_class("header" if is_header_row else "item-text")
            # Don't ellipsize - let text show fully
            lbl.set_xalign(0.0)
            cell_box.pack_start(lbl, False, False, 0)

        cell_controls: list[Gtk.Widget] = []
        for sub in child.children:
            if sub.kind == "text":
                core.build_text(child, sub, cell_box, align_end=False)
            elif sub.kind == "img":
                core.build_img(child, sub, cell_box, pack_end=False)
            elif sub.kind == "button":
                btn = core.build_button(child, sub, cell_box, pack_end=False)
                btn.set_can_focus(True)
                cell_controls.append(btn)
            elif sub.kind == "button_confirm":
                text = (sub.attrs.get("display", "") or "Confirm?").strip()
                action = sub.attrs.get("action", "")
                btn = Gtk.Button.new_with_label(text)
                btn.get_style_context().add_class("cc-button")
                btn.get_style_context().add_class("cc-button-confirm")
                btn.set_can_focus(True)
                cell_box.pack_start(btn, False, False, 6)

                def on_confirm_click(_w, _core=core, _text=text, _action=action):
                    _show_confirm_dialog(_core, _text, _action)

                btn.connect("clicked", on_confirm_click)
                cell_controls.append(btn)
            elif sub.kind == "toggle":
                tog = core.build_toggle(child, sub, cell_box, pack_end=False)
                tog.set_can_focus(True)
                cell_controls.append(tog)

        # Add choice button if feature has choice children
        choices = [c for c in child.children if c.kind == "choice"]
        if choices:
            feature_label = label_text or "Option"
            def open_choice(_core=core, _label=feature_label, _choices=choices):
                _open_choice_popup(_core, _label, _choices)

            choice_btn = Gtk.Button.new_with_label("Select")
            choice_btn.get_style_context().add_class("cc-button")
            choice_btn.get_style_context().add_class("cc-choice")
            choice_btn.set_can_focus(True)
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
        row_box.pack_start(cell_event, True, True, 0)

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
    row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row_box.set_halign(Gtk.Align.CENTER)  # Center the row contents
    # Set consistent width for all rows (90% of window width)
    row_box.set_size_request(int(core._window_width * 0.90), -1)
    row.add(row_box)
    row.set_above_child(False)

    display_label = (feat.attrs.get("display", "") or feat.attrs.get("name", "") or "").strip() or " "
    name_lbl = Gtk.Label(label=display_label)
    name_lbl.set_xalign(0.0)
    name_lbl.get_style_context().add_class("item-text")
    name_lbl.set_width_chars(15)  # Fixed width for label
    row_box.pack_start(name_lbl, False, False, 0)

    # Add spacer to push controls to the right
    spacer = Gtk.Box()
    spacer.set_hexpand(True)
    row_box.pack_start(spacer, True, True, 0)

    # Build children strictly in XML order, center value between buttons
    row._items = []
    row._item_index = 0

    for sub in feat.children:
        kind = sub.kind

        if kind == "button":
            text = (sub.attrs.get("display", "") or "Button").strip()
            action = sub.attrs.get("action", "")
            btn = Gtk.Button.new_with_label(text)
            btn.get_style_context().add_class("cc-button")
            btn.set_can_focus(True)
            btn.set_size_request(70, -1)  # Fixed width for buttons
            row_box.pack_start(btn, False, False, 8)
            btn.connect("clicked", core.make_action_cb(action, key=f"btn:{text}:{action}"))
            row._items.append(btn)

        elif kind == "button_confirm":
            text = (sub.attrs.get("display", "") or "Confirm?").strip()
            action = sub.attrs.get("action", "")
            btn = Gtk.Button.new_with_label(text)
            btn.get_style_context().add_class("cc-button")
            btn.get_style_context().add_class("cc-button-confirm")
            btn.set_can_focus(True)
            btn.set_size_request(70, -1)
            row_box.pack_start(btn, False, False, 8)

            def on_confirm_click(_w):
                _show_confirm_dialog(core, text, action)

            btn.connect("clicked", on_confirm_click)
            row._items.append(btn)

        elif kind == "text":
            lbl = Gtk.Label(label="")
            lbl.get_style_context().add_class("value")
            lbl.set_xalign(0.5)      # center
            lbl.set_width_chars(8)   # Fixed width for value
            row_box.pack_start(lbl, False, False, 8)
            disp = (sub.attrs.get("display", "") or "").strip()
            refresh = int(sub.attrs.get("refresh", feat.attrs.get("refresh", DEFAULT_REFRESH_SEC)))

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

                    def start(self):
                        self._schedule_tick(immediate=True)

                    def _schedule_tick(self, immediate=False):
                        delay = 1 if immediate else self.interval_ms
                        self._timer_id = GLib.timeout_add(delay, self._tick)

                    def _tick(self):
                        def work():
                            GLib.idle_add(self.update_fn)
                        threading.Thread(target=work, daemon=True).start()
                        self._schedule_tick(immediate=False)
                        return False

                core.refreshers.append(ExpandRefreshTask(upd_expand, refresh))
                lbl.set_text(expand_command_string(disp))
            elif is_cmd(disp):
                c = cmd_of(disp)
                def upd(val: str, _l=lbl): _l.set_text(val)
                core.refreshers.append(RefreshTask(upd, c, refresh))
            else:
                lbl.set_text(disp)

        elif kind == "img":
            core.build_img(feat, sub, row_box, pack_end=False)

        elif kind == "toggle":
            tog = core.build_toggle(feat, sub, row_box, pack_end=False)
            row._items.append(tog)

    # Choices (Select button only; current value is the <text> above)
    choices = [c for c in feat.children if c.kind == "choice"]
    if choices:
        def open_choice():
            _open_choice_popup(core, display_label, choices)

        choice_btn = Gtk.Button.new_with_label("Select")
        choice_btn.get_style_context().add_class("cc-button")
        choice_btn.get_style_context().add_class("cc-choice")
        choice_btn.set_can_focus(True)
        choice_btn.set_size_request(70, -1)  # Fixed width like other buttons
        row_box.pack_start(choice_btn, False, False, 8)
        choice_btn.connect("clicked", lambda *_: open_choice())

        row._items.append(choice_btn)
        if not hasattr(row, "_on_activate"):
            row._on_activate = open_choice

    # Only register row if it has interactive items
    if row._items:
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
            _activate_widget(row._items[row._item_index])

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

    return row


def _show_confirm_dialog(core: UICore, message: str, action: str):
    """Show a confirmation dialog before executing an action"""
    core._dialog_open = True  # Prevent main window from closing

    dialog = Gtk.Dialog(transient_for=core.window, modal=True)
    dialog.set_default_size(400, 200)
    dialog.set_decorated(False)
    dialog.set_resizable(False)
    dialog.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    dialog.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)

    # Style the dialog window itself
    dialog.get_style_context().add_class("popup-root")
    dialog.get_style_context().add_class("confirm-dialog")

    # Add frame for inner content
    frame = Gtk.Frame()
    frame.set_shadow_type(Gtk.ShadowType.NONE)

    content = dialog.get_content_area()
    content.set_border_width(0)  # Remove default border
    content.add(frame)

    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    inner.set_border_width(20)
    frame.add(inner)

    label = Gtk.Label(label=message)
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

    cancel_btn = Gtk.Button.new_with_label("Cancel")
    cancel_btn.get_style_context().add_class("cc-button")
    cancel_btn.set_size_request(100, -1)
    cancel_btn.set_can_focus(True)
    button_box.pack_start(cancel_btn, False, False, 0)
    cancel_btn.connect("clicked", lambda _: dialog.destroy())
    buttons.append(cancel_btn)

    confirm_btn = Gtk.Button.new_with_label("Confirm")
    confirm_btn.get_style_context().add_class("cc-button")
    confirm_btn.set_size_request(100, -1)
    confirm_btn.set_can_focus(True)
    button_box.pack_start(confirm_btn, False, False, 0)
    buttons.append(confirm_btn)

    def update_button_focus():
        for i, btn in enumerate(buttons):
            ctx = btn.get_style_context()
            if i == current_btn[0]:
                ctx.add_class("focused-cell")
                ctx.add_class("choice-selected")
                btn.grab_focus()
            else:
                ctx.remove_class("focused-cell")
                ctx.remove_class("choice-selected")

    def on_confirm(_w):
        if action:
            threading.Thread(target=lambda: run_shell_capture(action), daemon=True).start()
        dialog.destroy()

    # Override gamepad handler for dialog
    original_handler = core._handle_gamepad_action

    def dialog_gamepad_handler(action_key: str):
        core.reset_inactivity_timer()  # Reset timer on dialog interaction
        if action_key == "activate":
            if current_btn[0] == 1:
                on_confirm(None)
            else:
                dialog.destroy()
        elif action_key == "back":
            dialog.destroy()
        elif action_key in ("axis_left", "axis_right"):
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

    dialog.run()

    # Restore original handler
    core._handle_gamepad_action = original_handler
    core._dialog_open = False  # Allow main window to close again

    try:
        dialog.destroy()
    except:
        pass


def _open_choice_popup(core: UICore, feature_label: str, choices):
    """Open a popup dialog to select from available choices"""
    core._dialog_open = True  # Prevent main window from closing

    dialog = Gtk.Dialog(transient_for=core.window, modal=True)
    dialog.set_default_size(450, 350)
    dialog.set_decorated(False)
    dialog.set_resizable(False)
    dialog.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    dialog.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)

    # Style the dialog window itself
    dialog.get_style_context().add_class("popup-root")
    dialog.get_style_context().add_class("confirm-dialog")

    # Add frame for inner content
    frame = Gtk.Frame()
    frame.set_shadow_type(Gtk.ShadowType.NONE)

    content = dialog.get_content_area()
    content.set_border_width(0)  # Remove default border
    content.add(frame)

    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    inner.set_border_width(20)
    frame.add(inner)

    label = Gtk.Label(label=f"Choose {feature_label}:")
    label.set_xalign(0.5)  # Center the label
    label.get_style_context().add_class("group-title")
    inner.pack_start(label, False, False, 15)

    # Create a scrolled window for the choices
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_min_content_height(200)
    inner.pack_start(scrolled, True, True, 0)

    # Box to hold choice buttons
    choice_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    choice_box.set_border_width(6)
    scrolled.add(choice_box)

    choice_buttons = []
    current_choice = [0]

    def on_choice_selected(action: str):
        import threading
        dialog.response(Gtk.ResponseType.OK)
        if action:
            threading.Thread(target=lambda: run_shell_capture(action), daemon=True).start()

    def update_choice_focus():
        for i, btn in enumerate(choice_buttons):
            ctx = btn.get_style_context()
            if i == current_choice[0]:
                ctx.add_class("focused-cell")
                ctx.add_class("choice-selected")  # Additional class for choice highlighting
                btn.grab_focus()
            else:
                ctx.remove_class("focused-cell")
                ctx.remove_class("choice-selected")

    # Override core's gamepad handler temporarily for dialog navigation
    original_handler = core._handle_gamepad_action

    def dialog_gamepad_handler(action: str):
        core.reset_inactivity_timer()  # Reset timer on dialog interaction
        if action == "activate":
            if choice_buttons:
                choice_buttons[current_choice[0]].emit("clicked")
        elif action == "back":
            dialog.response(Gtk.ResponseType.CANCEL)
        elif action == "axis_up":
            current_choice[0] = max(0, current_choice[0] - 1)
            update_choice_focus()
        elif action == "axis_down":
            current_choice[0] = min(len(choice_buttons) - 1, current_choice[0] + 1)
            update_choice_focus()
        return False

    core._handle_gamepad_action = dialog_gamepad_handler

    # Create a button for each choice
    for choice in choices:
        display = (choice.attrs.get("display", "") or "Option").strip()
        action = choice.attrs.get("action", "")

        btn = Gtk.Button.new_with_label(display)
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
            dialog.response(Gtk.ResponseType.CANCEL)
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

    # Apply initial focus after dialog is shown
    if choice_buttons:
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

    dialog.run()
    dialog.destroy()

    # Restore original handler
    core._handle_gamepad_action = original_handler
    core._dialog_open = False  # Allow main window to close again


# ---- Application wrapper ----
class ControlCenterApp:
    def __init__(self, xml_root, css_path: str, auto_close_seconds: int = 0):
        self.core = UICore(css_path)
        self.auto_close_seconds = auto_close_seconds
        self.core._inactivity_timeout_seconds = auto_close_seconds
        self.window = ui_build_containers(self.core, xml_root)

    def run(self):
        self.core.start_refresh()
        self.core.start_gamepad()

        # Set up inactivity timer if specified (resets on user interaction)
        if self.auto_close_seconds > 0:
            self.core.reset_inactivity_timer()

        Gtk.main()

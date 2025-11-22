# ui_core.py - main UI components for the Control Center
# This file is part of the batocera distribution (https://batocera.org).
# Copyright (c) 2025 lbrpdx for the Batocera team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License
# as published by the Free Software Foundation, version 3.
#
# YOU MUST KEEP THIS HEADER AS IT IS

import time
from evdev import InputDevice, categorize, ecodes, list_devices
from gi.repository import GLib

class GamePads:
    def __init__(self):
        self._gamepad_devices = []

    def nb_devices(self):
        return len(self._gamepad_devices)

    def open_devices(self):
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

    def close_devices(self):
        """Release exclusive access to gamepad devices"""
        for dev in self._gamepad_devices:
            try:
                dev.ungrab()
                print(f"Released {dev.name}")
            except Exception:
                pass
        # Clear the list first so select() loop sees empty list
        devices_to_close = self._gamepad_devices[:]
        self._gamepad_devices = []
        # Small delay to let select() exit cleanly
        time.sleep(0.05)
        # Now close the devices
        for dev in devices_to_close:
            try:
                dev.close()
            except Exception:
                pass

    def stop_listen(self):
        self._gamepad_running = False

    def listen(self, f_handle_gamepad_action):
        """ where f_handle_gamepad_action is a function that takes 1 argument that can take the values:
        "activate"
        "back"
        "axis_up"
        "axis_down"
        "axis_left"
        "axis_right"
        """
        
        import select
        import time

        last_action = {}
        debounce_time = 0.15  # Faster gamepad response

        # Track axis states to detect movement
        axis_states = {}
        for dev in self._gamepad_devices:
            axis_states[dev.fd] = {}

        self._gamepad_running = True

        while self._gamepad_running:
            # Use select to wait for events from any device
            # Check if devices list is empty (shutdown in progress)
            if not self._gamepad_devices:
                break
            try:
                r, w, x = select.select(self._gamepad_devices, [], [], 0.1)
            except (OSError, ValueError):
                # File descriptor closed during select
                break
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
                                        GLib.idle_add(f_handle_gamepad_action, "activate")
                                    elif event.code in [ecodes.BTN_EAST, ecodes.BTN_B, ecodes.BTN_START, ecodes.BTN_SELECT]:
                                        GLib.idle_add(f_handle_gamepad_action, "back")
                                    # D-pad buttons (some controllers like PS3 use these)
                                    elif event.code == ecodes.BTN_DPAD_UP:
                                        GLib.idle_add(f_handle_gamepad_action, "axis_up")
                                    elif event.code == ecodes.BTN_DPAD_DOWN:
                                        GLib.idle_add(f_handle_gamepad_action, "axis_down")
                                    elif event.code == ecodes.BTN_DPAD_LEFT:
                                        GLib.idle_add(f_handle_gamepad_action, "axis_left")
                                    elif event.code == ecodes.BTN_DPAD_RIGHT:
                                        GLib.idle_add(f_handle_gamepad_action, "axis_right")

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
                                    GLib.idle_add(f_handle_gamepad_action, action_key)
                except Exception as e:
                    print(f"Error reading event: {e}")

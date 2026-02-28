# gamepads.py - Manage the controllers configured from ES
# This file is part of the batocera distribution (https://batocera.org).
# Copyright (c) 2025-2026 lbrpdx for the Batocera team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License
# as published by the Free Software Foundation, version 3.
#
# YOU MUST KEEP THIS HEADER AS IT IS

import time
import pyudev
from evdev import InputDevice, ecodes
import re
from pathlib import Path
import xml.etree.ElementTree as ET
import threading
from log import debug_print, DEBUG

from gi.repository import GLib


class GamePads:
    def __init__(self):
        self._gamepad_devices = []
        self._gamepad_thread = None
        self._continuous_timers = {}  # Track multiple continuous actions
        self._continuous_callbacks = {}  # Track callbacks for each action
        self._continuous_actions_enabled = False  # Control when continuous actions are active

    def nb_devices(self):
        return len(self._gamepad_devices)

    @staticmethod
    def dev2int(dev: str) -> int | None:
        matches = re.match(r"^/dev/input/event([0-9]*)$", dev) # limit events to the one of /dev/input/event* to avoid special things
        if matches is None:
            return None
        return int(matches.group(1))

    def open_devices(self):
        context = pyudev.Context()
        for event in context.list_devices(subsystem='input'):
            try:
                eventId = GamePads.dev2int(str(event.device_node))
                if eventId is not None:
                    isJoystick = ("ID_INPUT_JOYSTICK" in event.properties and event.properties["ID_INPUT_JOYSTICK"] == "1")
                    if isJoystick:
                        device = InputDevice(event.device_node)
                        debug_print(f"[GAMEPAD] Found gamepad: {device.name} at {event.device_node}")
                        self._gamepad_devices.append(device)
            except Exception as e:
                debug_print(f"[GAMEPAD] Error checking device {event}: {e}")

    def _grab_devices(self):
        for device in self._gamepad_devices:
            # Grab exclusive access to prevent EmulationStation from receiving events
            try:
                device.grab()
                debug_print(f"[GAMEPAD] Grabbed exclusive access to {device.name}")
            except Exception as e:
                debug_print(f"[GAMEPAD] Could not grab {device.name}: {e}")

    def close_devices(self):
        """Release exclusive access to gamepad devices"""
        for dev in self._gamepad_devices:
            try:
                dev.ungrab()
                dev.close()
                debug_print(f"[GAMEPAD] Released {dev.name}")
            except Exception:
                pass
        self._gamepad_devices = []

    def stop_listen(self):
        self._gamepad_running = False
        self._stop_all_continuous_actions()

    def _start_continuous_action(self, action, callback):
        """Start continuous action for the given action type"""
        self._stop_continuous_action(action)  # Stop any existing action of this type
        
        # Send initial action immediately
        GLib.idle_add(callback, action)
        
        # Determine interval based on action type
        if action in ["pan_up", "pan_down", "pan_left", "pan_right"]:
            interval = 100  # Fast panning (10 times per second)
        elif action in ["axis_up", "axis_down"]:  # Zoom actions
            interval = 200  # Medium zoom (5 times per second)
        elif action in ["axis_left", "axis_right"]:  # Page turning
            interval = 300  # Slower page turning (3.3 times per second)
        else:
            interval = 150  # Default interval
        
        # Start timer for continuous actions
        timer_id = GLib.timeout_add(interval, self._continuous_action_tick, action, callback)
        self._continuous_timers[action] = timer_id
        self._continuous_callbacks[action] = callback

    def _should_use_continuous_action(self, action_name):
        """Check if continuous actions should be used based on current context"""
        # Only use continuous actions when explicitly enabled (e.g., in document viewer)
        # and for specific navigation actions
        return action_name in ["joystick1up", "joystick1down", "joystick1left", "joystick1right",
                               "joystick2up", "joystick2down", "joystick2left", "joystick2right",
                               "up", "down", "left", "right"]

    def enable_continuous_actions(self):
        """Enable continuous actions (for document viewer)"""
        self._continuous_actions_enabled = True

    def disable_continuous_actions(self):
        """Disable continuous actions (for main window navigation)"""
        self._continuous_actions_enabled = False
        self._stop_all_continuous_actions()  # Stop any ongoing continuous actions

    def _continuous_action_tick(self, action, callback):
        """Timer callback for continuous actions"""
        if action in self._continuous_timers and callback:
            GLib.idle_add(callback, action)
            return True  # Continue timer
        return False  # Stop timer

    def _stop_continuous_action(self, action):
        """Stop continuous action for a specific action type"""
        if action in self._continuous_timers:
            GLib.source_remove(self._continuous_timers[action])
            del self._continuous_timers[action]
        if action in self._continuous_callbacks:
            del self._continuous_callbacks[action]

    def _stop_all_continuous_actions(self):
        """Stop all continuous actions"""
        for timer_id in self._continuous_timers.values():
            GLib.source_remove(timer_id)
        self._continuous_timers.clear()
        self._continuous_callbacks.clear()

    def startThread(self, handle_gamepad_action):
        def evdev_loop():
            try:
                self.open_devices()
                if self.nb_devices() == 0:
                    debug_print("[GAMEPAD] No gamepad devices found via evdev")
                    return
                self.listen(handle_gamepad_action)
            except Exception as e:
                debug_print(f"[GAMEPAD] Evdev gamepad error: {e}")
            finally:
                self.close_devices()
            debug_print("[GAMEPAD] end thread: evdev")

        # Store the thread so we can track it
        self._gamepad_thread = threading.Thread(target=evdev_loop, daemon=True)
        self._gamepad_thread.start()

    def stopThread(self):
        self.stop_listen()
        self._stop_all_continuous_actions()  # Stop any ongoing continuous actions
        if self._gamepad_thread is not None:
            self._gamepad_thread.join()
            self._gamepad_thread = None

    def get_mapping_axis_relaxed_values(self, device):
        """
        Axis released values
        To handle full axis (r2/l2 generally on some pads)
        """
        import evdev

        # read the sdl2 cache if possible for axis
        guid = GamePads.compute_guid(device.info.bustype, device.info.vendor, device.info.product, device.info.version)
        cache_file = Path(f"/userdata/system/.sdl2/{guid}_{device.name}.cache")
        if not cache_file.exists():
            return {}

        cache_content = cache_file.read_text(encoding="utf-8").splitlines()
        n = int(cache_content[0]) # number of lines of the cache

        relaxed_values: list[int] = [int(cache_content[i]) for i in range(1, n+1)]

        # get full list of axis (in case one is not used in es)
        caps = device.capabilities()
        code_values: dict[int, int]  = {}
        i = 0
        for code, _ in caps[evdev.ecodes.EV_ABS]:
            if code < evdev.ecodes.ABS_HAT0X:
                code_values[code] = relaxed_values[i]
                i = i+1

        # dict with es input names
        res: dict[str, _RelaxedDict] = {}
        for code, _ in caps[evdev.ecodes.EV_ABS]:
            if code < evdev.ecodes.ABS_HAT0X:
                # sdl values : from -32000 to 32000 / do not put < 0 cause a wheel/pad could be not correctly centered
                # 3 possible initial positions <1----------------|-------2-------|----------------3>
                val = code_values[code]
                res[code] = { "centered":  val > -4000 and val < 4000, "reversed": val > 4000 }
        return res

    @staticmethod
    def load_es_dbpads():
        configs = []
        for conffile in [Path("/userdata/system/configs/emulationstation/es_input.cfg"), Path("/usr/share/emulationstation/es_input.cfg")]:
            if conffile.exists():
                configs.append(ET.parse(conffile).getroot())
        return configs

    @staticmethod
    def compute_guid(bus, vendor, product, version):
        x = ""
        x += bus.to_bytes(2, byteorder='little').hex()
        x += "0000"
        x += vendor.to_bytes(2, byteorder='little').hex()
        x += "0000"
        x += product.to_bytes(2, byteorder='little').hex()
        x += "0000"
        x += version.to_bytes(2, byteorder='little').hex()
        x += "0000"
        return x

    @staticmethod
    def _find_best_controller_mapping(pads_configs, name, bus, vendor, product, version):
        guid = GamePads.compute_guid(bus, vendor, product, version)
        input_config = GamePads._find_input_config(pads_configs, name, guid)

        if input_config is None:
            return None

        mappings = {}
        for input in input_config:
            if input.tag == "input":
                input_name  = input.get("name")
                input_type  = input.get("type")
                input_code  = input.get("code")
                input_value = input.get("value")
                # hat in es (and thus sdl) are axis starting à 16, 16/17 for hat0, 18/19 for hat1, and so on
                if input_type == "hat":
                    input_code  = 16+int(input.get("id"))
                    if input_name == "up" or input_name == "down":
                        input_code += 1
                    if input_name == "up" or input_name == "left":
                        input_value = -1
                    else:
                        input_value = 1
                if input_name is not None and input_type is not None and input_code is not None and input_value is not None:
                    input_code = int(input_code)
                    input_value = int(input_value)
                    if input_type not in mappings:
                        mappings[input_type] = {}
                    if input_code not in mappings[input_type]:
                        mappings[input_type][input_code] = {}
                    if input_value not in mappings[input_type][input_code]:
                        mappings[input_type][input_code][input_value] = input_name
                        if input_type == "axis":
                            # es doesn't store all the axis sides
                            if input_name == "joystick1left":
                                mappings[input_type][input_code][-1*input_value] = "joystick1right"
                            if input_name == "joystick1up":
                                mappings[input_type][input_code][-1*input_value] = "joystick1down"
                            if input_name == "joystick2left":
                                mappings[input_type][input_code][-1*input_value] = "joystick2right"
                            if input_name == "joystick2up":
                                mappings[input_type][input_code][-1*input_value] = "joystick2down"
        return mappings

    @staticmethod
    def _find_input_config(pads_configs, name: str, guid: str):
        path = './inputConfig'

        for pads_config in pads_configs:
            element = pads_config.find(f'{path}[@deviceGUID="{guid}"][@deviceName="{name}"]')
            if element is not None:
                return element

        for pads_config in pads_configs:
            element = pads_config.find(f'{path}[@deviceGUID="{guid}"]')
            if element is not None:
                return element

        for pads_config in pads_configs:
            element = pads_config.find(f'{path}[@deviceName="{name}"]')
            if element is not None:
                return element
   
        return None

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

        # actions
        actions = {
            "b": "activate",
            "a": "back",
            "up": "axis_up",
            "down": "axis_down",
            "left": "axis_left",
            "right": "axis_right",
            "joystick1up": "axis_up",
            "joystick1down": "axis_down",
            "joystick1left": "axis_left",
            "joystick1right": "axis_right",
            "joystick2up": "pan_up",
            "joystick2down": "pan_down",
            "joystick2left": "pan_left",
            "joystick2right": "pan_right",
            "pageup": "previous_tab",
            "pagedown": "next_tab",
        }

        # get devices mappings
        pads_configs = GamePads.load_es_dbpads()
        mappings = {}
        for dev in self._gamepad_devices:
            mapping = GamePads._find_best_controller_mapping(pads_configs, dev.name, dev.info.bustype, dev.info.vendor, dev.info.product, dev.info.version)
            if mapping is None:
                debug_print(f"[GAMEPAD] Warning: No mapping found for gamepad {dev.name}")
                mappings[dev.fd] = {}  # Empty mapping to prevent errors
            else:
                mappings[dev.fd] = mapping

        # Track axis states to detect movement
        axis_states = {}
        axis_infos  = {}
        for dev in self._gamepad_devices:
            axis_states[dev.fd] = {}
            axis_infos[dev.fd]  = {}
            
            # get axis relaxed values for this specific device
            relaxValues = self.get_mapping_axis_relaxed_values(dev)
            
            if "axis" in mappings[dev.fd]:
                for code in mappings[dev.fd]["axis"]:
                    abs_info = dev.absinfo(code)
                    center = (abs_info.max + abs_info.min) // 2
                    if code in relaxValues and relaxValues[code]["centered"]:
                        threshold = (abs_info.max - abs_info.min) // 4  # Original 25% deadzone
                        bornemin = abs_info.min + threshold
                        bornemax = abs_info.max - threshold
                        debug_print(f"[GAMEPAD] Axis {code} centered deadzone: {bornemin} to {bornemax} (threshold: {threshold}, range: {abs_info.min}-{abs_info.max})")
                    else:
                        bornemin = abs_info.min -1 # can't reach it
                        bornemax = center
                        debug_print(f"[GAMEPAD] Axis {code} non-centered deadzone: {bornemin} to {bornemax} (center: {center}, range: {abs_info.min}-{abs_info.max})")
                    axis_infos[dev.fd][code] =  { "bornemin": bornemin, "bornemax": bornemax }
                    
                    # Initialize axis state properly using the same logic as event handling
                    current_value = abs_info.value
                    initial_axis_value = 0
                    if current_value < bornemin:
                        initial_axis_value = -1
                    elif current_value > bornemax:
                        initial_axis_value = 1
                    axis_states[dev.fd][code] = initial_axis_value
            else:
                pass

        # focus require that hotkeys down are received by underlaying apply
        # to not cause issue (retroarch for example think that hotkey remains down, then right alone forwards)
        self._grab_devices()
        self._gamepad_running = True

        while self._gamepad_running:
            # Use select to wait for events from any device
            # Check if devices list is empty (shutdown in progress)
            if len(self._gamepad_devices) == 0:
                break
            try:
                r, w, x = select.select(self._gamepad_devices, [], [], 0.1)
            except (OSError, ValueError):
                # File descriptor closed during select
                break

            for device in r:
                try:
                    for event in device.read():
                        self._handle_event(device, event, mappings[device.fd], axis_infos, axis_states, actions, f_handle_gamepad_action)

                except Exception as e:
                    debug_print(f"[GAMEPAD] Error reading event: {e}")

    def _handle_event(self, device, event, mapping, axis_infos, axis_states, actions, f_handle_gamepad_action):
        # Safety check - ensure mapping exists
        if not mapping:
            return
            
        if event.type == ecodes.EV_KEY:
            if event.value != 0:  # Button down
                if "button" in mapping and event.code in mapping["button"] and event.value in mapping["button"][event.code]:
                    if mapping["button"][event.code][event.value] in actions:
                        GLib.idle_add(f_handle_gamepad_action, actions[mapping["button"][event.code][event.value]])
        elif event.type == ecodes.EV_ABS:
            if event.code >= 16 and event.value != 0: # hat down
                if "hat" in mapping and event.code in mapping["hat"] and event.value in mapping["hat"][event.code]:
                    if mapping["hat"][event.code][event.value] in actions:
                        action_name = mapping["hat"][event.code][event.value]
                        action = actions[action_name]
                        # Use continuous actions only when enabled (document viewer)
                        if self._continuous_actions_enabled and self._should_use_continuous_action(action_name):
                            self._start_continuous_action(action, f_handle_gamepad_action)
                        else:
                            # Single action for main window navigation or when continuous actions disabled
                            GLib.idle_add(f_handle_gamepad_action, action)
            elif event.code >= 16 and event.value == 0: # hat released
                if "hat" in mapping and event.code in mapping["hat"]:
                    # Stop continuous actions for this hat only when continuous actions are enabled
                    if self._continuous_actions_enabled:
                        for hat_value in mapping["hat"][event.code]:
                            action_name = mapping["hat"][event.code][hat_value]
                            if action_name in actions and self._should_use_continuous_action(action_name):
                                action = actions[action_name]
                                self._stop_continuous_action(action)
            else:
                # axis - simplified logic similar to original
                if "axis" in mapping and event.code in mapping["axis"]:
                    # Check if we have the required data structures
                    if (device.fd in axis_states and event.code in axis_states[device.fd] and
                        device.fd in axis_infos and event.code in axis_infos[device.fd]):
                        
                        old_axis_value = axis_states[device.fd][event.code]
                        axis_value = 0
                        
                        # Determine new axis state with proper deadzone logic
                        if event.value < axis_infos[device.fd][event.code]["bornemin"]:
                            axis_value = -1
                        elif event.value > axis_infos[device.fd][event.code]["bornemax"]:
                            axis_value = 1
                        else:
                            axis_value = 0  # Within deadzone
                        
                        # Debug output for PS3 controller troubleshooting
                        if axis_value != old_axis_value:
                            debug_print(f"[GAMEPAD] Axis {event.code} changed from {old_axis_value} to {axis_value} ({event.value} in range {axis_infos[device.fd][event.code]['bornemin']} to {axis_infos[device.fd][event.code]['bornemax']})")
                        
                        # Update axis state
                        axis_states[device.fd][event.code] = axis_value
                        
                        # Handle axis activation (going from neutral to active)
                        if axis_value != 0 and old_axis_value == 0:
                            if axis_value in mapping["axis"][event.code]:
                                action_name = mapping["axis"][event.code][axis_value]
                                if action_name in actions:
                                    action = actions[action_name]
                                    
                                    # Use continuous or single action based on context
                                    if self._continuous_actions_enabled and self._should_use_continuous_action(action_name):
                                        self._start_continuous_action(action, f_handle_gamepad_action)
                                    else:
                                        GLib.idle_add(f_handle_gamepad_action, action)
                        
                        # Handle axis release (going from active to neutral)
                        elif axis_value == 0 and old_axis_value != 0:
                            if old_axis_value in mapping["axis"][event.code]:
                                action_name = mapping["axis"][event.code][old_axis_value]
                                if action_name in actions:
                                    action = actions[action_name]
                                    
                                    # Stop continuous action if it was running
                                    if self._continuous_actions_enabled and self._should_use_continuous_action(action_name):
                                        self._stop_continuous_action(action)
                        
                        # Handle axis direction change (from one direction to another)
                        elif axis_value != 0 and old_axis_value != 0 and axis_value != old_axis_value:
                            # Stop old action
                            if old_axis_value in mapping["axis"][event.code]:
                                old_action_name = mapping["axis"][event.code][old_axis_value]
                                if old_action_name in actions:
                                    old_action = actions[old_action_name]
                                    if self._continuous_actions_enabled and self._should_use_continuous_action(old_action_name):
                                        self._stop_continuous_action(old_action)
                            
                            # Start new action
                            if axis_value in mapping["axis"][event.code]:
                                action_name = mapping["axis"][event.code][axis_value]
                                if action_name in actions:
                                    action = actions[action_name]
                                    
                                    if self._continuous_actions_enabled and self._should_use_continuous_action(action_name):
                                        self._start_continuous_action(action, f_handle_gamepad_action)
                                    else:
                                        GLib.idle_add(f_handle_gamepad_action, action)

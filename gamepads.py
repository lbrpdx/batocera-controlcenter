# ui_core.py - main UI components for the Control Center
# This file is part of the batocera distribution (https://batocera.org).
# Copyright (c) 2025 lbrpdx for the Batocera team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License
# as published by the Free Software Foundation, version 3.
#
# YOU MUST KEEP THIS HEADER AS IT IS

# TODO
# read config from es for mapping
# axis : read and reput at center each time
# axis : full axis
# configurable mapping

import time
import pyudev
from evdev import InputDevice, ecodes
import re
from pathlib import Path
import xml.etree.ElementTree as ET

from gi.repository import GLib

class GamePads:
    def __init__(self):
        self._gamepad_devices = []

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
                        print(f"Found gamepad: {device.name} at {event.device_node}")
                        # Grab exclusive access to prevent EmulationStation from receiving events
                        try:
                            device.grab()
                            print(f"Grabbed exclusive access to {device.name}")
                        except Exception as e:
                            print(f"Could not grab {device.name}: {e}")

                        #
                        self._gamepad_devices.append(device)
            except Exception as e:
                print(f"Error checking device {event}: {e}")

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
            "joystick1right": "axis_right"
        }

        # get devices mappings
        pads_configs = GamePads.load_es_dbpads()
        mappings = {}
        for dev in self._gamepad_devices:
            mappings[dev.fd] = GamePads._find_best_controller_mapping(pads_configs, dev.name, dev.info.bustype, dev.info.vendor, dev.info.product, dev.info.version)

        # get axis relaxed values
        relaxValues = self.get_mapping_axis_relaxed_values(dev)

        # Track axis states to detect movement
        axis_states = {}
        axis_infos  = {}
        for dev in self._gamepad_devices:
            axis_states[dev.fd] = {}
            axis_infos[dev.fd]  = {}
            if "axis" in mappings[dev.fd]:
                for code in mappings[dev.fd]["axis"]:
                    abs_info = dev.absinfo(code)
                    center = (abs_info.max + abs_info.min) // 2
                    if relaxValues[code]["centered"]:
                        threshold = (abs_info.max - abs_info.min) // 4
                        bornemin = abs_info.min + threshold
                        bornemax = abs_info.max - threshold
                    else:
                        bornemin = abs_info.min -1 # can't reach it
                        bornemax = center
                    axis_infos[dev.fd][code] =  { "bornemin": bornemin, "bornemax": bornemax }
                    axis_states[dev.fd][code] = abs_info.value # relaxed

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
                        self._handle_event(device, event, mappings[device.fd], axis_infos[device.fd], axis_states, actions, f_handle_gamepad_action)
                except Exception as e:
                    print(f"Error reading event: {e}")

    def _handle_event(self, device, event, mapping, axis_infos, axis_states, actions, f_handle_gamepad_action):
        if event.type == ecodes.EV_KEY:
            if event.value != 0:  # Button down
                if "button" in mapping and event.code in mapping["button"] and event.value in mapping["button"][event.code]:
                    if mapping["button"][event.code][event.value] in actions:
                        GLib.idle_add(f_handle_gamepad_action, actions[mapping["button"][event.code][event.value]])
        elif event.type == ecodes.EV_ABS:
            if event.code >= 16 and event.value != 0: # hat down
                if "hat" in mapping and event.code in mapping["hat"] and event.value in mapping["hat"][event.code]:
                    if mapping["hat"][event.code][event.value] in actions:
                        GLib.idle_add(f_handle_gamepad_action, actions[mapping["hat"][event.code][event.value]])
            else:
                # axis
                if "axis" in mapping and event.code in mapping["axis"]:
                    axis_value = 0
                    if event.value < axis_infos[event.code]["bornemin"]:
                        axis_value = -1
                    elif event.value > axis_infos[event.code]["bornemax"]:
                        axis_value = 1
                    else:
                        axis_states[device.fd][event.code] = 0 # reset (relaxed required to reput)
                    if axis_value != 0 and axis_states[device.fd][event.code] == 0: # previous state must be relaxed
                        axis_states[device.fd][event.code] = axis_value
                        if axis_value in mapping["axis"][event.code]:
                            if mapping["axis"][event.code][axis_value] in actions:
                                GLib.idle_add(f_handle_gamepad_action, actions[mapping["axis"][event.code][axis_value]])

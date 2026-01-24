# xml_utils.py — XML model, parser, validator for Batocera Control Center
# This file is part of the batocera distribution (https://batocera.org).
# Copyright (c) 2025-2026 lbrpdx for the Batocera team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License
# as published by the Free Software Foundation, version 3.
#
# YOU MUST KEEP THIS HEADER AS IT IS
import xml.etree.ElementTree as ET

class CCElement:
    def __init__(self, kind: str, attrs: dict, children: list, line: int = -1):
        self.kind = kind
        self.attrs = attrs
        self.children = children
        self.line = line

def _get_line(elem: ET.Element) -> int:
    return getattr(elem, 'sourceline', -1)

def parse_xml(path: str) -> CCElement:
    parser = ET.XMLParser()
    tree = ET.parse(path, parser=parser)
    root = tree.getroot()

    def parse_node(node: ET.Element) -> CCElement:
        attrs = {k: v for k, v in node.attrib.items()}
        children = [parse_node(c) for c in list(node)]
        return CCElement(node.tag, attrs, children, line=_get_line(node))

    return parse_node(root)

def validate_xml(root: CCElement) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    # Allowed tags
    allowed_tags = {"features", "vgroup", "hgroup", "feature", "text", "button", "button_confirm", "toggle", "choice", "img", "qrcode", "doc", "tab", "progressbar", "switch"}

    # Requirements per tag (name optional everywhere now)
    required_per_tag = {
        "features": set(),
        "vgroup": set(),      # optional name, optional role, optional display (used as group title)
        "hgroup": set(),      # optional name, optional role, optional display (used as group title)
        "feature": set(),     # feature may have display (left-side label) but is optional
        "text": set(),        # display optional; supports ${...} command
        "button": {"action"}, # display optional, action required
        "button_confirm": {"display", "action"}, # display required (confirmation message), action required
        "toggle": set(),      # display and value both optional; action_on/off optional
        "switch": set(),      # display and value both optional; action_on/off optional (same as toggle)
        "choice": {"display", "action"},  # runs 'action' when selected
        "img": set(),         # display optional (path, URL, or ${...} command)
        "qrcode": set(),      # display optional (text, URL, or ${...} command to encode as QR)
        "doc": {"display", "content"},  # display for button label, content for document path
        "tab": set(),         # display optional (tab label)
        "progressbar": set(),     # display optional (value or ${...} command), min/max optional
    }

    # Known attributes by tag
    # Note: "id" and "if" are allowed on all tags (added during validation)
    known_attrs = {
        "features": {"name"},
        "vgroup": {"name", "role", "display"},
        "hgroup": {"name", "role", "display"},
        "feature": {"name", "group", "refresh", "display", "if"},
        "text": {"display", "refresh", "align"},
        "button": {"display", "action", "refresh", "align"},
        "button_confirm": {"display", "action", "refresh", "align"},
        "toggle": {"display", "value", "action_on", "action_off", "refresh", "align"},
        "switch": {"display", "value", "action_on", "action_off", "refresh", "align"},
        "choice": {"display", "action"},
        "img": {"display", "width", "height", "refresh", "align"},
        "qrcode": {"display", "width", "height", "refresh", "align", "bg", "style", "logo", "font"},
        "doc": {"display", "content", "refresh"},
        "tab": {"display", "align", "target"},
        "progressbar": {"display", "min", "max", "refresh", "align"},
    }

    def path_str(stack):
        return " > ".join([
            f"{e.kind}{'(' + e.attrs.get('name','') + ')' if e.attrs.get('name') else ''}"
            for e in stack
        ])

    def check_node(node: CCElement, stack: list[CCElement]):
        stack.append(node)

        if node.kind not in allowed_tags:
            errors.append(f"[line {node.line}] Unknown tag <{node.kind}> at {path_str(stack[:-1])}")
            stack.pop()
            return

        # Required attrs present
        for r in required_per_tag.get(node.kind, set()):
            if r not in node.attrs or (node.attrs.get(r, "").strip() == ""):
                errors.append(f"[line {node.line}] Missing required '{r}' on <{node.kind}> at {path_str(stack[:-1])}")

        # Unknown attrs (id and if are allowed on all elements)
        known = known_attrs.get(node.kind, set()) | {"id", "if"}
        for k in list(node.attrs.keys()):
            if k not in known:
                warnings.append(f"[line {node.line}] Unknown attribute '{k}' on <{node.kind}> at {path_str(stack[:-1])}")

        # refresh must be a number (int or float) >= 0 if present
        if "refresh" in node.attrs:
            val = (node.attrs.get("refresh") or "").strip()
            if val:
                try:
                    v = float(val)
                    if v < 0:
                        errors.append(f"[line {node.line}] refresh must be >= 0 on <{node.kind}> at {path_str(stack[:-1])}")
                except Exception:
                    errors.append(f"[line {node.line}] refresh must be a number on <{node.kind}> at {path_str(stack[:-1])}")

        # display formatting checks for any node carrying it
        if "display" in node.attrs:
            disp = (node.attrs.get("display") or "").strip()
            if disp == "":
                errors.append(f"[line {node.line}] 'display' cannot be empty on <{node.kind}> at {path_str(stack[:-1])}")
            # Validate ${...} command syntax - supports multiple commands in one string
            if "${" in disp:
                # Check for balanced ${ and } by tracking depth
                depth = 0
                i = 0
                while i < len(disp):
                    if i < len(disp) - 1 and disp[i:i+2] == "${":
                        depth += 1
                        i += 2
                    elif disp[i] == "}" and depth > 0:
                        depth -= 1
                        i += 1
                    else:
                        i += 1

                if depth != 0:
                    errors.append(
                        f"[line {node.line}] Malformed command in display; mismatched ${{...}} brackets at {path_str(stack[:-1])}"
                    )

        # toggle sanity checks
        if node.kind == "toggle":
            action_on = (node.attrs.get("action_on") or "").strip()
            action_off = (node.attrs.get("action_off") or "").strip()
            display = (node.attrs.get("display") or "").strip()
            value = (node.attrs.get("value") or "").strip()

            # Must have either display or value
            if not display and not value:
                errors.append(f"[line {node.line}] <toggle> must have either 'display' or 'value' attribute at {path_str(stack[:-1])}")

            # If both actions missing, toggle is read-only
            if not action_on and not action_off:
                warnings.append(f"[line {node.line}] <toggle> missing 'action_on'/'action_off'; toggle will be read-only at {path_str(stack[:-1])}")

        # button must have action (covered by required_per_tag); ensure non-empty
        if node.kind == "button":
            act = (node.attrs.get("action") or "").strip()
            if act == "":
                errors.append(f"[line {node.line}] <button> requires non-empty 'action' at {path_str(stack[:-1])}")

        # choice must have action (covered); ensure non-empty
        if node.kind == "choice":
            act = (node.attrs.get("action") or "").strip()
            if act == "":
                errors.append(f"[line {node.line}] <choice> requires non-empty 'action' at {path_str(stack[:-1])}")

        # img and qrcode width/height must be positive integers or percentages if present
        if node.kind in ("img", "qrcode"):
            for attr in ["width", "height"]:
                if attr in node.attrs:
                    val = (node.attrs.get(attr) or "").strip()
                    if val:
                        # Check if it's a percentage
                        if val.endswith('%'):
                            try:
                                percentage = float(val[:-1])
                                if percentage <= 0:
                                    errors.append(f"[line {node.line}] {attr} percentage must be > 0 on <{node.kind}> at {path_str(stack[:-1])}")
                            except ValueError:
                                errors.append(f"[line {node.line}] {attr} must be a valid percentage (e.g., '20%') on <{node.kind}> at {path_str(stack[:-1])}")
                        else:
                            # Check if it's an integer
                            try:
                                v = int(val)
                                if v <= 0:
                                    errors.append(f"[line {node.line}] {attr} must be > 0 on <{node.kind}> at {path_str(stack[:-1])}")
                            except ValueError:
                                errors.append(f"[line {node.line}] {attr} must be an integer or percentage (e.g., '200' or '20%') on <{node.kind}> at {path_str(stack[:-1])}")

        # vgroup can contain any child elements - no restrictions for flexibility

        # Recurse
        for c in node.children:
            check_node(c, stack)

        stack.pop()

    check_node(root, [])

    # Root must be <features>
    if root.kind != "features":
        errors.append(f"[line {root.line}] Root element must be <features>; found <{root.kind}>")

    return errors, warnings


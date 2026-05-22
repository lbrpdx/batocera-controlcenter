# This file is part of the batocera distribution (https://batocera.org).
# Copyright (c) 2025-2026 lbrpdx for the Batocera team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License
# as published by the Free Software Foundation, version 3.
#
# YOU MUST KEEP THIS HEADER AS IT IS

import gi
import os
gi.require_version('Gtk', '3.0'); gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, GLib, GdkPixbuf
import urllib.request
import tempfile
import subprocess

# for wayland
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    from gi.repository import Gdk
except:
    pass

import locale
_ = locale.gettext

class DocViewer:

    def __init__(self, is_wayland):
        self._handle_gamepad_action = None
        self._is_wayland = is_wayland

    def handle_gamepad_action(self, action: str):
        if self._handle_gamepad_action is not None:
            self._handle_gamepad_action(action)

    def open(self, parent_window, file_path: str, f_on_destroy, f_on_quit):
        """Open a fullscreen document viewer window (PDF, images, CBZ, or plain text)"""

        # Download file if it's a URL
        local_path = file_path
        temp_files = []  # Track temp files for cleanup

        if file_path.startswith(("http://", "https://")):
            try:
                with urllib.request.urlopen(file_path, timeout=10) as response:
                    # Try to get extension from URL
                    suffix = os.path.splitext(file_path.split('?')[0])[1]  # Remove query params
                    if not suffix:
                        suffix = ".tmp"  # Use temp extension, will detect from content
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                    temp_file.write(response.read())
                    temp_file.close()
                    local_path = temp_file.name
                    temp_files.append(local_path)
            except Exception as e:
                print(f"Error downloading file: {e}")
                return

        # Check if it's a PDF, CBZ, image, or text file based on file extension
        lower_path = local_path.lower()
        is_pdf = lower_path.endswith('.pdf')
        is_cbz = lower_path.endswith('.cbz')
        is_image = any(lower_path.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'])
        is_text = any(lower_path.endswith(ext) for ext in [
            '.txt', '.log', '.md', '.conf', '.cfg', '.ini', '.json', '.xml', '.yaml', '.yml'
        ])

        # If we can't determine from extension, try to detect from content (magic numbers)
        if not is_pdf and not is_cbz and not is_image and not is_text:
            try:
                with open(local_path, 'rb') as f:
                    header = f.read(16)  # Read more bytes for better detection
                    print(f"Detecting file type from content, first 8 bytes: {header[:8]}")
                    # Check for PDF magic number
                    if header.startswith(b'%PDF'):
                        is_pdf = True
                    # Check for ZIP/CBZ magic number (PK)
                    elif header.startswith(b'PK\x03\x04') or header.startswith(b'PK\x05\x06'):
                        is_cbz = True
                    # Check for common image formats
                    elif header.startswith(b'\x89PNG'):
                        is_image = True
                    elif header.startswith(b'\xff\xd8\xff'):  # JPEG
                        is_image = True
                    elif header.startswith(b'GIF87a') or header.startswith(b'GIF89a'):
                        is_image = True
                    elif header.startswith(b'BM'):  # BMP
                        is_image = True
                    elif len(header) >= 12 and header.startswith(b'RIFF') and header[8:12] == b'WEBP':
                        is_image = True
                    else:
                        # Try to detect if it's text (UTF-8 or ASCII)
                        try:
                            header.decode('utf-8')
                            is_text = True
                        except Exception:
                            pass
            except Exception as e:
                print(f"Error detecting file type: {e}")

        # Create fullscreen window
        viewer = Gtk.Window()

        if self._is_wayland:
            GtkLayerShell.init_for_window(viewer)
            GtkLayerShell.set_layer(viewer, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_keyboard_interactivity(viewer, False)
            # screen
            display = Gdk.Display.get_default()
            monitor = display.get_monitor(0)  # on batocera, 0 is the main screen, and 1 is the backglass
            GtkLayerShell.set_monitor(viewer, monitor)
            # screen size on wayland
            GtkLayerShell.set_anchor(viewer, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_anchor(viewer, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_anchor(viewer, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_anchor(viewer, GtkLayerShell.Edge.RIGHT, True)
        else:
            viewer.set_decorated(False)
            viewer.fullscreen()

        viewer.set_modal(True)
        viewer.set_transient_for(parent_window)
        viewer.get_style_context().add_class("popup-root")

        # Track if viewer is fully initialized (to ignore initial focus-out during fullscreen transition)
        viewer_initialized = [False]

        # Close everything if viewer loses focus to external app
        def on_viewer_focus_out(*_):
            # Ignore focus-out during initial setup
            if not viewer_initialized[0]:
                return False

            # Don't quit if we're just switching to the close button or other UI elements
            def check_and_close():
                # Only quit if the viewer window is completely inactive and not just switching focus internally
                if not viewer.is_active() and not viewer.has_focus():
                    # Additional check: make sure we're not just focusing on a child widget
                    focused_widget = viewer.get_focus()
                    if focused_widget is None:
                        f_on_quit()
                return False

            GLib.timeout_add(500, check_and_close)  # Increased delay to 500ms for better stability
            return False

        viewer.connect("focus-out-event", on_viewer_focus_out)

        # Add keyboard support as fallback
        def on_key_press(widget, event):
            # ESC key or B button equivalent
            if event.keyval == 65307:  # ESC key
                close_viewer()
                return True
            return False

        viewer.connect("key-press-event", on_key_press)

        # Mark viewer as initialized after fullscreen transition completes
        GLib.timeout_add(1000, lambda: (viewer_initialized.__setitem__(0, True), False)[1])

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        viewer.add(main_box)

        # Image display area (also used for text with TextView)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        main_box.pack_start(scrolled, True, True, 0)

        img = Gtk.Image()
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)

        # Text view for plain text files
        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_cursor_visible(False)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.get_style_context().add_class("doc-viewer-text")
        text_view.set_left_margin(20)
        text_view.set_right_margin(20)
        text_view.set_top_margin(20)
        text_view.set_bottom_margin(20)

        # Zoom functionality
        zoom_level = [1.0]  # Current zoom level (1.0 = 100%)
        original_pixbuf = [None]  # Store original pixbuf for images/PDFs/CBZ
        original_font_size = [14]  # Store original font size for text
        border_size = [0]

        def apply_initial_zoom():
            if original_pixbuf[0]:
                zoom_level[0] = get_initial_zoom(original_pixbuf[0], viewer)
                apply_zoom()

        def get_initial_zoom(pixbuf, viewer):
            if not pixbuf or viewer is None:
                return 1.0

            # Try logical window size first
            alloc = viewer.get_allocation()
            avail_w = alloc.width
            avail_h = alloc.height

            # If not realized yet, fall back to screen
            if avail_w <= 1 or avail_h <= 1:
                screen = viewer.get_screen()
                if screen:
                    avail_w = screen.get_width()
                    avail_h = screen.get_height()
                else:
                    return 1.0

            # Leave room for bottom buttons
            avail_h -= border_size[0]
            if avail_h <= 0:
                avail_h = 1
            zoomX = avail_w / pixbuf.get_width()
            zoomY = avail_h / pixbuf.get_height()

            # Fit both dimensions, allow upscaling
            return min(zoomX, zoomY)

        def apply_zoom():
            """Apply current zoom level to the active content"""
            if (is_image or is_pdf or is_cbz) and original_pixbuf[0]:
                orig_width = original_pixbuf[0].get_width()
                orig_height = original_pixbuf[0].get_height()
                new_width = int(orig_width * zoom_level[0])
                new_height = int(orig_height * zoom_level[0])
                scaled_pixbuf = original_pixbuf[0].scale_simple(
                    new_width, new_height, GdkPixbuf.InterpType.BILINEAR
                )
                img.set_from_pixbuf(scaled_pixbuf)
            elif is_text:
                # Zoom text by changing font size
                new_font_size = max(6, int(original_font_size[0] * zoom_level[0]))  # Minimum 6pt font
                from gi.repository import Pango
                font_desc = Pango.FontDescription()
                font_desc.set_family("monospace")
                font_desc.set_size(new_font_size * Pango.SCALE)  # Pangp.SCALE is the correct multiplier
                text_view.override_font(font_desc)

        def zoom_in():
            """Increase zoom level"""
            zoom_level[0] = min(zoom_level[0] * 1.2, 5.0)  # Max 500% zoom
            apply_zoom()

        def zoom_out():
            """Decrease zoom level"""
            zoom_level[0] = max(zoom_level[0] / 1.2, 0.2)  # Min 20% zoom
            apply_zoom()

        # Panning functionality for right analog stick
        def pan_content(direction):
            """Pan the scrolled content in the specified direction"""
            h_adj = scrolled.get_hadjustment()
            v_adj = scrolled.get_vadjustment()

            # Pan step size (adjust as needed)
            pan_step = 50

            if direction == "pan_up":
                new_value = max(v_adj.get_value() - pan_step, v_adj.get_lower())
                v_adj.set_value(new_value)
            elif direction == "pan_down":
                new_value = min(v_adj.get_value() + pan_step, v_adj.get_upper() - v_adj.get_page_size())
                v_adj.set_value(new_value)
            elif direction == "pan_left":
                new_value = max(h_adj.get_value() - pan_step, h_adj.get_lower())
                h_adj.set_value(new_value)
            elif direction == "pan_right":
                new_value = min(h_adj.get_value() + pan_step, h_adj.get_upper() - h_adj.get_page_size())
                h_adj.set_value(new_value)

        # Define close function early so it can be used by all handlers
        def close_viewer(*_):
            """Properly close the viewer and clean up"""
            viewer.destroy()  # This will trigger the destroy signal and call on_destroy
            return False

        # === IMAGE ===
        if is_image:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(local_path)

                # Store original pixbuf for zooming
                original_pixbuf[0] = pixbuf

                img.set_from_pixbuf(pixbuf)
                scrolled.add(img)

                button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                button_box.set_halign(Gtk.Align.CENTER)
                button_box.set_border_width(10)
                main_box.pack_start(button_box, False, False, 0)

                close_btn = Gtk.Button.new_with_label(_("Close"))
                close_btn.get_style_context().add_class("cc-button")
                close_btn.connect("clicked", close_viewer)
                button_box.pack_start(close_btn, False, False, 0)
                x, psize = button_box.get_preferred_size()
                border_size[0] = psize.height + 10 * 2

                def img_gamepad_handler(action: str):
                    if action in ("back", "activate"):
                        close_viewer()
                    elif action == "axis_up":
                        zoom_in()
                    elif action == "axis_down":
                        zoom_out()
                    elif action in ("pan_up", "pan_down", "pan_left", "pan_right"):
                        pan_content(action)
                    return False

                self._handle_gamepad_action = img_gamepad_handler

            except Exception as e:
                print(f"Error loading image: {e}")
                error_label = Gtk.Label(label=f"Error loading image: {e}")
                main_box.pack_start(error_label, True, True, 20)

        # === PDF ===
        elif is_pdf:
            try:
                from gi.repository import Gio

                scrolled.add(img)

                result = subprocess.run(['pdfinfo', local_path], capture_output=True, text=True)
                page_count = 1
                for line in result.stdout.split('\n'):
                    if line.startswith('Pages:'):
                        try:
                            page_count = int(line.split(':')[1].strip())
                        except Exception:
                            pass
                        break

                current_page = [1]
                first_page_rendered = [False]

                def render_page(page_num):
                    if 1 <= page_num <= page_count:
                        try:
                            cmd = [
                                'pdftoppm',
                                '-jpeg',
                                '-r', '120',         # 120 DPI is good enough
                                '-f', str(page_num), # First page
                                '-l', str(page_num), # Last page
                                local_path
                            ]

                            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                            if proc.returncode != 0:
                                print(f"pdftoppm error: {proc.stderr.decode('utf-8', errors='ignore')}")
                                return

                            img_data = proc.stdout
                            if not img_data or len(img_data) < 50:
                                print(f"Error: No data received for page {page_num}")
                                return

                            stream = Gio.MemoryInputStream.new_from_data(img_data, None)
                            pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
                            stream.close()

                            original_pixbuf[0] = pixbuf

                            # Only compute initial zoom once, for the first rendered page
                            if not first_page_rendered[0]:
                                zoom_level[0] = get_initial_zoom(original_pixbuf[0], viewer)
                                first_page_rendered[0] = True

                            apply_zoom()

                            current_page[0] = page_num

                            if page_count > 1:
                                prev_btn.set_sensitive(page_num > 1)
                                next_btn.set_sensitive(page_num < page_count)
                                page_label.set_text(f"{page_num} / {page_count}")

                        except Exception as e:
                            print(f"Error rendering page {page_num}: {e}")

                button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                button_box.set_halign(Gtk.Align.CENTER)
                button_box.set_border_width(10)
                main_box.pack_start(button_box, False, False, 0)

                if page_count > 1:
                    prev_btn = Gtk.Button.new_with_label("◀ " + _("Previous"))
                    prev_btn.get_style_context().add_class("cc-button")
                    prev_btn.connect("clicked", lambda *_: render_page(current_page[0] - 1))
                    button_box.pack_start(prev_btn, False, False, 0)

                    page_label = Gtk.Label()
                    page_label.get_style_context().add_class("value")
                    button_box.pack_start(page_label, False, False, 20)

                    next_btn = Gtk.Button.new_with_label(_("Next") + " ▶")
                    next_btn.get_style_context().add_class("cc-button")
                    next_btn.connect("clicked", lambda *_: render_page(current_page[0] + 1))
                    button_box.pack_start(next_btn, False, False, 0)

                close_btn = Gtk.Button.new_with_label(_("Close"))
                close_btn.get_style_context().add_class("cc-button")
                close_btn.connect("clicked", close_viewer)
                button_box.pack_start(close_btn, False, False, 20)
                x, psize = button_box.get_preferred_size()
                border_size[0] = psize.height + 10 * 2 + 20 * 2

                render_page(1)

                def pdf_gamepad_handler(action: str):
                    if action in ("activate", "axis_right"):
                        render_page(current_page[0] + 1)
                    elif action == "back":
                        close_viewer()
                    elif action == "axis_left":
                        render_page(current_page[0] - 1)
                    elif action == "axis_up":
                        zoom_in()
                    elif action == "axis_down":
                        zoom_out()
                    elif action in ("pan_up", "pan_down", "pan_left", "pan_right"):
                        pan_content(action)
                    return False

                self._handle_gamepad_action = pdf_gamepad_handler

            except Exception as e:
                print(f"Error loading PDF: {e}")
                error_label = Gtk.Label(
                    label=f"Error loading PDF: {e}\nMake sure pdftoppm and pdfinfo are installed."
                )
                error_label.set_line_wrap(True)
                main_box.pack_start(error_label, True, True, 20)

        # === CBZ ===
        elif is_cbz:
            try:
                import zipfile
                from gi.repository import Gio
                import re

                scrolled.add(img)

                cbz_file = zipfile.ZipFile(local_path, 'r')

                image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')
                all_files = cbz_file.namelist()
                image_files = [f for f in all_files if f.lower().endswith(image_extensions)]

                def natural_sort_key(s):
                    return [int(text) if text.isdigit() else text.lower()
                            for text in re.split('([0-9]+)', s)]

                image_files.sort(key=natural_sort_key)

                if not image_files:
                    raise Exception("No images found in CBZ file")

                page_count = len(image_files)
                current_page = [0]
                first_page_rendered = [False]

                def render_page(page_num):
                    if 0 <= page_num < page_count:
                        try:
                            file_name = image_files[page_num]
                            img_data = cbz_file.read(file_name)

                            stream = Gio.MemoryInputStream.new_from_data(img_data, None)
                            pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
                            stream.close()

                            original_pixbuf[0] = pixbuf

                            # Only compute initial zoom once, on first rendered page
                            if not first_page_rendered[0]:
                                zoom_level[0] = get_initial_zoom(original_pixbuf[0], viewer)
                                first_page_rendered[0] = True

                            apply_zoom()

                            current_page[0] = page_num

                            if page_count > 1:
                                prev_btn.set_sensitive(page_num > 0)
                                next_btn.set_sensitive(page_num < page_count - 1)
                                page_label.set_text(f"{page_num + 1} / {page_count}")

                        except Exception as e:
                            print(f"Error rendering page {page_num + 1}: {e}")

                button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                button_box.set_halign(Gtk.Align.CENTER)
                button_box.set_border_width(10)
                main_box.pack_start(button_box, False, False, 0)

                if page_count > 1:
                    prev_btn = Gtk.Button.new_with_label("◀ " + _("Previous"))
                    prev_btn.get_style_context().add_class("cc-button")
                    prev_btn.connect("clicked", lambda *_: render_page(current_page[0] - 1))
                    button_box.pack_start(prev_btn, False, False, 0)

                    page_label = Gtk.Label()
                    page_label.get_style_context().add_class("value")
                    button_box.pack_start(page_label, False, False, 20)

                    next_btn = Gtk.Button.new_with_label(_("Next") + " ▶")
                    next_btn.get_style_context().add_class("cc-button")
                    next_btn.connect("clicked", lambda *_: render_page(current_page[0] + 1))
                    button_box.pack_start(next_btn, False, False, 0)

                close_btn = Gtk.Button.new_with_label(_("Close"))
                close_btn.get_style_context().add_class("cc-button")
                close_btn.connect("clicked", close_viewer)
                button_box.pack_start(close_btn, False, False, 20)
                x, psize = button_box.get_preferred_size()
                border_size[0] = psize.height + 10 * 2 + 20 * 2

                render_page(0)

                def cbz_gamepad_handler(action: str):
                    if action in ("activate", "axis_right"):
                        render_page(current_page[0] + 1)
                    elif action == "back":
                        close_viewer()
                    elif action == "axis_left":
                        render_page(current_page[0] - 1)
                    elif action == "axis_up":
                        zoom_in()
                    elif action == "axis_down":
                        zoom_out()
                    elif action in ("pan_up", "pan_down", "pan_left", "pan_right"):
                        pan_content(action)
                    return False

                self._handle_gamepad_action = cbz_gamepad_handler

                def clean_up_zip(*_):
                    try:
                        cbz_file.close()
                    except Exception:
                        pass

                viewer.connect("destroy", clean_up_zip)

            except Exception as e:
                print(f"Error loading CBZ: {e}")
                error_label = Gtk.Label(label=f"Error loading CBZ: {e}")
                error_label.set_line_wrap(True)
                main_box.pack_start(error_label, True, True, 20)

        # === TEXT ===
        elif is_text:
            try:
                with open(local_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()

                text_buffer = text_view.get_buffer()
                text_buffer.set_text(content)

                from gi.repository import Pango
                font_desc = Pango.FontDescription()
                font_desc.set_family("monospace")
                font_desc.set_size(14 * Pango.SCALE)
                text_view.override_font(font_desc)
                original_font_size[0] = 14

                scrolled.add(text_view)

                button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                button_box.set_halign(Gtk.Align.CENTER)
                button_box.set_border_width(10)
                main_box.pack_start(button_box, False, False, 0)

                close_btn = Gtk.Button.new_with_label(_("Close"))
                close_btn.get_style_context().add_class("cc-button")
                close_btn.connect("clicked", close_viewer)
                button_box.pack_start(close_btn, False, False, 0)
                x, psize = button_box.get_preferred_size()
                border_size[0] = psize.height + 10 * 2

                def text_gamepad_handler(action: str):
                    if action in ("back", "activate"):
                        close_viewer()
                    elif action == "axis_up":
                        zoom_in()
                    elif action == "axis_down":
                        zoom_out()
                    elif action in ("pan_up", "pan_down", "pan_left", "pan_right"):
                        pan_content(action)
                    return False

                self._handle_gamepad_action = text_gamepad_handler

            except Exception as e:
                print(f"Error loading text file: {e}")
                error_label = Gtk.Label(label=f"Error loading text file: {e}")
                error_label.set_line_wrap(True)
                main_box.pack_start(error_label, True, True, 20)

        else:
            error_label = Gtk.Label(
                label=f"Unsupported file type: {local_path}\nSupported: PDF, CBZ, JPG, PNG, GIF, TXT"
            )
            error_label.set_line_wrap(True)
            main_box.pack_start(error_label, True, True, 20)

        def on_destroy(*_):
            import shutil
            for temp_file in temp_files:
                try:
                    if os.path.isdir(temp_file):
                        shutil.rmtree(temp_file)
                    elif os.path.exists(temp_file):
                        os.unlink(temp_file)
                except Exception:
                    pass
            f_on_destroy()

        # Only images get a one-shot global initial zoom; PDFs/CBZ do it per-page
        if is_image:
            apply_initial_zoom()

        viewer.connect("destroy", on_destroy)
        viewer.show_all()


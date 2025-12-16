# This file is part of the batocera distribution (https://batocera.org).
# Copyright (c) 2025 lbrpdx for the Batocera team
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

import locale
_ = locale.gettext

class PdfViewer:

    def __init__(self):
        self._handle_gamepad_action = None

    def handle_gamepad_action(self, action: str):
        if self._handle_gamepad_action is not None:
            self._handle_gamepad_action(action)

    def open(self, parent_window, file_path: str, f_on_destroy, f_on_quit):
        """Open a fullscreen PDF or image viewer window"""
    
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
    
        # Check if it's a PDF, CBZ, or image based on file extension
        lower_path = local_path.lower()
        is_pdf = lower_path.endswith('.pdf')
        is_cbz = lower_path.endswith('.cbz')
        is_image = any(lower_path.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'])
    
        # If we can't determine from extension, try to detect from content (magic numbers)
        if not is_pdf and not is_cbz and not is_image:
            try:
                with open(local_path, 'rb') as f:
                    header = f.read(16)  # Read more bytes for better detection
                    print(f"Detecting file type from content, first 8 bytes: {header[:8]}")
                    # Check for PDF magic number
                    if header.startswith(b'%PDF'):
                        is_pdf = True
                        # print("Detected as PDF from content")
                    # Check for ZIP/CBZ magic number (PK)
                    elif header.startswith(b'PK\x03\x04') or header.startswith(b'PK\x05\x06'):
                        is_cbz = True
                        # print("Detected as CBZ/ZIP from content")
                    # Check for common image formats
                    elif header.startswith(b'\x89PNG'):
                        is_image = True
                        # print("Detected as PNG from content")
                    elif header.startswith(b'\xff\xd8\xff'):  # JPEG
                        is_image = True
                        # print("Detected as JPEG from content")
                    elif header.startswith(b'GIF87a') or header.startswith(b'GIF89a'):
                        is_image = True
                        # print("Detected as GIF from content")
                    elif header.startswith(b'BM'):  # BMP
                        is_image = True
                        # print("Detected as BMP from content")
                    elif len(header) >= 12 and header.startswith(b'RIFF') and header[8:12] == b'WEBP':
                        is_image = True
                        # print("Detected as WEBP from content")
            except Exception as e:
                print(f"Error detecting file type: {e}")
    
        # print(f"File type: is_pdf={is_pdf}, is_image={is_image}, path={local_path}")
    
        # Create fullscreen window
        viewer = Gtk.Window()
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
    
            def check_and_close():
                if not viewer.is_active():
                    f_on_quit()
                return False
            GLib.timeout_add(200, check_and_close)
            return False
        viewer.connect("focus-out-event", on_viewer_focus_out)
    
        # Mark viewer as initialized after fullscreen transition completes
        GLib.timeout_add(1000, lambda: (viewer_initialized.__setitem__(0, True), False)[1])
    
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        viewer.add(main_box)
    
        # Image display area
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        main_box.pack_start(scrolled, True, True, 0)
    
        img = Gtk.Image()
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        scrolled.add(img)
    
        # Define close function early so it can be used by all handlers
        def close_viewer(*_):
            """Properly close the viewer and clean up"""
            viewer.hide()  # Hide immediately to prevent background visibility
            GLib.idle_add(viewer.destroy)  # Destroy in idle to ensure clean shutdown
            return False
    
        if is_image:
            # Image handling - do this first before PDF
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(local_path)
    
                # Scale to fit screen
                screen_width = parent_window.get_screen().get_width()
                screen_height = parent_window.get_screen().get_height() - 100
    
                orig_width = pixbuf.get_width()
                orig_height = pixbuf.get_height()
    
                scale = min(screen_width / orig_width, screen_height / orig_height, 1.0)
                if scale < 1.0:
                    new_width = int(orig_width * scale)
                    new_height = int(orig_height * scale)
                    pixbuf = pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
    
                img.set_from_pixbuf(pixbuf)
    
                # Close button for images
                button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                button_box.set_halign(Gtk.Align.CENTER)
                button_box.set_border_width(10)
                main_box.pack_start(button_box, False, False, 0)
    
                close_btn = Gtk.Button.new_with_label(_("Close"))
                close_btn.get_style_context().add_class("cc-button")
                close_btn.connect("clicked", close_viewer)
                button_box.pack_start(close_btn, False, False, 0)
    
                # Gamepad navigation for images
                def img_gamepad_handler(action: str):
                    if action in ("back", "activate"):
                        close_viewer()
                    return False
                self._handle_gamepad_action = img_gamepad_handler
    
            except Exception as e:
                print(f"Error loading image: {e}")
                error_label = Gtk.Label(label=f"Error loading image: {e}")
                main_box.pack_start(error_label, True, True, 20)
    
        elif is_pdf:
            # PDF handling with pdftoppm (now written in memory, not /tmp)
            try:
                from gi.repository import Gio
    
                # Get page count
                result = subprocess.run(['pdfinfo', local_path], capture_output=True, text=True)
                page_count = 1
                for line in result.stdout.split('\n'):
                    if line.startswith('Pages:'):
                        try:
                            page_count = int(line.split(':')[1].strip())
                        except:
                            pass
                        break
    
                current_page = [1]  # PDF pages are 1-indexed
    
                def render_page(page_num):
                    if 1 <= page_num <= page_count:
                        try:
                            # Render directly to memory (stdout)
                            cmd = [
                                'pdftoppm',
                                '-jpeg',
                                '-r', '120',  # 120 DPI quality is good enough
                                '-f', str(page_num),  # First page
                                '-l', str(page_num),  # Last page
                                local_path
                            ]
    
                            # Run the command and capture binary output
                            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
                            if proc.returncode != 0:
                                print(f"pdftoppm error: {proc.stderr.decode('utf-8', errors='ignore')}")
                                return
    
                            img_data = proc.stdout
    
                            if not img_data or len(img_data) < 50:
                                print(f"Error: No data received for page {page_num}")
                                return
    
                            # Create Pixbuf directly from the memory buffer
                            stream = Gio.MemoryInputStream.new_from_data(img_data, None)
                            pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
                            stream.close()
    
                            # Scale to fit screen
                            screen_width = parent_window.get_screen().get_width()
                            screen_height = parent_window.get_screen().get_height() - 100  # Leave room for buttons
    
                            orig_width = pixbuf.get_width()
                            orig_height = pixbuf.get_height()
    
                            scale = min(screen_width / orig_width, screen_height / orig_height, 1.0)
                            if scale < 1.0:
                                new_width = int(orig_width * scale)
                                new_height = int(orig_height * scale)
                                pixbuf = pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
    
                            img.set_from_pixbuf(pixbuf)
                            current_page[0] = page_num
    
                            # Update button sensitivity (only if multi-page)
                            if page_count > 1:
                                prev_btn.set_sensitive(page_num > 1)
                                next_btn.set_sensitive(page_num < page_count)
                                page_label.set_text(f"{page_num} / {page_count}")
    
                        except Exception as e:
                            print(f"Error rendering page {page_num}: {e}")
    
                # Navigation buttons for PDF
                button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                button_box.set_halign(Gtk.Align.CENTER)
                button_box.set_border_width(10)
                main_box.pack_start(button_box, False, False, 0)
    
                # Only show prev/next buttons if more than one page
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
    
                # Render first page
                render_page(1)
    
                # Gamepad navigation
                def pdf_gamepad_handler(action: str):
                    if action == "activate" or action == "axis_right":
                        render_page(current_page[0] + 1)
                    elif action == "back":
                        close_viewer()
                    elif action == "axis_left":
                        render_page(current_page[0] - 1)
                    return False
                self._handle_gamepad_action = pdf_gamepad_handler
    
            except Exception as e:
                print(f"Error loading PDF: {e}")
                error_label = Gtk.Label(label=f"Error loading PDF: {e}\nMake sure pdftoppm and pdfinfo are installed.")
                error_label.set_line_wrap(True)
                main_box.pack_start(error_label, True, True, 20)
    
        elif is_cbz:
            # CBZ handling (Comic Book Archive - ZIP file with images)
            try:
                import zipfile
                from gi.repository import Gio
                import re
    
                # No write on /tmp we keep the zipfile object open for the duration of the viewer
                cbz_file = zipfile.ZipFile(local_path, 'r')
    
                # Find all image files inside the zip
                image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')
                all_files = cbz_file.namelist()
                image_files = [f for f in all_files if f.lower().endswith(image_extensions)]
    
                # Sort images naturally (by filename)
                def natural_sort_key(s):
                    return [int(text) if text.isdigit() else text.lower()
                            for text in re.split('([0-9]+)', s)]
                image_files.sort(key=natural_sort_key)
    
                if not image_files:
                    raise Exception("No images found in CBZ file")
    
                page_count = len(image_files)
                current_page = [0]  # 0-indexed for list access
    
                def render_page(page_num):
                    if 0 <= page_num < page_count:
                        try:
                            # 1. Read the specific file from ZIP into RAM
                            file_name = image_files[page_num]
                            img_data = cbz_file.read(file_name)
    
                            # 2. Create a Gio Memory Input Stream
                            stream = Gio.MemoryInputStream.new_from_data(img_data, None)
    
                            # 3. Create Pixbuf directly from stream
                            pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
    
                            # 4. Close the stream (data is in pixbuf now)
                            stream.close()
    
                            # Scale to fit screen
                            screen_width = parent_window.get_screen().get_width()
                            screen_height = parent_window.get_screen().get_height() - 100
                            orig_width = pixbuf.get_width()
                            orig_height = pixbuf.get_height()
    
                            scale = min(screen_width / orig_width, screen_height / orig_height, 1.0)
                            if scale < 1.0:
                                new_width = int(orig_width * scale)
                                new_height = int(orig_height * scale)
                                pixbuf = pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
    
                            img.set_from_pixbuf(pixbuf)
                            current_page[0] = page_num
    
                            # Update button sensitivity (only if multi-page)
                            if page_count > 1:
                                prev_btn.set_sensitive(page_num > 0)
                                next_btn.set_sensitive(page_num < page_count - 1)
                                page_label.set_text(f"{page_num + 1} / {page_count}")
    
                        except Exception as e:
                            print(f"Error rendering page {page_num + 1}: {e}")
    
                # Navigation buttons for CBZ
                button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                button_box.set_halign(Gtk.Align.CENTER)
                button_box.set_border_width(10)
                main_box.pack_start(button_box, False, False, 0)
    
                # Only show prev/next buttons if more than one page
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
    
                # Render first page
                render_page(0)
    
                # Gamepad navigation
                def cbz_gamepad_handler(action: str):
                    if action == "activate" or action == "axis_right":
                        render_page(current_page[0] + 1)
                    elif action == "back":
                        close_viewer()
                    elif action == "axis_left":
                        render_page(current_page[0] - 1)
                    return False
                self._handle_gamepad_action = cbz_gamepad_handler
    
                # Close zipFile when the window is destoyed
                def clean_up_zip(*_):
                    try:
                        cbz_file.close()
                    except:
                        pass
    
                viewer.connect("destroy", clean_up_zip)
    
            except Exception as e:
                print(f"Error loading CBZ: {e}")
                error_label = Gtk.Label(label=f"Error loading CBZ: {e}")
                error_label.set_line_wrap(True)
                main_box.pack_start(error_label, True, True, 20)
    
        else:
            # Unknown file type
            error_label = Gtk.Label(label=f"Unsupported file type: {local_path}\nSupported: PDF, CBZ, JPG, PNG, GIF")
            error_label.set_line_wrap(True)
            main_box.pack_start(error_label, True, True, 20)
    
        def on_destroy(*_):
            # Clean up temp files
            import shutil
            for temp_file in temp_files:
                try:
                    if os.path.isdir(temp_file):
                        shutil.rmtree(temp_file)
                    elif os.path.exists(temp_file):
                        os.unlink(temp_file)
                except:
                    pass
            f_on_destroy()

        viewer.connect("destroy", on_destroy)
        viewer.show_all()

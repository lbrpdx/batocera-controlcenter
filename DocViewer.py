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
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import GtkLayerShell
from gi.repository import Gdk

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
        is_text = any(lower_path.endswith(ext) for ext in ['.txt', '.log', '.md', '.conf', '.cfg', '.ini', '.json', '.xml', '.yaml', '.yml'])
    
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
                        except:
                            pass
            except Exception as e:
                print(f"Error detecting file type: {e}")
    
        # print(f"File type: is_pdf={is_pdf}, is_image={is_image}, path={local_path}")
    
        # Create fullscreen window
        viewer = Gtk.Window()

        if self._is_wayland:
            GtkLayerShell.init_for_window(viewer)
            GtkLayerShell.set_layer(viewer, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_keyboard_interactivity(viewer, False)
            # screen
            display = Gdk.Display.get_default()
            monitor = display.get_monitor(0) # on batocera, 0 is the main screen, and 1 is the backglass (i'm not completly sure it is correct)
            GtkLayerShell.set_monitor(viewer, monitor)
            # screen size on wayland
            GtkLayerShell.set_anchor(viewer, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_anchor(viewer, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_anchor(viewer, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_anchor(viewer, GtkLayerShell.Edge.RIGHT, True)

        viewer.set_decorated(False)
        if not self._is_wayland:
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
        original_pixbuf = [None]  # Store original pixbuf for images/PDFs
        original_font_size = [14]  # Store original font size for text
        border_size = [0]

        def apply_initial_zoom():
            if original_pixbuf[0]:
                zoom_level[0] = get_initial_zoom(original_pixbuf[0], parent_window.get_screen())
                apply_zoom()

        def get_initial_zoom(pixbuf, screen):
            if pixbuf:
                zoomX = screen.get_width() / pixbuf.get_width()
                zoomY = (screen.get_height()-border_size[0]) / pixbuf.get_height()
                return min(zoomX, zoomY)
            return 1.0

        def apply_zoom():
            """Apply current zoom level to the active content"""
            if (is_image or is_pdf or is_cbz) and original_pixbuf[0]:
                # Zoom image/PDF/CBZ
                orig_width = original_pixbuf[0].get_width()
                orig_height = original_pixbuf[0].get_height()
                new_width = int(orig_width * zoom_level[0])
                new_height = int(orig_height * zoom_level[0])
                scaled_pixbuf = original_pixbuf[0].scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
                img.set_from_pixbuf(scaled_pixbuf)
            elif is_text:
                # Zoom text by changing font size
                new_font_size = max(6, int(original_font_size[0] * zoom_level[0]))  # Minimum 6pt font
                from gi.repository import Pango
                font_desc = Pango.FontDescription()
                font_desc.set_family("monospace")
                font_desc.set_size(new_font_size * Pango.SCALE)  # Pango.SCALE is the correct multiplier
                text_view.override_font(font_desc)
        
        def zoom_in():
            """Increase zoom level"""
            zoom_level[0] = min(zoom_level[0] * 1.2, 5.0)  # Max 500% zoom
            apply_zoom()  # Apply zoom for all content types
        
        def zoom_out():
            """Decrease zoom level"""
            zoom_level[0] = max(zoom_level[0] / 1.2, 0.2)  # Min 20% zoom
            apply_zoom()  # Apply zoom for all content types
        
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
    
        if is_image:
            # Image handling - do this first before PDF
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(local_path)
    
                # Store original pixbuf for zooming
                original_pixbuf[0] = pixbuf
    
                # Scale to fit screen initially
                screen_width = parent_window.get_screen().get_width()
                screen_height = parent_window.get_screen().get_height() - 100
    
                orig_width = pixbuf.get_width()
                orig_height = pixbuf.get_height()
    
                scale = min(screen_width / orig_width, screen_height / orig_height, 1.0)
                if scale < 1.0:
                    new_width = int(orig_width * scale)
                    new_height = int(orig_height * scale)
                    pixbuf = pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
                    # Update original pixbuf to the screen-fitted version for better zoom quality
                    original_pixbuf[0] = pixbuf
    
                img.set_from_pixbuf(pixbuf)
                scrolled.add(img)
    
                # Close button for images
                button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                button_box.set_halign(Gtk.Align.CENTER)
                button_box.set_border_width(10)
                main_box.pack_start(button_box, False, False, 0)
    
                close_btn = Gtk.Button.new_with_label(_("Close"))
                close_btn.get_style_context().add_class("cc-button")
                close_btn.connect("clicked", close_viewer)
                button_box.pack_start(close_btn, False, False, 0)
                x, psize = button_box.get_preferred_size()
                border_size[0] = psize.height+10*2 # border
    
                # Gamepad navigation for images
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
    
        elif is_pdf:
            # PDF handling with pdftoppm (now written in memory, not /tmp)
            try:
                from gi.repository import Gio
                
                # Add img to scrolled window for PDF
                scrolled.add(img)
    
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
    
                            # Store original pixbuf for zooming
                            original_pixbuf[0] = pixbuf
    
                            # Scale to fit screen initially
                            screen_width = parent_window.get_screen().get_width()
                            screen_height = parent_window.get_screen().get_height() - 100  # Leave room for buttons
    
                            orig_width = pixbuf.get_width()
                            orig_height = pixbuf.get_height()
    
                            scale = min(screen_width / orig_width, screen_height / orig_height, 1.0)
                            if scale < 1.0:
                                new_width = int(orig_width * scale)
                                new_height = int(orig_height * scale)
                                pixbuf = pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
                                # Update original pixbuf to the screen-fitted version for better zoom quality
                                original_pixbuf[0] = pixbuf
    
                            # Apply current zoom level
                            if zoom_level[0] != 1.0:
                                zoomed_width = int(pixbuf.get_width() * zoom_level[0])
                                zoomed_height = int(pixbuf.get_height() * zoom_level[0])
                                pixbuf = pixbuf.scale_simple(zoomed_width, zoomed_height, GdkPixbuf.InterpType.BILINEAR)
    
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
                x, psize = button_box.get_preferred_size()
                border_size[0] = psize.height+10*2+20*2 # border/padding
    
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
                    elif action == "axis_up":
                        zoom_in()
                        render_page(current_page[0])  # Re-render current page with new zoom
                    elif action == "axis_down":
                        zoom_out()
                        render_page(current_page[0])  # Re-render current page with new zoom
                    elif action in ("pan_up", "pan_down", "pan_left", "pan_right"):
                        pan_content(action)
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
                
                # Add img to scrolled window for CBZ
                scrolled.add(img)
    
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
    
                            # Store original pixbuf for zooming
                            original_pixbuf[0] = pixbuf
    
                            # Scale to fit screen initially
                            screen_width = parent_window.get_screen().get_width()
                            screen_height = parent_window.get_screen().get_height() - 100
                            orig_width = pixbuf.get_width()
                            orig_height = pixbuf.get_height()
    
                            scale = min(screen_width / orig_width, screen_height / orig_height, 1.0)
                            if scale < 1.0:
                                new_width = int(orig_width * scale)
                                new_height = int(orig_height * scale)
                                pixbuf = pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
                                # Update original pixbuf to the screen-fitted version for better zoom quality
                                original_pixbuf[0] = pixbuf
    
                            # Apply current zoom level
                            if zoom_level[0] != 1.0:
                                zoomed_width = int(pixbuf.get_width() * zoom_level[0])
                                zoomed_height = int(pixbuf.get_height() * zoom_level[0])
                                pixbuf = pixbuf.scale_simple(zoomed_width, zoomed_height, GdkPixbuf.InterpType.BILINEAR)
    
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
                x, psize = button_box.get_preferred_size()
                border_size[0] = psize.height+10*2+20*2 # border/padding
    
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
                    elif action == "axis_up":
                        zoom_in()
                        render_page(current_page[0])  # Re-render current page with new zoom
                    elif action == "axis_down":
                        zoom_out()
                        render_page(current_page[0])  # Re-render current page with new zoom
                    elif action in ("pan_up", "pan_down", "pan_left", "pan_right"):
                        pan_content(action)
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
    
        elif is_text:
            # Plain text file handling
            try:
                # Read the text file
                with open(local_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                
                # Set the text content
                text_buffer = text_view.get_buffer()
                text_buffer.set_text(content)
                
                # Set initial font and store original size for zooming
                from gi.repository import Pango
                font_desc = Pango.FontDescription()
                font_desc.set_family("monospace")
                font_desc.set_size(14 * Pango.SCALE)  # 14pt default
                text_view.override_font(font_desc)
                original_font_size[0] = 14  # Store original font size
                
                # Add text view to scrolled window
                scrolled.add(text_view)
                
                # Close button for text files
                button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                button_box.set_halign(Gtk.Align.CENTER)
                button_box.set_border_width(10)
                main_box.pack_start(button_box, False, False, 0)
                
                close_btn = Gtk.Button.new_with_label(_("Close"))
                close_btn.get_style_context().add_class("cc-button")
                close_btn.connect("clicked", close_viewer)
                button_box.pack_start(close_btn, False, False, 0)
                x, psize = button_box.get_preferred_size()
                border_size[0] = psize.height+10*2 # border
                
                # Gamepad navigation for text files
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
            # Unknown file type
            error_label = Gtk.Label(label=f"Unsupported file type: {local_path}\nSupported: PDF, CBZ, JPG, PNG, GIF, TXT")
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

        apply_initial_zoom()
        viewer.connect("destroy", on_destroy)
        viewer.show_all()

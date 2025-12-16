# Batocera Control Center

A flexible, XML-driven control panel for Batocera that provides an on-screen interface for system configuration and control. Works on both X11 and Wayland (Sway) with gamepad, touchscreen and keyboard support

![Batocera Control Center](controlcenter-screenshot.png)

## Features

- **Cross-platform**: Works on X11 and Wayland/Sway
- **Multiple input methods**: Keyboard, mouse, touchscreen, and gamepad (via evdev)
- **XML-driven UI**: Define your interface in a simple XML file
- **Live updates**: Display values update automatically from shell commands
- **Customizable styling**: GTK3 CSS for complete visual control
- **Auto-close**: Optional inactivity timeout
- **Modal dialogs**: Confirmation dialogs and choice popups
- **Flexible layout**: Horizontal and vertical groups with nested containers

## Quick Start

```bash
# Run with default configuration
./controlcenter.py

# Run with custom XML and CSS
./controlcenter.py /path/to/config.xml /path/to/style.css

# Run with 10-second inactivity timeout
./controlcenter.py controlcenter.xml style.css 10
```

## File Locations

When run without arguments, the application searches for configuration files in this priority order:

1. **User overrides**: `/userdata/system/configs/controlcenter/`
2. **System defaults**: `/usr/share/batocera/controlcenter/`
3. **Local directory**: Same directory as `controlcenter.py`

This allows users to customize their configuration without modifying system files.

## Command Line Parameters

```
./controlcenter.py [xml_path] [css_path] [auto_close_seconds]
```

- `xml_path`: Path to XML configuration file (default: auto-detected)
- `css_path`: Path to CSS stylesheet (default: auto-detected)
- `auto_close_seconds`: Inactivity timeout in seconds (default: 0 = never close)
  - Timer resets on any user interaction (navigation, button clicks)
  - Window also closes when losing focus (clicking outside)

## XML Configuration

### Basic Structure

```xml
<features>
  <hgroup display="Group Title">
    <vgroup>
      <feature display="Feature Name">
        <!-- Controls go here -->
      </feature>
    </vgroup>
  </hgroup>
</features>
```

### Universal Attributes

These attributes can be used on any element:

#### `id` - Element Identifier
Assigns a unique identifier to an element for conditional rendering.

```xml
<text id="public_ip" display="${curl ifconfig.me}" />
```

#### `if` - Conditional Rendering
Controls whether an element is rendered based on a condition.

**Supported conditions:**

1. **Check if another element is rendered:**
   ```xml
   <!-- Show only if element with id="cheevos" is rendered -->
   <text if="id(cheevos)" display="Achievements enabled!" />
   ```

2. **Check if another element is NOT rendered:**
   ```xml
   <!-- Show only if element with id="cheevos" is NOT rendered -->
   <text if="!id(cheevos)" display="Achievements disabled" />
   ```

3. **Check shell command output:**
   ```xml
   <!-- Show only if command returns non-empty string -->
   <feature if="${pgrep emulatorlauncher}" display="Game Running">
     <text display="A game is currently running" />
   </feature>
   ```

**Important Notes:**
- **Order matters**: Elements are processed top-to-bottom. An element with `if="id(xxx)"` must come AFTER the element with `id="xxx"`
- **Content-based IDs**: For `<text>` elements with commands, the ID is only registered if the command returns non-empty content
- **Dynamic IDs**: IDs can be registered/unregistered dynamically as content changes (useful with `refresh`)

**Examples:**

```xml
<!-- Conditional game info - only show if emulator is running -->
<hgroup display="Game Info" if="${pgrep emulatorlauncher}">
  <vgroup>
    <feature display="Current Game">
      <text display="${get-current-game}" />
    </feature>
  </vgroup>
</hgroup>

<!-- Show different messages based on achievement status -->
<text id="achievements" display="${check-achievements}" />
<text if="id(achievements)" display="Achievements Active" />
<text if="!id(achievements)" display="Achievements Disabled" />
```

### Container Elements

#### `<hgroup>` - Horizontal Group
Creates a titled section with a frame border.

```xml
<hgroup display="Sound Parameters">
  <!-- Content -->
</hgroup>
```

**Attributes:**
- `display`: Group title (optional, omit for no frame)

#### `<vgroup>` - Vertical Group
Creates a row of cells displayed horizontally. Can be used at root level or inside `<hgroup>`.

```xml
<vgroup>
  <feature display="Volume">
    <button display="Vol -" action="amixer set Master 5%-" />
    <text display="${amixer get Master | grep -o '[0-9]*%' | head -1}" />
    <button display="Vol +" action="amixer set Master 5%+" />
  </feature>
</vgroup>
```

**Special attribute:**
- `role="header"`: Makes the vgroup non-selectable and displays at the top (for status bars)
- `role="footer"`: Same as "header" but displays at the bottom

#### `<feature>` - Feature Row
A single row containing a label and controls.

```xml
<feature display="Power Mode">
  <text display="${cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor}" />
  <choice display="Performance" action="cpufreq-set -g performance" />
  <choice display="Powersave" action="cpufreq-set -g powersave" />
</feature>
```

**Attributes:**
- `display`: Label text
- `name`: Alternative to display (deprecated)

### Control Elements

#### `<button>` - Action Button
Executes a shell command when clicked.

```xml
<button display="Reboot" action="systemctl reboot" />
```

**Attributes:**
- `display`: Button label
- `action`: Shell command to execute
- `align`: Button alignment - `left`, `center` (default), or `right`

#### `<button_confirm>` - Confirmation Button
Shows a confirmation dialog before executing the action.

```xml
<button_confirm display="Kill Emulator" action="killall emulatorlauncher" />
```

**Attributes:**
- `display`: Button label (also used in confirmation message)
- `action`: Shell command to execute after confirmation

#### `<toggle>` - Toggle Switch
A switch that executes different commands for ON/OFF states.

```xml
<toggle
  value="${batocera-audio getSystemMute}"
  action_on="batocera-audio setSystemVolume mute"
  action_off="batocera-audio setSystemVolume unmute" />
```

**Attributes:**
- `value`: Command to get current state (returns "true"/"false", "1"/"0", "on"/"off", etc.)
- `display`: Command to get display value (optional, shows as label if provided)
- `action_on`: Command to execute when turning ON
- `action_off`: Command to execute when turning OFF
- `refresh`: Update interval in seconds (default: 0 = no refresh). Can be integer or float (e.g., `1`, `0.5`, `2.5`)
- `align`: Toggle alignment - `left`, `center` (default), or `right`

#### `<text>` - Display Text
Shows static text or dynamic output from a command.

```xml
<!-- Static text -->
<text display="Hello World" />

<!-- Dynamic text from command -->
<text display="${date +'%H:%M:%S'}" refresh="1" />

<!-- Command expansion in text -->
<text display="Load avg: ${cat /proc/loadavg | cut -d' ' -f1}%" />
```

**Attributes:**
- `display`: Text to display or `${command}` for dynamic content
- `refresh`: Update interval in seconds (default: 0 = no refresh). Can be integer or float (e.g., `1`, `0.5`, `2.5`)
- `align`: Text alignment - `left`, `center` (default), or `right`

**Command formats:**
- `${command}`: Single command, output replaces entire text
- `Text ${cmd1} more ${cmd2}`: Multiple commands embedded in text

#### `<choice>` - Choice Option
Creates a "Select" button that opens a popup with multiple choices.

```xml
<feature display="Power Mode">
  <text display="${cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor}" />
  <choice display="Performance" action="cpufreq-set -g performance" />
  <choice display="Powersave" action="cpufreq-set -g powersave" />
  <choice display="Ondemand" action="cpufreq-set -g ondemand" />
</feature>
```

**Attributes:**
- `display`: Option label in the popup
- `action`: Shell command to execute when selected

#### `<tab>` - Tab Navigation
Creates clickable tabs that switch between different content sections. Tabs must be defined in a feature, and each tab targets an `<hgroup>` by its `name` attribute.

```xml
<!-- Define tabs -->
<feature name="main_tabs" display="Navigation">
  <tab display="System" target="System" />
  <tab display="Games" target="Games" />
  <tab display="Network" target="Network" />
</feature>

<!-- Define tab content - each hgroup is a tab panel -->
<hgroup name="System" display="System Settings">
  <vgroup>
    <feature display="CPU">
      <text display="${cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d: -f2}" />
    </feature>
  </vgroup>
</hgroup>

<hgroup name="Games" display="Game Library">
  <vgroup>
    <feature display="Total Games">
      <text display="${find /userdata/roms -name '*.zip' | wc -l}" />
    </feature>
  </vgroup>
</hgroup>

<hgroup name="Network" display="Network Status">
  <vgroup>
    <feature display="IP Address">
      <text display="${hostname -I | awk '{print $1}'}" />
    </feature>
  </vgroup>
</hgroup>
```

**Attributes:**
- `display`: Tab label text (shown on the tab button)
- `target`: Name of the `<hgroup>` to show when this tab is selected (must match an hgroup's `name` attribute)

**Notes:**
- Tabs are defined in a `<feature>` element, typically at the top of your XML
- Each tab's `target` must match the `name` attribute of an `<hgroup>`
- Only one tab's content is visible at a time
- The first tab is selected by default
- Tab content is stacked vertically when multiple vgroups are present
- Tabs can be navigated with keyboard (Left/Right arrows) or gamepad (D-Pad Left/Right)
- Clicking a tab or pressing Enter/A button activates it

**Example with multiple content sections:**

```xml
<features>
  <!-- Tab navigation -->
  <feature name="tabs" display="Quick Access">
    <tab display="Audio" target="audio_settings" />
    <tab display="Video" target="video_settings" />
    <tab display="Controls" target="control_settings" />
  </feature>
  
  <!-- Audio tab content -->
  <hgroup name="audio_settings" display="Audio Settings">
    <vgroup>
      <feature display="Volume">
        <button display="Vol -" action="amixer set Master 5%-" />
        <text display="${amixer get Master | grep -o '[0-9]*%' | head -1}" />
        <button display="Vol +" action="amixer set Master 5%+" />
      </feature>
    </vgroup>
  </hgroup>
  
  <!-- Video tab content -->
  <hgroup name="video_settings" display="Video Settings">
    <vgroup>
      <feature display="Resolution">
        <text display="${xrandr | grep '*' | awk '{print $1}'}" />
      </feature>
    </vgroup>
  </hgroup>
  
  <!-- Controls tab content -->
  <hgroup name="control_settings" display="Control Settings">
    <vgroup>
      <feature display="Gamepad">
        <text display="${ls /dev/input/js* 2>/dev/null | wc -l} connected" />
      </feature>
    </vgroup>
  </hgroup>
</features>
```

#### `<img>` - Image Display
Shows an image from a file, URL, or command output.

```xml
<!-- Static image file -->
<img display="/usr/share/pixmaps/logo.png" width="100" height="100" />

<!-- Image from URL -->
<img display="http://example.com/image.png" width="200" />

<!-- Dynamic image path from command -->
<img display="${echo /path/to/image.png}" height="150" />
```

**Attributes:**
- `display`: File path, URL, or `${command}` that returns a path
- `width`: Image width in pixels (optional)
- `height`: Image height in pixels (optional)
- `refresh`: Update interval in seconds (default: 0 = no refresh). Can be integer or float (e.g., `1`, `0.5`, `2.5`)
- `align`: Image alignment - `left`, `center` (default), or `right`

**Notes:**
- If only width or height is specified, aspect ratio is preserved
- Supports common formats: PNG, JPEG, GIF, etc.

#### `<qrcode>` - QR Code Display
Generates and displays a QR code from text, URL, or command output. Requires the `qrcode` Python library (installed by default on Batocera).

```xml
<!-- Static QR code from URL -->
<qrcode display="https://batocera.org" width="150" height="150" />

<!-- Static QR code from text, with a dark background -->
<qrcode display="Hello World" bg="#141821" />

<!-- Dynamic QR code from command -->
<qrcode display="${echo https://example.com/status}" refresh="1" />
```

**Attributes:**
- `display`: Text, URL, or `${command}` that returns data to encode as QR code
- `width`: QR code width in pixels (optional, default: 200)
- `height`: QR code height in pixels (optional, default: 200)
- `refresh`: Update interval in seconds (default: 0 = no refresh). Can be integer or float (e.g., `1`, `0.5`, `2.5`)
- `align`: QR code alignment - `left`, `center` (default), or `right`
- `bg`: HTML hex code for the background of the QR code - foreground color will be contrasting white or black automatically

**Notes:**
- Requires `qrcode` Python library (already installed on Batocera)
- QR codes are generated as black on white background
- QR codes are always square - if only width or height is specified, both dimensions will use that value
- If neither width nor height is specified, defaults to 200x200 pixels
- Useful for sharing URLs, WiFi credentials, or dynamic status information

#### `<pdf>` - PDF/Image/CBZ Viewer Button
Creates a button that opens a fullscreen viewer for PDFs, images, or comic book archives (CBZ).

```xml
<!-- View a local PDF -->
<pdf display="View Manual" content="/userdata/roms/atari2600/manuals/manual.pdf" />

<!-- View a comic book archive -->
<pdf display="View Comic" content="/userdata/library/comic.cbz" />

<!-- View from URL -->
<pdf display="Online Doc" content="https://example.com/document.pdf" />

<!-- Dynamic path from command -->
<pdf display="Latest screenshot" content="${find /userdata/screenshots -name 'screenshot*.png' | head -1}" />
```

**Attributes:**
- `display`: Button label text (required)
- `content`: File path or URL to PDF/image (required). Can be `${command}` for dynamic paths
- `align`: Button alignment - `left`, `center` (default), or `right`

**Supported formats:**
- **PDF**: Requires `pdftoppm` and `pdfinfo` (from poppler-utils package)
  - Multi-page navigation with Previous/Next buttons
  - Gamepad: Left/Right or A button to navigate, B to close
- **CBZ**: Comic Book Archive (ZIP file containing images)
  - Multi-page navigation with Previous/Next buttons
  - Images sorted naturally by filename
  - Gamepad: Left/Right or A button to navigate, B to close
- **Images**: JPG, PNG, GIF, and other formats supported by GdkPixbuf
  - Gamepad: A or B button to close

**Notes:**
- Opens in fullscreen overlay window
- PDFs are rendered at 100 DPI for good enough quality
- CBZ files are extracted and images displayed in natural sort order
- Images are automatically scaled to fit screen
- Supports both local files and HTTP/HTTPS URLs
- Requires `pdftoppm` and `pdfinfo` commands for PDF support (usually pre-installed on Batocera)

### Refresh Behavior

By default, elements do not refresh automatically (`refresh="0"`). This reduces CPU usage for static content. For dynamic elements that need periodic updates, explicitly set a refresh interval in seconds (e.g., `refresh="1"` or `refresh="0.5"`).

**Elements that typically need refresh:**
- System information (CPU usage, memory, temperature)
- Time displays
- Running game information
- Volume levels
- Toggle states that can change externally
- Dynamic QR codes (e.g., for changing URLs or status)

**Elements that don't need refresh:**
- Static text and labels
- Buttons (they execute commands on click)
- Choice options
- Static images and QR codes

**Example:**
```xml
<!-- Static text - no refresh needed -->
<text display="System Settings" />

<!-- Dynamic CPU usage - refresh every second -->
<text display="${top -bn1 | grep 'Cpu(s)' | awk '{print $2}'}%" refresh="1" />

<!-- Fast refresh for time display (twice per second) -->
<text display="${date +'%H:%M:%S.%N' | cut -c1-12}" refresh="0.5" />

<!-- Volume that updates when buttons are clicked - needs refresh to show external changes -->
<text display="${batocera-audio getSystemVolume}%" refresh="1" />

<!-- Static QR code - no refresh needed -->
<qrcode display="https://batocera.org" width="150" height="150" />

<!-- Dynamic QR code that updates every 5 seconds -->
<qrcode display="${echo http://192.168.1.1:8080/status}" refresh="5" />

<!-- Slow refresh for less critical info (every 2.5 seconds) -->
<text display="${uptime -p}" refresh="2.5" />
```

### Layout Examples

#### Header Status Bar

```xml
<vgroup role="header">
  <feature name="Time">
    <text display="${date +'%H:%M:%S'}" refresh="1" />
  </feature>
  <feature display="CPU">
    <text display="${top -bn1 | grep 'Cpu(s)' | awk '{print $2}'}%" refresh="1" />
  </feature>
</vgroup>
```

#### Volume Control

```xml
<hgroup display="Sound Parameters">
  <vgroup>
    <feature>
      <button display="Vol -" action="batocera-audio setSystemVolume -5" />
      <text display="${batocera-audio getSystemVolume}%" refresh="1" />
      <button display="Vol +" action="batocera-audio setSystemVolume +5" />
    </feature>
    <feature display="Mute Sound">
      <toggle
        value="${batocera-audio getSystemMute}"
        action_on="batocera-audio setSystemVolume mute"
        action_off="batocera-audio setSystemVolume unmute"
        refresh="1" />
    </feature>
  </vgroup>
</hgroup>
```

#### Power Management with Choices

```xml
<hgroup display="Power Parameters">
  <vgroup>
    <feature display="Power Mode">
      <text display="${cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor}" refresh="1" />
      <choice display="Performance" action="cpufreq-set -g performance" />
      <choice display="Powersave" action="cpufreq-set -g powersave" />
      <choice display="Ondemand" action="cpufreq-set -g ondemand" />
    </feature>
  </vgroup>
</hgroup>
```

#### Network Information with QR Code

```xml
<hgroup display="Network Info">
  <vgroup>
    <feature display="ES Web Interface">
      <qrcode display="${echo http://$(hostname -s | awk '{print $1}'):1234}" width="150" height="150" refresh="5" />
    </feature>
  </vgroup>
</hgroup>
```

## CSS Styling

The interface uses GTK3 CSS for styling. All elements have CSS classes for customization.

### Available CSS Classes

```css
/* Main window */
.popup-root { }

/* Groups */
.group-frame { }
.group-title { }

/* Rows */
.vgroup-row { }
.vgroup-cell { }
.vgroup-cell-first { }

/* Text and labels */
.item-text { }
.value { }
.header { }

/* Buttons */
.cc-button { }
.cc-toggle { }
.cc-choice { }
.cc-button-confirm { }

/* Selection states */
.focused { }
.focused-cell { }
.choice-selected { }

/* Dialogs */
.confirm-dialog { }
.choice-option { }

/* Separators */
.section-separator { }
```

### Example Stylesheet

```css
/* Main window background */
.popup-root {
  background-color: rgba(20, 20, 20, 0.95);
  color: #ffffff;
}

/* Group frames */
.group-frame {
  border: 2px solid #444444;
  border-radius: 8px;
  background-color: rgba(30, 30, 30, 0.8);
  margin: 8px;
  padding: 8px;
}

.group-title {
  color: #00d4ff;
  font-size: 16px;
  font-weight: bold;
}

/* Buttons */
.cc-button {
  background-color: #333333;
  color: #ffffff;
  border: 2px solid #555555;
  border-radius: 6px;
  padding: 8px 16px;
  min-width: 80px;
}

.cc-button:hover {
  background-color: #444444;
  border-color: #00d4ff;
}

/* Selected button */
.focused-cell,
.choice-selected {
  background-color: #00a8cc !important;
  border-color: #00d4ff !important;
}

/* Toggle switches */
.cc-toggle {
  background-color: #555555;
  border: 2px solid #777777;
  border-radius: 20px;
  padding: 6px 20px;
  min-width: 60px;
}

.cc-toggle:checked {
  background-color: #00cc66;
  border-color: #00ff88;
}

/* Text values */
.value {
  color: #00d4ff;
  font-size: 14px;
  font-weight: bold;
}
```

## Input Controls

### Keyboard

- **Arrow Keys**: Navigate between controls
  - Up/Down: Move between rows
  - Left/Right: Move between controls in a row or vgroup
- **Enter/Space**: Activate selected control
- **Escape**: Close window or dialog

### Gamepad

The application uses evdev for gamepad support with exclusive access (prevents EmulationStation from receiving inputs while the control center is open).

- **D-Pad/Left Stick**: Navigate
- **A Button (South)**: Activate/Confirm
- **B Button (East) / Start**: Close/Cancel

Supported controllers: Xbox, PlayStation, and most standard gamepads.

### Mouse/Touch

- Click any button or control to activate
- Click outside the window to close (if focus-out is enabled)

## Window Behavior

### Sizing
- Width: 70% of screen width
- Height: Automatically sized to content, up to 70% of screen height
- Content is scrollable if it exceeds the maximum height

### Positioning
- **X11**: Centered horizontally, 20px from top
- **Wayland/Sway**: Centered by compositor

### Auto-Close
The window closes automatically in these situations:
1. **Inactivity timeout**: If configured (3rd command line parameter)
   - Timer resets on any user interaction
   - Does not close while dialogs are open
2. **Focus loss**: When clicking outside the window
   - Does not close when opening dialogs (choice/confirm)

### Wayland/Sway Specific
On Wayland/Sway, the window uses a special technique to ensure visibility:
- Briefly enters fullscreen mode on startup
- Returns to floating mode and centers
- This works around Sway's visibility handling for floating windows

## Development

### File Structure

```
batocera-controlcenter/
├── controlcenter.py     # Main entry point
├── ui_core.py           # UI rendering and window management
├── xml_utils.py         # XML parsing and validation
├── shell.py             # Shell command execution utilities
├── refresh.py           # Background refresh tasks
├── controlcenter.xml    # Default UI configuration
├── style.css            # Default stylesheet
└── README.md            # This file
```

### Adding New Control Types

1. Add XML validation in `xml_utils.py`
2. Add rendering logic in `ui_core.py` (in `_build_vgroup_row` or `_build_feature_row`)
3. Add CSS classes in `style.css`
4. Update this README with documentation

### Testing

```bash
# Test with custom config
./controlcenter.py test.xml test.css

# Test with auto-close
./controlcenter.py test.xml test.css 5

# Test on X11
DISPLAY=:0 ./controlcenter.py

# Test on Wayland
WAYLAND_DISPLAY=wayland-0 ./controlcenter.py
```

### Debugging

Enable debug output by checking the console. The application prints:
- Backend detection (X11/Wayland)
- Window sizing information
- CSS loading status
- Gamepad detection

## Requirements

- Python 3.7+
- GTK 3.0
- GLib
- python-evdev (for gamepad support)
- Wayland/Sway or X11

## License

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 3.

Copyright (c) 2025 lbrpdx for the Batocera team

## Contributing

Contributions are welcome! Please ensure:
- XML validation passes for new elements
- CSS classes are documented
- Code follows existing style
- README is updated for new features

## Troubleshooting

### Window doesn't appear on Wayland
- Ensure Sway is running: `echo $WAYLAND_DISPLAY`
- Check Sway logs: `journalctl -u sway`
- Try with decorations: The window uses decorated mode on Wayland by default

### Gamepad not working
- Check evdev is installed: `python3 -c "import evdev"`
- Verify gamepad is detected: `ls /dev/input/event*`
- Check permissions: User must have access to `/dev/input/event*`

### Commands not executing
- Test commands in terminal first
- Check command output: Commands should return clean text
- Escape special characters in XML: Use `&amp;` for `&`, `&lt;` for `<`, etc.

### CSS not loading
- Verify CSS file exists and is readable
- Check console for CSS parsing errors
- Ensure GTK 3.0 compatible syntax (no GTK 4 features)

### Window closes unexpectedly
- Check if auto-close timeout is set
- Verify focus-out behavior (clicking outside closes window)
- Ensure dialogs are not triggering premature closure

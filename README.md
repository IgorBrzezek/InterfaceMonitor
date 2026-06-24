# IMon — Interface Monitor TUI

**Version 0.0.6** by Igor Brzezek

A terminal-based network interface monitor that displays all network interfaces with their MAC addresses, IP configuration, gateway, DHCP/STATUS status, and real-time traffic rates in a curses TUI (Text User Interface).

![screenshot](https://img.shields.io/badge/platform-linux-lightgrey)

---

## Requirements

- Python 3.6+
- `psutil` library (`pip install psutil`)
- Linux (uses `/proc/net/route` and `ip` commands internally)
- A terminal that supports UTF-8 and at least 256 colors (recommended)

---

## Installation

```bash
pip install psutil
git clone https://github.com/igorbrzezek/imon.git
cd imon
python imon.py
```

---

## Usage

```
python imon.py                                        # run with imon.cfg in the same directory
python imon.py -c my.cfg                              # use a custom config file
python imon.py --int eth0                             # monitor only eth0
python imon.py --int eth0,wlan0,enp3s0                # monitor specific interfaces
python imon.py --color hgc                            # amber monochrome (Hercules style)
python imon.py --color mono                           # black & white mode
python imon.py --console                              # console mode with ANSI colors (no TUI)
python imon.py --version                              # print version and exit
```

On first launch the tool looks for `imon.cfg` in the script's directory. If no config file is found, all defaults are used.

---

## Interface

The screen is divided into three areas:

### 1. Top status bar
Shows the application name, version, your public IP address (right-aligned, fetched from `api.ipify.org`), and a **PAUSED** label (blinking) when updates are frozen. Toggle the public IP display with the `A` key.

### 2. Main panels

#### Interfaces panel
A table with the following columns:

| Column | Description |
|---|---|
| **Name** | Interface name (e.g. `eth0`, `wlan0`) |
| **MAC** | MAC address |
| **DS** | Address source: **D** = DHCP/dynamic, **S** = Static/manual, **N** = No IP |
| **IPv4/Mask** | IPv4 address with prefix length (e.g. `192.168.1.10/24`) |
| **Gateway** | Default gateway for this interface (only on the interface that holds the default route) |
| **IPv6** | IPv6 address with prefix length (if available) |

Row coloring reflects interface state:

| State | Color |
|---|---|
| Up with IP | Green |
| Up without IP | Yellow |
| Down without IP | Red |
| Down but still has IP (e.g. carrier loss) | Magenta |

#### Traffic panel
A table showing real-time bandwidth usage:

| Column | Description |
|---|---|
| **Interface** | Interface name |
| **Up** | Upload rate (bytes/s) |
| **Dn** | Download rate (bytes/s) |
| **Tx** | Total bytes sent since system start |
| **Rx** | Total bytes received since system start |
| **Pkt/s** | Combined packet rate (sent + received per second) |

Rate units auto-scale: B/s, KB/s, MB/s, GB/s.

### 3. Bottom status bar
Shows the hostname, number of detected interfaces, keyboard shortcut hints, and the current date/time.

---

## Console Mode (`--console`)

Instead of the curses TUI, you can run IMon in console mode:

```bash
python imon.py --console
```

This outputs the same interface and traffic data directly to the terminal with ANSI colors, no curses required. Press `q` or Ctrl+C to exit. The display refreshes at the same interval as the TUI mode.

---

## Color Modes

The terminal color scheme can be changed with the `--color` CLI flag or the `color_mode` config option.

| Mode | Description |
|---|---|
| `vga` (default) | Full 256-color palette — all interface states, traffic rates, ping bars, and popups are color-coded |
| `hgc` | Amber monochrome — emulates a Hercules Graphics Card amber-phosphor monitor. All text rendered in RGB(255,145,0) on black |
| `mono` | Black & white — all text rendered in white on black, no color distinctions |

In `hgc` and `mono` modes, all color settings in `[colors]` and `[ping]` are overridden; any individual color customizations are ignored while the mode is active.

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `H` | Toggle help popup |
| `I` | Toggle colors & states info popup |
| `N` | Open ping dialog |
| `T` | Open traceroute dialog |
| `P` | Pause / resume screen refresh |
| `A` | Toggle public IP display in top bar |
| `C` | Show credits (version, author, GitHub) |
| `Q` | Quit the application |
| `ESC` | Close any open popup |
| `F1` | Same as `H` |

### Ping dialog (`N`)

- **Tab** — switch focus between IP address and interval fields
- **Enter** — start pinging
- **Esc** — stop ping and close dialog
- Type digits and dots to enter an IP address
- The minimum interval for non-root users is 1.0 s; for root it is 0.01 s
- Displays current Min / Max / Avg latency and a color-coded progress bar
- Bar color thresholds (configurable):
  - **Green**: < 15 ms
  - **Yellow**: < 30 ms
  - **Orange**: < 60 ms
  - **Magenta**: < 100 ms
  - **Red**: < 500 ms
  - **Critical**: >= 500 ms

### Traceroute dialog (`T`)

- Type an IP address and press **Enter** to trace
- Uses the configurable command: `mtr` (default), `traceroute`, or `tracepath`
- **Esc** to close
- `mtr` is run in report mode (`-n -r -w -c 10`)
- Other tools use `-n` flag for numeric output

---

## Configuration

All settings are defined in `imon.cfg` (standard INI format). Below is a complete reference of every section and option.

### `[general]`

| Option | Type | Default | Description |
|---|---|---|---|
| `app_name` | string | `IMon` | Name shown in the top status bar |
| `version` | string | `0.0.6` | Version string displayed next to app name |
| `author` | string | `Igor Brzezek` | Author name shown in `--version` output |
| `refresh_interval_ms` | int | `1000` | TUI redraw interval in milliseconds |
| `color_mode` | string | `vga` | Color mode: `vga` (full color), `hgc` (amber monochrome), `mono` (black & white) |
| `background_char` | string | `" "` (space) | Character used to fill the screen background; useful with block chars like `░`, `▒`, `▓` |

### `[display]`

| Option | Type | Default | Description |
|---|---|---|---|
| `show_loopback` | bool | `false` | Whether to show the loopback (`lo`) interface |
| `show_public_ip` | bool | `true` | Whether to fetch and display the public IP in the top bar |
| `show_hostname` | bool | `true` | Show hostname in the bottom status bar |
| `show_datetime` | bool | `true` | Show current date/time in the bottom status bar |
| `show_iface_count` | bool | `true` | Show number of detected interfaces in the bottom bar |
| `datetime_format` | string | `%d-%m-%Y %H:%M:%S` | `strftime` format for date/time display |
| `interface_name_width` | int | `12` | Maximum width for the interface name column |
| `mac_width` | int | `17` | Maximum width for the MAC address column |
| `ipv4_width` | int | `18` | Maximum width for the IPv4 address column |
| `gateway_width` | int | `15` | Maximum width for the gateway column |
| `ipv6_width` | int | `25` | Maximum width for the IPv6 address column |
| `traffic_rate_width` | int | `10` | Maximum width for traffic rate columns (Up/Dn) |
| `traffic_total_width` | int | `10` | Maximum width for traffic total columns (Tx/Rx) |
| `interfaces` | string | `all` | Comma-separated list of interfaces to monitor (e.g. `eth0,wlan0`); `all` or empty = all |

Column widths are dynamically adjusted to fit the terminal; these values serve as upper bounds.

### `[colors]`

Each color option is a comma-separated pair `foreground,background`.

Available color names: `black`, `red`, `green`, `yellow`, `blue`, `magenta`, `cyan`, `white`, `orange`, `gray`, `grey`.

| Option | Default | Description |
|---|---|---|
| `background` | `black` | Screen background color |
| `status_bar_top` | `white,blue` | Top status bar text and background |
| `status_bar_bottom` | `white,blue` | Bottom status bar text and background |
| `border` | `cyan,black` | Window borders |
| `border_title` | `yellow,black` | Window title text |
| `text_normal` | `white,black` | Normal body text |
| `text_label` | `cyan,black` | Labels and column headers |
| `text_value` | `green,black` | Data values (rendered bold) |
| `text_warning` | `yellow,black` | Warning messages |
| `text_error` | `red,black` | Error messages |
| `highlight` | `black,cyan` | Highlighted (selected) items |
| `traffic_up` | `green,black` | Upload rate values (rendered bold) |
| `traffic_dn` | `yellow,black` | Download rate values (rendered bold) |
| `dhcp_color` | `green,black` | The **D** indicator in the DS column |
| `static_color` | `magenta,black` | The **S** indicator in the DS column |
| `state_down` | `red,black` | Interface: down, no IP |
| `state_up_noip` | `yellow,black` | Interface: up, no IP |
| `state_down_hasip` | `magenta,black` | Interface: down but still has IP address |
| `header_bg` | `blue` | Background color for table headers (single color, not a pair) |

The `orange` color is defined as RGB(1000, 600, 0) if the terminal supports `init_color`. If not, it falls back to yellow.

The `gray`/`grey` color is defined as RGB(500, 500, 500) on terminals with 256+ colors (xterm color 244). On basic terminals it falls back to white.

### `[keys]`

Single-character key bindings (case-insensitive).

| Option | Default | Description |
|---|---|---|
| `quit` | `q` | Quit the application |
| `help` | `h` | Toggle help popup |
| `toggle_pause` | `p` | Pause / resume interface refresh |
| `info` | `i` | Toggle colors & states info popup |
| `ping` | `n` | Open ping dialog |
| `traceroute` | `t` | Open traceroute dialog |
| `credits` | `c` | Show credits popup (version, author, GitHub) |
| `toggle_public_ip` | `a` | Toggle public IP display in the top bar |

### `[ping]`

Thresholds for the ping progress bar coloring.

| Option | Type | Default | Description |
|---|---|---|---|
| `bar_green_below` | float | `15.0` | Values below this threshold are green (ms) |
| `bar_yellow_below` | float | `30.0` | Values below this threshold are yellow (ms) |
| `bar_orange_below` | float | `60.0` | Values below this threshold are orange (ms) |
| `bar_magenta_below` | float | `100.0` | Values below this threshold are magenta (ms) |
| `bar_red_below` | float | `500.0` | Values below this threshold are red (ms); values at or above are critical |

Each threshold also has a corresponding color pair option:

| Option | Default |
|---|---|
| `color_green` | `green,black` |
| `color_yellow` | `yellow,black` |
| `color_orange` | `yellow,black` |
| `color_magenta` | `magenta,black` |
| `color_red` | `red,black` |
| `color_critical` | `red,black` |

### `[popup]`

| Option | Type | Default | Description |
|---|---|---|---|
| `background` | color pair | `white,black` | Background color inside popup windows |
| `border_color` | color pair | `cyan,black` | Popup border foreground and background |
| `border_double` | bool | `true` | Use double-line box drawing (`╔╗╚╝═║`) when true; single-line (`┌┐└┘─│`) when false |

### `[traceroute]`

| Option | Type | Default | Description |
|---|---|---|---|
| `command` | string | `mtr` | Traceroute program to use. Allowed values: `tracepath`, `traceroute`, `mtr` |

When `mtr` is used it is invoked with `-n -r -w -c 10` (numeric, report mode, wide output, 10 pings per hop).
Other tools are invoked with `-n` (numeric output only).

### `[network]`

| Option | Type | Default | Description |
|---|---|---|---|
| `traffic_interval` | float | `1.0` | How often to poll traffic counters (bytes/packets sent/recv) in seconds |
| `interface_interval` | float | `5.0` | How often to refresh the interface list, addresses, and gateway information in seconds |

---

## How it works

1. **Data collection** runs in a background thread (`InterfaceCollector`).
2. It uses `psutil.net_if_addrs()`, `psutil.net_if_stats()`, `psutil.net_io_counters()` to gather interface data.
3. Default gateways are obtained by parsing `ip route show default` and/or `/proc/net/route`.
4. DHCP detection checks running processes (`dhclient`, `dhcpcd`) and lease files in `/var/lib/dhcp/`.
5. Traffic rates are computed as deltas between successive polls divided by the elapsed time.
6. The curses TUI redraws at `refresh_interval_ms` intervals, reading from the shared state protected by a threading lock.
7. **Public IP** is fetched from `https://api.ipify.org` in a background thread on startup and refreshed every 120 seconds. It is displayed in the top-right corner of the status bar and can be toggled with the `A` key or disabled via `show_public_ip = false` in the config.

---

## License

MIT

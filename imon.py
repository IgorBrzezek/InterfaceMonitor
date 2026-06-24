#!/usr/bin/env python3
# IMon v0.0.5 by Igor Brzezek
"""Interface Monitor TUI — display all network interfaces with MAC, IP,
gateway, DHCP/STATIC and real-time traffic rates.

Usage:
  python IMon.py               # run with imon.cfg in same directory
  python IMon.py -c my.cfg     # custom config
  python IMon.py --version     # show version

Requirements:
  pip install psutil
"""

import argparse
import configparser
import curses
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


# ---------------------------------------------------------------------------
# Info data
# ---------------------------------------------------------------------------
SCRIPT_AUTHOR = "Igor Brzezek"
SCRIPT_VERSION = "0.0.5"
SCRIPT_GITHUB = "https://github.com/igorbrzezek"


# ---------------------------------------------------------------------------
# Unicode box-drawing (double line)
# ---------------------------------------------------------------------------
BOX_TL = "\u2554"
BOX_TR = "\u2557"
BOX_BL = "\u255A"
BOX_BR = "\u255D"
BOX_H  = "\u2550"
BOX_V  = "\u2551"
BOX_LT = "\u2560"
BOX_RT = "\u2563"

BOX_S_TL = "\u250C"
BOX_S_TR = "\u2510"
BOX_S_BL = "\u2514"
BOX_S_BR = "\u2518"
BOX_S_H  = "\u2500"
BOX_S_V  = "\u2502"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024*1024):.2f} MB"
    else:
        return f"{n / (1024*1024*1024):.2f} GB"


def format_rate(bps: float) -> str:
    if bps < 1024:
        return f"{bps:.0f} B/s"
    elif bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    elif bps < 1024 * 1024 * 1024:
        return f"{bps / (1024*1024):.2f} MB/s"
    else:
        return f"{bps / (1024*1024*1024):.2f} GB/s"


def netmask_to_prefix(mask: str) -> int:
    try:
        parts = mask.split(".")
        if len(parts) == 4:
            binary = sum(int(p) << (24 - 8 * i) for i, p in enumerate(parts))
            return bin(binary).count("1")
    except ValueError:
        pass
    return 0


def format_pkt_rate(n: float) -> str:
    if n < 1000:
        return f"{n:.0f}"
    elif n < 1000000:
        return f"{n / 1000:.1f}K"
    else:
        return f"{n / 1000000:.2f}M"


def format_datetime(fmt: str = "%d-%m-%Y %H:%M:%S") -> str:
    return time.strftime(fmt)


# ---------------------------------------------------------------------------
# Color mapping (same scheme as OVPNMonitor)
# ---------------------------------------------------------------------------

# Extended colour numbers (resolved at runtime in _init_extended_colors)
COLOR_ORANGE = 16
COLOR_AMBER  = 208  # xterm-256color #ff8700 = RGB(255,135,0), closest to amber; init_color tries to refine to exact 255,145,0
COLOR_GRAY   = 244  # xterm-256color medium gray (safe fallback)

HGC_FG = curses.COLOR_YELLOW  # 3 – ANSI yellow, maps to bright 11 via A_BOLD

COLOR_MAP = {
    "black":   curses.COLOR_BLACK,
    "red":     curses.COLOR_RED,
    "green":   curses.COLOR_GREEN,
    "yellow":  curses.COLOR_YELLOW,
    "blue":    curses.COLOR_BLUE,
    "magenta": curses.COLOR_MAGENTA,
    "cyan":    curses.COLOR_CYAN,
    "white":   curses.COLOR_WHITE,
    "orange":  COLOR_ORANGE,
    "gray":    COLOR_GRAY,
    "grey":    COLOR_GRAY,
}


def parse_color_pair(value: str) -> Tuple[int, int]:
    parts = [p.strip().lower() for p in value.split(",")]
    if len(parts) != 2:
        return (curses.COLOR_WHITE, curses.COLOR_BLACK)
    fg = COLOR_MAP.get(parts[0], curses.COLOR_WHITE)
    bg = COLOR_MAP.get(parts[1], curses.COLOR_BLACK)
    return (fg, bg)


# ---------------------------------------------------------------------------
# Config dataclasses & loader
# ---------------------------------------------------------------------------

@dataclass
class ColorConfig:
    background: int = curses.COLOR_BLACK
    status_bar_top: Tuple[int, int] = (curses.COLOR_WHITE, curses.COLOR_BLUE)
    status_bar_bottom: Tuple[int, int] = (curses.COLOR_WHITE, curses.COLOR_BLUE)
    border: Tuple[int, int] = (curses.COLOR_CYAN, curses.COLOR_BLACK)
    border_title: Tuple[int, int] = (curses.COLOR_YELLOW, curses.COLOR_BLACK)
    text_normal: Tuple[int, int] = (curses.COLOR_WHITE, curses.COLOR_BLACK)
    text_label: Tuple[int, int] = (curses.COLOR_CYAN, curses.COLOR_BLACK)
    text_value: Tuple[int, int] = (curses.COLOR_GREEN, curses.COLOR_BLACK)
    text_warning: Tuple[int, int] = (curses.COLOR_YELLOW, curses.COLOR_BLACK)
    text_error: Tuple[int, int] = (curses.COLOR_RED, curses.COLOR_BLACK)
    highlight: Tuple[int, int] = (curses.COLOR_BLACK, curses.COLOR_CYAN)
    traffic_up: Tuple[int, int] = (curses.COLOR_GREEN, curses.COLOR_BLACK)
    traffic_dn: Tuple[int, int] = (curses.COLOR_YELLOW, curses.COLOR_BLACK)
    dhcp_color: Tuple[int, int] = (curses.COLOR_GREEN, curses.COLOR_BLACK)
    static_color: Tuple[int, int] = (curses.COLOR_MAGENTA, curses.COLOR_BLACK)
    state_down: Tuple[int, int] = (curses.COLOR_RED, curses.COLOR_BLACK)
    state_up_noip: Tuple[int, int] = (curses.COLOR_YELLOW, curses.COLOR_BLACK)
    state_down_hasip: Tuple[int, int] = (curses.COLOR_MAGENTA, curses.COLOR_BLACK)
    header_bg: int = curses.COLOR_BLUE


@dataclass
class PingThresholds:
    green: float = 15.0
    yellow: float = 30.0
    orange: float = 60.0
    magenta: float = 100.0
    red: float = 500.0
    color_green: Tuple[int, int] = (curses.COLOR_GREEN, curses.COLOR_BLACK)
    color_yellow: Tuple[int, int] = (curses.COLOR_YELLOW, curses.COLOR_BLACK)
    color_orange: Tuple[int, int] = (curses.COLOR_YELLOW, curses.COLOR_BLACK)
    color_magenta: Tuple[int, int] = (curses.COLOR_MAGENTA, curses.COLOR_BLACK)
    color_red: Tuple[int, int] = (curses.COLOR_RED, curses.COLOR_BLACK)
    color_critical: Tuple[int, int] = (curses.COLOR_RED, curses.COLOR_BLACK)


@dataclass
class KeyConfig:
    quit: str = "q"
    help: str = "h"
    toggle_pause: str = "p"
    info: str = "i"
    ping: str = "n"
    traceroute: str = "t"
    credits: str = "c"
    toggle_public_ip: str = "a"


@dataclass
class DisplayConfig:
    show_loopback: bool = False
    show_public_ip: bool = True
    show_hostname: bool = True
    show_datetime: bool = True
    show_iface_count: bool = True
    datetime_format: str = "%d-%m-%Y %H:%M:%S"
    interface_name_width: int = 12
    mac_width: int = 17
    ipv4_width: int = 18
    gateway_width: int = 15
    ipv6_width: int = 25
    traffic_rate_width: int = 10
    traffic_total_width: int = 10
    interface_filter: set = field(default_factory=set)


@dataclass
class NetworkConfig:
    traffic_interval: float = 1.0
    interface_interval: float = 5.0


# Allowed traceroute programs
TRACEROUTE_ALLOWED = {"tracepath", "traceroute", "mtr"}


@dataclass
class PopupConfig:
    bg: Tuple[int, int] = (curses.COLOR_WHITE, curses.COLOR_BLACK)
    border_fg: int = curses.COLOR_CYAN
    border_bg: int = curses.COLOR_BLACK
    border_double: bool = True


@dataclass
class Config:
    app_name: str = "IMon"
    version: str = SCRIPT_VERSION
    author: str = SCRIPT_AUTHOR
    refresh_interval_ms: int = 1000
    background_char: str = " "

    colors: ColorConfig = field(default_factory=ColorConfig)
    keys: KeyConfig = field(default_factory=KeyConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    ping: PingThresholds = field(default_factory=PingThresholds)
    popup: PopupConfig = field(default_factory=PopupConfig)
    color_mode: str = "vga"
    traceroute_cmd: str = "mtr"
    color_pairs: Dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Curses color-pair registration
# ---------------------------------------------------------------------------

_pair_counter = 1
_mda_suppress = False


def register_color_pair(fg: int, bg: int, force: bool = False) -> int:
    global _pair_counter
    if _mda_suppress and not force:
        return 0
    pair_id = _pair_counter
    _pair_counter += 1
    try:
        curses.init_pair(pair_id, fg, bg)
    except curses.error:
        pass
    return pair_id


def _resolve_gray():
    """Return a usable gray colour number for this terminal."""
    if curses.COLORS >= 256:
        try:
            curses.init_color(COLOR_GRAY, 500, 500, 500)
        except curses.error:
            pass
        return COLOR_GRAY  # 244 is medium gray in xterm-256color
    return curses.COLOR_WHITE


def _init_extended_colors():
    """Try to initialise custom colours (orange, HGC amber tones)."""
    try:
        curses.init_color(16, 1000, 600, 0)
    except curses.error:
        COLOR_MAP["orange"] = curses.COLOR_YELLOW
    # Best-effort: try to make ANSI yellow (3) → dark amber and
    # bright yellow (11) → bright amber.  If init_color fails,
    # the fallback yellow is still visible.
    for idx, r, g, b in (
        (3,  780, 450, 15),   # RGB(199,115,4)  → 0-1000
        (11, 988, 560, 0),    # RGB(252,143,0)  → 0-1000
    ):
        try:
            curses.init_color(idx, r, g, b)
        except curses.error:
            pass


AMBER_FG = curses.COLOR_YELLOW


def init_colors(cfg: Config) -> None:
    global _pair_counter
    _pair_counter = 1
    cc = cfg.colors
    pairs = {}

    _init_extended_colors()
    _apply_color_mode(cfg)
    resolved_gray = _resolve_gray()

    pairs["background"] = register_color_pair(
        HGC_FG if cfg.color_mode == "hgc" else curses.COLOR_WHITE,
        cc.background,
        force=cfg.color_mode != "mda")

    color_fields = [
        "status_bar_top", "status_bar_bottom", "border", "border_title",
        "text_normal", "text_label", "text_value", "text_warning",
        "text_error", "highlight", "traffic_up", "traffic_dn",
        "dhcp_color", "static_color",
        "state_down", "state_up_noip", "state_down_hasip",
    ]
    for name in color_fields:
        fg, bg = getattr(cc, name)
        pairs[name] = register_color_pair(fg, bg)

    if cfg.color_mode == "hgc":
        pairs["header_bg"] = register_color_pair(HGC_FG, cc.header_bg)
    else:
        pairs["header_bg"] = register_color_pair(curses.COLOR_WHITE, cc.header_bg)

    pc2 = cfg.popup
    # Resolve gray in popup colours
    def _rg(c):
        return resolved_gray if c == COLOR_GRAY else c
    pc2.bg = (_rg(pc2.bg[0]), _rg(pc2.bg[1]))
    pc2.border_fg = _rg(pc2.border_fg)
    pc2.border_bg = _rg(pc2.border_bg)
    popup_bg_color = pc2.bg[1]
    pairs["popup_bg"] = register_color_pair(*pc2.bg)
    pairs["popup_border"] = register_color_pair(pc2.border_fg, pc2.border_bg)
    pairs["popup_text_normal"] = register_color_pair(cc.text_normal[0], popup_bg_color)
    pairs["popup_text_label"] = register_color_pair(cc.text_label[0], popup_bg_color)
    pairs["popup_text_value"] = register_color_pair(cc.text_value[0], popup_bg_color)
    pairs["popup_text_warning"] = register_color_pair(cc.text_warning[0], popup_bg_color)
    pairs["popup_text_error"] = register_color_pair(cc.text_error[0], popup_bg_color)
    pairs["popup_border_title"] = register_color_pair(cc.border_title[0], popup_bg_color)

    pc = cfg.ping
    ping_fields = [
        ("ping_green", pc.color_green),
        ("ping_yellow", pc.color_yellow),
        ("ping_orange", (pc.color_orange[0], pc.color_orange[1])),
        ("ping_magenta", pc.color_magenta),
        ("ping_red", pc.color_red),
        ("ping_critical", pc.color_critical),
    ]
    for name, (fg, bg) in ping_fields:
        pairs[name] = register_color_pair(fg, bg)

    cfg.color_pairs = pairs


def get_attr(cfg: Config, name: str, bold: bool = False) -> int:
    if _mda_suppress:
        bold = False
    pair_id = cfg.color_pairs.get(name, 0)
    attr = curses.color_pair(pair_id)
    if bold:
        attr |= curses.A_BOLD
    return attr


# ---------------------------------------------------------------------------
# Config file parser
# ---------------------------------------------------------------------------

def load_config(path: Optional[str] = None) -> Config:
    cfg = Config()
    if not path or not os.path.isfile(path):
        return cfg

    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")

    if cp.has_section("general"):
        g = cp["general"]
        cfg.app_name = g.get("app_name", cfg.app_name)
        cfg.version = g.get("version", cfg.version)
        cfg.author = g.get("author", cfg.author)
        cfg.refresh_interval_ms = g.getint("refresh_interval_ms", cfg.refresh_interval_ms)
        bg = g.get("background_char", cfg.background_char)
        cfg.background_char = bg if bg else " "
        if "color_mode" in g:
            val = g["color_mode"].strip().lower()
            if val in ("vga", "hgc", "mono"):
                cfg.color_mode = val

    if cp.has_section("colors"):
        c = cp["colors"]
        cc = cfg.colors
        if "background" in c:
            cc.background = COLOR_MAP.get(c["background"].strip().lower(), curses.COLOR_BLACK)
        for fn in ["status_bar_top", "status_bar_bottom", "border", "border_title",
                    "text_normal", "text_label", "text_value", "text_warning",
                    "text_error", "highlight", "traffic_up", "traffic_dn",
                    "dhcp_color", "static_color",
                    "state_down", "state_up_noip", "state_down_hasip"]:
            if fn in c:
                setattr(cc, fn, parse_color_pair(c[fn]))
        if "header_bg" in c:
            cc.header_bg = COLOR_MAP.get(c["header_bg"].strip().lower(), curses.COLOR_BLUE)

    if cp.has_section("display"):
        d = cp["display"]
        dc = cfg.display
        if "show_loopback" in d:
            dc.show_loopback = d.getboolean("show_loopback")
        if "show_public_ip" in d:
            dc.show_public_ip = d.getboolean("show_public_ip")
        if "show_hostname" in d:
            dc.show_hostname = d.getboolean("show_hostname")
        if "show_datetime" in d:
            dc.show_datetime = d.getboolean("show_datetime")
        if "show_iface_count" in d:
            dc.show_iface_count = d.getboolean("show_iface_count")
        dc.datetime_format = d.get("datetime_format", raw=True, fallback=dc.datetime_format)
        dc.interface_name_width = d.getint("interface_name_width", dc.interface_name_width)
        dc.mac_width = d.getint("mac_width", dc.mac_width)
        dc.ipv4_width = d.getint("ipv4_width", dc.ipv4_width)
        dc.gateway_width = d.getint("gateway_width", dc.gateway_width)
        dc.ipv6_width = d.getint("ipv6_width", dc.ipv6_width)
        dc.traffic_rate_width = d.getint("traffic_rate_width", dc.traffic_rate_width)
        dc.traffic_total_width = d.getint("traffic_total_width", dc.traffic_total_width)
        if "interfaces" in d:
            raw = d.get("interfaces", raw=True).strip().lower()
            if raw and raw != "all":
                dc.interface_filter = {x.strip() for x in raw.split(",") if x.strip()}
            else:
                dc.interface_filter = set()

    if cp.has_section("keys"):
        k = cp["keys"]
        kc = cfg.keys
        kc.quit = k.get("quit", kc.quit)
        kc.help = k.get("help", kc.help)
        kc.toggle_pause = k.get("toggle_pause", kc.toggle_pause)
        kc.info = k.get("info", kc.info)
        kc.ping = k.get("ping", kc.ping)
        kc.traceroute = k.get("traceroute", kc.traceroute)
        kc.credits = k.get("credits", kc.credits)
        kc.toggle_public_ip = k.get("toggle_public_ip", kc.toggle_public_ip)

    if cp.has_section("network"):
        n = cp["network"]
        nc = cfg.network
        nc.traffic_interval = n.getfloat("traffic_interval", nc.traffic_interval)
        nc.interface_interval = n.getfloat("interface_interval", nc.interface_interval)

    if cp.has_section("ping"):
        p = cp["ping"]
        pt = cfg.ping
        pt.green = p.getfloat("bar_green_below", pt.green)
        pt.yellow = p.getfloat("bar_yellow_below", pt.yellow)
        pt.orange = p.getfloat("bar_orange_below", pt.orange)
        pt.magenta = p.getfloat("bar_magenta_below", pt.magenta)
        pt.red = p.getfloat("bar_red_below", pt.red)
        for color_key in ("color_green", "color_yellow", "color_orange",
                          "color_magenta", "color_red", "color_critical"):
            if color_key in p:
                setattr(pt, color_key, parse_color_pair(p[color_key]))

    if cp.has_section("traceroute"):
        tr = cp["traceroute"]
        if "command" in tr:
            val = tr["command"].strip().lower()
            if val in TRACEROUTE_ALLOWED:
                cfg.traceroute_cmd = val

    if cp.has_section("popup"):
        p = cp["popup"]
        pc2 = cfg.popup
        if "background" in p:
            pc2.bg = parse_color_pair(p["background"])
        if "border_color" in p:
            parts = [x.strip().lower() for x in p["border_color"].split(",")]
            if len(parts) >= 1:
                pc2.border_fg = COLOR_MAP.get(parts[0], curses.COLOR_CYAN)
            if len(parts) >= 2:
                pc2.border_bg = COLOR_MAP.get(parts[1], curses.COLOR_BLACK)
        if "border_double" in p:
            pc2.border_double = p.getboolean("border_double")

    return cfg


def _apply_color_mode(cfg: Config) -> None:
    if cfg.color_mode == "vga":
        return

    global _mda_suppress
    black = curses.COLOR_BLACK

    if cfg.color_mode == "mda":
        _mda_suppress = True
        fg = -1
        try:
            curses.putp(b"\033[38;2;255;145;0m")
            curses.putp(b"\033[48;2;0;0;0m")
        except curses.error:
            pass
    elif cfg.color_mode == "hgc":
        _mda_suppress = False
        fg = HGC_FG  # 3 = ANSI yellow; A_BOLD maps to bright yellow (11)
        # Falls through to the mono path below
    else:
        fg = curses.COLOR_WHITE

    # mono path (also reused as generic fallback)
    cc = cfg.colors
    for fn in ["status_bar_top", "status_bar_bottom", "border", "border_title",
                "text_normal", "text_label", "text_value", "text_warning",
                "text_error", "highlight", "traffic_up", "traffic_dn",
                "dhcp_color", "static_color",
                "state_down", "state_up_noip", "state_down_hasip"]:
        setattr(cc, fn, (fg, black))
    cc.background = black
    cc.header_bg = black

    pc = cfg.ping
    for fn in ["color_green", "color_yellow", "color_orange",
                "color_magenta", "color_red", "color_critical"]:
        setattr(pc, fn, (fg, black))

    pc2 = cfg.popup
    pc2.bg = (fg, black)
    pc2.border_fg = fg
    pc2.border_bg = black


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class InterfaceInfo:
    name: str = ""
    mac: str = ""
    ipv4: str = ""
    prefix: int = 0
    gateway4: str = ""
    ipv6: str = ""
    ipv6_prefix: int = 0
    gateway6: str = ""
    is_dhcp: bool = True
    is_up: bool = False
    is_loopback: bool = False

    bytes_sent: int = 0
    bytes_recv: int = 0
    bytes_sent_rate: float = 0.0
    bytes_recv_rate: float = 0.0

    packets_sent: int = 0
    packets_recv: int = 0
    packets_sent_rate: float = 0.0
    packets_recv_rate: float = 0.0

    _prev_bytes_sent: int = 0
    _prev_bytes_recv: int = 0
    _prev_packets_sent: int = 0
    _prev_packets_recv: int = 0
    _prev_time: float = 0.0


@dataclass
class MonitorState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    interfaces: Dict[str, InterfaceInfo] = field(default_factory=dict)
    paused: bool = False
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Network data collection
# ---------------------------------------------------------------------------

def _get_default_gateway4() -> str:
    """Get the default IPv4 gateway from the routing table."""
    try:
        out = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=3,
        )
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "default":
                return parts[2]
    except (subprocess.TimeoutExpired, OSError):
        pass
    try:
        with open("/proc/net/route") as f:
            for line in f:
                fields = line.strip().split()
                if len(fields) >= 8 and fields[1] == "00000000":
                    gw_hex = fields[2]
                    return ".".join(str(int(gw_hex[i:i+2], 16)) for i in range(6, -1, -2))
    except OSError:
        pass
    return ""


def _get_default_gateway6() -> str:
    try:
        out = subprocess.run(
            ["ip", "-6", "route", "show", "default"],
            capture_output=True, text=True, timeout=3,
        )
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "default":
                return parts[2]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _default_gateway_iface() -> str:
    """Return the interface name used for the default IPv4 route."""
    try:
        out = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=3,
        )
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0] == "default":
                return parts[4]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _default_gateway6_iface() -> str:
    try:
        out = subprocess.run(
            ["ip", "-6", "route", "show", "default"],
            capture_output=True, text=True, timeout=3,
        )
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0] == "default":
                return parts[4]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _is_dhcp_interface(iface: str) -> bool:
    try:
        out = subprocess.run(
            ["ps", "-eo", "cmd"],
            capture_output=True, text=True, timeout=3,
        )
        for line in out.stdout.splitlines():
            if "dhclient" in line and iface in line:
                return True
            if "dhcpcd" in line and iface in line:
                return True
    except OSError:
        pass
    if os.path.isdir("/var/lib/dhcp"):
        try:
            for f in os.listdir("/var/lib/dhcp"):
                if iface in f and "lease" in f.lower():
                    return True
        except OSError:
            pass
    return False


def collect_interfaces(state: MonitorState, show_loopback: bool = False) -> None:
    if not HAS_PSUTIL:
        return

    now = time.time()
    gw4 = _get_default_gateway4()
    gw6 = _get_default_gateway6()
    def_iface = _default_gateway_iface()
    def_iface6 = _default_gateway6_iface()

    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        io = psutil.net_io_counters(pernic=True)
    except (psutil.Error, OSError):
        return

    with state.lock:
        for iface in stats:
            iface_lower = iface.lower()
            if iface_lower == "lo" and not show_loopback:
                state.interfaces.pop(iface, None)
                continue

            name = iface
            is_up = stats[iface].isup
            is_loop = (iface_lower == "lo")
            mac = ""
            ipv4 = ""
            prefix = 0
            ipv6 = ""
            ipv6_prefix = 0
            dhcp = True

            if iface in addrs:
                for addr in addrs[iface]:
                    fam = addr.family
                    if fam in (socket.AF_PACKET, getattr(psutil, 'AF_LINK', -1)):
                        mac = addr.address
                    elif fam == socket.AF_INET:
                        ipv4 = addr.address
                        if addr.netmask:
                            prefix = netmask_to_prefix(addr.netmask)
                    elif fam == socket.AF_INET6:
                        ipv6 = addr.address.split("%")[0]
                        if addr.netmask:
                            try:
                                ipv6_prefix = int(addr.netmask)
                            except ValueError:
                                ipv6_prefix = 64

            iface_gw4 = gw4 if def_iface == iface else ""
            iface_gw6 = gw6 if def_iface6 == iface else ""
            dhcp = _is_dhcp_interface(iface)

            prev = state.interfaces.get(iface)
            bs = br = ps = pr = 0
            if iface in io:
                c = io[iface]
                bs = c.bytes_sent
                br = c.bytes_recv
                ps = c.packets_sent
                pr = c.packets_recv

            rate_s = rate_r = rate_ps = rate_pr = 0.0
            if prev and prev._prev_time > 0:
                dt = now - prev._prev_time
                if dt > 0:
                    rate_s = max(0, (bs - prev._prev_bytes_sent) / dt)
                    rate_r = max(0, (br - prev._prev_bytes_recv) / dt)
                    rate_ps = max(0, (ps - prev._prev_packets_sent) / dt)
                    rate_pr = max(0, (pr - prev._prev_packets_recv) / dt)

            state.interfaces[iface] = InterfaceInfo(
                name=name, mac=mac,
                ipv4=ipv4, prefix=prefix, gateway4=iface_gw4,
                ipv6=ipv6, ipv6_prefix=ipv6_prefix, gateway6=iface_gw6,
                is_dhcp=dhcp, is_up=is_up, is_loopback=is_loop,
                bytes_sent=bs, bytes_recv=br,
                bytes_sent_rate=rate_s, bytes_recv_rate=rate_r,
                packets_sent=ps, packets_recv=pr,
                packets_sent_rate=rate_ps, packets_recv_rate=rate_pr,
                _prev_bytes_sent=bs, _prev_bytes_recv=br,
                _prev_packets_sent=ps, _prev_packets_recv=pr,
                _prev_time=now,
            )


# ---------------------------------------------------------------------------
# Collector thread
# ---------------------------------------------------------------------------

class InterfaceCollector(threading.Thread):
    def __init__(self, state: MonitorState, interval: float, show_loopback: bool = False):
        super().__init__(daemon=True, name="InterfaceCollector")
        self.state = state
        self.interval = interval
        self.show_loopback = show_loopback
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            if not self.state.paused:
                try:
                    collect_interfaces(self.state, self.show_loopback)
                except Exception as e:
                    with self.state.lock:
                        self.state.errors.append(str(e))
            deadline = time.time() + self.interval
            while time.time() < deadline:
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)


# ---------------------------------------------------------------------------
# Curses TUI
# ---------------------------------------------------------------------------

def safe_addstr(win, y: int, x: int, text: str, attr: int = 0):
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        mx = min(len(text), w - x - 1)
        if mx > 0:
            win.addnstr(y, x, text, mx, attr)
    except curses.error:
        pass


def draw_double_box(win, y: int, x: int, h: int, w: int,
                    title: str = "", attr: int = 0,
                    title_attr: int = 0, fill_attr: int = 0,
                    double: bool = True):
    if h < 2 or w < 4:
        return
    if fill_attr:
        fill = " " * (w - 2)
        for r in range(1, h - 1):
            safe_addstr(win, y + r, x + 1, fill, fill_attr)
    tl, tr, bl, br, hh, vv = (
        (BOX_TL, BOX_TR, BOX_BL, BOX_BR, BOX_H, BOX_V) if double
        else (BOX_S_TL, BOX_S_TR, BOX_S_BL, BOX_S_BR, BOX_S_H, BOX_S_V)
    )
    safe_addstr(win, y, x, tl, attr)
    safe_addstr(win, y, x + 1, hh * (w - 2), attr)
    safe_addstr(win, y, x + w - 1, tr, attr)
    for r in range(1, h - 1):
        safe_addstr(win, y + r, x, vv, attr)
        safe_addstr(win, y + r, x + w - 1, vv, attr)
    safe_addstr(win, y + h - 1, x, bl, attr)
    safe_addstr(win, y + h - 1, x + 1, hh * (w - 2), attr)
    safe_addstr(win, y + h - 1, x + w - 1, br, attr)
    if title and w > len(title) + 4:
        t = f" {title} "
        tx = x + (w - len(t)) // 2
        safe_addstr(win, y, tx, t, title_attr if title_attr else attr)


class UIManager:
    def __init__(self, stdscr, cfg: Config, state: MonitorState):
        self.stdscr = stdscr
        self.cfg = cfg
        self.state = state
        self.show_help = False
        self.show_info = False
        self.show_ping = False
        self.ping_ip = ""
        self.ping_interval = "1.0"
        self.ping_focus = 0            # 0=IP, 1=Interval
        self.ping_is_root = (os.geteuid() == 0)
        self.ping_min: Optional[float] = None
        self.ping_max: Optional[float] = None
        self.ping_avg: Optional[float] = None
        self.ping_last: Optional[float] = None
        self.ping_sum: float = 0.0
        self.ping_count: int = 0
        self.ping_running = False
        self.ping_error: Optional[str] = None
        self.ping_process = None

        self.show_traceroute = False
        self.traceroute_ip = ""
        self.traceroute_output: List[str] = []
        self.traceroute_running = False
        self.traceroute_error: Optional[str] = None
        self.traceroute_start: float = 0.0

        self.show_splash = False

        self.public_ip = ""
        self.public_ip_visible = self.cfg.display.show_public_ip
        self._start_public_ip_fetcher()

        self.hostname = socket.gethostname()

    def _fetch_public_ip(self) -> str:
        try:
            req = urllib.request.Request(
                "https://api.ipify.org",
                headers={"User-Agent": "IMon/0.0.5"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.read().decode("utf-8").strip()
        except Exception:
            return ""

    def _start_public_ip_fetcher(self):
        def loop():
            while True:
                ip = self._fetch_public_ip()
                if ip:
                    self.public_ip = ip
                    break
                time.sleep(10)
            while True:
                time.sleep(120)
                ip = self._fetch_public_ip()
                if ip:
                    self.public_ip = ip
        t = threading.Thread(target=loop, daemon=True)
        t.start()

    def get_size(self):
        h, w = self.stdscr.getmaxyx()
        return max(h, 20), max(w, 60)

    def draw(self):
        self.stdscr.erase()
        h, w = self.get_size()

        bg_attr = get_attr(self.cfg, "background")
        bg_line = self.cfg.background_char * w
        for r in range(h):
            safe_addstr(self.stdscr, r, 0, bg_line, bg_attr)

        self._draw_top_bar(w)
        self._draw_bottom_bar(h, w)
        self._draw_panels(h, w)

        if self.show_splash:
            self._draw_splash_popup(h, w)
        if self.show_help:
            self._draw_help_popup(h, w)
        if self.show_info:
            self._draw_info_popup(h, w)
        if self.show_ping:
            self._draw_ping_popup(h, w)
        if self.show_traceroute:
            self._draw_traceroute_popup(h, w)

        self.stdscr.noutrefresh()
        curses.doupdate()
        if self.cfg.color_mode == "mda":
            try:
                curses.putp(b"\033[38;2;255;145;0m")
                curses.putp(b"\033[48;2;0;0;0m")
            except curses.error:
                pass

    # -- Status bars -------------------------------------------------------

    def _draw_top_bar(self, w: int):
        attr = get_attr(self.cfg, "status_bar_top", bold=True)
        safe_addstr(self.stdscr, 0, 0, " " * w, attr)
        safe_addstr(self.stdscr, 0, 0,
                    f" {self.cfg.app_name} v{self.cfg.version}", attr)

        right = ""
        if self.public_ip_visible and self.public_ip:
            right = f" {self.public_ip} "
        if self.state.paused:
            right = f"{right} PAUSED " if right else " PAUSED "
        if right:
            right += " "
            safe_addstr(self.stdscr, 0, w - len(right), right,
                        attr | (curses.A_BLINK if self.state.paused else 0))

    def _draw_bottom_bar(self, h: int, w: int):
        attr = get_attr(self.cfg, "status_bar_bottom", bold=True)
        y = h - 1
        safe_addstr(self.stdscr, y, 0, " " * w, attr)

        dc = self.cfg.display
        col = 1
        if dc.show_hostname:
            safe_addstr(self.stdscr, y, col, self.hostname, attr)
            col += len(self.hostname)

        if dc.show_iface_count:
            with self.state.lock:
                cnt = len(self.state.interfaces)
            safe_addstr(self.stdscr, y, col, f"  [{cnt} ifaces]", attr)
            col += len(f"  [{cnt} ifaces]")

        hints = " H:Help  I:Info  P:Pause  N:Ping  T:Tracert  A:PublicIP  C:Credits  Q:Quit"
        hx = max(col, (w - len(hints)) // 2)
        safe_addstr(self.stdscr, y, hx, hints, attr)

        if dc.show_datetime:
            dt = format_datetime(dc.datetime_format)
            safe_addstr(self.stdscr, y, w - len(dt) - 2, dt, attr)

    # -- Panels ------------------------------------------------------------

    def _draw_panels(self, h: int, w: int):
        border_attr = get_attr(self.cfg, "border")
        title_attr = get_attr(self.cfg, "border_title", bold=True)
        value_attr = get_attr(self.cfg, "text_value", bold=True)
        label_attr = get_attr(self.cfg, "text_label")
        warn_attr = get_attr(self.cfg, "text_warning")
        header_attr = get_attr(self.cfg, "header_bg", bold=True)
        win_bg = get_attr(self.cfg, "text_normal")

        with self.state.lock:
            ifaces = list(self.state.interfaces.values())
        if self.cfg.display.interface_filter:
            ifaces = [i for i in ifaces if i.name in self.cfg.display.interface_filter]

        margin = 1
        inner_w = w - 2 * margin - 1
        cy, ce = 2, h - 2
        avail_h = ce - cy

        int_h = max(6, avail_h * 3 // 7)
        tr_h = max(5, avail_h - int_h - 1)

        int_y = cy
        tr_y = int_y + int_h + 1

        self._draw_int_window(int_y, margin, int_h, inner_w, ifaces,
                              border_attr, title_attr, value_attr,
                              label_attr, warn_attr, header_attr, win_bg)
        self._draw_traffic_window(tr_y, margin, tr_h, inner_w, ifaces,
                                  border_attr, title_attr, value_attr,
                                  label_attr, warn_attr, header_attr, win_bg)

    def _draw_int_window(self, y, x, h, w, ifaces, border_attr, title_attr,
                         value_attr, label_attr, warn_attr, header_attr, win_bg):
        draw_double_box(self.stdscr, y, x, h, w, "Interfaces",
                        border_attr, title_attr, win_bg)
        ix = x + 2
        iw = w - 4
        if iw < 15:
            return

        dc = self.cfg.display

        # Dynamic column sizing (account for 2-space gaps between 6 columns)
        gap_total = 10  # 5 gaps between 6 columns * 2 spaces each
        nw = min(dc.interface_name_width, max(4, (iw - gap_total) // 10))
        rest = iw - nw - gap_total
        mw = min(dc.mac_width, max(4, rest // 6))
        rest -= mw
        dsw = 2
        rest -= dsw
        i4w = min(dc.ipv4_width, max(6, rest // 3))
        rest -= i4w
        gw = min(dc.gateway_width, max(4, rest // 3))
        rest -= gw
        i6w = min(dc.ipv6_width, rest - 8) if rest > 12 else 0
        rest -= i6w

        row = y + 2

        # Header
        if row < y + h - 1:
            hdr = (
                f"  {'Name':<{nw}}  {'MAC':<{mw}}  {'DS':<{dsw}}  "
                f"{'IPv4/Mask':<{i4w}}  {'Gateway':<{gw}}"
            )
            if i6w > 4:
                hdr += f"  {'IPv6':<{i6w}}"
            safe_addstr(self.stdscr, row, ix, hdr[:iw], header_attr)
            row += 1

        # Separator
        if row < y + h - 1:
            safe_addstr(self.stdscr, row, ix, BOX_H * iw, label_attr)
            row += 1

        if not ifaces:
            if row < y + h - 1:
                safe_addstr(self.stdscr, row, ix, " No interfaces", warn_attr)
            return

        for iface in ifaces:
            if row >= y + h - 1:
                break
            nm = iface.name[:nw].ljust(nw)
            mc = iface.mac[:mw].ljust(mw)
            ipv4_str = f"{iface.ipv4}/{iface.prefix}" if iface.prefix else iface.ipv4
            i4 = ipv4_str[:i4w].ljust(i4w)
            gw4 = iface.gateway4[:gw].ljust(gw)
            i6 = ""
            if i6w > 4:
                i6s = f"{iface.ipv6}/{iface.ipv6_prefix}" if iface.ipv6 else "-"
                i6 = i6s[:i6w].ljust(i6w)
            if not iface.ipv4:
                ds_val = "N"
            elif iface.is_dhcp:
                ds_val = "D"
            else:
                ds_val = "S"
            ds = ds_val.ljust(dsw)
            if not iface.is_up and iface.ipv4:
                line_attr = get_attr(self.cfg, "state_down_hasip")
            elif not iface.is_up:
                line_attr = get_attr(self.cfg, "state_down")
            elif not iface.ipv4:
                line_attr = get_attr(self.cfg, "state_up_noip")
            else:
                line_attr = value_attr

            line = f"  {nm}  {mc}  {ds}  {i4}  {gw4}"
            if i6w > 4:
                line += f"  {i6}"
            line = line[:iw]
            safe_addstr(self.stdscr, row, ix, line, line_attr)
            row += 1

    def _draw_traffic_window(self, y, x, h, w, ifaces, border_attr, title_attr,
                             value_attr, label_attr, warn_attr, header_attr, win_bg):
        draw_double_box(self.stdscr, y, x, h, w, "Traffic",
                        border_attr, title_attr, win_bg)
        ix = x + 2
        iw = w - 4
        if iw < 15:
            return

        dc = self.cfg.display
        nw = min(dc.interface_name_width + 2, iw // 6 + 2)
        rest = iw - nw - 6
        col_w = max(8, rest // 5)

        row = y + 2

        if row < y + h - 1:
            hdr = (
                f" {'Interface':<{nw}} "
                f"{'Up':>{col_w}} {'Dn':>{col_w}} "
                f"{'Tx':>{col_w}} {'Rx':>{col_w}} "
                f"{'Pkt/s':>{col_w}}"
            )[:iw]
            safe_addstr(self.stdscr, row, ix, hdr, header_attr)
            row += 1

        if row < y + h - 1:
            safe_addstr(self.stdscr, row, ix, BOX_H * iw, label_attr)
            row += 1

        if not ifaces:
            if row < y + h - 1:
                safe_addstr(self.stdscr, row, ix, " No interfaces", warn_attr)
            return

        up_attr = get_attr(self.cfg, "traffic_up", bold=True)
        dn_attr = get_attr(self.cfg, "traffic_dn", bold=True)

        for iface in ifaces:
            if row >= y + h - 1:
                break
            nm = iface.name[:nw].ljust(nw)
            up = format_rate(iface.bytes_sent_rate)[:col_w].rjust(col_w)
            dn = format_rate(iface.bytes_recv_rate)[:col_w].rjust(col_w)
            tx = format_bytes(iface.bytes_sent)[:col_w].rjust(col_w)
            rx = format_bytes(iface.bytes_recv)[:col_w].rjust(col_w)
            pkt = format_pkt_rate(iface.packets_sent_rate + iface.packets_recv_rate)[:col_w].rjust(col_w)

            if not iface.is_up and iface.ipv4:
                line_attr_iface = get_attr(self.cfg, "state_down_hasip")
            elif not iface.is_up:
                line_attr_iface = get_attr(self.cfg, "state_down")
            elif not iface.ipv4:
                line_attr_iface = get_attr(self.cfg, "state_up_noip")
            else:
                line_attr_iface = value_attr
            safe_addstr(self.stdscr, row, ix, f" {nm} ", line_attr_iface)
            bx = ix + nw + 2
            safe_addstr(self.stdscr, row, bx, up, up_attr)
            safe_addstr(self.stdscr, row, bx + col_w + 1, dn, dn_attr)
            safe_addstr(self.stdscr, row, bx + 2 * (col_w + 1), tx, line_attr_iface)
            safe_addstr(self.stdscr, row, bx + 3 * (col_w + 1), rx, line_attr_iface)
            safe_addstr(self.stdscr, row, bx + 4 * (col_w + 1), pkt, line_attr_iface)
            row += 1

    # -- Popup -------------------------------------------------------------

    def _draw_splash_popup(self, h: int, w: int):
        pw = min(46, w - 6)
        ph = 8
        py = (h - ph) // 2
        px = (w - pw) // 2

        bg = get_attr(self.cfg, "popup_bg")
        for r in range(py, py + ph):
            safe_addstr(self.stdscr, r, px, " " * pw, bg)

        border_attr = get_attr(self.cfg, "popup_border")
        title_attr = get_attr(self.cfg, "popup_border_title", bold=True)
        draw_double_box(self.stdscr, py, px, ph, pw,
                        self.cfg.app_name, border_attr, title_attr,
                        double=self.cfg.popup.border_double)

        label_attr = get_attr(self.cfg, "popup_text_label", bold=True)
        val_attr = get_attr(self.cfg, "popup_text_value", bold=True)
        norm_attr = get_attr(self.cfg, "popup_text_normal")

        safe_addstr(self.stdscr, py + 2, px + 3, f"Version: {SCRIPT_VERSION}", val_attr)
        safe_addstr(self.stdscr, py + 3, px + 3, f"Author:  {SCRIPT_AUTHOR}", norm_attr)
        safe_addstr(self.stdscr, py + 4, px + 3, f"GitHub:  {SCRIPT_GITHUB}", norm_attr)

        hint = "Press any key to dismiss"
        hx = px + (pw - len(hint)) // 2
        safe_addstr(self.stdscr, py + ph - 2, hx, hint,
                    get_attr(self.cfg, "popup_text_warning"))

    def _draw_help_popup(self, h: int, w: int):
        pw = min(50, w - 6)
        color_modes = {"vga": "VGA (full color)", "hgc": "HGC (amber)", "mono": "MONO (B&W)"}
        cur_mode = color_modes.get(self.cfg.color_mode, self.cfg.color_mode)
        items = [
            ("Version:", SCRIPT_VERSION),
            ("Author:",  SCRIPT_AUTHOR),
            ("GitHub:",  SCRIPT_GITHUB),
            ("Color:",   cur_mode),
            ("", ""),
            ("H",   "Toggle this help"),
            ("I",   "Toggle colors info"),
            ("N",   "Ping dialog"),
            ("T",   "Trace route (tracert)"),
            ("P",   "Pause / resume refresh"),
            ("A",   "Toggle public IP display"),
            ("C",   "Show credits"),
            ("Q",   "Quit"),
            ("ESC", "Close popup"),
        ]
        ph = len(items) + 4
        py = (h - ph) // 2
        px = (w - pw) // 2

        bg = get_attr(self.cfg, "popup_bg")
        for r in range(py, py + ph):
            safe_addstr(self.stdscr, r, px, " " * pw, bg)

        border_attr = get_attr(self.cfg, "popup_border")
        title_attr = get_attr(self.cfg, "popup_border_title", bold=True)
        draw_double_box(self.stdscr, py, px, ph, pw,
                        f"Help \u2014 {self.cfg.app_name} v{SCRIPT_VERSION} by {SCRIPT_AUTHOR}", border_attr, title_attr,
                        double=self.cfg.popup.border_double)

        la = get_attr(self.cfg, "popup_text_label", bold=True)
        va = get_attr(self.cfg, "popup_text_normal")
        for i, (k, d) in enumerate(items):
            if k == "":
                continue
            safe_addstr(self.stdscr, py + 2 + i, px + 3, f"  {k:>8s} ", la)
            safe_addstr(self.stdscr, py + 2 + i, px + 16, d, va)

    # -- Input -------------------------------------------------------------

    def _draw_info_popup(self, h: int, w: int):
        pw = min(46, w - 6)
        items = [
            ("Interface States:", "title"),
            ("  Down, no IP", "state_down"),
            ("  Up, no IP", "state_up_noip"),
            ("  Down, has IP", "state_down_hasip"),
            ("", ""),
            ("DS Column:", "title"),
            ("  D  = DHCP / dynamic address", ""),
            ("  S  = Static / manual address", ""),
            ("  N  = No IP address", ""),
            ("  E  = Error / unknown", ""),
        ]
        ph = len(items) + 4
        py = (h - ph) // 2
        px = (w - pw) // 2

        bg = get_attr(self.cfg, "popup_bg")
        for r in range(py, py + ph):
            safe_addstr(self.stdscr, r, px, " " * pw, bg)

        border_attr = get_attr(self.cfg, "popup_border")
        title_attr = get_attr(self.cfg, "popup_border_title", bold=True)
        draw_double_box(self.stdscr, py, px, ph, pw,
                        "Colors & States", border_attr, title_attr,
                        double=self.cfg.popup.border_double)

        for i, (text, kind) in enumerate(items):
            if not text:
                continue
            if kind == "title":
                attr = get_attr(self.cfg, "popup_text_label", bold=True)
            elif kind:
                attr = get_attr(self.cfg, kind)
            else:
                attr = get_attr(self.cfg, "popup_text_normal")
            safe_addstr(self.stdscr, py + 2 + i, px + 3, text, attr)

    # -- Ping -------------------------------------------------------------

    def _get_ping_color_name(self, ms: float) -> str:
        pt = self.cfg.ping
        if ms < pt.green:
            return "ping_green"
        elif ms < pt.yellow:
            return "ping_yellow"
        elif ms < pt.orange:
            return "ping_orange"
        elif ms < pt.magenta:
            return "ping_magenta"
        elif ms < pt.red:
            return "ping_red"
        else:
            return "ping_critical"

    def _draw_ping_popup(self, h: int, w: int):
        pw = min(54, w - 6)
        ph = 16
        py = (h - ph) // 2
        px = (w - pw) // 2

        bg = get_attr(self.cfg, "popup_bg")
        for r in range(py, py + ph):
            safe_addstr(self.stdscr, r, px, " " * pw, bg)

        border_attr = get_attr(self.cfg, "popup_border")
        title_attr = get_attr(self.cfg, "popup_border_title", bold=True)
        draw_double_box(self.stdscr, py, px, ph, pw,
                        "Ping", border_attr, title_attr,
                        double=self.cfg.popup.border_double)

        label_attr = get_attr(self.cfg, "popup_text_label", bold=True)
        val_attr = get_attr(self.cfg, "popup_text_value", bold=True)
        norm_attr = get_attr(self.cfg, "popup_text_normal")
        warn_attr = get_attr(self.cfg, "popup_text_warning")

        # -- IP input field --
        ip_label = "IP address:"
        safe_addstr(self.stdscr, py + 2, px + 3, ip_label, label_attr)
        ix = px + 3 + len(ip_label) + 1
        iw = pw - (ix - px) - 3
        ip_disp = self.ping_ip.ljust(iw)[:iw]
        ip_attr = curses.A_REVERSE if self.ping_focus == 0 and not self.ping_running else norm_attr
        safe_addstr(self.stdscr, py + 2, ix, ip_disp, ip_attr)

        # -- Interval input field --
        min_int = 0.01 if self.ping_is_root else 1.0
        int_label = f"Interval:"
        safe_addstr(self.stdscr, py + 3, px + 3, int_label, label_attr)
        int_x = px + 3 + len(int_label) + 1
        int_w = 8
        int_disp = self.ping_interval.ljust(int_w)[:int_w]
        int_attr = curses.A_REVERSE if self.ping_focus == 1 and not self.ping_running else norm_attr
        safe_addstr(self.stdscr, py + 3, int_x, int_disp, int_attr)
        safe_addstr(self.stdscr, py + 3, int_x + int_w + 1,
                    f"s  (min {min_int})", warn_attr)

        # Separator
        safe_addstr(self.stdscr, py + 4, px + 2, BOX_H * (pw - 4), norm_attr)

        # -- Stats --
        if self.ping_count > 0:
            safe_addstr(self.stdscr, py + 6, px + 3,
                        f"Min: {self.ping_min:.1f}ms  "
                        f"Max: {self.ping_max:.1f}ms  "
                        f"Avg: {self.ping_avg:.1f}ms", val_attr)
            safe_addstr(self.stdscr, py + 7, px + 3,
                        f"Packets: {self.ping_count}", val_attr)
        else:
            safe_addstr(self.stdscr, py + 6, px + 3,
                        "Press Enter to start ping", norm_attr)

        if self.ping_running:
            safe_addstr(self.stdscr, py + 7, px + 3, "Pinging...", warn_attr)
        elif self.ping_error:
            err_attr = get_attr(self.cfg, "popup_text_error")
            safe_addstr(self.stdscr, py + 7, px + 3,
                        self.ping_error[:pw - 6], err_attr)

        # -- Progress bar --
        bar_y = py + 9
        bar_x = px + 3
        bar_w = pw - 6
        if self.ping_last is not None and bar_w > 4:
            pt = self.cfg.ping
            max_bar = pt.red
            fill = min(self.ping_last / max_bar, 1.0)
            fill_chars = int(fill * bar_w)
            bar_color = self._get_ping_color_name(self.ping_last)
            bar_attr = get_attr(self.cfg, bar_color, bold=True)

            safe_addstr(self.stdscr, bar_y, bar_x, "░" * bar_w, norm_attr)
            if fill_chars > 0:
                safe_addstr(self.stdscr, bar_y, bar_x,
                            "▓" * fill_chars, bar_attr)

            label = f"{self.ping_last:.1f}ms / {max_bar:.0f}ms"
            lx = bar_x + (bar_w - len(label)) // 2
            safe_addstr(self.stdscr, bar_y + 1, lx, label, bar_attr)
        else:
            safe_addstr(self.stdscr, bar_y, bar_x, "░" * bar_w, norm_attr)

        # -- Hints --
        hint_attr = get_attr(self.cfg, "popup_text_warning")
        if self.ping_running:
            hints = "[Esc] Stop & Close"
        else:
            hints = "[Tab] Switch   [Enter] Start   [Esc] Close"
        hx = px + (pw - len(hints)) // 2
        safe_addstr(self.stdscr, py + ph - 2, hx, hints, hint_attr)

    def _stop_ping(self):
        if self.ping_process:
            try:
                self.ping_process.terminate()
                self.ping_process.wait(2)
            except Exception:
                try:
                    self.ping_process.kill()
                except Exception:
                    pass
            self.ping_process = None
        self.ping_running = False

    def _run_ping(self):
        ip = self.ping_ip.strip()
        if not ip:
            self.ping_error = "No IP address entered"
            return
        try:
            interval = abs(float(self.ping_interval))
            min_int = 0.01 if self.ping_is_root else 1.0
            if interval < min_int:
                interval = min_int
                self.ping_interval = f"{min_int}"
        except ValueError:
            self.ping_error = "Invalid interval"
            return

        self._stop_ping()
        self.ping_error = None
        self.ping_min = None
        self.ping_max = None
        self.ping_avg = None
        self.ping_last = None
        self.ping_count = 0
        self.ping_sum = 0.0
        self.ping_running = True

        def _ping_thread():
            try:
                proc = subprocess.Popen(
                    ["ping", "-i", str(interval), "-n", ip],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1,
                )
                self.ping_process = proc
                for line in proc.stdout:
                    if not self.show_ping:
                        break
                    m = re.search(r"time=([\d.]+)\s*ms", line)
                    if m:
                        val = float(m.group(1))
                        self.ping_last = val
                        self.ping_sum += val
                        self.ping_count += 1
                        if self.ping_min is None or val < self.ping_min:
                            self.ping_min = val
                        if self.ping_max is None or val > self.ping_max:
                            self.ping_max = val
                        self.ping_avg = self.ping_sum / self.ping_count
                proc.wait()
            except Exception as e:
                if self.show_ping:
                    self.ping_error = str(e)[:50]
            finally:
                self.ping_process = None
                self.ping_running = False

        t = threading.Thread(target=_ping_thread, daemon=True)
        t.start()

    # -- Traceroute -------------------------------------------------------

    def _draw_traceroute_popup(self, h: int, w: int):
        pw = min(80, w - 4)
        ph = min(h - 4, 22)
        py = (h - ph) // 2
        px = (w - pw) // 2

        bg = get_attr(self.cfg, "popup_bg")
        for r in range(py, py + ph):
            safe_addstr(self.stdscr, r, px, " " * pw, bg)

        border_attr = get_attr(self.cfg, "popup_border")
        title_attr = get_attr(self.cfg, "popup_border_title", bold=True)
        draw_double_box(self.stdscr, py, px, ph, pw,
                        "Trace Route", border_attr, title_attr,
                        double=self.cfg.popup.border_double)

        label_attr = get_attr(self.cfg, "popup_text_label", bold=True)
        norm_attr = get_attr(self.cfg, "popup_text_normal")
        warn_attr = get_attr(self.cfg, "popup_text_warning")
        val_attr = get_attr(self.cfg, "popup_text_value", bold=True)

        # IP input
        safe_addstr(self.stdscr, py + 2, px + 3, "IP address:", label_attr)
        ix = px + 3 + 12
        iw = max(15, min(pw - 30, 16))  # 15 chars fits "111.111.111.111"
        disp = self.traceroute_ip.ljust(iw)[:iw]
        inp_attr = curses.A_REVERSE if not self.traceroute_running else norm_attr
        safe_addstr(self.stdscr, py + 2, ix, disp, inp_attr)
        if not self.traceroute_running:
            safe_addstr(self.stdscr, py + 2, ix + iw + 2,
                        "[Enter] Trace", warn_attr)

        # Separator
        safe_addstr(self.stdscr, py + 3, px + 2, BOX_H * (pw - 4), norm_attr)

        # Output area
        out_y = py + 4
        out_h = ph - 6
        out_w = pw - 6

        elapsed = time.time() - self.traceroute_start if self.traceroute_running and self.traceroute_start else 0.0
        if self.traceroute_output:
            lines = self.traceroute_output[-(out_h - 1):]
            for i, line in enumerate(lines):
                if out_y + i >= py + ph - 2:
                    break
                safe_addstr(self.stdscr, out_y + i, px + 3,
                            line[:out_w], norm_attr)
            if self.traceroute_running:
                safe_addstr(self.stdscr, out_y + len(lines), px + 3,
                            f"Tracing... ({elapsed:.0f}s)", warn_attr)
        elif self.traceroute_running:
            safe_addstr(self.stdscr, out_y, px + 3,
                        f"Tracing... ({elapsed:.0f}s)", warn_attr)
        elif self.traceroute_error:
            err_attr = get_attr(self.cfg, "popup_text_error")
            safe_addstr(self.stdscr, out_y, px + 3,
                        self.traceroute_error[:out_w], err_attr)
        else:
            safe_addstr(self.stdscr, out_y, px + 3,
                        "Enter IP and press Enter", norm_attr)

        # Hint
        hint_attr = get_attr(self.cfg, "popup_text_warning")
        hints = "[Esc] Close"
        hx = px + (pw - len(hints)) // 2
        safe_addstr(self.stdscr, py + ph - 2, hx, hints, hint_attr)

    def _run_traceroute(self):
        ip = self.traceroute_ip.strip()
        if not ip:
            self.traceroute_error = "No IP address entered"
            return

        cmd = self.cfg.traceroute_cmd
        if cmd not in TRACEROUTE_ALLOWED:
            allowed = ", ".join(sorted(TRACEROUTE_ALLOWED))
            self.traceroute_error = f"Invalid command '{cmd}'. Allowed: {allowed}"
            return

        self.traceroute_output = []
        self.traceroute_error = None
        self.traceroute_running = True
        self.traceroute_start = time.time()

        # Build command with correct flags per tool
        if cmd == "mtr":
            args = [cmd, "-n", "-r", "-w", "-c", "10", ip]
        else:
            args = [cmd, "-n", ip]

        def _traceroute_thread():
            try:
                out = subprocess.run(
                    args,
                    capture_output=True, text=True, timeout=30,
                )
                for line in out.stdout.splitlines():
                    line = line.rstrip("\r")
                    if line and not line.startswith("\x1b"):
                        self.traceroute_output.append(line)
                if not self.traceroute_output:
                    err = out.stderr.strip() or f"'{cmd}' returned no data"
                    self.traceroute_error = err[:80]
            except FileNotFoundError:
                self.traceroute_error = f"'{cmd}' not found on system"
            except subprocess.TimeoutExpired:
                self.traceroute_error = "Trace timed out (30s)"
            except Exception as e:
                self.traceroute_error = str(e)[:80]
            finally:
                self.traceroute_running = False

        t = threading.Thread(target=_traceroute_thread, daemon=True)
        t.start()

    def handle_key(self, key: int) -> bool:
        ch = chr(key) if 0 < key < 256 else ""
        cl = ch.lower()
        kc = self.cfg.keys

        if self.show_splash:
            self.show_splash = False
            return True

        if self.show_traceroute:
            if key == 27:
                self.show_traceroute = False
                self.traceroute_ip = ""
                self.traceroute_output = []
                self.traceroute_error = None
                self.traceroute_running = False
                self.traceroute_start = 0.0
                return True
            if key in (10, 13) and not self.traceroute_running:
                self._run_traceroute()
                return True
            if key in (127, 8, curses.KEY_BACKSPACE) and not self.traceroute_running:
                self.traceroute_ip = self.traceroute_ip[:-1]
                return True
            if 32 <= key < 127 and not self.traceroute_running:
                allowed = set("0123456789.:abcdefABCDEF")
                if ch in allowed and len(self.traceroute_ip) < 39:
                    self.traceroute_ip += ch
                return True
            return True

        if self.show_ping:
            if key == 27:
                self._stop_ping()
                self.show_ping = False
                self.ping_ip = ""
                self.ping_interval = "1.0"
                self.ping_focus = 0
                self.ping_min = self.ping_max = self.ping_avg = None
                self.ping_last = None
                self.ping_error = None
                self.ping_count = 0
                self.ping_sum = 0.0
                return True
            if key in (10, 13) and not self.ping_running:
                self._run_ping()
                return True
            if key == 9 or key == curses.KEY_BTAB:  # Tab / Shift-Tab
                self.ping_focus = 1 - self.ping_focus
                return True
            if key in (127, 8, curses.KEY_BACKSPACE):
                if self.ping_running:
                    return True
                if self.ping_focus == 0:
                    self.ping_ip = self.ping_ip[:-1]
                else:
                    self.ping_interval = self.ping_interval[:-1]
                return True
            if 32 <= key < 127:
                if self.ping_running:
                    return True
                if self.ping_focus == 0:
                    allowed = set("0123456789.:abcdefABCDEF")
                    if ch in allowed and len(self.ping_ip) < 39:
                        self.ping_ip += ch
                else:
                    if ch in "0123456789." and len(self.ping_interval) < 10:
                        if ch == "." and "." in self.ping_interval:
                            return True
                        self.ping_interval += ch
                return True
            return True

        if cl == kc.quit and not self.show_help and not self.show_info:
            return False
        if key == 27:
            self.show_help = False
            self.show_info = False
            return True
        if cl == kc.help or key == curses.KEY_F1:
            self.show_help = not self.show_help
            if self.show_help:
                self.show_info = False
            return True
        if cl == kc.info:
            self.show_info = not self.show_info
            if self.show_info:
                self.show_help = False
            return True
        if cl == kc.toggle_pause:
            with self.state.lock:
                self.state.paused = not self.state.paused
            return True
        if cl == kc.ping and not self.show_help and not self.show_info:
            self.show_ping = not self.show_ping
            if self.show_ping:
                self.ping_ip = ""
                self.ping_interval = "1.0"
                self.ping_focus = 0
                self.ping_min = self.ping_max = self.ping_avg = None
                self.ping_last = None
                self.ping_error = None
                self.ping_count = 0
                self.ping_sum = 0.0
                self.ping_running = False
                self.ping_process = None
            return True
        if cl == kc.traceroute and not self.show_help and not self.show_info:
            self.show_traceroute = not self.show_traceroute
            if self.show_traceroute:
                self.traceroute_ip = ""
                self.traceroute_output = []
                self.traceroute_error = None
                self.traceroute_running = False
                self.traceroute_start = 0.0
            return True
        if cl == kc.credits:
            self.show_splash = not self.show_splash
            return True
        if cl == kc.toggle_public_ip:
            self.public_ip_visible = not self.public_ip_visible
            return True
        return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main_loop(stdscr, cfg: Config):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    stdscr.nodelay(False)
    stdscr.timeout(cfg.refresh_interval_ms)

    init_colors(cfg)
    state = MonitorState()

    collector = InterfaceCollector(
        state, cfg.network.interface_interval, cfg.display.show_loopback,
    )
    collector.start()

    collect_interfaces(state, cfg.display.show_loopback)

    try:
        ui = UIManager(stdscr, cfg, state)
        while True:
            try:
                ui.draw()
            except curses.error:
                pass
            key = stdscr.getch()
            if key == curses.KEY_RESIZE:
                stdscr.clear()
                continue
            if key != -1:
                if not ui.handle_key(key):
                    break
    finally:
        if cfg.color_mode in ("hgc", "mda"):
            try:
                curses.putp(b"\033[0m")
            except curses.error:
                pass
        collector.stop()
        collector.join(2)


def main():
    parser = argparse.ArgumentParser(
        description=f"IMon v{SCRIPT_VERSION} \u2014 Interface Monitor TUI by {SCRIPT_AUTHOR} ({SCRIPT_GITHUB})")
    parser.add_argument("-c", "--config", metavar="FILE",
                        help="Path to config file (default: imon.cfg)")
    parser.add_argument("--int", metavar="IFACE",
                        help="Comma-separated interface name(s) to monitor (e.g. eth0,wlan0)")
    parser.add_argument("--color", choices=("vga", "hgc", "mono", "mda"),
                        help="Color mode: vga (default, full color), hgc (two-tone amber), mono (black & white), mda (amber monochrome)")
    parser.add_argument("--version", action="store_true",
                        help="Show version and exit")
    args = parser.parse_args()

    cfg_path = args.config
    if not cfg_path:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(script_dir, "imon.cfg")

    cfg = load_config(cfg_path)

    if args.color:
        cfg.color_mode = args.color

    if args.int:
        cfg.display.interface_filter = {x.strip() for x in args.int.split(",") if x.strip()}

    if args.version:
        print(f"{cfg.app_name} v{cfg.version} by {cfg.author}")
        return

    if not HAS_PSUTIL:
        print("Error: psutil is required. Install with: pip install psutil",
              file=sys.stderr)
        sys.exit(1)

    try:
        curses.wrapper(lambda s: main_loop(s, cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

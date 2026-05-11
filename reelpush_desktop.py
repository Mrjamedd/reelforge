#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
import mimetypes
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk


APP_NAME = "ReelPush Studio"


def _application_resource_root() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent

    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str):
        candidates.append(Path(meipass).resolve())

    executable_parent = Path(sys.executable).resolve().parent
    candidates.extend(
        [
            executable_parent,
            executable_parent.parent,
            executable_parent.parent / "Resources",
            executable_parent.parent / "Frameworks",
            Path.cwd(),
        ]
    )

    fallback = candidates[0] if candidates else Path(tempfile.gettempdir())
    return fallback


def _user_data_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "ReelPush Studio"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ReelPush Studio"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "reelpush-studio"


USER_DATA_ROOT = _user_data_root()
RESOURCE_ROOT = _application_resource_root()
ROOT_DIR = USER_DATA_ROOT / "runtime" if getattr(sys, "frozen", False) else RESOURCE_ROOT
OFFICIAL_SERVER_URL = "http://129.213.126.251:8100"
API_BASE = os.environ.get("REELPUSH_API_BASE", f"{OFFICIAL_SERVER_URL}/api").rstrip("/")
API_ROOT = API_BASE[:-4] if API_BASE.endswith("/api") else API_BASE
SETTINGS_PATH = USER_DATA_ROOT / "desktop_settings.json"
ENV_PATH = ROOT_DIR / ".env"
ENV_EXAMPLE_PATH = ROOT_DIR / ".env.example"
OAUTH_REDIRECT_URIS = {
    platform: f"{API_ROOT}/api/oauth/{platform}/callback"
    for platform in ("youtube", "instagram", "tiktok")
}


def _set_active_api_url(url: str) -> None:
    """Update API_BASE, API_ROOT, and OAUTH_REDIRECT_URIS to point at a new server URL."""
    global API_BASE, API_ROOT
    API_BASE = _api_base_for_url(url)
    API_ROOT = API_BASE[:-4]
    for platform in ("youtube", "instagram", "tiktok"):
        OAUTH_REDIRECT_URIS[platform] = f"{API_ROOT}/api/oauth/{platform}/callback"


def _api_base_for_url(url: str) -> str:
    clean = url.strip().rstrip("/")
    return clean if clean.endswith("/api") else f"{clean}/api"


def _sync_bundled_runtime_files() -> None:
    if not getattr(sys, "frozen", False):
        return
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    for name in (".env.example", "README.md"):
        source = RESOURCE_ROOT / name
        if source.exists():
            shutil.copy2(source, ROOT_DIR / name)


_sync_bundled_runtime_files()


def _ensure_local_env_file() -> None:
    if ENV_PATH.exists():
        _ensure_desktop_api_url()
        return
    if not ENV_EXAMPLE_PATH.exists():
        return
    lines: list[str] = []
    for raw_line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in raw_line:
            key, value = raw_line.split("=", 1)
            value = value.split(" #", 1)[0].rstrip()
            lines.append(f"{key.strip()}={value.strip()}")
        else:
            lines.append(raw_line)
    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    _ensure_desktop_api_url()


def _ensure_desktop_api_url() -> None:
    if not ENV_PATH.exists():
        return

    known_stale_values = {"", "http://localhost:8000", "http://127.0.0.1:8000"}
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    next_lines: list[str] = []
    found = False
    changed = False

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() == "API_URL":
                found = True
                if value.strip().strip("\"'") in known_stale_values:
                    next_lines.append(f"API_URL={API_ROOT}")
                    changed = True
                    continue
        next_lines.append(line)

    if not found:
        if next_lines and next_lines[-1].strip():
            next_lines.append("")
        next_lines.append(f"API_URL={API_ROOT}")
        changed = True

    if changed:
        ENV_PATH.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def _load_local_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values




_ensure_local_env_file()
LOCAL_ENV = _load_local_env()

PLATFORM_DETAILS = {
    "youtube": {
        "name": "YouTube Shorts",
        "short_name": "YouTube",
        "badge": "YT",
        "env": ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"),
        "fyi": "FYI: requires a Google Cloud OAuth client and a YouTube channel connection.",
    },
    "instagram": {
        "name": "Instagram Reels",
        "short_name": "Instagram",
        "badge": "IG",
        "env": ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"),
        "fyi": "FYI: Instagram publishing requires a Professional account linked to a Facebook Page.",
    },
    "tiktok": {
        "name": "TikTok",
        "short_name": "TikTok",
        "badge": "TK",
        "env": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"),
        "fyi": "FYI: TikTok direct publishing requires developer app approval for video publishing.",
    },
}
PLATFORM_ORDER = ("youtube", "instagram", "tiktok")

CREDENTIAL_FIELDS = {
    "YouTube": (
        ("YOUTUBE_CLIENT_ID", "Client ID", False),
        ("YOUTUBE_CLIENT_SECRET", "Client Secret", True),
    ),
    "Instagram": (
        ("INSTAGRAM_APP_ID", "App ID", False),
        ("INSTAGRAM_APP_SECRET", "App Secret", True),
    ),
    "TikTok": (
        ("TIKTOK_CLIENT_KEY", "Client Key", False),
        ("TIKTOK_CLIENT_SECRET", "Client Secret", True),
    ),
}

LOCAL_SERVICES = {
    "api": "Server API",
    "internet": "Network Connection",
}

BASE_COLORS = {
    "bg": "#101113",
    "panel": "#17191D",
    "soft_panel": "#202328",
    "field": "#111418",
    "field_hover": "#181D22",
    "log": "#0B0D10",
    "text": "#F2F0EA",
    "muted": "#B8B4AA",
    "subtle": "#817D75",
    "border": "#343942",
    "border_soft": "#262B33",
    "focus": "#6B5E4B",
    "secondary": "#262A30",
    "secondary_hover": "#303640",
    "disabled": "#1B1E23",
    "disabled_text": "#676C74",
    "good": "#6FD39B",
    "warn": "#F0B866",
    "bad": "#F07D75",
}

ACCENT_PRESETS = {
    "red": {
        "name": "Punch Coral",
        "description": "A direct studio accent for primary publishing actions.",
        "accent": "#F26D4D",
        "accent_hover": "#FF7E61",
        "accent_panel": "#2C1D18",
    },
    "blue": {
        "name": "Monitor Cyan",
        "description": "A crisp telemetry accent for server and readiness states.",
        "accent": "#48B7C7",
        "accent_hover": "#62C9D8",
        "accent_panel": "#14282D",
    },
    "green": {
        "name": "Ready Green",
        "description": "A clean signal color for review-ready publishing work.",
        "accent": "#6CCB8E",
        "accent_hover": "#7CDB9E",
        "accent_panel": "#16271D",
    },
    "purple": {
        "name": "Edit Violet",
        "description": "A restrained review accent for copy and credential work.",
        "accent": "#B094E8",
        "accent_hover": "#BEA5F2",
        "accent_panel": "#241E34",
    },
    "orange": {
        "name": "Signal Amber",
        "description": "A warm production-light accent for the current workspace.",
        "accent": "#F08D3C",
        "accent_hover": "#FF9F50",
        "accent_panel": "#2C2116",
    },
    "neutral": {
        "name": "Graphite White",
        "description": "A spare monochrome accent for the quietest control room.",
        "accent": "#D7D0C4",
        "accent_hover": "#E5DED2",
        "accent_panel": "#252522",
    },
}


def _colors_for_preset(key: str) -> dict[str, str]:
    preset = ACCENT_PRESETS.get(key, ACCENT_PRESETS["red"])
    return {**BASE_COLORS, **{name: preset[name] for name in ("accent", "accent_hover", "accent_panel")}}


THEMES = {
    key: {
        "name": preset["name"],
        "description": preset["description"],
        "colors": _colors_for_preset(key),
    }
    for key, preset in ACCENT_PRESETS.items()
}
LEGACY_THEME_MAP = {
    "graphite": "red",
    "steel": "blue",
    "olive": "green",
    "paper": "neutral",
}
DEFAULT_THEME = "red"
SPACING = {
    "xs": 4,
    "sm": 7,
    "md": 11,
    "lg": 15,
    "xl": 22,
    "xxl": 28,
}
TYPE_SCALE = {
    "display": (30, "600"),
    "h1": (22, "600"),
    "h2": (16, "600"),
    "body": (14, "400"),
    "body_strong": (14, "500"),
    "small": (12, "400"),
    "micro": (10, "500"),
}
RADIUS_PANEL = 8
RADIUS_CARD = 6
RADIUS_INPUT = 5
RADIUS_CHIP = 4
PADDING_PANEL = 22
PADDING_CARD = 16
PADDING_FIELD_X = SPACING["md"]
PADDING_FIELD_Y = SPACING["sm"]


def _load_desktop_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_desktop_settings(settings: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")


class RoundedFrame(tk.Frame):
    def __init__(
        self,
        parent,
        *,
        bg_color: str,
        fill_color: str,
        radius: int = RADIUS_CARD,
        padding: int = SPACING["lg"],
        shadow: bool = False,
        outline_color: str | None = None,
        outline_width: int = 1,
        min_height: int = 0,
    ) -> None:
        super().__init__(parent, bg=bg_color, highlightthickness=0, bd=0)
        self.bg_color = bg_color
        self.fill_color = fill_color
        self.radius = radius
        self.padding = padding
        self.shadow = shadow
        self.outline_color = outline_color
        self.outline_width = outline_width
        self.min_height = min_height

        self.canvas = tk.Canvas(self, bg=bg_color, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=fill_color, highlightthickness=0, bd=0)
        self.body_window = self.canvas.create_window(
            padding,
            padding,
            anchor="nw",
            window=self.body,
        )

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.body.bind("<Configure>", self._on_body_configure)

    @property
    def _shadow_offset(self) -> int:
        return 1 if self.shadow else 0

    def set_fill(self, fill_color: str) -> None:
        self.fill_color = fill_color
        self.body.configure(bg=fill_color)
        self._draw()

    def set_bg(self, bg_color: str) -> None:
        self.bg_color = bg_color
        self.configure(bg=bg_color)
        self.canvas.configure(bg=bg_color)
        self._draw()

    def set_outline(self, outline_color: str | None) -> None:
        self.outline_color = outline_color
        self._draw()

    def _on_body_configure(self, _event=None) -> None:
        self.canvas.configure(
            width=self.body.winfo_reqwidth() + (self.padding * 2) + self._shadow_offset,
            height=max(self.body.winfo_reqheight() + (self.padding * 2) + self._shadow_offset, self.min_height),
        )

    def _on_canvas_configure(self, event) -> None:
        width = max(event.width - (self.padding * 2) - self._shadow_offset, 1)
        height = max(event.height - (self.padding * 2) - self._shadow_offset, self.body.winfo_reqheight(), 1)
        self.canvas.itemconfigure(self.body_window, width=width, height=height)
        self._draw()

    def _draw(self) -> None:
        self.canvas.delete("surface")
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        edge = 1
        if self.shadow:
            self._rounded_rect(1, 2, width - 1, height - 1, self.radius, "#08090B", "surface")
        self._rounded_rect(
            edge,
            edge,
            width - self._shadow_offset - edge,
            height - self._shadow_offset - edge,
            self.radius,
            self.fill_color,
            "surface",
            outline=self.outline_color or "",
            outline_width=self.outline_width if self.outline_color else 1,
        )
        self.canvas.tag_lower("surface")

    def _rounded_rect(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int,
        fill: str,
        tag: str,
        *,
        outline: str = "",
        outline_width: int = 1,
    ) -> None:
        if x2 <= x1 or y2 <= y1:
            return
        radius = min(radius, max((x2 - x1) // 2, 1), max((y2 - y1) // 2, 1))
        if outline:
            self._fill_rounded_rect(x1, y1, x2, y2, radius, outline, tag)
            inset = max(outline_width, 1)
            if x2 - x1 <= inset * 2 or y2 - y1 <= inset * 2:
                return
            inner_radius = max(radius - inset, 1)
            self._fill_rounded_rect(
                x1 + inset,
                y1 + inset,
                x2 - inset,
                y2 - inset,
                inner_radius,
                fill,
                tag,
            )
            return
        self._fill_rounded_rect(x1, y1, x2, y2, radius, fill, tag)

    def _fill_rounded_rect(self, x1: int, y1: int, x2: int, y2: int, radius: int, fill: str, tag: str) -> None:
        if x2 <= x1 or y2 <= y1:
            return
        radius = min(radius, max((x2 - x1) // 2, 1), max((y2 - y1) // 2, 1))
        self.canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline="", tags=tag)
        self.canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill, outline="", tags=tag)
        self.canvas.create_arc(x1, y1, x1 + radius * 2, y1 + radius * 2, start=90, extent=90, fill=fill, outline="", tags=tag)
        self.canvas.create_arc(x2 - radius * 2, y1, x2, y1 + radius * 2, start=0, extent=90, fill=fill, outline="", tags=tag)
        self.canvas.create_arc(x2 - radius * 2, y2 - radius * 2, x2, y2, start=270, extent=90, fill=fill, outline="", tags=tag)
        self.canvas.create_arc(x1, y2 - radius * 2, x1 + radius * 2, y2, start=180, extent=90, fill=fill, outline="", tags=tag)


class ScrollableFrame(tk.Frame):
    def __init__(self, parent, *, bg_color: str, bottom_padding: int = 48) -> None:
        super().__init__(parent, bg=bg_color, highlightthickness=0, bd=0)
        self.bg_color = bg_color
        self.canvas = tk.Canvas(self, bg=bg_color, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview, style="Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.body = tk.Frame(self.canvas, bg=bg_color, highlightthickness=0, bd=0)
        self.content = tk.Frame(self.body, bg=bg_color, highlightthickness=0, bd=0)
        self.content.pack(fill="both", expand=True)
        tk.Frame(self.body, bg=bg_color, height=bottom_padding, highlightthickness=0, bd=0).pack(fill="x")
        self.body_window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def set_bg(self, bg_color: str) -> None:
        self.bg_color = bg_color
        self.configure(bg=bg_color)
        self.canvas.configure(bg=bg_color)
        self.body.configure(bg=bg_color)
        self.content.configure(bg=bg_color)

    def _on_body_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self.body_window, width=max(event.width, 1))

    def _bind_mousewheel(self, _event=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, event) -> None:
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")


class DropdownField(RoundedFrame):
    def __init__(
        self,
        parent,
        *,
        colors: dict[str, str],
        variable: tk.StringVar,
        values: list[str],
        font: tkfont.Font,
        command=None,
    ) -> None:
        super().__init__(
            parent,
            bg_color=colors["panel"],
            fill_color=colors["field"],
            radius=RADIUS_INPUT,
            padding=SPACING["xs"],
            outline_color=colors["border"],
        )
        self.colors = colors
        self.variable = variable
        self.values = values
        self.command = command
        self.text_label = tk.Label(
            self.body,
            textvariable=variable,
            bg=colors["field"],
            fg=colors["text"],
            font=font,
            padx=PADDING_FIELD_X,
            pady=PADDING_FIELD_Y,
            anchor="w",
            cursor="hand2",
        )
        self.text_label.pack(side="left", fill="x", expand=True)
        self.arrow_label = tk.Label(
            self.body,
            text="⌄",
            bg=colors["field"],
            fg=colors["muted"],
            font=font,
            padx=SPACING["sm"],
            pady=PADDING_FIELD_Y,
            cursor="hand2",
        )
        self.arrow_label.pack(side="right")
        for widget in (self, self.canvas, self.body, self.text_label, self.arrow_label):
            widget.bind("<Button-1>", self._open_menu)
            widget.bind("<Enter>", lambda _event: self._set_hover(True))
            widget.bind("<Leave>", lambda _event: self._set_hover(False))

    def refresh_colors(self, colors: dict[str, str]) -> None:
        self.colors = colors
        self.bg_color = colors["panel"]
        self.fill_color = colors["field"]
        self.set_outline(colors["border"])
        self.configure(bg=colors["panel"])
        self.canvas.configure(bg=colors["panel"])
        self.body.configure(bg=colors["field"])
        self.text_label.configure(bg=colors["field"], fg=colors["text"])
        self.arrow_label.configure(bg=colors["field"], fg=colors["muted"])

    def _open_menu(self, _event=None) -> str:
        self.set_outline(self.colors["accent"])
        menu = tk.Menu(
            self,
            tearoff=0,
            bg=self.colors["field"],
            fg=self.colors["text"],
            activebackground=self.colors["accent_panel"],
            activeforeground=self.colors["text"],
            bd=0,
            relief="flat",
        )
        for value in self.values:
            menu.add_command(label=value, command=lambda choice=value: self._choose(choice))
        try:
            menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())
        finally:
            menu.grab_release()
            self.after(
                100,
                lambda: self.set_outline(
                    self.colors["focus"] if getattr(self, "_rp_hovered", False) else self.colors["border"]
                ),
            )
        return "break"

    def _choose(self, value: str) -> None:
        self.variable.set(value)
        if self.command:
            self.command(value)

    def _set_hover(self, hovered: bool) -> None:
        setattr(self, "_rp_hovered", hovered)
        self.set_outline(self.colors["focus"] if hovered else self.colors["border"])


class AuthExpiredError(RuntimeError):
    """Raised when the local ReelPush API session token has expired."""


class ApiClient:
    def __init__(self) -> None:
        self.token: str | None = None

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        req = request.Request(
            f"{API_BASE}{path}",
            method=method,
            data=data,
            headers=self._headers(headers),
        )
        try:
            with request.urlopen(req, timeout=300) as response:
                raw = response.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            stripped = body.lstrip()
            if stripped.lower().startswith("<!") or stripped.lower().startswith("<html"):
                # Cloudflare or proxy HTML error — never surface raw HTML to the user.
                _cf_messages = {
                    524: "Server timed out or disconnected (524). Please retry or reconnect.",
                    520: "Server returned an unexpected response (520). Please retry or reconnect.",
                    521: "Server is unreachable (521). Please retry or reconnect.",
                    522: "Connection timed out (522). Please retry or reconnect.",
                    523: "Server is unreachable (523). Please retry or reconnect.",
                    525: "SSL handshake failed (525). Please retry or reconnect.",
                    526: "Invalid SSL certificate (526). Please retry or reconnect.",
                }
                message = _cf_messages.get(
                    exc.code,
                    f"Server returned an error (HTTP {exc.code}). Please retry or reconnect.",
                )
                logging.getLogger(__name__).warning("HTTP %s proxy/gateway error (HTML body suppressed)", exc.code)
                raise RuntimeError(message) from exc
            try:
                detail = json.loads(body)
            except json.JSONDecodeError:
                detail = body
            message = detail.get("detail") if isinstance(detail, dict) else detail
            if exc.code == 401 and str(message).strip() == "Invalid or expired credentials.":
                raise AuthExpiredError(str(message)) from exc
            raise RuntimeError(message) from exc
        except error.URLError as exc:
            raise RuntimeError(f"Could not reach ReelPush API at {API_BASE}.") from exc

    def login(self, email: str, password: str) -> None:
        payload = json.dumps({"email": email, "password": password}).encode("utf-8")
        result = self._request(
            "POST",
            "/auth/login",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        self.token = result["access_token"]

    def oauth_statuses(self) -> list[dict[str, Any]]:
        return self._request("GET", "/oauth/status")

    def oauth_connect_url(self, platform: str) -> str:
        result = self._request("GET", f"/oauth/{platform}/connect-url")
        return result["authorization_url"]

    def oauth_disconnect(self, platform: str) -> None:
        self._request("DELETE", f"/oauth/{platform}/disconnect")

    def oauth_test_credentials(self, platform: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/oauth/{platform}/test",
            data=b"",
            headers={"Content-Type": "application/json"},
        )

    def oauth_post_delete_test_credentials(self, platform: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/oauth/{platform}/post-delete-test",
            data=b"",
            headers={"Content-Type": "application/json"},
        )

    def get_profile(self) -> dict[str, Any]:
        return self._request("GET", "/workspace/profile")

    def update_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "PUT",
            "/workspace/profile",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def get_staged(self) -> dict[str, Any]:
        return self._request("GET", "/workspace/staged")

    def update_staged(self, upload_id: str | None, selected_platforms: list[str]) -> dict[str, Any]:
        payload = {"upload_id": upload_id, "selected_platforms": selected_platforms}
        return self._request(
            "PUT",
            "/workspace/staged",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def clear_staged(self) -> dict[str, Any]:
        return self._request("DELETE", "/workspace/staged")

    def publish_staged(self) -> list[dict[str, Any]]:
        return self._request("POST", "/workspace/publish", data=b"", headers={"Content-Type": "application/json"})

    def publish_now(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request(
            "POST",
            "/workspace/publish-now",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def register(self, email: str, password: str) -> dict[str, Any]:
        payload = json.dumps({"email": email, "password": password}).encode("utf-8")
        return self._request(
            "POST",
            "/auth/register",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

    def verify_email(self, email: str, code: str) -> dict[str, Any]:
        payload = json.dumps({"email": email, "code": code}).encode("utf-8")
        return self._request(
            "POST",
            "/auth/verify-email",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

    def get_app_settings(self) -> dict[str, Any]:
        return self._request("GET", "/workspace/app-settings") or {}

    def put_app_settings(self, settings_dict: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(settings_dict).encode("utf-8")
        return self._request(
            "PUT",
            "/workspace/app-settings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

    def upload_file(self, file_path: str) -> dict[str, Any]:
        boundary = f"----ReelPushBoundary{uuid.uuid4().hex}"
        filename = os.path.basename(file_path)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        file_bytes = Path(file_path).read_bytes()

        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

        return self._request(
            "POST",
            "/uploads/",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )


class ReelPushDesktop(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1180x860")
        self.minsize(960, 620)

        self.client = ApiClient()
        self.results_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_file_path: str | None = None
        self.selected_platform_vars: dict[str, tk.BooleanVar] = {}
        self.latest_platform_statuses: list[dict[str, Any]] = []
        self.latest_staged: dict[str, Any] = {}
        self.latest_runtime_statuses: dict[str, dict[str, Any]] = {}
        self.section_buttons: dict[str, dict[str, Any]] = {}
        self.section_frames: dict[str, tk.Frame] = {}
        self.platform_rows: dict[str, dict[str, Any]] = {}
        self.readiness_rows: dict[str, dict[str, Any]] = {}
        self.dropdown_fields: list[DropdownField] = []
        self.credential_field_rows: list[dict[str, Any]] = []
        self.cloud_app_settings: dict[str, Any] = {}
        self.desktop_settings = _load_desktop_settings()
        if self.desktop_settings.get("server_url"):
            _set_active_api_url(self.desktop_settings["server_url"])
        self.theme_key = self.desktop_settings.get("theme", DEFAULT_THEME)
        self.theme_key = LEGACY_THEME_MAP.get(self.theme_key, self.theme_key)
        if self.theme_key not in THEMES:
            self.theme_key = DEFAULT_THEME
        self.colors = THEMES[self.theme_key]["colors"]
        self.configure(bg=self.colors["bg"])

        self._build_styles()
        self.container = ttk.Frame(
            self,
            style="Shell.TFrame",
            padding=(SPACING["xxl"], SPACING["lg"], SPACING["xxl"], SPACING["lg"]),
        )
        self.container.pack(fill="both", expand=True)

        self.after(100, self._poll_results)
        if self.desktop_settings.get("server_url"):
            self.show_bootstrap()
        else:
            self.show_first_run()

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        colors = self.colors

        available_fonts = set(tkfont.families(self))
        body_family = self._pick_font_family(
            available_fonts,
            ["Avenir Next", "SF Pro Text", "Helvetica Neue", "TkDefaultFont"],
        )
        heading_family = self._pick_font_family(
            available_fonts,
            ["Avenir Next Condensed", "DIN Alternate", "Avenir Next", "SF Pro Display", body_family],
        )

        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family=body_family, size=TYPE_SCALE["body"][0])
        tkfont.nametofont("TkTextFont").configure(family=body_family, size=TYPE_SCALE["body"][0])
        tkfont.nametofont("TkMenuFont").configure(family=body_family, size=TYPE_SCALE["body"][0])
        tkfont.nametofont("TkHeadingFont").configure(family=heading_family, size=TYPE_SCALE["h2"][0], weight="bold")

        self.display_font = tkfont.Font(family=heading_family, size=TYPE_SCALE["display"][0], weight="bold")
        self.section_font = tkfont.Font(family=heading_family, size=TYPE_SCALE["h2"][0], weight="bold")
        self.heading_font = tkfont.Font(family=heading_family, size=TYPE_SCALE["h1"][0], weight="bold")
        self.subheading_font = tkfont.Font(family=body_family, size=TYPE_SCALE["body"][0])
        self.body_font = tkfont.Font(family=body_family, size=TYPE_SCALE["body"][0])
        self.strong_font = tkfont.Font(family=body_family, size=TYPE_SCALE["body_strong"][0], weight="bold")
        self.muted_font = tkfont.Font(family=body_family, size=TYPE_SCALE["small"][0])
        self.nav_font = tkfont.Font(family=body_family, size=TYPE_SCALE["body"][0], weight="bold")
        self.small_heading_font = tkfont.Font(family=heading_family, size=TYPE_SCALE["small"][0], weight="bold")
        self.meta_font = tkfont.Font(family=body_family, size=TYPE_SCALE["micro"][0], weight="bold")

        style.configure("Shell.TFrame", background=colors["bg"])
        style.configure("Panel.TFrame", background=colors["panel"])
        style.configure("SoftPanel.TFrame", background=colors["soft_panel"])
        style.configure("AccentPanel.TFrame", background=colors["accent_panel"])
        style.configure("Card.TFrame", background=colors["panel"])
        style.configure("Ribbon.TFrame", background=colors["bg"])
        style.configure("Tile.TFrame", background=colors["soft_panel"])
        style.configure("Card.TLabelframe", background=colors["panel"], foreground=colors["text"])
        style.configure("Card.TLabelframe.Label", background=colors["panel"], foreground=colors["text"], font=self.section_font)
        style.configure("Display.TLabel", background=colors["bg"], foreground=colors["text"], font=self.display_font)
        style.configure("HeroKicker.TLabel", background=colors["bg"], foreground=colors["accent"], font=self.meta_font)
        style.configure("Heading.TLabel", background=colors["bg"], foreground=colors["text"], font=self.heading_font)
        style.configure("Subheading.TLabel", background=colors["bg"], foreground=colors["muted"], font=self.subheading_font)
        style.configure("RibbonMuted.TLabel", background=colors["bg"], foreground=colors["subtle"], font=self.muted_font)
        style.configure("Workspace.TLabel", background=colors["bg"], foreground=colors["text"], font=self.body_font)
        style.configure("WorkspaceMuted.TLabel", background=colors["bg"], foreground=colors["muted"], font=self.muted_font)
        style.configure("AuthTitle.TLabel", background=colors["panel"], foreground=colors["text"], font=self.heading_font)
        style.configure("AuthSubtitle.TLabel", background=colors["panel"], foreground=colors["muted"], font=self.subheading_font)
        style.configure("CardTitle.TLabel", background=colors["panel"], foreground=colors["text"], font=self.section_font)
        style.configure("CardSubtitle.TLabel", background=colors["panel"], foreground=colors["muted"], font=self.muted_font)
        style.configure("Body.TLabel", background=colors["panel"], foreground=colors["text"], font=self.body_font)
        style.configure("Muted.TLabel", background=colors["panel"], foreground=colors["muted"], font=self.muted_font)
        style.configure("Micro.TLabel", background=colors["panel"], foreground=colors["subtle"], font=self.meta_font)
        style.configure("FieldHelp.TLabel", background=colors["panel"], foreground=colors["subtle"], font=self.muted_font)
        style.configure("PanelBody.TLabel", background=colors["soft_panel"], foreground=colors["text"], font=self.body_font)
        style.configure("PanelMuted.TLabel", background=colors["soft_panel"], foreground=colors["muted"], font=self.muted_font)
        style.configure("Good.TLabel", background=colors["panel"], foreground=colors["good"], font=self.body_font)
        style.configure("Warn.TLabel", background=colors["panel"], foreground=colors["warn"], font=self.body_font)
        style.configure("Bad.TLabel", background=colors["panel"], foreground=colors["bad"], font=self.body_font)
        style.configure("SmallGood.TLabel", background=colors["panel"], foreground=colors["good"], font=self.meta_font)
        style.configure("SmallWarn.TLabel", background=colors["panel"], foreground=colors["warn"], font=self.meta_font)
        style.configure("SmallBad.TLabel", background=colors["panel"], foreground=colors["bad"], font=self.meta_font)
        style.configure("PanelGood.TLabel", background=colors["soft_panel"], foreground=colors["good"], font=self.body_font)
        style.configure("PanelWarn.TLabel", background=colors["soft_panel"], foreground=colors["warn"], font=self.body_font)
        style.configure("PanelBad.TLabel", background=colors["soft_panel"], foreground=colors["bad"], font=self.body_font)
        style.configure("PanelSmallGood.TLabel", background=colors["soft_panel"], foreground=colors["good"], font=self.meta_font)
        style.configure("PanelSmallWarn.TLabel", background=colors["soft_panel"], foreground=colors["warn"], font=self.meta_font)
        style.configure("PanelSmallBad.TLabel", background=colors["soft_panel"], foreground=colors["bad"], font=self.meta_font)
        style.configure("SelectedPanelBody.TLabel", background=colors["accent_panel"], foreground=colors["text"], font=self.body_font)
        style.configure("SelectedPanelMuted.TLabel", background=colors["accent_panel"], foreground=colors["muted"], font=self.muted_font)
        style.configure("SelectedPanelSmallGood.TLabel", background=colors["accent_panel"], foreground=colors["good"], font=self.meta_font)
        style.configure("SelectedPanelSmallWarn.TLabel", background=colors["accent_panel"], foreground=colors["warn"], font=self.meta_font)
        style.configure("SelectedPanelSmallBad.TLabel", background=colors["accent_panel"], foreground=colors["bad"], font=self.meta_font)
        button_padding = (SPACING["lg"], SPACING["sm"])
        accent_padding = (SPACING["xl"], SPACING["sm"])
        style.configure(
            "Accent.TButton",
            background=colors["accent"],
            foreground="#FAF7F4",
            borderwidth=0,
            bordercolor=colors["accent"],
            focusthickness=0,
            relief="flat",
            padding=accent_padding,
            font=self.nav_font,
        )
        style.map(
            "Accent.TButton",
            background=[("disabled", colors["disabled"]), ("pressed", colors["accent"]), ("active", colors["accent_hover"])],
            bordercolor=[("disabled", colors["border_soft"]), ("active", colors["accent_hover"])],
            foreground=[("disabled", colors["disabled_text"])],
        )
        style.configure(
            "Secondary.TButton",
            background=colors["secondary"],
            foreground=colors["text"],
            borderwidth=1,
            bordercolor=colors["border"],
            focusthickness=0,
            relief="flat",
            padding=button_padding,
            font=self.nav_font,
        )
        style.map(
            "Secondary.TButton",
            background=[("disabled", colors["disabled"]), ("pressed", colors["secondary"]), ("active", colors["secondary_hover"])],
            bordercolor=[("active", colors["focus"]), ("disabled", colors["border_soft"])],
            foreground=[("disabled", colors["disabled_text"])],
        )
        style.configure(
            "Tertiary.TButton",
            background=colors["soft_panel"],
            foreground=colors["muted"],
            borderwidth=0,
            bordercolor=colors["soft_panel"],
            focusthickness=0,
            relief="flat",
            padding=button_padding,
            font=self.nav_font,
        )
        style.map(
            "Tertiary.TButton",
            background=[("disabled", colors["disabled"]), ("pressed", colors["panel"]), ("active", colors["soft_panel"])],
            bordercolor=[("active", colors["soft_panel"]), ("disabled", colors["border_soft"])],
            foreground=[("disabled", colors["disabled_text"]), ("active", colors["text"])],
        )
        style.configure("Nav.TButton", background=colors["bg"], foreground=colors["muted"], borderwidth=0, padding=(SPACING["lg"], SPACING["sm"]), font=self.nav_font)
        style.map("Nav.TButton", background=[("active", colors["soft_panel"])], foreground=[("active", colors["text"])])
        style.configure("Nav.Active.TButton", background=colors["accent_panel"], foreground=colors["text"], borderwidth=0, padding=(SPACING["lg"], SPACING["sm"]), font=self.nav_font)
        style.map("Nav.Active.TButton", background=[("active", colors["accent_panel"])], foreground=[("active", colors["text"])])
        style.configure(
            "TEntry",
            fieldbackground=colors["field"],
            foreground=colors["text"],
            insertcolor=colors["text"],
            borderwidth=0,
            relief="flat",
            padding=(SPACING["md"], SPACING["sm"]),
            font=self.body_font,
        )
        style.map(
            "TEntry",
            fieldbackground=[("disabled", colors["disabled"]), ("focus", colors["field"])],
            foreground=[("disabled", colors["disabled_text"])],
        )
        style.configure(
            "TCombobox",
            fieldbackground=colors["field"],
            background=colors["field"],
            foreground=colors["text"],
            arrowcolor=colors["muted"],
            borderwidth=0,
            relief="flat",
            padding=(SPACING["md"], SPACING["sm"]),
            font=self.body_font,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["field"]), ("disabled", colors["disabled"])],
            foreground=[("readonly", colors["text"]), ("disabled", colors["disabled_text"])],
            arrowcolor=[("active", colors["text"]), ("disabled", colors["disabled_text"])],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=colors["secondary"],
            troughcolor=colors["bg"],
            borderwidth=0,
            arrowsize=8,
            width=10,
        )
        style.configure("TCheckbutton", background=colors["panel"], foreground=colors["text"])
        style.map("TCheckbutton", background=[("active", colors["panel"])], foreground=[("active", colors["text"])])
        style.configure("Panel.TCheckbutton", background=colors["soft_panel"], foreground=colors["text"])
        style.map("Panel.TCheckbutton", background=[("active", colors["soft_panel"])], foreground=[("active", colors["text"])])
        style.configure("TRadiobutton", background=colors["panel"], foreground=colors["text"], font=self.body_font)
        style.map(
            "TRadiobutton",
            background=[("active", colors["panel"]), ("selected", colors["panel"])],
            foreground=[("active", colors["text"]), ("disabled", colors["disabled_text"])],
        )
        style.configure("Accent.Horizontal.TProgressbar", troughcolor=colors["soft_panel"], background=colors["accent"])

    @staticmethod
    def _pick_font_family(available_fonts: set[str], candidates: list[str]) -> str:
        for family in candidates:
            if family in available_fonts:
                return family
        return "TkDefaultFont"

    def _clear_container(self) -> None:
        for child in self.container.winfo_children():
            child.destroy()

    def _poll_results(self) -> None:
        try:
            while True:
                action, payload = self.results_queue.get_nowait()
                if action == "first-run-ok":
                    handler = getattr(self, "_first_run_ok_handler", None)
                    if handler:
                        handler(payload)
                elif action == "first-run-error":
                    handler = getattr(self, "_first_run_error_handler", None)
                    if handler:
                        handler(payload)
                elif action == "bootstrap-ok":
                    self.auto_login()
                elif action == "bootstrap-error":
                    messagebox.showerror("Startup failed", payload)
                elif action == "login-ok":
                    self.show_main(payload)
                elif action == "auto-login-error":
                    self.show_login(str(payload))
                elif action == "login-error":
                    messagebox.showerror("Login failed", payload)
                elif action == "register-ok":
                    self.show_verify_email(payload)
                elif action == "register-error":
                    if hasattr(self, "reg_error_var"):
                        self.reg_error_var.set(payload)
                    else:
                        messagebox.showerror("Registration failed", payload)
                elif action == "verify-error":
                    if hasattr(self, "verify_error_var"):
                        self.verify_error_var.set(payload)
                    else:
                        messagebox.showerror("Verification failed", payload)
                elif action == "data-loaded":
                    self._apply_workspace_data(payload)
                elif action == "data-error":
                    messagebox.showerror("ReelPush", payload)
                elif action == "profile-saved":
                    self.status_var.set("Publishing details saved.")
                    self._wi_success("Draft saved.")
                    self._update_character_counts()
                    self._refresh_delivery_readiness()
                elif action == "stage-saved":
                    self.latest_staged = payload
                    self.status_var.set("Staged publish saved.")
                    self._wi_success("Stage saved.")
                    self._refresh_delivery_readiness()
                elif action == "uploaded":
                    upload = payload
                    self.staged_upload = upload
                    self.current_file_path = upload["original_filename"]
                    self._update_video_preview()
                    self.status_var.set("Video uploaded. Save stage when ready.")
                    self._wi_success("Video uploaded.")
                    self._refresh_delivery_readiness()
                elif action == "published":
                    self._show_publish_results(payload)
                    self._wi_success("Publish complete.")
                elif action == "operation-error":
                    messagebox.showerror("ReelPush", payload)
                    self.status_var.set(payload)
                    self._wi_error(payload)
                elif action == "credential-test-ok":
                    platform = payload["platform"]
                    row = self.account_rows.get(platform)
                    if row:
                        row["test"].configure(state="normal")
                        row["test_result"].set(payload["message"])
                        row["test_result_label"].configure(style="PanelGood.TLabel")
                    credential_button = self.credential_test_buttons.get(platform)
                    if credential_button:
                        credential_button.configure(state="normal")
                    credential_status = self.credential_test_status_vars.get(platform)
                    credential_status_label = self.credential_test_status_labels.get(platform)
                    if credential_status and credential_status_label:
                        credential_status.set(payload["message"])
                        credential_status_label.configure(style="Good.TLabel")
                    self.status_var.set(payload["message"])
                    self._wi_success(f"{platform.title()}: verified.")
                    self.load_workspace_data()
                elif action == "credential-test-error":
                    platform = payload["platform"]
                    row = self.account_rows.get(platform)
                    if row:
                        row["test"].configure(state="normal")
                        row["test_result"].set(payload["message"])
                        row["test_result_label"].configure(style="PanelBad.TLabel")
                    credential_button = self.credential_test_buttons.get(platform)
                    if credential_button:
                        credential_button.configure(state="normal")
                    credential_status = self.credential_test_status_vars.get(platform)
                    credential_status_label = self.credential_test_status_labels.get(platform)
                    if credential_status and credential_status_label:
                        credential_status.set(payload["message"])
                        credential_status_label.configure(style="Bad.TLabel")
                    self.status_var.set(f"{platform.title()} credential test failed.")
                    self._wi_error(f"{platform.title()}: {payload['message']}")
                    self.load_workspace_data()
                elif action == "server-connection-tested":
                    ok = payload.get("ok", False)
                    email = payload.get("email")
                    if ok:
                        msg = f"Connected as {email}." if email else "Server reachable — add credentials to sign in."
                        self.server_conn_status_var.set(msg)
                    else:
                        self.server_conn_status_var.set(payload.get("error", "Connection failed."))
                    if hasattr(self, "server_save_button"):
                        self.server_save_button.configure(state="normal")
                elif action == "cred-save-ok":
                    if hasattr(self, "credentials_status_var"):
                        self.credentials_status_var.set("Credentials saved to cloud.")
                elif action == "cred-save-error":
                    if hasattr(self, "credentials_status_var"):
                        self.credentials_status_var.set(f"Save failed: {payload}")
                elif action == "cred-load-ok":
                    if hasattr(self, "credentials_status_var"):
                        self._apply_cloud_credentials(payload)
                        self.credentials_status_var.set("Credentials loaded from cloud.")
                elif action == "cred-load-error":
                    if hasattr(self, "credentials_status_var"):
                        self.credentials_status_var.set(f"Load failed: {payload}")
        except queue.Empty:
            pass
        self.after(100, self._poll_results)

    def _run_bg(self, work, success_action: str | None = None) -> None:
        def runner() -> None:
            try:
                result = self._call_with_reauth(work)
                if success_action:
                    self.results_queue.put((success_action, result))
            except Exception as exc:  # noqa: BLE001
                self.results_queue.put(("operation-error", str(exc)))

        threading.Thread(target=runner, daemon=True).start()

    # ─── Working indicator ────────────────────────────────────────────────────

    def _wi_show(self, msg: str) -> None:
        """Show or update the transient working indicator in the top-right corner."""
        if hasattr(self, "_wi_hide_id"):
            self.after_cancel(self._wi_hide_id)
            del self._wi_hide_id
        c = self.colors
        if not hasattr(self, "_wi_frame"):
            frame = tk.Frame(self, bg=c["secondary"], padx=12, pady=7)
            self._wi_var = tk.StringVar()
            self._wi_label = tk.Label(
                frame,
                textvariable=self._wi_var,
                bg=c["secondary"],
                fg=c["muted"],
                font=self.muted_font,
            )
            self._wi_label.pack()
            self._wi_frame = frame
        self._wi_var.set(msg)
        self._wi_frame.configure(bg=c["secondary"])
        self._wi_label.configure(bg=c["secondary"], fg=c["muted"])
        self._wi_frame.place(relx=1.0, rely=0.0, anchor="ne", x=-16, y=16)
        self._wi_frame.lift()

    def _wi_success(self, msg: str) -> None:
        """Briefly show a success message then auto-hide the indicator."""
        if not hasattr(self, "_wi_frame"):
            return
        if hasattr(self, "_wi_hide_id"):
            self.after_cancel(self._wi_hide_id)
        c = self.colors
        self._wi_var.set(msg)
        self._wi_frame.configure(bg=c["accent_panel"])
        self._wi_label.configure(bg=c["accent_panel"], fg=c["good"])
        self._wi_frame.place(relx=1.0, rely=0.0, anchor="ne", x=-16, y=16)
        self._wi_frame.lift()
        self._wi_hide_id = self.after(2500, self._wi_hide)

    def _wi_error(self, msg: str) -> None:
        """Show an error in the indicator. Auto-hides after 8 seconds."""
        if not hasattr(self, "_wi_frame"):
            return
        if hasattr(self, "_wi_hide_id"):
            self.after_cancel(self._wi_hide_id)
        c = self.colors
        display = msg[:80] + ("…" if len(msg) > 80 else "")
        self._wi_var.set(display)
        self._wi_frame.configure(bg=c["accent_panel"])
        self._wi_label.configure(bg=c["accent_panel"], fg=c["bad"])
        self._wi_frame.place(relx=1.0, rely=0.0, anchor="ne", x=-16, y=16)
        self._wi_frame.lift()
        self._wi_hide_id = self.after(8000, self._wi_hide)

    def _wi_hide(self) -> None:
        """Hide the working indicator."""
        if hasattr(self, "_wi_frame"):
            self._wi_frame.place_forget()
        if hasattr(self, "_wi_hide_id"):
            del self._wi_hide_id

    # ──────────────────────────────────────────────────────────────────────────

    def _call_with_reauth(self, work):
        try:
            return work()
        except AuthExpiredError:
            email = self.desktop_settings.get("login_email", "")
            password = self.desktop_settings.get("login_password", "")
            if not email or not password:
                raise RuntimeError("Session expired. Sign in again, then retry.") from None
            self.client.login(email, password)
            return work()

    def _collect_runtime_statuses(self) -> dict[str, dict[str, Any]]:
        statuses = {
            key: {"ok": False, "label": "Offline", "detail": "Not checked yet."}
            for key in LOCAL_SERVICES
        }

        try:
            with request.urlopen(f"{API_BASE}/health", timeout=2) as response:
                statuses["api"] = {
                    "ok": response.status == 200,
                    "label": "Online" if response.status == 200 else f"HTTP {response.status}",
                    "detail": API_BASE,
                }
        except Exception as exc:  # noqa: BLE001
            statuses["api"] = {"ok": False, "label": "Offline", "detail": str(exc)}

        try:
            with request.urlopen("https://www.gstatic.com/generate_204", timeout=3) as response:
                online = response.status in (200, 204)
                statuses["internet"] = {
                    "ok": online,
                    "label": "Online" if online else f"HTTP {response.status}",
                    "detail": "External network check",
                }
        except Exception as exc:  # noqa: BLE001
            statuses["internet"] = {"ok": False, "label": "Offline", "detail": str(exc)}

        return statuses

    def _runtime_publish_blockers(self, statuses: dict[str, dict[str, Any]]) -> list[str]:
        api = statuses.get("api", {})
        internet = statuses.get("internet", {})
        blockers: list[str] = []
        if not internet.get("ok"):
            detail = str(internet.get("detail") or "No internet connection detected.").replace("\n", " ")
            blockers.append(f"Network unavailable: {detail}")
        if not api.get("ok"):
            detail = str(api.get("detail") or f"Could not reach ReelPush API at {API_BASE}.").replace("\n", " ")
            blockers.append(f"Server unavailable: {detail}")
        return blockers

    @staticmethod
    def _env_value(name: str) -> str:
        return LOCAL_ENV.get(name) or os.environ.get(name, "")

    def _platform_diagnostics(self, status: dict[str, Any]) -> dict[str, Any]:
        platform = status["platform"]
        details = PLATFORM_DETAILS.get(platform, {"name": platform.title(), "env": (), "fyi": ""})
        missing_env = list(status.get("missing_credentials") or [name for name in details["env"] if not self._env_value(name)])
        connected = bool(status.get("connected"))
        configured = bool(status.get("configured"))
        pending_approval = bool(status.get("pending_approval"))
        credential_status = str(status.get("credential_status") or "")
        backend_detail = status.get("credential_detail")
        next_action = status.get("next_action")

        if credential_status:
            if credential_status == "verified":
                readiness = "Credentials working"
                status_badge = "Verified"
                tone = "good"
                detail_text = backend_detail or "Credentials and connected account verified."
            elif credential_status == "warning":
                readiness = "Working with warning"
                status_badge = "Warning"
                tone = "warn"
                detail_text = backend_detail or "Credentials are usable, but the platform may still require approval."
            elif credential_status == "configured":
                readiness = "Connection required"
                status_badge = "Connect account"
                tone = "warn"
                detail_text = backend_detail or "App credentials are present. Connect an account to verify posting access."
            elif credential_status == "connected":
                readiness = "Connected"
                status_badge = "Connected"
                tone = "warn"
                detail_text = backend_detail or "Connected account status is still being verified."
            elif credential_status == "invalid":
                readiness = "Credentials not working"
                status_badge = "Fix required"
                tone = "bad"
                detail_text = backend_detail or "Connected account could not be verified."
            else:
                readiness = "Credentials required"
                status_badge = "Missing"
                tone = "bad"
                detail_text = backend_detail or f"Missing environment variables: {', '.join(missing_env or details['env'])}."
            if next_action:
                detail_text = f"{detail_text} {next_action}"
            can_publish_now = bool(status.get("can_publish", False))
        else:
            blockers: list[str] = []
            if missing_env or not configured:
                blockers.append(f"Missing environment variables: {', '.join(missing_env or details['env'])}.")
            elif not connected:
                blockers.append("Use Connect to link a platform account when you are ready to publish.")
            if pending_approval and platform in {"instagram", "tiktok"}:
                blockers.append("Platform app review or publishing approval may be required before direct posting works.")

            if not blockers:
                readiness = "Ready to send now"
                status_badge = "Verified"
                tone = "good"
                detail_text = "Credentials are present and an account is connected."
            elif connected and configured and pending_approval:
                readiness = "Ready to attempt, approval-sensitive"
                status_badge = "Warning"
                tone = "warn"
                detail_text = " ".join(blockers)
            else:
                readiness = "Not ready to publish"
                status_badge = "Missing"
                tone = "bad"
                detail_text = " ".join(blockers)
            can_publish_now = connected and configured

        account = status.get("account") or {}
        username = account.get("platform_username")
        account_text = f"✓ Connected as {username}" if connected and username else ("✓ Connected" if connected else "Not connected")

        return {
            "platform": platform,
            "name": details["name"],
            "account": account_text,
            "readiness": readiness,
            "status_badge": status_badge,
            "detail": detail_text,
            "fyi": details["fyi"],
            "tone": tone,
            "can_publish_now": can_publish_now,
            "can_connect": configured and not connected,
        }

    @staticmethod
    def _tone_style(tone: str, *, panel: bool = False) -> str:
        prefix = "Panel" if panel else ""
        if tone == "good":
            return f"{prefix}Good.TLabel"
        if tone == "warn":
            return f"{prefix}Warn.TLabel"
        return f"{prefix}Bad.TLabel"

    def _set_field_focus(self, surface: RoundedFrame | None, focused: bool) -> None:
        if surface is None:
            return
        setattr(surface, "_rp_focused", focused)
        self._apply_field_outline(surface)

    def _set_field_hover(self, surface: RoundedFrame | None, hovered: bool) -> None:
        if surface is None:
            return
        setattr(surface, "_rp_hovered", hovered)
        self._apply_field_outline(surface)

    def _set_field_error(self, surface: RoundedFrame | None, errored: bool) -> None:
        if surface is None:
            return
        setattr(surface, "_rp_errored", errored)
        self._apply_field_outline(surface)

    def _apply_field_outline(self, surface: RoundedFrame) -> None:
        if getattr(surface, "_rp_errored", False):
            surface.set_outline(self.colors["bad"])
        elif getattr(surface, "_rp_focused", False):
            surface.set_outline(self.colors["accent"])
        elif getattr(surface, "_rp_hovered", False):
            surface.set_outline(self.colors["focus"])
        else:
            surface.set_outline(self.colors["border"])

    def _install_entry_placeholder(
        self,
        entry: tk.Entry,
        placeholder: str,
        surface: RoundedFrame | None = None,
    ) -> None:
        setattr(entry, "_rp_placeholder", placeholder)
        setattr(entry, "_rp_placeholder_active", False)

        def focus_in(_event=None) -> None:
            self._set_field_focus(surface, True)
            if getattr(entry, "_rp_placeholder_active", False):
                entry.delete(0, "end")
                entry.configure(fg=self.colors["text"])
                setattr(entry, "_rp_placeholder_active", False)

        def focus_out(_event=None) -> None:
            self._set_field_focus(surface, False)
            if not entry.get().strip():
                self._set_entry_value(entry, "")

        entry.bind("<FocusIn>", focus_in, add="+")
        entry.bind("<FocusOut>", focus_out, add="+")
        self._set_entry_value(entry, "")

    def _set_entry_value(self, entry: tk.Entry, value: str | None) -> None:
        placeholder = getattr(entry, "_rp_placeholder", "")
        entry.delete(0, "end")
        if value:
            entry.configure(fg=self.colors["text"])
            entry.insert(0, value)
            setattr(entry, "_rp_placeholder_active", False)
        elif placeholder:
            entry.configure(fg=self.colors["muted"])
            entry.insert(0, placeholder)
            setattr(entry, "_rp_placeholder_active", True)
        else:
            setattr(entry, "_rp_placeholder_active", False)

    def _entry_value(self, entry: tk.Entry) -> str:
        if getattr(entry, "_rp_placeholder_active", False):
            return ""
        return entry.get().strip()

    def _install_text_placeholder(
        self,
        text: tk.Text,
        placeholder: str,
        surface: RoundedFrame | None = None,
    ) -> None:
        setattr(text, "_rp_placeholder", placeholder)
        setattr(text, "_rp_placeholder_active", False)

        def focus_in(_event=None) -> None:
            self._set_field_focus(surface, True)
            if getattr(text, "_rp_placeholder_active", False):
                text.delete("1.0", "end")
                text.configure(fg=self.colors["text"])
                setattr(text, "_rp_placeholder_active", False)

        def focus_out(_event=None) -> None:
            self._set_field_focus(surface, False)
            if not text.get("1.0", "end-1c").strip():
                self._set_text_value(text, "")

        text.bind("<FocusIn>", focus_in, add="+")
        text.bind("<FocusOut>", focus_out, add="+")
        self._set_text_value(text, "")

    def _set_text_value(self, text: tk.Text, value: str | None) -> None:
        placeholder = getattr(text, "_rp_placeholder", "")
        text.delete("1.0", "end")
        if value:
            text.configure(fg=self.colors["text"])
            text.insert("1.0", value)
            setattr(text, "_rp_placeholder_active", False)
        elif placeholder:
            text.configure(fg=self.colors["muted"])
            text.insert("1.0", placeholder)
            setattr(text, "_rp_placeholder_active", True)
        else:
            setattr(text, "_rp_placeholder_active", False)

    def _text_value(self, text: tk.Text) -> str:
        if getattr(text, "_rp_placeholder_active", False):
            return ""
        return text.get("1.0", "end").strip()

    def _make_entry_field(
        self,
        parent,
        *,
        show: str = "",
        placeholder: str = "",
        textvariable: tk.StringVar | None = None,
        bg_color: str | None = None,
    ) -> tuple[RoundedFrame, tk.Entry]:
        surface = RoundedFrame(
            parent,
            bg_color=bg_color or self.colors["panel"],
            fill_color=self.colors["field"],
            radius=RADIUS_INPUT,
            padding=SPACING["xs"],
            outline_color=self.colors["border"],
        )
        entry = tk.Entry(
            surface.body,
            show=show,
            bg=self.colors["field"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            disabledbackground=self.colors["disabled"],
            disabledforeground=self.colors["disabled_text"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=self.body_font,
        )
        if textvariable is not None:
            entry.config(textvariable=textvariable)
        entry.pack(fill="x", padx=PADDING_FIELD_X, pady=PADDING_FIELD_Y)
        entry.bind("<FocusIn>", lambda _event: self._set_field_focus(surface, True), add="+")
        entry.bind("<FocusOut>", lambda _event: self._set_field_focus(surface, False), add="+")
        for widget in (surface, surface.canvas, surface.body, entry):
            widget.bind("<Enter>", lambda _event, field=surface: self._set_field_hover(field, True), add="+")
            widget.bind("<Leave>", lambda _event, field=surface: self._set_field_hover(field, False), add="+")
        if placeholder:
            self._install_entry_placeholder(entry, placeholder, surface)
        return surface, entry

    def _make_dropdown_field(
        self,
        parent,
        *,
        variable: tk.StringVar,
        values: list[str],
        command=None,
        bg_color: str | None = None,
    ) -> DropdownField:
        field = DropdownField(
            parent,
            colors={**self.colors, "panel": bg_color or self.colors["panel"]},
            variable=variable,
            values=values,
            font=self.body_font,
            command=command,
        )
        self.dropdown_fields.append(field)
        return field

    def _make_center_panel(self) -> tk.Frame:
        surface = RoundedFrame(
            self.container,
            bg_color=self.colors["bg"],
            fill_color=self.colors["panel"],
            radius=RADIUS_PANEL,
            padding=SPACING["xxl"],
            outline_color=self.colors["border_soft"],
            shadow=False,
        )
        surface.place(relx=0.5, rely=0.5, anchor="center")
        return surface.body

    def show_bootstrap(self) -> None:
        self._clear_container()

        panel = self._make_center_panel()

        ttk.Label(panel, text=APP_NAME, style="AuthTitle.TLabel").pack(anchor="w")
        ttk.Label(
            panel,
            text="Starting services and preparing your workspace.",
            style="AuthSubtitle.TLabel",
        ).pack(anchor="w", pady=(SPACING["sm"], SPACING["xl"]))

        self.bootstrap_status = tk.StringVar(value="Starting services…")
        ttk.Label(panel, textvariable=self.bootstrap_status, style="Body.TLabel").pack(anchor="w", pady=(0, SPACING["md"]))

        progress = ttk.Progressbar(panel, mode="indeterminate", length=420, style="Accent.Horizontal.TProgressbar")
        progress.pack(anchor="w")
        progress.start(10)

        def bootstrap() -> None:
            try:
                self.after(0, lambda: self.bootstrap_status.set("Connecting to server…"))
                deadline = time.time() + 15
                while time.time() < deadline:
                    try:
                        with request.urlopen(f"{API_BASE}/health", timeout=3) as response:
                            if response.status == 200:
                                self.results_queue.put(("bootstrap-ok", None))
                                return
                    except Exception:  # noqa: BLE001
                        time.sleep(1)
                self.results_queue.put((
                    "bootstrap-error",
                    f"Cannot reach {API_ROOT}.\n\nCheck the Server URL in Settings, or confirm the server is running.",
                ))
            except Exception as exc:  # noqa: BLE001
                self.results_queue.put(("bootstrap-error", str(exc)))

        threading.Thread(target=bootstrap, daemon=True).start()

    def show_first_run(self) -> None:
        self._clear_container()

        panel = self._make_center_panel()

        ttk.Label(panel, text="Welcome to ReelPush", style="AuthTitle.TLabel").pack(anchor="w")
        ttk.Label(
            panel,
            text="Sign in with your ReelPush account.",
            style="AuthSubtitle.TLabel",
        ).pack(anchor="w", pady=(SPACING["sm"], SPACING["xl"]))

        creds_row = ttk.Frame(panel, style="Panel.TFrame")
        creds_row.pack(fill="x")
        creds_row.columnconfigure(0, weight=1)
        creds_row.columnconfigure(1, weight=1)

        email_col = ttk.Frame(creds_row, style="Panel.TFrame")
        email_col.grid(row=0, column=0, sticky="ew", padx=(0, SPACING["md"]))
        ttk.Label(email_col, text="YOUR EMAIL", style="Micro.TLabel").pack(anchor="w")
        email_surface, email_entry = self._make_entry_field(email_col, bg_color=self.colors["panel"])
        email_surface.pack(fill="x", pady=(4, 0))

        pw_col = ttk.Frame(creds_row, style="Panel.TFrame")
        pw_col.grid(row=0, column=1, sticky="ew")
        ttk.Label(pw_col, text="YOUR PASSWORD", style="Micro.TLabel").pack(anchor="w")
        pw_surface, pw_entry = self._make_entry_field(pw_col, show="*", bg_color=self.colors["panel"])
        pw_surface.pack(fill="x", pady=(4, 0))

        ttk.Label(
            panel,
            text="Credentials are saved locally on this device only.",
            style="FieldHelp.TLabel",
        ).pack(anchor="w", pady=(SPACING["xs"], SPACING["md"]))

        status_var = tk.StringVar(value="")
        connect_button: list[ttk.Button] = []

        def do_connect() -> None:
            url = OFFICIAL_SERVER_URL
            email = email_entry.get().strip()
            password = pw_entry.get().strip()
            status_var.set("Connecting…")
            if connect_button:
                connect_button[0].configure(state="disabled")

            def work() -> dict[str, Any]:
                return self._do_test_server_connection(url, email, password)

            def runner() -> None:
                result = work()
                ok = result.get("ok", False)
                if ok:
                    self.desktop_settings["server_url"] = url
                    self.desktop_settings["login_email"] = email
                    self.desktop_settings["login_password"] = password
                    _save_desktop_settings(self.desktop_settings)
                    _set_active_api_url(url)
                    self.results_queue.put(("first-run-ok", None))
                else:
                    self.results_queue.put(("first-run-error", result.get("error", "Connection failed.")))

            threading.Thread(target=runner, daemon=True).start()

        first_run_actions = ttk.Frame(panel, style="Panel.TFrame")
        first_run_actions.pack(anchor="w")
        btn = ttk.Button(first_run_actions, text="Connect", style="Accent.TButton", command=do_connect)
        btn.pack(side="left")
        connect_button.append(btn)

        def do_first_run_register() -> None:
            _set_active_api_url(OFFICIAL_SERVER_URL)
            self.desktop_settings["server_url"] = OFFICIAL_SERVER_URL
            _save_desktop_settings(self.desktop_settings)
            self.show_register()

        ttk.Button(first_run_actions, text="Create Account", style="Secondary.TButton", command=do_first_run_register).pack(side="left", padx=(SPACING["md"], 0))

        status_label = ttk.Label(panel, textvariable=status_var, style="Muted.TLabel", wraplength=420)
        status_label.pack(anchor="w", pady=(SPACING["md"], 0))

        def on_first_run_ok(_payload: Any) -> None:
            status_label.destroy()
            self.show_bootstrap()

        def on_first_run_error(message: str) -> None:
            status_var.set(message)
            if connect_button:
                connect_button[0].configure(state="normal")

        self._first_run_ok_handler = on_first_run_ok
        self._first_run_error_handler = on_first_run_error

    def auto_login(self) -> None:
        self.bootstrap_status.set("Connecting to workspace…")
        email = self.desktop_settings.get("login_email", "")
        password = self.desktop_settings.get("login_password", "")
        if not email or not password:
            self.show_login()
            return

        def work() -> dict[str, str]:
            self.client.login(email, password)
            try:
                self.cloud_app_settings = self.client.get_app_settings()
            except Exception:
                self.cloud_app_settings = {}
            return {"email": email}

        def runner() -> None:
            try:
                self.results_queue.put(("login-ok", work()))
            except Exception as exc:  # noqa: BLE001
                self.results_queue.put(("auto-login-error", str(exc)))

        threading.Thread(target=runner, daemon=True).start()

    def show_login(self, error_message: str | None = None) -> None:
        self._clear_container()

        panel = self._make_center_panel()

        login_email_default = self.desktop_settings.get("login_email", "")
        login_password_default = self.desktop_settings.get("login_password", "")
        subtitle = "Sign in to your ReelPush account."
        if error_message:
            subtitle = "Sign in failed. Check your credentials and try again."

        ttk.Label(panel, text="Sign in", style="AuthTitle.TLabel").pack(anchor="w")
        ttk.Label(panel, text=subtitle, style="AuthSubtitle.TLabel", wraplength=420).pack(anchor="w", pady=(SPACING["sm"], SPACING["xl"]))
        if error_message:
            error_var = tk.StringVar(value=error_message)
            ttk.Label(panel, textvariable=error_var, style="Muted.TLabel", wraplength=420).pack(anchor="w", pady=(0, SPACING["xl"]))

        ttk.Label(panel, text="Email", style="Body.TLabel").pack(anchor="w")
        login_email_surface, self.login_email = self._make_entry_field(panel, bg_color=self.colors["panel"])
        self.login_email.insert(0, login_email_default)
        login_email_surface.pack(anchor="w", fill="x", pady=(SPACING["xs"], SPACING["xs"]))
        ttk.Label(panel, text="Your account on this ReelPush server.", style="FieldHelp.TLabel").pack(anchor="w", pady=(0, SPACING["md"]))

        ttk.Label(panel, text="Password", style="Body.TLabel").pack(anchor="w")
        login_password_surface, self.login_password = self._make_entry_field(panel, show="*", bg_color=self.colors["panel"])
        self.login_password.insert(0, login_password_default)
        login_password_surface.pack(anchor="w", fill="x", pady=(SPACING["xs"], SPACING["xs"]))
        ttk.Label(panel, text="Your account password.", style="FieldHelp.TLabel").pack(anchor="w", pady=(0, SPACING["xl"]))

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.pack(anchor="w")

        def do_login() -> None:
            email = self.login_email.get().strip()
            password = self.login_password.get().strip()

            def work() -> dict[str, str]:
                self.client.login(email, password)
                try:
                    self.cloud_app_settings = self.client.get_app_settings()
                except Exception:
                    self.cloud_app_settings = {}
                return {"email": email}

            threading.Thread(
                target=lambda: self._login_worker(work),
                daemon=True,
            ).start()

        def do_register() -> None:
            self.show_register()

        ttk.Button(actions, text="Open Workspace", style="Accent.TButton", command=do_login).pack(side="left")
        ttk.Button(actions, text="Create Account", style="Secondary.TButton", command=do_register).pack(side="left", padx=(SPACING["md"], 0))

    def _login_worker(self, work) -> None:
        try:
            result = work()
            self.results_queue.put(("login-ok", result))
        except Exception as exc:  # noqa: BLE001
            self.results_queue.put(("login-error", str(exc)))

    def show_register(self) -> None:
        self._clear_container()

        panel = self._make_center_panel()

        ttk.Label(panel, text="Create Account", style="AuthTitle.TLabel").pack(anchor="w")
        ttk.Label(panel, text="Register a new account on this ReelPush server.", style="AuthSubtitle.TLabel", wraplength=420).pack(anchor="w", pady=(SPACING["sm"], SPACING["xl"]))

        self.reg_error_var = tk.StringVar(value="")
        self.reg_error_label = ttk.Label(panel, textvariable=self.reg_error_var, style="Muted.TLabel", wraplength=420)
        self.reg_error_label.pack(anchor="w")

        ttk.Label(panel, text="Email", style="Body.TLabel").pack(anchor="w")
        reg_email_surface, self.reg_email = self._make_entry_field(panel, bg_color=self.colors["panel"])
        reg_email_surface.pack(anchor="w", fill="x", pady=(SPACING["xs"], SPACING["md"]))

        ttk.Label(panel, text="Password", style="Body.TLabel").pack(anchor="w")
        reg_pw_surface, self.reg_password = self._make_entry_field(panel, show="*", bg_color=self.colors["panel"])
        reg_pw_surface.pack(anchor="w", fill="x", pady=(SPACING["xs"], SPACING["xs"]))
        ttk.Label(panel, text="Minimum 8 characters.", style="FieldHelp.TLabel").pack(anchor="w", pady=(0, SPACING["md"]))

        ttk.Label(panel, text="Confirm Password", style="Body.TLabel").pack(anchor="w")
        reg_cpw_surface, self.reg_confirm_password = self._make_entry_field(panel, show="*", bg_color=self.colors["panel"])
        reg_cpw_surface.pack(anchor="w", fill="x", pady=(SPACING["xs"], SPACING["xl"]))

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.pack(anchor="w")

        def do_submit() -> None:
            email = self.reg_email.get().strip()
            password = self.reg_password.get()
            confirm = self.reg_confirm_password.get()
            self.reg_error_var.set("")

            if not email or not password:
                self.reg_error_var.set("Email and password are required.")
                return
            if password != confirm:
                self.reg_error_var.set("Passwords do not match.")
                return
            if len(password) < 8:
                self.reg_error_var.set("Password must be at least 8 characters.")
                return

            def work() -> dict:
                return self.client.register(email, password)

            threading.Thread(
                target=lambda: self._register_worker(email, work),
                daemon=True,
            ).start()

        ttk.Button(actions, text="Register", style="Accent.TButton", command=do_submit).pack(side="left")
        ttk.Button(actions, text="Back to Login", style="Secondary.TButton", command=lambda: self.show_login()).pack(side="left", padx=(SPACING["md"], 0))

    def _register_worker(self, email: str, work) -> None:
        try:
            work()
            self.results_queue.put(("register-ok", email))
        except Exception as exc:  # noqa: BLE001
            self.results_queue.put(("register-error", str(exc)))

    def show_verify_email(self, email: str) -> None:
        self._clear_container()

        panel = self._make_center_panel()

        ttk.Label(panel, text="Verify Your Email", style="AuthTitle.TLabel").pack(anchor="w")
        ttk.Label(panel, text=f"A 6-digit code was sent to {email}.\nIt expires in 35 minutes.", style="AuthSubtitle.TLabel", wraplength=420).pack(anchor="w", pady=(SPACING["sm"], SPACING["xl"]))

        self.verify_error_var = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self.verify_error_var, style="Muted.TLabel", wraplength=420).pack(anchor="w")

        ttk.Label(panel, text="Verification Code", style="Body.TLabel").pack(anchor="w")
        verify_code_surface, self.verify_code_entry = self._make_entry_field(panel, bg_color=self.colors["panel"])
        verify_code_surface.pack(anchor="w", fill="x", pady=(SPACING["xs"], SPACING["xl"]))

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.pack(anchor="w")

        def do_verify() -> None:
            code = self.verify_code_entry.get().strip()
            self.verify_error_var.set("")
            if len(code) != 6 or not code.isdigit():
                self.verify_error_var.set("Enter the 6-digit code from your email.")
                return

            def work() -> dict:
                result = self.client.verify_email(email, code)
                self.client.token = result["access_token"]
                try:
                    self.cloud_app_settings = self.client.get_app_settings()
                except Exception:
                    self.cloud_app_settings = {}
                return {"email": email}

            threading.Thread(
                target=lambda: self._verify_worker(work),
                daemon=True,
            ).start()

        ttk.Button(actions, text="Verify & Sign In", style="Accent.TButton", command=do_verify).pack(side="left")
        ttk.Button(actions, text="Back", style="Secondary.TButton", command=lambda: self.show_register()).pack(side="left", padx=(SPACING["md"], 0))

    def _verify_worker(self, work) -> None:
        try:
            result = work()
            self.results_queue.put(("login-ok", result))
        except Exception as exc:  # noqa: BLE001
            self.results_queue.put(("verify-error", str(exc)))

    def show_main(self, user: dict[str, str]) -> None:
        self._clear_container()
        self.user_email = user["email"]
        self.staged_upload: dict[str, Any] | None = None
        self.status_var = tk.StringVar(value="Loading workspace")
        self.account_var = tk.StringVar(value=self.user_email)

        header = ttk.Frame(self.container, style="Shell.TFrame")
        header.pack(fill="x", pady=(0, SPACING["md"]))
        left = ttk.Frame(header, style="Shell.TFrame")
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="PRODUCTION STUDIO", style="HeroKicker.TLabel").pack(anchor="w")
        ttk.Label(left, text=APP_NAME, style="Display.TLabel").pack(anchor="w", pady=(1, 0))
        ttk.Label(
            left,
            text="Stage, validate, and publish short-form video from one focused workspace.",
            style="Subheading.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        badge = RoundedFrame(
            header,
            bg_color=self.colors["bg"],
            fill_color=self.colors["panel"],
            radius=RADIUS_CARD,
            padding=SPACING["md"],
            outline_color=self.colors["border_soft"],
            shadow=False,
        )
        badge.pack(side="right", anchor="ne", padx=(SPACING["xl"], 0), pady=(SPACING["xs"], 0))
        ttk.Label(badge.body, text="CLOUD WORKSPACE", style="Muted.TLabel").pack(anchor="e")
        ttk.Label(badge.body, textvariable=self.account_var, style="Body.TLabel").pack(anchor="e", pady=(SPACING["xs"], 0))
        ttk.Label(badge.body, textvariable=self.status_var, style="Muted.TLabel").pack(anchor="e", pady=(SPACING["xs"], 0))

        self._build_service_overview(self.container)
        self._build_section_nav(self.container)
        self._build_workflow_overview(self.container)

        self.content_frame = ttk.Frame(self.container, style="Shell.TFrame")
        self.content_frame.pack(fill="both", expand=True)
        self.content_frame.columnconfigure(0, weight=1)
        self.content_frame.rowconfigure(0, weight=1)

        self.publish_scroll = ScrollableFrame(self.content_frame, bg_color=self.colors["bg"], bottom_padding=48)
        self.accounts_scroll = ScrollableFrame(self.content_frame, bg_color=self.colors["bg"], bottom_padding=48)
        self.settings_scroll = ScrollableFrame(self.content_frame, bg_color=self.colors["bg"], bottom_padding=56)
        self.publish_tab = self.publish_scroll.content
        self.accounts_tab = self.accounts_scroll.content
        self.settings_tab = self.settings_scroll.content
        self.section_frames = {
            "publishing": self.publish_scroll,
            "accounts": self.accounts_scroll,
            "settings": self.settings_scroll,
        }
        for frame in self.section_frames.values():
            frame.grid(row=0, column=0, sticky="nsew")

        self._build_publishing_tab()
        self._build_accounts_tab()
        self._build_settings_tab()
        self._set_active_section("publishing")
        self.load_workspace_data()

    def _build_section_nav(self, parent: ttk.Frame) -> None:
        nav_shell = RoundedFrame(
            parent,
            bg_color=self.colors["bg"],
            fill_color=self.colors["panel"],
            radius=RADIUS_CARD,
            padding=SPACING["xs"],
            outline_color=self.colors["border_soft"],
        )
        nav_shell.pack(fill="x", pady=(0, SPACING["md"]))
        nav = nav_shell.body
        self.section_buttons = {}
        for key, label in (("publishing", "Publishing"), ("accounts", "Accounts"), ("settings", "Settings")):
            pill = RoundedFrame(
                nav,
                bg_color=self.colors["panel"],
                fill_color=self.colors["panel"],
                radius=RADIUS_CHIP,
                padding=SPACING["xs"],
                outline_color=None,
            )
            pill.pack(side="left", padx=(0 if key == "publishing" else SPACING["sm"], 0))
            tab = tk.Label(
                pill.body,
                text=label,
                bg=self.colors["panel"],
                fg=self.colors["subtle"],
                font=self.nav_font,
                padx=SPACING["xl"],
                pady=SPACING["sm"],
                cursor="hand2",
            )
            tab.pack()
            for widget in (pill, pill.body, tab):
                widget.bind("<Button-1>", lambda _event, section=key: self._set_active_section(section))
                widget.bind("<Enter>", lambda _event, section=key: self._set_tab_hover(section, True))
                widget.bind("<Leave>", lambda _event, section=key: self._set_tab_hover(section, False))
            self.section_buttons[key] = {"surface": pill, "label": tab}

    def _set_active_section(self, section: str) -> None:
        frame = self.section_frames.get(section)
        if frame is None:
            return
        frame.tkraise()
        self.active_section = section
        for key, widgets in self.section_buttons.items():
            active = key == section
            fill = self.colors["accent_panel"] if active else self.colors["panel"]
            widgets["surface"].set_bg(self.colors["panel"])
            widgets["surface"].set_fill(fill)
            widgets["surface"].set_outline(self.colors["accent"] if active else None)
            widgets["label"].configure(
                bg=fill,
                fg=self.colors["text"] if active else self.colors["subtle"],
            )

    def _set_tab_hover(self, section: str, hovered: bool) -> None:
        widgets = self.section_buttons.get(section)
        if not widgets or getattr(self, "active_section", "") == section:
            return
        fill = self.colors["soft_panel"] if hovered else self.colors["panel"]
        widgets["surface"].set_fill(fill)
        widgets["surface"].set_outline(self.colors["focus"] if hovered else None)
        widgets["label"].configure(bg=fill, fg=self.colors["text"] if hovered else self.colors["subtle"])

    def _build_workflow_overview(self, parent: ttk.Frame) -> None:
        workflow_surface = RoundedFrame(
            parent,
            bg_color=self.colors["bg"],
            fill_color=self.colors["panel"],
            radius=RADIUS_PANEL,
            padding=SPACING["lg"],
            outline_color=self.colors["border_soft"],
            shadow=False,
        )
        workflow_surface.pack(fill="x", pady=(0, SPACING["md"]))
        workflow = workflow_surface.body
        workflow.columnconfigure(0, weight=1)

        heading = ttk.Frame(workflow, style="Card.TFrame")
        heading.grid(row=0, column=0, sticky="ew", pady=(0, SPACING["md"]))
        ttk.Label(heading, text="Publish Runway", style="CardTitle.TLabel").pack(side="left")
        self.workflow_summary_var = tk.StringVar(value="Incomplete")
        ttk.Label(heading, textvariable=self.workflow_summary_var, style="FieldHelp.TLabel").pack(side="right")

        steps_frame = ttk.Frame(workflow, style="Card.TFrame")
        steps_frame.grid(row=1, column=0, sticky="ew")
        self.workflow_steps_frame = steps_frame
        self.workflow_rows: dict[str, dict[str, Any]] = {}
        steps = (
            ("details", "01", "Add Details", "Title and caption"),
            ("video", "02", "Select Video", "Staged media"),
            ("platforms", "03", "Choose Platforms", "Publishing targets"),
            ("validate", "04", "Validate & Publish", "Final checks"),
        )
        for index, (key, number, title, detail) in enumerate(steps):
            step_surface = RoundedFrame(
                steps_frame,
                bg_color=self.colors["panel"],
                fill_color=self.colors["field"],
                radius=RADIUS_CARD,
                padding=SPACING["md"],
                outline_color=self.colors["border_soft"],
                min_height=74,
            )
            step_surface.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else SPACING["sm"], 0))
            steps_frame.columnconfigure(index, weight=1)
            step = step_surface.body
            step.columnconfigure(1, weight=1)

            number_label = tk.Label(
                step,
                text=number,
                bg=self.colors["field"],
                fg=self.colors["muted"],
                font=self.meta_font,
                width=4,
                anchor="w",
            )
            number_label.grid(row=0, column=0, sticky="nw", padx=(0, SPACING["sm"]))
            title_label = tk.Label(
                step,
                text=title,
                bg=self.colors["field"],
                fg=self.colors["text"],
                font=self.small_heading_font,
                anchor="w",
            )
            title_label.grid(row=0, column=1, sticky="w")
            status_var = tk.StringVar(value="Incomplete")
            status_label = tk.Label(
                step,
                textvariable=status_var,
                bg=self.colors["field"],
                fg=self.colors["warn"],
                font=self.meta_font,
                anchor="w",
            )
            status_label.grid(row=1, column=1, sticky="w", pady=(SPACING["xs"], 0))
            detail_var = tk.StringVar(value=detail)
            detail_label = tk.Label(
                step,
                textvariable=detail_var,
                bg=self.colors["field"],
                fg=self.colors["muted"],
                font=self.meta_font,
                anchor="w",
            )
            detail_label.grid(row=2, column=1, sticky="w", pady=(SPACING["xs"], 0))

            self.workflow_rows[key] = {
                "surface": step_surface,
                "number": number_label,
                "title": title_label,
                "status": status_var,
                "status_label": status_label,
                "detail": detail_var,
                "detail_label": detail_label,
                "index": index,
            }
        steps_frame.bind("<Configure>", lambda event: self._layout_workflow_steps(event.width), add="+")
        self.after_idle(lambda: self._layout_workflow_steps(steps_frame.winfo_width()))

    def _layout_workflow_steps(self, width: int) -> None:
        if not hasattr(self, "workflow_rows") or width <= 1:
            return
        columns = 4
        if width < 700:
            columns = 1
        elif width < 980:
            columns = 2
        if getattr(self, "_workflow_columns", None) == columns:
            return
        self._workflow_columns = columns
        frame = self.workflow_steps_frame
        for column in range(4):
            frame.columnconfigure(column, weight=0)
        for row in self.workflow_rows.values():
            index = row["index"]
            grid_row = index // columns
            grid_column = index % columns
            row["surface"].grid(
                row=grid_row,
                column=grid_column,
                sticky="ew",
                padx=(0 if grid_column == 0 else SPACING["sm"], 0),
                pady=(0 if grid_row == 0 else SPACING["sm"], 0),
            )
        for column in range(columns):
            frame.columnconfigure(column, weight=1)

    def _update_workflow_status(
        self,
        *,
        details_status: str,
        details_detail: str,
        video_status: str,
        video_detail: str,
        platforms_status: str,
        platforms_detail: str,
        validate_status: str,
        validate_detail: str,
    ) -> None:
        if not hasattr(self, "workflow_rows"):
            return
        status_map = {
            "details": (details_status, details_detail),
            "video": (video_status, video_detail),
            "platforms": (platforms_status, platforms_detail),
            "validate": (validate_status, validate_detail),
        }
        active_key = next((key for key in ("details", "video", "platforms", "validate") if status_map[key][0] != "Ready"), "validate")
        for key, row in self.workflow_rows.items():
            status, detail = status_map[key]
            active = key == active_key
            fill = self.colors["accent_panel"] if active else self.colors["field"]
            row["surface"].set_fill(fill)
            row["surface"].set_outline(self.colors["accent"] if active else self.colors["border_soft"])
            row["status"].set(status)
            row["detail"].set(detail)
            tone = self.colors["good"] if status == "Ready" else self.colors["warn"] if status == "Incomplete" else self.colors["bad"]
            for label_key in ("number", "title", "status_label", "detail_label"):
                row[label_key].configure(bg=fill)
            row["number"].configure(fg=self.colors["accent"] if active else self.colors["muted"])
            row["title"].configure(fg=self.colors["text"])
            row["status_label"].configure(fg=tone)
            row["detail_label"].configure(fg=self.colors["text"] if active else self.colors["muted"])
        ready = all(status == "Ready" for status, _detail in status_map.values())
        self.workflow_summary_var.set("Ready to publish" if ready else f"Active step: {status_map[active_key][1]}")

    def _build_service_overview(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent, style="Shell.TFrame")
        frame.pack(fill="x", pady=(0, SPACING["sm"]))

        grid = ttk.Frame(frame, style="Shell.TFrame")
        grid.pack(fill="x")

        self.service_rows: dict[str, dict[str, Any]] = {}
        for index, (key, label) in enumerate(LOCAL_SERVICES.items()):
            card_surface = RoundedFrame(
                grid,
                bg_color=self.colors["bg"],
                fill_color=self.colors["panel"],
                radius=RADIUS_CARD,
                padding=SPACING["sm"],
                outline_color=self.colors["border_soft"],
                shadow=False,
                min_height=50,
            )
            card_surface.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else SPACING["sm"], 0))
            grid.columnconfigure(index, weight=1)
            grid.rowconfigure(0, weight=1)
            card = card_surface.body
            card.columnconfigure(2, weight=1)

            status_var = tk.StringVar(value="Checking")
            detail_var = tk.StringVar(value=label)
            dot = tk.Canvas(card, width=9, height=9, bg=self.colors["panel"], bd=0, highlightthickness=0)
            dot.create_oval(2, 2, 7, 7, fill=self.colors["warn"], outline="")
            dot.grid(row=0, column=0, sticky="w", padx=(0, SPACING["sm"]))
            ttk.Label(card, text=label, style="Body.TLabel").grid(row=0, column=1, sticky="w")
            status_label = ttk.Label(card, textvariable=status_var, style="Warn.TLabel")
            status_label.grid(row=0, column=2, sticky="e", padx=(SPACING["sm"], 0))
            ttk.Label(card, textvariable=detail_var, style="Muted.TLabel", wraplength=270).grid(
                row=1, column=0, columnspan=3, sticky="w", pady=(2, 0)
            )
            self.service_rows[key] = {
                "status": status_var,
                "detail": detail_var,
                "label": status_label,
                "dot": dot,
            }

    def _build_publishing_tab(self) -> None:
        self.publish_tab.columnconfigure(0, weight=3)
        self.publish_tab.columnconfigure(1, weight=1, minsize=340)
        self.publish_tab.rowconfigure(0, weight=1)

        self.editor_surface = RoundedFrame(
            self.publish_tab,
            bg_color=self.colors["bg"],
            fill_color=self.colors["panel"],
            radius=RADIUS_PANEL,
            padding=PADDING_PANEL,
            outline_color=self.colors["border_soft"],
            shadow=False,
        )
        self.editor_surface.grid(row=0, column=0, sticky="nsew", padx=(0, SPACING["lg"]))
        editor = self.editor_surface.body
        self.sidebar_surface = RoundedFrame(
            self.publish_tab,
            bg_color=self.colors["bg"],
            fill_color=self.colors["panel"],
            radius=RADIUS_PANEL,
            padding=PADDING_PANEL,
            outline_color=self.colors["border_soft"],
            shadow=False,
        )
        self.sidebar_surface.grid(row=0, column=1, sticky="nsew")
        sidebar = self.sidebar_surface.body
        sidebar.columnconfigure(0, weight=1)

        ttk.Label(editor, text="Composer", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            editor,
            text="Write the reusable copy and publishing defaults for this staged post.",
            style="CardSubtitle.TLabel",
        ).pack(anchor="w", pady=(SPACING["xs"], SPACING["md"]))

        title_header = ttk.Frame(editor, style="Card.TFrame")
        title_header.pack(fill="x")
        ttk.Label(title_header, text="TITLE", style="Micro.TLabel").pack(side="left")
        self.title_count_var = tk.StringVar(value="0 / 100")
        ttk.Label(title_header, textvariable=self.title_count_var, style="FieldHelp.TLabel").pack(side="right")
        self.title_input_surface, self.title_entry = self._make_entry_field(
            editor,
            placeholder="Enter post title",
            bg_color=self.colors["panel"],
        )
        self.title_input_surface.pack(fill="x", pady=(SPACING["xs"], SPACING["md"]))
        self.title_entry.bind("<KeyRelease>", lambda _event: self._handle_post_details_changed())

        caption_header = ttk.Frame(editor, style="Card.TFrame")
        caption_header.pack(fill="x")
        ttk.Label(caption_header, text="CAPTION", style="Micro.TLabel").pack(side="left")
        self.caption_count_var = tk.StringVar(value="0 / 2,200")
        ttk.Label(caption_header, textvariable=self.caption_count_var, style="FieldHelp.TLabel").pack(side="right")
        colors = self.colors
        self.caption_surface = RoundedFrame(
            editor,
            bg_color=self.colors["panel"],
            fill_color=self.colors["field"],
            radius=RADIUS_INPUT,
            padding=SPACING["xs"],
            outline_color=self.colors["border"],
        )
        self.caption_surface.pack(fill="both", expand=True, pady=(SPACING["xs"], SPACING["xs"]))
        self.caption_text = tk.Text(
            self.caption_surface.body,
            height=6,
            bg=colors["field"],
            fg=colors["text"],
            font=self.body_font,
            insertbackground=colors["text"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            wrap="word",
        )
        self.caption_text.pack(fill="both", expand=True, padx=PADDING_FIELD_X, pady=SPACING["md"])
        self.caption_text.bind("<KeyRelease>", lambda _event: self._handle_post_details_changed())
        for widget in (self.caption_surface, self.caption_surface.canvas, self.caption_surface.body, self.caption_text):
            widget.bind("<Enter>", lambda _event: self._set_field_hover(self.caption_surface, True), add="+")
            widget.bind("<Leave>", lambda _event: self._set_field_hover(self.caption_surface, False), add="+")
        self._install_text_placeholder(self.caption_text, "Write a caption for this post", self.caption_surface)
        ttk.Label(
            editor,
            text="Common short-form guide: keep captions concise; platform-specific limits are checked before publish.",
            style="FieldHelp.TLabel",
        ).pack(anchor="w", pady=(0, SPACING["sm"]))

        hashtags_header = ttk.Frame(editor, style="Card.TFrame")
        hashtags_header.pack(fill="x")
        ttk.Label(hashtags_header, text="HASHTAGS", style="Micro.TLabel").pack(side="left")
        self.hashtags_count_var = tk.StringVar(value="0 tags")
        ttk.Label(hashtags_header, textvariable=self.hashtags_count_var, style="FieldHelp.TLabel").pack(side="right")
        self.hashtags_input_surface, self.hashtags_entry = self._make_entry_field(
            editor,
            placeholder="fyp, launch, behind the scenes",
            bg_color=self.colors["panel"],
        )
        self.hashtags_input_surface.pack(fill="x", pady=(SPACING["xs"], SPACING["sm"]))
        self.hashtags_entry.bind("<KeyRelease>", lambda _event: self._handle_post_details_changed())
        self.hashtag_chip_frame = ttk.Frame(editor, style="Card.TFrame")
        self.hashtag_chip_frame.pack(fill="x", pady=(0, SPACING["sm"]))
        ttk.Label(editor, text="Separate tags with commas. ReelPush normalizes # prefixes when saving.", style="FieldHelp.TLabel").pack(anchor="w", pady=(0, SPACING["sm"]))

        self.publishing_grid = ttk.Frame(editor, style="Card.TFrame")
        self.publishing_grid.pack(fill="x", pady=(SPACING["xs"], SPACING["md"]))
        self.publishing_grid.columnconfigure(0, weight=1)
        self.publishing_grid.columnconfigure(1, weight=1)

        self.privacy_frame = ttk.Frame(self.publishing_grid, style="Card.TFrame")
        self.privacy_frame.grid(row=0, column=0, sticky="ew", padx=(0, SPACING["sm"]))
        ttk.Label(self.privacy_frame, text="PRIVACY", style="Micro.TLabel").pack(anchor="w")
        self.privacy_var = tk.StringVar(value="public")
        self.privacy_dropdown = self._make_dropdown_field(
            self.privacy_frame,
            variable=self.privacy_var,
            values=["public", "private", "unlisted", "friends"],
            command=lambda _choice: self._handle_post_details_changed(),
            bg_color=self.colors["panel"],
        )
        self.privacy_dropdown.pack(fill="x", pady=(SPACING["xs"], 0))

        self.schedule_frame = ttk.Frame(self.publishing_grid, style="Card.TFrame")
        self.schedule_frame.grid(row=0, column=1, sticky="ew", padx=(SPACING["sm"], 0))
        ttk.Label(self.schedule_frame, text="SCHEDULE", style="Micro.TLabel").pack(anchor="w")
        self.schedule_var = tk.StringVar(value="")
        self.schedule_input_surface, self.schedule_entry = self._make_entry_field(
            self.schedule_frame,
            textvariable=self.schedule_var,
            bg_color=self.colors["panel"],
        )
        self.schedule_input_surface.pack(fill="x", pady=(SPACING["xs"], 0))
        self.schedule_entry.bind("<KeyRelease>", lambda _event: self._handle_post_details_changed())
        ttk.Label(self.schedule_frame, text="Optional. Leave blank for immediate publish.", style="FieldHelp.TLabel").pack(anchor="w", pady=(SPACING["xs"], 0))

        requirements_surface = RoundedFrame(
            editor,
            bg_color=self.colors["panel"],
            fill_color=self.colors["soft_panel"],
            radius=RADIUS_CARD,
            padding=SPACING["md"],
            outline_color=self.colors["border_soft"],
        )
        requirements_surface.pack(fill="x", pady=(0, SPACING["md"]))
        self._build_post_requirements(requirements_surface.body)

        preview_surface = RoundedFrame(
            editor,
            bg_color=self.colors["panel"],
            fill_color=self.colors["soft_panel"],
            radius=RADIUS_CARD,
            padding=SPACING["md"],
            outline_color=self.colors["border_soft"],
        )
        preview_surface.pack(fill="x")
        preview = preview_surface.body
        meta_icon = tk.Canvas(preview, width=34, height=34, bg=self.colors["soft_panel"], highlightthickness=0, bd=0)
        meta_icon.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, SPACING["md"]))
        meta_icon.create_rectangle(5, 5, 29, 29, fill=self.colors["accent_panel"], outline="")
        meta_icon.create_line(11, 14, 23, 14, fill=self.colors["muted"])
        meta_icon.create_line(11, 20, 23, 20, fill=self.colors["muted"])
        ttk.Label(preview, text="Preview Metadata", style="PanelBody.TLabel").grid(row=0, column=1, sticky="w")
        self.preview_meta_var = tk.StringVar(value="No staged video metadata available yet.")
        ttk.Label(preview, textvariable=self.preview_meta_var, style="PanelMuted.TLabel", wraplength=620).grid(
            row=1, column=1, sticky="w", pady=(SPACING["xs"], 0)
        )

        ttk.Label(sidebar, text="Publish Control", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(sidebar, text="Stage media, choose targets, and send when checks pass.", style="CardSubtitle.TLabel").grid(
            row=1, column=0, sticky="w", pady=(SPACING["xs"], SPACING["md"])
        )

        video_surface = RoundedFrame(
            sidebar,
            bg_color=self.colors["panel"],
            fill_color=self.colors["soft_panel"],
            radius=RADIUS_CARD,
            padding=SPACING["md"],
            outline_color=self.colors["border_soft"],
        )
        video_surface.grid(row=2, column=0, sticky="ew", pady=(0, SPACING["md"]))
        video_box = video_surface.body
        video_box.columnconfigure(0, weight=1)
        self.file_var = tk.StringVar(value="No video staged yet")
        self.video_meta_var = tk.StringVar(value="Choose a local video when you are ready to validate and publish.")
        self.video_warning_var = tk.StringVar(value="")
        placeholder = tk.Canvas(video_box, height=72, bg=self.colors["soft_panel"], highlightthickness=0, bd=0)
        placeholder.grid(row=0, column=0, sticky="ew", pady=(0, SPACING["sm"]))
        placeholder.create_rectangle(8, 8, 62, 64, fill=self.colors["accent_panel"], outline=self.colors["border_soft"])
        placeholder.create_polygon(29, 27, 29, 47, 47, 37, fill=self.colors["accent"], outline="")
        self.video_placeholder = placeholder
        ttk.Label(video_box, text="Video", style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Label(video_box, textvariable=self.file_var, style="PanelBody.TLabel", wraplength=280).grid(
            row=2, column=0, sticky="w", pady=(SPACING["xs"], 0)
        )
        ttk.Label(video_box, textvariable=self.video_meta_var, style="PanelMuted.TLabel", wraplength=280).grid(
            row=3, column=0, sticky="w", pady=(SPACING["xs"], SPACING["md"])
        )
        ttk.Label(video_box, textvariable=self.video_warning_var, style="PanelWarn.TLabel", wraplength=280).grid(
            row=4, column=0, sticky="w", pady=(0, SPACING["md"])
        )
        video_actions = ttk.Frame(video_box, style="SoftPanel.TFrame")
        video_actions.grid(row=5, column=0, sticky="w")
        ttk.Button(video_actions, text="Choose Video", style="Accent.TButton", command=self.choose_video).pack(side="left")
        self.clear_button = ttk.Button(video_actions, text="Clear", style="Tertiary.TButton", command=self.clear_stage)
        self.clear_button.pack(side="left", padx=(SPACING["sm"], 0))
        self.clear_button.configure(state="disabled")

        ttk.Label(sidebar, text="Targets", style="Body.TLabel").grid(row=3, column=0, sticky="w", pady=(0, SPACING["sm"]))
        platforms_frame = ttk.Frame(sidebar, style="Card.TFrame")
        platforms_frame.grid(row=4, column=0, sticky="ew")
        self._build_platform_rows(platforms_frame)

        readiness_surface = RoundedFrame(
            sidebar,
            bg_color=self.colors["panel"],
            fill_color=self.colors["soft_panel"],
            radius=RADIUS_CARD,
            padding=SPACING["md"],
            outline_color=self.colors["border_soft"],
        )
        readiness_surface.grid(row=5, column=0, sticky="ew", pady=(SPACING["md"], 0))
        readiness = readiness_surface.body
        ttk.Label(readiness, text="Readiness Summary", style="PanelBody.TLabel").pack(anchor="w")
        self.delivery_summary_var = tk.StringVar(value="Complete the checklist to publish.")
        self.delivery_summary_label = ttk.Label(readiness, textvariable=self.delivery_summary_var, style="PanelWarn.TLabel", wraplength=300)
        self.delivery_summary_label.pack(
            anchor="w", pady=(SPACING["xs"], SPACING["sm"])
        )
        self.delivery_detail_var = tk.StringVar(value="")
        ttk.Label(readiness, textvariable=self.delivery_detail_var, style="PanelMuted.TLabel", wraplength=300).pack(
            anchor="w", pady=(0, SPACING["sm"])
        )
        self._build_readiness_checklist(readiness)

        stage_actions = ttk.Frame(sidebar, style="Card.TFrame")
        stage_actions.grid(row=6, column=0, sticky="ew", pady=(SPACING["md"], 0))
        stage_actions.columnconfigure((0, 1), weight=1)
        ttk.Button(stage_actions, text="Save Draft", style="Secondary.TButton", command=self.save_profile).grid(
            row=0, column=0, sticky="ew", padx=(0, SPACING["sm"])
        )
        ttk.Button(stage_actions, text="Save Stage", style="Secondary.TButton", command=self.save_stage).grid(
            row=0, column=1, sticky="ew", padx=(SPACING["sm"], 0)
        )
        self.publish_button = ttk.Button(sidebar, text="Publish Now", style="Accent.TButton", command=self.publish_now)
        self.publish_button.grid(row=7, column=0, sticky="ew", pady=(SPACING["md"], 0))
        self.publish_button.configure(state="disabled")

        self.publish_log = tk.Text(
            sidebar,
            height=5,
            bg=colors["log"],
            fg=colors["text"],
            font=self.body_font,
            insertbackground=colors["text"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            wrap="word",
        )
        self.publish_log.grid(row=8, column=0, sticky="nsew", pady=(SPACING["md"], 0))
        sidebar.rowconfigure(8, weight=1)
        self.publish_log.insert("end", "Publish results will appear here.\n")
        self.publish_log.configure(state="disabled")
        self.publish_tab.bind("<Configure>", lambda event: self._layout_publishing_tab(event.width), add="+")
        self.publishing_grid.bind("<Configure>", lambda event: self._layout_publish_detail_fields(event.width), add="+")
        self.after_idle(lambda: self._layout_publishing_tab(self.publish_tab.winfo_width()))
        self.after_idle(lambda: self._layout_publish_detail_fields(self.publishing_grid.winfo_width()))

    def _layout_publishing_tab(self, width: int) -> None:
        if not hasattr(self, "editor_surface") or width <= 1:
            return
        stacked = width < 980
        if getattr(self, "_publishing_stacked", None) == stacked:
            return
        self._publishing_stacked = stacked
        self.publish_tab.columnconfigure(0, weight=1)
        self.publish_tab.columnconfigure(1, weight=0, minsize=0)
        if stacked:
            self.editor_surface.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, SPACING["lg"]))
            self.sidebar_surface.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        else:
            self.publish_tab.columnconfigure(0, weight=3)
            self.publish_tab.columnconfigure(1, weight=1, minsize=340)
            self.editor_surface.grid(row=0, column=0, sticky="nsew", padx=(0, SPACING["lg"]), pady=0)
            self.sidebar_surface.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)

    def _layout_publish_detail_fields(self, width: int) -> None:
        if not hasattr(self, "privacy_frame") or width <= 1:
            return
        stacked = width < 620
        if getattr(self, "_publish_fields_stacked", None) == stacked:
            return
        self._publish_fields_stacked = stacked
        self.publishing_grid.columnconfigure(0, weight=1)
        self.publishing_grid.columnconfigure(1, weight=1 if not stacked else 0)
        if stacked:
            self.privacy_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, SPACING["md"]))
            self.schedule_frame.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        else:
            self.privacy_frame.grid(row=0, column=0, sticky="ew", padx=(0, SPACING["sm"]), pady=0)
            self.schedule_frame.grid(row=0, column=1, sticky="ew", padx=(SPACING["sm"], 0), pady=0)

    def _build_post_requirements(self, parent: tk.Frame) -> None:
        self.post_requirement_rows: dict[str, dict[str, Any]] = {}
        header = ttk.Frame(parent, style="SoftPanel.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, SPACING["md"]))
        ttk.Label(header, text="Post Requirements", style="PanelBody.TLabel").pack(side="left")
        self.post_requirements_summary_var = tk.StringVar(value="0 of 4 complete")
        ttk.Label(header, textvariable=self.post_requirements_summary_var, style="PanelMuted.TLabel").pack(side="right")

        items = (
            ("title", "Title added"),
            ("caption", "Caption added"),
            ("video", "Video selected"),
            ("platforms", "At least one platform selected"),
        )
        for index, (key, label) in enumerate(items):
            row_index = 1 + index // 2
            col_index = index % 2
            item = ttk.Frame(parent, style="SoftPanel.TFrame")
            item.grid(row=row_index, column=col_index, sticky="ew", padx=(0 if col_index == 0 else SPACING["md"], 0), pady=(0, SPACING["sm"]))
            parent.columnconfigure(col_index, weight=1)
            icon_var = tk.StringVar(value="-")
            icon = ttk.Label(item, textvariable=icon_var, style="PanelSmallWarn.TLabel", width=8)
            icon.pack(side="left")
            text = ttk.Label(item, text=label, style="PanelMuted.TLabel")
            text.pack(side="left")
            self.post_requirement_rows[key] = {"icon": icon_var, "icon_label": icon, "text_label": text}

    def _set_post_requirement(self, key: str, complete: bool) -> None:
        row = getattr(self, "post_requirement_rows", {}).get(key)
        if not row:
            return
        row["icon"].set("Ready" if complete else "Missing")
        row["icon_label"].configure(style="PanelSmallGood.TLabel" if complete else "PanelSmallBad.TLabel")
        row["text_label"].configure(style="PanelBody.TLabel" if complete else "PanelMuted.TLabel")

    def _build_platform_rows(self, parent: ttk.Frame) -> None:
        self.selected_platform_vars = {}
        self.platform_rows = {}
        for row_index, platform in enumerate(PLATFORM_ORDER):
            details = PLATFORM_DETAILS[platform]
            var = tk.BooleanVar(value=False)
            self.selected_platform_vars[platform] = var

            row_surface = RoundedFrame(
                parent,
                bg_color=self.colors["panel"],
                fill_color=self.colors["field"],
                radius=RADIUS_CARD,
                padding=SPACING["md"],
                outline_color=self.colors["border_soft"],
            )
            row_surface.grid(row=row_index, column=0, sticky="ew", pady=(0 if row_index == 0 else SPACING["sm"], 0))
            row = row_surface.body
            row.columnconfigure(1, weight=1)
            row.columnconfigure(2, minsize=72)
            parent.columnconfigure(0, weight=1)

            badge = tk.Label(
                row,
                text=details.get("badge", details["name"][:2]).upper(),
                bg=self.colors["accent_panel"],
                fg=self.colors["text"],
                font=self.meta_font,
                width=4,
                padx=SPACING["sm"],
                pady=SPACING["sm"],
            )
            badge.grid(row=0, column=0, rowspan=3, sticky="n", padx=(0, SPACING["md"]))
            name_label = tk.Label(
                row,
                text=details["name"],
                bg=self.colors["field"],
                fg=self.colors["text"],
                font=self.body_font,
                anchor="w",
            )
            name_label.grid(row=0, column=1, sticky="w")
            status_var = tk.StringVar(value="Checking account")
            readiness_var = tk.StringVar(value="Waiting for status")
            selected_var = tk.StringVar(value="Add")
            status_label = tk.Label(
                row,
                textvariable=status_var,
                bg=self.colors["field"],
                fg=self.colors["warn"],
                font=self.meta_font,
                anchor="w",
            )
            status_label.grid(row=1, column=1, sticky="w", pady=(SPACING["xs"], 0))
            readiness_label = tk.Label(
                row,
                textvariable=readiness_var,
                bg=self.colors["field"],
                fg=self.colors["muted"],
                font=self.muted_font,
                anchor="w",
                justify="left",
                wraplength=250,
            )
            readiness_label.grid(row=2, column=1, columnspan=2, sticky="w", pady=(SPACING["xs"], 0))
            selected_label = tk.Label(
                row,
                textvariable=selected_var,
                bg=self.colors["field"],
                fg=self.colors["muted"],
                font=self.meta_font,
                anchor="e",
            )
            selected_label.grid(row=0, column=2, sticky="e", padx=(SPACING["sm"], 0))
            status_dot = tk.Canvas(row, width=10, height=10, bg=self.colors["field"], bd=0, highlightthickness=0)
            status_dot.create_oval(2, 2, 8, 8, fill=self.colors["warn"], outline="")
            status_dot.grid(row=1, column=2, sticky="e", padx=(SPACING["sm"], 0), pady=(SPACING["xs"], 0))

            for widget in (row_surface, row_surface.body, row, badge, name_label, status_label, readiness_label, selected_label, status_dot):
                widget.bind("<Button-1>", lambda _event, p=platform: self._toggle_platform(p))
                widget.bind("<Enter>", lambda _event, p=platform: self._set_platform_hover(p, True))
                widget.bind("<Leave>", lambda _event, p=platform: self._set_platform_hover(p, False))

            self.platform_rows[platform] = {
                "surface": row_surface,
                "badge": badge,
                "dot": status_dot,
                "name_label": name_label,
                "status": status_var,
                "readiness": readiness_var,
                "selected": selected_var,
                "status_label": status_label,
                "readiness_label": readiness_label,
                "selected_label": selected_label,
                "hovered": False,
                "tone": "warn",
            }
        self._refresh_platform_selection_styles()

    def _build_readiness_checklist(self, parent: tk.Frame) -> None:
        self.readiness_rows = {}
        checklist = (
            ("network", "Network connected"),
            ("video", "Video selected"),
            ("details", "Post details valid"),
            ("platform_selected", "Platform selected"),
            ("platform_credentials", "Accounts connected"),
            ("schedule", "Publish timing"),
            ("ready", "Ready to publish"),
        )
        list_frame = ttk.Frame(parent, style="SoftPanel.TFrame")
        list_frame.pack(fill="x")
        for index, (key, label) in enumerate(checklist):
            row = ttk.Frame(list_frame, style="SoftPanel.TFrame")
            row.pack(fill="x", pady=(0 if index == 0 else SPACING["sm"], 0))
            icon_var = tk.StringVar(value="-")
            text_var = tk.StringVar(value=label)
            detail_var = tk.StringVar(value="")
            icon = ttk.Label(row, textvariable=icon_var, style="PanelSmallWarn.TLabel", width=7)
            icon.grid(row=0, column=0, sticky="nw")
            text = ttk.Label(row, textvariable=text_var, style="PanelMuted.TLabel", wraplength=245)
            text.grid(row=0, column=1, sticky="w")
            detail = ttk.Label(row, textvariable=detail_var, style="PanelMuted.TLabel", wraplength=245)
            detail.grid(row=1, column=1, sticky="w")
            self.readiness_rows[key] = {
                "icon": icon_var,
                "text": text_var,
                "detail": detail_var,
                "icon_label": icon,
                "text_label": text,
                "detail_label": detail,
            }

    def _toggle_platform(self, platform: str) -> None:
        var = self.selected_platform_vars.get(platform)
        if not var:
            return
        var.set(not var.get())
        self._refresh_delivery_readiness()
        self._refresh_platform_selection_styles()

    def _set_platform_hover(self, platform: str, hovered: bool) -> None:
        row = self.platform_rows.get(platform)
        if not row:
            return
        row["hovered"] = hovered
        self._refresh_platform_selection_styles()

    def _refresh_platform_selection_styles(self) -> None:
        for platform, row in getattr(self, "platform_rows", {}).items():
            selected = self.selected_platform_vars[platform].get() if platform in self.selected_platform_vars else False
            hovered = bool(row.get("hovered"))
            fill = self.colors["accent_panel"] if selected else (self.colors["field_hover"] if hovered else self.colors["field"])
            outline = self.colors["accent"] if selected else (self.colors["focus"] if hovered else self.colors["border_soft"])
            row["surface"].set_fill(fill)
            row["surface"].set_outline(outline)
            row["badge"].configure(
                bg=self.colors["accent"] if selected else self.colors["accent_panel"],
                fg="#ffffff" if selected else self.colors["text"],
            )
            row["dot"].configure(bg=fill)
            dot_color = self.colors["good"] if row.get("tone") == "good" else self.colors["warn"] if row.get("tone") == "warn" else self.colors["bad"]
            row["dot"].delete("all")
            row["dot"].create_oval(2, 2, 8, 8, fill=dot_color, outline="")
            row["selected"].set("Selected" if selected else "Add")
            row["name_label"].configure(bg=fill, fg=self.colors["text"])
            row["readiness_label"].configure(bg=fill, fg=self.colors["text"] if selected else self.colors["muted"])
            row["selected_label"].configure(bg=fill, fg=self.colors["text"] if selected else self.colors["muted"])
            row["status_label"].configure(bg=fill, fg=dot_color)

    def _handle_post_details_changed(self) -> None:
        self._update_character_counts()
        self._refresh_delivery_readiness()

    def _update_character_counts(self) -> None:
        if not hasattr(self, "title_count_var"):
            return
        title = self._entry_value(self.title_entry)
        caption = self._text_value(self.caption_text)
        hashtags = self._parsed_hashtags()
        self.title_count_var.set(f"{len(title)} / 100")
        self.caption_count_var.set(f"{len(caption)} / 2,200")
        self.hashtags_count_var.set(f"{len(hashtags)} tag{'s' if len(hashtags) != 1 else ''}")
        self._set_field_error(getattr(self, "title_input_surface", None), bool(title and len(title) > 100))
        self._set_field_error(getattr(self, "caption_surface", None), bool(caption and len(caption) > 2200))
        self._render_hashtag_chips(hashtags)

    def _parsed_hashtags(self) -> list[str]:
        if not hasattr(self, "hashtags_entry"):
            return []
        return [tag.strip().lstrip("#") for tag in self._entry_value(self.hashtags_entry).split(",") if tag.strip()]

    def _render_hashtag_chips(self, hashtags: list[str]) -> None:
        if not hasattr(self, "hashtag_chip_frame"):
            return
        for child in self.hashtag_chip_frame.winfo_children():
            child.destroy()
        if not hashtags:
            ttk.Label(self.hashtag_chip_frame, text="No hashtags added yet.", style="FieldHelp.TLabel").pack(anchor="w")
            return
        for tag in hashtags[:8]:
            chip_surface = RoundedFrame(
                self.hashtag_chip_frame,
                bg_color=self.colors["panel"],
                fill_color=self.colors["secondary"],
                radius=RADIUS_CHIP,
                padding=1,
            )
            chip_surface.pack(side="left", padx=(0, SPACING["sm"]), pady=(0, SPACING["xs"]))
            tk.Label(
                chip_surface.body,
                text=f"#{tag}",
                bg=self.colors["secondary"],
                fg=self.colors["text"],
                font=self.meta_font,
                padx=SPACING["sm"],
                pady=SPACING["xs"],
            ).pack()
        if len(hashtags) > 8:
            more_surface = RoundedFrame(
                self.hashtag_chip_frame,
                bg_color=self.colors["panel"],
                fill_color=self.colors["accent_panel"],
                radius=RADIUS_CHIP,
                padding=1,
            )
            more_surface.pack(side="left", pady=(0, SPACING["xs"]))
            tk.Label(
                more_surface.body,
                text=f"+{len(hashtags) - 8}",
                bg=self.colors["accent_panel"],
                fg=self.colors["muted"],
                font=self.meta_font,
                padx=SPACING["sm"],
                pady=SPACING["xs"],
            ).pack()

    def _format_upload_meta(self, upload: dict[str, Any] | None) -> str:
        if not upload:
            return "No staged video metadata available yet."
        parts: list[str] = []
        width = upload.get("width")
        height = upload.get("height")
        duration = upload.get("duration_seconds")
        size = upload.get("file_size_bytes")
        if width and height:
            parts.append(f"{width}x{height}")
        if duration is not None:
            secs = int(duration)
            parts.append(f"{secs // 60}:{secs % 60:02d}")
        if size:
            parts.append(f"{size / (1024 * 1024):.1f} MB")
        mime = upload.get("mime_type") or ""
        fmt_label = {
            "video/mp4": "MP4",
            "video/quicktime": "MOV",
            "video/webm": "WebM",
            "video/x-msvideo": "AVI",
            "video/x-matroska": "MKV",
        }.get(mime, mime)
        if fmt_label:
            parts.append(fmt_label)
        return " | ".join(parts) if parts else "Video metadata will appear after upload analysis."

    @staticmethod
    def _format_upload_warnings(upload: dict[str, Any] | None) -> str:
        warnings = list((upload or {}).get("validation_warnings") or [])
        if not warnings:
            return ""
        visible = warnings[:3]
        suffix = f"\n(+{len(warnings) - len(visible)} more)" if len(warnings) > len(visible) else ""
        return "Check video:\n" + "\n".join(f"• {w}" for w in visible) + suffix

    def _update_video_preview(self) -> None:
        if not hasattr(self, "file_var"):
            return
        upload = getattr(self, "staged_upload", None)
        if upload:
            self.file_var.set(upload.get("original_filename") or "Selected video")
            meta = self._format_upload_meta(upload)
            self.video_meta_var.set(meta)
            if hasattr(self, "video_warning_var"):
                self.video_warning_var.set(self._format_upload_warnings(upload))
            if hasattr(self, "clear_button"):
                self.clear_button.configure(state="normal")
            if hasattr(self, "video_placeholder"):
                self.video_placeholder.delete("all")
                self.video_placeholder.create_rectangle(8, 8, 78, 64, fill=self.colors["accent_panel"], outline=self.colors["border_soft"])
                self.video_placeholder.create_rectangle(92, 18, 260, 27, fill=self.colors["secondary"], outline="")
                self.video_placeholder.create_rectangle(92, 40, 214, 49, fill=self.colors["secondary"], outline="")
                self.video_placeholder.create_polygon(37, 26, 37, 46, 56, 36, fill=self.colors["accent"], outline="")
            if hasattr(self, "preview_meta_var"):
                self.preview_meta_var.set(meta)
        else:
            self.file_var.set("No video staged yet")
            self.video_meta_var.set("Choose a local video when you are ready to validate and publish.")
            if hasattr(self, "video_warning_var"):
                self.video_warning_var.set("")
            if hasattr(self, "clear_button"):
                self.clear_button.configure(state="disabled")
            if hasattr(self, "video_placeholder"):
                self.video_placeholder.delete("all")
                self.video_placeholder.create_rectangle(8, 8, 72, 64, fill=self.colors["accent_panel"], outline=self.colors["border_soft"])
                self.video_placeholder.create_oval(30, 25, 52, 47, fill=self.colors["secondary"], outline="")
                self.video_placeholder.create_polygon(38, 30, 38, 43, 49, 36, fill=self.colors["accent"], outline="")
                self.video_placeholder.create_rectangle(90, 22, 240, 31, fill=self.colors["secondary"], outline="")
                self.video_placeholder.create_rectangle(90, 45, 194, 54, fill=self.colors["secondary"], outline="")
            if hasattr(self, "preview_meta_var"):
                self.preview_meta_var.set("No staged video metadata available yet.")

    def _build_accounts_tab(self) -> None:
        accounts_surface = RoundedFrame(
            self.accounts_tab,
            bg_color=self.colors["bg"],
            fill_color=self.colors["panel"],
            radius=RADIUS_PANEL,
            padding=PADDING_PANEL,
            outline_color=self.colors["border_soft"],
        )
        accounts_surface.pack(fill="both", expand=True)
        self.accounts_frame = accounts_surface.body

        ttk.Label(self.accounts_frame, text="Account Switchboard", style="CardTitle.TLabel").pack(anchor="w")

        ttk.Label(
            self.accounts_frame,
            text="Approve each platform in the browser, then return here to verify publishing access.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(SPACING["xs"], SPACING["md"]))

        self.account_rows: dict[str, dict[str, Any]] = {}
        for platform in PLATFORM_ORDER:
            row_surface = RoundedFrame(
                self.accounts_frame,
                bg_color=self.colors["panel"],
                fill_color=self.colors["soft_panel"],
                radius=RADIUS_CARD,
                padding=SPACING["md"],
                outline_color=self.colors["border_soft"],
                shadow=False,
            )
            row_surface.pack(fill="x", pady=(0, SPACING["md"]))
            row = row_surface.body

            top = ttk.Frame(row, style="SoftPanel.TFrame")
            top.pack(fill="x")

            badge = tk.Label(
                top,
                text=PLATFORM_DETAILS[platform].get("badge", platform[:2]).upper(),
                bg=self.colors["accent_panel"],
                fg=self.colors["text"],
                font=self.meta_font,
                padx=SPACING["sm"],
                pady=SPACING["xs"],
            )
            badge.pack(side="left", padx=(0, SPACING["md"]))

            name = ttk.Label(top, text=PLATFORM_DETAILS[platform]["name"], style="PanelBody.TLabel")
            name.pack(side="left")

            status_var = tk.StringVar(value="Checking…")
            status_pill = RoundedFrame(
                top,
                bg_color=self.colors["soft_panel"],
                fill_color=self.colors["accent_panel"],
                radius=RADIUS_CHIP,
                padding=1,
                outline_color=self.colors["border_soft"],
            )
            status_pill.pack(side="left", padx=(SPACING["lg"], 0))
            status = tk.Label(
                status_pill.body,
                textvariable=status_var,
                bg=self.colors["accent_panel"],
                fg=self.colors["warn"],
                font=self.meta_font,
                padx=SPACING["sm"],
                pady=SPACING["xs"],
            )
            status.pack()

            readiness_var = tk.StringVar(value="")
            readiness = ttk.Label(top, textvariable=readiness_var, style="PanelWarn.TLabel")
            readiness.pack(side="left", padx=(SPACING["lg"], 0))

            connect_btn = ttk.Button(
                top,
                text="Connect",
                style="Accent.TButton",
                command=lambda p=platform: self.connect_platform(p),
            )
            connect_btn.pack(side="right")

            disconnect_btn = ttk.Button(
                top,
                text="Disconnect",
                style="Secondary.TButton",
                command=lambda p=platform: self.disconnect_platform(p),
            )
            disconnect_btn.pack(side="right", padx=(0, SPACING["sm"]))

            test_btn = ttk.Button(
                top,
                text="Test Credentials",
                style="Secondary.TButton",
                command=lambda p=platform: self.test_platform_credentials(p),
            )
            test_btn.pack(side="right", padx=(0, SPACING["sm"]))

            detail_var = tk.StringVar(value="Checking configuration and account status...")
            ttk.Label(row, textvariable=detail_var, style="PanelMuted.TLabel", wraplength=720).pack(anchor="w", pady=(SPACING["sm"], 0))

            note_var = tk.StringVar(value=PLATFORM_DETAILS[platform]["fyi"])
            ttk.Label(row, textvariable=note_var, style="PanelMuted.TLabel", wraplength=720).pack(anchor="w", pady=(SPACING["xs"], 0))

            test_result_var = tk.StringVar(value="")
            test_result_label = ttk.Label(
                row,
                textvariable=test_result_var,
                style="PanelMuted.TLabel",
                wraplength=720,
            )
            test_result_label.pack(anchor="w", pady=(SPACING["xs"], 0))

            self.account_rows[platform] = {
                "surface": row_surface,
                "status_pill": status_pill,
                "status": status_var,
                "status_label": status,
                "readiness": readiness_var,
                "readiness_label": readiness,
                "detail": detail_var,
                "note": note_var,
                "connect": connect_btn,
                "disconnect": disconnect_btn,
                "test": test_btn,
                "test_result": test_result_var,
                "test_result_label": test_result_label,
            }

        ttk.Button(
            self.accounts_frame,
            text="Refresh Accounts",
            style="Secondary.TButton",
            command=self.refresh_accounts,
        ).pack(anchor="w", pady=(SPACING["xl"], 0))

    def _build_settings_tab(self) -> None:
        # ── Backend Connection ─────────────────────────────────────────────
        conn_surface = RoundedFrame(
            self.settings_tab,
            bg_color=self.colors["bg"],
            fill_color=self.colors["panel"],
            radius=RADIUS_PANEL,
            padding=PADDING_PANEL,
            outline_color=self.colors["border_soft"],
        )
        conn_surface.pack(fill="x", pady=(0, SPACING["md"]))
        conn = conn_surface.body

        ttk.Label(conn, text="Server Connection", style="CardTitle.TLabel").pack(anchor="w", pady=(0, SPACING["md"]))

        # Server mode toggle
        current_url = self.desktop_settings.get("server_url", OFFICIAL_SERVER_URL)
        is_personal = current_url != OFFICIAL_SERVER_URL
        self._using_personal_server = tk.BooleanVar(value=is_personal)

        mode_row = ttk.Frame(conn, style="Panel.TFrame")
        mode_row.pack(fill="x", pady=(0, SPACING["md"]))

        def _on_mode_toggle() -> None:
            using_personal = self._using_personal_server.get()
            if using_personal:
                self.server_url_entry.configure(state="normal")
                if self.server_url_entry.get().strip() == OFFICIAL_SERVER_URL:
                    self.server_url_entry.delete(0, "end")
            else:
                self.server_url_entry.delete(0, "end")
                self.server_url_entry.insert(0, OFFICIAL_SERVER_URL)
                self.server_url_entry.configure(state="disabled")

        official_btn = ttk.Radiobutton(
            mode_row, text="Official ReelPush Server", variable=self._using_personal_server,
            value=False, command=_on_mode_toggle,
        )
        official_btn.pack(side="left", padx=(0, SPACING["xl"]))
        personal_btn = ttk.Radiobutton(
            mode_row, text="Personal / Self-Hosted Server", variable=self._using_personal_server,
            value=True, command=_on_mode_toggle,
        )
        personal_btn.pack(side="left")

        ttk.Label(conn, text="SERVER URL", style="Micro.TLabel").pack(anchor="w")
        server_url_surface, self.server_url_entry = self._make_entry_field(conn, bg_color=self.colors["panel"])
        self.server_url_entry.insert(0, current_url)
        if not is_personal:
            self.server_url_entry.configure(state="disabled")
        server_url_surface.pack(fill="x", pady=(4, 0))
        ttk.Label(
            conn,
            text="Personal server URL, e.g. https://reelpush.yourdomain.com or http://1.2.3.4:8100",
            style="FieldHelp.TLabel",
        ).pack(anchor="w", pady=(SPACING["xs"], SPACING["md"]))

        creds_row = ttk.Frame(conn, style="Panel.TFrame")
        creds_row.pack(fill="x", pady=(0, SPACING["xs"]))
        creds_row.columnconfigure(0, weight=1)
        creds_row.columnconfigure(1, weight=1)

        email_frame = ttk.Frame(creds_row, style="Panel.TFrame")
        email_frame.grid(row=0, column=0, sticky="ew", padx=(0, SPACING["md"]))
        ttk.Label(email_frame, text="YOUR EMAIL", style="Micro.TLabel").pack(anchor="w")
        email_surface, self.server_email_entry = self._make_entry_field(email_frame, bg_color=self.colors["panel"])
        self.server_email_entry.insert(0, self.desktop_settings.get("login_email", ""))
        email_surface.pack(fill="x", pady=(4, 0))

        pw_frame = ttk.Frame(creds_row, style="Panel.TFrame")
        pw_frame.grid(row=0, column=1, sticky="ew")
        ttk.Label(pw_frame, text="YOUR PASSWORD", style="Micro.TLabel").pack(anchor="w")
        pw_surface, self.server_password_entry = self._make_entry_field(pw_frame, show="*", bg_color=self.colors["panel"])
        self.server_password_entry.insert(0, self.desktop_settings.get("login_password", ""))
        pw_surface.pack(fill="x", pady=(4, 0))

        ttk.Label(conn, text="Server URL and login are saved locally on this device.", style="FieldHelp.TLabel").pack(
            anchor="w", pady=(SPACING["xs"], SPACING["md"])
        )

        conn_actions = ttk.Frame(conn, style="Panel.TFrame")
        conn_actions.pack(anchor="w")
        self.server_save_button = ttk.Button(
            conn_actions, text="Save & Connect", style="Accent.TButton", command=self.save_server_settings
        )
        self.server_save_button.pack(side="left")
        ttk.Button(
            conn_actions, text="Test Connection", style="Secondary.TButton", command=self.test_server_connection
        ).pack(side="left", padx=(SPACING["md"], 0))
        ttk.Button(
            conn_actions, text="Create Account", style="Secondary.TButton", command=self.show_register
        ).pack(side="left", padx=(SPACING["md"], 0))

        self.server_conn_status_var = tk.StringVar(
            value="Using official ReelPush server." if not is_personal else "Enter your server URL and credentials, then click Save & Connect."
        )
        ttk.Label(conn, textvariable=self.server_conn_status_var, style="Muted.TLabel", wraplength=620).pack(
            anchor="w", pady=(SPACING["md"], 0)
        )

        # ── Appearance ─────────────────────────────────────────────────────
        panel_surface = RoundedFrame(
            self.settings_tab,
            bg_color=self.colors["bg"],
            fill_color=self.colors["panel"],
            radius=RADIUS_PANEL,
            padding=PADDING_PANEL,
            outline_color=self.colors["border_soft"],
        )
        panel_surface.pack(fill="x")
        panel = panel_surface.body

        ttk.Label(panel, text="Studio Appearance", style="CardTitle.TLabel").pack(anchor="w", pady=(0, SPACING["md"]))
        ttk.Label(panel, text="ACCENT PRESET", style="Micro.TLabel").pack(anchor="w")
        theme_names = [theme["name"] for theme in THEMES.values()]
        self.theme_var = tk.StringVar(value=THEMES[self.theme_key]["name"])
        self.theme_dropdown = self._make_dropdown_field(
            panel,
            variable=self.theme_var,
            values=theme_names,
            command=lambda _choice: self._preview_theme_description(),
            bg_color=self.colors["panel"],
        )
        self.theme_dropdown.pack(anchor="w", fill="x", pady=(4, 8))

        self.theme_description_var = tk.StringVar(value=THEMES[self.theme_key]["description"])  # type: ignore
        ttk.Label(panel, textvariable=self.theme_description_var, style="Muted.TLabel", wraplength=620).pack(anchor="w")
        self._build_accent_preset_swatches(panel)

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.pack(anchor="w", pady=(SPACING["lg"], 0))
        ttk.Button(actions, text="Apply Accent", style="Accent.TButton", command=self.apply_selected_theme).pack(side="left")

        self.settings_status_var = tk.StringVar(value="Accent preference is saved for this desktop app.")
        ttk.Label(panel, textvariable=self.settings_status_var, style="Muted.TLabel").pack(anchor="w", pady=(SPACING["md"], 0))

        credentials_surface = RoundedFrame(
            self.settings_tab,
            bg_color=self.colors["bg"],
            fill_color=self.colors["panel"],
            radius=RADIUS_PANEL,
            padding=PADDING_PANEL,
            outline_color=self.colors["border_soft"],
        )
        credentials_surface.pack(fill="x", pady=(SPACING["md"], 0))
        credentials = credentials_surface.body
        ttk.Label(credentials, text="Platform Credentials", style="CardTitle.TLabel").pack(anchor="w", pady=(0, SPACING["md"]))
        ttk.Label(
            credentials,
            text=(
                "Enter your platform OAuth credentials below. They are encrypted and stored on the server "
                "so they sync automatically when you log in on any computer."
            ),
            style="Muted.TLabel",
            wraplength=620,
        ).pack(anchor="w", pady=(0, SPACING["lg"]))

        self.credential_entries: dict[str, tk.Entry] = {}
        self.credential_test_buttons: dict[str, ttk.Button] = {}
        self.credential_test_status_vars: dict[str, tk.StringVar] = {}
        self.credential_test_status_labels: dict[str, ttk.Label] = {}
        self.oauth_redirect_vars: dict[str, tk.StringVar] = {}
        self.credential_field_rows = []

        # Mapping from CREDENTIAL_FIELDS env key to app settings key
        _cred_key_map = {
            "YOUTUBE_CLIENT_ID": "youtube_client_id",
            "YOUTUBE_CLIENT_SECRET": "youtube_client_secret",
            "INSTAGRAM_APP_ID": "instagram_app_id",
            "INSTAGRAM_APP_SECRET": "instagram_app_secret",
            "TIKTOK_CLIENT_KEY": "tiktok_client_key",
            "TIKTOK_CLIENT_SECRET": "tiktok_client_secret",
        }
        stored = getattr(self, "cloud_app_settings", {}) or {}

        for platform_name, fields in CREDENTIAL_FIELDS.items():
            platform = platform_name.lower()
            platform_frame = ttk.Frame(credentials, style="Panel.TFrame")
            platform_frame.pack(fill="x", pady=(0, SPACING["sm"]))
            platform_frame.columnconfigure(1, weight=1)

            # Row 0: platform name + test status + test button
            ttk.Label(platform_frame, text=platform_name, style="Body.TLabel", width=12).grid(
                row=0, column=0, sticky="nw", padx=(0, SPACING["md"]), pady=(SPACING["xs"], 0)
            )
            fields_frame = ttk.Frame(platform_frame, style="Panel.TFrame")
            fields_frame.grid(row=0, column=1, sticky="ew")
            fields_frame.columnconfigure(0, weight=1)

            for fi, (env_key, label, is_secret) in enumerate(fields):
                settings_key = _cred_key_map.get(env_key, env_key.lower())
                ttk.Label(fields_frame, text=label, style="Micro.TLabel").grid(row=fi * 2, column=0, sticky="w")
                show_char = "*" if is_secret else ""
                surf, entry = self._make_entry_field(fields_frame, show=show_char, bg_color=self.colors["panel"])
                surf.grid(row=fi * 2 + 1, column=0, sticky="ew", pady=(2, SPACING["xs"]))
                stored_val = stored.get(settings_key, "")
                if stored_val:
                    entry.insert(0, stored_val)
                self.credential_entries[env_key] = entry

            test_col_frame = ttk.Frame(platform_frame, style="Panel.TFrame")
            test_col_frame.grid(row=0, column=2, sticky="ne", padx=(SPACING["md"], 0))
            test_status_var = tk.StringVar(value="")
            test_status_label = ttk.Label(
                test_col_frame, textvariable=test_status_var, style="Muted.TLabel", wraplength=200,
            )
            test_status_label.pack(anchor="e")
            test_button = ttk.Button(
                test_col_frame,
                text="Test",
                style="Secondary.TButton",
                command=lambda p=platform: self.test_platform_credentials(p),
            )
            test_button.pack(anchor="e", pady=(SPACING["xs"], 0))

            self.credential_test_buttons[platform] = test_button
            self.credential_test_status_vars[platform] = test_status_var
            self.credential_test_status_labels[platform] = test_status_label

            if platform == "youtube":
                redirect_frame = ttk.Frame(credentials, style="Panel.TFrame")
                redirect_frame.pack(fill="x", pady=(0, SPACING["md"]))
                redirect_frame.columnconfigure(0, weight=1)
                ttk.Label(redirect_frame, text="Google redirect URI", style="Muted.TLabel").grid(
                    row=0, column=0, columnspan=2, sticky="w"
                )
                redirect_var = tk.StringVar(value=OAUTH_REDIRECT_URIS[platform])
                self.oauth_redirect_vars[platform] = redirect_var
                ttk.Label(
                    redirect_frame, textvariable=redirect_var, style="PanelBody.TLabel", wraplength=560
                ).grid(row=1, column=0, sticky="ew", pady=(SPACING["xs"], 0))
                ttk.Button(
                    redirect_frame,
                    text="Copy",
                    style="Secondary.TButton",
                    command=lambda p=platform: self.copy_oauth_redirect_uri(p),
                ).grid(row=1, column=1, sticky="e", padx=(SPACING["md"], 0), pady=(SPACING["xs"], 0))
                ttk.Label(
                    redirect_frame,
                    text="Add this exact URL to the authorized redirect URIs for the same Google OAuth client ID.",
                    style="Muted.TLabel",
                    wraplength=620,
                ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(SPACING["sm"], 0))

        cred_actions = ttk.Frame(credentials, style="Panel.TFrame")
        cred_actions.pack(anchor="w", pady=(SPACING["lg"], 0))
        ttk.Button(cred_actions, text="Save to Cloud", style="Accent.TButton", command=self.save_credentials_to_cloud).pack(side="left")
        ttk.Button(cred_actions, text="Load from Cloud", style="Secondary.TButton", command=self.load_credentials_from_cloud).pack(side="left", padx=(SPACING["md"], 0))

        self.credentials_status_var = tk.StringVar(value="")
        ttk.Label(credentials, textvariable=self.credentials_status_var, style="Muted.TLabel", wraplength=620).pack(
            anchor="w", pady=(SPACING["xs"], 0)
        )


    def copy_oauth_redirect_uri(self, platform: str) -> None:
        uri = OAUTH_REDIRECT_URIS.get(platform)
        if not uri:
            return
        self.clipboard_clear()
        self.clipboard_append(uri)
        self.credentials_status_var.set(f"Copied {PLATFORM_DETAILS[platform]['short_name']} redirect URI.")

    _CRED_ENV_TO_SETTINGS = {
        "YOUTUBE_CLIENT_ID": "youtube_client_id",
        "YOUTUBE_CLIENT_SECRET": "youtube_client_secret",
        "INSTAGRAM_APP_ID": "instagram_app_id",
        "INSTAGRAM_APP_SECRET": "instagram_app_secret",
        "TIKTOK_CLIENT_KEY": "tiktok_client_key",
        "TIKTOK_CLIENT_SECRET": "tiktok_client_secret",
    }

    def _collect_credential_entries(self) -> dict[str, str]:
        result = {}
        for env_key, entry in self.credential_entries.items():
            val = entry.get().strip()
            settings_key = self._CRED_ENV_TO_SETTINGS.get(env_key, env_key.lower())
            if val:
                result[settings_key] = val
        return result

    def save_credentials_to_cloud(self) -> None:
        creds = self._collect_credential_entries()
        if not creds:
            self.credentials_status_var.set("No credentials to save.")
            return

        def work() -> dict:
            return self.client.put_app_settings(creds)

        def _save_worker() -> None:
            try:
                result = self._call_with_reauth(work)
                self.results_queue.put(("cred-save-ok", result))
            except Exception as exc:  # noqa: BLE001
                self.results_queue.put(("cred-save-error", str(exc)))

        self.credentials_status_var.set("Saving credentials to cloud…")
        threading.Thread(target=_save_worker, daemon=True).start()

    def load_credentials_from_cloud(self) -> None:
        def _load_worker() -> None:
            try:
                result = self._call_with_reauth(self.client.get_app_settings)
                self.results_queue.put(("cred-load-ok", result))
            except Exception as exc:  # noqa: BLE001
                self.results_queue.put(("cred-load-error", str(exc)))

        self.credentials_status_var.set("Loading credentials from cloud…")
        threading.Thread(target=_load_worker, daemon=True).start()

    def _apply_cloud_credentials(self, data: dict) -> None:
        """Populate credential entry fields from cloud settings data."""
        for env_key, entry in self.credential_entries.items():
            settings_key = self._CRED_ENV_TO_SETTINGS.get(env_key, env_key.lower())
            val = data.get(settings_key, "")
            if val:
                entry.delete(0, "end")
                entry.insert(0, val)

    def _build_accent_preset_swatches(self, parent: ttk.Frame) -> None:
        swatches = ttk.Frame(parent, style="Panel.TFrame")
        swatches.pack(fill="x", pady=(SPACING["md"], 0))
        self.accent_swatch_widgets: dict[str, dict[str, Any]] = {}
        for key, theme in THEMES.items():
            colors = theme["colors"]
            swatch = RoundedFrame(
                swatches,
                bg_color=self.colors["panel"],
                fill_color=colors["accent_panel"],
                radius=RADIUS_CHIP,
                padding=SPACING["xs"],
                outline_color=self.colors["accent"] if key == self.theme_key else self.colors["border_soft"],
                outline_width=2 if key == self.theme_key else 1,
            )
            swatch.pack(side="left", padx=(0, SPACING["sm"]), pady=(0, SPACING["xs"]))
            chip = tk.Label(
                swatch.body,
                text=theme["name"],
                bg=colors["accent_panel"],
                fg=self.colors["text"],
                font=self.meta_font,
                padx=SPACING["md"],
                pady=SPACING["sm"],
                cursor="hand2",
            )
            chip.pack()
            for widget in (swatch, swatch.body, chip):
                widget.bind("<Button-1>", lambda _event, preset=key: self._select_accent_preset(preset))
            self.accent_swatch_widgets[key] = {"surface": swatch, "label": chip}

    def _select_accent_preset(self, key: str) -> None:
        if key not in THEMES:
            return
        self.theme_var.set(THEMES[key]["name"])
        self._preview_theme_description()
        self._update_accent_swatch_selection()

    def _update_accent_swatch_selection(self) -> None:
        selected_key = self._theme_key_from_name(self.theme_var.get())
        for key, widgets in getattr(self, "accent_swatch_widgets", {}).items():
            selected = key == selected_key
            widgets["surface"].outline_width = 2 if selected else 1
            widgets["surface"].set_outline(self.colors["accent"] if selected else self.colors["border_soft"])

    def _theme_key_from_name(self, name: str) -> str:
        for key, theme in THEMES.items():
            if theme["name"] == name:
                return key
        return DEFAULT_THEME

    def _preview_theme_description(self) -> None:
        key = self._theme_key_from_name(self.theme_var.get())
        self.theme_description_var.set(THEMES[key]["description"])
        self._update_accent_swatch_selection()

    def _set_settings_credential_status(self, platform: str, message: str, style: str) -> None:
        status_var = self.credential_test_status_vars.get(platform)
        status_label = self.credential_test_status_labels.get(platform)
        if status_var and status_label:
            status_var.set(message)
            status_label.configure(style=style)

    def save_server_settings(self) -> None:
        using_personal = getattr(self, "_using_personal_server", None)
        if using_personal and not using_personal.get():
            url = OFFICIAL_SERVER_URL
        else:
            url = self.server_url_entry.get().strip().rstrip("/")
        email = self.server_email_entry.get().strip()
        password = self.server_password_entry.get().strip()
        if not url:
            self.server_conn_status_var.set("Enter a server URL first.")
            return
        self.desktop_settings["server_url"] = url
        self.desktop_settings["login_email"] = email
        self.desktop_settings["login_password"] = password
        _save_desktop_settings(self.desktop_settings)
        _set_active_api_url(url)
        for platform, var in self.oauth_redirect_vars.items():
            var.set(OAUTH_REDIRECT_URIS[platform])
        self.server_conn_status_var.set("Saved. Testing connection…")
        self.server_save_button.configure(state="disabled")
        self._run_bg(
            lambda: self._do_test_server_connection(url, email, password),
            "server-connection-tested",
        )

    def test_server_connection(self) -> None:
        url = self.server_url_entry.get().strip().rstrip("/")
        email = self.server_email_entry.get().strip()
        password = self.server_password_entry.get().strip()
        if not url:
            self.server_conn_status_var.set("Enter a server URL first.")
            return
        self.server_conn_status_var.set("Testing…")
        self._run_bg(
            lambda: self._do_test_server_connection(url, email, password),
            "server-connection-tested",
        )

    def _do_test_server_connection(self, url: str, email: str, password: str) -> dict[str, Any]:
        base = _api_base_for_url(url)
        try:
            with request.urlopen(request.Request(f"{base}/health"), timeout=5) as resp:
                if resp.status != 200:
                    return {"ok": False, "error": f"Health check returned HTTP {resp.status}."}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Cannot reach {url}: {exc}"}
        if not email or not password:
            return {"ok": True, "email": None}
        try:
            payload = json.dumps({"email": email, "password": password}).encode()
            login_req = request.Request(
                f"{base}/auth/login",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(login_req, timeout=5) as resp:
                result = json.loads(resp.read())
                self.client.token = result["access_token"]
            return {"ok": True, "email": email}
        except error.HTTPError as exc:
            if exc.code == 401:
                return {"ok": False, "error": "Invalid email or password."}
            return {"ok": False, "error": f"Login failed (HTTP {exc.code})."}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Login error: {exc}"}

    def apply_selected_theme(self) -> None:
        key = self._theme_key_from_name(self.theme_var.get())
        self.theme_key = key
        self.colors = THEMES[key]["colors"]
        self.desktop_settings["theme"] = key
        _save_desktop_settings(self.desktop_settings)
        self._build_styles()
        self._apply_theme_to_existing_widgets()
        self.settings_status_var.set(f"{THEMES[key]['name']} accent saved.")

    def _apply_theme_to_existing_widgets(self) -> None:
        colors = self.colors
        self.configure(bg=colors["bg"])
        for scroll_name in ("publish_scroll", "accounts_scroll", "settings_scroll"):
            scroll = getattr(self, scroll_name, None)
            if scroll is not None:
                scroll.set_bg(colors["bg"])
        for field in getattr(self, "dropdown_fields", []):
            field.refresh_colors(colors)
        for surface_name in (
            "title_input_surface",
            "caption_surface",
            "hashtags_input_surface",
            "schedule_input_surface",
        ):
            surface = getattr(self, surface_name, None)
            if surface is not None:
                surface.set_bg(colors["panel"])
                surface.set_fill(colors["field"])
                self._apply_field_outline(surface)
        for entry_name in ("title_entry", "hashtags_entry", "schedule_entry"):
            entry = getattr(self, entry_name, None)
            if entry is not None:
                entry.configure(
                    bg=colors["field"],
                    fg=colors["muted"] if getattr(entry, "_rp_placeholder_active", False) else colors["text"],
                    insertbackground=colors["text"],
                )
        if hasattr(self, "caption_text"):
            self.caption_text.configure(
                bg=colors["field"],
                fg=colors["muted"] if getattr(self.caption_text, "_rp_placeholder_active", False) else colors["text"],
                insertbackground=colors["text"],
            )
        if hasattr(self, "publish_log"):
            self.publish_log.configure(
                bg=colors["log"],
                fg=colors["text"],
                insertbackground=colors["text"],
            )
        if hasattr(self, "video_placeholder"):
            self.video_placeholder.configure(bg=colors["soft_panel"])
            self._update_video_preview()
        if hasattr(self, "section_frames"):
            self._set_active_section(getattr(self, "active_section", "publishing"))
        self._refresh_delivery_readiness()
        self._update_accent_swatch_selection()


    def load_workspace_data(self) -> None:
        def work() -> dict[str, Any]:
            return {
                "statuses": self.client.oauth_statuses(),
                "profile": self.client.get_profile(),
                "staged": self.client.get_staged(),
                "runtime": self._collect_runtime_statuses(),
            }

        def runner() -> None:
            try:
                self.results_queue.put(("data-loaded", self._call_with_reauth(work)))
            except Exception as exc:  # noqa: BLE001
                self.results_queue.put(("data-error", str(exc)))

        threading.Thread(target=runner, daemon=True).start()

    def _apply_workspace_data(self, payload: dict[str, Any]) -> None:
        profile = payload["profile"]
        staged = payload["staged"]
        statuses = payload["statuses"]
        runtime = payload.get("runtime", {})
        self.latest_platform_statuses = statuses
        self.latest_staged = staged
        self.latest_runtime_statuses = runtime

        self._set_entry_value(self.title_entry, profile.get("default_title") or "")

        self._set_text_value(self.caption_text, profile.get("default_caption") or "")

        self._set_entry_value(self.hashtags_entry, profile.get("default_hashtags") or "")

        self.privacy_var.set(profile.get("default_privacy") or "public")

        self.staged_upload = staged.get("upload")
        self._update_video_preview()

        selected = set(staged.get("selected_platforms") or [])
        for platform, var in self.selected_platform_vars.items():
            var.set(platform in selected)

        self._update_character_counts()
        self._apply_account_statuses(statuses)
        self._apply_runtime_statuses(runtime)
        self._apply_delivery_readiness(statuses, staged)
        self.status_var.set("Workspace loaded")

    def _apply_runtime_statuses(self, statuses: dict[str, dict[str, Any]]) -> None:
        for key, row in getattr(self, "service_rows", {}).items():
            status = statuses.get(key, {})
            ok = bool(status.get("ok"))
            label = status.get("label") or ("Online" if ok else "Offline")
            detail = str(status.get("detail") or LOCAL_SERVICES.get(key, key)).replace("\n", " ")
            if len(detail) > 58:
                detail = f"{detail[:55]}..."
            row["status"].set(label)
            row["detail"].set(detail)
            row["label"].configure(style="Good.TLabel" if ok else "Bad.TLabel")
            if "dot" in row:
                fill = self.colors["good"] if ok else self.colors["bad"]
                row["dot"].delete("all")
                row["dot"].create_oval(2, 2, 7, 7, fill=fill, outline="")

    def _apply_account_statuses(self, statuses: list[dict[str, Any]]) -> None:
        by_platform = {status["platform"]: status for status in statuses}
        for platform, row in self.account_rows.items():
            status = by_platform.get(platform, {"platform": platform})
            diagnostics = self._platform_diagnostics(status)

            row["status"].set(diagnostics["account"])
            row["readiness"].set(diagnostics["readiness"])
            row["detail"].set(diagnostics["detail"])
            row["note"].set(diagnostics["fyi"])

            style = self._tone_style(diagnostics["tone"], panel=True)
            tone_color = self.colors["good"] if diagnostics["tone"] == "good" else self.colors["warn"] if diagnostics["tone"] == "warn" else self.colors["bad"]
            row["status_pill"].set_fill(self.colors["accent_panel"] if diagnostics["tone"] == "good" else self.colors["field"])
            row["status_pill"].set_outline(tone_color)
            row["status_label"].configure(bg=row["status_pill"].fill_color, fg=tone_color)
            row["readiness_label"].configure(style=style)
            row["connect"].configure(state="normal" if diagnostics["can_connect"] else "disabled")
            row["disconnect"].configure(state="normal" if status.get("connected", False) else "disabled")
            row["test"].configure(state="normal" if status.get("connected", False) else "disabled")
            if not row["test_result"].get():
                row["test_result_label"].configure(style="PanelMuted.TLabel")

            platform_row = self.platform_rows.get(platform)
            if platform_row:
                platform_row["status"].set(diagnostics["status_badge"])
                platform_row["readiness"].set(diagnostics["detail"])
                platform_row["tone"] = diagnostics["tone"]
        self._refresh_platform_selection_styles()

    def _apply_delivery_readiness(self, statuses: list[dict[str, Any]], staged: dict[str, Any]) -> None:
        selected = set(self._selected_platforms())
        has_upload = bool(getattr(self, "staged_upload", None) or staged.get("upload"))
        title = self._entry_value(self.title_entry) if hasattr(self, "title_entry") else ""
        caption = self._text_value(self.caption_text) if hasattr(self, "caption_text") else ""
        privacy = self.privacy_var.get().strip() if hasattr(self, "privacy_var") else ""
        schedule_value = self.schedule_var.get().strip() if hasattr(self, "schedule_var") else ""
        diagnostics = [self._platform_diagnostics(status) for status in statuses if status["platform"] in selected]
        diagnosed_platforms = {item["platform"] for item in diagnostics}
        runtime_statuses = getattr(self, "latest_runtime_statuses", {})
        runtime_blockers = self._runtime_publish_blockers(runtime_statuses) if runtime_statuses else ["Checking network connection."]
        network_ok = not runtime_blockers

        title_ok = 0 < len(title) <= 100
        caption_ok = 0 < len(caption) <= 2200
        privacy_ok = bool(privacy)
        title_detail = "Ready" if title_ok else ("Shorten title to 100 characters or fewer." if title else "Add a post title.")
        caption_detail = "Ready" if caption_ok else ("Shorten caption to 2,200 characters or fewer." if caption else "Add a caption.")
        details_ok = title_ok and caption_ok
        details_state = "complete" if details_ok else ("warning" if (title and not title_ok) or (caption and not caption_ok) else "missing")
        details_detail = "Ready" if details_ok else " ".join(part for part in (title_detail, caption_detail) if part != "Ready")

        platform_selected_ok = bool(selected)
        platform_selected_detail = "Ready" if platform_selected_ok else "Select at least one platform."
        credentials_state = "missing"
        credentials_detail = "Select at least one platform."
        missing_status = selected - diagnosed_platforms
        if missing_status:
            names = ", ".join(PLATFORM_DETAILS.get(platform, {"name": platform.title()})["name"] for platform in missing_status)
            credentials_state = "warning"
            credentials_detail = f"Still checking: {names}."
        elif selected:
            blocked = [item for item in diagnostics if not item["can_publish_now"]]
            if blocked:
                credentials_detail = " ".join(f"{item['name']}: {item['detail']}" for item in blocked)
            else:
                credentials_state = "complete"
                ready_names = ", ".join(item["name"] for item in diagnostics)
                credentials_detail = f"Ready for {ready_names}."

        schedule_ok = not schedule_value
        schedule_state = "complete" if schedule_ok else "warning"
        schedule_detail = (
            "Ready"
            if schedule_ok
            else "Scheduled desktop publishing is not available in this local flow. Clear Schedule to publish now."
        )

        checks = {
            "network": ("complete" if network_ok else "missing", "Ready" if network_ok else " ".join(runtime_blockers)),
            "video": ("complete" if has_upload else "missing", "Ready" if has_upload else "Choose a video in the publish panel."),
            "details": (details_state, details_detail or "Add title and caption."),
            "platform_selected": ("complete" if platform_selected_ok else "missing", platform_selected_detail),
            "platform_credentials": (credentials_state, credentials_detail),
            "schedule": (schedule_state, schedule_detail),
        }
        ready_to_publish = all(state == "complete" for state, _detail in checks.values())
        checks["ready"] = (
            "complete" if ready_to_publish else "missing",
            "All required checks passed."
            if ready_to_publish
            else "Resolve the remaining checklist items.",
        )

        for key, (state, detail) in checks.items():
            self._set_readiness_item(key, state, detail)

        completed_requirements = sum(
            int(value)
            for value in (
                title_ok,
                caption_ok,
                has_upload,
                platform_selected_ok,
            )
        )
        self._set_post_requirement("title", title_ok)
        self._set_post_requirement("caption", caption_ok)
        self._set_post_requirement("video", has_upload)
        self._set_post_requirement("platforms", platform_selected_ok)
        if hasattr(self, "post_requirements_summary_var"):
            self.post_requirements_summary_var.set(f"{completed_requirements} of 4 complete")

        if ready_to_publish:
            summary = "Ready to publish"
            summary_detail = "All required checks passed."
            summary_style = "PanelGood.TLabel"
        elif not has_upload:
            summary = "Missing video"
            summary_detail = "Choose a video before publishing."
            summary_style = "PanelBad.TLabel"
        elif not network_ok:
            summary = "Network unavailable"
            summary_detail = " ".join(runtime_blockers)
            summary_style = "PanelBad.TLabel"
        elif not platform_selected_ok:
            summary = "No platforms selected"
            summary_detail = "Select at least one publishing target."
            summary_style = "PanelBad.TLabel"
        elif caption and len(caption) > 2200:
            summary = "Caption too long"
            summary_detail = caption_detail
            summary_style = "PanelBad.TLabel"
        elif not caption:
            summary = "Caption missing"
            summary_detail = "Write a caption for this post."
            summary_style = "PanelBad.TLabel"
        elif not title:
            summary = "Title missing"
            summary_detail = "Enter a post title."
            summary_style = "PanelBad.TLabel"
        elif credentials_state != "complete":
            summary = "Account not connected"
            summary_detail = credentials_detail
            summary_style = "PanelWarn.TLabel" if credentials_state == "warning" else "PanelBad.TLabel"
        elif schedule_state != "complete":
            summary = "Needs attention"
            summary_detail = schedule_detail
            summary_style = "PanelWarn.TLabel"
        elif not privacy_ok:
            summary = "Needs attention"
            summary_detail = "Select a privacy setting."
            summary_style = "PanelWarn.TLabel"
        else:
            summary = "Needs attention"
            summary_detail = checks["ready"][1]
            summary_style = "PanelWarn.TLabel"

        self.delivery_summary_var.set(summary)
        self.delivery_detail_var.set(summary_detail)
        if hasattr(self, "delivery_summary_label"):
            self.delivery_summary_label.configure(style=summary_style)
        self.publish_button.configure(state="normal" if ready_to_publish else "disabled")
        self._refresh_platform_selection_styles()

        details_status = "Ready" if details_ok else ("Needs Attention" if details_state == "warning" else "Incomplete")
        video_status = "Ready" if has_upload else "Incomplete"
        platforms_status = "Ready" if platform_selected_ok else "Incomplete"
        validate_status = "Ready" if ready_to_publish else (
            "Needs Attention"
            if not network_ok or details_state == "warning" or credentials_state != "complete" or schedule_state != "complete"
            else "Incomplete"
        )
        self._update_workflow_status(
            details_status=details_status,
            details_detail="Details ready" if details_ok else ("Fix copy limits" if details_state == "warning" else "Add title and caption"),
            video_status=video_status,
            video_detail="Video selected" if has_upload else "Select video",
            platforms_status=platforms_status,
            platforms_detail="Targets selected" if platform_selected_ok else "Choose platforms",
            validate_status=validate_status,
            validate_detail="Ready to publish" if ready_to_publish else summary,
        )

    def _set_readiness_item(self, key: str, state: str, detail: str) -> None:
        row = self.readiness_rows.get(key)
        if not row:
            return
        labels = {
            "complete": ("Complete", "PanelSmallGood.TLabel", "PanelBody.TLabel"),
            "warning": ("Warning", "PanelSmallWarn.TLabel", "PanelMuted.TLabel"),
            "missing": ("Missing", "PanelSmallBad.TLabel", "PanelMuted.TLabel"),
        }
        text, icon_style, label_style = labels.get(state, labels["missing"])
        row["icon"].set(text)
        row["detail"].set(detail)
        row["icon_label"].configure(style=icon_style)
        row["text_label"].configure(style=label_style)

    def _refresh_delivery_readiness(self) -> None:
        if hasattr(self, "delivery_summary_var"):
            self._apply_delivery_readiness(self.latest_platform_statuses, self.latest_staged)

    def _current_profile_payload(self) -> dict[str, Any]:
        return {
            "default_title": self._entry_value(self.title_entry) or None,
            "default_caption": self._text_value(self.caption_text) or None,
            "default_hashtags": self._entry_value(self.hashtags_entry) or None,
            "default_privacy": self.privacy_var.get(),
        }

    def save_profile(self) -> None:
        payload = self._current_profile_payload()
        self.status_var.set("Saving publishing details…")
        self._wi_show("Saving draft…")
        self._run_bg(lambda: self.client.update_profile(payload), "profile-saved")

    def choose_video(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Choose a video",
            filetypes=[
                ("Video files", "*.mp4 *.mov *.avi *.webm *.mkv"),
                ("All files", "*.*"),
            ],
        )
        if not file_path:
            return

        self.status_var.set("Uploading video to ReelPush…")
        self._wi_show("Uploading video…")
        self.current_file_path = file_path
        self._run_bg(lambda: self.client.upload_file(file_path), "uploaded")

    def _selected_platforms(self) -> list[str]:
        return [platform for platform, var in self.selected_platform_vars.items() if var.get()]

    def save_stage(self) -> None:
        if not self.staged_upload:
            messagebox.showwarning("No video", "Choose a video first.")
            return
        platforms = self._selected_platforms()
        if not platforms:
            messagebox.showwarning("No platforms", "Select at least one platform.")
            return
        self.status_var.set("Saving details and staged publish…")
        self._wi_show("Saving stage…")
        assert self.staged_upload is not None
        upload_id = self.staged_upload["id"]
        profile_payload = self._current_profile_payload()

        def work() -> dict[str, Any]:
            self.client.update_profile(profile_payload)
            return self.client.update_staged(upload_id, platforms)

        self._run_bg(work, "stage-saved")

    def clear_stage(self) -> None:
        self.status_var.set("Clearing staged publish…")
        self._wi_show("Clearing stage…")

        def work() -> Any:
            result = self.client.clear_staged()
            self.staged_upload = None
            self.current_file_path = None
            return result

        def runner() -> None:
            try:
                work()
                self.results_queue.put(("data-loaded", {
                    "statuses": self.client.oauth_statuses(),
                    "profile": self.client.get_profile(),
                    "staged": self.client.get_staged(),
                    "runtime": self._collect_runtime_statuses(),
                }))
            except Exception as exc:  # noqa: BLE001
                self.results_queue.put(("operation-error", str(exc)))

        threading.Thread(target=runner, daemon=True).start()

    def publish_now(self) -> None:
        if not self.staged_upload:
            messagebox.showwarning("No video", "Choose a video first.")
            return
        platforms = self._selected_platforms()
        if not platforms:
            messagebox.showwarning("No platforms", "Select at least one platform.")
            return
        if getattr(self, "schedule_var", None) and self.schedule_var.get().strip():
            messagebox.showwarning(
                "Scheduling unavailable",
                "Scheduled desktop publishing is not wired into this local flow yet. Clear Schedule to publish now.",
            )
            return
        self.status_var.set("Checking network connection…")
        runtime_statuses = self._collect_runtime_statuses()
        self.latest_runtime_statuses = runtime_statuses
        self._apply_runtime_statuses(runtime_statuses)
        runtime_blockers = self._runtime_publish_blockers(runtime_statuses)
        if runtime_blockers:
            self._refresh_delivery_readiness()
            self._show_publish_blocked_reason("Network unavailable", runtime_blockers)
            messagebox.showwarning(
                "Network unavailable",
                "ReelPush needs a working network connection before posting.\n\n" + "\n".join(runtime_blockers),
            )
            self.status_var.set("Publish blocked: network unavailable.")
            return
        statuses_by_platform = {
            status.get("platform"): status
            for status in getattr(self, "latest_platform_statuses", [])
            if status.get("platform")
        }
        blocked = []
        for platform in platforms:
            diagnostics = self._platform_diagnostics(statuses_by_platform.get(platform, {"platform": platform}))
            if not diagnostics["can_publish_now"]:
                blocked.append(f"{diagnostics['name']}: {diagnostics['detail']}")
        if blocked:
            messagebox.showwarning(
                "Credentials not ready",
                "ReelPush checks credentials before posting.\n\n" + "\n".join(blocked),
            )
            return
        self.status_var.set("Saving details and publishing staged video…")
        platform_label = " and ".join(p.title() for p in platforms)
        self._wi_show(f"Publishing to {platform_label}…")
        assert self.staged_upload is not None
        publish_payload = self._current_profile_payload()
        publish_payload.update(
            {
                "upload_id": self.staged_upload["id"],
                "selected_platforms": platforms,
            }
        )

        def work() -> list[dict[str, Any]]:
            return self.client.publish_now(publish_payload)

        self._run_bg(work, "published")

    def _show_publish_blocked_reason(self, title: str, reasons: list[str]) -> None:
        if not hasattr(self, "publish_log"):
            return
        self.publish_log.configure(state="normal")
        self.publish_log.delete("1.0", "end")
        self.publish_log.insert("end", f"Status:   Blocked\n")
        self.publish_log.insert("end", f"Reason:   {title}\n")
        for reason in reasons:
            self.publish_log.insert("end", f"Detail:   {reason}\n")
        self.publish_log.configure(state="disabled")

    def _show_publish_results(self, jobs: list[dict[str, Any]]) -> None:
        self.publish_log.configure(state="normal")
        self.publish_log.delete("1.0", "end")
        for job in jobs:
            self.publish_log.insert("end", f"Platform: {job['platform']}\n")
            self.publish_log.insert("end", f"Status:   {job['status']}\n")
            error_message = job.get("error_message") or job.get("last_error")
            if error_message:
                self.publish_log.insert("end", f"Reason:   {error_message}\n")
            if job.get("platform_post_url"):
                self.publish_log.insert("end", f"Post URL: {job['platform_post_url']}\n")
            if job.get("platform_post_id"):
                self.publish_log.insert("end", f"Post ID:  {job['platform_post_id']}\n")
            self.publish_log.insert("end", "\n")
        self.publish_log.configure(state="disabled")
        self.status_var.set("Publish attempt finished.")
        self.load_workspace_data()

    def connect_platform(self, platform: str) -> None:
        try:
            url = self._call_with_reauth(lambda: self.client.oauth_connect_url(platform))
            webbrowser.open(url)
            if platform == "youtube":
                self.status_var.set(
                    "Opened YouTube approval. Google Cloud must allow "
                    f"{OAUTH_REDIRECT_URIS['youtube']}."
                )
            else:
                self.status_var.set(f"Opened {platform.title()} approval in your browser.")
            self.after(4000, self.refresh_accounts)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Connect failed", str(exc))

    def disconnect_platform(self, platform: str) -> None:
        self.status_var.set(f"Disconnecting {platform.title()}…")
        self._run_bg(lambda: self.client.oauth_disconnect(platform), None)
        self.after(1200, self.refresh_accounts)

    def test_platform_credentials(self, platform: str) -> None:
        row = self.account_rows.get(platform)
        if row:
            row["test"].configure(state="disabled")
            row["test_result"].set("Testing credentials...")
            row["test_result_label"].configure(style="PanelWarn.TLabel")
        credential_button = self.credential_test_buttons.get(platform)
        if credential_button:
            credential_button.configure(state="disabled")
        credential_status = self.credential_test_status_vars.get(platform)
        credential_status_label = self.credential_test_status_labels.get(platform)
        if credential_status and credential_status_label:
            credential_status.set("Testing...")
            credential_status_label.configure(style="Muted.TLabel")
        self.status_var.set(f"Testing {platform.title()} credentials…")
        self._wi_show(f"Testing {platform.title()} credentials…")

        def runner() -> None:
            try:
                result = self._call_with_reauth(lambda: self.client.oauth_test_credentials(platform))
                self.results_queue.put(
                    (
                        "credential-test-ok",
                        {
                            "platform": platform,
                            "message": result.get("message") or f"{platform.title()} credentials verified.",
                        },
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self.results_queue.put(
                    (
                        "credential-test-error",
                        {
                            "platform": platform,
                            "message": str(exc),
                        },
                    )
                )

        threading.Thread(target=runner, daemon=True).start()

    def refresh_accounts(self) -> None:
        self.load_workspace_data()


def main() -> None:
    app = ReelPushDesktop()
    app.mainloop()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
MIRA Chat - Rich-based CLI for chatting with MIRA.

Usage:
    python talkto_mira.py              # Interactive chat
    python talkto_mira.py --headless "message"  # One-shot query

TODO: Consider adding `questionary` for arrow-key selection menus on /model and /think.
      Works with Rich but has its own styling (prompt_toolkit-based).
"""

import argparse
import atexit
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import requests
from rich.console import Console
from rich.panel import Panel
from rich.align import Align
from rich.text import Text

from clients.vault_client import get_api_key

MIRA_API_URL = os.getenv("MIRA_API_URL", "http://localhost:1993")
REQUEST_TIMEOUT = 120
SERVER_STARTUP_TIMEOUT = 30

_server_process = None
console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

def strip_emotion_tag(text: str) -> str:
    pattern = r'\n?<mira:my_emotion>.*?</mira:my_emotion>'
    return re.sub(pattern, '', text, flags=re.DOTALL).strip()


def send_message(token: str, message: str) -> dict:
    url = f"{MIRA_API_URL}/v0/api/chat"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json={"message": message}, timeout=REQUEST_TIMEOUT)
        return response.json()
    except requests.exceptions.Timeout:
        return {"success": False, "error": {"message": "Request timed out"}}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": {"message": f"Cannot connect to {MIRA_API_URL}"}}
    except Exception as e:
        return {"success": False, "error": {"message": str(e)}}


def call_action(token: str, domain: str, action: str, data: dict = None) -> dict:
    url = f"{MIRA_API_URL}/v0/api/actions"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json={"domain": domain, "action": action, "data": data or {}}, timeout=10)
        return response.json()
    except Exception as e:
        return {"success": False, "error": {"message": str(e)}}


# ─────────────────────────────────────────────────────────────────────────────
# Preferences
# ─────────────────────────────────────────────────────────────────────────────

THINKING_LEVELS = {"off": 0, "low": 1024, "medium": 4096, "high": 32000, "default": None}
THINKING_LABELS = {0: "off", 1024: "low", 4096: "medium", 32000: "high", None: "default"}
MODEL_ALIASES = {"opus": "claude-opus-4-5-20251101", "haiku": "claude-haiku-4-5-20251001", "default": None}
MODEL_LABELS = {"claude-opus-4-5-20251101": "opus", "claude-haiku-4-5-20251001": "haiku", None: "default"}


def get_preferences(token: str) -> tuple[str, str]:
    model_resp = call_action(token, "continuum", "get_model_preference")
    think_resp = call_action(token, "continuum", "get_thinking_budget_preference")
    # API wraps handler results in {"success": ..., "data": {...}}
    model_data = model_resp.get("data", {}) if model_resp.get("success") else {}
    think_data = think_resp.get("data", {}) if think_resp.get("success") else {}
    model = model_data.get("model")
    budget = think_data.get("budget")
    return MODEL_LABELS.get(model, model or "default"), THINKING_LABELS.get(budget, str(budget) if budget else "default")


def set_model_preference(token: str, model_alias: str) -> bool:
    if model_alias not in MODEL_ALIASES:
        return False
    resp = call_action(token, "continuum", "set_model_preference", {"model": MODEL_ALIASES[model_alias]})
    return resp.get("success", False)


def set_thinking_preference(token: str, level: str) -> bool:
    if level not in THINKING_LEVELS:
        return False
    resp = call_action(token, "continuum", "set_thinking_budget_preference", {"budget": THINKING_LEVELS[level]})
    return resp.get("success", False)


# ─────────────────────────────────────────────────────────────────────────────
# Server
# ─────────────────────────────────────────────────────────────────────────────

def is_api_running() -> bool:
    try:
        response = requests.get(f"{MIRA_API_URL}/v0/api/health", timeout=2)
        return response.status_code in [200, 503]
    except:
        return False


def start_api_server() -> subprocess.Popen:
    global _server_process
    main_py = project_root / "main.py"
    if not main_py.exists():
        raise RuntimeError(f"Cannot find main.py at {main_py}")
    _server_process = subprocess.Popen([sys.executable, str(main_py)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(project_root))
    return _server_process


def wait_for_api_ready(timeout: int = SERVER_STARTUP_TIMEOUT) -> bool:
    start_time = time.time()
    while time.time() - start_time < timeout:
        if is_api_running():
            return True
        time.sleep(0.5)
    return False


def shutdown_server():
    global _server_process
    if _server_process is not None:
        try:
            _server_process.terminate()
            _server_process.wait(timeout=5)
        except:
            try:
                _server_process.kill()
            except:
                pass
        _server_process = None


# ─────────────────────────────────────────────────────────────────────────────
# Splashscreen
# ─────────────────────────────────────────────────────────────────────────────

ASCII_CHARS = ['.', '+', '*', 'o', "'", '-', '~', '|']


def clear_screen_and_scrollback() -> None:
    """Clear visible screen and scrollback buffer."""
    print("\033[2J\033[3J\033[H", end="", flush=True)


def show_splashscreen(start_server: bool = False) -> bool:
    """
    Animated ASCII splashscreen matching web loading animation.

    If start_server=True, starts the API server and animates until ready.
    Returns True if server was started, False otherwise.
    """
    if console.width < 40:
        if start_server and not is_api_running():
            start_api_server()
            return wait_for_api_ready()
        return False

    clear_screen_and_scrollback()

    width = console.width
    frame_delay = 0.05
    min_frames = 40  # Minimum 2 seconds of animation
    max_frames = int(SERVER_STARTUP_TIMEOUT / frame_delay)  # Max based on server timeout

    # Initialize character line (sparse like web version)
    chars = []
    for i in range(width):
        if i == 0 or i == width - 1 or random.random() < 0.2:
            chars.append(random.choice(ASCII_CHARS))
        else:
            chars.append(' ')

    # Center vertically
    vertical_pos = console.height // 2

    # Start server if requested
    server_started = False
    if start_server and not is_api_running():
        start_api_server()
        server_started = True

    frame = 0
    server_ready = not start_server or is_api_running()  # Already ready if not starting

    while frame < max_frames:
        # Check if server is ready (after minimum animation time)
        if server_started and frame >= min_frames:
            if is_api_running():
                server_ready = True
                break

        # Randomly mutate some characters each frame
        for i in range(width):
            if random.random() < 0.15:
                if random.random() < 0.3:
                    chars[i] = random.choice(ASCII_CHARS)
                else:
                    chars[i] = ' '

        # Render frame
        line = ''.join(chars)
        print(f"\033[{vertical_pos};1H", end="")  # Move cursor to center row
        console.print(line, style="bright_green", end="", highlight=False)
        time.sleep(frame_delay)
        frame += 1

        # If not waiting for server, just run minimum frames
        if not server_started and frame >= min_frames:
            break

    clear_screen_and_scrollback()
    return server_started and server_ready


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────

def render_user_message(text: str) -> None:
    """Render a user message - cyan border, right-aligned."""
    width = min(len(text) + 4, int(console.width * 0.6))
    panel = Panel(text, border_style="cyan", width=max(width, 20), padding=(0, 1))
    console.print(Align.right(panel))


def render_mira_message(text: str, is_error: bool = False) -> None:
    """Render a MIRA message - magenta border, left-aligned."""
    width = min(len(max(text.split('\n'), key=len)) + 4, int(console.width * 0.7))
    style = "red" if is_error else "magenta"
    panel = Panel(text, border_style=style, width=max(width, 20), padding=(0, 1))
    console.print(panel)


def render_status_bar(model: str, thinking: str) -> None:
    """Render the status bar. Hides default values."""
    # Build left side - only show non-default values
    parts = []
    if model != "default":
        parts.append(model)
    if thinking != "default":
        parts.append(thinking)

    left_text = " • ".join(parts) if parts else ""
    left = Text(f" {left_text}" if left_text else "", style="cyan")
    right = Text("/help • ctrl+c quit", style="dim")

    padding = console.width - len(left.plain) - len(right.plain)
    console.print(Text.assemble(left, " " * max(padding, 1), right))
    console.print("─" * console.width, style="dim")


def render_screen(
    history: list[tuple[str, str]],
    model: str,
    thinking: str,
    pending_user_msg: str = None,
    show_thinking: bool = False
) -> None:
    """Clear and render the full screen with status bar always at bottom."""
    clear_screen_and_scrollback()

    # Calculate content height
    content_lines = len(history) * 8 + 2
    if pending_user_msg:
        content_lines += 4
    if show_thinking:
        content_lines += 4
    terminal_height = console.height

    # Push content to bottom if not enough to fill screen
    if content_lines < terminal_height - 2:
        blank_lines = terminal_height - content_lines - 2
        console.print("\n" * blank_lines, end="")

    # Render history
    for user_msg, mira_msg in history:
        render_user_message(user_msg)
        console.print()
        render_mira_message(mira_msg)
        console.print()

    # Render pending message (not yet in history)
    if pending_user_msg:
        render_user_message(pending_user_msg)
        console.print()

    # Render thinking indicator
    if show_thinking:
        render_thinking()
        console.print()

    # Status bar always last
    render_status_bar(model, thinking)


class ThinkingAnimation:
    """Animated bouncing face while waiting for response."""

    FACE = "^_^"
    WIDTH = 12  # Inner width for bouncing

    def __init__(self):
        self.running = False
        self.thread = None
        self.position = 0
        self.direction = 1

    def start(self):
        """Start the animation in a background thread."""
        self.running = True
        self.position = 0
        self.direction = 1
        self.thread = threading.Thread(target=self._animate, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop the animation."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.2)
        # Clear the animation line
        print(f"\033[1A\033[2K", end="", flush=True)

    def _animate(self):
        """Animation loop - bounces face back and forth."""
        max_pos = self.WIDTH - len(self.FACE)
        while self.running:
            # Build the frame
            left_pad = " " * self.position
            right_pad = " " * (max_pos - self.position)
            frame = f"[ {left_pad}{self.FACE}{right_pad} ]"

            # Print in place (move up, clear line, print)
            print(f"\r{frame}", end="", flush=True)

            # Update position
            self.position += self.direction
            if self.position >= max_pos:
                self.direction = -1
            elif self.position <= 0:
                self.direction = 1

            time.sleep(0.1)


_thinking_animation = ThinkingAnimation()


def render_thinking() -> None:
    """Show thinking indicator (static fallback)."""
    print("[ ^_^        ]", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Chat Loop
# ─────────────────────────────────────────────────────────────────────────────

def chat_loop(token: str) -> None:
    history: list[tuple[str, str]] = []
    model_pref, thinking_pref = get_preferences(token)

    # Mutable state for resize handler (closures capture by reference for mutables)
    prefs = {'model': model_pref, 'thinking': thinking_pref}

    def handle_resize(signum, frame):
        render_screen(history, prefs['model'], prefs['thinking'])

    # SIGWINCH is Unix-only (terminal window resize)
    if hasattr(signal, 'SIGWINCH'):
        signal.signal(signal.SIGWINCH, handle_resize)

    render_screen(history, model_pref, thinking_pref)

    while True:
        try:
            user_input = console.input("[cyan]>[/cyan] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() in ('quit', 'exit', 'bye'):
            console.print("[dim]Goodbye![/dim]")
            break

        # Slash commands
        if user_input.startswith('/'):
            parts = user_input[1:].split(maxsplit=1)
            cmd = parts[0].lower() if parts else ""
            arg = parts[1].lower() if len(parts) > 1 else None

            if cmd == "help":
                console.print()
                render_mira_message("/model [opus|haiku|default]\n/think [off|low|medium|high|default]\n/status\n/clear\nquit, exit, bye")
                console.print()

            elif cmd == "status":
                model_pref, thinking_pref = get_preferences(token)
                prefs['model'], prefs['thinking'] = model_pref, thinking_pref
                console.print()
                render_mira_message(f"Model: {model_pref}\nThinking: {thinking_pref}")
                console.print()

            elif cmd == "model":
                if arg and arg in MODEL_ALIASES:
                    if set_model_preference(token, arg):
                        model_pref = prefs['model'] = arg
                        render_screen(history, model_pref, thinking_pref)
                    else:
                        console.print()
                        render_mira_message("Failed to set model", is_error=True)
                        console.print()
                elif arg:
                    console.print()
                    render_mira_message("Options: opus, haiku, default", is_error=True)
                    console.print()
                else:
                    console.print()
                    render_mira_message(f"Current: {model_pref}\nOptions: opus, haiku, default")
                    console.print()

            elif cmd == "think":
                if arg and arg in THINKING_LEVELS:
                    if set_thinking_preference(token, arg):
                        thinking_pref = prefs['thinking'] = arg
                        render_screen(history, model_pref, thinking_pref)
                    else:
                        console.print()
                        render_mira_message("Failed to set thinking", is_error=True)
                        console.print()
                elif arg:
                    console.print()
                    render_mira_message("Options: off, low, medium, high, default", is_error=True)
                    console.print()
                else:
                    console.print()
                    render_mira_message(f"Current: {thinking_pref}\nOptions: off, low, medium, high, default")
                    console.print()

            elif cmd == "clear":
                history.clear()
                render_screen(history, model_pref, thinking_pref)

            else:
                console.print()
                render_mira_message(f"Unknown: /{cmd}", is_error=True)
                console.print()

            continue

        # Regular message - show thinking animation
        render_screen(history, model_pref, thinking_pref, pending_user_msg=user_input, show_thinking=True)
        _thinking_animation.start()

        result = send_message(token, user_input)
        _thinking_animation.stop()

        if result.get("success"):
            response = strip_emotion_tag(result.get("data", {}).get("response", ""))
            history.append((user_input, response))
        else:
            error = result.get("error", {}).get("message", "Unknown error")
            history.append((user_input, f"Error: {error}"))

        render_screen(history, model_pref, thinking_pref)


# ─────────────────────────────────────────────────────────────────────────────
# Entry
# ─────────────────────────────────────────────────────────────────────────────

def one_shot(token: str, message: str) -> None:
    result = send_message(token, message)
    if result.get("success"):
        print(strip_emotion_tag(result.get("data", {}).get("response", "")))
    else:
        print(f"Error: {result.get('error', {}).get('message', 'Unknown')}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="MIRA Chat")
    parser.add_argument('--headless', type=str, help="One-shot message")
    args = parser.parse_args()

    server_started = False
    if not args.headless:
        # Splashscreen handles server startup during animation
        need_server = not is_api_running()
        server_ready = show_splashscreen(start_server=need_server)

        if need_server:
            if not server_ready:
                console.print("[red]Server failed to start[/red]", style="bold")
                shutdown_server()
                sys.exit(1)
            server_started = True
            atexit.register(shutdown_server)
            signal.signal(signal.SIGINT, lambda s, f: (shutdown_server(), sys.exit(0)))
            signal.signal(signal.SIGTERM, lambda s, f: (shutdown_server(), sys.exit(0)))

    try:
        token = get_api_key('mira_api')
    except Exception as e:
        console.print(f"[red]Failed to get API token: {e}[/red]")
        if server_started:
            shutdown_server()
        sys.exit(1)

    if args.headless:
        one_shot(token, args.headless)
    else:
        chat_loop(token)

    if server_started:
        shutdown_server()


if __name__ == "__main__":
    main()

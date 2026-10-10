#!/usr/bin/env python3
"""
Map Hider for Streamers
A Python script for streamers that stream Rust in OBS - hide/show your map overlay using a global hotkey to avoid stream sniping.

The "RustMap" source is treated as a COVER overlay:
  - Hold mode (default): press to SHOW the cover (in-game map is open), release to HIDE it.
  - Toggle mode: press to flip the cover on/off.
"""

import json
import logging
import os
import time
import threading
import signal
import sys
from pathlib import Path
from typing import Optional, Dict, Any

import obsws_python as obs
import keyboard

APP_NAME = "OBS MAP HIDER"
APP_VERSION = "1.1.0"

# --- console styling ---------------------------------------------------------

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
CYAN, GREEN, YELLOW, RED, GREY = (
    "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[90m",
)


def _enable_ansi() -> bool:
    """Return True when ANSI colors should be used.

    Native on Linux; on Windows the console must have virtual terminal
    processing enabled first. Honors NO_COLOR and disables when piped.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            kernel32.SetConsoleMode(
                handle, mode.value | 4  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
        except Exception:
            return False
    return True


_COLOR = _enable_ansi()


def _c(text: str, *codes: str) -> str:
    """Wrap text in ANSI codes when colors are enabled."""
    if not _COLOR or not codes:
        return text
    return "".join(codes) + text + RESET


class ColorFormatter(logging.Formatter):
    """Pads the level name to a fixed column and colorizes it."""

    LEVEL_COLORS = {
        "DEBUG": GREY,
        "INFO": CYAN,
        "WARNING": YELLOW,
        "ERROR": RED,
    }

    def format(self, record: logging.LogRecord) -> str:
        level = f"{record.levelname:<7}"
        color = self.LEVEL_COLORS.get(record.levelname)
        if color:
            level = _c(level, color)
        return f"{self.formatTime(record, self.datefmt)}  {level} {record.getMessage()}"


class MapToggler:
    def __init__(self, config_path: str = "config.json"):
        self.config = self._load_config(config_path)
        self._setup_logging()

        self.obs_client: Optional[obs.ReqClient] = None
        self.is_running = True
        self.source_visible = True
        self.last_action_time = 0
        self.hold_pressed = False
        self.is_pinned = False
        self.reveal_timer = None
        self._reveal_token = 0
        self.hotkey_down = False
        self._obs_hint_shown = False
        self._lock = threading.RLock()
        self.fade_filter_name = self.config.get('fade_filter_name', 'Opacity')
        self.pin_hotkey = self.config.get('pin_hotkey', 'f6')

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        # SIGTERM is unreliable on Windows but harmless to register where available
        if hasattr(signal, 'SIGTERM'):
            try:
                signal.signal(signal.SIGTERM, self._signal_handler)
            except (ValueError, OSError):
                pass

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Config file not found: {config_path}", file=sys.stderr)
            print(
                "Copy config.example.json to config.json, edit it, then run again.",
                file=sys.stderr,
            )
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON in config file: {e}", file=sys.stderr)
            sys.exit(1)

    def _setup_logging(self):
        """Setup logging configuration"""
        level = getattr(logging, self.config.get('log_level', 'INFO').upper())
        handler = logging.StreamHandler()
        handler.setFormatter(ColorFormatter(datefmt="%H:%M:%S"))
        logging.basicConfig(level=level, handlers=[handler])
        # obsws_python logs the WebSocket password at INFO and full connection
        # tracebacks at ERROR. Every failure is already caught and reported by
        # this script's own handlers, so silence the library entirely.
        logging.getLogger("obsws_python").setLevel(logging.CRITICAL)

    def _print_banner(self):
        """Print the startup banner with the active configuration."""
        hotkey = self.config.get('hotkey', 'g').upper()
        mode = self.config.get('mode', 'toggle')
        scene = self.config.get('scene_name') or "current live scene"
        fade_ms = self.config.get('fade_ms', 0)
        reveal_ms = self.config.get('reveal_time_ms', 0)
        reconnect_s = self.config.get('reconnect_interval_s', 5)

        if mode == 'hold':
            mode_line = f"hold {hotkey} to cover · release to uncover"
        else:
            mode_line = f"tap {hotkey} to flip the cover on/off"

        rows = [
            ("mode", mode_line),
            ("hotkey", hotkey),
        ]
        if self.pin_hotkey:
            rows.append(
                ("pin", f"{self.pin_hotkey.upper()} keeps the cover on without holding")
            )
        rows.append(
            ("cover", f"'{self.config['source_name']}' in '{scene}'")
        )
        rows.append(("obs", f"{self.config['obs_host']}:{self.config['obs_port']}"))
        extras = []
        extras.append(f"fade {fade_ms} ms" if fade_ms else "no fade")
        extras.append(f"reveal {reveal_ms} ms" if reveal_ms else "instant hide")
        extras.append(f"reconnect {reconnect_s} s")
        rows.append(("extras", " · ".join(extras)))

        bar = "─" * 60
        print(_c(bar, DIM))
        print(
            _c(f"  {APP_NAME} ", BOLD, CYAN)
            + _c(f"v{APP_VERSION}", GREY)
            + _c("  ·  anti stream-sniping cover", GREY)
        )
        print(_c(bar, DIM))
        for key, value in rows:
            print(f"  {_c(f'{key:<9}', GREY)} {value}")
        print(_c(bar, DIM))
        print(flush=True)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logging.info("Shutdown signal received")
        self.stop()

    def connect_obs(self) -> bool:
        """Connect to OBS WebSocket"""
        try:
            self.obs_client = obs.ReqClient(
                host=self.config['obs_host'],
                port=self.config['obs_port'],
                password=self.config['obs_password']
            )
            self._obs_hint_shown = False
            logging.info(_c("● Connected to OBS", GREEN))
            return True
        except ConnectionRefusedError:
            # OBS is not running or its WebSocket server is disabled — the
            # normal state before the streamer opens OBS. Report it once with
            # hints, then keep retries to one quiet line instead of spamming
            # tracebacks every interval.
            if not self._obs_hint_shown:
                logging.warning(
                    _c(
                        f"Can't reach OBS at "
                        f"{self.config['obs_host']}:{self.config['obs_port']} "
                        f"— retrying every "
                        f"{self.config.get('reconnect_interval_s', 5)} s",
                        YELLOW,
                    )
                )
                logging.info(
                    _c(
                        "  check: OBS is running · WebSocket enabled "
                        "(Tools → WebSocket Server Settings) · port matches config.json",
                        GREY,
                    )
                )
                self._obs_hint_shown = True
            else:
                logging.info(_c("… still waiting for OBS", GREY))
            return False
        except Exception as e:
            logging.warning(f"Could not connect to OBS: {e}")
            return False

    def disconnect_obs(self):
        """Disconnect from OBS WebSocket"""
        if self.obs_client:
            try:
                self.obs_client.disconnect()
                logging.info("Disconnected from OBS")
            except Exception:
                pass
            self.obs_client = None

    def _resolve_scene_name(self) -> Optional[str]:
        """Resolve the target scene name from config or fall back to the current program scene."""
        scene_name = self.config.get('scene_name')
        if scene_name:
            return scene_name
        try:
            current_scene = self.obs_client.get_current_program_scene()
            return current_scene.current_program_scene_name
        except Exception as e:
            logging.error(f"Failed to determine current scene: {e}")
            return None

    def _get_source_visibility(self) -> bool:
        """Get current source visibility"""
        if not self.obs_client:
            return True
        try:
            scene_name = self._resolve_scene_name()
            if not scene_name:
                return True
            resp = self.obs_client.get_scene_item_list(scene_name)
            for item in resp.scene_items:
                if item['sourceName'] == self.config['source_name']:
                    return item['sceneItemEnabled']
            logging.warning(
                f"Source '{self.config['source_name']}' not found in scene '{scene_name}'"
            )
            return True
        except Exception as e:
            logging.error(f"Failed to get source visibility: {e}")
            return True

    def _set_source_visibility(self, visible: bool):
        """Set source visibility in OBS"""
        if not self.obs_client:
            return
        try:
            scene_name = self._resolve_scene_name()
            if not scene_name:
                return

            # Get scene item ID
            resp = self.obs_client.get_scene_item_list(scene_name)
            item_id = None
            for item in resp.scene_items:
                if item['sourceName'] == self.config['source_name']:
                    item_id = item['sceneItemId']
                    break

            if item_id is None:
                logging.error(f"Source '{self.config['source_name']}' not found in scene '{scene_name}'")
                return

            # Apply fade if configured
            fade_ms = self.config.get('fade_ms', 0)
            if fade_ms > 0:
                self._fade_source(scene_name, item_id, visible, fade_ms)
            else:
                self.obs_client.set_scene_item_enabled(scene_name, item_id, visible)

            if visible:
                logging.info(
                    _c("● COVER ON   — map hidden from viewers", GREEN, BOLD)
                )
            else:
                logging.info(_c("○ COVER OFF  — map visible again", GREY))

        except Exception as e:
            logging.error(f"Failed to set source visibility: {e}")

    def _fade_source(self, scene_name: str, item_id: int, visible: bool, fade_ms: int):
        """
        Apply a fade by animating a Color Correction filter's opacity on the
        source. Requires a filter named by 'fade_filter_name' (default 'Opacity')
        of kind 'color_filter' added to the source in OBS.
        Falls back to an instant toggle if the filter is missing.
        """
        source_name = self.config['source_name']
        filter_name = self.fade_filter_name
        try:
            # Verify the opacity filter exists
            filters = self.obs_client.get_source_filter_list(source_name).filters
            has_opacity = any(
                (f['filterName'] if isinstance(f, dict) else f.filterName) == filter_name
                for f in filters
            )
            if not has_opacity:
                logging.warning(
                    f"Fade filter '{filter_name}' not found on source "
                    f"'{source_name}'. Add a 'Color Correction' filter named "
                    f"'{filter_name}' to enable fading. Falling back to instant toggle."
                )
                self.obs_client.set_scene_item_enabled(scene_name, item_id, visible)
                return

            steps = 20
            step_time = fade_ms / 1000 / steps
            start_opacity = 0.0 if visible else 100.0
            end_opacity = 100.0 if visible else 0.0

            if visible:
                # Start invisible, then enable the item so there is no flash
                self.obs_client.set_source_filter_settings(
                    source_name, filter_name, {"opacity": 0}
                )
                self.obs_client.set_scene_item_enabled(scene_name, item_id, True)
            else:
                self.obs_client.set_scene_item_enabled(scene_name, item_id, True)

            for i in range(steps + 1):
                opacity = start_opacity + (end_opacity - start_opacity) * (i / steps)
                self.obs_client.set_source_filter_settings(
                    source_name, filter_name, {"opacity": round(opacity, 1)}
                )
                time.sleep(step_time)

            # Final state: lock opacity and set item visibility
            self.obs_client.set_source_filter_settings(
                source_name, filter_name, {"opacity": end_opacity}
            )
            self.obs_client.set_scene_item_enabled(scene_name, item_id, visible)

        except Exception as e:
            logging.error(f"Fade effect failed: {e}")
            try:
                self.obs_client.set_scene_item_enabled(scene_name, item_id, visible)
            except Exception:
                pass

    def _can_perform_action(self) -> bool:
        """Check if enough time has passed since last action (debounce)"""
        current_time = time.time() * 1000
        debounce_ms = self.config.get('debounce_ms', 150)
        return (current_time - self.last_action_time) >= debounce_ms

    def _toggle_pin(self):
        """Pin/unpin the cover via the pin hotkey (default F6).

        When pinned, the cover stays visible without holding G.
        Pressing G while pinned releases the pin and hides the cover.
        """
        with self._lock:
            if self.is_pinned:
                # Unpin and hide immediately
                self.is_pinned = False
                self.source_visible = False
                self._reveal_token += 1  # invalidate any pending reveal hide
                if self.reveal_timer:
                    self.reveal_timer.cancel()
                    self.reveal_timer = None
                self._set_source_visibility(False)
                logging.info(_c("○ COVER OFF  — pin released", GREY))
            else:
                # Pin: cancel any pending hide, show the cover, keep it on
                self._reveal_token += 1  # invalidate any pending reveal hide
                if self.reveal_timer:
                    self.reveal_timer.cancel()
                    self.reveal_timer = None
                self.is_pinned = True
                self.source_visible = True
                self._set_source_visibility(True)
                logging.info(
                    _c(f"■ COVER PINNED — press {self.config.get('hotkey', 'g').upper()} to release", YELLOW, BOLD)
                )
            self.last_action_time = time.time() * 1000

    def _on_hotkey_press(self):
        """Handle hotkey press event (called on KEY_DOWN)"""
        # Ignore OS key-repeat: the `keyboard` library fires KEY_DOWN for every
        # repeated event while G is physically held. Without this guard, holding
        # G in toggle mode flips the cover on/off rapidly, and in hold mode the
        # repeats race against the reveal timer and cause show/hide flicker.
        if self.hotkey_down:
            return
        self.hotkey_down = True
        if not self._can_perform_action():
            return
        with self._lock:
            mode = self.config.get('mode', 'toggle')

            # If the cover is pinned, G releases the pin and seamlessly hands
            # off to hold mode: the cover STAYS visible (no flicker that could
            # expose the map to stream snipers) while G is held, then hides on
            # release. This guarantees the map is never exposed mid-transition.
            if self.is_pinned:
                self.is_pinned = False
                self.hold_pressed = True
                self.source_visible = True
                self._reveal_token += 1  # invalidate any pending reveal hide
                if self.reveal_timer:
                    self.reveal_timer.cancel()
                    self.reveal_timer = None
                self.last_action_time = time.time() * 1000
                logging.info(
                    _c("● COVER ON   — pin released into hold, release to uncover", GREEN)
                )
                return

            if mode == 'toggle':
                self.source_visible = not self.source_visible
                self._set_source_visibility(self.source_visible)
                self.last_action_time = time.time() * 1000

                # Auto-hide after reveal time if showing
                if self.source_visible:
                    reveal_time = self.config.get('reveal_time_ms', 0)
                    if reveal_time > 0:
                        if self.reveal_timer:
                            self.reveal_timer.cancel()
                        self._reveal_token += 1
                        token = self._reveal_token
                        self.reveal_timer = threading.Timer(
                            reveal_time / 1000,
                            lambda: self._auto_hide_toggle(token)
                        )
                        self.reveal_timer.start()

            elif mode == 'hold' and not self.hold_pressed:
                # Cancel any pending reveal timer so re-pressing G during the
                # reveal window doesn't leave a stale hide scheduled.
                if self.reveal_timer:
                    self.reveal_timer.cancel()
                    self.reveal_timer = None
                self.hold_pressed = True
                self._set_source_visibility(True)  # Show cover when pressed
                self.last_action_time = time.time() * 1000

    def _on_key_event(self, event):
        """Route key press and release events"""
        if event.event_type == keyboard.KEY_DOWN:
            self._on_hotkey_press()
        elif event.event_type == keyboard.KEY_UP:
            self._on_hotkey_release()

    def _on_hotkey_release(self):
        """Handle hotkey release event (called on KEY_UP)"""
        self.hotkey_down = False
        with self._lock:
            mode = self.config.get('mode', 'toggle')

            if mode == 'hold' and self.hold_pressed:
                self.hold_pressed = False
                # Cancel any pending timer from a previous press
                if self.reveal_timer:
                    self.reveal_timer.cancel()
                    self.reveal_timer = None
                reveal_time = self.config.get('reveal_time_ms', 0)
                hotkey_label = self.config.get('hotkey', 'g')
                if reveal_time > 0:
                    # Keep the cover visible for reveal_time_ms after release,
                    # then hide it automatically.
                    self._reveal_token += 1
                    token = self._reveal_token
                    self.reveal_timer = threading.Timer(
                        reveal_time / 1000,
                        lambda: self._auto_hide_hold(token)
                    )
                    self.reveal_timer.start()
                    logging.info(
                        f"'{hotkey_label}' released - cover stays visible "
                        f"for {reveal_time}ms then hides"
                    )
                else:
                    # No reveal time: hide the moment G is released.
                    self.source_visible = False
                    self._set_source_visibility(False)
                    logging.info(f"'{hotkey_label}' released - hiding immediately")

    def _reconnect_loop(self):
        """Background thread to handle OBS reconnection"""
        while self.is_running:
            if not self.obs_client:
                # connect_obs reports unreachable/wrong-password states itself
                if self.connect_obs():
                    mode = self.config.get('mode', 'toggle')
                    if mode == 'hold':
                        # Hold mode: cover overlay should start hidden
                        with self._lock:
                            self.source_visible = False
                            self._set_source_visibility(False)
                        logging.info("Hold mode: cover overlay hidden on connect")
                    else:
                        with self._lock:
                            self.source_visible = self._get_source_visibility()
                        logging.info(
                            f"Initial source state: "
                            f"{'visible' if self.source_visible else 'hidden'}"
                        )
                else:
                    time.sleep(self.config.get('reconnect_interval_s', 5))
            else:
                # Check connection health periodically
                try:
                    self.obs_client.get_version()
                    time.sleep(self.config.get('reconnect_interval_s', 5))
                except Exception:
                    logging.warning("Lost connection to OBS")
                    with self._lock:
                        self.disconnect_obs()

    def run(self):
        """Main run loop"""
        self._print_banner()

        # Start reconnection thread
        reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        reconnect_thread.start()

        # Setup hotkey.
        # hook_key gives us both KEY_DOWN and KEY_UP events in a single
        # callback, which is required for hold mode. Using add_hotkey on top
        # of this would fire _on_hotkey_press twice on every key press.
        hotkey = self.config.get('hotkey', 'g')
        keyboard.hook_key(hotkey, self._on_key_event, suppress=False)

        # Pin hotkey (default F6): toggles a persistent cover that stays on
        # without holding G. Press G to release the pin and hide.
        if self.pin_hotkey:
            keyboard.add_hotkey(self.pin_hotkey, self._toggle_pin, suppress=False)
            logging.info(
                f"Pin hotkey '{self.pin_hotkey}' - press to pin cover, "
                f"'{hotkey}' to release"
            )

        logging.info(f"Listening for '{hotkey}' key in {self.config.get('mode', 'toggle')} mode")
        logging.info("Press Ctrl+C to exit")

        # Main loop
        try:
            while self.is_running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass

        self.stop()

    def _auto_hide_toggle(self, token: int):
        """Auto-hide map after reveal time in toggle mode.

        The token guards against a stale timer firing after a newer press has
        already re-shown the cover — without it the delayed hide would flicker
        the cover off even though the user just pressed G again.
        """
        with self._lock:
            if token != self._reveal_token:
                return
            if self.source_visible:
                self.source_visible = False
                self._set_source_visibility(False)
                logging.info("Auto-hiding map after reveal time")

    def _auto_hide_hold(self, token: int):
        """Auto-hide cover overlay after reveal_time_ms in hold mode"""
        with self._lock:
            if token != self._reveal_token:
                return
            self.source_visible = False
            self._set_source_visibility(False)
            logging.info("Auto-hiding cover after reveal time")

    def stop(self):
        """Stop the application"""
        logging.info("Stopping...")
        self.is_running = False
        if self.reveal_timer:
            self.reveal_timer.cancel()
        self.disconnect_obs()
        keyboard.unhook_all()


def main():
    """Main entry point"""
    if len(sys.argv) > 1:
        config_file = Path(sys.argv[1])
    else:
        # Default to config.json next to this script so it works regardless
        # of the directory the script is launched from.
        config_file = Path(__file__).resolve().parent / "config.json"

    toggler = MapToggler(config_file)
    toggler.run()


if __name__ == "__main__":
    main()

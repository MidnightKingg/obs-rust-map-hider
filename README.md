# OBS Map Hider for Rust Streamers

Hide your in-game map from viewers with a single key press, so stream snipers can't see your position while you check the map.

Built for streaming **Rust** in OBS Studio, but it works with **any game or overlay** — anything you can put in an OBS scene, this can hide.

<!-- Replace with a real demo GIF of your own setup:
![demo](docs/demo.gif)
-->

## The problem it solves

When you open your map on stream, viewers can screenshot your position and relay it to rival players ("stream sniping"). This script toggles a **cover overlay** in OBS — a blur, image, or anything you like — over your map the moment you open it, and hides it again the moment you close it. Your viewers never see the map; you still play normally.

## Features

- **Global hotkey** — works even when Rust is in exclusive fullscreen
- **Three ways to control the cover**:
  - **Hold mode** — hold `G` while the map is open, release to re-hide
  - **Toggle mode** — tap `G` to flip the cover on/off
  - **Pin** — tap `F6` to keep the cover on without holding anything
- **Optional reveal delay** — keep the cover up briefly after you release the key
- **Optional fade** — animate the cover in/out via a Color Correction filter
- **Auto-reconnect** — survives OBS restarts without you touching anything

## Quick start (Windows)

1. **Set up OBS** — enable the WebSocket server (details below)
2. **Double-click `run_toggle_map.bat`** — it finds Python 3.10+ (offering to install it if missing), installs dependencies, and starts the script
3. **Copy `config.example.json` to `config.json`**, fill in your OBS WebSocket password and your source/scene names, and restart the script

That's it. Open your map in-game and press `G`.

> **Never commit or share `config.json`** — it contains your OBS WebSocket password. It is already listed in `.gitignore`.

## Quick start (any OS, manual)

Requires Python 3.10+ and OBS Studio with WebSocket enabled. On Linux, the `keyboard` library needs root (`sudo python toggle_map.py`).

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create your local config
cp config.example.json config.json
# ...then edit config.json

# 3. Run
python toggle_map.py
```

## Set up the OBS WebSocket server

1. Open OBS Studio
2. Go to **Tools → WebSocket Server Settings**
3. Check **Enable WebSocket server**
4. Set **Server Port** to `4455` (default)
5. Set a **Server Password** — this goes into `config.json` as `obs_password`
6. Click **OK**

## Set up the cover overlay

Create a source in OBS that will act as the cover — for example:

- a **Color Source** or **Image** sized over where your map appears, or
- a **Media Source** with a blur/placeholder graphic

Name it `RustMap` (or anything — set `source_name` in `config.json` to match) and put it in your gameplay scene (set `scene_name` to match, or leave it empty to always target the scene that is currently live).

**Default state: hidden.** When you open the map in-game, you press the hotkey to show the cover; when you close the map, the cover hides again.

## Configuration reference

All settings live in `config.json` (copy it from `config.example.json`):

| Setting | Default | Description |
|---|---|---|
| `obs_host` | `localhost` | OBS WebSocket host |
| `obs_port` | `4455` | OBS WebSocket port |
| `obs_password` | — | Your OBS WebSocket password |
| `source_name` | `RustMap` | Name of the cover source in OBS |
| `scene_name` | `In-Game` | Scene containing the cover. Empty string = always use the current live scene |
| `hotkey` | `g` | Key that shows/hides the cover |
| `pin_hotkey` | `f6` | Key that pins the cover on. Set to `null` to disable pinning |
| `mode` | `hold` | `hold` or `toggle` (see below) |
| `debounce_ms` | `150` | Ignores key bounces shorter than this |
| `reveal_time_ms` | `500` | How long the cover stays visible after release/show (0 = instant) |
| `fade_ms` | `0` | Fade duration in ms. `0` = instant. Requires an opacity filter (see below) |
| `fade_filter_name` | `Opacity` | Name of the Color Correction filter animated for fading |
| `reconnect_interval_s` | `5` | Seconds between OBS reconnection attempts |
| `log_level` | `INFO` | Python log level (`DEBUG` to see everything) |

### Hold mode (default)

| Action | Result |
|---|---|
| Hold **G** | Cover shows (viewers can't see your map) |
| Release **G** | Cover hides — immediately, or after `reveal_time_ms` |

### Toggle mode

| Action | Result |
|---|---|
| Press **G** | Cover flips on/off |
| (if `reveal_time_ms` > 0) | Cover auto-hides that long after being shown |

### Pin mode (works in both modes)

| Action | Result |
|---|---|
| Press **F6** | Cover shows and stays on (pinned) — good for long map checks |
| Press **F6** again | Cover hides |
| Hold **G** while pinned | Pin releases and hands off to hold mode — the cover stays on with no flicker, then hides when you release **G** |

The handoff detail matters: the cover never blinks off mid-transition, so there's no frame where the map is exposed to viewers.

### Enabling the fade (optional)

To use `fade_ms`, add a **Color Correction** filter to your cover source in OBS:

1. Right-click the source → **Filters**
2. Add a filter of type **Color Correction**
3. Rename it to `Opacity` (or set `fade_filter_name` to match)
4. Leave its opacity at 100

When `fade_ms > 0` the script animates this filter's opacity. If the filter is missing, it falls back to an instant toggle and logs a warning.

## Troubleshooting

**Script won't connect to OBS**
- Verify OBS WebSocket is enabled and OBS is running
- Check `obs_port` / `obs_password` in `config.json`
- Watch the console — the script retries every `reconnect_interval_s` seconds

**Hotkey not working**
- Another application may be using the same key — try a different `hotkey`
- On Linux, run with `sudo` (global hotkeys require root there)

**Source not found**
- `source_name` must match the OBS source name exactly (case-sensitive)
- Check `scene_name`, or set it to `""` to target the current live scene

## Requirements

- Python 3.10+
- OBS Studio 28+ (bundled obs-websocket v5) with the WebSocket server enabled
- Windows recommended; Linux works with root (macOS is not supported by the `keyboard` library)

## License

[MIT](LICENSE) — free to use, modify, and share.

---

Created by MidnightKing. If this saved you from a stream sniper, feel free to say hi.

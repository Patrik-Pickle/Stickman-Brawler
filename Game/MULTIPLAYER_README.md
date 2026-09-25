# Stickman Brawler - Online Multiplayer

The game is now split into three files:

- **`game_common.py`** - shared rules (physics, cards, round/match logic). No pygame or GUI dependency at all.
- **`server.py`** - the authoritative host. Runs the actual simulation; headless, no window, no sprites/audio needed.
- **`client.py`** - what each player runs. Handles the window, art, sound, and your keyboard input; sends input to the server and draws whatever it reports back.

`stickman_brawler.py` (the original local/hotseat version) still works exactly as before if you ever want same-keyboard play - it's untouched.

## Why this shape

The server is the only thing that decides what actually happened (movement, hits, round wins, which cards got picked). Clients just send "here's what my keys are doing" and draw the result - neither player's client can cheat by messing with damage, speed, etc., since none of that logic runs on their machine.

## Setup

**One player runs the server:**
```
python server.py
```
This listens on UDP port 5555 by default (`python server.py 6000` to use a different port). It'll print `Waiting for 2 players to connect...` and just sit there - that's normal, leave it running.

**Both players run the client**, each with their own copy of the `sprites/`, `music/`, and `sfx/` folders next to `client.py`:
```
python client.py
```
Click **Play** and you'll get an in-game screen asking for the server's address:
- Playing on the same machine as the server (or testing solo): the box is pre-filled with `127.0.0.1:5555` - just press Enter or click Connect.
- Playing over the same WiFi/LAN: the host shares their local IP (e.g. `192.168.1.23:5555`), the other player types that in.
- Playing over the open internet: use a mesh VPN (Tailscale/ZeroTier) or a UDP tunnel service (playit.gg) rather than raw port-forwarding - much less hassle. Whatever address/port they give you goes straight into this same box.

Once both clients have connected, the match starts automatically.

## Controls (same for both players now)

```
A / D      - move left / right
W          - jump
F          - light attack
G          - heavy attack
S          - block (hold)
```

Since each player now has their own keyboard, there's just one control scheme rather than the old split P1/P2 layout.

```
R   - ask the server to reset the whole match
M   - return to the main menu (disconnects)
ESC - quit / back out of Settings
```

Music/SFX volume and your stickman's color are still adjustable from Settings, same as before - those are local, cosmetic preferences and don't need to match between the two players' screens.

## What's tested vs. what to watch for

I validated the shared simulation (`game_common.py`) and the server end-to-end over real UDP sockets in my own sandbox - joining, fighting, KOs, round-end, card-select navigation and picking, and match reset all work correctly against real network traffic. I could not run `client.py`'s actual pygame window myself (no display/pygame available in my environment), so give it a real run and let me know if anything looks or feels off - that's the piece most likely to need a follow-up pass.

Known limitations of this first version, worth knowing about:
- **No reconnect handling.** If a client disconnects mid-match, the server just keeps using their last known input rather than pausing or waiting - closing and reopening the client won't cleanly rejoin an existing match.
- **No client-side prediction or interpolation.** Positions update exactly on whatever the server last sent, with no smoothing in between. On a LAN this should feel fine; over a laggier connection, movement may look slightly stepped rather than perfectly fluid.

## Building standalone .exe files (no Python needed to play)

If you'd rather hand people a double-clickable program instead of asking them to install Python, use `build_windows.bat`:

1. On a Windows machine, make sure Python is installed (only needed for this build step).
2. Put `build_windows.bat` in the same folder as `client.py`, `server.py`, `stickman_brawler.py`, `game_common.py`, and the `sprites/`, `music/`, `sfx/` folders.
3. Double-click `build_windows.bat` (or run it from a terminal). It installs PyInstaller, builds all three `.exe` files, and copies the asset folders alongside them automatically.
4. Everything ends up in a `dist` folder: `StickmanBrawlerClient.exe`, `StickmanBrawlerServer.exe`, `StickmanBrawlerLocal.exe`, a player-facing `README.md` (copied from `README_DIST.md`, covering how to run each mode and set up internet play via playit.gg), plus `sprites/`, `music/`, `sfx/`. Zip that whole folder and send it - recipients just unzip and double-click, no Python or IDE required.

A few things worth knowing:
- **This has to be run on Windows** to produce a Windows `.exe` - PyInstaller doesn't cross-compile from another OS. If you want a Mac version too, the same approach works on a Mac (just run the equivalent PyInstaller commands there instead of the `.bat` file), producing a separate Mac build.
- **The asset folders stay external, not baked into the `.exe`.** That's intentional - it means you can still drop in new sprites, music, or sound effects later without rebuilding anything, exactly like today. Just don't separate the `.exe` files from their `sprites`/`music`/`sfx` folders.
- The client `.exe` opens with no console window (just the game). The server `.exe` keeps its console window, since seeing "Player 1 connected" etc. is genuinely useful for whoever's hosting.

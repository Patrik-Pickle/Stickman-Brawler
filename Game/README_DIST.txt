STICKMAN BRAWLER
================

This folder has everything you need to play - no Python or installs required.

  StickmanBrawlerLocal.exe    - same-keyboard, 2 players, 1 PC
  StickmanBrawlerClient.exe   - what EACH player runs for online play
  StickmanBrawlerServer.exe   - whoever is hosting runs this ONE time per match
  sprites/  music/  sfx/      - art & audio the .exe files load - keep these
                                 folders next to the .exe files, don't move
                                 the .exe files out on their own


QUICK START: SAME-KEYBOARD (1 PC, 2 PLAYERS)
----------------------------------------------

Double-click StickmanBrawlerLocal.exe. That's it - Main Menu > Play >
pick how many round wins takes the match > Start Match.

  Player 1 (Blue, left)          Player 2 (Red, right)
    A / D    - move                LEFT/RIGHT - move
    W        - jump                UP         - jump
    F        - light attack        J          - light attack
    G        - heavy attack        K          - heavy attack
    S        - block (hold)        DOWN       - block (hold)

During card selection, the player who lost the round uses their own move
keys to highlight a card and their light attack key to confirm.

Anytime during a match: R resets the match, M returns to the main menu,
ESC quits (or backs out of Settings).


QUICK START: ONLINE (2 PCs)
------------------------------

One person hosts, both people play.

1. THE HOST RUNS THE SERVER.
   Double-click StickmanBrawlerServer.exe. A console window opens and says
   "Waiting for 2 players to connect..." - leave it running in the
   background for the whole match. It listens on UDP port 5555.

2. BOTH PLAYERS RUN THE CLIENT.
   Double-click StickmanBrawlerClient.exe, click Play, and you'll be asked
   for the server's address:

     Situation                                    What to type
     -------------------------------------------  ------------------------
     Testing solo, client + server on same PC      leave as 127.0.0.1:5555
                                                    and press Enter
     Same WiFi/LAN                                 the host's local IP,
                                                    e.g. 192.168.1.23:5555
     Over the open internet                        see below

Whoever connects to 127.0.0.1 / localhost also gets asked to pick the
Match Length (how many round wins takes the match) before hosting - that's
the client-side signal that "you're also running the server on this PC."

Once both clients have connected, the match starts automatically.

Controls are the same as the local version above, just one player per
keyboard - no need to remember P1 vs P2 keys.


PLAYING OVER THE INTERNET (NOT THE SAME WIFI)
------------------------------------------------

The server listens on UDP, so a straightforward way to let a friend
connect from outside your network without messing with router port
forwarding is a UDP tunnel service like playit.gg (https://playit.gg).
This is free for casual use. Only the HOST (the person running
StickmanBrawlerServer.exe) needs to do this - the other player doesn't
install anything extra.

1. Start StickmanBrawlerServer.exe first, and leave it running.
2. On playit.gg (https://playit.gg), download and run the playit agent,
   then open the claim link it prints/shows to log in (or create a free
   account) and confirm the agent.
3. On the playit dashboard, create a new tunnel:
     - Protocol: UDP (or "TCP/UDP" if that's the only combined option -
       UDP is what actually matters here)
     - Local port: 5555 (the game server's default port)
     - Local address: 127.0.0.1 (leave as-is if the playit agent is
       running on the same PC as the server)
4. Once the tunnel is created, playit shows you a public address,
   something like something.ply.gg:12345 or something.joinmc.link:12345.
   That whole address:port is what you send your friend.
5. Your friend runs StickmanBrawlerClient.exe, clicks Play, and types that
   exact address into the server-address box instead of an IP.

Keep the playit agent and StickmanBrawlerServer.exe both running for as
long as you want people to be able to connect - closing either one ends
the tunnel/match.

(A mesh VPN like Tailscale or ZeroTier also works well if you and your
friend are willing to both install one - each of you gets a private IP
that behaves like you're on the same LAN. Either approach is fine; playit
needs setup only on the host's side, a mesh VPN needs it on both sides.)


SETTINGS
--------

From the main menu, Settings lets each player (independently - this is
local to your own screen and doesn't need to match the other player's)
adjust:
  - Music and SFX volume
  - Your stickman's color
  - The battlefield background, including loading your own image from disk


TROUBLESHOOTING
----------------

- Client says it can't connect / nothing happens: double-check the server
  is actually running and still showing "Waiting for 2 players..." or a
  "Player connected" message, and that the address:port you typed matches
  exactly what the host gave you.
- Playing over the internet and it won't connect: this is almost always
  the tunnel, not the game - confirm the playit.gg agent shows the tunnel
  as active, and that you typed the tunnel's address, not the host's
  local IP.
- Antivirus/SmartScreen flags the .exe: this is common and expected for
  small unsigned PyInstaller-built .exe files. It's not a sign of an
  actual virus - choose "run anyway" / "keep."
- Window looks wrong or art is missing: make sure sprites/, music/, and
  sfx/ are still sitting next to the .exe files, not moved or deleted -
  the .exe files load those at runtime rather than having them baked in.

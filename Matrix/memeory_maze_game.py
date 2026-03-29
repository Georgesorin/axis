"""
memeory_maze_game.py
====================
Memory Maze — standalone game for a 16 × 32 LED touch floor.

Run directly:  python memeory_maze_game.py
  Opens a tkinter SimWindow (16×32 grid).
  Click / drag to simulate floor touches.  F11 = fullscreen.

Architecture
------------
  Network        — UDP send/receive, trigger packets, frame packing
  MemoryMazeGame — all game logic + rendering into self.grid dict
  SimWindow      — tkinter preview; reads self.grid, relays mouse as touches
  main()         — wires all three, runs via root.mainloop()

Board orientation
-----------------
  The START is a 3-col × 4-row block centred on the LEFT long side (x=0..2).
  All display text is FLIPPED (rotated 180°) so it reads from the start side.
  The FINISH is always a fixed 4×4 square on the right side; its vertical
  position shifts every reset.

Lobby layout
------------
  Start block   : x=0..2, rows centred vertically (always 3 cols × 4 rows)
  Minus button  : 3 rows × 1 col, directly BELOW start, oriented vertically
  Plus button   : 3×3 square with cut corners, directly ABOVE start
  Start button  : 4 rows × 1 col, 1 col to the right of the start block
  Player count  : flipped 3×5 text, centred, facing the start side

Game phases
-----------
  LOBBY      Choose players (2-6), confirm via Start button.
  SHOW_MAZE  BFS wave reveals paths (5 s).
  PLAYING    Galaxy floor, hidden maze, special tiles, audio timer.
  FAIL       Red flash, return all to start.
  RESET      All return to start mid-game → "RESET GAME" screen with wave.
  WIN        All at finish → "WIN  xP" flipped.
  LOSE       Timer expired → "LOST" flipped.

Special tiles
-------------
  Purple (×3) — step: 3 random path tiles pulse cyan for 3 s; tile shows
                path colour after you leave.
  Yellow (×2) — step: BFS path fades for 4 s, tile becomes wall; tile shows
                wall colour (black) after you leave.

Timer
-----
  No on-board timer display.  Audio cues via maze_sounds.AudioTimer:
    half-time beep, 30-second beep, countdown ticks 10→1.

Win/Lose/Reset checks
---------------------
  WIN   : all players are on finish tiles (none on field).
  RESET : all players are on start tiles (none on field) during PLAYING/FAIL.
  Maze reveal peek : all players are on start OR finish (none on field).
"""

import math
import random
import socket
import threading
import time
import tkinter as tk
from typing import Dict, List, Optional, Set, Tuple

from maze_generator import (
    Coord,
    generate_thick_maze_prim,
    bfs_reveal_order,
    bfs_path_to_targets,
    pick_finish_position,
)
from small_font import FONT_3x5
from maze_sounds import AudioTimer, save_wav, generate_tone, play_sound, SFX_DIR

# =============================================================================
# Constants
# =============================================================================
BOARD_W = 16
BOARD_H = 32
NUM_CHANNELS = 8
LEDS_PER_CHANNEL = 64
FRAME_DATA_LEN = NUM_CHANNELS * LEDS_PER_CHANNEL * 3

UDP_TARGET_IP = "255.255.255.255"
SEND_PORT = 2000
RECV_PORT = 2001

BLACK        = (  0,   0,   0)
WHITE        = (255, 255, 255)
PATH_BLUE    = (  0,  55, 210)
HINT_CYAN    = (  0, 180, 220)
PURPLE_COL   = (130,   0, 220)
YELLOW_COL   = (220, 200,   0)
GREEN_START  = (  0, 180,   0)
GREEN_FINISH = ( 20, 255,  80)
RED_FAIL     = (220,   0,   0)
BTN_MINUS_COL = (160,  20, 220)   # minus button colour
BTN_PLUS_COL  = (  0, 180, 220)   # plus  button colour
BTN_START_COL = (255, 140,   0)   # start button colour

PHASE_LOBBY     = "lobby"
PHASE_SHOW_MAZE = "show_maze"
PHASE_PLAYING   = "playing"
PHASE_FAIL      = "fail"
PHASE_RESET     = "reset"
PHASE_WIN       = "win"
PHASE_LOSE      = "lose"

MAZE_SHOW_DUR     = 5.0
MAZE_RETURN_DUR   = 3.0
FIELD_CLEAR_GRACE = 0.5
PURPLE_DUR        = 3.0
YELLOW_DUR        = 4.0
FAIL_FLASH_DUR    = 1.5
RESET_DUR         = 4.0
WIN_DUR           = 7.0
LOSE_DUR          = 6.0
LOBBY_CONFIRM_DUR = 0.8
SCORE_PER_SEC  = 10
FAIL_PENALTY   = 20
NUM_PURPLE     = 3
NUM_YELLOW     = 2
MIN_PLAYERS    = 2
MAX_PLAYERS    = 6

# Start region: always 3 cols × 4 rows centred on the left long side
START_COLS = 3
START_ROWS = 4
START_X    = 0
START_Y    = (BOARD_H - START_ROWS) // 2   # top row of start block = 14

# Finish region: 4×4 square, position shifts each reset
FINISH_SIZE = 4


# =============================================================================
# Sound file generation (called once at startup)
# =============================================================================

def _ensure_sounds():
    """Generate all needed WAV files into SFX_DIR if they don't exist."""
    import os
    files = {
        "half.wav":   (880,  0.25, 0.6, "sine",   0),
        "warn30.wav": (660,  0.35, 0.7, "square", 0),
        "tick.wav":   (1100, 0.12, 0.5, "sine",   0),
        "player.wav": (1200, 0.10, 0.4, "sine",   0),   # lobby player-change beep
    }
    for fname, (freq, dur, vol, wtype, slide) in files.items():
        path = os.path.join(SFX_DIR, fname)
        if not os.path.exists(path):
            data = generate_tone(freq, dur, vol=vol, type=wtype, slide=slide)
            save_wav(fname, data)


# =============================================================================
# Math helpers
# =============================================================================

def _clamp(v, lo=0, hi=255):
    return max(lo, min(hi, int(round(v))))

def _sc(col, alpha):
    return (_clamp(col[0]*alpha), _clamp(col[1]*alpha), _clamp(col[2]*alpha))

def _lerp(a, b, t):
    t = max(0.0, min(1.0, t))
    return (_clamp(a[0]+(b[0]-a[0])*t),
            _clamp(a[1]+(b[1]-a[1])*t),
            _clamp(a[2]+(b[2]-a[2])*t))

def _pulse(t, period=1.0, lo=0.3, hi=1.0):
    return lo + (hi - lo) * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))

def _fade_io(elapsed, dur):
    if dur <= 0 or elapsed < 0 or elapsed > dur:
        return 0.0
    return math.sin(math.pi * elapsed / dur)

def _game_duration(players):
    """≤3 players → 2 min (120 s); >3 players → 3 min (180 s)."""
    return 120 if players <= 3 else 180


# =============================================================================
# Text rendering helpers (flipped 180° to face start side)
# =============================================================================

def _text3x5_flipped(g, text, x, y, col):
    """
    Render text in FONT_3x5 rotated 180° so it reads from the start (left) side.
    In 180° rotation: a pixel at (vx, vy) maps to (BOARD_W-1-vx, BOARD_H-1-vy).
    """
    cx = x
    for ch in text:
        glyph = FONT_3x5.get(ch, FONT_3x5.get("?", [0, 0, 0]))
        for ci, mask in enumerate(glyph):
            for ri in range(5):
                if (mask >> ri) & 1:
                    px = BOARD_W - 1 - (cx + ci)
                    py = BOARD_H - 1 - (y + ri)
                    if 0 <= px < BOARD_W and 0 <= py < BOARD_H:
                        g[(px, py)] = col
        cx += len(glyph) + 1


def _text_width_3x5(text):
    total = 0
    for ch in text:
        glyph = FONT_3x5.get(ch, FONT_3x5.get("?", [0, 0, 0]))
        total += len(glyph) + 1
    return max(0, total - 1)


# =============================================================================
# Network
# =============================================================================

class Network:
    """UDP send/receive for the LED hardware / Simulator."""

    def __init__(self, target_ip=UDP_TARGET_IP, send_port=SEND_PORT, recv_port=RECV_PORT):
        self.target_ip = target_ip
        self.send_port = send_port
        self.recv_port = recv_port
        self.seq = 0
        self._lock = threading.Lock()
        self.triggers = {}

        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_send.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self.sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_recv.settimeout(0.3)
        try:
            self.sock_recv.bind(("0.0.0.0", self.recv_port))
            print(f"Network: recv on :{self.recv_port}  send->{target_ip}:{send_port}")
        except Exception as e:
            print(f"Network: recv bind failed: {e}")

        self._running = True
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def _recv_loop(self):
        while self._running:
            try:
                data, _ = self.sock_recv.recvfrom(2048)
                if len(data) >= 1373 and data[0] == 0x88:
                    with self._lock:
                        for ch in range(NUM_CHANNELS):
                            base = 2 + ch * 171
                            for led in range(LEDS_PER_CHANNEL):
                                self.triggers[(ch, led)] = (data[base+1+led] == 0xCC)
            except socket.timeout:
                pass
            except Exception:
                pass

    def is_pressed(self, x, y):
        if not (0 <= x < BOARD_W and 0 <= y < BOARD_H):
            return False
        ch = min(y // 4, NUM_CHANNELS - 1)
        row_in_ch = y % 4
        led = (row_in_ch*16 + x) if row_in_ch % 2 == 0 else (row_in_ch*16 + (15-x))
        with self._lock:
            return self.triggers.get((ch, led), False)

    def send_frame(self, grid):
        buf = bytearray(FRAME_DATA_LEN)
        for (x, y), color in grid.items():
            if not (0 <= x < BOARD_W and 0 <= y < BOARD_H):
                continue
            ch = min(y // 4, NUM_CHANNELS - 1)
            row_in_ch = y % 4
            led = (row_in_ch*16 + x) if row_in_ch % 2 == 0 else (row_in_ch*16 + (15-x))
            block = NUM_CHANNELS * 3
            offset = led * block + ch
            if offset + NUM_CHANNELS*2 < len(buf):
                buf[offset]                  = color[1]
                buf[offset + NUM_CHANNELS]   = color[0]
                buf[offset + NUM_CHANNELS*2] = color[2]
        self._send_packet(buf)

    def _send_packet(self, frame_data):
        self.seq = (self.seq % 0xFFFF) + 1
        ip, port = self.target_ip, self.send_port

        def tx(pkt):
            try:
                self.sock_send.sendto(pkt, (ip, port))
                self.sock_send.sendto(pkt, ("127.0.0.1", port))
            except Exception:
                pass

        def make(inner, cs=0x1E):
            r1, r2 = random.randint(0,127), random.randint(0,127)
            ln = len(inner) - 1
            p = bytearray([0x75, r1, r2, (ln>>8)&0xFF, ln&0xFF]) + inner
            p += bytearray([cs, 0x00])
            return p

        tx(bytearray([0x75,0,0,0,8,0x02,0,0,0x33,0x44,
                      (self.seq>>8)&0xFF, self.seq&0xFF, 0,0,0,0x0E,0]))
        pay = bytearray()
        for _ in range(NUM_CHANNELS):
            pay += bytes([(LEDS_PER_CHANNEL>>8)&0xFF, LEDS_PER_CHANNEL&0xFF])
        tx(make(bytearray([0x02,0,0,0x88,0x77,0xFF,0xF0,
                           (len(pay)>>8)&0xFF, len(pay)&0xFF]) + pay))
        for i, idx in enumerate(range(0, len(frame_data), 984), 1):
            chunk = frame_data[idx:idx+984]
            inner = bytearray([0x02,0,0,0x88,0x77,
                                (i>>8)&0xFF, i&0xFF,
                                (len(chunk)>>8)&0xFF, len(chunk)&0xFF]) + bytearray(chunk)
            tx(make(inner, 0x1E if len(chunk)==984 else 0x36))
            time.sleep(0.001)
        tx(bytearray([0x75,0,0,0,8,0x02,0,0,0x55,0x66,
                      (self.seq>>8)&0xFF, self.seq&0xFF, 0,0,0,0x0E,0]))

    def stop(self):
        self._running = False
        try:
            self.sock_recv.close()
        except Exception:
            pass


# =============================================================================
# MemoryMazeGame
# =============================================================================

class MemoryMazeGame:
    """
    Full Memory Maze game logic + rendering.

    Start region  : x=0..2 (3 cols), y=START_Y..START_Y+3 (4 rows), always.
    Finish region : 4×4 square; right edge at x=BOARD_W-1; y varies each reset.

    Lobby buttons (all relative to start block, facing start side)
    ──────────────────────────────────────────────────────────────
    Minus  : 1 col × 3 rows, directly below start, col=1 (centred)
    Plus   : 3×3 square with cut corners, directly above start, centred
    Start  : 4 rows × 1 col, 1 col to the right of start (col=START_COLS)
    Count  : flipped text, centred beside plus button
    """

    def __init__(self, net):
        self.net = net
        self.grid = {(x, y): BLACK for y in range(BOARD_H) for x in range(BOARD_W)}

        self.phase        = PHASE_LOBBY
        self.player_count = MIN_PLAYERS
        self.team_score   = 0
        self.finishers    = 0
        self.total_time   = 0.0
        self.remaining    = 0.0

        self.maze           = []
        self.start_tiles    = self._static_start_tiles()
        self.finish_tiles   = set()
        self.purple_tiles   = set()
        self.yellow_tiles   = set()
        self.purple_reveals = []   # list of {tiles, start, duration}
        self.yellow_paths   = []   # list of {tiles, start, duration}

        self._curr_pressed   = set()
        self._phase_time     = 0.0
        self._timer          = 0.0
        self._maze_visible   = False
        self._reveal_order   = []
        self._reveal_count   = 0.0

        self._lobby_players  = MIN_PLAYERS
        self._lobby_confirm  = 0.0

        self._field_clear_tmr = 0.0
        self._confetti        = []
        self._just_returned   = False

        self._audio_timer: Optional[AudioTimer] = None

        # Lobby button geometry (derived from start block)
        self._minus_tiles = self._compute_minus_tiles()
        self._plus_tiles  = self._compute_plus_tiles()
        self._go_tiles    = self._compute_go_tiles()

        print("MemoryMazeGame ready — LOBBY.")

    # ─────────────────────────────────────────────────────────────────────────
    # Static geometry
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _static_start_tiles() -> Set[Coord]:
        """Start is always 3 cols × 4 rows centred on the left long side."""
        return {(x, y) for x in range(START_COLS)
                for y in range(START_Y, START_Y + START_ROWS)}

    def _compute_minus_tiles(self) -> Set[Coord]:
        """3 rows × 1 col directly BELOW start block, centred (col 1)."""
        cx = START_X + START_COLS // 2          # col 1
        top = START_Y + START_ROWS               # first row below start
        return {(cx, top + r) for r in range(3)}

    def _compute_plus_tiles(self) -> Set[Coord]:
        """
        3×3 square with cut corners (plus-like shape) directly ABOVE start,
        centred horizontally.  Cut corners = only the cross remains.
        """
        cx = START_X + START_COLS // 2          # col 1
        bot = START_Y - 1                        # row just above start
        top = bot - 2                            # 3 rows tall: top..bot
        mid = top + 1
        # Full 3×3 minus four corners → cross shape
        full = {(cx-1+dx, top+dy) for dx in range(3) for dy in range(3)}
        corners = {(cx-1, top), (cx+1, top), (cx-1, bot), (cx+1, bot)}
        return full - corners

    def _compute_go_tiles(self) -> Set[Coord]:
        """4 rows × 1 col, 1 col to the right of the start block."""
        gx = START_X + START_COLS               # col 3
        return {(gx, START_Y + r) for r in range(START_ROWS)}

    # ─────────────────────────────────────────────────────────────────────────
    # State description
    # ─────────────────────────────────────────────────────────────────────────

    def describe_state(self):
        secs  = max(0, int(self.remaining))
        total = len(self._reveal_order)
        labels = {
            PHASE_LOBBY:     "Choose players, press GO button to begin",
            PHASE_SHOW_MAZE: "Memorise the maze — disappears on the field",
            PHASE_PLAYING:   "Navigate the hidden maze to the finish",
            PHASE_FAIL:      "Wrong tile! All players return to start",
            PHASE_RESET:     "All returned — resetting round",
            PHASE_WIN:       "All players reached the finish!",
            PHASE_LOSE:      "Timer ran out!",
        }
        return {
            "phase":            self.phase,
            "phase_label":      labels.get(self.phase, self.phase),
            "player_count":     self.player_count,
            "lobby_selection":  self._lobby_players,
            "team_score":       self.team_score,
            "finishers":        self.finishers,
            "remaining_sec":    round(self.remaining, 1),
            "remaining_fmt":    f"{secs//60}:{secs%60:02d}",
            "maze_visible":     self._maze_visible,
            "maze_ready":       bool(self.maze),
            "reveal_progress":  round(self._reveal_count/total, 3) if total else 0.0,
            "purple_tiles":     len(self.purple_tiles),
            "yellow_tiles":     len(self.yellow_tiles),
        }

    def print_state(self):
        s  = self.describe_state()
        W  = 50
        hr = "=" * W
        def row(lbl, val):
            vs  = str(val)
            pad = W - 4 - len(lbl) - len(vs)
            return f"| {lbl}{chr(32)*max(1,pad)}{vs} |"
        lines = [
            f"+{hr}+",
            f"|{'  MEMORY MAZE — GAME STATE':^{W}}|",
            f"+{hr}+",
            row("Phase:", s["phase"].upper()),
            f"| {s['phase_label'][:W-2]:<{W-2}} |",
            f"+{hr}+",
            row("Lobby selection:", s["lobby_selection"]),
            row("Players:",         s["player_count"]),
            row("Score:",           f"{s['team_score']} pts"),
            row("Finishers:",       f"{s['finishers']} / {s['player_count']}"),
            row("Time left:",       s["remaining_fmt"]),
            f"+{hr}+",
            row("Maze visible:",    "Yes" if s["maze_visible"] else "No"),
            row("Reveal:",          f"{s['reveal_progress']*100:.0f}%"),
            row("Purple tiles:",    s["purple_tiles"]),
            row("Yellow tiles:",    s["yellow_tiles"]),
            f"+{hr}+",
        ]
        print("\n".join(lines))

    # ─────────────────────────────────────────────────────────────────────────
    # Touch shims (for SimWindow / Controller)
    # ─────────────────────────────────────────────────────────────────────────

    def on_touch(self, index):
        x, y = index % BOARD_W, index // BOARD_W
        if 0 <= x < BOARD_W and 0 <= y < BOARD_H:
            self._curr_pressed.add((x, y))
            self._handle_press(x, y)

    def on_release(self, index):
        x, y = index % BOARD_W, index // BOARD_W
        self._curr_pressed.discard((x, y))

    def on_touch_press(self, x, y):
        self._curr_pressed.add((x, y))
        self._handle_press(x, y)

    def on_touch_release(self, x, y):
        self._curr_pressed.discard((x, y))

    # ─────────────────────────────────────────────────────────────────────────
    # Main loop
    # ─────────────────────────────────────────────────────────────────────────

    def update(self, dt):
        self._just_returned = False
        self._phase_time   += dt
        self._poll_touches()

        if   self.phase == PHASE_LOBBY:     self._upd_lobby(dt)
        elif self.phase == PHASE_SHOW_MAZE: self._upd_show_maze(dt)
        elif self.phase == PHASE_PLAYING:   self._upd_playing(dt)
        elif self.phase == PHASE_FAIL:      self._upd_fail(dt)
        elif self.phase == PHASE_RESET:     self._upd_reset(dt)
        elif self.phase in (PHASE_WIN, PHASE_LOSE): self._upd_end(dt)

    def render(self):
        g = self.grid
        if   self.phase == PHASE_LOBBY:     self._draw_lobby(g)
        elif self.phase == PHASE_SHOW_MAZE: self._draw_show_maze(g)
        elif self.phase == PHASE_PLAYING:   self._draw_playing(g)
        elif self.phase == PHASE_FAIL:      self._draw_fail(g)
        elif self.phase == PHASE_RESET:     self._draw_reset(g)
        elif self.phase == PHASE_WIN:       self._draw_win(g)
        elif self.phase == PHASE_LOSE:      self._draw_lose(g)
        self.net.send_frame(g)

    def is_game_over(self):
        return self._just_returned

    # ─────────────────────────────────────────────────────────────────────────
    # Touch polling (edge detection)
    # ─────────────────────────────────────────────────────────────────────────

    def _poll_touches(self):
        new = set()
        for y in range(BOARD_H):
            for x in range(BOARD_W):
                try:
                    if self.net.is_pressed(x, y):
                        new.add((x, y))
                except Exception:
                    pass
        for tile in new - self._curr_pressed:
            self._handle_press(*tile)
        self._curr_pressed = new

    def _handle_press(self, x, y):
        if   self.phase == PHASE_LOBBY:     self._lobby_press(x, y)
        elif self.phase == PHASE_SHOW_MAZE: self._show_maze_press(x, y)
        elif self.phase == PHASE_PLAYING:   self._playing_press(x, y)
        elif self.phase == PHASE_FAIL:      self._fail_press(x, y)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase updaters
    # ─────────────────────────────────────────────────────────────────────────

    def _upd_lobby(self, dt):
        if self._lobby_confirm > 0:
            self._lobby_confirm -= dt

    def _upd_show_maze(self, dt):
        total = len(self._reveal_order)
        if total > 0:
            self._reveal_count = min(float(total),
                                     self._reveal_count + (total / MAZE_SHOW_DUR) * dt)
        self._timer -= dt
        if self._timer <= 0:
            self._maze_visible = False
            self.phase = PHASE_PLAYING
            print("Maze hidden — PLAYING.")

    def _upd_playing(self, dt):
        self.remaining -= dt
        if self.remaining <= 0:
            self.remaining = 0.0
            self._enter_lose()
            return

        # Audio timer
        if self._audio_timer:
            self._audio_timer.update()

        now = time.time()
        self.purple_reveals = [r for r in self.purple_reveals
                                if now - r["start"] < r["duration"]]
        self.yellow_paths   = [p for p in self.yellow_paths
                                if now - p["start"] < p["duration"]]

        # --- Maze peek: all pressed tiles are on start OR finish (not field) ---
        if self._curr_pressed:
            all_safe = all(pos in self.start_tiles or pos in self.finish_tiles
                           for pos in self._curr_pressed)
            any_on_field = not all_safe
        else:
            all_safe = False
            any_on_field = False

        if all_safe:
            self._field_clear_tmr += dt
            if self._field_clear_tmr >= FIELD_CLEAR_GRACE and not self._maze_visible:
                self._maze_visible = True
                self._timer = MAZE_RETURN_DUR
        else:
            self._field_clear_tmr = 0.0
            if any_on_field:
                self._maze_visible = False

        if self._maze_visible:
            self._timer -= dt
            if self._timer <= 0:
                self._maze_visible = False

        # --- RESET check: all pressed tiles are on START (none elsewhere) ---
        if self._curr_pressed:
            all_on_start = all(pos in self.start_tiles for pos in self._curr_pressed)
            if all_on_start and len(self._curr_pressed) >= self.player_count:
                self._enter_reset()

    def _upd_fail(self, dt):
        self._timer -= dt
        # Check reset: all players back on start
        if self._curr_pressed:
            all_on_start = all(pos in self.start_tiles for pos in self._curr_pressed)
            if all_on_start and len(self._curr_pressed) >= self.player_count:
                self._enter_reset()

    def _upd_reset(self, dt):
        self._timer -= dt
        if self._timer <= 0:
            print("Round reset — new maze.")
            self._new_round()

    def _upd_end(self, dt):
        self._timer -= dt
        self._upd_confetti(dt)
        if self._timer <= 0:
            print(f"Returning to LOBBY. Score: {self.team_score}")
            self._just_returned = True
            self.phase = PHASE_LOBBY
            self._confetti.clear()

    # ─────────────────────────────────────────────────────────────────────────
    # Press handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _lobby_press(self, x, y):
        pos = (x, y)
        if pos in self._minus_tiles:
            old = self._lobby_players
            self._lobby_players = max(MIN_PLAYERS, self._lobby_players - 1)
            self._lobby_confirm = LOBBY_CONFIRM_DUR
            play_sound("player.wav")
            print(f"Lobby: {old} → {self._lobby_players} players (−)")
            return
        if pos in self._plus_tiles:
            old = self._lobby_players
            self._lobby_players = min(MAX_PLAYERS, self._lobby_players + 1)
            self._lobby_confirm = LOBBY_CONFIRM_DUR
            play_sound("player.wav")
            print(f"Lobby: {old} → {self._lobby_players} players (+)")
            return
        if pos in self._go_tiles:
            print(f"Lobby: GO pressed — {self._lobby_players} players.")
            self._start_game(self._lobby_players)

    def _show_maze_press(self, x, y):
        # Stepping off start/finish hides the maze immediately
        if (x, y) not in self.start_tiles and (x, y) not in self.finish_tiles:
            self._maze_visible = False
        self._step(x, y)

    def _playing_press(self, x, y):
        self._step(x, y)

    def _fail_press(self, x, y):
        pass  # reset is handled in _upd_fail via _curr_pressed check

    def _step(self, x, y):
        pos = (x, y)

        # ── Finish ──
        if pos in self.finish_tiles:
            self.finishers += 1
            pts = int(self.remaining * SCORE_PER_SEC)
            self.team_score = max(0, self.team_score + pts)
            print(f"Finish! {self.finishers}/{self.player_count}, +{pts} pts")
            # Win check: all players on finish (checked via current pressed)
            # We defer to _upd_playing's win check each frame instead:
            if self.finishers >= self.player_count:
                self._enter_win()
            return

        if pos in self.start_tiles:
            return

        # ── Purple hint tile ──
        if pos in self.purple_tiles:
            self._clear_2x2(x, y, self.purple_tiles)
            pool = [(px, py) for py in range(BOARD_H) for px in range(BOARD_W)
                    if self.maze[py][px]
                    and (px, py) not in self.start_tiles
                    and (px, py) not in self.finish_tiles
                    and (px, py) not in self.purple_tiles
                    and (px, py) not in self.yellow_tiles]
            hints = random.sample(pool, min(3, len(pool)))
            self.purple_reveals.append({
                "tiles": set(hints), "start": time.time(), "duration": PURPLE_DUR
            })
            print(f"Purple at {pos} — {len(hints)} hints.")
            return

        # ── Yellow path tile ──
        if pos in self.yellow_tiles:
            self._clear_2x2(x, y, self.yellow_tiles)
            path = bfs_path_to_targets(
                self.maze, x, y, self.finish_tiles, BOARD_W, BOARD_H)
            if path:
                self.yellow_paths.append({
                    "tiles": path, "start": time.time(), "duration": YELLOW_DUR
                })
                print(f"Yellow at {pos} — path {len(path)} tiles.")
            # Wall off the 2×2 block
            tx2, ty2 = (x // 2 * 2), (y // 2 * 2)
            for iy in range(ty2, min(ty2+2, BOARD_H)):
                for ix in range(tx2, min(tx2+2, BOARD_W)):
                    self.maze[iy][ix] = False
            return

        # ── Wall ──
        if not self.maze[y][x]:
            print(f"FAIL — wall at {pos}.")
            self._enter_fail()

    def _clear_2x2(self, x, y, tile_set: Set[Coord]):
        tx2, ty2 = (x // 2 * 2), (y // 2 * 2)
        for iy in range(ty2, ty2+2):
            for ix in range(tx2, tx2+2):
                tile_set.discard((ix, iy))

    # ─────────────────────────────────────────────────────────────────────────
    # State transitions
    # ─────────────────────────────────────────────────────────────────────────

    def _start_game(self, players):
        self.player_count = players
        self.team_score   = 0
        self.finishers    = 0
        self.total_time   = float(_game_duration(players))
        self.remaining    = self.total_time
        print(f"Starting: {players} players, {int(self.total_time)}s.")
        self._new_round()

    def _new_round(self):
        # Recompute start (always same) and pick a new finish position
        self.start_tiles  = self._static_start_tiles()
        fx, fy = pick_finish_position(BOARD_W, BOARD_H)
        self.finish_tiles = {(fx+dx, fy+dy)
                             for dx in range(FINISH_SIZE)
                             for dy in range(FINISH_SIZE)}

        # Generate maze seeded from the start region
        seed_y = START_Y + START_ROWS // 2
        self.maze = generate_thick_maze_prim(
            BOARD_W, BOARD_H, seed_x=START_X, seed_y=seed_y)

        # Force start and finish regions open
        self._force_open(self.start_tiles)
        self._force_open(self.finish_tiles)

        self._place_special_tiles()
        self.purple_reveals.clear()
        self.yellow_paths.clear()

        self._reveal_order = bfs_reveal_order(
            self.maze, self.start_tiles, BOARD_W, BOARD_H)
        self._reveal_count   = 0.0
        self._timer          = MAZE_SHOW_DUR
        self._maze_visible   = True
        self.finishers        = 0
        self._field_clear_tmr = 0.0
        self.phase = PHASE_SHOW_MAZE

        # Reset audio timer
        self._audio_timer = AudioTimer(self.remaining)

        n = sum(c for row in self.maze for c in row)
        print(f"Maze: {n} path tiles  finish at ({fx},{fy})  "
              f"P={len(self.purple_tiles)}  Y={len(self.yellow_tiles)}")

    def _force_open(self, tiles: Set[Coord]):
        for x, y in tiles:
            if 0 <= x < BOARD_W and 0 <= y < BOARD_H:
                self.maze[y][x] = True

    def _place_special_tiles(self):
        """Place special tiles in 2×2 clusters matching maze thickness."""
        potential = []
        for y in range(0, BOARD_H - 1, 2):
            for x in range(0, BOARD_W - 1, 2):
                if (self.maze[y][x]
                        and (x, y) not in self.start_tiles
                        and (x, y) not in self.finish_tiles):
                    potential.append((x, y))
        random.shuffle(potential)

        def block4(tl):
            return {(tl[0]+dx, tl[1]+dy) for dx in range(2) for dy in range(2)}

        self.purple_tiles = set()
        for _ in range(min(NUM_PURPLE, len(potential))):
            tl = potential.pop()
            self.purple_tiles.update(block4(tl))

        self.yellow_tiles = set()
        for _ in range(min(NUM_YELLOW, len(potential))):
            tl = potential.pop()
            self.yellow_tiles.update(block4(tl))

    def _enter_fail(self):
        self.team_score      = max(0, self.team_score - FAIL_PENALTY)
        self.phase           = PHASE_FAIL
        self._timer          = FAIL_FLASH_DUR
        self._maze_visible   = False
        self.purple_reveals.clear()
        self.yellow_paths.clear()

    def _enter_reset(self):
        print("All on start — entering RESET.")
        self.phase  = PHASE_RESET
        self._timer = RESET_DUR
        self._maze_visible = False

    def _enter_win(self):
        print(f"WIN! Score: {self.team_score}")
        self.phase  = PHASE_WIN
        self._timer = WIN_DUR
        self._spawn_confetti(win=True)

    def _enter_lose(self):
        self.team_score = max(0, self.team_score - FAIL_PENALTY)
        print(f"LOSE. Score: {self.team_score}")
        self.phase  = PHASE_LOSE
        self._timer = LOSE_DUR
        self._spawn_confetti(win=False)

    # ─────────────────────────────────────────────────────────────────────────
    # Confetti
    # ─────────────────────────────────────────────────────────────────────────

    def _spawn_confetti(self, win: bool):
        palette = (
            [(20,220,40),(0,180,30),(60,255,100),(0,130,20),
             (120,255,60),(200,240,20),(255,255,200),(80,255,200)]
            if win else
            [(220,10,0),(255,50,0),(200,0,0),(255,100,0),
             (180,30,0),(255,150,30),(255,80,50),(255,200,80)]
        )
        self._confetti = [
            {"x": random.uniform(0, BOARD_W-1),
             "y": random.uniform(-BOARD_H, 0),
             "vx": random.uniform(-1.2, 1.2),
             "vy": random.uniform(5.5, 13.),
             "color": random.choice(palette)}
            for _ in range(44)
        ]

    def _upd_confetti(self, dt):
        for p in self._confetti:
            p["y"] += p["vy"] * dt
            p["x"]  = (p["x"] + p["vx"] * dt) % BOARD_W
            if p["y"] >= BOARD_H:
                p["y"]  = random.uniform(-4, 0)
                p["x"]  = random.uniform(0, BOARD_W-1)
                p["vy"] = random.uniform(5.5, 13.)
                p["vx"] = random.uniform(-1.2, 1.2)

    # ─────────────────────────────────────────────────────────────────────────
    # Low-level drawing helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _px(self, g, x, y, col):
        if 0 <= x < BOARD_W and 0 <= y < BOARD_H:
            g[(x, y)] = col

    def _fill(self, g, col):
        for k in g:
            g[k] = col

    def _draw_galaxy(self, g):
        t = self._phase_time
        for y in range(BOARD_H):
            for x in range(BOARD_W):
                n1 = math.sin(x*.42+t*.55) * math.cos(y*.33+t*.30)
                n2 = math.sin(x*.68-t*.40+y*.19) * .55
                n3 = math.cos(y*.52+t*.22+x*.10) * .45
                v  = max(0., min(1., (n1*.45+n2*.32+n3*.23)+.5))
                if v < .35:
                    s=v/.35; g[(x,y)]=(_clamp(s*8), 0, _clamp(s*55))
                elif v < .65:
                    s=(v-.35)/.30; g[(x,y)]=(_clamp(8+s*15), _clamp(s*8), _clamp(55+s*110))
                else:
                    s=(v-.65)/.35; g[(x,y)]=(_clamp(23+s*90), _clamp(8*(1-s)), _clamp(165+s*55))
                sv = abs(math.sin(x*7.31+y*5.13+t*2.17))
                if sv > .975:
                    fade=(sv-.975)/.025; r2,gg,b2=g[(x,y)]
                    g[(x,y)]=(_clamp(r2+140*fade), _clamp(gg+140*fade), _clamp(b2+140*fade))

    def _draw_maze_overlay(self, g):
        """Draw the revealed maze over whatever background is already set."""
        t   = self._phase_time
        now = time.time()
        revealed   = set(self._reveal_order[:int(self._reveal_count)])
        wave_front = int(self._reveal_count)
        rev_idx    = {tile: i for i, tile in enumerate(self._reveal_order)}

        for y in range(BOARD_H):
            for x in range(BOARD_W):
                pos = (x, y)
                if pos in self.start_tiles:
                    g[pos] = GREEN_START
                elif pos in self.finish_tiles:
                    g[pos] = GREEN_FINISH
                # Special tiles: only shown when maze IS visible
                elif pos in self.purple_tiles:
                    g[pos] = _sc(PURPLE_COL, _pulse(t, period=1.0))
                elif pos in self.yellow_tiles:
                    g[pos] = _sc(YELLOW_COL, _pulse(t, period=1.3))
                elif pos in revealed and self.maze[y][x]:
                    dist = wave_front - rev_idx.get(pos, wave_front)
                    g[pos] = _lerp(WHITE, PATH_BLUE, dist/4) if 0 < dist <= 4 else PATH_BLUE
                else:
                    g[pos] = BLACK

        # Purple reveals
        for rev in self.purple_reveals:
            alpha = _fade_io(now - rev["start"], rev["duration"])
            col   = _sc(HINT_CYAN, .45+alpha*.55)
            for px, py in rev["tiles"]:
                self._px(g, px, py, col)

        # Yellow path reveals
        for yp in self.yellow_paths:
            alpha = _fade_io(now - yp["start"], yp["duration"])
            col   = _sc(YELLOW_COL, alpha)
            for px, py in yp["tiles"]:
                self._px(g, px, py, col)

    def _draw_confetti(self, g):
        for p in self._confetti:
            px = int(p["x"]) % BOARD_W
            py = int(p["y"]) % BOARD_H
            if 0 <= px < BOARD_W and 0 <= py < BOARD_H:
                g[(px, py)] = p["color"]

    # ─────────────────────────────────────────────────────────────────────────
    # Phase draw methods
    # ─────────────────────────────────────────────────────────────────────────

    def _draw_lobby(self, g):
        """
        Lobby layout (all coords in board space):
          Starfield background.
          Minus button : 1 col × 3 rows below start (col=1, rows START_Y+4..START_Y+6)
          Plus  button : cross shape above start (centred at col=1, rows START_Y-3..START_Y-1)
          Start block  : dim pulsing green  x=0..2, y=START_Y..START_Y+3
          GO button    : orange, x=3, y=START_Y..START_Y+3
          Player count : flipped text, faces the start side
        """
        t = self._phase_time
        self._fill(g, (5, 3, 12))

        # Stars
        for y in range(BOARD_H):
            for x in range(BOARD_W):
                sv = abs(math.sin(x*7.31+y*5.13+t*1.5))
                if sv > .982:
                    fade = (sv-.982)/.018
                    g[(x,y)] = (_clamp(80*fade), _clamp(80*fade), _clamp(100*fade))

        # Minus button (below start)
        a_minus = (.65+.35*abs(math.sin(t*4)) if self._lobby_confirm > 0
                   else .45+.20*math.sin(t*1.8))
        for pos in self._minus_tiles:
            g[pos] = _sc(BTN_MINUS_COL, a_minus)

        # Plus button (above start, cross shape)
        a_plus = (.65+.35*abs(math.sin(t*4)) if self._lobby_confirm > 0
                  else .45+.20*math.sin(t*2.2+1.))
        for pos in self._plus_tiles:
            g[pos] = _sc(BTN_PLUS_COL, a_plus)

        # Start block guide (dim green)
        sa = .28 + .18*math.sin(t*1.2)
        for pos in self.start_tiles:
            g[pos] = _sc(GREEN_START, sa)

        # GO button (orange, right of start)
        a_go = .55 + .35*abs(math.sin(t*1.5))
        for pos in self._go_tiles:
            g[pos] = _sc(BTN_START_COL, a_go)

        # Player count — flipped text centred near plus button area
        num_str  = str(self._lobby_players)
        label    = num_str + "P"
        tw       = _text_width_3x5(label)
        # Place text so it reads from the start side; we render at logical coords
        # then flip internally.  Logical origin: above plus button area, centred.
        lx = max(0, (BOARD_W - tw) // 2)
        ly = START_Y - 8   # a few rows above the plus button
        num_col = (255, 220, 0) if self._lobby_confirm > 0 else WHITE
        _text3x5_flipped(g, label, lx, ly, num_col)

    def _draw_show_maze(self, g):
        self._fill(g, BLACK)
        self._draw_maze_overlay(g)
        # No player count display, no timer on board

    def _draw_playing(self, g):
        self._draw_galaxy(g)
        if self._maze_visible:
            self._draw_maze_overlay(g)
        else:
            t   = self._phase_time
            now = time.time()
            # Always show start and finish
            for pos in self.start_tiles:  g[pos] = GREEN_START
            for pos in self.finish_tiles: g[pos] = GREEN_FINISH
            # Special tiles: NOT shown while maze is hidden (per spec)
            # Active reveals ARE shown
            for rev in self.purple_reveals:
                a = _fade_io(now - rev["start"], rev["duration"])
                for px, py in rev["tiles"]:
                    self._px(g, px, py, _sc(HINT_CYAN, .5+a*.5))
            for yp in self.yellow_paths:
                a = _fade_io(now - yp["start"], yp["duration"])
                for px, py in yp["tiles"]:
                    self._px(g, px, py, _sc(YELLOW_COL, a))
        # No timer on board

    def _draw_fail(self, g):
        t = self._phase_time
        if self._timer > 0:
            flash = min(1., self._timer / FAIL_FLASH_DUR)
            for y in range(BOARD_H):
                for x in range(BOARD_W):
                    g[(x, y)] = (_clamp(200*flash), 0, 0)
        else:
            self._fill(g, (6, 0, 8))
        # Pulse start to guide players back
        a = _pulse(t, period=.75, lo=.35, hi=1.)
        for pos in self.start_tiles:
            g[pos] = _sc(GREEN_START, a)
        # Flipped "GO BACK" text
        _text3x5_flipped(g, "GO",   2, BOARD_H - 12, WHITE)
        _text3x5_flipped(g, "BACK", 0, BOARD_H - 7,  WHITE)

    def _draw_reset(self, g):
        """
        'RESET GAME' screen.  Red waves radiating FROM the start (left) side.
        """
        t  = self._phase_time
        # Wave direction: toward high x (away from start)
        for y in range(BOARD_H):
            for x in range(BOARD_W):
                wave = math.sin((x * 0.6) - t * 5.0) * 0.5 + 0.5
                r = _clamp(150 * wave + 50)
                g[(x, y)] = (r, 0, 0)
        # Start block bright green
        for pos in self.start_tiles:
            g[pos] = GREEN_START
        # Flipped "RESET GAME" — two lines
        _text3x5_flipped(g, "RESET", 1, BOARD_H - 14, WHITE)
        _text3x5_flipped(g, "GAME",  2, BOARD_H - 8,  WHITE)

    def _draw_win(self, g):
        self._fill(g, (0, 8, 4))
        self._draw_confetti(g)
        # Flipped "WIN  xP"
        score_str = str(max(0, self.team_score))
        label     = "WIN " + score_str + "P"
        tw        = _text_width_3x5(label)
        lx        = max(0, (BOARD_W - tw) // 2)
        _text3x5_flipped(g, "WIN", 1, BOARD_H - 20, WHITE)
        _text3x5_flipped(g, score_str + " P", 0, BOARD_H - 13, (255, 220, 0))

    def _draw_lose(self, g):
        self._fill(g, (8, 0, 0))
        self._draw_confetti(g)
        # Flipped "LOST"
        _text3x5_flipped(g, "LOST", 0, BOARD_H - 13, WHITE)


# =============================================================================
# SimWindow — tkinter preview
# =============================================================================

class SimWindow:
    """
    tkinter window that shows the 16×32 LED grid and relays mouse events
    to the game as touch press / release events.

    Controls
    --------
    Left-click / drag  →  touch press
    Release            →  touch release
    F / F11            →  toggle fullscreen
    Escape             →  exit fullscreen
    """

    CELL_PX = 18

    def __init__(self, root, game):
        self.root = root
        self.game = game
        self.cell = float(self.CELL_PX)
        self.fullscreen = False
        self._last_tile = None
        self._running   = True

        root.title("Memory Maze — Simulator")
        root.configure(bg="#111")

        self.canvas = tk.Canvas(root, bg="black", highlightthickness=0,
                                width=int(BOARD_W*self.cell),
                                height=int(BOARD_H*self.cell))
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.status = tk.StringVar(value="LOBBY")
        tk.Label(root, textvariable=self.status, bg="#1a1a1a", fg="#00ff00",
                 font=("Consolas", 9), anchor="w").pack(fill=tk.X, padx=4)

        self.canvas.bind("<ButtonPress-1>",   self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<B1-Motion>",       self._on_drag)

        root.bind("<f>",         self._toggle_fs)
        root.bind("<F11>",       self._toggle_fs)
        root.bind("<Escape>",    self._exit_fs)
        root.bind("<Configure>", self._on_resize)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._rects = {}
        self._build_rects()

        self._last_t = time.time()
        self._loop()

    def _build_rects(self):
        self.canvas.delete("all")
        self._rects.clear()
        c = self.cell
        for y in range(BOARD_H):
            for x in range(BOARD_W):
                rid = self.canvas.create_rectangle(
                    x*c, y*c, x*c+c, y*c+c,
                    fill="#000000", outline="#181818", width=1)
                self._rects[(x, y)] = rid

    def _draw_grid(self):
        g = self.game.grid
        for (x, y), (r, gv, b) in g.items():
            rid = self._rects.get((x, y))
            if rid:
                self.canvas.itemconfig(rid, fill="#%02x%02x%02x" % (r, gv, b))

    def _loop(self):
        if not self._running:
            return
        now = time.time()
        dt  = max(0.001, min(0.1, now - self._last_t))
        self._last_t = now
        self.game.update(dt)
        self.game.render()
        self._draw_grid()
        s = self.game.describe_state()
        self.status.set(
            f"{s['phase'].upper():<10}  Players:{s['player_count']}  "
            f"Score:{s['team_score']}  Time:{s['remaining_fmt']}"
        )
        if self.game.is_game_over():
            self.game.print_state()
        self.root.after(50, self._loop)

    def _tile(self, event):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        c = min(w/BOARD_W, h/BOARD_H)
        ox = (w - c*BOARD_W)/2
        oy = (h - c*BOARD_H)/2
        x = int((event.x - ox)/c)
        y = int((event.y - oy)/c)
        return (x, y) if 0<=x<BOARD_W and 0<=y<BOARD_H else None

    def _on_press(self, ev):
        t = self._tile(ev)
        if t:
            self._last_tile = t
            self.game.on_touch_press(*t)

    def _on_release(self, ev):
        t = self._tile(ev) or self._last_tile
        if t:
            self.game.on_touch_release(*t)
        self._last_tile = None

    def _on_drag(self, ev):
        t = self._tile(ev)
        if t and t != self._last_tile:
            if self._last_tile:
                self.game.on_touch_release(*self._last_tile)
            self._last_tile = t
            self.game.on_touch_press(*t)

    def _on_resize(self, ev):
        if ev.widget is self.root:
            w = self.canvas.winfo_width()
            h = self.canvas.winfo_height()
            if w > 1 and h > 1:
                nc = min(w/BOARD_W, h/BOARD_H)
                if abs(nc - self.cell) > 0.5:
                    self.cell = nc
                    self._build_rects()

    def _toggle_fs(self, _=None):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

    def _exit_fs(self, _=None):
        if self.fullscreen:
            self.fullscreen = False
            self.root.attributes("-fullscreen", False)

    def _on_close(self):
        self._running = False
        self.root.destroy()


# =============================================================================
# Entry point
# =============================================================================

def main():
    import pygame
    pygame.mixer.init()
    _ensure_sounds()

    net  = Network()
    game = MemoryMazeGame(net)
    root = tk.Tk()
    SimWindow(root, game)
    print("Memory Maze SimWindow open.")
    print("  Click grid = floor touch.  F11 = fullscreen.  Close to quit.\n")
    game.print_state()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        print("\nQuit.")
        net.stop()
        pygame.mixer.quit()


if __name__ == "__main__":
    main()
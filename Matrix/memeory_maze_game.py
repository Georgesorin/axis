"""
memory_maze_game.py
===================
Memory Maze — standalone game for a 16 × 32 LED touch floor.

Run directly:  python memory_maze_game.py [--sim]
  --sim  : use matrix_sim_config.json (ports 2000/2001, broadcast)
  (no flag) : use matrix_ctrl_config.json (ports 4626/7800, device IP)

Architecture
------------
  Network        — UDP send/receive, trigger packets, frame packing
  MemoryMazeGame — all game logic + rendering into self.grid dict
  SimWindow      — tkinter preview; reads self.grid, relays mouse as touches
  main()         — wires all three, runs via root.mainloop()
"""

import json
import math
import os
import random
import socket
import sys
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
NUM_CHANNELS     = 8
LEDS_PER_CHANNEL = 64
FRAME_DATA_LEN   = NUM_CHANNELS * LEDS_PER_CHANNEL * 3

BLACK        = (  0,   0,   0)
WHITE        = (255, 255, 255)
PATH_BLUE    = (  0,  55, 210)
HINT_CYAN    = (  0, 180, 220)
PURPLE_COL   = (130,   0, 220)
YELLOW_COL   = (220, 200,   0)
GREEN_START  = (  0, 180,   0)
GREEN_FINISH = ( 20, 255,  80)
RED_FAIL     = (220,   0,   0)
BTN_MINUS_COL = (160,  20, 220)
BTN_PLUS_COL  = (  0, 180, 220)
BTN_START_COL = (255, 140,   0)

PHASE_LOBBY     = "lobby"
PHASE_SHOW_MAZE = "show_maze"
PHASE_PLAYING   = "playing"
PHASE_FAIL      = "fail"
PHASE_WIN       = "win"
PHASE_LOSE      = "lose"

MAZE_SHOW_DUR     = 15.0  # Increased to 20 seconds
PURPLE_DUR        = 5.0   
YELLOW_DUR        = 5.0   
FAIL_FLASH_DUR    = 1.5
WIN_DUR           = 10.0
LOSE_DUR          = 10.0
LOBBY_CONFIRM_DUR = 0.8
SCORE_PER_SEC  = 1
FAIL_PENALTY   = 20
NUM_PURPLE     = 3
NUM_YELLOW     = 2
MIN_PLAYERS    = 2
MAX_PLAYERS    = 6

# Start region: always 3 cols × 4 rows centred on the left long side
START_COLS = 3
START_ROWS = 4
START_X    = 0
START_Y    = (BOARD_H - START_ROWS) // 2   # = 14

# Finish region: 4×4 square, position shifts each reset
FINISH_SIZE = 4


# =============================================================================
# Config loader
# =============================================================================

def _load_network_config(sim: bool) -> dict:
    fname = "matrix_sim_config.json" if sim else "matrix_ctrl_config.json"
    defaults_sim  = {"device_ip": "255.255.255.255", "send_port": 2001,
                     "recv_port": 2000, "bind_ip": "0.0.0.0"}
    defaults_ctrl = {"device_ip": "255.255.255.255", "send_port": 4626,
                     "recv_port": 7800,  "bind_ip": "0.0.0.0"}
    defaults = defaults_sim if sim else defaults_ctrl
    try:
        with open(fname) as f:
            cfg = json.load(f)
        for k, v in defaults.items():
            cfg.setdefault(k, v)
        return cfg
    except Exception:
        return defaults


# =============================================================================
# Sound generation
# =============================================================================

def _ensure_sounds():
    files = {
        # Timer cues
        "half.wav":    (659,  0.35, 0.6, "sine",   300),    
        "warn30.wav":  (784,  0.35, 0.7, "sine",  -200),    
        "tick.wav":    (1100, 0.12, 0.5, "sine",   0),
        # Lobby buttons
        "btn_plus.wav":  (1320, 0.09, 0.5, "sine",   200),  
        "btn_minus.wav": (880,  0.09, 0.5, "sine",  -200),   
        "btn_go.wav":    (660,  0.20, 0.7, "square", 400),   
        # Game events
        "fail.wav":    (180,  0.45, 0.8, "square", -60),    
        "win.wav":     (1047, 0.50, 0.7, "sine",   300),    
        "lose.wav":    (220,  0.60, 0.7, "saw",   -80),     
        "reset.wav":   (440,  0.30, 0.6, "square",  0),    
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
    return 120 if players <= 3 else 180


# =============================================================================
# Text rendering 
# =============================================================================

_GLYPH_H = 5

def _text3x5_rot90(g, text: str, x0: int, y0: int, col: tuple) -> None:
    cy = y0
    for ch in text:
        glyph = FONT_3x5.get(ch, FONT_3x5.get("?", [0, 0, 0]))
        glyph_w = len(glyph)
        for ci, mask in enumerate(glyph):
            for ri in range(_GLYPH_H):
                if (mask >> ri) & 1:
                    px = x0 + (_GLYPH_H - 1 - ri)
                    py = cy + ci
                    if 0 <= px < BOARD_W and 0 <= py < BOARD_H:
                        g[(px, py)] = col
        cy += glyph_w + 1

def _text_height_3x5(text: str) -> int:
    total = 0
    for ch in text:
        glyph = FONT_3x5.get(ch, FONT_3x5.get("?", [0, 0, 0]))
        total += len(glyph) + 1
    return max(0, total - 1)

def _draw_centered_rot90(g, text: str, x0: int, col: tuple) -> None:
    th = _text_height_3x5(text)
    y0 = max(0, (BOARD_H - th) // 2)
    _text3x5_rot90(g, text, x0, y0, col)


# =============================================================================
# Network
# =============================================================================

class Network:
    def __init__(self, sim: bool = False):
        cfg = _load_network_config(sim)
        self.target_ip = cfg["device_ip"]
        self.send_port = cfg["send_port"]
        self.recv_port = cfg["recv_port"]
        self.bind_ip   = cfg.get("bind_ip", "0.0.0.0")
        self.sim       = sim
        self.seq       = 0
        self._lock     = threading.Lock()
        self.triggers: Dict[Tuple[int, int], bool] = {}

        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_send.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self.sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_recv.settimeout(0.3)
        try:
            self.sock_recv.bind((self.bind_ip, self.recv_port))
            print(f"Network ({'sim' if sim else 'ctrl'}): recv :{self.recv_port}  send→{self.target_ip}:{self.send_port}")
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
                                self.triggers[(ch, led)] = (data[base + 1 + led] == 0xCC)
            except socket.timeout:
                pass
            except Exception:
                pass

    def is_pressed(self, x: int, y: int) -> bool:
        if not (0 <= x < BOARD_W and 0 <= y < BOARD_H):
            return False
        ch  = min(y // 4, NUM_CHANNELS - 1)
        row = y % 4
        led = row * 16 + x if row % 2 == 0 else row * 16 + (15 - x)
        with self._lock:
            return self.triggers.get((ch, led), False)

    def send_frame(self, grid: dict):
        buf = bytearray(FRAME_DATA_LEN)
        for (x, y), color in grid.items():
            if not (0 <= x < BOARD_W and 0 <= y < BOARD_H):
                continue
            ch     = min(y // 4, NUM_CHANNELS - 1)
            row    = y % 4
            led    = row * 16 + x if row % 2 == 0 else row * 16 + (15 - x)
            block  = NUM_CHANNELS * 3
            offset = led * block + ch
            if offset + NUM_CHANNELS * 2 < len(buf):
                buf[offset]                  = color[1]   
                buf[offset + NUM_CHANNELS]   = color[0]   
                buf[offset + NUM_CHANNELS*2] = color[2]   
        self._send_packet(buf)

    def _send_packet(self, frame_data: bytes):
        self.seq = (self.seq % 0xFFFF) + 1
        ip, port = self.target_ip, self.send_port

        def tx(pkt):
            try:
                self.sock_send.sendto(pkt, (ip, port))
            except Exception:
                pass

        def make(inner, cs=0x1E):
            r1, r2 = random.randint(0, 127), random.randint(0, 127)
            ln = len(inner) - 1
            p  = bytearray([0x75, r1, r2, (ln >> 8) & 0xFF, ln & 0xFF])
            p += bytearray(inner)
            p += bytearray([cs, 0x00])
            return p

        tx(bytearray([0x75, 0, 0, 0, 8, 0x02, 0, 0, 0x33, 0x44, (self.seq >> 8) & 0xFF, self.seq & 0xFF, 0, 0, 0, 0x0E, 0]))
        pay = bytearray()
        for _ in range(NUM_CHANNELS):
            pay += bytes([(LEDS_PER_CHANNEL >> 8) & 0xFF, LEDS_PER_CHANNEL & 0xFF])
        tx(make(bytearray([0x02, 0, 0, 0x88, 0x77, 0xFF, 0xF0, (len(pay) >> 8) & 0xFF, len(pay) & 0xFF]) + pay))
        
        for i, idx in enumerate(range(0, len(frame_data), 984), 1):
            chunk = frame_data[idx:idx + 984]
            inner = bytearray([0x02, 0, 0, 0x88, 0x77, (i >> 8) & 0xFF, i & 0xFF, (len(chunk) >> 8) & 0xFF, len(chunk) & 0xFF]) + bytearray(chunk)
            tx(make(inner, 0x1E if len(chunk) == 984 else 0x36))
            time.sleep(0.001)
            
        tx(bytearray([0x75, 0, 0, 0, 8, 0x02, 0, 0, 0x55, 0x66, (self.seq >> 8) & 0xFF, self.seq & 0xFF, 0, 0, 0, 0x0E, 0]))

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
    def __init__(self, net: Network):
        self.net  = net
        self.grid = {(x, y): BLACK for y in range(BOARD_H) for x in range(BOARD_W)}

        self.phase        = PHASE_LOBBY
        self.player_count = MIN_PLAYERS
        self.team_score   = 0
        self.finishers    = 0
        self.total_time   = 0.0
        self.remaining    = 0.0

        self.maze             = []
        self.start_tiles      = self._static_start_tiles()
        self.finish_tiles: Set[Coord] = set()
        self.purple_tiles: Set[Coord] = set()
        self.yellow_tiles: Set[Coord] = set()
        self.purple_reveals: List[dict] = []
        self.yellow_paths:   List[dict] = []

        self._curr_pressed: Set[Coord] = set()
        self._phase_time   = 0.0
        self._timer        = 0.0
        self._maze_visible = False
        self._reveal_order: List[Coord] = []
        self._reveal_count = 0.0

        self._lobby_players  = MIN_PLAYERS
        self._lobby_confirm  = 0.0

        self._return_presses = 0

        self._confetti: List[dict] = []
        self._just_returned = False

        self._audio_timer: Optional[AudioTimer] = None

        self._minus_tiles = self._compute_minus_tiles()
        self._plus_tiles  = self._compute_plus_tiles()
        self._go_tiles    = self._compute_go_tiles()

        print("MemoryMazeGame ready — LOBBY.")

    # ─────────────────────────────────────────────────────────────────────────
    # Static geometry
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _static_start_tiles() -> Set[Coord]:
        return {(x, y) for x in range(START_COLS)
                for y in range(START_Y, START_Y + START_ROWS)}

    def _compute_minus_tiles(self) -> Set[Coord]:
        cx  = START_X + START_COLS // 2
        top = START_Y + START_ROWS
        return {(cx, top + r) for r in range(3)}

    def _compute_plus_tiles(self) -> Set[Coord]:
        cx  = START_X + START_COLS // 2
        bot = START_Y - 1
        top = bot - 2
        full    = {(cx - 1 + dx, top + dy) for dx in range(3) for dy in range(3)}
        corners = {(cx - 1, top), (cx + 1, top), (cx - 1, bot), (cx + 1, bot)}
        return full - corners

    def _compute_go_tiles(self) -> Set[Coord]:
        gx = START_X + START_COLS
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
            PHASE_WIN:       "All players reached the finish!",
            PHASE_LOSE:      "Timer ran out!",
        }
        return {
            "phase":           self.phase,
            "phase_label":     labels.get(self.phase, self.phase),
            "player_count":    self.player_count,
            "lobby_selection": self._lobby_players,
            "team_score":      self.team_score,
            "finishers":       self.finishers,
            "remaining_sec":   round(self.remaining, 1),
            "remaining_fmt":   f"{secs//60}:{secs%60:02d}",
            "maze_visible":    self._maze_visible,
            "maze_ready":      bool(self.maze),
            "reveal_progress": round(self._reveal_count / total, 3) if total else 0.0,
            "purple_tiles":    len(self.purple_tiles),
            "yellow_tiles":    len(self.yellow_tiles),
            "return_presses":    self._return_presses,
            "players_to_return": max(0, self.player_count - self._return_presses),
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
    # Touch shims 
    # ─────────────────────────────────────────────────────────────────────────

    def on_touch(self, index: int):
        x, y = index % BOARD_W, index // BOARD_W
        if 0 <= x < BOARD_W and 0 <= y < BOARD_H:
            self._curr_pressed.add((x, y))
            self._handle_press(x, y)

    def on_release(self, index: int):
        x, y = index % BOARD_W, index // BOARD_W
        self._curr_pressed.discard((x, y))

    def on_touch_press(self, x: int, y: int):
        self._curr_pressed.add((x, y))
        self._handle_press(x, y)

    def on_touch_release(self, x: int, y: int):
        self._curr_pressed.discard((x, y))

    # ─────────────────────────────────────────────────────────────────────────
    # Main loop
    # ─────────────────────────────────────────────────────────────────────────

    def update(self, dt: float):
        self._just_returned = False
        self._phase_time   += dt
        self._poll_touches()

        if   self.phase == PHASE_LOBBY:     self._upd_lobby(dt)
        elif self.phase == PHASE_SHOW_MAZE: self._upd_show_maze(dt)
        elif self.phase == PHASE_PLAYING:   self._upd_playing(dt)
        elif self.phase == PHASE_FAIL:      self._upd_fail(dt)
        elif self.phase in (PHASE_WIN, PHASE_LOSE): self._upd_end(dt)

    def render(self):
        g = self.grid
        if   self.phase == PHASE_LOBBY:     self._draw_lobby(g)
        elif self.phase == PHASE_SHOW_MAZE: self._draw_show_maze(g)
        elif self.phase == PHASE_PLAYING:   self._draw_playing(g)
        elif self.phase == PHASE_FAIL:      self._draw_fail(g)
        elif self.phase == PHASE_WIN:       self._draw_win(g)
        elif self.phase == PHASE_LOSE:      self._draw_lose(g)
        self.net.send_frame(g)

    def is_game_over(self) -> bool:
        return self._just_returned

    # ─────────────────────────────────────────────────────────────────────────
    # Touch polling 
    # ─────────────────────────────────────────────────────────────────────────

    def _poll_touches(self):
        new: Set[Coord] = set()
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

    def _handle_press(self, x: int, y: int):
        if   self.phase == PHASE_LOBBY:     self._lobby_press(x, y)
        elif self.phase == PHASE_SHOW_MAZE: self._show_maze_press(x, y)
        elif self.phase == PHASE_PLAYING:   self._playing_press(x, y)
        elif self.phase == PHASE_FAIL:      self._fail_press(x, y)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase updaters
    # ─────────────────────────────────────────────────────────────────────────

    def _upd_lobby(self, dt: float):
        if self._lobby_confirm > 0:
            self._lobby_confirm -= dt

    def _upd_show_maze(self, dt: float):
        total = len(self._reveal_order)
        if total > 0:
            self._reveal_count = min(float(total),
                                     self._reveal_count + (total / MAZE_SHOW_DUR) * dt)
        self._timer -= dt
        if self._timer <= 0:
            self._maze_visible = False
            self.phase = PHASE_PLAYING
            # Start timer precisely when phase switches to Playing to prevent 10s loss
            self._audio_timer = AudioTimer(self.remaining) 
            print("Maze hidden — PLAYING.")

    def _upd_playing(self, dt: float):
        self.remaining -= dt
        if self.remaining <= 0:
            self.remaining = 0.0
            self._enter_lose()
            return

        if self._audio_timer:
            self._audio_timer.update()

        now = time.time()
        self.purple_reveals = [r for r in self.purple_reveals
                               if now - r["start"] < r["duration"]]
        self.yellow_paths   = [p for p in self.yellow_paths
                               if now - p["start"] < p["duration"]]

    def _upd_fail(self, dt: float):
        self._timer -= dt

    def _upd_end(self, dt: float):
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

    def _lobby_press(self, x: int, y: int):
        pos = (x, y)
        if pos in self._minus_tiles:
            old = self._lobby_players
            self._lobby_players = max(MIN_PLAYERS, self._lobby_players - 1)
            self._lobby_confirm = LOBBY_CONFIRM_DUR
            play_sound("btn_minus.wav")
            print(f"Lobby: {old} → {self._lobby_players} players (−)")
            return
        if pos in self._plus_tiles:
            old = self._lobby_players
            self._lobby_players = min(MAX_PLAYERS, self._lobby_players + 1)
            self._lobby_confirm = LOBBY_CONFIRM_DUR
            play_sound("btn_plus.wav")
            print(f"Lobby: {old} → {self._lobby_players} players (+)")
            return
        if pos in self._go_tiles:
            play_sound("btn_go.wav")
            print(f"Lobby: GO pressed — {self._lobby_players} players.")
            self._start_game(self._lobby_players)

    def _show_maze_press(self, x: int, y: int):
        if (x, y) not in self.start_tiles:
            print(f"FAIL — Player entered field early at ({x}, {y})!")
            self._enter_fail()
        if (x, y) not in self.start_tiles and (x, y) not in self.finish_tiles:
            self._maze_visible = False
        self._step(x, y)

    def _playing_press(self, x: int, y: int):
        self._step(x, y)

    def _fail_press(self, x: int, y: int):
        if (x, y) in self.start_tiles:
            self._return_presses += 1
            print(f"Fail: {self._return_presses}/{self.player_count} returned.")
            if self._return_presses >= self.player_count:
                print("All returned — new round.")
                # We intentionally DO NOT reset the timer here so it ticks down continuously
                self._new_round()

    def _step(self, x: int, y: int):
        pos = (x, y)

        # ── Finish ────────────────────────────────────────────────────────────
        if pos in self.finish_tiles:
            self.finishers += 1
            print(f"Finish! {self.finishers}/{self.player_count}")

            # Check if this is the last player reaching the finish
            if self.finishers >= self.player_count:
                # Calculate score based ONLY on the time remaining for the last player
                pts = int(self.remaining * SCORE_PER_SEC)
                self.team_score = max(0, self.team_score + pts)
                print(f"Last player finished! Final score calculation: +{pts} pts. Total: {self.team_score}")
                self._enter_win()
            return
        
        if pos in self.start_tiles:
            return
        
        # ── Purple hint tile ──────────────────────────────────────────────────
        if pos in self.purple_tiles:
            self._clear_2x2(x, y, self.purple_tiles)
            
            # Find 2x2 blocks mapped to even coordinates to reveal whole pathways
            potential_blocks = []
            for py in range(0, BOARD_H - 1, 2):
                for px in range(0, BOARD_W - 1, 2):
                    if (self.maze[py][px]
                            and (px, py) not in self.start_tiles
                            and (px, py) not in self.finish_tiles
                            and (px, py) not in self.purple_tiles
                            and (px, py) not in self.yellow_tiles):
                        potential_blocks.append((px, py))
            
            chosen_blocks = random.sample(potential_blocks, min(3, len(potential_blocks)))
            
            hints = set()
            for bx, by in chosen_blocks:
                for dy in range(2):
                    for dx in range(2):
                        hints.add((bx + dx, by + dy))

            self.purple_reveals.append({
                "tiles": hints, "start": time.time(), "duration": PURPLE_DUR
            })
            print(f"Purple at {pos} — {len(chosen_blocks)} blocks revealed.")
            return

        # ── Yellow path tile ──────────────────────────────────────────────────
        if pos in self.yellow_tiles:
            self._clear_2x2(x, y, self.yellow_tiles)
            path = bfs_path_to_targets(
                self.maze, x, y, self.finish_tiles, BOARD_W, BOARD_H)
            if path:
                self.yellow_paths.append({
                    "tiles": path, "start": time.time(), "duration": YELLOW_DUR
                })
                print(f"Yellow at {pos} — path {len(path)} tiles.")
            tx2, ty2 = (x // 2 * 2), (y // 2 * 2)
            for iy in range(ty2, min(ty2 + 2, BOARD_H)):
                for ix in range(tx2, min(tx2 + 2, BOARD_W)):
                    self.maze[iy][ix] = False
            return

        # ── Wall ──────────────────────────────────────────────────────────────
        if not self.maze[y][x]:
            print(f"FAIL — wall at {pos}.")
            self._enter_fail()

    def _clear_2x2(self, x: int, y: int, tile_set: Set[Coord]):
        tx2, ty2 = (x // 2 * 2), (y // 2 * 2)
        for iy in range(ty2, ty2 + 2):
            for ix in range(tx2, tx2 + 2):
                tile_set.discard((ix, iy))

    # ─────────────────────────────────────────────────────────────────────────
    # State transitions
    # ─────────────────────────────────────────────────────────────────────────

    def _start_game(self, players: int):
        self.player_count = players
        self.team_score   = 0
        self.finishers    = 0
        self.total_time   = float(_game_duration(players))
        self.remaining    = self.total_time
        print(f"Starting: {players} players, {int(self.total_time)}s.")
        self._new_round()

    def _new_round(self):
        self.start_tiles  = self._static_start_tiles()
        fx, fy = pick_finish_position(BOARD_W, BOARD_H)
        self.finish_tiles = {(fx + dx, fy + dy)
                             for dx in range(FINISH_SIZE)
                             for dy in range(FINISH_SIZE)}

        seed_y = START_Y + START_ROWS // 2
        self.maze = generate_thick_maze_prim(
            BOARD_W, BOARD_H, seed_x=START_X, seed_y=seed_y)

        self._force_open(self.start_tiles)
        self._force_open(self.finish_tiles)
        self._place_special_tiles()
        self.purple_reveals.clear()
        self.yellow_paths.clear()

        self._reveal_order   = bfs_reveal_order(
            self.maze, self.start_tiles, BOARD_W, BOARD_H)
        self._reveal_count   = 0.0
        self._timer          = MAZE_SHOW_DUR
        self._maze_visible   = True
        self.finishers        = 0
        self._return_presses  = 0
        
        # Reset phase entirely to show maze, audio timer will be instantiated on transit
        self.phase = PHASE_SHOW_MAZE
        self._audio_timer = None 

        n = sum(c for row in self.maze for c in row)
        print(f"Maze: {n} path tiles  finish at ({fx},{fy})  "
              f"P={len(self.purple_tiles)}  Y={len(self.yellow_tiles)}")

    def _force_open(self, tiles: Set[Coord]):
        for x, y in tiles:
            if 0 <= x < BOARD_W and 0 <= y < BOARD_H:
                self.maze[y][x] = True

    def _place_special_tiles(self):
        potential = []
        for y in range(0, BOARD_H - 1, 2):
            for x in range(0, BOARD_W - 1, 2):
                if (self.maze[y][x]
                        and (x, y) not in self.start_tiles
                        and (x, y) not in self.finish_tiles):
                    potential.append((x, y))
        random.shuffle(potential)

        def block4(tl):
            return {(tl[0] + dx, tl[1] + dy) for dx in range(2) for dy in range(2)}

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
        self._return_presses = 0
        self.purple_reveals.clear()
        self.yellow_paths.clear()
        play_sound("fail.wav")

    def _enter_win(self):
        print(f"WIN! Score: {self.team_score}")
        self.phase  = PHASE_WIN
        self._timer = WIN_DUR
        self._spawn_confetti(win=True)
        play_sound("win.wav")

    def _enter_lose(self):
        self.team_score = max(0, self.team_score - FAIL_PENALTY)
        print(f"LOSE. Score: {self.team_score}")
        self.phase  = PHASE_LOSE
        self._timer = LOSE_DUR
        self._spawn_confetti(win=False)
        play_sound("lose.wav")

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
            {"x": random.uniform(0, BOARD_W - 1),
             "y": random.uniform(-BOARD_H, 0),
             "vx": random.uniform(-1.2, 1.2),
             "vy": random.uniform(5.5, 13.),
             "color": random.choice(palette)}
            for _ in range(44)
        ]

    def _upd_confetti(self, dt: float):
        for p in self._confetti:
            p["y"] += p["vy"] * dt
            p["x"]  = (p["x"] + p["vx"] * dt) % BOARD_W
            if p["y"] >= BOARD_H:
                p["y"]  = random.uniform(-4, 0)
                p["x"]  = random.uniform(0, BOARD_W - 1)
                p["vy"] = random.uniform(5.5, 13.)
                p["vx"] = random.uniform(-1.2, 1.2)

    # ─────────────────────────────────────────────────────────────────────────
    # Low-level drawing helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _px(self, g, x: int, y: int, col: tuple):
        if 0 <= x < BOARD_W and 0 <= y < BOARD_H:
            g[(x, y)] = col

    def _fill(self, g, col: tuple):
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
                elif pos in self.purple_tiles:
                    g[pos] = _sc(PURPLE_COL, _pulse(t, period=1.0))
                elif pos in self.yellow_tiles:
                    g[pos] = _sc(YELLOW_COL, _pulse(t, period=1.3))
                elif pos in revealed and self.maze[y][x]:
                    dist = wave_front - rev_idx.get(pos, wave_front)
                    g[pos] = _lerp(WHITE, PATH_BLUE, dist/4) if 0 < dist <= 4 else PATH_BLUE
                else:
                    g[pos] = BLACK

        for rev in self.purple_reveals:
            alpha = _fade_io(now - rev["start"], rev["duration"])
            col   = _sc(HINT_CYAN, .45+alpha*.55)
            for px, py in rev["tiles"]:
                self._px(g, px, py, col)

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
        t = self._phase_time
        self._fill(g, (5, 3, 12))

        # Stars
        for y in range(BOARD_H):
            for x in range(BOARD_W):
                sv = abs(math.sin(x*7.31+y*5.13+t*1.5))
                if sv > .982:
                    fade = (sv-.982)/.018
                    g[(x,y)] = (_clamp(80*fade), _clamp(80*fade), _clamp(100*fade))

        # Minus button
        a_minus = (.65+.35*abs(math.sin(t*4)) if self._lobby_confirm > 0
                   else .45+.20*math.sin(t*1.8))
        for pos in self._minus_tiles:
            g[pos] = _sc(BTN_MINUS_COL, a_minus)

        # Plus button 
        a_plus = (.65+.35*abs(math.sin(t*4)) if self._lobby_confirm > 0
                  else .45+.20*math.sin(t*2.2+1.))
        for pos in self._plus_tiles:
            g[pos] = _sc(BTN_PLUS_COL, a_plus)

        # Start block guide
        sa = .28 + .18*math.sin(t*1.2)
        for pos in self.start_tiles:
            g[pos] = _sc(GREEN_START, sa)

        # GO button
        a_go = .55 + .35*abs(math.sin(t*1.5))
        for pos in self._go_tiles:
            g[pos] = _sc(BTN_START_COL, a_go)

        label   = str(self._lobby_players) + "P"
        num_col = (255, 220, 0) if self._lobby_confirm > 0 else WHITE
        _draw_centered_rot90(g, label, 5, num_col)

    def _draw_show_maze(self, g):
        self._fill(g, BLACK)
        self._draw_maze_overlay(g)

    def _draw_playing(self, g):
        self._draw_galaxy(g)
        if self._maze_visible:
            self._draw_maze_overlay(g)
        else:
            now = time.time()
            for pos in self.start_tiles:  g[pos] = GREEN_START
            for pos in self.finish_tiles: g[pos] = GREEN_FINISH
            for rev in self.purple_reveals:
                a = _fade_io(now - rev["start"], rev["duration"])
                for px, py in rev["tiles"]:
                    self._px(g, px, py, _sc(HINT_CYAN, .5+a*.5))
            for yp in self.yellow_paths:
                a = _fade_io(now - yp["start"], yp["duration"])
                for px, py in yp["tiles"]:
                    self._px(g, px, py, _sc(YELLOW_COL, a))

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
        _draw_centered_rot90(g, "RETRY", 9, WHITE)

    def _draw_win(self, g):
        self._fill(g, (0, 8, 4))
        self._draw_confetti(g)
        score_str = str(max(0, self.team_score))
        win_h   = _text_height_3x5("WIN")
        score_h = _text_height_3x5(score_str + "P")
        gap     = 2
        total_h = win_h + gap + score_h
        y0 = max(0, (BOARD_H - total_h) // 2)
        x0 = (BOARD_W - 5) // 2   
        _text3x5_rot90(g, "WIN", x0, y0, WHITE)
        _text3x5_rot90(g, score_str + "P", x0, y0 + win_h + gap, (255, 220, 0))

    def _draw_lose(self, g):
        self._fill(g, (8, 0, 0))
        self._draw_confetti(g)
        _draw_centered_rot90(g, "LOST", (BOARD_W - 5) // 2, WHITE)


# =============================================================================
# SimWindow — tkinter preview
# =============================================================================

class SimWindow:
    CELL_PX = 18

    def __init__(self, root: tk.Tk, game: MemoryMazeGame):
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

        self._rects: Dict[Coord, int] = {}
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
        return (x, y) if 0 <= x < BOARD_W and 0 <= y < BOARD_H else None

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
    sim = "--sim" in sys.argv
    if sim:
        print("Running in SIMULATION mode (matrix_sim_config.json)")
    else:
        print("Running in CONTROLLER mode (matrix_ctrl_config.json)")

    pygame.mixer.init()
    _ensure_sounds()

    net  = Network(sim=sim)
    game = MemoryMazeGame(net)
    root = tk.Tk()
    SimWindow(root, game)
    print("Memory Maze SimWindow open.")
    print("  Click grid = floor touch.  F11 = fullscreen.  Close to quit.")
    print("  Pass --sim to use simulation ports (2000/2001).\n")
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
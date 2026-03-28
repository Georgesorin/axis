"""
memory_maze_game.py
═══════════════════
Full Memory Maze Game for a 16 × 32 LED floor matrix.

GAME FLOW
  LOBBY      → player count (2-8) chosen by stepping on a floor zone
  SHOW_MAZE  → maze revealed with animated BFS wave for 5 s
  PLAYING    → galaxy floor, hidden maze, special tiles, countdown timer
  FAIL       → wrong tile stepped → room dark, only start lit, return to start
  WIN / LOSE → score display, then back to LOBBY

TOUCH INTERFACE (called by Controller / Simulator)
  game.on_touch_press(x, y)   — tile pressed  (0 ≤ x < 16, 0 ≤ y < 32)
  game.on_touch_release(x, y) — tile released
  # compatibility shim: touch_index = y * BOARD_W + x
  game.on_touch(touch_index)
  game.on_release(touch_index)
  game.update(dt)             — advance logic by dt seconds (call ≈ 20 FPS)
  game.build_frame(fw)        — paint state into a FrameWriter-compatible object:
                                fw.clear()  /  fw.set_pixel(x,y,r,g,b)  /  fw.get_bytes()
"""

import time
import math
import random
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from small_font  import FONT_3x5
from matrix_font import FONT_5x7

# ══════════════════════════════════════════════════════════════════════════════
# Board constants
# ══════════════════════════════════════════════════════════════════════════════
BOARD_W          = 16
BOARD_H          = 32
NUM_CHANNELS     = 8
LEDS_PER_CHANNEL = 64
FRAME_DATA_LEN   = NUM_CHANNELS * LEDS_PER_CHANNEL * 3   # 1 536 bytes

# ══════════════════════════════════════════════════════════════════════════════
# Colors  (R, G, B)
# ══════════════════════════════════════════════════════════════════════════════
BLACK         = (  0,   0,   0)
WHITE         = (255, 255, 255)
PATH_BLUE     = (  0,  55, 210)   # maze path
HINT_CYAN     = (  0, 180, 220)   # purple-reveal hint tiles
PURPLE_COL    = (130,   0, 220)   # purple special tile
YELLOW_COL    = (220, 200,   0)   # yellow special tile
GREEN_START   = (  0, 180,   0)   # start region
GREEN_FINISH  = ( 20, 255,  80)   # finish region
RED_FAIL      = (220,   0,   0)
DIM_START_OK  = (  0,  60,   0)   # start tiles in fail-dark mode

# ══════════════════════════════════════════════════════════════════════════════
# Game phases
# ══════════════════════════════════════════════════════════════════════════════
PHASE_LOBBY     = "lobby"
PHASE_SHOW_MAZE = "show_maze"
PHASE_PLAYING   = "playing"
PHASE_FAIL      = "fail"
PHASE_WIN       = "win"
PHASE_LOSE      = "lose"

# ══════════════════════════════════════════════════════════════════════════════
# Timing / scoring constants
# ══════════════════════════════════════════════════════════════════════════════
MAZE_INITIAL_SHOW   = 5.0    # s — reveal at game start
MAZE_RETURN_SHOW    = 3.0    # s — brief re-show when all players at start/finish
PURPLE_REVEAL_DUR   = 3.0    # s — how long purple hint tiles pulse
YELLOW_PATH_DUR     = 4.0    # s — how long yellow path stays visible
FAIL_FLASH_DUR      = 1.5    # s — red flash before darkness
WIN_DISPLAY_DUR     = 7.0    # s — win screen before returning to lobby
LOSE_DISPLAY_DUR    = 6.0    # s — lose screen before returning to lobby
LOBBY_CONFIRM_DUR   = 0.8    # s — flash after player count selected
SCORE_PER_SECOND    = 10     # points per remaining second on finish
FAIL_PENALTY        = 20     # points deducted on fail/timeout

# Purple tiles placed in maze
NUM_PURPLE = 3
# Yellow tiles placed in maze
NUM_YELLOW = 2


def _game_duration(players: int) -> int:
    """3 min for 2 players, scaling linearly to 5 min for 8 players."""
    return int(180 + (players - 2) * 20)   # 180 s … 300 s


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _clamp(v: float, lo: int = 0, hi: int = 255) -> int:
    return max(lo, min(hi, int(round(v))))


def _sc(col: Tuple[int,int,int], alpha: float) -> Tuple[int,int,int]:
    """Scale color by alpha (0..1)."""
    return (_clamp(col[0]*alpha), _clamp(col[1]*alpha), _clamp(col[2]*alpha))


def _lerp(a: Tuple, b: Tuple, t: float) -> Tuple:
    t = max(0.0, min(1.0, t))
    return (_clamp(a[0]+(b[0]-a[0])*t), _clamp(a[1]+(b[1]-a[1])*t), _clamp(a[2]+(b[2]-a[2])*t))


def _pulse(t: float, period: float = 1.0, lo: float = 0.3, hi: float = 1.0) -> float:
    """Smooth sinusoidal pulse between lo and hi."""
    return lo + (hi - lo) * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))


def _fade_in_out(t: float, dur: float) -> float:
    """Returns 0→1→0 over 'dur' seconds using a half-sine envelope."""
    if dur <= 0 or t < 0 or t > dur:
        return 0.0
    return math.sin(math.pi * t / dur)


# ══════════════════════════════════════════════════════════════════════════════
# Lobby zone layout — two panels separated by a black divider at y = 15
#
#  TOP PANEL  (y 0-14)   → 2-4 players   3 equal bands of 5 rows each
#  SEPARATOR  (y 15)     → black gap
#  BOT PANEL  (y 16-31)  → 5-8 players   4 equal bands of 4 rows each
#
# Each entry: (player_count, y_top, y_bottom, base_color)
# ══════════════════════════════════════════════════════════════════════════════

# fmt: off
_LOBBY_TOP: List[Tuple[int,int,int,Tuple[int,int,int]]] = [
    (2,  0,  4,  (  0,  70, 220)),   # blue
    (3,  5,  9,  (  0, 175, 185)),   # cyan
    (4, 10, 14,  (  0, 165,  55)),   # green
]

_LOBBY_BOT: List[Tuple[int,int,int,Tuple[int,int,int]]] = [
    (5, 16, 19,  (170, 210,   0)),   # yellow-green
    (6, 20, 23,  (220, 155,   0)),   # amber
    (7, 24, 27,  (220,  70,   0)),   # orange
    (8, 28, 31,  (215,  15,   0)),   # red
]
# fmt: on

# Flat list used by the press handler
_ALL_LOBBY_ZONES = _LOBBY_TOP + _LOBBY_BOT


# ══════════════════════════════════════════════════════════════════════════════
# GameState
# ══════════════════════════════════════════════════════════════════════════════

class GameState:
    """Full Memory Maze game state for a 16 × 32 LED floor matrix."""

    # ─── init ────────────────────────────────────────────────────────────────

    def __init__(self):
        # Phase and player count
        self.phase: str = PHASE_LOBBY
        self.player_count: int = 2

        # Maze
        self.maze:         List[List[bool]]       = []   # [y][x]  True = path
        self.start_tiles:  Set[Tuple[int,int]]    = set()
        self.finish_tiles: Set[Tuple[int,int]]    = set()
        self.finish_entry: Optional[Tuple[int,int]] = None

        # Special tiles
        self.purple_tiles: Set[Tuple[int,int]] = set()
        self.yellow_tiles: Set[Tuple[int,int]] = set()

        # Active visual effects
        # purple_reveals:  [{ tiles: set, start: float, duration: float }]
        self.purple_reveals: List[Dict] = []
        # yellow_paths:    [{ tiles: list, start: float, duration: float }]
        self.yellow_paths:   List[Dict] = []

        # Current touches (set of (x, y))
        self.current_touches: Set[Tuple[int,int]] = set()

        # Scoring
        self.team_score:  int = 0
        self.finishers:   int = 0

        # Timer
        self.remaining:   float = 0.0
        self.total_time:  float = 0.0

        # Phase timer (multipurpose countdown)
        self._timer:         float = 0.0
        self._maze_visible:  bool  = False

        # Maze reveal animation
        self._reveal_order:  List[Tuple[int,int]] = []
        self._reveal_count:  float = 0.0   # fractional index into _reveal_order

        # "Return to start" tracking (fail phase)
        self._return_presses: int = 0

        # Galaxy animation
        self._gal_phase: float = 0.0

        # Lobby state
        self._lobby_choice: Optional[int] = None
        self._lobby_flash:  float = 0.0

        # Brief maze re-show during playing phase
        self._field_clear_timer: float = 0.0   # how long field has been clear of touches

        # Confetti particles for WIN / LOSE screens
        # Each: {'x': float, 'y': float, 'vx': float, 'vy': float, 'color': tuple}
        self._confetti: List[Dict] = []

    # ─── public interface ────────────────────────────────────────────────────

    def on_touch(self, touch_index: int):
        """Shim: touch_index = y * BOARD_W + x"""
        x, y = touch_index % BOARD_W, touch_index // BOARD_W
        if 0 <= x < BOARD_W and 0 <= y < BOARD_H:
            self.on_touch_press(x, y)

    def on_release(self, touch_index: int):
        """Shim: touch_index = y * BOARD_W + x"""
        x, y = touch_index % BOARD_W, touch_index // BOARD_W
        if 0 <= x < BOARD_W and 0 <= y < BOARD_H:
            self.on_touch_release(x, y)

    def on_touch_press(self, x: int, y: int):
        self.current_touches.add((x, y))
        if   self.phase == PHASE_LOBBY:     self._lobby_press(x, y)
        elif self.phase == PHASE_SHOW_MAZE: self._show_maze_press(x, y)
        elif self.phase == PHASE_PLAYING:   self._playing_press(x, y)
        elif self.phase == PHASE_FAIL:      self._fail_press(x, y)

    def on_touch_release(self, x: int, y: int):
        self.current_touches.discard((x, y))

    def set_player_count(self, count: int):
        """External API: set player count and start game (bypasses lobby)."""
        self._lobby_choice = max(2, min(8, count))
        self._lobby_flash  = LOBBY_CONFIRM_DUR

    def update(self, dt: float):
        self._gal_phase += dt
        if   self.phase == PHASE_LOBBY:     self._upd_lobby(dt)
        elif self.phase == PHASE_SHOW_MAZE: self._upd_show_maze(dt)
        elif self.phase == PHASE_PLAYING:   self._upd_playing(dt)
        elif self.phase == PHASE_FAIL:      self._upd_fail(dt)
        elif self.phase in (PHASE_WIN, PHASE_LOSE): self._upd_end(dt)

    def build_frame(self, fw):
        fw.clear()
        if   self.phase == PHASE_LOBBY:     self._draw_lobby(fw)
        elif self.phase == PHASE_SHOW_MAZE: self._draw_show_maze(fw)
        elif self.phase == PHASE_PLAYING:   self._draw_playing(fw)
        elif self.phase == PHASE_FAIL:      self._draw_fail(fw)
        elif self.phase == PHASE_WIN:       self._draw_win(fw)
        elif self.phase == PHASE_LOSE:      self._draw_lose(fw)

    # ─── maze generation (Randomized Prim's) ─────────────────────────────────

    def _generate_maze_prim(self) -> List[List[bool]]:
        """
        Randomized Prim's algorithm on a 16 × 32 grid.
        Room cells sit at even (x, y) coordinates; walls between them are
        carved as the algorithm progresses.  Starting cell is near the start
        region's centre.
        """
        w, h = BOARD_W, BOARD_H
        grid = [[False] * w for _ in range(h)]

        # Snap start to nearest even cell inside (or just left of) start region
        sx = 0
        sy = (BOARD_H // 2) & ~1   # nearest even to the vertical centre

        grid[sy][sx] = True

        # Wall list: (wall_x, wall_y, target_room_x, target_room_y)
        walls: List[Tuple[int,int,int,int]] = []

        def _push_walls(cx: int, cy: int):
            for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2)):
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < w and 0 <= ny < h and not grid[ny][nx]:
                    walls.append(((cx + nx) // 2, (cy + ny) // 2, nx, ny))

        _push_walls(sx, sy)

        while walls:
            idx = random.randrange(len(walls))
            wx, wy, nx, ny = walls.pop(idx)
            if not grid[ny][nx]:
                grid[wy][wx] = True   # open the wall cell
                grid[ny][nx] = True   # open the new room cell
                _push_walls(nx, ny)

        return grid

    # ─── region computation ────────────────────────────────────────────────

    def _compute_regions(self):
        """Set start_tiles and finish_tiles based on current player_count."""
        p  = self.player_count
        yc = BOARD_H // 2

        # Start: left edge, 2 or 3 columns, 4 rows centred vertically
        cols = 2 if p <= 4 else 3
        y0   = yc - 2
        self.start_tiles = {
            (x, y) for x in range(cols) for y in range(y0, y0 + 4)
        }

        # Finish: right edge, 3×3 or 4×4, centred vertically
        size = 3 if p <= 4 else 4
        x0   = BOARD_W - size
        y0   = yc - size // 2
        self.finish_tiles = {
            (x, y) for x in range(x0, x0 + size) for y in range(y0, y0 + size)
        }

    def _force_open(self, tiles: Set[Tuple[int,int]]):
        for x, y in tiles:
            if 0 <= x < BOARD_W and 0 <= y < BOARD_H:
                self.maze[y][x] = True

    def _enforce_finish_entry(self):
        """
        Ensure the finish region has exactly ONE adjacent path tile from outside
        (the single entry point).  All other border cells are walled off.
        """
        # Gather all cells immediately outside the finish region
        border: Set[Tuple[int,int]] = set()
        for fx, fy in self.finish_tiles:
            for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                nx, ny = fx + dx, fy + dy
                if (0 <= nx < BOARD_W and 0 <= ny < BOARD_H
                        and (nx, ny) not in self.finish_tiles
                        and (nx, ny) not in self.start_tiles):
                    border.add((nx, ny))

        # Which border cells are already maze paths?
        path_entries = [(x, y) for x, y in border if self.maze[y][x]]

        if not path_entries:
            # No entry found — carve one from the leftmost border cell
            cands = sorted(border, key=lambda t: t[0])
            entry = cands[0]
            self.maze[entry[1]][entry[0]] = True
            path_entries = [entry]

        # Keep exactly one entry (prefer the leftmost)
        entry = min(path_entries, key=lambda t: t[0])

        # Wall off every other border cell
        for bx, by in border:
            if (bx, by) != entry:
                self.maze[by][bx] = False

        self.finish_entry = entry

    # ─── BFS helpers ──────────────────────────────────────────────────────────

    def _bfs_reveal_order(self) -> List[Tuple[int,int]]:
        """BFS from start region → return path tiles in discovery order (for animation)."""
        visited: Set[Tuple[int,int]] = set()
        order:   List[Tuple[int,int]] = []
        queue = deque()

        for t in self.start_tiles:
            if self.maze[t[1]][t[0]] and t not in visited:
                visited.add(t)
                queue.append(t)

        while queue:
            x, y = queue.popleft()
            order.append((x, y))
            for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                nx, ny = x + dx, y + dy
                if (0 <= nx < BOARD_W and 0 <= ny < BOARD_H
                        and (nx, ny) not in visited
                        and self.maze[ny][nx]):
                    visited.add((nx, ny))
                    queue.append((nx, ny))

        return order

    def _bfs_path_to_finish(self, sx: int, sy: int) -> Optional[List[Tuple[int,int]]]:
        """
        BFS shortest path from (sx, sy) to any finish tile.
        Treats finish tiles as passable regardless of maze state.
        Returns the path (list of (x,y)) or None if unreachable.
        """
        if not (0 <= sx < BOARD_W and 0 <= sy < BOARD_H):
            return None

        parent: Dict[Tuple[int,int], Optional[Tuple[int,int]]] = {(sx, sy): None}
        queue  = deque([(sx, sy)])

        while queue:
            cx, cy = queue.popleft()
            if (cx, cy) in self.finish_tiles:
                path: List[Tuple[int,int]] = []
                pos: Optional[Tuple[int,int]] = (cx, cy)
                while pos is not None:
                    path.append(pos)
                    pos = parent[pos]
                path.reverse()
                return path
            for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                nx, ny = cx + dx, cy + dy
                nb     = (nx, ny)
                if 0 <= nx < BOARD_W and 0 <= ny < BOARD_H and nb not in parent:
                    if self.maze[ny][nx] or nb in self.finish_tiles:
                        parent[nb] = (cx, cy)
                        queue.append(nb)

        return None

    # ─── special tile placement ────────────────────────────────────────────

    def _place_special_tiles(self):
        playing_field = [
            (x, y)
            for y in range(BOARD_H)
            for x in range(BOARD_W)
            if self.maze[y][x]
            and (x, y) not in self.start_tiles
            and (x, y) not in self.finish_tiles
        ]
        random.shuffle(playing_field)
        self.purple_tiles = set(playing_field[:NUM_PURPLE])
        self.yellow_tiles = set(playing_field[NUM_PURPLE: NUM_PURPLE + NUM_YELLOW])

    # ─── game init / round reset ───────────────────────────────────────────

    def _start_game(self, player_count: int):
        self.player_count = player_count
        self.team_score   = 0
        self.finishers    = 0
        self.total_time   = float(_game_duration(player_count))
        self.remaining    = self.total_time
        self._new_round()

    def _new_round(self):
        """Generate maze, place special tiles, enter SHOW_MAZE phase."""
        self._compute_regions()
        self.maze = self._generate_maze_prim()
        self._force_open(self.start_tiles)
        self._force_open(self.finish_tiles)
        self._enforce_finish_entry()
        self._place_special_tiles()
        self.purple_reveals.clear()
        self.yellow_paths.clear()
        self._reveal_order = self._bfs_reveal_order()
        self._reveal_count  = 0.0
        self._timer         = MAZE_INITIAL_SHOW
        self._maze_visible  = True
        self.finishers      = 0
        self._return_presses = 0
        self._field_clear_timer = 0.0
        self.phase = PHASE_SHOW_MAZE

    # ─── update methods ───────────────────────────────────────────────────

    def _upd_lobby(self, dt: float):
        if self._lobby_choice is not None:
            self._lobby_flash -= dt
            if self._lobby_flash <= 0:
                self._start_game(self._lobby_choice)
                self._lobby_choice = None

    def _upd_show_maze(self, dt: float):
        # Advance BFS reveal wave
        total = len(self._reveal_order)
        if total > 0:
            speed = total / MAZE_INITIAL_SHOW       # cells per second
            self._reveal_count = min(float(total), self._reveal_count + speed * dt)

        self._timer -= dt
        if self._timer <= 0:
            self._maze_visible = False
            self.phase = PHASE_PLAYING

    def _upd_playing(self, dt: float):
        self.remaining -= dt
        if self.remaining <= 0:
            self.remaining = 0.0
            self._enter_lose()
            return

        # Expire timed effects
        now = time.time()
        self.purple_reveals = [r for r in self.purple_reveals
                                if now - r['start'] < r['duration']]
        self.yellow_paths   = [p for p in self.yellow_paths
                                if now - p['start'] < p['duration']]

        # Brief maze re-show when ALL active touches are on start or finish only
        any_on_field = any(
            (x, y) not in self.start_tiles and (x, y) not in self.finish_tiles
            for (x, y) in self.current_touches
        )

        if not any_on_field and self.current_touches:
            self._field_clear_timer += dt
            if self._field_clear_timer >= 0.5 and not self._maze_visible:
                self._maze_visible = True
                self._timer = MAZE_RETURN_SHOW
        else:
            self._field_clear_timer = 0.0
            if any_on_field:
                self._maze_visible = False

        # Count down brief maze re-show
        if self._maze_visible:
            self._timer -= dt
            if self._timer <= 0:
                self._maze_visible = False

    def _upd_fail(self, dt: float):
        self._timer -= dt   # used for the red-flash duration

    def _upd_end(self, dt: float):
        self._timer -= dt
        self._upd_confetti(dt)
        if self._timer <= 0:
            self.phase = PHASE_LOBBY
            self._lobby_choice = None
            self._confetti.clear()

    # ─── press handlers ───────────────────────────────────────────────────

    def _lobby_press(self, x: int, y: int):
        for p_count, y0, y1, _col in _ALL_LOBBY_ZONES:
            if y0 <= y <= y1:
                self._lobby_choice = p_count
                self._lobby_flash  = LOBBY_CONFIRM_DUR
                break

    def _show_maze_press(self, x: int, y: int):
        # Any step onto the playing field hides the maze immediately
        if (x, y) not in self.start_tiles and (x, y) not in self.finish_tiles:
            self._maze_visible = False
        self._step(x, y)

    def _playing_press(self, x: int, y: int):
        self._step(x, y)

    def _fail_press(self, x: int, y: int):
        if (x, y) in self.start_tiles:
            self._return_presses += 1
            if self._return_presses >= self.player_count:
                # All players back at start — new round (keep score, reset timer)
                self.remaining = self.total_time
                self._new_round()

    def _step(self, x: int, y: int):
        """Process a player stepping onto tile (x, y)."""
        pos = (x, y)

        if pos in self.finish_tiles:
            self.finishers += 1
            self.team_score += int(self.remaining * SCORE_PER_SECOND)
            if self.finishers >= self.player_count:
                self._enter_win()
            return

        if pos in self.start_tiles:
            return   # safe zone

        if pos in self.purple_tiles:
            # Activate purple hint: reveal 3 random path tiles for PURPLE_REVEAL_DUR seconds
            self.purple_tiles.discard(pos)
            pool = [
                t for t in [
                    (px, py)
                    for py in range(BOARD_H)
                    for px in range(BOARD_W)
                    if self.maze[py][px]
                    and (px, py) not in self.start_tiles
                    and (px, py) not in self.finish_tiles
                    and (px, py) not in self.purple_tiles
                    and (px, py) not in self.yellow_tiles
                ]
            ]
            hints = random.sample(pool, min(3, len(pool)))
            self.purple_reveals.append({
                'tiles':    set(hints),
                'start':    time.time(),
                'duration': PURPLE_REVEAL_DUR,
            })
            return

        if pos in self.yellow_tiles:
            # Activate yellow path: show BFS path to finish, tile becomes wall
            self.yellow_tiles.discard(pos)
            path = self._bfs_path_to_finish(x, y)
            if path:
                self.yellow_paths.append({
                    'tiles':    path,
                    'start':    time.time(),
                    'duration': YELLOW_PATH_DUR,
                })
            self.maze[y][x] = False   # tile becomes wall
            return

        if not self.maze[y][x]:
            # Stepped on wall → FAIL
            self._enter_fail()
            return

        # Valid path tile — no-op (could add footstep effects here)

    def _enter_fail(self):
        self.team_score    -= FAIL_PENALTY
        self.phase          = PHASE_FAIL
        self._timer         = FAIL_FLASH_DUR
        self._return_presses = 0
        self._maze_visible  = False
        self.purple_reveals.clear()
        self.yellow_paths.clear()

    def _enter_win(self):
        self.phase  = PHASE_WIN
        self._timer = WIN_DISPLAY_DUR
        self._spawn_confetti(win=True)

    def _enter_lose(self):
        self.team_score -= FAIL_PENALTY
        self.phase  = PHASE_LOSE
        self._timer = LOSE_DISPLAY_DUR
        self._spawn_confetti(win=False)

    # ═══════════════════════════════════════════════════════════════════════
    # Drawing
    # ═══════════════════════════════════════════════════════════════════════

    # ─── galaxy floor ─────────────────────────────────────────────────────

    def _draw_galaxy(self, fw):
        """
        Animated galaxy floor: moving black → deep-blue → purple gradient.
        Uses multi-layer trig for organic flow, with rare bright star sparkles.
        """
        t = self._gal_phase
        for y in range(BOARD_H):
            for x in range(BOARD_W):
                # Three overlapping sine waves with different frequencies / speeds
                n1 = math.sin(x * 0.42 + t * 0.55) * math.cos(y * 0.33 + t * 0.30)
                n2 = math.sin(x * 0.68 - t * 0.40 + y * 0.19) * 0.55
                n3 = math.cos(y * 0.52 + t * 0.22 + x * 0.10) * 0.45
                v  = max(0.0, min(1.0, (n1 * 0.45 + n2 * 0.32 + n3 * 0.23) + 0.5))

                # Colour ramp: black → navy → electric blue → purple
                if v < 0.35:
                    s = v / 0.35
                    r, g, b = _clamp(s * 8), 0, _clamp(s * 55)
                elif v < 0.65:
                    s = (v - 0.35) / 0.30
                    r, g, b = _clamp(8 + s * 15), _clamp(s * 8), _clamp(55 + s * 110)
                else:
                    s = (v - 0.65) / 0.35
                    r, g, b = _clamp(23 + s * 90), _clamp(8 * (1-s)), _clamp(165 + s * 55)

                # Bright star sparkle (rare, ~2 % of pixels at any moment)
                star_v = abs(math.sin(x * 7.31 + y * 5.13 + t * 2.17))
                if star_v > 0.975:
                    fade = (star_v - 0.975) / 0.025
                    r = _clamp(r + 140 * fade)
                    g = _clamp(g + 140 * fade)
                    b = _clamp(b + 140 * fade)

                fw.set_pixel(x, y, r, g, b)

    # ─── maze overlay (paths, start, finish, specials) ────────────────────

    def _draw_maze_overlay(self, fw):
        """
        Draw maze paths in blue, start in green, finish in bright-green,
        purple tiles as pulsing purple, yellow tiles as pulsing yellow.
        The BFS reveal wave is respected: cells not yet 'revealed' are black.
        """
        now = time.time()
        t   = self._gal_phase

        # Set of tiles currently revealed by the animation wave
        revealed: Set[Tuple[int,int]] = set(
            self._reveal_order[:int(self._reveal_count)]
        )

        for y in range(BOARD_H):
            for x in range(BOARD_W):
                pos = (x, y)
                if pos in self.start_tiles:
                    fw.set_pixel(x, y, *GREEN_START)
                elif pos in self.finish_tiles:
                    fw.set_pixel(x, y, *GREEN_FINISH)
                elif pos in self.purple_tiles:
                    alpha = _pulse(t, period=1.0, lo=0.5, hi=1.0)
                    fw.set_pixel(x, y, *_sc(PURPLE_COL, alpha))
                elif pos in self.yellow_tiles:
                    alpha = _pulse(t, period=1.3, lo=0.5, hi=1.0)
                    fw.set_pixel(x, y, *_sc(YELLOW_COL, alpha))
                elif pos in revealed and self.maze[y][x]:
                    # Animate the leading edge of the reveal wave with a brighter blue
                    rev_idx = self._reveal_order.index(pos) if pos in self._reveal_order else -1
                    wave_front = int(self._reveal_count)
                    if rev_idx >= max(0, wave_front - 4):
                        fw.set_pixel(x, y, *_lerp(WHITE, PATH_BLUE, (wave_front - rev_idx) / 4))
                    else:
                        fw.set_pixel(x, y, *PATH_BLUE)
                else:
                    fw.set_pixel(x, y, 0, 0, 0)   # wall or unrevealed = black

        # Purple hint tiles pulsing (revealed by purple activation)
        for rev in self.purple_reveals:
            elapsed = now - rev['start']
            alpha   = _fade_in_out(elapsed, rev['duration'])
            col     = _sc(HINT_CYAN, 0.45 + alpha * 0.55)
            for px, py in rev['tiles']:
                fw.set_pixel(px, py, *col)

        # Yellow path tiles fading in/out
        for yp in self.yellow_paths:
            elapsed = now - yp['start']
            alpha   = _fade_in_out(elapsed, yp['duration'])
            col     = _sc(YELLOW_COL, alpha)
            for px, py in yp['tiles']:
                fw.set_pixel(px, py, *col)

    # ─── phase draw methods ───────────────────────────────────────────────

    def _draw_lobby(self, fw):
        t = self._gal_phase

        # ── background: very dark galaxy so bands pop ──────────────────────
        for y in range(BOARD_H):
            for x in range(BOARD_W):
                fw.set_pixel(x, y, 4, 2, 10)

        # ── helper: draw one band ──────────────────────────────────────────
        def _band(p_count: int, y0: int, y1: int, base: Tuple[int,int,int]):
            selected = (self._lobby_choice == p_count)
            if selected:
                # Fast strobe pulse while confirming
                alpha = 0.55 + 0.45 * abs(math.sin(t * 10))
            else:
                # Gentle breathing
                idx   = p_count - 2
                alpha = 0.38 + 0.14 * math.sin(t * 1.6 + idx * 0.85)

            for row in range(y0, y1 + 1):
                for col in range(BOARD_W):
                    # Slight horizontal shimmer
                    shimmer = 0.88 + 0.12 * math.sin(col * 0.55 + t * 2.4 + row * 0.3)
                    fw.set_pixel(col, row, *_sc(base, alpha * shimmer))

            # Player-count digit(s) centred vertically in the band
            label = str(p_count)
            lw    = len(label) * 4          # 3 px glyph + 1 px gap each char
            cx    = BOARD_W // 2 - lw // 2
            cy    = (y0 + y1) // 2 - 2
            text_col = WHITE if not selected else (255, 255, 180)
            self._text3x5(fw, label, cx, cy, text_col)

        # ── top panel: 2-4 ────────────────────────────────────────────────
        for p_count, y0, y1, base in _LOBBY_TOP:
            _band(p_count, y0, y1, base)

        # "2-4" group label — top-left of top panel
        self._text3x5(fw, "2-4", 0, 0, (200, 200, 255))

        # ── separator (y=15) ──────────────────────────────────────────────
        for col in range(BOARD_W):
            fw.set_pixel(col, 15, 0, 0, 0)

        # ── bottom panel: 5-8 ─────────────────────────────────────────────
        for p_count, y0, y1, base in _LOBBY_BOT:
            _band(p_count, y0, y1, base)

        # "5-8" group label — top-left of bottom panel
        self._text3x5(fw, "5-8", 0, 16, (255, 220, 160))

    def _draw_show_maze(self, fw):
        # Black background then maze
        for y in range(BOARD_H):
            for x in range(BOARD_W):
                fw.set_pixel(x, y, 0, 0, 0)
        self._draw_maze_overlay(fw)
        # Player count indicator in top-left
        self._text3x5(fw, f"{self.player_count}P", 0, 0, YELLOW_COL)

    def _draw_playing(self, fw):
        self._draw_galaxy(fw)

        if self._maze_visible:
            # Overlay full maze
            self._draw_maze_overlay(fw)
        else:
            # Only permanent markers visible over galaxy
            for x, y in self.start_tiles:
                fw.set_pixel(x, y, *GREEN_START)
            for x, y in self.finish_tiles:
                fw.set_pixel(x, y, *GREEN_FINISH)

            now = time.time()
            t   = self._gal_phase
            # Purple hint glows
            for px, py in self.purple_tiles:
                alpha = _pulse(t, period=1.0, lo=0.3, hi=0.7)
                fw.set_pixel(px, py, *_sc(PURPLE_COL, alpha))
            # Yellow tile glows
            for yx, yy in self.yellow_tiles:
                alpha = _pulse(t, period=1.3, lo=0.3, hi=0.7)
                fw.set_pixel(yx, yy, *_sc(YELLOW_COL, alpha))
            # Active purple reveals
            for rev in self.purple_reveals:
                elapsed = now - rev['start']
                alpha   = _fade_in_out(elapsed, rev['duration'])
                col     = _sc(HINT_CYAN, 0.5 + alpha * 0.5)
                for rx, ry in rev['tiles']:
                    fw.set_pixel(rx, ry, *col)
            # Active yellow paths
            for yp in self.yellow_paths:
                elapsed = now - yp['start']
                alpha   = _fade_in_out(elapsed, yp['duration'])
                col     = _sc(YELLOW_COL, alpha)
                for px, py in yp['tiles']:
                    fw.set_pixel(px, py, *col)

        # Timer overlay
        self._draw_timer(fw)

    def _draw_fail(self, fw):
        t     = self._gal_phase
        phase = self._timer   # counts DOWN from FAIL_FLASH_DUR

        if phase > 0:
            # Red flash — fades out as timer approaches 0
            flash = min(1.0, phase / FAIL_FLASH_DUR)
            for y in range(BOARD_H):
                for x in range(BOARD_W):
                    fw.set_pixel(x, y, _clamp(200 * flash), 0, 0)
        else:
            # Dark room — very dim purple base
            for y in range(BOARD_H):
                for x in range(BOARD_W):
                    fw.set_pixel(x, y, 6, 0, 8)

        # Start tiles pulse green — guide players home
        alpha = _pulse(t, period=0.75, lo=0.35, hi=1.0)
        for sx, sy in self.start_tiles:
            fw.set_pixel(sx, sy, *_sc(GREEN_START, alpha))

        # "GO BACK" hint
        self._text3x5(fw, "GO", 1, 1, WHITE)
        self._text3x5(fw, "BACK", 0, 6, WHITE)

    def _draw_win(self, fw):
        # Very dark background so green confetti pops
        for y in range(BOARD_H):
            for x in range(BOARD_W):
                fw.set_pixel(x, y, 0, 8, 4)

        # Confetti particles
        for p in self._confetti:
            px, py = int(p['x']) % BOARD_W, int(p['y']) % BOARD_H
            if 0 <= px < BOARD_W and 0 <= py < BOARD_H:
                fw.set_pixel(px, py, *p['color'])

        # "WON" centred near top
        self._text3x5(fw, "WON", 4, 4, WHITE)

        # Score
        score_str = str(max(0, self.team_score))
        sx = max(0, BOARD_W // 2 - (len(score_str) * 4) // 2)
        self._text3x5(fw, score_str, sx, 12, YELLOW_COL)
        self._text3x5(fw, "pts", 5, 19, (180, 180, 180))

    def _draw_lose(self, fw):
        # Very dark background so red confetti pops
        for y in range(BOARD_H):
            for x in range(BOARD_W):
                fw.set_pixel(x, y, 8, 0, 0)

        # Confetti particles
        for p in self._confetti:
            px, py = int(p['x']) % BOARD_W, int(p['y']) % BOARD_H
            if 0 <= px < BOARD_W and 0 <= py < BOARD_H:
                fw.set_pixel(px, py, *p['color'])

        # "LOST" centred near top
        self._text3x5(fw, "LOST", 1, 4, WHITE)

        # Score
        score_str = str(self.team_score)
        sx = max(0, BOARD_W // 2 - (len(score_str) * 4) // 2)
        self._text3x5(fw, score_str, sx, 12, YELLOW_COL)
        self._text3x5(fw, "pts", 5, 19, (180, 180, 180))

    # ─── confetti system ──────────────────────────────────────────────────

    def _spawn_confetti(self, win: bool):
        """
        Spawn 44 confetti particles.  Particles start above the top edge
        (y < 0) so they rain down naturally from the moment the screen appears.

        WIN  → many green shades + some white/yellow sparkles
        LOSE → many red/orange shades + some dark-orange sparks
        """
        NUM_PARTICLES = 44

        if win:
            palette = [
                ( 20, 220,  40),   # lime green
                (  0, 180,  30),   # mid green
                ( 60, 255, 100),   # bright mint
                (  0, 130,  20),   # forest green
                (120, 255,  60),   # yellow-green
                (200, 240,  20),   # acid yellow-green
                (255, 255, 200),   # near-white sparkle
                ( 80, 255, 200),   # aqua sparkle
            ]
        else:
            palette = [
                (220,  10,   0),   # crimson
                (255,  50,   0),   # red-orange
                (200,   0,   0),   # deep red
                (255, 100,   0),   # orange
                (180,  30,   0),   # dark red
                (255, 150,  30),   # amber
                (255,  80,  50),   # salmon
                (255, 200,  80),   # gold spark
            ]

        self._confetti = []
        for i in range(NUM_PARTICLES):
            self._confetti.append({
                'x':     random.uniform(0, BOARD_W - 1),
                'y':     random.uniform(-BOARD_H, 0),    # start off-screen above
                'vx':    random.uniform(-1.2, 1.2),       # px per second horizontal drift
                'vy':    random.uniform(5.5, 13.0),       # px per second fall speed
                'color': random.choice(palette),
            })

    def _upd_confetti(self, dt: float):
        """Advance all confetti particles; wrap back to top when off bottom."""
        for p in self._confetti:
            p['y'] += p['vy'] * dt
            p['x'] += p['vx'] * dt
            # Wrap x gently within board
            if p['x'] < 0:
                p['x'] += BOARD_W
            elif p['x'] >= BOARD_W:
                p['x'] -= BOARD_W
            # When a particle exits the bottom, recycle it from the top
            if p['y'] >= BOARD_H:
                p['y']  = random.uniform(-4, 0)
                p['x']  = random.uniform(0, BOARD_W - 1)
                p['vy'] = random.uniform(5.5, 13.0)
                p['vx'] = random.uniform(-1.2, 1.2)

    # ─── HUD helpers ──────────────────────────────────────────────────────

    def _draw_timer(self, fw):
        """Render M:SS timer in the top-right corner of the matrix."""
        secs = max(0, int(self.remaining))
        mm   = secs // 60
        ss   = secs % 60
        text = f"{mm}:{ss:02d}"
        # Colour: white normally, flashing red when < 30 s
        if self.remaining < 30:
            col = WHITE if (int(self._gal_phase * 4) % 2 == 0) else RED_FAIL
        else:
            col = WHITE
        x = BOARD_W - len(text) * 4   # right-align (4 px per char)
        self._text3x5(fw, text, max(0, x), 0, col)

    def _text3x5(self, fw, text: str, x: int, y: int,
                 col: Tuple[int,int,int]):
        """
        Render 'text' using the 3×5 FONT_3x5 bitmap font.
        Each character occupies 3 columns; characters are separated by 1 blank column.
        Pixels outside the board are silently clipped.
        """
        cx = x
        for ch in text:
            glyph = FONT_3x5.get(ch, FONT_3x5.get('?', [0, 0, 0]))
            for col_idx, col_byte in enumerate(glyph):
                for row_idx in range(5):
                    if (col_byte >> row_idx) & 1:
                        px, py = cx + col_idx, y + row_idx
                        if 0 <= px < BOARD_W and 0 <= py < BOARD_H:
                            fw.set_pixel(px, py, *col)
            cx += len(glyph) + 1   # 3 columns + 1 px gap


# ══════════════════════════════════════════════════════════════════════════════
# Standalone FrameWriterAdapter
# (mirrors the inner class in Controller.py — kept here for testing / Simulator)
# ══════════════════════════════════════════════════════════════════════════════

class FrameWriterAdapter:
    """
    Translates logical (x, y) pixel writes into the hardware byte layout
    expected by NetworkManager.send_packet().

    Wire format (matching Simulator's refresh_from_buffer):
        For each led_pos in 0..63, channel in 0..7:
            offset = led_pos * 24 + channel
            buffer[offset]      = G  (green)
            buffer[offset + 8]  = R  (red)
            buffer[offset + 16] = B  (blue)

        (x, y) → channel = y // 4,  row_in_ch = y % 4
                  led_x   = x  (even rows)  /  15 - x  (odd rows)
                  led_pos = row_in_ch * 16 + led_x
    """

    def __init__(self):
        self.buffer = bytearray(FRAME_DATA_LEN)

    def clear(self):
        for i in range(len(self.buffer)):
            self.buffer[i] = 0

    def set_pixel(self, x: int, y: int, r: int, g: int, b: int):
        if not (0 <= x < BOARD_W and 0 <= y < BOARD_H):
            return
        channel     = y // 4
        row_in_ch   = y % 4
        led_x       = x if row_in_ch % 2 == 0 else (15 - x)
        led_pos     = row_in_ch * 16 + led_x
        offset      = led_pos * NUM_CHANNELS * 3 + channel   # = led_pos * 24 + channel
        if offset + NUM_CHANNELS * 2 < len(self.buffer):
            self.buffer[offset]                  = _clamp(g)
            self.buffer[offset + NUM_CHANNELS]   = _clamp(r)
            self.buffer[offset + NUM_CHANNELS*2] = _clamp(b)

    def get_bytes(self, append_checksum: bool = False) -> bytes:
        return bytes(self.buffer)
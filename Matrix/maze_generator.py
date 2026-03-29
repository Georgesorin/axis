"""
maze_generator.py
═════════════════
Randomized Prim's maze generation and BFS utilities for the 16 × 32 LED floor.

HOW THICK PRIM'S WORKS HERE
────────────────────────────
Paths and walls are both 2 tiles wide.  "Room" cells are 2×2 blocks whose
top-left corner sits at coordinates divisible by 4.  The algorithm:
  1. Marks the seed 2×2 room as open.
  2. Adds the four neighbouring rooms (4 steps away) to a candidate wall list.
  3. Repeatedly picks a random candidate; if the room on the far side is still
     closed, opens the 2×2 wall block between them AND the 2×2 room, then
     queues that room's neighbours.
This guarantees a perfect maze with no loops and all rooms reachable.

The finish region is NOT walled off to a single entry — it connects naturally
wherever Prim's carved a path.  The finish position shifts each reset.
"""

import random
from collections import deque
from typing import List, Optional, Set, Tuple

# ── type aliases ──────────────────────────────────────────────────────────────
Grid  = List[List[bool]]
Coord = Tuple[int, int]


# ══════════════════════════════════════════════════════════════════════════════
# Core: Thick Randomized Prim's (2-wide paths & walls)
# ══════════════════════════════════════════════════════════════════════════════

def generate_thick_maze_prim(
    width:  int,
    height: int,
    seed_x: int = 0,
    seed_y: Optional[int] = None,
) -> Grid:
    """
    Generate a perfect maze where both paths and walls are 2 tiles wide.

    Room cells sit at coordinates where BOTH x AND y are multiples of 4.
    Each room is a 2×2 block.  The 2×2 block between two adjacent rooms is
    the "wall" that Prim's either keeps or removes.

    Parameters
    ----------
    width, height : grid dimensions (ideally multiples of 4).
    seed_x        : column of the starting room (snapped to nearest 4-multiple).
    seed_y        : row    of the starting room (snapped to nearest 4-multiple).
                    Defaults to vertical centre.

    Returns
    -------
    grid[y][x] : bool — True = passable path, False = solid wall.
    """
    grid: Grid = [[False] * width for _ in range(height)]

    # Snap seed to nearest 4-aligned room
    sx = (max(0, min(width  - 2, seed_x))) // 4 * 4
    sy_default = (height // 2) // 4 * 4
    sy = (max(0, min(height - 2, seed_y if seed_y is not None else sy_default))) // 4 * 4

    def fill_block(x: int, y: int, w: int, h: int, value: bool) -> None:
        for iy in range(y, min(y + h, height)):
            for ix in range(x, min(x + w, width)):
                grid[iy][ix] = value

    # Open the seed 2×2 room
    fill_block(sx, sy, 2, 2, True)

    # Wall candidates: (wall_tl_x, wall_tl_y, room_tl_x, room_tl_y)
    walls: List[Tuple[int, int, int, int]] = []

    def _push_walls(cx: int, cy: int) -> None:
        for dx, dy in ((4, 0), (-4, 0), (0, 4), (0, -4)):
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < width - 1 and 0 <= ny < height - 1 and not grid[ny][nx]:
                wx, wy = (cx + nx) // 2, (cy + ny) // 2
                walls.append((wx, wy, nx, ny))

    _push_walls(sx, sy)

    while walls:
        idx = random.randrange(len(walls))
        wx, wy, nx, ny = walls.pop(idx)

        if not grid[ny][nx]:           # room on the far side still closed
            fill_block(wx, wy, 2, 2, True)   # open the 2×2 wall block
            fill_block(nx, ny, 2, 2, True)   # open the 2×2 room
            _push_walls(nx, ny)

    return grid


# ══════════════════════════════════════════════════════════════════════════════
# BFS utilities (2×2-block aware)
# ══════════════════════════════════════════════════════════════════════════════

def bfs_reveal_order(
    maze:        Grid,
    start_tiles: Set[Coord],
    width:       int,
    height:      int,
) -> List[Coord]:
    """
    BFS that discovers the maze in 2×2 blocks, matching the generation
    structure.  Returns tiles in wave-front order for the reveal animation.
    """
    visited_blocks: Set[Coord] = set()
    order: List[Coord] = []
    queue = deque()

    for x, y in start_tiles:
        tl = (x // 2 * 2, y // 2 * 2)
        if tl not in visited_blocks:
            visited_blocks.add(tl)
            queue.append(tl)

    while queue:
        cx, cy = queue.popleft()

        # Emit all 4 pixels of this 2×2 block
        for dy in range(2):
            for dx in range(2):
                px, py = cx + dx, cy + dy
                if 0 <= px < width and 0 <= py < height:
                    order.append((px, py))

        # Expand to adjacent 2×2 blocks (step by 2)
        for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2)):
            nx, ny = cx + dx, cy + dy
            nb = (nx, ny)
            if (0 <= nx < width - 1 and 0 <= ny < height - 1
                    and nb not in visited_blocks and maze[ny][nx]):
                visited_blocks.add(nb)
                queue.append(nb)

    return order


def bfs_path_to_targets(
    maze:         Grid,
    sx:           int,
    sy:           int,
    target_tiles: Set[Coord],
    width:        int,
    height:       int,
) -> Optional[List[Coord]]:
    """
    BFS shortest path from (sx, sy) to any tile in target_tiles, moving in
    2×2 block steps.  Returns the path as a flat list of individual (x, y)
    tiles (each block contributes 4 tiles), or None if unreachable.
    """
    st_tl = (sx // 2 * 2, sy // 2 * 2)
    parent: dict = {st_tl: None}
    queue = deque([st_tl])

    while queue:
        cx, cy = queue.popleft()

        # Hit if any corner of the 2×2 block is a target
        if any((cx + dx, cy + dy) in target_tiles
               for dx in range(2) for dy in range(2)):
            path: List[Coord] = []
            pos: Optional[Coord] = (cx, cy)
            while pos is not None:
                for dy in range(2):
                    for dx in range(2):
                        path.append((pos[0] + dx, pos[1] + dy))
                pos = parent[pos]
            path.reverse()
            return path

        for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2)):
            nx, ny = cx + dx, cy + dy
            nb = (nx, ny)
            if (0 <= nx < width - 1 and 0 <= ny < height - 1
                    and nb not in parent and maze[ny][nx]):
                parent[nb] = (cx, cy)
                queue.append(nb)

    return None


# ══════════════════════════════════════════════════════════════════════════════
# Finish position helper
# ══════════════════════════════════════════════════════════════════════════════

def pick_finish_position(
    width:  int,
    height: int,
    rng:    Optional[random.Random] = None,
) -> Coord:
    """
    Pick a random 4-aligned top-left corner for the 4×4 finish block on the
    right half of the board.  The finish moves every reset.

    The finish is always a fixed 4×4 square; this function returns its top-left
    corner.  The right edge of the finish aligns with the right edge of the
    board (x = width - 4).
    """
    r = rng or random
    finish_x = width - 4                       # flush with right edge
    # pick a 4-aligned y so the 4×4 block stays inside the board
    max_y_block = (height - 4) // 4
    block_row   = r.randint(0, max_y_block)
    finish_y    = block_row * 4
    return (finish_x, finish_y)
import tkinter as tk
import random
import time
import math
import winsound
import os
import pygame

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BOARD_W = 32
BOARD_H = 16
CELL    = 24

SEP_COL = 15

P1_COLOR = (180, 0, 255)
P2_COLOR = (0, 120, 255)
TRAP     = (255, 0, 0)
BONUS    = (0, 255, 0)

def hex3(c):
    return '#%02x%02x%02x' % c


DIGITS = {
    "0": ["111","101","101","101","111"],
    "1": ["010","110","010","010","111"],
    "2": ["111","001","111","100","111"],
    "3": ["111","001","111","001","111"],
    "4": ["101","101","111","001","001"],
    "5": ["111","100","111","001","111"],
    "6": ["111","100","111","101","111"],
    "7": ["111","001","001","001","001"],
    "8": ["111","101","111","101","111"],
    "9": ["111","101","111","001","111"],
    "GO": ["111 111","101 101","101 101","101 101","111 111"],
    "P": ["110","101","110","100","100"],
    "W": ["101","101","101","111","010"],
    "I": ["111","010","010","010","111"],
    "N": ["101","111","111","101","101"],
    "S": ["111","100","111","001","111"],
    "D": ["110","101","101","101","110"],
    "R": ["110","101","110","101","101"],
    "A": ["010","101","111","101","101"],
    "!": ["010","010","010","000","010"],
    " ": ["000","000","000","000","000"],
}


FONT_3x5 = {
    ' ': [0,0,0], '!': [0,23,0], '"': [3,0,3], '#': [31,10,31],
    '$': [18,31,9], '%': [28,4,7], '&': [21,21,14], '\'': [0,3,0],
    '(': [0,14,17], ')': [17,14,0], '*': [10,4,10], '+': [4,14,4],
    ',': [0,24,0], '-': [4,4,4], '.': [0,16,0], '/': [24,4,3],
    '0': [31,17,31], '1': [0,31,0], '2': [29,21,23], '3': [21,21,31],
    '4': [7,4,31], '5': [23,21,29], '6': [31,21,29], '7': [1,1,31],
    '8': [31,21,31], '9': [23,21,31], ':': [0,10,0], ';': [0,26,0],
    '<': [4,10,17], '=': [10,10,10], '>': [17,10,4], '?': [1,21,7],
    '@': [14,21,22], 'A': [30,5,30], 'B': [31,21,10], 'C': [14,17,17],
    'D': [31,17,14], 'E': [31,21,21], 'F': [31,5,5], 'G': [14,21,29],
    'H': [31,4,31], 'I': [17,31,17], 'J': [8,16,15], 'K': [31,4,27],
    'L': [31,16,16], 'M': [31,2,31], 'N': [31,2,28], 'O': [14,17,14],
    'P': [31,5,2], 'Q': [14,17,30], 'R': [31,5,26], 'S': [18,21,9],
    'T': [1,31,1], 'U': [15,16,15], 'V': [7,24,7], 'W': [31,8,31],
    'X': [27,4,27], 'Y': [3,28,3], 'Z': [25,21,19], '[': [31,17,0],
    '\\': [3,4,24], ']': [0,17,31], '^': [2,1,2], '_': [16,16,16],
    '`': [0,1,2], 'a': [10,21,31], 'b': [31,20,8], 'c': [14,17,17],
    'd': [8,20,31], 'e': [14,21,21], 'f': [4,31,5], 'g': [18,21,15],
    'h': [31,4,24], 'i': [0,29,0], 'j': [16,16,13], 'k': [31,4,27],
    'l': [0,31,0], 'm': [30,2,30], 'n': [28,4,24], 'o': [14,17,14],
    'p': [31,5,2], 'q': [2,5,31], 'r': [28,4,4], 's': [18,21,9],
    't': [4,31,20], 'u': [15,16,15], 'v': [7,24,7], 'w': [31,8,31],
    'x': [27,4,27], 'y': [3,28,31], 'z': [25,21,19], '{': [4,27,17],
    '|': [0,31,0], '}': [17,27,4], '~': [2,1,2],
}


class Game:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Matrix Game")

        self.canvas = tk.Canvas(
            self.root,
            width=BOARD_W * CELL,
            height=BOARD_H * CELL,
            bg="black"
        )
        self.canvas.pack()

        self.tiles  = {}
        self.scores = [0, 0]

        self.bonus_active = [False, False]
        self.bonus_time   = [0, 0]

        self.round       = 1
        self.max_rounds  = 3
        self.round_time  = 20
        self.round_start = time.time()

        self.start_anim  = True
        self.start_time  = time.time()

        self.wipe   = False
        self.wipe_x = 0

        self.countdown         = False
        self.countdown_val     = 3
        self.countdown_time    = time.time()
        self.countdown_started = False

        self.round_anim  = False
        self.wave_radius = 0

        self.game_over_flag = False
        self.winner_line1   = ""
        self.winner_line2   = ""
        self.winner_col     = (255, 255, 255)

        self.root.bind("<Button-1>", self.click)

        try:
            pygame.mixer.init()
            pygame.mixer.music.load(os.path.join(BASE_DIR, "subway-surfers.mp3"))
            pygame.mixer.music.set_volume(0.6)
            pygame.mixer.music.play(-1)
        except Exception as e:
            print(f"[Muzică] {e}")

        self.loop()
        self.root.mainloop()

    def sound_trap(self):
        winsound.PlaySound(
            os.path.join(BASE_DIR, "trap.wav"),
            winsound.SND_FILENAME | winsound.SND_ASYNC
        )

    def sound_bonus(self):
        winsound.Beep(1200, 80)

    def rainbow(self, x, y, t):
        r = int((math.sin(t + x * 0.3) + 1) * 127)
        g = int((math.sin(t + y * 0.3 + 2) + 1) * 127)
        b = int((math.sin(t + x * 0.2 + y * 0.2 + 4) + 1) * 127)
        return (r, g, b)

    def led(self, x, y, color, scale=1):
        x1 = x * CELL
        y1 = y * CELL
        x2 = x1 + CELL * scale
        y2 = y1 + CELL * scale
        self.canvas.create_rectangle(x1, y1, x2, y2,
                                     fill=hex3(color), outline="")

    def draw_led_text_centered(self, key):
        scale   = 2
        pattern = DIGITS[key]

        char_w_cells = len(pattern[0]) * scale
        char_h_cells = len(pattern) * scale

        start_x = (BOARD_W - char_w_cells) // 2
        start_y = (BOARD_H - char_h_cells) // 2

        for ri, row in enumerate(pattern):
            for ci, px in enumerate(row):
                if px == "1":
                    lx = start_x + ci * scale
                    ly = start_y + ri * scale
                    for dx in range(scale):
                        for dy in range(scale):
                            if 0 <= lx+dx < BOARD_W and 0 <= ly+dy < BOARD_H:
                                self.led(lx + dx, ly + dy, (255, 255, 255))

    def draw_round_text(self):
        scale   = 2
        text    = f"R{self.round}"
        char_w  = 3 * scale + scale
        total_w = len(text) * char_w - scale
        start_x = (BOARD_W - total_w) // 2
        start_y = (BOARD_H - 5 * scale) // 2

        for i, ch in enumerate(text):
            if ch == "R":
                pattern = ["110","101","110","101","101"]
            else:
                pattern = DIGITS.get(ch, [])
            for ri, row in enumerate(pattern):
                for ci, px in enumerate(row):
                    if px == "1":
                        lx = start_x + i * char_w + ci * scale
                        ly = start_y + ri * scale
                        for dx in range(scale):
                            for dy in range(scale):
                                if 0 <= lx+dx < BOARD_W and 0 <= ly+dy < BOARD_H:
                                    self.led(lx + dx, ly + dy, (220, 0, 0))

    def draw_pixel_text(self, line1, line2, color):
        scale   = 1
        char_w  = 3
        char_h  = 5
        spacing = 1

        step_x  = (char_w + spacing) * scale
        step_y  = char_h * scale
        gap     = 2

        total_h = step_y + (step_y + gap if line2 else 0)
        base_y  = (BOARD_H - total_h) // 2

        def draw_line(text, start_y):
            total_w = len(text) * step_x - spacing * scale
            start_x = max(0, (BOARD_W - total_w) // 2)
            for i, ch in enumerate(text):
                cols = FONT_3x5.get(ch, FONT_3x5.get(' ', [0,0,0]))
                for ci, col_bits in enumerate(cols):
                    for ri in range(char_h):
                        if (col_bits >> ri) & 1:
                            lx = start_x + i * step_x + ci * scale
                            ly = start_y + ri * scale
                            for dx in range(scale):
                                for dy in range(scale):
                                    px, py = lx + dx, ly + dy
                                    if 0 <= px < BOARD_W and 0 <= py < BOARD_H:
                                        self.led(px, py, color)

        draw_line(line1, base_y)
        if line2:
            draw_line(line2, base_y + step_y + gap)

    def click(self, e):
        if (self.start_anim or self.wipe or self.countdown
                or self.round_anim or self.game_over_flag):
            return

        x = e.x // CELL
        y = e.y // CELL

        if (x, y) not in self.tiles:
            return

        kind, _ = self.tiles[(x, y)]

        if x < SEP_COL:
            p = 0
        elif x > SEP_COL:
            p = 1
        else:
            return

        if self.bonus_active[p]:
            if kind == "trap":
                self.scores[p] = max(0, self.scores[p] - 1)
                self.sound_trap()
            else:
                self.scores[p] += 2
        else:
            if kind == "target":
                self.scores[p] += 1
            elif kind == "trap":
                self.scores[p] = max(0, self.scores[p] - 1)
                self.sound_trap()
            elif kind == "bonus":
                self.bonus_active[p] = True
                self.bonus_time[p]   = time.time() + 15
                self.sound_bonus()

        del self.tiles[(x, y)]

    def spawn(self):
        if (self.start_anim or self.wipe or self.countdown
                or self.round_anim or self.game_over_flag):
            return

        spawn_rate = 0.08 + self.round * 0.04

        if random.random() < spawn_rate:
            while True:
                x = random.randint(0, BOARD_W - 1)
                if x != SEP_COL:
                    break
            y        = random.randint(0, BOARD_H - 1)
            lifetime = max(1.2, 3 - self.round * 0.6)
            kind     = random.choice(["target", "trap", "bonus"])
            self.tiles[(x, y)] = (kind, time.time() + lifetime)

    def update(self):
        if self.game_over_flag:
            return

        now = time.time()

        if self.start_anim:
            if now - self.start_time > 3:
                self.start_anim = False
                self.wipe       = True
                self.wipe_x     = 0
            return

        if self.wipe:
            self.wipe_x += 1
            if self.wipe_x >= BOARD_W:
                self.wipe           = False
                self.countdown      = True
                self.countdown_val  = 3
                self.countdown_time = time.time()
            return

        if self.round_anim:
            self.wave_radius += 1
            if self.wave_radius > max(BOARD_W, BOARD_H):
                self.round_anim        = False
                self.countdown         = True
                self.countdown_val     = 3
                self.countdown_time    = time.time()
                self.countdown_started = False
            return

        if self.countdown and not self.countdown_started:
            winsound.PlaySound(
                os.path.join(BASE_DIR, "countdown.wav"),
                winsound.SND_FILENAME | winsound.SND_ASYNC
            )
            self.countdown_started = True

        if self.countdown:
            if now - self.countdown_time > 1:
                self.countdown_val -= 1
                self.countdown_time = now
            if self.countdown_val < 0:
                self.countdown   = False
                self.round_start = time.time()
            return

        if now - self.round_start > self.round_time:
            self.next_round()
            return

        for pos in list(self.tiles.keys()):
            if now > self.tiles[pos][1]:
                del self.tiles[pos]

        for i in range(2):
            if self.bonus_active[i] and now > self.bonus_time[i]:
                self.bonus_active[i] = False

    def next_round(self):
        self.round += 1
        self.tiles.clear()

        if self.round > self.max_rounds:
            self.game_over()
        else:
            self.round_anim  = True
            self.wave_radius = 0

    def game_over(self):
        self.game_over_flag = True
        self.tiles.clear()

        if self.scores[0] > self.scores[1]:
            self.winner_line1 = "P1"
            self.winner_line2 = "WINS"
            self.winner_col   = P1_COLOR
        elif self.scores[1] > self.scores[0]:
            self.winner_line1 = "P2"
            self.winner_line2 = "WINS"
            self.winner_col   = P2_COLOR
        else:
            self.winner_line1 = "DRAW"
            self.winner_line2 = ""
            self.winner_col   = (255, 255, 255)

    def draw(self):
        self.canvas.delete("all")

        if self.game_over_flag:
            self.canvas.create_rectangle(
                0, 0, BOARD_W * CELL, BOARD_H * CELL,
                fill="black", outline=""
            )
            self.draw_pixel_text(
                self.winner_line1, self.winner_line2,
                self.winner_col
            )
            return

        if self.start_anim:
            t = time.time()
            for y in range(BOARD_H):
                for x in range(BOARD_W):
                    self.led(x, y, self.rainbow(x, y, t))
            return

        if self.wipe:
            t = time.time()
            for y in range(BOARD_H):
                for x in range(BOARD_W):
                    col = (0, 0, 0) if x < self.wipe_x else self.rainbow(x, y, t)
                    self.led(x, y, col)
            for y in range(BOARD_H):
                self.led(self.wipe_x, y, (255, 255, 255))
            return

        if self.round_anim:
            cx = BOARD_W // 2
            cy = BOARD_H // 2
            for y in range(BOARD_H):
                for x in range(BOARD_W):
                    dist = abs(x - cx) + abs(y - cy)
                    col  = (255, 255, 255) if dist < self.wave_radius else (0, 0, 0)
                    self.led(x, y, col)
            self.draw_round_text()
            return

        if self.countdown:
            self.canvas.create_rectangle(
                0, 0, BOARD_W * CELL, BOARD_H * CELL,
                fill="black", outline=""
            )
            key = "GO" if self.countdown_val <= 0 else str(self.countdown_val)
            self.draw_led_text_centered(key)
            return

        self.canvas.create_rectangle(
            0, 0, BOARD_W * CELL, BOARD_H * CELL,
            fill="black", outline=""
        )

        self.canvas.create_rectangle(
            SEP_COL * CELL,       0,
            (SEP_COL + 1) * CELL, BOARD_H * CELL,
            fill="white", outline=""
        )

        for (x, y), (kind, _) in self.tiles.items():
            p    = 0 if x < SEP_COL else 1
            base = P1_COLOR if p == 0 else P2_COLOR

            if kind == "trap":
                col = TRAP
            elif kind == "bonus":
                col = BONUS
            else:
                col = base

            if self.bonus_active[p] and kind != "trap":
                col = (255, 255, 255)

            self.led(x, y, col)

        self.canvas.create_text(
            4, BOARD_H * CELL - 4,
            text=str(self.scores[0]),
            fill=hex3(P1_COLOR),
            anchor="sw",
            font=("Courier", 14, "bold")
        )
        self.canvas.create_text(
            BOARD_W * CELL - 4, BOARD_H * CELL - 4,
            text=str(self.scores[1]),
            fill=hex3(P2_COLOR),
            anchor="se",
            font=("Courier", 14, "bold")
        )

    def loop(self):
        self.spawn()
        self.update()
        self.draw()
        self.root.after(80, self.loop)


Game()
import tkinter as tk
import random
import time
import math
import os
import pygame
import socket
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


BOARD_W = 16
BOARD_H = 32
CELL    = 24

SEP_ROW = 16

P1_ROWS = range(0,  SEP_ROW)       # 0-15
P2_ROWS = range(SEP_ROW + 1, BOARD_H)  # 17-31


P1_COLOR = (180, 0,   255)   # purple
P2_COLOR = (0,   120, 255)   # blue
TRAP     = (255, 0,   0)     # red
BONUS    = (0,   255, 0)     # green
BLACK    = (0,   0,   0)
WHITE    = (255, 255, 255)

UDP_TARGET_IP    = "255.255.255.255"
UDP_SEND_PORT    = 4626
UDP_RECV_PORT    = 7800
BOARD_WIDTH      = 16
BOARD_LENGTH     = 32
NUM_CHANNELS     = 8
LEDS_PER_CHANNEL = 64
FRAME_DATA_LENGTH = NUM_CHANNELS * LEDS_PER_CHANNEL * 3


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
    "R": ["110","101","110","101","101"],
}


def hex3(c):
    return '#%02x%02x%02x' % c


def draw_char_on_grid(grid, char, start_x, start_y, color):
    cols = FONT_3x5.get(char.upper(), FONT_3x5.get(' ', [0, 0, 0]))
    for ci, col_bits in enumerate(cols):
        for ri in range(5):
            if (col_bits >> ri) & 1:
                px = start_x + ci
                py = start_y + ri
                if 0 <= px < BOARD_W and 0 <= py < BOARD_H:
                    grid[(px, py)] = color


def draw_score_on_separator(grid, score_p1, score_p2):
    for x in range(BOARD_W):
        for dy in range(-2, 3): 
            y = SEP_ROW + dy
            if 0 <= y < BOARD_H:
                grid[(x, y)] = WHITE

    s1 = str(score_p1)
    s2 = str(score_p2)
    char_step = 4

    p1_width  = len(s1) * char_step - 1
    p1_start  = max(0, 6 - p1_width)
    for i, ch in enumerate(s1):
        draw_char_on_grid(grid, ch,
                          p1_start + i * char_step,
                          SEP_ROW - 2,
                          P1_COLOR)
    draw_char_on_grid(grid, '-', 7, SEP_ROW - 2, WHITE)

    for i, ch in enumerate(s2):
        draw_char_on_grid(grid, ch,
                          9 + i * char_step,
                          SEP_ROW - 2,
                          P2_COLOR)

#network

class Network:
    def __init__(self, target_ip=UDP_TARGET_IP,
                 send_port=UDP_SEND_PORT, recv_port=UDP_RECV_PORT):
        self.target_ip = target_ip
        self.send_port = send_port
        self.recv_port = recv_port
        self.seq       = 0
        self._lock     = threading.Lock()
        self.triggers  = {}

        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_send.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self.sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_recv.settimeout(0.3)
        self.sock_recv.bind(("0.0.0.0", self.recv_port))

        self._running = True
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def _recv_loop(self):
        while self._running:
            try:
                data, _ = self.sock_recv.recvfrom(2048)
                if len(data) >= 1373 and data[0] == 0x88:
                    with self._lock:
                        for ch in range(8):
                            base = 2 + ch * 171
                            for led in range(64):
                                self.triggers[(ch, led)] = (
                                    data[base + 1 + led] == 0xCC
                                )
            except socket.timeout:
                pass
            except Exception:
                pass

    def is_pressed(self, x, y):
        if not (0 <= x < BOARD_WIDTH and 0 <= y < BOARD_LENGTH):
            return False
        ch  = min(y // 4, NUM_CHANNELS - 1)
        row = y % 4
        led = row * 16 + x if row % 2 == 0 else row * 16 + (15 - x)
        with self._lock:
            return self.triggers.get((ch, led), False)

    def send_frame(self, grid):
        buf = bytearray(FRAME_DATA_LENGTH)
        for (x, y), color in grid.items():
            if not (0 <= x < BOARD_WIDTH and 0 <= y < BOARD_LENGTH):
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

    def _send_packet(self, frame_data):
        self.seq = (self.seq % 0xFFFF) + 1
        ip, port = self.target_ip, self.send_port

        def tx(pkt):
            try: self.sock_send.sendto(pkt, (ip, port))
            except: pass

        def make(inner, cs=0x1E):
            r1, r2 = random.randint(0,127), random.randint(0,127)
            ln = len(inner) - 1
            p  = bytearray([0x75, r1, r2, (ln>>8)&0xFF, ln&0xFF])
            p += bytearray(inner)
            p += bytearray([cs, 0x00])
            return p

        tx(bytearray([0x75,0,0,0,8,2,0,0,0x33,0x44,
                       (self.seq>>8)&0xFF, self.seq&0xFF, 0,0,0,0x0E,0]))
        pay = bytearray()
        for _ in range(NUM_CHANNELS):
            pay += bytes([(LEDS_PER_CHANNEL>>8)&0xFF, LEDS_PER_CHANNEL&0xFF])
        tx(make(bytearray([2,0,0,0x88,0x77,0xFF,0xF0,
                            (len(pay)>>8)&0xFF, len(pay)&0xFF]) + pay))
        for i, idx in enumerate(range(0, len(frame_data), 984), 1):
            chunk = frame_data[idx:idx+984]
            inner = bytearray([2,0,0,0x88,0x77,
                                (i>>8)&0xFF, i&0xFF,
                                (len(chunk)>>8)&0xFF, len(chunk)&0xFF
                                ]) + bytearray(chunk)
            tx(make(inner, 0x1E if len(chunk)==984 else 0x36))
            time.sleep(0.001)
        tx(bytearray([0x75,0,0,0,8,2,0,0,0x55,0x66,
                       (self.seq>>8)&0xFF, self.seq&0xFF, 0,0,0,0x0E,0]))

    def stop(self):
        self._running = False
        try: self.sock_recv.close()
        except: pass


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

        self.net  = Network()
        self.grid = {}

        # tiles: {(x,y): (kind, expire_time)}
        # kind = "target" | "trap" | "bonus"
        self.tiles  = {}
        self.scores = [0, 0]

        self.bonus_active = [False, False]
        self.bonus_time   = [0, 0]

        self.round      = 1
        self.max_rounds = 3
        self.round_time = 20
        self.round_start = time.time()

        # Animation states
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
        self.winner_col     = WHITE

        self.root.bind("<Button-1>", self.on_click)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Music
        try:
            pygame.mixer.init()
            pygame.mixer.music.load(os.path.join(BASE_DIR, "subway-surfers.mp3"))
            pygame.mixer.music.set_volume(0.6)
            pygame.mixer.music.play(-1)
        except Exception as e:
            print(f"[Music] {e}")

        self.loop()
        self.root.mainloop()

    def on_close(self):
        try: self.net.stop()
        except: pass
        self.root.destroy()

    #Sounds

    def sound_trap(self):
        try:
            import winsound
            winsound.PlaySound(
                os.path.join(BASE_DIR, "trap.wav"),
                winsound.SND_FILENAME | winsound.SND_ASYNC
            )
        except: pass

    def sound_bonus(self):
        try:
            import winsound
            winsound.Beep(1200, 80)
        except: pass

    def sound_countdown(self):
        try:
            import winsound
            winsound.PlaySound(
                os.path.join(BASE_DIR, "countdown.wav"),
                winsound.SND_FILENAME | winsound.SND_ASYNC
            )
        except: pass


    def rainbow(self, x, y, t):
        r = int((math.sin(t + x * 0.3) + 1) * 127)
        g = int((math.sin(t + y * 0.3 + 2) + 1) * 127)
        b = int((math.sin(t + x * 0.2 + y * 0.2 + 4) + 1) * 127)
        return (r, g, b)

    def set_pixel(self, x, y, color):
        if not (0 <= x < BOARD_W and 0 <= y < BOARD_H):
            return
        x1 = x * CELL
        y1 = y * CELL
        self.canvas.create_rectangle(
            x1, y1, x1 + CELL, y1 + CELL,
            fill=hex3(color), outline=""
        )
        self.grid[(x, y)] = color

    def draw_led_text_centered(self, key):
        scale   = 2
        pattern = DIGITS[key]
        char_w  = len(pattern[0]) * scale
        char_h  = len(pattern) * scale
        start_x = (BOARD_W - char_w) // 2
        start_y = (BOARD_H - char_h) // 2

        for ri, row in enumerate(pattern):
            for ci, px in enumerate(row):
                if px == "1":
                    lx = start_x + ci * scale
                    ly = start_y + ri * scale
                    for dx in range(scale):
                        for dy in range(scale):
                            self.set_pixel(lx + dx, ly + dy, WHITE)

    def draw_round_text(self):
        scale  = 2
        text   = f"R{self.round}"
        char_w = 3 * scale + scale
        total_w = len(text) * char_w - scale
        start_x = (BOARD_W - total_w) // 2
        start_y = (BOARD_H - 5 * scale) // 2

        for i, ch in enumerate(text):
            pattern = DIGITS.get(ch, DIGITS.get("R", []))
            for ri, row in enumerate(pattern):
                for ci, px in enumerate(row):
                    if px == "1":
                        lx = start_x + i * char_w + ci * scale
                        ly = start_y + ri * scale
                        for dx in range(scale):
                            for dy in range(scale):
                                self.set_pixel(lx + dx, ly + dy, (220, 0, 0))

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
                cols = FONT_3x5.get(ch, FONT_3x5.get(' ', [0, 0, 0]))
                for ci, col_bits in enumerate(cols):
                    for ri in range(char_h):
                        if (col_bits >> ri) & 1:
                            lx = start_x + i * step_x + ci * scale
                            ly = start_y + ri * scale
                            for dx in range(scale):
                                for dy in range(scale):
                                    self.set_pixel(lx + dx, ly + dy, color)

        draw_line(line1, base_y)
        if line2:
            draw_line(line2, base_y + step_y + gap)

    def which_player(self, y):
        if y in P1_ROWS:   return 0
        if y in P2_ROWS:   return 1
        return None   # separator row

    def handle_press(self, x, y):
        if (x, y) not in self.tiles:
            return

        kind, _ = self.tiles[(x, y)]
        p = self.which_player(y)
        if p is None:
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

    def on_click(self, e):
        if self.start_anim or self.wipe or self.countdown or \
           self.round_anim or self.game_over_flag:
            return
        x = e.x // CELL
        y = e.y // CELL
        self.handle_press(x, y)

    def spawn(self):
    
        if self.start_anim or self.wipe or self.countdown or \
           self.round_anim or self.game_over_flag:
            return
        
        spawn_rate = (0.08 + self.round * 0.04) * 2

        for _ in range(2):
            if random.random() < spawn_rate:
                p = random.choice([0, 1])
                rows = list(P1_ROWS) if p == 0 else list(P2_ROWS)

                x = random.randint(0, BOARD_W - 1)
                y = random.choice(rows)

                if (x, y) not in self.tiles:
                    lifetime = max(1.2, 3 - self.round * 0.6)
                    kind = random.choice(["target", "trap", "bonus"])
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
                self.wipe              = False
                self.countdown         = True
                self.countdown_val     = 3
                self.countdown_time    = time.time()
                self.countdown_started = False
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
        if self.countdown:
            if not self.countdown_started:
                self.sound_countdown()
                self.countdown_started = True
            if now - self.countdown_time > 1:
                self.countdown_val  -= 1
                self.countdown_time  = now
            if self.countdown_val < 0:
                self.countdown   = False
                self.round_start = time.time()
            return

        for pos in list(self.tiles.keys()):
            if self.net.is_pressed(pos[0], pos[1]):
                self.handle_press(pos[0], pos[1])
        if now - self.round_start > self.round_time:
            self.next_round()
            return

        for pos in list(self.tiles.keys()):
            if pos in self.tiles and now > self.tiles[pos][1]:
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
            self.winner_col   = WHITE


    def draw(self):
        self.canvas.delete("all")
        self.grid.clear()

        #  Game over
        if self.game_over_flag:
            self.canvas.create_rectangle(
                0, 0, BOARD_W*CELL, BOARD_H*CELL, fill="black", outline=""
            )
            self.draw_pixel_text(
                self.winner_line1, self.winner_line2, self.winner_col
            )
            self.net.send_frame(self.grid)
            return

        # ── Start rainbow
        if self.start_anim:
            t = time.time()
            for y in range(BOARD_H):
                for x in range(BOARD_W):
                    self.set_pixel(x, y, self.rainbow(x, y, t))
            self.net.send_frame(self.grid)
            return

        #Wipe animation
        if self.wipe:
            t = time.time()
            for y in range(BOARD_H):
                for x in range(BOARD_W):
                    col = BLACK if x < self.wipe_x else self.rainbow(x, y, t)
                    self.set_pixel(x, y, col)
            if 0 <= self.wipe_x < BOARD_W:
                for y in range(BOARD_H):
                    self.set_pixel(self.wipe_x, y, WHITE)
            self.net.send_frame(self.grid)
            return

        #  Round wave animation
        if self.round_anim:
            cx, cy = BOARD_W // 2, BOARD_H // 2
            for y in range(BOARD_H):
                for x in range(BOARD_W):
                    dist = abs(x - cx) + abs(y - cy)
                    self.set_pixel(x, y, WHITE if dist < self.wave_radius else BLACK)
            self.draw_round_text()
            self.net.send_frame(self.grid)
            return

        # Countdown
        if self.countdown:
            self.canvas.create_rectangle(
                0, 0, BOARD_W*CELL, BOARD_H*CELL, fill="black", outline=""
            )
            key = "GO" if self.countdown_val <= 0 else str(self.countdown_val)
            self.draw_led_text_centered(key)
            self.net.send_frame(self.grid)
            return

       
        self.canvas.create_rectangle(
            0, 0, BOARD_W*CELL, BOARD_H*CELL, fill="black", outline=""
        )

        for y in range(BOARD_H):
            for x in range(BOARD_W):
                self.grid[(x, y)] = BLACK

        for (x, y), (kind, _) in self.tiles.items():
            p    = self.which_player(y)
            if p is None:
                continue
            base = P1_COLOR if p == 0 else P2_COLOR

            if kind == "trap":
                col = TRAP
            elif kind == "bonus":
                col = BONUS
            else:
                col = base

            
            if self.bonus_active[p] and kind != "trap":
                col = WHITE

            self.set_pixel(x, y, col)

        draw_score_on_separator(self.grid, self.scores[0], self.scores[1])

        for (x, y), color in self.grid.items():
            x1 = x * CELL
            y1 = y * CELL
            self.canvas.create_rectangle(
                x1, y1, x1+CELL, y1+CELL,
                fill=hex3(color), outline=""
            )

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

        self.net.send_frame(self.grid)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def loop(self):
        self.spawn()
        self.update()
        self.draw()
        self.root.after(80, self.loop)


Game()
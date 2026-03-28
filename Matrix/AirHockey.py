import socket
import threading
import time
import random
import math

# Constante
BOARD_LENGTH      = 32
BOARD_WIDTH       = 16
NUM_CHANNELS      = 8
LEDS_PER_CHANNEL  = 64
FRAME_DATA_LENGTH = NUM_CHANNELS * LEDS_PER_CHANNEL * 3

UDP_TARGET_IP = "255.255.255.255"
UDP_SEND_PORT = 2000
UDP_RECV_PORT = 2001

# Culori
BLACK    = (0,   0,   0)
WHITE    = (255, 255, 255)
RED      = (200, 0,   0)
YELLOW   = (255, 255, 0)
DARK_RED = (60,  0,   0)
PURPLE   = (178, 102, 255)
ORANGE   = (255, 130, 0)
GRAY     = (25,  25,  25)
BORDER   = (15,  15,  50)

# ─── Layout ───────────────────────────────────────────────────────────────────
#  R0        : top border (blank)
#  R1-R3     : arrow control zone E2
#  R4        : goal E2   (cols 5-8)
#  R5-R6     : empty
#  R7        : paddle E2
#  R8-R25    : play field
#  R16       : center (puck start)
#  R26       : paddle E1
#  R27-R28   : empty
#  R29       : goal E1   (cols 5-8)
#  R30-R32   : arrow control zone E1
#  R32       : bottom border (blank)

ARROW_ROWS_E2 = [0, 1, 2]
GOAL_ROW_E2   = 3
PADDLE_ROW_E2 = 6

PADDLE_ROW_E1 = 25
GOAL_ROW_E1   = 28
ARROW_ROWS_E1 = [29, 30, 31]

GOAL_COLS = [6, 7, 8, 9]

PLAY_TOP    = PADDLE_ROW_E2 + 1
PLAY_BOTTOM = PADDLE_ROW_E1 - 1
CENTER_X    = BOARD_WIDTH // 2
CENTER_Y    = (PLAY_TOP + PLAY_BOTTOM) // 2

# Control columns
CTRL_LEFT_COLS  = [1, 2, 3, 4]
CTRL_RIGHT_COLS = [11, 12, 13, 14]

# Arrow pixel patterns (3 rows x 4 cols)
# ARROW_R points RIGHT →
ARROW_R = [
    [0, 0, 1, 0],
    [1, 1, 1, 1],
    [0, 0, 1, 0],
]
# ARROW_L points LEFT ←
ARROW_L = [
    [0, 1, 0, 0],
    [1, 1, 1, 1],
    [0, 1, 0, 0],
]

ARROW_LEFT_START  = 1   # cols 1-4
ARROW_RIGHT_START = 11  # cols 11-14

FONT_5x7 = {
    '0': [62, 65, 65,  65, 62],
    '1': [0,  66, 127, 64, 0],
    '2': [98, 81, 73,  69, 70],
    '3': [34, 65, 73,  73, 54],
    'G': [62, 65, 73,  73, 46],
    'O': [62, 65, 65,  65, 62],
    '!': [0,  0,  95,  0,  0]
}

def draw_char(g, char, x, y, color):
    for ci, col_byte in enumerate(FONT_5x7.get(char, [0,0,0,0,0])):
        for ri in range(7):
            if (col_byte >> ri) & 1:
                px, py = int(x + ci), int(y + ri)
                if 0 <= px < BOARD_WIDTH and 0 <= py < BOARD_LENGTH:
                    g[(px, py)] = color

# ─── Network ──────────────────────────────────────────────────────────────────
class Network:
    def __init__(self, target_ip=UDP_TARGET_IP,
                 send_port=UDP_SEND_PORT, recv_port=UDP_RECV_PORT):
        self.target_ip = target_ip
        self.send_port = send_port
        self.recv_port = recv_port
        self.seq       = 0
        self._lock     = threading.Lock()
        self.triggers  = {}  # (ch, led) -> bool

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
                                self.triggers[(ch, led)] = (data[base+1+led] == 0xCC)
            except socket.timeout:
                pass
            except Exception:
                pass

    def is_pressed(self, x, y):
        if not (0 <= x < BOARD_WIDTH and 0 <= y < BOARD_LENGTH):
            return False
        ch        = min(y // 4, NUM_CHANNELS - 1)
        row_in_ch = y % 4
        led       = row_in_ch*16 + x if row_in_ch%2==0 else row_in_ch*16 + (15-x)
        with self._lock:
            return self.triggers.get((ch, led), False)

    def send_frame(self, grid):
        buf = bytearray(FRAME_DATA_LENGTH)
        for (x, y), color in grid.items():
            if not (0 <= x < BOARD_WIDTH and 0 <= y < BOARD_LENGTH):
                continue
            ch        = min(y // 4, NUM_CHANNELS - 1)
            row_in_ch = y % 4
            led       = row_in_ch*16 + x if row_in_ch%2==0 else row_in_ch*16 + (15-x)
            block     = NUM_CHANNELS * 3
            offset    = led * block + ch
            if offset + NUM_CHANNELS*2 < len(buf):
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
            p = bytearray([0x75, r1, r2, (ln>>8)&0xFF, ln&0xFF]) + bytearray(inner)
            p += bytearray([cs, 0x00])
            return p

        # Start
        tx(bytearray([0x75,0,0,0,8,2,0,0,0x33,0x44,
                       (self.seq>>8)&0xFF,self.seq&0xFF,0,0,0,0x0E,0]))
        # FFF0
        pay = bytearray()
        for _ in range(NUM_CHANNELS):
            pay += bytes([(LEDS_PER_CHANNEL>>8)&0xFF, LEDS_PER_CHANNEL&0xFF])
        tx(make(bytearray([2,0,0,0x88,0x77,0xFF,0xF0,
                            (len(pay)>>8)&0xFF,len(pay)&0xFF]) + pay))
        # Data
        for i, idx in enumerate(range(0, len(frame_data), 984), 1):
            chunk = frame_data[idx:idx+984]
            inner = bytearray([2,0,0,0x88,0x77,
                                (i>>8)&0xFF,i&0xFF,
                                (len(chunk)>>8)&0xFF,len(chunk)&0xFF]) + bytearray(chunk)
            tx(make(inner, 0x1E if len(chunk)==984 else 0x36))
            time.sleep(0.001)
        # End
        tx(bytearray([0x75,0,0,0,8,2,0,0,0x55,0x66,
                       (self.seq>>8)&0xFF,self.seq&0xFF,0,0,0,0x0E,0]))

    def stop(self):
        self._running = False

# ─── Game ─────────────────────────────────────────────────────────────────────
class HockeyGame:
    def __init__(self, net: Network, config: dict):
        self.net    = net
        self.config = config

        self.max_goals  = config.get("max_goals", 5)
        speeds          = {"slow": 4.0, "normal": 6.5, "fast": 10.0}
        self.base_speed = speeds.get(config.get("speed", "normal"), 6.5)
        self.paddle_len = config.get("paddle_len", 4)
        self.paddle_spd = 9.0

        self.score      = [0, 0]   # [E1, E2]
        self.state      = "countdown"
        self.anim_timer = 0.0
        self.goal_team  = 0
        self.countdown_n= 3

        self.paddle_e1  = float(CENTER_X)
        self.paddle_e2  = float(CENTER_X)
        self.ball_speed = self.base_speed
        self.reset_ball(towards=1)

        self.grid = {(x, y): BLACK
                     for y in range(BOARD_LENGTH)
                     for x in range(BOARD_WIDTH)}

    def reset_ball(self, towards=1):
        self.ball_x = float(CENTER_X)
        self.ball_y = float(CENTER_Y)
        angle = random.uniform(25, 65)
        rad   = math.radians(angle)
        sx    = math.cos(rad) * (1 if random.random() > 0.5 else -1)
        sy    = math.sin(rad) * towards
        mag   = math.sqrt(sx*sx + sy*sy)
        self.ball_dx = sx / mag
        self.ball_dy = sy / mag

    # ── Input ─────────────────────────────────────────────────────────────────
    def _pressed_left(self, rows):
        return any(self.net.is_pressed(x, r) for r in rows for x in CTRL_LEFT_COLS)

    def _pressed_right(self, rows):
        return any(self.net.is_pressed(x, r) for r in rows for x in CTRL_RIGHT_COLS)

    def _dir_e1(self):
        # E1 bottom, faces UP: left zone = ← = -1, right zone = → = +1
        l = self._pressed_left(ARROW_ROWS_E1)
        r = self._pressed_right(ARROW_ROWS_E1)
        if l and not r: return -1
        if r and not l: return  1
        return 0

    def _dir_e2(self):
        l = self._pressed_left(ARROW_ROWS_E2)
        r = self._pressed_right(ARROW_ROWS_E2)
        if l and not r: return -1  # Acum săgeata stânga mută paleta la stânga
        if r and not l: return 1  # Acum săgeata dreapta mută paleta la dreapta
        return 0

    def _clamp_paddle(self, cx):
        half = self.paddle_len / 2.0
        return max(1 + half, min(14 - half, cx))

    def _paddle_tiles(self, cx):
        start = int(round(cx - self.paddle_len / 2.0))
        return [max(1, min(14, start + i)) for i in range(self.paddle_len)]

    # ── Update ────────────────────────────────────────────────────────────────
    def update(self, dt):
        if   self.state == "playing":   self._update_playing(dt)
        elif self.state == "goal_anim": self._update_goal_anim(dt)
        elif self.state == "countdown": self._update_countdown(dt)
        elif self.state == "victory":   self.anim_timer += dt

    def _update_playing(self, dt):
        # Paddles
        self.paddle_e1 = self._clamp_paddle(self.paddle_e1 + self._dir_e1() * self.paddle_spd * dt)
        self.paddle_e2 = self._clamp_paddle(self.paddle_e2 + self._dir_e2() * self.paddle_spd * dt)

        # Ball
        nx = self.ball_x + self.ball_dx * self.ball_speed * dt
        ny = self.ball_y + self.ball_dy * self.ball_speed * dt

        # Side walls (play area cols 1-14)
        if nx <= 1.0:
            nx = 1.0; self.ball_dx = abs(self.ball_dx)
        elif nx >= 14.0:
            nx = 14.0; self.ball_dx = -abs(self.ball_dx)

        # Paddle E2 (ball going UP)
        if self.ball_dy < 0 and ny <= PADDLE_ROW_E2 and self.ball_y > PADDLE_ROW_E2:
            if int(round(nx)) in self._paddle_tiles(self.paddle_e2):
                ny = PADDLE_ROW_E2 + 0.5
                self.ball_dy = abs(self.ball_dy)
                self._deflect(nx, self.paddle_e2)
                self.ball_speed = min(self.ball_speed * 1.05, self.base_speed * 2.0)

        # Paddle E1 (ball going DOWN)
        if self.ball_dy > 0 and ny >= PADDLE_ROW_E1 and self.ball_y < PADDLE_ROW_E1:
            if int(round(nx)) in self._paddle_tiles(self.paddle_e1):
                ny = PADDLE_ROW_E1 - 0.5
                self.ball_dy = -abs(self.ball_dy)
                self._deflect(nx, self.paddle_e1)
                self.ball_speed = min(self.ball_speed * 1.05, self.base_speed * 2.0)

        # Goal E2 (ball going UP past goal row)
        if ny <= GOAL_ROW_E2:
            if int(round(nx)) in GOAL_COLS:
                self._score(scorer=0); return
            else:
                ny = float(GOAL_ROW_E2) + 0.5
                self.ball_dy = abs(self.ball_dy)

        # Goal E1 (ball going DOWN past goal row)
        if ny >= GOAL_ROW_E1:
            if int(round(nx)) in GOAL_COLS:
                self._score(scorer=1); return
            else:
                ny = float(GOAL_ROW_E1) - 0.5
                self.ball_dy = -abs(self.ball_dy)

        self.ball_x, self.ball_y = nx, ny

    def _deflect(self, ball_x, paddle_cx):
        rel = max(-1.0, min(1.0, (ball_x - paddle_cx) / (self.paddle_len / 2.0)))
        self.ball_dx = rel * 0.9
        sign = 1 if self.ball_dy > 0 else -1
        mag  = math.sqrt(self.ball_dx**2 + 1.0)
        self.ball_dx /= mag
        self.ball_dy  = sign / mag

    def _score(self, scorer):
        self.score[scorer] += 1
        self.goal_team  = scorer
        self.state      = "goal_anim"
        self.anim_timer = 0.0

    def _update_goal_anim(self, dt):
        self.anim_timer += dt
        if self.anim_timer >= 2.5:
            if max(self.score) >= self.max_goals:
                self.state = "victory"; self.anim_timer = 0.0
            else:
                self._start_countdown()

    def _start_countdown(self):
        towards = -1 if self.goal_team == 0 else 1
        self.reset_ball(towards=towards)
        self.ball_speed = self.base_speed
        self.state      = "countdown"
        self.anim_timer = 0.0

    def _update_countdown(self, dt):
        self.anim_timer  += dt
        self.countdown_n  = max(0, 3 - int(self.anim_timer))
        if self.anim_timer >= 4.0:
            self.state = "playing"

    def is_victory(self):
        return self.state == "victory" and self.anim_timer > 6.0

    # ── Render ────────────────────────────────────────────────────────────────
    def render(self):
        g = self.grid
        for k in g: g[k] = BLACK

        if   self.state == "goal_anim": self._render_goal_flash(g)
        elif self.state == "countdown": self._render_field(g); self._render_countdown(g)
        elif self.state == "victory":   self._render_victory(g)
        else:                           self._render_field(g)

        self.net.send_frame(g)

    def _render_field(self, g):
        # Borders col 0 and 15
        for y in range(BOARD_LENGTH):
            g[(0,  y)] = BORDER
            g[(15, y)] = BORDER

        # Goals
        dim_orange = (ORANGE[0] // 3, ORANGE[1] // 3, ORANGE[2] // 3)
        dim_purple = (PURPLE[0] // 3, PURPLE[1] // 3, PURPLE[2] // 3)
        draw_char(g, str(self.score[1]), 5, 8, dim_orange)
        draw_char(g, str(self.score[0]), 5, 17, dim_purple)

        for x in range(BOARD_WIDTH):
            g[(x, GOAL_ROW_E2)] = RED   if x in GOAL_COLS else DARK_RED
            g[(x, GOAL_ROW_E1)] = RED   if x in GOAL_COLS else DARK_RED

        # Arrows E2 (rows 1-3)
        # Left zone (cols 1-4) = arrow RIGHT (s_dr) → moving paddle right
        # Right zone (cols 11-14) = arrow LEFT (s_st) → moving paddle left
        lp2 = self._pressed_left(ARROW_ROWS_E2)
        rp2 = self._pressed_right(ARROW_ROWS_E2)
        cl2 = WHITE if lp2 else ORANGE
        cr2 = WHITE if rp2 else ORANGE
        for ri, row in enumerate(ARROW_ROWS_E2):
            for ci in range(4):
                # Am inversat ARROW_R cu ARROW_L
                g[(ARROW_LEFT_START + ci, row)] = cl2 if ARROW_L[ri][ci] else BLACK
                g[(ARROW_RIGHT_START + ci, row)] = cr2 if ARROW_R[ri][ci] else BLACK

        # Arrows E1 (rows 30-32)
        # Left zone (cols 1-4) = arrow LEFT (s_st) → moving paddle left
        # Right zone (cols 11-14) = arrow RIGHT (s_dr) → moving paddle right
        lp1 = self._pressed_left(ARROW_ROWS_E1)
        rp1 = self._pressed_right(ARROW_ROWS_E1)
        cl1 = WHITE if lp1 else PURPLE
        cr1 = WHITE if rp1 else PURPLE
        for ri, row in enumerate(ARROW_ROWS_E1):
            for ci in range(4):
                g[(ARROW_LEFT_START  + ci, row)] = cl1 if ARROW_L[ri][ci] else BLACK
                g[(ARROW_RIGHT_START + ci, row)] = cr1 if ARROW_R[ri][ci] else BLACK

        # Paddles
        for x in self._paddle_tiles(self.paddle_e2):
            g[(x, PADDLE_ROW_E2)] = ORANGE
        for x in self._paddle_tiles(self.paddle_e1):
            g[(x, PADDLE_ROW_E1)] = PURPLE

        # Center dot
        g[(CENTER_X, CENTER_Y)] = GRAY

        # Puck
        bx = int(round(self.ball_x))
        by = int(round(self.ball_y))
        if 0 <= bx < BOARD_WIDTH and 0 <= by < BOARD_LENGTH:
            g[(bx, by)] = WHITE

        # Score dots
        #for i in range(self.score[1]):   # E2 top, row 0
        #    if i < 5: g[(3 + i*2, 0)] = ORANGE
        #for i in range(self.score[0]):   # E1 bottom, row 31
        #    if i < 5: g[(3 + i*2, 31)] = PURPLE

    def _render_goal_flash(self, g):
        col = PURPLE if self.goal_team == 0 else ORANGE
        flash = (int(self.anim_timer * 6) % 2 == 0)
        fill  = col if flash else BLACK
        for k in g: g[k] = fill
        #for i in range(self.score[1]):
        #    if i < 5: g[(3+i*2, 0)]  = ORANGE
        #for i in range(self.score[0]):
        #    if i < 5: g[(3+i*2, 31)] = PURPLE
        draw_char(g, str(self.score[1]), 5, 8, ORANGE)
        draw_char(g, str(self.score[0]), 5, 17, PURPLE)

    def _render_countdown(self, g):
        n = self.countdown_n
        if n > 0:
            draw_char(g, str(n), CENTER_X - 2, CENTER_Y - 3, WHITE)
        elif n == 0:
            y_pos = CENTER_Y - 3  # Aceeași poziție pe verticală

            draw_char(g, 'G', 0, y_pos, WHITE)
            draw_char(g, 'O', 5, y_pos, WHITE)

            # Notă: Deși blocul '!' începe la X=10,
            # semnul vizual va apărea la X=12 datorită datelor din font.
            draw_char(g, '!', 10, y_pos, WHITE)

    def _render_victory(self, g):
        col   = PURPLE if self.score[0] > self.score[1] else ORANGE
        flash = (int(self.anim_timer * 4) % 2 == 0)
        for k in g: g[k] = col if flash else BLACK
        #for i in range(self.score[1]):
        #    if i < 5: g[(3+i*2, 0)]  = ORANGE
        #for i in range(self.score[0]):
        #    if i < 5: g[(3+i*2, 31)] = PURPLE
        draw_char(g, str(self.score[1]), 5, 8, ORANGE)
        draw_char(g, str(self.score[0]), 5, 17, PURPLE)

# ─── Config UI ────────────────────────────────────────────────────────────────
def run_config_ui():
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return {"max_goals":5,"speed":"normal","paddle_len":4,
                "team1_name":"Team 1","team2_name":"Team 2"}

    result = {}
    root = tk.Tk()
    root.title("🏒 Floor Hockey — Setup")
    root.configure(bg="#111")
    root.resizable(False, False)

    def lbl(text, row):
        tk.Label(root, text=text, bg="#111", fg="#aaa",
                 font=("Consolas", 11)).grid(row=row, column=0,
                 sticky="w", padx=20, pady=8)

    lbl("Goals to win:", 0)
    goals_v = tk.StringVar(value="5")
    ttk.Combobox(root, textvariable=goals_v, values=["3","5","7"],
                 width=10, state="readonly").grid(row=0, column=1, padx=10)

    lbl("Ball speed:", 1)
    speed_v = tk.StringVar(value="normal")
    ttk.Combobox(root, textvariable=speed_v, values=["slow","normal","fast"],
                 width=10, state="readonly").grid(row=1, column=1, padx=10)

    lbl("Paddle length:", 2)
    paddle_v = tk.StringVar(value="4")
    ttk.Combobox(root, textvariable=paddle_v, values=["3","4","5"],
                 width=10, state="readonly").grid(row=2, column=1, padx=10)

    lbl("Team 1 (bottom / PURPLE):", 3)
    t1_v = tk.StringVar(value="Team 1")
    tk.Entry(root, textvariable=t1_v, bg="#222", fg="white",
             font=("Consolas", 10), insertbackground="white"
             ).grid(row=3, column=1, padx=10)

    lbl("Team 2 (top / orange):", 4)
    t2_v = tk.StringVar(value="Team 2")
    tk.Entry(root, textvariable=t2_v, bg="#222", fg="white",
             font=("Consolas", 10), insertbackground="white"
             ).grid(row=4, column=1, padx=10)

    cancelled = [False]

    def start():
        result.update({
            "max_goals":  int(goals_v.get()),
            "speed":      speed_v.get(),
            "paddle_len": int(paddle_v.get()),
            "team1_name": t1_v.get() or "Team 1",
            "team2_name": t2_v.get() or "Team 2",
        })
        root.destroy()

    def cancel():
        cancelled[0] = True; root.destroy()

    bf = tk.Frame(root, bg="#111")
    bf.grid(row=5, column=0, columnspan=2, pady=15)
    tk.Button(bf, text="▶  START GAME", command=start,
              bg="#224488", fg="white", font=("Consolas", 12, "bold"),
              relief="flat", padx=20, pady=8).pack(side=tk.LEFT, padx=8)
    tk.Button(bf, text="Cancel", command=cancel,
              bg="#333", fg="white", font=("Consolas", 10),
              relief="flat", padx=10, pady=8).pack(side=tk.LEFT, padx=8)

    root.mainloop()
    return None if (cancelled[0] or not result) else result

# ─── Second monitor ───────────────────────────────────────────────────────────
def run_display(game_ref, config):
    try:
        import pygame
    except ImportError:
        print("pygame not available — skipping second monitor."); return

    pygame.init()
    screen = pygame.display.set_mode((800, 480))
    pygame.display.set_caption("🏒 Floor Hockey")
    clock  = pygame.time.Clock()

    FH = pygame.font.SysFont("Arial", 160, bold=True)
    FL = pygame.font.SysFont("Arial", 64,  bold=True)
    FM = pygame.font.SysFont("Arial", 36)
    FS = pygame.font.SysFont("Arial", 24)

    T1  = config.get("team1_name", "Team 1")
    T2  = config.get("team2_name", "Team 2")
    C1  = (0, 200, 255)
    C2  = (255, 140, 0)
    BG  = (12, 12, 22)
    WH  = (255, 255, 255)

    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT: pygame.quit(); return
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                pygame.quit(); return

        game = game_ref[0]
        if not game: clock.tick(30); continue

        screen.fill(BG)
        W, H = screen.get_size()
        s0, s1 = game.score

        if game.state == "victory":
            wc    = C1 if s0 > s1 else C2
            wname = T1 if s0 > s1 else T2
            flash = (int(game.anim_timer * 3) % 2 == 0)
            screen.fill(wc if flash else BG)
            screen.blit(t := FL.render(f"🏆  {wname} WINS!", True, WH),
                        t.get_rect(center=(W//2, H//2 - 50)))
            screen.blit(t := FM.render(f"{s0}  —  {s1}", True, (200,200,200)),
                        t.get_rect(center=(W//2, H//2 + 40)))

        elif game.state == "goal_anim":
            sc    = C1 if game.goal_team == 0 else C2
            sname = T1 if game.goal_team == 0 else T2
            flash = (int(game.anim_timer * 6) % 2 == 0)
            screen.fill(sc if flash else BG)
            screen.blit(t := FL.render("⚽  GOAL!", True, WH),
                        t.get_rect(center=(W//2, H//2 - 60)))
            screen.blit(t := FM.render(sname, True, sc if not flash else WH),
                        t.get_rect(center=(W//2, H//2 + 20)))
            screen.blit(t := FS.render(f"{s0}  —  {s1}", True, (180,180,180)),
                        t.get_rect(center=(W//2, H//2 + 75)))

        elif game.state == "countdown":
            n = game.countdown_n
            screen.blit(t := FH.render(str(s0), True, C1), t.get_rect(center=(W//2-150, H//2+20)))
            screen.blit(t := FH.render("—",     True, (50,50,50)), t.get_rect(center=(W//2, H//2+20)))
            screen.blit(t := FH.render(str(s1), True, C2), t.get_rect(center=(W//2+150, H//2+20)))
            if n > 0:
                screen.blit(t := FL.render(str(n), True, WH), t.get_rect(center=(W//2, 80)))

        else:  # playing
            screen.blit(t := FM.render(T1, True, C1), t.get_rect(midleft=(40, H//2-60)))
            screen.blit(t := FM.render(T2, True, C2), t.get_rect(midright=(W-40, H//2-60)))
            screen.blit(t := FH.render(str(s0), True, C1), t.get_rect(center=(W//2-150, H//2+20)))
            screen.blit(t := FH.render("—",     True, (50,50,50)), t.get_rect(center=(W//2, H//2+20)))
            screen.blit(t := FH.render(str(s1), True, C2), t.get_rect(center=(W//2+150, H//2+20)))
            screen.blit(t := FS.render(f"First to {game.max_goals} goals", True, (60,60,60)),
                        t.get_rect(center=(W//2, H-30)))

        pygame.display.flip()
        clock.tick(30)

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("🏒 Floor Hockey — Matrix Edition")
    config = run_config_ui()
    if not config:
        print("Cancelled."); return

    net      = Network()
    game     = HockeyGame(net, config)
    game_ref = [game]

    threading.Thread(target=run_display, args=(game_ref, config), daemon=True).start()
    print("Running! Ctrl+C to quit.")

    dt = 1.0 / 20
    try:
        while True:
            t0 = time.time()
            game.update(dt)
            game.render()
            if game.is_victory():
                print(f"Game over! {game.score}")
                time.sleep(2)
                game = HockeyGame(net, config)
                game_ref[0] = game
            time.sleep(max(0, dt - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nQuit.")
        net.stop()

if __name__ == "__main__":
    main()

"""
Evil Eye Game — Python implementation
Two phases:
  Phase 1: Collect blue buttons (+2s), avoid red buttons (-3s) — 60s timer
  Phase 2: Follow sequence on your walls before time runs out
"""

import socket
import threading
import time
import random
import math
import queue
import struct

# ─── Protocol Constants ───────────────────────────────────────────────────────
UDP_DEVICE_IP     = "255.255.255.255"
UDP_DEVICE_PORT   = 4626
UDP_RECV_PORT     = 7800

NUM_CHANNELS      = 4
LEDS_PER_CHANNEL  = 11          # 0 = Eye, 1-10 = Buttons
FRAME_DATA_LEN    = LEDS_PER_CHANNEL * NUM_CHANNELS * 3   # 132 bytes

PASSWORD_ARRAY = [
    35, 63, 187, 69, 107, 178, 92, 76, 39, 69, 205, 37, 223, 255, 165, 231,
    16, 220, 99, 61, 25, 203, 203, 155, 107, 30, 92, 144, 218, 194, 226, 88,
    196, 190, 67, 195, 159, 185, 209, 24, 163, 65, 25, 172, 126, 63, 224, 61,
    160, 80, 125, 91, 239, 144, 25, 141, 183, 204, 171, 188, 255, 162, 104, 225,
    186, 91, 232, 3, 100, 208, 49, 211, 37, 192, 20, 99, 27, 92, 147, 152,
    86, 177, 53, 153, 94, 177, 200, 33, 175, 195, 15, 228, 247, 18, 244, 150,
    165, 229, 212, 96, 84, 200, 168, 191, 38, 112, 171, 116, 121, 186, 147, 203,
    30, 118, 115, 159, 238, 139, 60, 57, 235, 213, 159, 198, 160, 50, 97, 201,
    253, 242, 240, 77, 102, 12, 183, 235, 243, 247, 75, 90, 13, 236, 56, 133,
    150, 128, 138, 190, 140, 13, 213, 18, 7, 117, 255, 45, 69, 214, 179, 50,
    28, 66, 123, 239, 190, 73, 142, 218, 253, 5, 212, 174, 152, 75, 226, 226,
    172, 78, 35, 93, 250, 238, 19, 32, 247, 223, 89, 123, 86, 138, 150, 146,
    214, 192, 93, 152, 156, 211, 67, 51, 195, 165, 66, 10, 10, 31, 1, 198,
    234, 135, 34, 128, 208, 200, 213, 169, 238, 74, 221, 208, 104, 170, 166, 36,
    76, 177, 196, 3, 141, 167, 127, 56, 177, 203, 45, 107, 46, 82, 217, 139,
    168, 45, 198, 6, 43, 11, 57, 88, 182, 84, 189, 29, 35, 143, 138, 171,
]

# ─── Colors (R, G, B) ─────────────────────────────────────────────────────────
OFF      = (0,   0,   0)
BLUE     = (0,   0,   255)
RED      = (255, 0,   0)
PURPLE   = (128, 0,   200)
YELLOW   = (255, 200, 0)
GREEN    = (0,   255, 0)
WHITE    = (255, 255, 255)
ORANGE   = (255, 100, 0)
DIM_BLUE = (0,   0,   60)
DIM_RED  = (60,  0,   0)
CYAN     = (0,   200, 255)

# ─── Game Config ──────────────────────────────────────────────────────────────
PHASE1_DURATION  = 60.0   # seconds
TIME_BLUE        = +2.0   # seconds added per blue button
TIME_RED         = -3.0   # seconds removed per red button
MIN_PHASE2_TIME  = 10.0   # minimum guaranteed phase 2 time
BASE_PHASE2_TIME = 30.0   # base time before phase 1 adjustments

# Sequence colors and their meaning
SEQ_COLORS = {
    "start":  BLUE,     # first button in sequence
    "normal": PURPLE,   # press and move to next
    "skip":   YELLOW,   # skip next, press the one after
    "finish": GREEN,    # last button — finish!
}

# Wall assignments per player count
WALL_ASSIGNMENTS = {
    2: {1: [1, 2], 2: [3, 4]},
    4: {1: [1],    2: [2],    3: [3],    4: [4]},
}

# ─── Protocol Helpers ─────────────────────────────────────────────────────────
def calc_checksum(data: bytes | bytearray) -> int:
    idx = sum(data) & 0xFF
    return PASSWORD_ARRAY[idx] if idx < len(PASSWORD_ARRAY) else 0


def build_frame_data(led_states: dict) -> bytes:
    """led_states: {(ch 1-4, led 0-10): (r,g,b)}"""
    frame = bytearray(FRAME_DATA_LEN)
    for (ch, led), (r, g, b) in led_states.items():
        idx = ch - 1
        if 0 <= idx < NUM_CHANNELS and 0 <= led < LEDS_PER_CHANNEL:
            frame[led * 12 + idx]     = g
            frame[led * 12 + 4 + idx] = r
            frame[led * 12 + 8 + idx] = b
    return bytes(frame)


def build_start_packet(seq: int) -> bytes:
    pkt = bytearray([0x75, random.randint(0,127), random.randint(0,127),
                     0x00, 0x08, 0x02, 0x00, 0x00, 0x33, 0x44,
                     (seq>>8)&0xFF, seq&0xFF, 0x00, 0x00])
    pkt.append(calc_checksum(pkt))
    return bytes(pkt)


def build_end_packet(seq: int) -> bytes:
    pkt = bytearray([0x75, random.randint(0,127), random.randint(0,127),
                     0x00, 0x08, 0x02, 0x00, 0x00, 0x55, 0x66,
                     (seq>>8)&0xFF, seq&0xFF, 0x00, 0x00])
    pkt.append(calc_checksum(pkt))
    return bytes(pkt)


def build_fff0_packet(seq: int) -> bytes:
    payload = bytearray()
    for _ in range(NUM_CHANNELS):
        payload += bytes([(LEDS_PER_CHANNEL>>8)&0xFF, LEDS_PER_CHANNEL&0xFF])
    inner = bytes([0x02, 0x00, 0x00, 0x88, 0x77, 0xFF, 0xF0,
                   (len(payload)>>8)&0xFF, len(payload)&0xFF]) + payload
    hdr = bytearray([0x75, random.randint(0,127), random.randint(0,127),
                     (len(inner)>>8)&0xFF, len(inner)&0xFF])
    pkt = bytearray(hdr + inner)
    pkt[10] = (seq>>8)&0xFF
    pkt[11] = seq&0xFF
    pkt.append(calc_checksum(pkt))
    return bytes(pkt)


def build_data_packet(frame_data: bytes, seq: int) -> bytes:
    inner = bytes([0x02, 0x00, 0x00, 0x88, 0x77, 0x00, 0x00,
                   (len(frame_data)>>8)&0xFF, len(frame_data)&0xFF]) + frame_data
    hdr = bytearray([0x75, random.randint(0,127), random.randint(0,127),
                     (len(inner)>>8)&0xFF, len(inner)&0xFF])
    pkt = bytearray(hdr + inner)
    pkt[10] = (seq>>8)&0xFF
    pkt[11] = seq&0xFF
    pkt.append(calc_checksum(pkt))
    return bytes(pkt)


# ─── Network Layer ────────────────────────────────────────────────────────────
class EvilEyeNetwork:
    def __init__(self, device_ip=UDP_DEVICE_IP,
                 send_port=UDP_DEVICE_PORT, recv_port=UDP_RECV_PORT):
        self.device_ip  = device_ip
        self.send_port  = send_port
        self.recv_port  = recv_port
        self._seq       = 0
        self._lock      = threading.Lock()

        # Button states: (ch 1-4, led 0-10) -> bool
        self.button_states  = {}
        self._prev_states   = {}
        self.on_button_change = None   # callback(ch, led, pressed)

        # Sender queue
        self._send_q    = queue.Queue(maxsize=4)
        self._running   = True

        # Sockets
        self._sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock_send.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self._sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock_recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock_recv.settimeout(0.5)
        self._sock_recv.bind(("0.0.0.0", self.recv_port))

        threading.Thread(target=self._sender_loop, daemon=True).start()
        threading.Thread(target=self._recv_loop,   daemon=True).start()

    def _next_seq(self):
        with self._lock:
            self._seq = (self._seq + 1) & 0xFFFF
            return self._seq

    def send_leds(self, led_states: dict):
        """Queue a full LED frame."""
        frame = build_frame_data(led_states)
        try:
            self._send_q.put_nowait(frame)
        except queue.Full:
            pass

    def _sender_loop(self):
        while self._running:
            try:
                frame = self._send_q.get(timeout=0.5)
            except queue.Empty:
                continue
            seq = self._next_seq()
            ep  = (self.device_ip, self.send_port)
            try:
                self._sock_send.sendto(build_start_packet(seq), ep)
                time.sleep(0.008)
                self._sock_send.sendto(build_fff0_packet(seq), ep)
                time.sleep(0.008)
                self._sock_send.sendto(build_data_packet(frame, seq), ep)
                time.sleep(0.008)
                self._sock_send.sendto(build_end_packet(seq), ep)
            except Exception as e:
                print(f"[NET] Send error: {e}")
            self._send_q.task_done()

    def _recv_loop(self):
        while self._running:
            try:
                data, _ = self._sock_recv.recvfrom(1024)
            except socket.timeout:
                continue
            except Exception:
                break

            if len(data) != 687 or data[0] != 0x88:
                continue

            new_states = {}
            for ch in range(1, NUM_CHANNELS + 1):
                base = 2 + (ch - 1) * 171
                for idx in range(LEDS_PER_CHANNEL):
                    val = data[base + 1 + idx]
                    new_states[(ch, idx)] = (val == 0xCC)

            # Fire callbacks only on changes
            for key, pressed in new_states.items():
                if self._prev_states.get(key) != pressed:
                    self._prev_states[key] = pressed
                    with self._lock:
                        self.button_states[key] = pressed
                    if self.on_button_change and pressed:
                        self.on_button_change(key[0], key[1])

    def stop(self):
        self._running = False
        try: self._sock_recv.close()
        except: pass


# ─── Player State ─────────────────────────────────────────────────────────────
class Player:
    def __init__(self, player_id: int, walls: list):
        self.id         = player_id
        self.walls      = walls          # list of wall numbers (1-4)
        self.score      = 0
        self.time_bonus = 0.0            # accumulated from phase 1
        self.phase2_time = 0.0           # remaining time in phase 2
        self.sequence   = []             # list of (wall, led, color_key)
        self.seq_index  = 0              # current position in sequence
        self.finished   = False
        self.finish_time = None

    @property
    def current_seq_step(self):
        if self.seq_index < len(self.sequence):
            return self.sequence[self.seq_index]
        return None

    @property
    def next_seq_step(self):
        if self.seq_index + 1 < len(self.sequence):
            return self.sequence[self.seq_index + 1]
        return None


# ─── Game ─────────────────────────────────────────────────────────────────────
class EvilEyeGame:
    def __init__(self, net: EvilEyeNetwork, num_players: int, config: dict):
        self.net         = net
        self.num_players = num_players
        self.config      = config

        self.phase       = "idle"   # idle | phase1 | transition | phase2 | finished
        self.phase1_timer = 0.0
        self.winner      = None
        self._lock       = threading.Lock()

        # LED state sent to hardware
        self._leds = {}

        # Create players
        assignments = WALL_ASSIGNMENTS[num_players]
        self.players = {
            pid: Player(pid, walls)
            for pid, walls in assignments.items()
        }

        # Phase 1: button color assignments {(ch, led): "blue"|"red"}
        self._p1_colors  = {}
        # Phase 1: which buttons still active (not yet pressed)
        self._p1_active  = set()

        # Register button handler
        self.net.on_button_change = self._on_button_press

        # Eye animation state
        self._eye_phase  = 0.0

    # ── Phase 1 Setup ─────────────────────────────────────────────────────────
    def start_phase1(self):
        self.phase = "phase1"
        self.phase1_timer = PHASE1_DURATION

        # Randomize button colors (buttons 1-10 on each wall)
        self._p1_colors.clear()
        self._p1_active.clear()
        for ch in range(1, NUM_CHANNELS + 1):
            for led in range(1, 11):
                color = "blue" if random.random() < 0.5 else "red"
                self._p1_colors[(ch, led)] = color
                self._p1_active.add((ch, led))

        self._render_phase1()
        print("[GAME] Phase 1 started!")

    def _render_phase1(self):
        leds = {}
        for ch in range(1, NUM_CHANNELS + 1):
            # Eye off during phase 1
            leds[(ch, 0)] = OFF
            for led in range(1, 11):
                if (ch, led) in self._p1_active:
                    color_key = self._p1_colors.get((ch, led), "blue")
                    leds[(ch, led)] = BLUE if color_key == "blue" else RED
                else:
                    leds[(ch, led)] = OFF
        self._leds = leds
        self.net.send_leds(leds)

    # ── Phase 1 Button Press ──────────────────────────────────────────────────
    def _handle_phase1_press(self, ch, led):
        if (ch, led) not in self._p1_active:
            return

        # Find which player owns this wall
        player = self._find_player_by_wall(ch)
        if player is None:
            return

        color_key = self._p1_colors.get((ch, led), "blue")
        self._p1_active.discard((ch, led))

        if color_key == "blue":
            player.score      += 1
            player.time_bonus += TIME_BLUE
            # Flash button white briefly then off
            self._leds[(ch, led)] = WHITE
        else:
            player.score      -= 1
            player.time_bonus += TIME_RED
            self._leds[(ch, led)] = DIM_RED

        self.net.send_leds(dict(self._leds))

        # Schedule button off after 200ms
        def turn_off():
            time.sleep(0.2)
            self._leds[(ch, led)] = OFF
            self.net.send_leds(dict(self._leds))
        threading.Thread(target=turn_off, daemon=True).start()

        print(f"[P1] Player {player.id} pressed Wall {ch} LED {led} "
              f"({color_key}) | score={player.score} bonus={player.time_bonus:.1f}s")

    # ── Phase 2 Setup ─────────────────────────────────────────────────────────
    def start_phase2(self):
        self.phase = "phase2"

        for player in self.players.values():
            # Calculate phase 2 time
            player.phase2_time = max(
                MIN_PHASE2_TIME,
                BASE_PHASE2_TIME + player.time_bonus
            )
            # Generate sequence for this player's walls
            player.sequence  = self._generate_sequence(player.walls)
            player.seq_index = 0
            player.finished  = False
            print(f"[P2] Player {player.id}: {player.phase2_time:.1f}s | "
                  f"seq_len={len(player.sequence)}")

        self._render_phase2()
        print("[GAME] Phase 2 started!")

    def _generate_sequence(self, walls: list) -> list:
        """
        Generate a sequence of (wall, led, color_key) tuples.
        All buttons 1-10 on the player's walls, shuffled.
        Colors: start(blue), normal(purple), skip(yellow), finish(green)
        """
        # Collect all available buttons
        buttons = [(w, led) for w in walls for led in range(1, 11)]
        random.shuffle(buttons)

        seq = []
        n   = len(buttons)
        for i, (w, led) in enumerate(buttons):
            if i == 0:
                color_key = "start"
            elif i == n - 1:
                color_key = "finish"
            else:
                # Weighted random: 50% normal, 30% skip, 20% finish-ish
                r = random.random()
                if r < 0.55:
                    color_key = "normal"
                else:
                    color_key = "skip"
            seq.append((w, led, color_key))

        return seq

    def _render_phase2(self):
        leds = {}

        # Eyes on — animated
        eye_val = int((math.sin(self._eye_phase) * 0.5 + 0.5) * 200 + 55)
        for ch in range(1, NUM_CHANNELS + 1):
            leds[(ch, 0)] = (eye_val, 0, 0)   # red pulsing eyes

        # All buttons off first
        for ch in range(1, NUM_CHANNELS + 1):
            for led in range(1, 11):
                leds[(ch, led)] = OFF

        # Light up current step for each player
        for player in self.players.values():
            if player.finished:
                # Show all green for finished player
                for w in player.walls:
                    for led in range(1, 11):
                        leds[(w, led)] = GREEN
                continue

            step = player.current_seq_step
            if step:
                w, led, color_key = step
                leds[(w, led)] = SEQ_COLORS[color_key]

            # Also show skip target dimly if current is "skip"
            if step and step[2] == "skip":
                next_step = player.next_seq_step
                if next_step:
                    # The step after next (skip means skip next, press one after)
                    skip_idx = player.seq_index + 2
                    if skip_idx < len(player.sequence):
                        tw, tled, _ = player.sequence[skip_idx]
                        # Dim yellow hint
                        leds[(tw, tled)] = (80, 60, 0)

        self._leds = leds
        self.net.send_leds(leds)

    # ── Phase 2 Button Press ──────────────────────────────────────────────────
    def _handle_phase2_press(self, ch, led):
        player = self._find_player_by_wall(ch)
        if player is None or player.finished:
            return

        step = player.current_seq_step
        if step is None:
            return

        w, expected_led, color_key = step

        # Check if player pressed on correct wall
        if ch != w:
            # Wrong wall — penalize
            self._flash_wrong(ch, led, player)
            return

        if color_key == "skip":
            # For skip: player must press the button 2 steps ahead
            # First, we need to check if they pressed correctly
            skip_target_idx = player.seq_index + 2
            if skip_target_idx < len(player.sequence):
                tw, tled, _ = player.sequence[skip_target_idx]
                if ch == tw and led == tled:
                    # Correct skip!
                    player.seq_index += 3   # skip current, skipped, land on next
                    print(f"[P2] Player {player.id} SKIP! step→{player.seq_index}")
                    self._check_finish(player)
                else:
                    self._flash_wrong(ch, led, player)
            else:
                # Skip would go past end — just advance normally
                if led == expected_led:
                    self._advance_player(player)
                else:
                    self._flash_wrong(ch, led, player)

        elif color_key == "finish":
            if led == expected_led:
                player.finished   = True
                player.finish_time = time.time()
                self.winner = player.id
                self.phase  = "finished"
                print(f"[P2] Player {player.id} FINISHED!")
                self._render_victory(player.id)
            else:
                self._flash_wrong(ch, led, player)

        else:
            # start or normal
            if led == expected_led:
                self._advance_player(player)
            else:
                self._flash_wrong(ch, led, player)

    def _advance_player(self, player: Player):
        player.seq_index += 1
        print(f"[P2] Player {player.id} advance → step {player.seq_index}/{len(player.sequence)}")
        self._check_finish(player)

    def _check_finish(self, player: Player):
        if player.seq_index >= len(player.sequence):
            player.finished   = True
            player.finish_time = time.time()
            self.winner = player.id
            self.phase  = "finished"
            print(f"[P2] Player {player.id} FINISHED!")
            self._render_victory(player.id)

    def _flash_wrong(self, ch, led, player: Player):
        """Flash red on wrong press, penalize 3s."""
        player.phase2_time = max(0, player.phase2_time - 3.0)
        orig = self._leds.get((ch, led), OFF)
        self._leds[(ch, led)] = RED
        self.net.send_leds(dict(self._leds))
        def restore():
            time.sleep(0.3)
            self._leds[(ch, led)] = orig
            self.net.send_leds(dict(self._leds))
        threading.Thread(target=restore, daemon=True).start()
        print(f"[P2] Player {player.id} WRONG! -{3}s → {player.phase2_time:.1f}s left")

    # ── Victory ───────────────────────────────────────────────────────────────
    def _render_victory(self, winner_id: int):
        leds = {}
        winner_walls = self.players[winner_id].walls

        for ch in range(1, NUM_CHANNELS + 1):
            if ch in winner_walls:
                # Winner walls flash green
                leds[(ch, 0)] = GREEN
                for led in range(1, 11):
                    leds[(ch, led)] = GREEN
            else:
                leds[(ch, 0)] = DIM_RED
                for led in range(1, 11):
                    leds[(ch, led)] = OFF

        self._leds = leds
        self.net.send_leds(leds)

    def _render_timeout(self):
        """All players ran out of time."""
        leds = {}
        for ch in range(1, NUM_CHANNELS + 1):
            leds[(ch, 0)] = RED
            for led in range(1, 11):
                leds[(ch, led)] = DIM_RED
        self._leds = leds
        self.net.send_leds(leds)

    # ── Transition ────────────────────────────────────────────────────────────
    def _render_transition(self, progress: float):
        """Animated transition between phases (progress 0→1)."""
        leds = {}
        val = int(progress * 255)
        for ch in range(1, NUM_CHANNELS + 1):
            leds[(ch, 0)] = (val, 0, 0)
            for led in range(1, 11):
                pulse = int((math.sin(progress * math.pi * 4 + led * 0.5) * 0.5 + 0.5) * val)
                leds[(ch, led)] = (pulse, 0, pulse)
        self._leds = leds
        self.net.send_leds(leds)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _find_player_by_wall(self, ch: int):
        for player in self.players.values():
            if ch in player.walls:
                return player
        return None

    def _on_button_press(self, ch: int, led: int):
        """Called from network thread on any button press."""
        with self._lock:
            phase = self.phase
        if phase == "phase1":
            self._handle_phase1_press(ch, led)
        elif phase == "phase2":
            self._handle_phase2_press(ch, led)

    # ── Main Update Loop ──────────────────────────────────────────────────────
    def update(self, dt: float):
        with self._lock:
            phase = self.phase

        if phase == "phase1":
            self.phase1_timer -= dt
            self._eye_phase   += dt * 1.5

            if self.phase1_timer <= 0:
                self.phase = "transition"
                self._transition_start = time.time()
                print("[GAME] Phase 1 ended — transitioning...")

            else:
                # Re-render every frame (eye animation if wanted)
                self._render_phase1()

        elif phase == "transition":
            elapsed  = time.time() - self._transition_start
            progress = min(1.0, elapsed / 3.0)
            self._render_transition(progress)
            if progress >= 1.0:
                self.start_phase2()

        elif phase == "phase2":
            self._eye_phase += dt * 2.0
            all_finished = all(p.finished for p in self.players.values())
            all_timeout  = all(p.phase2_time <= 0 for p in self.players.values())

            # Tick each player's timer
            any_alive = False
            for player in self.players.values():
                if not player.finished:
                    player.phase2_time -= dt
                    if player.phase2_time > 0:
                        any_alive = True

            if all_finished or (not any_alive and not all_finished):
                if not all_finished:
                    # Timeout — no winner
                    self.phase = "finished"
                    self.winner = None
                    self._render_timeout()
                    print("[GAME] All players timed out!")
            else:
                self._render_phase2()

        elif phase == "finished":
            # Flash victory animation
            self._eye_phase += dt * 3.0
            pulse = (math.sin(self._eye_phase) * 0.5 + 0.5)
            if self.winner:
                winner_walls = self.players[self.winner].walls
                leds = dict(self._leds)
                for ch in winner_walls:
                    val = int(pulse * 255)
                    leds[(ch, 0)] = (0, val, 0)
                    for led in range(1, 11):
                        leds[(ch, led)] = (0, val, 0)
                self.net.send_leds(leds)

    def get_status(self) -> dict:
        """Return current game status for display."""
        return {
            "phase":       self.phase,
            "p1_timer":    self.phase1_timer,
            "winner":      self.winner,
            "players": {
                pid: {
                    "score":      p.score,
                    "time_bonus": p.time_bonus,
                    "p2_time":    p.phase2_time,
                    "seq_progress": f"{p.seq_index}/{len(p.sequence)}",
                    "finished":   p.finished,
                }
                for pid, p in self.players.items()
            }
        }


# ─── Config UI (tkinter) ──────────────────────────────────────────────────────
def run_config_ui():
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return {"num_players": 2, "device_ip": UDP_DEVICE_IP}

    result = {}
    root = tk.Tk()
    root.title("👁️ Evil Eye Game — Setup")
    root.configure(bg="#111")
    root.resizable(False, False)

    def lbl(text, row):
        tk.Label(root, text=text, bg="#111", fg="#aaa",
                 font=("Consolas", 11)).grid(row=row, column=0,
                 sticky="w", padx=20, pady=10)

    lbl("Number of players:", 0)
    players_v = tk.StringVar(value="2")
    ttk.Combobox(root, textvariable=players_v, values=["2", "4"],
                 width=10, state="readonly").grid(row=0, column=1, padx=10)

    lbl("Phase 1 duration (s):", 1)
    p1_v = tk.StringVar(value="60")
    tk.Entry(root, textvariable=p1_v, bg="#222", fg="white",
             font=("Consolas", 10), insertbackground="white",
             width=10).grid(row=1, column=1, padx=10)

    lbl("Base phase 2 time (s):", 2)
    p2_v = tk.StringVar(value="30")
    tk.Entry(root, textvariable=p2_v, bg="#222", fg="white",
             font=("Consolas", 10), insertbackground="white",
             width=10).grid(row=2, column=1, padx=10)

    lbl("Device IP:", 3)
    ip_v = tk.StringVar(value=UDP_DEVICE_IP)
    tk.Entry(root, textvariable=ip_v, bg="#222", fg="white",
             font=("Consolas", 10), insertbackground="white",
             width=18).grid(row=3, column=1, padx=10)

    # Info box
    info = tk.Label(root,
        text="2 players: Wall 1+2 vs Wall 3+4\n4 players: 1 wall each",
        bg="#1a1a1a", fg="#888", font=("Consolas", 9), justify="left")
    info.grid(row=4, column=0, columnspan=2, padx=20, pady=8, sticky="w")

    cancelled = [False]

    def start():
        try:
            result["num_players"]    = int(players_v.get())
            result["phase1_duration"]= float(p1_v.get())
            result["base_p2_time"]   = float(p2_v.get())
            result["device_ip"]      = ip_v.get().strip()
        except ValueError:
            return
        root.destroy()

    def cancel():
        cancelled[0] = True
        root.destroy()

    bf = tk.Frame(root, bg="#111")
    bf.grid(row=5, column=0, columnspan=2, pady=15)
    tk.Button(bf, text="👁️  START GAME", command=start,
              bg="#440044", fg="white", font=("Consolas", 12, "bold"),
              relief="flat", padx=20, pady=8).pack(side=tk.LEFT, padx=8)
    tk.Button(bf, text="Cancel", command=cancel,
              bg="#333", fg="white", font=("Consolas", 10),
              relief="flat", padx=10, pady=8).pack(side=tk.LEFT, padx=8)

    root.mainloop()
    return None if (cancelled[0] or not result) else result


# ─── Second Monitor (pygame) ──────────────────────────────────────────────────
def run_display(game_ref, config):
    try:
        import pygame
    except ImportError:
        print("pygame not available — skipping display.")
        return

    pygame.init()
    screen = pygame.display.set_mode((800, 480))
    pygame.display.set_caption("👁️ Evil Eye Game")
    clock  = pygame.time.Clock()

    FL = pygame.font.SysFont("Arial", 56,  bold=True)
    FM = pygame.font.SysFont("Arial", 36)
    FS = pygame.font.SysFont("Arial", 24)
    FT = pygame.font.SysFont("Arial", 18)

    BG      = (10,  5,  15)
    P_COLS  = {1: (0,150,255), 2: (255,80,0), 3: (0,220,100), 4: (220,0,220)}

    def draw_timer_bar(surface, x, y, w, h, val, max_val, color):
        pygame.draw.rect(surface, (40,40,40), (x, y, w, h))
        filled = int(w * max(0, val / max_val)) if max_val > 0 else 0
        pygame.draw.rect(surface, color, (x, y, filled, h))
        pygame.draw.rect(surface, (100,100,100), (x, y, w, h), 1)

    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); return
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                pygame.quit(); return

        game = game_ref[0]
        if not game:
            clock.tick(30); continue

        status = game.get_status()
        screen.fill(BG)
        W, H = screen.get_size()

        phase = status["phase"]

        # ── Header ──
        phase_names = {
            "idle":       "WAITING...",
            "phase1":     "⚡ PHASE 1 — COLLECT",
            "transition": "👁️ THE EYE AWAKENS...",
            "phase2":     "👁️ PHASE 2 — FOLLOW THE SEQUENCE",
            "finished":   "🏆 GAME OVER",
        }
        title = FL.render(phase_names.get(phase, phase.upper()), True, (200, 100, 255))
        screen.blit(title, title.get_rect(center=(W//2, 40)))

        if phase == "phase1":
            # Big phase 1 timer
            t = max(0, status["p1_timer"])
            timer_col = (255,80,80) if t < 10 else (255,220,0) if t < 20 else (0,220,100)
            tt = FL.render(f"{t:.1f}s", True, timer_col)
            screen.blit(tt, tt.get_rect(center=(W//2, 110)))

            # Player scores
            players = status["players"]
            n = len(players)
            pw = (W - 60) // n
            for i, (pid, pdata) in enumerate(players.items()):
                px = 30 + i * pw
                col = P_COLS.get(pid, (200,200,200))
                # Player box
                pygame.draw.rect(screen, (25,15,35), (px, 160, pw-10, 200), border_radius=8)
                pygame.draw.rect(screen, col, (px, 160, pw-10, 200), 2, border_radius=8)

                pname = FM.render(f"Player {pid}", True, col)
                screen.blit(pname, pname.get_rect(center=(px + (pw-10)//2, 195)))

                score_col = (0,220,100) if pdata["score"] >= 0 else (255,80,80)
                sc = FM.render(f"Score: {pdata['score']:+d}", True, score_col)
                screen.blit(sc, sc.get_rect(center=(px + (pw-10)//2, 240)))

                bonus_col = (0,180,255) if pdata["time_bonus"] >= 0 else (255,80,80)
                bc = FS.render(f"Time bonus: {pdata['time_bonus']:+.0f}s", True, bonus_col)
                screen.blit(bc, bc.get_rect(center=(px + (pw-10)//2, 285)))

                # Projected phase 2 time
                proj = max(MIN_PHASE2_TIME, BASE_PHASE2_TIME + pdata["time_bonus"])
                pc = FT.render(f"→ Phase 2: {proj:.0f}s", True, (150,150,150))
                screen.blit(pc, pc.get_rect(center=(px + (pw-10)//2, 320)))

            # Legend
            leg_y = 390
            for color_name, color_val, meaning in [
                ("BLUE",   (0,80,255),   "+2s to Phase 2"),
                ("RED",    (220,0,0),    "-3s from Phase 2"),
            ]:
                pygame.draw.rect(screen, color_val, (30, leg_y, 20, 20))
                lt = FT.render(f"= {meaning}", True, (160,160,160))
                screen.blit(lt, (58, leg_y))
                leg_y += 28

        elif phase == "transition":
            msg = FM.render("The Eye is watching...", True, (180, 0, 180))
            screen.blit(msg, msg.get_rect(center=(W//2, H//2)))

        elif phase == "phase2":
            players = status["players"]
            n = len(players)
            pw = (W - 60) // n
            for i, (pid, pdata) in enumerate(players.items()):
                px = 30 + i * pw
                col = P_COLS.get(pid, (200,200,200))
                pygame.draw.rect(screen, (25,15,35), (px, 80, pw-10, 300), border_radius=8)
                pygame.draw.rect(screen, col, (px, 80, pw-10, 300), 2, border_radius=8)

                pname = FM.render(f"Player {pid}", True, col)
                screen.blit(pname, pname.get_rect(center=(px + (pw-10)//2, 110)))

                if pdata["finished"]:
                    fin = FM.render("✓ FINISHED!", True, (0,255,100))
                    screen.blit(fin, fin.get_rect(center=(px + (pw-10)//2, 180)))
                else:
                    t2 = pdata["p2_time"]
                    t2_col = (255,80,80) if t2 < 10 else (255,220,0) if t2 < 20 else (0,220,100)
                    tt2 = FM.render(f"{max(0,t2):.1f}s", True, t2_col)
                    screen.blit(tt2, tt2.get_rect(center=(px + (pw-10)//2, 160)))

                    # Timer bar
                    max_t = max(MIN_PHASE2_TIME, BASE_PHASE2_TIME + 20)
                    draw_timer_bar(screen, px+10, 195, pw-30, 18,
                                   max(0,t2), max_t, t2_col)

                    # Sequence progress
                    prog = pdata["seq_progress"]
                    pt = FS.render(f"Step {prog}", True, (180,180,180))
                    screen.blit(pt, pt.get_rect(center=(px + (pw-10)//2, 235)))

            # Legend
            leg_y = 410
            for color_name, color_val, meaning in [
                ("BLUE",   (0,0,200),    "= START"),
                ("PURPLE", (128,0,200),  "= NEXT"),
                ("YELLOW", (200,180,0),  "= SKIP ONE"),
                ("GREEN",  (0,200,0),    "= FINISH!"),
            ]:
                pygame.draw.rect(screen, color_val, (30, leg_y, 16, 16))
                lt = FT.render(f"= {meaning}", True, (140,140,140))
                screen.blit(lt, (52, leg_y))
                leg_y += 22

        elif phase == "finished":
            winner = status["winner"]
            if winner:
                wcol = P_COLS.get(winner, (255,255,255))
                wt = FL.render(f"🏆 Player {winner} WINS!", True, wcol)
                screen.blit(wt, wt.get_rect(center=(W//2, H//2 - 30)))
            else:
                wt = FL.render("⏰ Time's up! No winner.", True, (200,100,100))
                screen.blit(wt, wt.get_rect(center=(W//2, H//2 - 30)))

            # Show all scores
            players = status["players"]
            for i, (pid, pdata) in enumerate(players.items()):
                col = P_COLS.get(pid, (200,200,200))
                st  = FS.render(
                    f"P{pid}: score={pdata['score']:+d}  "
                    f"bonus={pdata['time_bonus']:+.0f}s  "
                    f"{'FINISHED' if pdata['finished'] else 'DNF'}",
                    True, col)
                screen.blit(st, st.get_rect(center=(W//2, H//2 + 40 + i*35)))

        pygame.display.flip()
        clock.tick(30)


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("👁️  Evil Eye Game")

    config = run_config_ui()
    if not config:
        print("Cancelled.")
        return

    # Apply config globals
    global PHASE1_DURATION, BASE_PHASE2_TIME
    PHASE1_DURATION  = config.get("phase1_duration", 60.0)
    BASE_PHASE2_TIME = config.get("base_p2_time",    30.0)

    device_ip    = config.get("device_ip", UDP_DEVICE_IP)
    num_players  = config.get("num_players", 2)

    print(f"Starting: {num_players} players | device={device_ip}")
    print(f"Phase1: {PHASE1_DURATION}s | Base P2: {BASE_PHASE2_TIME}s")

    net  = EvilEyeNetwork(device_ip=device_ip)
    game = EvilEyeGame(net, num_players, config)
    game_ref = [game]

    # Start display thread
    import threading
    threading.Thread(target=run_display, args=(game_ref, config), daemon=True).start()

    # Brief countdown then start
    print("Starting in 3...")
    time.sleep(1)
    print("2...")
    time.sleep(1)
    print("1...")
    time.sleep(1)

    game.start_phase1()

    DT = 1.0 / 20   # 20 FPS update loop
    print("Game running! Ctrl+C to quit.")

    try:
        while game.phase != "finished" or True:
            t0 = time.time()
            game.update(DT)
            if game.phase == "finished":
                # Keep display alive for 10s then exit
                elapsed_finished = getattr(game, "_finished_at", None)
                if elapsed_finished is None:
                    game._finished_at = time.time()
                elif time.time() - game._finished_at > 10:
                    print("Game ended.")
                    break
            time.sleep(max(0, DT - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nQuit.")

    net.stop()


if __name__ == "__main__":
    main()
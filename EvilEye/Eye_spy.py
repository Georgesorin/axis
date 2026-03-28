import socket
import threading
import time
import random

UDP_IP = "127.0.0.1"
UDP_SEND_PORT = 2002
UDP_RECV_PORT = 2003

WIDTH = 10
HEIGHT = 4

BLUE = (0, 150, 255)
RED = (255, 0, 0)
PURPLE = (180, 0, 255)
YELLOW = (255, 255, 0)
GREEN = (0, 255, 100)
OFF = (0, 0, 0)


class EvilEyeGame:
    def __init__(self):
        self.grid = [[OFF for _ in range(WIDTH)] for _ in range(HEIGHT)]

        self.phase = 1
        self.start_time = time.time()

        self.scores = [0, 0]
        self.phase2_time = [20, 20]

        self.sequence = []
        self.seq_index = [0, 0]

        self.running = True

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listener.bind(("0.0.0.0", UDP_RECV_PORT))

        threading.Thread(target=self.send_loop, daemon=True).start()
        threading.Thread(target=self.listen_loop, daemon=True).start()

        self.init_phase1()

    def init_phase1(self):
        for y in range(HEIGHT):
            for x in range(WIDTH):
                self.grid[y][x] = random.choice([BLUE, RED])

    def init_phase2(self):
        self.sequence = ["blue"]
        for _ in range(6):
            self.sequence.append(random.choice(["purple", "yellow"]))
        self.sequence.append("green")

        self.seq_index = [0, 0]
        self.start_time = time.time()

    def send_loop(self):
        while self.running:
            frame = self.build_frame()
            self.sock.sendto(frame, (UDP_IP, UDP_SEND_PORT))
            time.sleep(0.02)

    def build_frame(self):
        data = bytearray(132)

        led = 0
        for y in range(HEIGHT):
            for x in range(WIDTH):
                r, g, b = self.grid[y][x]

                idx = led * 3
                if idx + 2 < len(data):
                    data[idx] = g
                    data[idx + 1] = r
                    data[idx + 2] = b

                led += 1

        return data

    def listen_loop(self):
        while True:
            data, _ = self.listener.recvfrom(1024)

            if len(data) < 3:
                continue

            x = data[1]
            y = data[2]

            if x >= WIDTH or y >= HEIGHT:
                continue

            self.handle_click(x, y)

    def handle_click(self, x, y):
        player = 0 if x < WIDTH // 2 else 1

        if self.phase == 1:
            color = self.grid[y][x]

            if color == BLUE:
                self.scores[player] += 1
                self.phase2_time[player] += 2
                print(f"P{player+1} +1")
            else:
                self.scores[player] = max(0, self.scores[player] - 1)
                self.phase2_time[player] -= 3
                print(f"P{player+1} HIT RED")

            self.grid[y][x] = random.choice([BLUE, RED])

        elif self.phase == 2:
            if self.seq_index[player] >= len(self.sequence):
                return

            step = self.sequence[self.seq_index[player]]

            if step == "yellow":
                self.seq_index[player] += 2
            else:
                self.seq_index[player] += 1

            print(f"P{player+1} progress:", self.seq_index[player])

            if self.seq_index[player] >= len(self.sequence):
                print(f"P{player+1} FINISHED!")

    def update(self):
        now = time.time()

        if self.phase == 1:
            if now - self.start_time > 10:
                print("PHASE 2 START")
                self.phase = 2
                self.init_phase2()

        elif self.phase == 2:
            done = True
            for i in range(2):
                if self.seq_index[i] < len(self.sequence):
                    done = False

            if done:
                self.phase = 3
                print("GAME OVER")

    def run(self):
        while True:
            self.update()
            time.sleep(0.05)


if __name__ == "__main__":
    game = EvilEyeGame()
    game.run()
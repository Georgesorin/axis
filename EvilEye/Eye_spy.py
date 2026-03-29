import socket
import threading
import time
import random

UDP_IP = "127.0.0.1"
UDP_SEND_PORT = 4626
UDP_RECV_PORT = 7800

BUTTONS = 40

BLUE = (0,150,255)
RED = (255,0,0)
PURPLE = (180,0,255)
YELLOW = (255,255,0)
GREEN = (0,255,100)
OFF = (0,0,0)


class EvilEyeGame:
    def __init__(self):
        self.players = int(input("Players (2 or 4): ") or 2)

        self.buttons = [OFF]*BUTTONS
        self.current_pressed = {i:set() for i in range(1,self.players+1)}

        self.phase = 1
        self.start_time = time.time()

        self.scores = [0]*self.players
        self.phase2_time = [20]*self.players

        self.sequence = []
        self.seq_index = [0]*self.players

        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv.bind(("0.0.0.0", UDP_RECV_PORT))
        self.sock_recv.settimeout(0.1)

        threading.Thread(target=self.send_loop, daemon=True).start()
        threading.Thread(target=self.listen_loop, daemon=True).start()

        self.init_phase1()

    # ================= NETWORK =================

    def build_packet(self, eye_rgb, walls):
        frame = bytearray(132)

        # LED 0 = eye
        for ch in range(4):
            frame[ch] = eye_rgb[1]
            frame[4+ch] = eye_rgb[0]
            frame[8+ch] = eye_rgb[2]

        # LED 1-10 = buttons
        for led in range(1,11):
            offset = led*12
            for ch in range(4):
                wall = ch+1
                r,g,b = walls.get(wall, {}).get(led, (0,0,0))
                frame[offset+ch] = g
                frame[offset+4+ch] = r
                frame[offset+8+ch] = b

        payload = bytearray([0,0,0,0x88,0x77,0,0xF0,0,0]) + frame
        pkt = bytearray([0x75,0,0,0,len(payload)]) + payload
        return pkt

    def send_loop(self):
        while True:
            walls = {}

            for i in range(BUTTONS):
                wall = i//10 + 1
                btn = i%10 + 1

                if wall not in walls:
                    walls[wall] = {}

                walls[wall][btn] = self.buttons[i]

            pkt = self.build_packet((0,0,0), walls)
            self.sock_send.sendto(pkt, (UDP_IP, UDP_SEND_PORT))

            time.sleep(0.05)

    def listen_loop(self):
        while True:
            try:
                data,_ = self.sock_recv.recvfrom(2048)

                if len(data)>2 and data[0]==0x88 and data[1]==0x01:
                    for ch in range(1,self.players+1):
                        pressed=set()
                        for idx in range(1,11):
                            byte_idx = 2 + (ch-1)*171 + 1 + idx
                            if data[byte_idx]==0xCC:
                                pressed.add(idx)
                        self.current_pressed[ch]=pressed

                    self.handle_inputs()

            except:
                pass

    # ================= GAME =================

    def get_player(self,i):
        if self.players==2:
            return 0 if i<20 else 1
        else:
            return i//10

    def handle_inputs(self):
        for p in range(1,self.players+1):
            for btn in self.current_pressed[p]:
                global_idx = (p-1)*10 + (btn-1)
                self.handle_click(global_idx)

    def handle_click(self,i):
        p = self.get_player(i)

        if self.phase==1:
            color = self.buttons[i]

            if color==BLUE:
                self.scores[p]+=1
                self.phase2_time[p]+=2
                print(f"P{p+1} +1")
            else:
                self.scores[p]=max(0,self.scores[p]-1)
                self.phase2_time[p]-=3
                print(f"P{p+1} RED")

            self.buttons[i]=random.choice([BLUE,RED])

        elif self.phase==2:
            if self.seq_index[p]>=len(self.sequence):
                return

            step = self.sequence[self.seq_index[p]]

            if step=="yellow":
                self.seq_index[p]+=2
            else:
                self.seq_index[p]+=1

            print(f"P{p+1} progress:", self.seq_index[p])

    # ================= PHASE =================

    def init_phase1(self):
        for i in range(BUTTONS):
            self.buttons[i]=random.choice([BLUE,RED])

    def init_phase2(self):
        self.sequence=["blue"]+[random.choice(["purple","yellow"]) for _ in range(6)]+["green"]
        self.seq_index=[0]*self.players

    def update(self):
        now=time.time()

        if self.phase==1 and now-self.start_time>10:
            print("PHASE 2 START")
            self.phase=2
            self.init_phase2()

        elif self.phase==2:
            if all(self.seq_index[i]>=len(self.sequence) for i in range(self.players)):
                print("GAME OVER")
                self.phase=3

    def run(self):
        while True:
            self.update()
            time.sleep(0.05)


if __name__=="__main__":
    game=EvilEyeGame()
    game.run()
import socket
import time
import random
import threading
import psutil
import os

try:
    import pygame
    pygame.mixer.init()
    HAS_AUDIO = True
except ImportError:
    print("⚠️ Pygame nu este instalat. Sunetele nu vor funcționa. Folosiți 'pip install pygame'.")
    HAS_AUDIO = False

def play_sfx(filename):
    if HAS_AUDIO:
        filepath = os.path.join("_sfx", filename)
        if os.path.exists(filepath):
            try:
                pygame.mixer.Sound(filepath).play()
            except:
                pass

def calc_sum(data):
    return sum(data) & 0xFF


def get_local_interfaces():
    interfaces = [("Simulator Local (127.0.0.1)", "127.0.0.1", "127.0.0.1")]
    try:
        for iface, addrs in psutil.net_if_addrs().items():
            ip = None
            bcast = "255.255.255.255"
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address != "127.0.0.1":
                    ip = addr.address
                    if addr.broadcast:
                        bcast = addr.broadcast
            if ip:
                interfaces.append((iface, ip, bcast))
    except Exception as e:
        pass
    return interfaces


def build_discovery_packet():
    rand1, rand2 = random.randint(0, 127), random.randint(0, 127)
    payload = bytearray([0x0A, 0x02, *b"KX-HC04", 0x03, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x14])
    pkt = bytearray([0x67, rand1, rand2, len(payload)]) + payload
    pkt.append(calc_sum(pkt))
    return pkt, rand1, rand2


def run_discovery_flow():
    interfaces = get_local_interfaces()
    if not interfaces: return None
    print("\n--- Selectare Rețea ---")
    for i, (iface, ip, bcast) in enumerate(interfaces):
        print(f"[{i}] {iface} - {ip}")
    try:
        choice = int(input("\nSelectați numărul interfeței: "))
    except:
        choice = 0
    sel = interfaces[choice]

    if sel[1] == "127.0.0.1": return "127.0.0.1"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.bind((sel[1], 7800))
    except:
        pass

    pkt, r1, r2 = build_discovery_packet()
    try:
        sock.sendto(pkt, (sel[2], 4626))
    except:
        return None

    sock.settimeout(0.5)
    end_time = time.time() + 3
    devices = []
    while time.time() < end_time:
        try:
            data, addr = sock.recvfrom(1024)
            if len(data) >= 30 and data[0] == 0x68 and data[1] == r1 and data[2] == r2:
                devices.append({'ip': addr[0]})
        except:
            pass
    sock.close()
    if devices: return devices[0]['ip']
    return "127.0.0.1"


class EvilEyeGame:
    def __init__(self, target_ip, num_players):
        self.target_ip = target_ip if target_ip else "127.0.0.1"
        self.num_players = num_players
        self.lives = {i: 3 for i in range(1, num_players + 1)}

        self.player_colors = {
            1: (0, 0, 255),  # Albastru
            2: (0, 255, 0),  # Verde
            3: (255, 255, 0),  # Galben
            4: (255, 0, 255)  # Mov
        }
        self.all_colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255), (0, 255, 255),
                           (255, 165, 0)]

        self.running = True
        self.time_off = 20
        self.current_pressed_buttons = {i: set() for i in range(1, num_players + 1)}

        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_recv.bind(("0.0.0.0", 7800))
        self.sock_recv.settimeout(0.1)

    def build_light_packet(self, eye_rgb, walls_button_rgb):
        frame_data = bytearray(132)
        for ch in range(4):
            frame_data[0 + ch], frame_data[4 + ch], frame_data[8 + ch] = eye_rgb[1], eye_rgb[0], eye_rgb[2]

        for led_idx in range(1, 11):
            offset = led_idx * 12
            for ch in range(4):
                wall_id = ch + 1
                r, g, b = walls_button_rgb.get(wall_id, {}).get(led_idx, (0, 0, 0))
                frame_data[offset + ch], frame_data[offset + 4 + ch], frame_data[offset + 8 + ch] = g, r, b

        payload = bytearray([0x00, 0x00, 0x00, 0x88, 0x77, 0x00, 0x00, 0x00, 0x00]) + frame_data
        pkt = bytearray([0x75, 0x00, 0x00, 0x00, len(payload)]) + payload
        return pkt

    def send_lights(self, eye_rgb, walls_button_rgb):
        try:
            self.sock_send.sendto(self.build_light_packet(eye_rgb, walls_button_rgb), (self.target_ip, 4626))
        except:
            pass

    def listener_thread(self):
        while self.running:
            try:
                data, _ = self.sock_recv.recvfrom(2048)
                if len(data) > 2 and data[0] == 0x88 and data[1] == 0x01:
                    for ch in range(1, self.num_players + 1):
                        pressed = set()
                        for idx in range(1, 11):
                            if data[2 + (ch - 1) * 171 + 1 + idx] == 0xCC: pressed.add(idx)
                        self.current_pressed_buttons[ch] = pressed
            except:
                continue

    def play(self):
        threading.Thread(target=self.listener_thread, daemon=True).start()
        runda = 1

        while self.running:
            active_players = [p for p, l in self.lives.items() if l > 0]
            if len(active_players) <= 1: break

            print(f"\n--- RUNDA {runda} ---")
            print(f"Ochiul este STINS ({self.time_off}s). Colectează-ți culoarea! ATENȚIE LA CAPCANE!")

            end_off_time = time.time() + self.time_off
            game_over_trigger = False  # Flag pentru a opri complet buclele și a preveni glitch-urile
            while time.time() < end_off_time and not game_over_trigger:
                walls_lights, target_buttons, trap_buttons = {}, {}, {}

                # Generăm butoane și capcane
                for p in active_players:
                    walls_lights[p] = {}
                    target_buttons[p], trap_buttons[p] = set(), set()
                    btn_aprinse = random.sample(range(1, 11), 5)

                    for b in btn_aprinse[:2]:  # 2 corecte
                        walls_lights[p][b] = self.player_colors[p]
                        target_buttons[p].add(b)
                    for b in btn_aprinse[2:]:  # 3 capcane
                        culori_disp = [c for c in self.all_colors if c != self.player_colors[p]]
                        walls_lights[p][b] = random.choice(culori_disp)
                        trap_buttons[p].add(b)

                self.send_lights((0, 0, 0), walls_lights)

                # Așteptăm click-uri timp de 1.5 secunde
                t_refresh = time.time()
                while time.time() - t_refresh < 1.5 and time.time() < end_off_time:
                    schimbare = False
                    for p in list(active_players):
                        if p not in active_players: continue
                        apasate = self.current_pressed_buttons[p]

                        # Click pe buton corect
                        for b in list(target_buttons[p]):
                            if b in apasate:
                                walls_lights[p][b] = (0, 0, 0)
                                target_buttons[p].remove(b)
                                play_sfx("move.wav")
                                schimbare = True

                        # Click pe capcană
                        for b in list(trap_buttons[p]):
                            if b in apasate:
                                self.lives[p] -= 1
                                play_sfx("trap.wav")
                                print(f"💀 JUCĂTORUL {p} A APĂSAT O CAPCANĂ! Vieți rămase: {self.lives[p]}")
                                trap_buttons[p].remove(b)
                                walls_lights[p][b] = (0, 0, 0)
                                schimbare = True
                                if self.lives[p] <= 0:
                                    print(f"☠️ JUCĂTORUL {p} A FOST ELIMINAT! ☠️")
                                    active_players.remove(p)
                                    walls_lights[p] = {}
                                    if len(active_players) <= 1:
                                        game_over_trigger = True  # Marchează sfârșitul jocului curat

                    if schimbare: self.send_lights((0, 0, 0), walls_lights)
                    if len(active_players) <= 1: break
                    time.sleep(0.05)

            if len(active_players) <= 1 or game_over_trigger: break

            # --- FAZA 2: OCHI APRINS ---
            print("⚠️ OCHIUL E ROȘU! DĂ CLICK PE BUTONUL ALB PENTRU A FI ÎN SIGURANȚĂ! ⚠️")
            safe_buttons = {p: random.randint(1, 10) for p in active_players}
            walls_lights = {p: {safe_buttons[p]: (255, 255, 255)} for p in active_players}

            grace_period = 2.0
            eye_on_duration = 4.0
            start_on_time = time.time()
            saved_players, punished_this_round = set(), set()

            while time.time() < start_on_time + eye_on_duration:
                # Retrimitem mereu luminile ca să trecem de "timeout-ul" simulatorului
                self.send_lights((255, 0, 0), walls_lights)

                for p in list(active_players):
                    if p not in active_players: continue
                    # Jucătorul dă click pe butonul alb
                    if p not in saved_players and p not in punished_this_round:
                        if safe_buttons[p] in self.current_pressed_buttons[p]:
                            saved_players.add(p)
                            walls_lights[p][safe_buttons[p]] = (0, 255, 0)  # Devine Verde!
                            self.send_lights((255, 0, 0), walls_lights)
                            print(f"✅ Jucătorul {p} s-a salvat!")

                # Penalizare dacă trece timpul și nu a dat click
                if time.time() > start_on_time + grace_period:
                    for p in list(active_players):
                        if p not in saved_players and p not in punished_this_round:
                            self.lives[p] -= 1
                            play_sfx("trap.wav")
                            punished_this_round.add(p)
                            print(f"❌ JUCĂTORUL {p} NU A AJUNS LA SAFE-ZONE! Vieți: {self.lives[p]}")
                            if self.lives[p] <= 0:
                                active_players.remove(p)
                                walls_lights[p] = {}
                                self.send_lights((255, 0, 0), walls_lights)
                time.sleep(0.05)

            if self.time_off > 2: self.time_off -= 2
            runda += 1

        active_players = [p for p, l in self.lives.items() if l > 0]
        print("\n🏁 JOCUL S-A TERMINAT! 🏁")

        if len(active_players) == 1:
            winner = active_players[0]
            print(f"🏆 CÂȘTIGĂTOR ESTE JUCĂTORUL {winner}! 🏆")
            play_sfx("victory.wav")  # Cântă victoria

            print("✨ SE AFIȘEAZĂ ANIMAȚIA DE VICTORIE (10 secunde)... ✨")
            win_color = self.player_colors[winner]
            flash_end = time.time() + 10.0
            state = True

            # Buclă de pâlpâire timp de 10 secunde
            while time.time() < flash_end:
                walls_lights = {}
                if state:
                    # Aprinde toate cele 10 butoane cu culoarea câștigătorului
                    walls_lights[winner] = {i: win_color for i in range(1, 11)}
                    self.send_lights((0, 255, 0), walls_lights)  # Ochiul se face Verde Fericit
                else:
                    self.send_lights((0, 0, 0), {})  # Stins

                state = not state
                time.sleep(0.4)  # Viteza de pâlpâire

        self.send_lights((0, 0, 0), {})
        self.running = False


if __name__ == "__main__":
    target_ip = run_discovery_flow()
    while True:
        try:
            num = int(input("Introdu numărul de jucători (2-4): "))
            if 2 <= num <= 4: break
        except:
            pass

    game = EvilEyeGame(target_ip, num)
    try:
        game.play()
    except KeyboardInterrupt:
        game.running = False
        game.send_lights((0, 0, 0), {})
import wave
import math
import struct
import random
import os
import time
import pygame

SFX_DIR = "_sfx"

# -------------------
# SOUND GENERATOR
# -------------------
def save_wav(filename, data, sample_rate=44100):
    if not os.path.exists(SFX_DIR):
        os.makedirs(SFX_DIR)
        
    path = os.path.join(SFX_DIR, filename)
    with wave.open(path, 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(1)  # 8-bit audio
        f.setframerate(sample_rate)
        f.writeframes(data)
    print(f"Generated {path}")

def generate_tone(freq, duration, vol=0.5, type='sine', slide=0):
    sample_rate = 44100
    n_samples = int(sample_rate * duration)
    data = bytearray()
    
    for i in range(n_samples):
        t = i / sample_rate
        cur_freq = freq + slide * t
        
        if type == 'sine':
            val = math.sin(2 * math.pi * cur_freq * t)
        elif type == 'square':
            val = 1.0 if math.sin(2 * math.pi * cur_freq * t) > 0 else -1.0
        elif type == 'saw':
            val = 2.0 * (t * cur_freq - math.floor(0.5 + t * cur_freq))
        elif type == 'noise':
            val = random.uniform(-1, 1)
            
        # Convert -1.0...1.0 to 0...255
        scaled = int((val * vol + 1.0) * 127.5)
        scaled = max(0, min(255, scaled))
        data.append(scaled)
        
    return data

def play_sound(filename):
    path = os.path.join(SFX_DIR, filename)
    if os.path.exists(path):
        sound = pygame.mixer.Sound(path)
        sound.play()
    else:
        print(f"Sound {filename} not found!")

# -------------------
# AUDIO TIMER CLASS
# -------------------
class AudioTimer:
    def __init__(self, duration):
        """
        duration: total time in seconds
        """
        self.duration = duration
        self.start_time = time.time()
        self.half_triggered = False
        self.warn30_triggered = False
        self.countdown_triggered = False
        self.last_tick = None

    def reset(self):
        self.start_time = time.time()
        self.half_triggered = False
        self.warn30_triggered = False
        self.countdown_triggered = False
        self.last_tick = None

    def update(self):
        elapsed = time.time() - self.start_time
        remaining = int(self.duration - elapsed)

        # Half-time beep
        if not self.half_triggered and elapsed >= self.duration / 2:
            play_sound("half.wav")
            self.half_triggered = True

        # 30-second warning beep
        if not self.warn30_triggered and remaining <= 30:
            play_sound("warn30.wav")
            self.warn30_triggered = True

        # Countdown ticks 10..1
        if remaining <= 10:
            if self.last_tick != remaining:
                play_sound("tick.wav")
                self.last_tick = remaining
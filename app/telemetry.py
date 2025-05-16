### app/telemetry.py
import threading
import random
import time

class Telemetry:
    def __init__(self):
        self.running = False
        self.data = {
            'speed': 0,
            'gear': 1,
            'lap': 1,
            'tire_wear': 100.0,
            'position': 10,
            'fuel': 100.0
        }

    def update(self):
        while self.running:
            self.data['speed'] = random.randint(180, 320)
            self.data['gear'] = random.randint(1, 8)
            self.data['lap'] += 1
            self.data['tire_wear'] -= random.uniform(0.5, 2.0)
            self.data['position'] = random.randint(1, 20)
            self.data['fuel'] -= random.uniform(1.0, 2.5)
            time.sleep(5)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self.update)
        self.thread.start()

    def stop(self):
        self.running = False
        self.thread.join()

    def get_data(self):
        return self.data.copy()
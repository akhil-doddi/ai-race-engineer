# telemetry.py
import random
import time
from threading import Thread

class Telemetry:
    def __init__(self):
        self.data = {
            "lap": 1,
            "position": 5,
            "lap_time": 92.0,  # in seconds
            "tire_wear": 100.0,  # %
            "fuel_level": 100.0,  # %
            "engine_temp": 90.0  # °C
        }
        self.running = False

    def update(self):
        while self.running:
            self.data["lap_time"] = round(random.uniform(91, 94), 2)
            self.data["tire_wear"] = max(0, self.data["tire_wear"] - random.uniform(0.2, 0.8))
            self.data["fuel_level"] = max(0, self.data["fuel_level"] - random.uniform(0.5, 1.5))
            self.data["engine_temp"] = round(random.uniform(95, 102), 1)

            # Simulate a position change every few laps
            if random.random() < 0.1:
                self.data["position"] = max(1, self.data["position"] - 1)
            elif random.random() < 0.1:
                self.data["position"] += 1

            time.sleep(1)  # update every 1 second

    def start(self):
        self.running = True
        Thread(target=self.update, daemon=True).start()

    def stop(self):
        self.running = False

    def get_data(self):
        return self.data.copy()
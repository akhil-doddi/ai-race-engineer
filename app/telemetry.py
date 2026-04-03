import threading
import random
import time

# Base lap time in seconds (e.g. 92.0 = 1:32.0)
BASE_LAP_TIME = 92.0
TOTAL_LAPS = 50

class Telemetry:
    def __init__(self):
        self.running = False
        self.data = {
            # Race state
            'lap': 1,
            'total_laps': TOTAL_LAPS,
            'laps_remaining': TOTAL_LAPS - 1,
            'position': random.randint(3, 15),

            # Gaps
            'gap_ahead': round(random.uniform(0.5, 5.0), 1),   # seconds to car ahead
            'gap_behind': round(random.uniform(0.5, 5.0), 1),  # seconds to car behind

            # Tyres
            'tire_compound': random.choice(['Soft', 'Medium', 'Hard']),
            'tire_wear': 100.0,   # 100% = new, 0% = done
            'tire_age_laps': 0,   # how many laps on this set

            # Fuel
            'fuel': 100.0,        # kg remaining (100 = full)
            'fuel_per_lap': round(random.uniform(1.8, 2.2), 2),  # consistent burn rate

            # Lap times
            'last_lap_time': self._format_laptime(BASE_LAP_TIME),
            'best_lap_time': self._format_laptime(BASE_LAP_TIME),
            'lap_delta': '+0.000',  # vs best lap

            # Car
            'speed': 220,
            'gear': 5,
            'drs': False,
        }

    def _format_laptime(self, seconds):
        """Convert seconds float to M:SS.mmm string e.g. 92.456 -> 1:32.456"""
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}:{secs:06.3f}"

    def _calculate_lap_time(self):
        """
        Lap time degrades as tires wear and improves slightly as fuel burns off.
        - Every 10% tire wear adds ~0.3s
        - Every 10kg of fuel adds ~0.06s (heavier = slower)
        """
        tire_penalty = ((100 - self.data['tire_wear']) / 10) * 0.3
        fuel_benefit = ((100 - self.data['fuel']) / 10) * 0.06
        natural_variance = random.uniform(-0.1, 0.15)
        lap_time = BASE_LAP_TIME + tire_penalty - fuel_benefit + natural_variance
        return round(lap_time, 3)

    def update(self):
        while self.running:
            # --- Lap counter ---
            self.data['lap'] += 1
            self.data['laps_remaining'] = max(0, TOTAL_LAPS - self.data['lap'])
            self.data['tire_age_laps'] += 1

            # --- Tyre wear: degrades faster on softs, slower on hards ---
            wear_rates = {'Soft': random.uniform(1.5, 3.0),
                          'Medium': random.uniform(0.8, 1.8),
                          'Hard': random.uniform(0.4, 1.0)}
            wear = wear_rates[self.data['tire_compound']]
            self.data['tire_wear'] = max(0.0, self.data['tire_wear'] - wear)

            # --- Fuel burn ---
            self.data['fuel'] = max(0.0, self.data['fuel'] - self.data['fuel_per_lap'])

            # --- Lap time (affected by tyre wear and fuel) ---
            lap_time_secs = self._calculate_lap_time()
            self.data['last_lap_time'] = self._format_laptime(lap_time_secs)

            # Track best lap
            best_secs = self._parse_laptime(self.data['best_lap_time'])
            if lap_time_secs < best_secs:
                self.data['best_lap_time'] = self._format_laptime(lap_time_secs)
                self.data['lap_delta'] = '-{:.3f}'.format(best_secs - lap_time_secs)
            else:
                self.data['lap_delta'] = '+{:.3f}'.format(lap_time_secs - best_secs)

            # --- Position: rarely changes, ±1 only ---
            if random.random() < 0.15:
                change = random.choice([-1, 1])
                self.data['position'] = max(1, min(20, self.data['position'] + change))

            # --- Gaps to cars ahead/behind ---
            self.data['gap_ahead'] = max(0.1, round(
                self.data['gap_ahead'] + random.uniform(-0.3, 0.3), 1))
            self.data['gap_behind'] = max(0.1, round(
                self.data['gap_behind'] + random.uniform(-0.3, 0.3), 1))

            # --- Car state ---
            self.data['speed'] = random.randint(180, 320)
            self.data['gear'] = random.randint(3, 8)
            self.data['drs'] = random.choice([True, False])

            time.sleep(5)

    def _parse_laptime(self, laptime_str):
        """Convert M:SS.mmm string back to seconds float."""
        mins, secs = laptime_str.split(':')
        return int(mins) * 60 + float(secs)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.thread.join()

    def get_data(self):
        return self.data.copy()

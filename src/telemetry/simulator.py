"""
src/telemetry/simulator.py

Phase 1 telemetry source: simulates realistic F1 race data in a background thread.

WHY THIS EXISTS:
In Phase 2, this file will be replaced by udp_listener.py which receives real
packets from the PS5. The rest of the system (race_state, events, strategy) will
not need to change — they consume the same data structure regardless of source.
This is the architectural boundary that makes the Phase 1 → Phase 2 swap clean.

DESIGN DECISION:
Tyre wear, fuel, and lap times are mathematically linked — worn tyres produce
slower laps, lighter fuel loads produce faster laps. This mimics real F1 physics
and gives the AI meaningful data to reason about rather than random noise.
"""

import threading
import random
import time

from config.settings import BASE_LAP_TIME, TOTAL_LAPS


class TelemetrySimulator:
    """
    Simulates a live F1 car's telemetry data, updating every 5 seconds.

    Produces a race_state dictionary consumed by RaceStateManager.
    All values degrade or change in physically realistic ways:
    - Tyre wear only decreases
    - Fuel only decreases
    - Lap times worsen as tyres wear, improve slightly as fuel burns off
    - Position changes rarely (±1, 15% chance per lap)
    """

    def __init__(self):
        self.running = False
        self.data = {
            # Race progress
            "lap": 1,
            "total_laps": TOTAL_LAPS,
            "laps_remaining": TOTAL_LAPS - 1,
            "position": random.randint(3, 15),

            # Gap to nearest competitors in seconds
            "gap_ahead": round(random.uniform(0.5, 5.0), 1),
            "gap_behind": round(random.uniform(0.5, 5.0), 1),

            # Tyre state — life starts full (100%), only decreases
            "tire_compound": random.choice(["Soft", "Medium", "Hard"]),
            "tire_wear": 100.0,
            "tire_age_laps": 0,

            # Fuel — starts full (100 kg), burns consistently each lap
            "fuel": 100.0,
            "fuel_per_lap": round(random.uniform(1.8, 2.2), 2),

            # Lap timing
            "last_lap_time": self._format_laptime(BASE_LAP_TIME),
            "best_lap_time": self._format_laptime(BASE_LAP_TIME),
            "lap_delta": "+0.000",

            # Car state
            "speed": 220,
            "gear": 5,
            "drs": False,
        }

    def _format_laptime(self, seconds: float) -> str:
        """Convert raw seconds to M:SS.mmm display format. e.g. 92.456 → '1:32.456'"""
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}:{secs:06.3f}"

    def _parse_laptime(self, laptime_str: str) -> float:
        """Convert M:SS.mmm string back to raw seconds for comparison."""
        mins, secs = laptime_str.split(":")
        return int(mins) * 60 + float(secs)

    def _calculate_lap_time(self) -> float:
        """
        Derive a realistic lap time from current tyre and fuel state.

        Physics model:
        - Every 10% tyre life lost adds ~0.3s (rubber degradation slows cornering)
        - Every 10kg of fuel burned saves ~0.06s (lighter car = faster)
        - Small random variance simulates driver input and track conditions
        """
        tire_penalty = ((100 - self.data["tire_wear"]) / 10) * 0.3
        fuel_benefit = ((100 - self.data["fuel"]) / 10) * 0.06
        natural_variance = random.uniform(-0.1, 0.15)
        return round(BASE_LAP_TIME + tire_penalty - fuel_benefit + natural_variance, 3)

    def _update(self):
        """Main update loop — runs in background thread, fires every 5 seconds."""
        while self.running:
            # Advance lap counter
            self.data["lap"] += 1
            self.data["laps_remaining"] = max(0, TOTAL_LAPS - self.data["lap"])
            self.data["tire_age_laps"] += 1

            # Degrade tyres — rate varies by compound
            wear_rates = {
                "Soft": random.uniform(1.5, 3.0),    # Fast but fragile
                "Medium": random.uniform(0.8, 1.8),  # Balanced
                "Hard": random.uniform(0.4, 1.0),    # Durable but slow
            }
            wear = wear_rates[self.data["tire_compound"]]
            self.data["tire_wear"] = max(0.0, self.data["tire_wear"] - wear)

            # Burn fuel — consistent rate per lap
            self.data["fuel"] = max(0.0, self.data["fuel"] - self.data["fuel_per_lap"])

            # Calculate new lap time based on current state
            lap_time_secs = self._calculate_lap_time()
            self.data["last_lap_time"] = self._format_laptime(lap_time_secs)

            # Track personal best lap
            best_secs = self._parse_laptime(self.data["best_lap_time"])
            if lap_time_secs < best_secs:
                self.data["best_lap_time"] = self._format_laptime(lap_time_secs)
                self.data["lap_delta"] = "-{:.3f}".format(best_secs - lap_time_secs)
            else:
                self.data["lap_delta"] = "+{:.3f}".format(lap_time_secs - best_secs)

            # Position shifts rarely — overtakes happen, but not every lap
            if random.random() < 0.15:
                change = random.choice([-1, 1])
                self.data["position"] = max(1, min(20, self.data["position"] + change))

            # Gaps fluctuate slightly each lap
            self.data["gap_ahead"] = max(0.1, round(
                self.data["gap_ahead"] + random.uniform(-0.3, 0.3), 1))
            self.data["gap_behind"] = max(0.1, round(
                self.data["gap_behind"] + random.uniform(-0.3, 0.3), 1))

            # Instantaneous car state
            self.data["speed"] = random.randint(180, 320)
            self.data["gear"] = random.randint(3, 8)
            self.data["drs"] = random.choice([True, False])

            time.sleep(5)

    def start(self):
        """Start the background telemetry update thread."""
        self.running = True
        # daemon=True ensures the thread dies automatically when main program exits
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def stop(self):
        """Gracefully stop the telemetry thread."""
        self.running = False
        self.thread.join()

    def get_snapshot(self) -> dict:
        """
        Return a frozen copy of the current telemetry state.

        Returns a copy (not a reference) so consumers always get a consistent
        snapshot, even if the background thread updates data mid-read.
        """
        return self.data.copy()

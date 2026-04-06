"""
src/telemetry/simulator.py

Phase 1 telemetry source: simulates a strategically complex F1 race in a background thread.

DESIGN GOALS:
This simulator is not trying to simulate a perfect race pace.
It is designed to CREATE DECISION PRESSURE for the AI race engineer:
  - Medium tyres that degrade enough to force a two-stop strategy
  - A safety car that compresses the field and reshuffles strategy
  - Dirty air that punishes following too close
  - A "cliff" where tyres fall off a performance cliff after 70% wear

TYRE MODEL:
  - Normal phase  : 3.0–4.5% wear per lap (Medium)
  - Cliff phase   : >70% accumulated wear → wear rate multiplies by 1.5–2.5×
  - Stint target  : 18–22 laps on Mediums before life drops critically low

SAFETY CAR:
  - Deployed randomly between laps 15 and 28
  - Lasts 3–6 laps
  - During SC: lap times +25%, wear drops to 0.3–0.8%/lap, field gaps compress

STRATEGIC COMPLEXITY CREATED:
  - Lap 18–22: first pit window opens (Mediums near cliff)
  - Safety car may compress gaps → undercut opportunities
  - Two-stop becomes clearly optimal if tyres aren't managed perfectly
"""

import threading
import random
import time

from config.settings import BASE_LAP_TIME, TOTAL_LAPS


class TelemetrySimulator:
    """
    Simulates a live F1 car's telemetry data, updating every 5 seconds.

    Produces a race_state dictionary consumed by RaceStateManager.
    Designed to create strategic complexity, not just random numbers.
    """

    def __init__(self):
        self.running = False

        # --- Safety car timing (decided at race start, unknown to AI) ---
        self._sc_deploy_lap = random.randint(15, 28)
        self._sc_end_lap    = self._sc_deploy_lap + random.randint(3, 6)
        self._sc_announced  = False   # print SC deploy message once

        self.data = {
            # Race progress
            "lap":              1,
            "total_laps":       TOTAL_LAPS,
            "laps_remaining":   TOTAL_LAPS - 1,
            "position":         random.randint(5, 12),

            # Competitor gaps
            "gap_ahead":        round(random.uniform(1.0, 4.0), 1),
            "gap_behind":       round(random.uniform(1.0, 4.0), 1),

            # Tyre state — life starts at 100%, decreases each lap
            "tire_compound":    random.choice(["Soft", "Medium", "Hard"]),
            "tire_wear":        100.0,   # % life remaining (100 = new, 0 = gone)
            "tire_age_laps":    0,

            # Fuel — starts full, burns ~1.9 units/lap for 53 laps
            "fuel":             100.0,
            "fuel_per_lap":     round(100.0 / TOTAL_LAPS, 2),

            # Lap timing
            "last_lap_time":    self._fmt_laptime(BASE_LAP_TIME),
            "best_lap_time":    self._fmt_laptime(BASE_LAP_TIME),
            "lap_delta":        "+0.000",

            # Instantaneous car state
            "speed":            220,
            "gear":             5,
            "drs":              False,

            # Track condition — GREEN normally, SAFETY_CAR during SC period
            "track_status":     "green",
        }

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _fmt_laptime(self, seconds: float) -> str:
        """Convert raw seconds to M:SS.mmm. e.g. 92.456 → '1:32.456'"""
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}:{secs:06.3f}"

    def _parse_laptime(self, laptime_str: str) -> float:
        """Convert M:SS.mmm back to raw seconds."""
        mins, secs = laptime_str.split(":")
        return int(mins) * 60 + float(secs)

    def _tyre_wear_this_lap(self) -> float:
        """
        Calculate tyre wear for one lap using the cliff model.

        Medium tyres:
          - Normal phase (life > 30%): 3.0–4.5% per lap
          - Cliff phase  (life ≤ 30%): 5.5–8.0% per lap (rubber grain breaks down)

        Under safety car: only 0.3–0.8% wear (low speeds, gentle cornering).
        """
        if self.data["track_status"] == "safety_car":
            return random.uniform(0.3, 0.8)

        compound  = self.data["tire_compound"]
        tire_life = self.data["tire_wear"]

        # Base wear rate by compound
        base_rates = {
            "Soft":   random.uniform(4.5, 6.5),
            "Medium": random.uniform(3.0, 4.5),
            "Hard":   random.uniform(1.5, 2.5),
        }
        base = base_rates.get(compound, 3.5)

        # Cliff effect: below 30% life, rubber behaviour deteriorates rapidly
        if tire_life < 30:
            cliff_multiplier = 1.5 + ((30 - tire_life) / 30) * 1.0
            base *= cliff_multiplier

        return base

    def _calculate_lap_time(self) -> float:
        """
        Derive a realistic lap time from tyre wear, fuel load, and track conditions.

        Penalties:
          - Tyre wear    : up to +3.0s at 70% life, exponential beyond (cliff)
          - Dirty air    : +0.2–0.5s when gap ahead < 1.0s
        Benefits:
          - Fuel burn    : −0.06s per 10 units burned (lighter car)
          - Safety car   : lap time replaced with SC delta lap (~115s)
        """
        if self.data["track_status"] == "safety_car":
            # Safety car lap: ~25% slower + random variation
            return round(BASE_LAP_TIME * 1.25 + random.uniform(-0.5, 1.0), 3)

        tire_life = self.data["tire_wear"]

        # Tyre degradation penalty — linear until cliff, then sharper
        if tire_life >= 30:
            wear_penalty = ((100 - tire_life) / 10) * 0.3
        else:
            # Normal phase contribution up to the cliff at 70% accumulated
            normal_phase = (70 / 10) * 0.3
            # Cliff contribution: accelerating penalty below 30% remaining life
            cliff_contribution = ((30 - tire_life) / 10) * 0.9
            wear_penalty = normal_phase + cliff_contribution

        # Fuel benefit: lighter car is faster
        fuel_benefit = ((100 - self.data["fuel"]) / 10) * 0.06

        # Dirty air penalty: very close behind another car loses downforce
        dirty_air = 0.0
        if self.data["gap_ahead"] < 1.0 and self.data["gap_ahead"] > 0:
            dirty_air = random.uniform(0.2, 0.5)

        variance = random.uniform(-0.15, 0.2)

        return round(BASE_LAP_TIME + wear_penalty - fuel_benefit + dirty_air + variance, 3)

    # -----------------------------------------------------------------------
    # Background update loop
    # -----------------------------------------------------------------------

    def _update(self):
        """Main update loop — runs in background thread, fires every 5 seconds."""
        while self.running:
            lap = self.data["lap"] + 1

            # ── Safety car status ──────────────────────────────────────────
            if lap == self._sc_deploy_lap:
                self.data["track_status"] = "safety_car"
                print(f"\n🟡 SAFETY CAR DEPLOYED — Lap {lap}  (clears Lap {self._sc_end_lap})")
            elif lap == self._sc_end_lap + 1:
                self.data["track_status"] = "green"
                print(f"\n🟢 SAFETY CAR IN — Lap {lap}  (green flag)")

            # ── Advance lap counter ────────────────────────────────────────
            self.data["lap"]            = lap
            self.data["laps_remaining"] = max(0, TOTAL_LAPS - lap)
            self.data["tire_age_laps"] += 1

            # ── Tyre wear ─────────────────────────────────────────────────
            wear = self._tyre_wear_this_lap()
            self.data["tire_wear"] = max(0.0, round(self.data["tire_wear"] - wear, 1))

            # ── Fuel burn ─────────────────────────────────────────────────
            self.data["fuel"] = max(0.0, round(
                self.data["fuel"] - self.data["fuel_per_lap"], 2))

            # ── Lap time ──────────────────────────────────────────────────
            lap_secs = self._calculate_lap_time()
            self.data["last_lap_time"] = self._fmt_laptime(lap_secs)

            # Track personal best
            best_secs = self._parse_laptime(self.data["best_lap_time"])
            if lap_secs < best_secs:
                self.data["best_lap_time"] = self._fmt_laptime(lap_secs)
                self.data["lap_delta"]     = "-{:.3f}".format(best_secs - lap_secs)
            else:
                self.data["lap_delta"]     = "+{:.3f}".format(lap_secs - best_secs)

            # ── Position changes ───────────────────────────────────────────
            # More frequent under SC (field bunches, easier overtakes after restart)
            overtake_chance = 0.35 if self.data["track_status"] == "safety_car" else 0.20
            if random.random() < overtake_chance:
                change = random.choice([-1, -1, 1])   # slight bias toward gaining
                self.data["position"] = max(1, min(20, self.data["position"] + change))

            # ── Gaps ──────────────────────────────────────────────────────
            if self.data["track_status"] == "safety_car":
                # Field compresses under SC — gaps shrink toward 0.8–1.5s
                target = random.uniform(0.8, 1.5)
                self.data["gap_ahead"]  = round(
                    self.data["gap_ahead"]  * 0.7 + target * 0.3, 1)
                self.data["gap_behind"] = round(
                    self.data["gap_behind"] * 0.7 + target * 0.3, 1)
            else:
                # Green flag racing: gaps fluctuate naturally
                self.data["gap_ahead"]  = max(0.1, round(
                    self.data["gap_ahead"]  + random.uniform(-0.5, 0.5), 1))
                self.data["gap_behind"] = max(0.1, round(
                    self.data["gap_behind"] + random.uniform(-0.5, 0.5), 1))

            # ── Instantaneous car state ────────────────────────────────────
            self.data["speed"] = random.randint(180, 320)
            self.data["gear"]  = random.randint(3, 8)
            self.data["drs"]   = random.choice([True, False])

            time.sleep(5)

    def start(self):
        """Start the background telemetry update thread."""
        self.running = True
        self.thread  = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def stop(self):
        """Gracefully stop the telemetry thread."""
        self.running = False
        self.thread.join()

    def get_snapshot(self) -> dict:
        """
        Return a frozen copy of the current telemetry state.
        Returns a copy so consumers always get a consistent snapshot.
        """
        return self.data.copy()

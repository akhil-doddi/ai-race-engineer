"""
src/telemetry/fastf1_replay.py

FastF1 race replay — a third telemetry source alongside the simulator and
UDP listener. Loads any completed F1 race session and replays it lap-by-lap,
feeding real race data into the AI engineer exactly as if it were a live race.

WHY THIS EXISTS:
The simulator produces the same physics-based fake race every time.
The UDP listener requires a live PS5 session.
FastF1 replay gives a third option: any real race from the last several
seasons, for any driver on the 20-car grid, with accurate tyre strategies,
real safety car periods, actual lap times, and real gap data.

WHAT FASTF1 PROVIDES (per driver, per lap):
  - Lap number, race position, lap time, personal best flag
  - Tyre compound (Soft/Medium/Hard/Intermediate/Wet) and age in laps
  - TrackStatus per lap (green / yellow / SC / VSC)
  - Pit stop detection (TyreLife drops to low value on new set)
  - Speed trap readings (SpeedST — fastest straightline speed on that lap)

WHAT WE COMPUTE FROM FASTF1 DATA:
  - tire_wear      : estimated % life remaining from compound age vs max stint
  - gap_ahead      : cumulative race time delta to the driver one position ahead
  - gap_behind     : cumulative race time delta to the driver one position behind
  - fuel           : linear burn model from estimated race-start fuel load
  - lap_delta      : current lap vs personal best this stint
  - session_fastest_lap : best lap time from ALL 20 drivers up to current lap
                          (used by the upcoming Phase 3 #7 fastest lap trigger)

PIT STOP HANDLING:
FastF1 shows a new tyre set starting when TyreLife drops back toward 1.
The replay emits a `pit_this_lap` flag in the raw dict so main.py can call
tracker.reset_pit() and reset auto_pit_state without running the pit
simulation animation (the real pit already happened in the data).

HOW TO USE:
  python3 -m src.main   →  choose 'f' for FastF1 replay
  Prompts:  year (2018–2024), event name (e.g. Monza), driver from list
  Speed:    default 3s per lap (~2.5 min for a 50-lap race, fast enough to test)

CACHING:
FastF1 caches session data locally after the first download. Subsequent loads
of the same session are instant (reads from disk, no network needed).
Cache lives at: <project root>/.fastf1_cache/
"""

import threading
import time
from pathlib import Path

import fastf1
import pandas as pd

from config.settings import BASE_LAP_TIME

# ---------------------------------------------------------------------------
# FastF1 cache — stores downloaded session data to avoid re-downloading
# ---------------------------------------------------------------------------

_CACHE_DIR = Path(__file__).parent.parent.parent / ".fastf1_cache"
_CACHE_DIR.mkdir(exist_ok=True)
fastf1.Cache.enable_cache(str(_CACHE_DIR))

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

# How FastF1 names compounds → our display names
COMPOUND_DISPLAY = {
    "SOFT":         "Soft",
    "MEDIUM":       "Medium",
    "HARD":         "Hard",
    "INTERMEDIATE": "Intermediate",
    "WET":          "Wet",
    "UNKNOWN":      "Medium",
}

# Expected maximum stint length per compound (for tyre life % estimation).
# These match event_detector.py's pit_window_age table plus a buffer.
COMPOUND_MAX_LAPS = {
    "SOFT":         22,
    "MEDIUM":       35,
    "HARD":         50,
    "INTERMEDIATE": 30,
    "WET":          25,
    "UNKNOWN":      35,
}

# FastF1 TrackStatus code → our track_status string.
# FastF1 codes (as single-char strings): 1=clear, 2=yellow, 4=SC, 5=red/SC,
# 6=VSC deployed, 7=VSC ending (still VSC speed).
TRACK_STATUS_MAP = {
    "1": "green",
    "2": "green",               # Yellow: localised, not a strategy event
    "4": "safety_car",
    "5": "safety_car",          # Red flag / SC combination
    "6": "virtual_safety_car",
    "7": "virtual_safety_car",  # VSC ending — car still behind VSC
}

# Estimated race-start fuel load in kg.
# Real F1 cars typically carry 100–110kg depending on circuit.
STARTING_FUEL_KG = 105.0


# ---------------------------------------------------------------------------
# FastF1Replay
# ---------------------------------------------------------------------------

class FastF1Replay:
    """
    Replays a completed F1 race session lap-by-lap as a live telemetry source.

    Usage:
        replay = FastF1Replay()
        replay.load_session(year=2023, event="Monza", driver_abbr="VER")
        replay.start()
        # ... in main loop:
        snapshot = replay.get_snapshot()
        replay.stop()

    Implements the same start() / stop() / get_snapshot() interface as
    TelemetrySimulator and UDPTelemetryListener, so TelemetryController
    wraps it transparently — zero changes needed in any layer above.
    """

    def __init__(self):
        self.running        = False
        self._laps_list     = []        # list of raw dicts, one per lap
        self._current_idx   = 0
        self._lock          = threading.Lock()
        self._stop_event    = threading.Event()
        self._thread        = None
        self.lap_interval   = 3.0       # seconds between lap advances

        # Pre-filled defaults so get_snapshot() is safe before load_session().
        self.data = _default_snapshot()

    # -----------------------------------------------------------------------
    # Session loading
    # -----------------------------------------------------------------------

    def load_session(
        self,
        year: int,
        event: str,
        driver_abbr: str,
        lap_interval: float = 3.0,
    ) -> None:
        """
        Download (or load from cache) a completed race session and build
        the per-lap snapshot list for this driver.

        Args:
            year:         Race season, e.g. 2023
            event:        Race event name or round number, e.g. "Monza" or 15
            driver_abbr:  3-letter driver abbreviation, e.g. "VER"
            lap_interval: Seconds between lap advances during replay (default 3s)
        """
        self.lap_interval = lap_interval

        print(f"\n📡 Loading FastF1 session: {year} {event} Race...")
        print("   (first load downloads from FastF1 — subsequent loads are instant from cache)\n")

        session = fastf1.get_session(year, event, "R")
        session.load(
            laps=True,
            telemetry=False,    # per-sample telemetry — not needed, too large
            weather=False,
            messages=False,
        )

        total_laps = int(session.laps["LapNumber"].max())

        # Build a running fastest lap for every lap (all 20 drivers combined).
        # Gives the AI the context to eventually fire a fastest-lap trigger.
        session_fl_by_lap = _compute_running_fastest_lap(session.laps, total_laps)

        # Gap data: cumulative time delta between adjacent positions each lap.
        all_gaps = _compute_gaps(session.laps, driver_abbr, total_laps)

        # Our driver's laps, sorted.
        driver_laps = (
            session.laps
            .pick_drivers(driver_abbr)
            .sort_values("LapNumber")
            .reset_index(drop=True)
        )

        if driver_laps.empty:
            raise ValueError(
                f"No lap data found for driver '{driver_abbr}'. "
                f"Available: {list(session.laps['Driver'].unique())}"
            )

        fuel_per_lap = round(STARTING_FUEL_KG / total_laps, 2)
        best_lap_secs = None   # personal best (updated lap by lap)
        prev_tyre_age = None   # detect pit stops when TyreLife resets

        laps_list = []

        for _, row in driver_laps.iterrows():
            lap_n    = int(row["LapNumber"])
            position = int(row["Position"]) if not pd.isna(row["Position"]) else 10

            # ── Tyre ─────────────────────────────────────────────────────────
            compound_raw = str(row.get("Compound", "UNKNOWN")).upper()
            compound     = COMPOUND_DISPLAY.get(compound_raw, "Medium")
            tyre_age     = int(row["TyreLife"]) if not pd.isna(row.get("TyreLife", float("nan"))) else 0
            tire_wear    = _estimate_tire_wear(compound_raw, tyre_age)

            # Pit detection: TyreLife dropped vs previous lap (new set fitted).
            pit_this_lap = (
                prev_tyre_age is not None and tyre_age < prev_tyre_age
            )
            prev_tyre_age = tyre_age

            # ── Lap times ────────────────────────────────────────────────────
            lap_time_secs = _timedelta_to_seconds(row.get("LapTime"))
            if lap_time_secs and lap_time_secs > 0:
                if best_lap_secs is None or lap_time_secs < best_lap_secs:
                    best_lap_secs = lap_time_secs
                delta_secs = lap_time_secs - best_lap_secs
                delta_str  = f"+{delta_secs:.3f}" if delta_secs >= 0 else f"{delta_secs:.3f}"
            else:
                lap_time_secs = BASE_LAP_TIME
                delta_str = "+0.000"

            best_secs_now = best_lap_secs or BASE_LAP_TIME

            # ── Fuel ─────────────────────────────────────────────────────────
            fuel_remaining = max(0.0, STARTING_FUEL_KG - (lap_n * fuel_per_lap))

            # ── Gaps ─────────────────────────────────────────────────────────
            gap_ahead, gap_behind = all_gaps.get(lap_n, (2.0, 2.0))

            # ── Track status ─────────────────────────────────────────────────
            ts_raw       = str(row.get("TrackStatus", "1")).strip()
            track_status = TRACK_STATUS_MAP.get(ts_raw, "green")

            # ── Speed ────────────────────────────────────────────────────────
            speed = int(row["SpeedST"]) if not pd.isna(row.get("SpeedST", float("nan"))) else 0

            laps_list.append({
                "lap":                    lap_n,
                "total_laps":             total_laps,
                "laps_remaining":         max(0, total_laps - lap_n),
                "position":               position,
                "gap_ahead":              gap_ahead,
                "gap_behind":             gap_behind,
                "tire_compound":          compound,
                "tire_wear":              tire_wear,
                "tire_age_laps":          tyre_age,
                "fuel":                   round(fuel_remaining, 1),
                "fuel_per_lap":           fuel_per_lap,
                "last_lap_time":          _fmt_laptime(lap_time_secs),
                "best_lap_time":          _fmt_laptime(best_secs_now),
                "lap_delta":              delta_str,
                "speed":                  speed,
                "gear":                   0,     # not available per-lap in FastF1
                "drs":                    False,  # not available per-lap in FastF1
                "track_status":           track_status,
                # Extra context fields consumed downstream
                "pit_this_lap":           pit_this_lap,
                "session_fastest_lap":    session_fl_by_lap.get(lap_n),
            })

        self._laps_list = laps_list
        self._current_idx = 0
        with self._lock:
            self.data = laps_list[0] if laps_list else _default_snapshot()

        driver_info = _get_driver_info(session, driver_abbr)
        print(
            f"✅ Loaded {len(laps_list)} laps for "
            f"{driver_info['full_name']} ({driver_info['team']}) "
            f"— {year} {session.event['EventName']}"
        )
        print(
            f"   Replay speed: {lap_interval}s per lap  "
            f"(~{int(len(laps_list) * lap_interval / 60)}min for full race)\n"
        )

    # -----------------------------------------------------------------------
    # Public interface (matches TelemetrySimulator / UDPTelemetryListener)
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the background lap-advance thread."""
        if not self._laps_list:
            raise RuntimeError("Call load_session() before start().")
        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._advance_loop,
            daemon=True,
            name="FastF1Replay",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the replay thread."""
        self._stop_event.set()
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_snapshot(self) -> dict:
        """Return the current lap's telemetry as a raw dict."""
        with self._lock:
            return dict(self.data)

    @property
    def is_finished(self) -> bool:
        """True once the final lap of the session has been reached."""
        return self._current_idx >= len(self._laps_list) - 1

    # -----------------------------------------------------------------------
    # Background loop
    # -----------------------------------------------------------------------

    def _advance_loop(self) -> None:
        """
        Advance one lap every lap_interval seconds.
        Stops automatically after the final lap.
        """
        while not self._stop_event.is_set():
            self._stop_event.wait(self.lap_interval)
            if self._stop_event.is_set():
                break
            with self._lock:
                next_idx = self._current_idx + 1
                if next_idx < len(self._laps_list):
                    self._current_idx = next_idx
                    self.data = self._laps_list[next_idx]
                    # Print a lap marker so the user can follow replay progress
                    lap    = self.data["lap"]
                    total  = self.data["total_laps"]
                    pos    = self.data["position"]
                    tw     = self.data["tire_wear"]
                    cpd    = self.data["tire_compound"]
                    ts     = self.data["track_status"]
                    ts_sym = "🟡" if ts != "green" else "  "
                    pit_mk = " 🔧PIT" if self.data.get("pit_this_lap") else ""
                    print(
                        f"📼 Lap {lap:2d}/{total} | P{pos:2d} | "
                        f"{cpd} {tw:.0f}%{ts_sym}{pit_mk}"
                    )
                else:
                    # Final lap reached — stop advancing
                    self._stop_event.set()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def list_drivers(year: int, event: str) -> list[dict]:
    """
    Load a session and return all 20 drivers as a list of dicts for display.

    Args:
        year:  Season year
        event: Race name or round number

    Returns:
        List of {"number": str, "abbr": str, "full_name": str, "team": str}
        sorted by driver number.
    """
    print(f"\n📡 Fetching driver list for {year} {event}...")
    session = fastf1.get_session(year, event, "R")
    # load only the minimum needed — laps=False speeds this up significantly
    session.load(laps=False, telemetry=False, weather=False, messages=False)

    drivers = []
    for number in sorted(session.drivers, key=lambda x: int(x)):
        info = _get_driver_info(session, number)
        drivers.append({
            "number":    number,
            "abbr":      info["abbr"],
            "full_name": info["full_name"],
            "team":      info["team"],
        })
    return drivers


def _get_driver_info(session, driver_id: str) -> dict:
    """Return a dict with abbr, full_name, and team for a driver."""
    try:
        d = session.get_driver(driver_id)
        return {
            "abbr":      str(d.get("Abbreviation", driver_id)),
            "full_name": str(d.get("FullName", driver_id)),
            "team":      str(d.get("TeamName", "Unknown")),
        }
    except Exception:
        return {"abbr": driver_id, "full_name": driver_id, "team": "Unknown"}


def _compute_gaps(
    all_laps: "pd.DataFrame",
    driver_abbr: str,
    total_laps: int,
) -> dict[int, tuple[float, float]]:
    """
    Compute gap_ahead and gap_behind for every lap of our driver.

    Method: use the cumulative race time (FastF1 `Time` column, which is
    seconds elapsed from session start at the moment the lap ends) to find
    the delta between adjacent-position cars on the same lap number.

    This is an approximation — in reality gaps are measured at the moment
    the leading car crosses the finish line — but it is the standard approach
    used by FastF1 gap analysis tutorials and is accurate to within ~0.5s.

    Returns:
        {lap_number: (gap_ahead_seconds, gap_behind_seconds)}
    """
    # Only keep the columns we need; drop rows with missing critical data.
    needed   = ["Driver", "LapNumber", "Time", "Position"]
    laps     = all_laps[needed].copy()
    laps     = laps.dropna(subset=["LapNumber", "Time", "Position"])
    laps     = laps[laps["Time"].notna()]

    # Convert timedelta → float seconds for arithmetic
    laps["time_s"] = laps["Time"].apply(
        lambda t: t.total_seconds() if hasattr(t, "total_seconds") else float("nan")
    )
    laps = laps.dropna(subset=["time_s"])

    gaps: dict[int, tuple[float, float]] = {}
    our_laps = laps[laps["Driver"] == driver_abbr]

    for _, row in our_laps.iterrows():
        lap_n    = int(row["LapNumber"])
        pos      = int(row["Position"])
        t_us     = row["time_s"]

        same_lap = laps[laps["LapNumber"] == lap_n]

        # Gap ahead (position - 1)
        ahead_df = same_lap[same_lap["Position"] == pos - 1]
        if not ahead_df.empty:
            gap_ahead = abs(t_us - ahead_df.iloc[0]["time_s"])
            gap_ahead = min(gap_ahead, 60.0)   # cap — pit-stop laps can spike
        else:
            gap_ahead = 2.5   # no car ahead (we're leading) or data missing

        # Gap behind (position + 1)
        behind_df = same_lap[same_lap["Position"] == pos + 1]
        if not behind_df.empty:
            gap_behind = abs(t_us - behind_df.iloc[0]["time_s"])
            gap_behind = min(gap_behind, 60.0)
        else:
            gap_behind = 2.5

        gaps[lap_n] = (round(gap_ahead, 2), round(gap_behind, 2))

    return gaps


def _compute_running_fastest_lap(
    all_laps: "pd.DataFrame",
    total_laps: int,
) -> dict[int, float | None]:
    """
    For each lap number, return the session fastest lap time (in seconds)
    among ALL drivers up to and including that lap.

    This gives the AI the context to fire Phase 3 #7 fastest-lap trigger:
    "You are 0.3s off the fastest lap. Push for it."

    Returns:
        {lap_number: fastest_lap_secs_so_far}  e.g. {1: None, 2: 92.4, ...}
    """
    laps = all_laps[["LapNumber", "LapTime"]].copy()
    laps = laps.dropna(subset=["LapNumber", "LapTime"])
    laps["lap_secs"] = laps["LapTime"].apply(
        lambda t: t.total_seconds() if hasattr(t, "total_seconds") else None
    )
    laps = laps.dropna(subset=["lap_secs"])
    laps = laps[laps["lap_secs"] > 60]   # filter outliers (red flag / formation)

    result: dict[int, float | None] = {}
    current_best: float | None = None

    for lap_n in range(1, total_laps + 1):
        lap_rows = laps[laps["LapNumber"] == lap_n]["lap_secs"]
        if not lap_rows.empty:
            lap_min = lap_rows.min()
            if current_best is None or lap_min < current_best:
                current_best = lap_min
        result[lap_n] = current_best

    return result


def _estimate_tire_wear(compound_raw: str, tyre_age: int) -> float:
    """
    Estimate tyre life % remaining from compound and laps on set.

    Uses a two-phase model matching event_detector.py:
      - Linear phase    (tyre_age ≤ 70% of max): gradual wear
      - Cliff phase     (tyre_age  > 70% of max): accelerating wear

    Returns a float from 1.0 (nearly dead) to 100.0 (fresh).
    """
    compound   = compound_raw.upper() if compound_raw else "UNKNOWN"
    max_laps   = COMPOUND_MAX_LAPS.get(compound, 35)
    pct_used   = min(tyre_age / max_laps, 1.0)
    cliff_start = 0.70

    if pct_used <= cliff_start:
        wear_pct = pct_used * 65.0   # linear: 0 → 65% of life used by cliff start
    else:
        linear_at_cliff = cliff_start * 65.0
        cliff_progress  = (pct_used - cliff_start) / (1.0 - cliff_start)
        wear_pct        = linear_at_cliff + cliff_progress * 35.0 * 1.8

    return round(max(1.0, min(100.0, 100.0 - wear_pct)), 1)


def _timedelta_to_seconds(td) -> float | None:
    """Convert a pandas Timedelta or NaT to float seconds. Returns None on NaT."""
    if pd.isna(td) or td is None:
        return None
    if hasattr(td, "total_seconds"):
        return td.total_seconds()
    return None


def _fmt_laptime(seconds: float) -> str:
    """Format seconds as M:SS.mmm string."""
    if not seconds or seconds <= 0:
        return "1:32.000"
    mins = int(seconds // 60)
    secs = seconds % 60
    return f"{mins}:{secs:06.3f}"


def _default_snapshot() -> dict:
    """Return safe default values matching UDPTelemetryListener's default data."""
    return {
        "lap":                  1,
        "total_laps":           50,
        "laps_remaining":       49,
        "position":             10,
        "gap_ahead":            2.0,
        "gap_behind":           2.0,
        "tire_compound":        "Medium",
        "tire_wear":            100.0,
        "tire_age_laps":        0,
        "fuel":                 STARTING_FUEL_KG,
        "fuel_per_lap":         2.1,
        "last_lap_time":        _fmt_laptime(BASE_LAP_TIME),
        "best_lap_time":        _fmt_laptime(BASE_LAP_TIME),
        "lap_delta":            "+0.000",
        "speed":                0,
        "gear":                 0,
        "drs":                  False,
        "track_status":         "green",
        "pit_this_lap":         False,
        "session_fastest_lap":  None,
    }

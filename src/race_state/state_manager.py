"""
src/race_state/state_manager.py

Converts raw telemetry data into a clean, structured race_state object.

WHY THIS LAYER EXISTS:
The AI, event detector, and strategy engine must never consume raw telemetry
directly. Raw telemetry differs between Phase 1 (simulator) and Phase 2 (UDP
packets from PS5). This layer abstracts that difference — everything above it
always receives the same clean race_state structure, regardless of source.

In Phase 2, only this file needs to change to parse UDP packets into the
same race_state format. Zero changes required in events, strategy, or AI layers.

DESIGN PRINCIPLE: Separation of concerns.
The telemetry layer knows HOW to get data.
The race_state layer knows WHAT the data means.
"""


def build_race_state(raw: dict) -> dict:
    """
    Transform a raw telemetry snapshot into a clean race_state object.

    In Phase 1, raw telemetry from TelemetrySimulator already matches
    the target structure closely. This function validates, cleans, and
    standardises the data — adding derived fields where useful.

    Args:
        raw: Dictionary snapshot from TelemetrySimulator.get_snapshot()
             or (Phase 2) UDPTelemetryListener.get_snapshot()

    Returns:
        A clean, validated race_state dictionary consumed by all layers above.
    """
    return {
        # Race progress
        "lap": int(raw.get("lap", 1)),
        "total_laps": int(raw.get("total_laps", 50)),
        "laps_remaining": int(raw.get("laps_remaining", 49)),
        "position": int(raw.get("position", 10)),

        # Competitor gaps in seconds
        "gap_ahead": float(raw.get("gap_ahead", 0.0)),
        "gap_behind": float(raw.get("gap_behind", 0.0)),

        # Tyre state
        "tire_compound": str(raw.get("tire_compound", "Medium")),
        "tire_wear": float(raw.get("tire_wear", 100.0)),       # % life remaining
        "tire_age_laps": int(raw.get("tire_age_laps", 0)),

        # Fuel state
        "fuel": float(raw.get("fuel", 100.0)),                 # kg remaining
        "fuel_per_lap": float(raw.get("fuel_per_lap", 2.0)),   # kg burned per lap

        # Lap timing
        "last_lap_time": str(raw.get("last_lap_time", "1:32.000")),
        "best_lap_time": str(raw.get("best_lap_time", "1:32.000")),
        "lap_delta": str(raw.get("lap_delta", "+0.000")),

        # Instantaneous car state
        "speed": int(raw.get("speed", 0)),
        "gear": int(raw.get("gear", 1)),
        "drs": bool(raw.get("drs", False)),

        # Track condition — "green" normally, "safety_car" during SC period
        # This comes from the simulator (track_status field) or from the UDP
        # listener in Phase 2. Defaults to "green" if not present.
        "track_status": str(raw.get("track_status", "green")),
    }

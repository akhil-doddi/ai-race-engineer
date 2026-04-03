"""
src/events/event_detector.py

Detects meaningful race events from the current race_state and determines
whether the engineer should speak proactively.

WHY THIS EXISTS:
The AI must NOT be called continuously. Calling GPT on every telemetry update
would destroy latency and cost money on useless API calls. Instead, this module
applies fast, deterministic rules to decide WHEN a situation is worth speaking
about. The AI is only invoked AFTER this layer confirms something meaningful
has happened.

DESIGN PRINCIPLE: Rules decide WHEN to speak. AI decides HOW to speak.

URGENCY LEVELS:
- green  : Everything nominal. Engineer stays silent.
- yellow : Something worth monitoring. Speak once, don't repeat.
- red    : Immediate action required. Speak urgently.

COOLDOWN PROTECTION:
Each event level fires only when urgency changes (green→yellow, yellow→red).
This prevents the same alert from repeating every loop iteration.
"""

# Seconds lost in a typical F1 pit stop (used in future undercut calculations)
PIT_STOP_TIME_LOSS = 22.0


def get_event(race_state: dict) -> dict:
    """
    Analyse the current race_state and return an event recommendation.

    Args:
        race_state: Clean race_state dict from RaceStateManager.

    Returns:
        Event dict containing:
            - urgency        : 'green' | 'yellow' | 'red'
            - should_pit     : True if pit stop is recommended this lap
            - reason         : Human-readable explanation string
            - laps_left_on_tyre  : Estimated laps remaining on current set
            - fuel_laps_remaining: Estimated laps of fuel remaining
    """
    tire_life     = race_state["tire_wear"]
    tire_compound = race_state["tire_compound"]
    tire_age      = race_state["tire_age_laps"]
    fuel          = race_state["fuel"]
    laps_left     = race_state["laps_remaining"]
    gap_behind    = race_state["gap_behind"]
    fuel_per_lap  = race_state["fuel_per_lap"]

    # --- Estimate laps remaining on this tyre set ---
    # Use actual measured wear rate rather than fixed compound estimates.
    # If we've done 10 laps and worn 40% life, wear rate = 4% per lap,
    # so we have roughly (remaining life / wear_rate) laps left.
    max_laps_by_compound = {"Soft": 20, "Medium": 35, "Hard": 50}
    compound_max = max_laps_by_compound.get(tire_compound, 30)

    if tire_age > 0:
        wear_per_lap = (100 - tire_life) / tire_age
        if wear_per_lap > 0:
            laps_left_on_tyre = max(0, round(tire_life / wear_per_lap))
        else:
            # Wear rate appears zero — fall back to compound-based estimate
            laps_left_on_tyre = max(0, compound_max - tire_age)
    else:
        # Fresh tyre — no wear data yet, use compound default
        laps_left_on_tyre = compound_max

    # --- Fuel projection ---
    # Only flag fuel as critical when fewer than 5 laps remain.
    # Flagging at race start (when burn rate * laps > 100kg) is a false alarm.
    fuel_laps_remaining = round(fuel / fuel_per_lap, 1) if fuel_per_lap > 0 else 99.0
    fuel_critical = fuel_laps_remaining < 5

    # --- Build event verdict ---
    # Start optimistic (green) and escalate only when rules trigger.
    reasons = []
    urgency = "green"
    should_pit = False

    # Tyre life thresholds — checked in descending severity order
    if tire_life < 15:
        reasons.append(f"tyre life critical at {tire_life:.0f} percent")
        urgency = "red"
        should_pit = True
    elif tire_life < 30:
        reasons.append(f"tyre life low at {tire_life:.0f} percent")
        urgency = "yellow"
        if laps_left_on_tyre <= 3:
            # Few laps left on tyre — escalate to red
            urgency = "red"
            should_pit = True
    elif tire_life < 50:
        if laps_left_on_tyre <= 5:
            reasons.append(f"tyre life at {tire_life:.0f} percent, approaching pit window")
            urgency = "yellow"

    # Fuel critical — independent of tyre state (both can be true simultaneously)
    if fuel_critical:
        reasons.append(f"fuel critical, only {fuel_laps_remaining} laps remaining")
        urgency = "red"

    # Undercut threat — only relevant when already in a warning state
    # A close car behind could undercut if we wait too long to pit
    if gap_behind < 2.0 and urgency in ("yellow", "red"):
        reasons.append(f"undercut risk, car behind only {gap_behind}s away")

    # End-of-race override — too late to benefit from a pit stop
    if laps_left <= 3 and tire_life > 20:
        reasons = ["too late to pit, bring it home"]
        should_pit = False
        urgency = "green"

    reason_str = "; ".join(reasons) if reasons else "tyres and fuel nominal"

    return {
        "urgency": urgency,
        "should_pit": should_pit,
        "reason": reason_str,
        "laps_left_on_tyre": laps_left_on_tyre,
        "fuel_laps_remaining": fuel_laps_remaining,
    }


def format_alert(event: dict) -> str | None:
    """
    Format an event into a terminal display string.

    Returns None if urgency is green (nothing to display).
    The caller checks `if alert:` before printing or speaking.
    """
    if event["urgency"] == "green":
        return None

    icon = "🟡" if event["urgency"] == "yellow" else "🔴"
    action = "PIT RECOMMENDED" if event["should_pit"] else "MONITOR"

    return (
        f"\n{icon} STRATEGY ALERT — {action}\n"
        f"   {event['reason']}\n"
        f"   Tyres: ~{event['laps_left_on_tyre']} laps left on this set\n"
    )

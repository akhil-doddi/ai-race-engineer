PIT_STOP_TIME_LOSS = 22.0  # seconds lost in the pit lane

def get_pit_recommendation(telemetry):
    """
    Analyses current telemetry and returns a pit recommendation dict:
      - should_pit          : True if we recommend pitting this lap
      - urgency             : 'green' | 'yellow' | 'red'
      - reason              : short explanation string
      - laps_left_on_tyre   : estimated laps before tyre life hits 0
      - fuel_laps_remaining : estimated laps of fuel left
    """
    tire_life     = telemetry['tire_wear']
    tire_compound = telemetry['tire_compound']
    tire_age      = telemetry['tire_age_laps']
    fuel          = telemetry['fuel']
    laps_left     = telemetry['laps_remaining']
    gap_behind    = telemetry['gap_behind']
    fuel_per_lap  = telemetry['fuel_per_lap']

    # Estimated laps left on this tyre set
    # Use tyre life % remaining and age together for a more accurate estimate.
    # If we've done 10 laps and worn 40% life, the wear rate is 4% per lap,
    # so we have roughly (remaining life / wear_rate) laps left.
    max_laps = {'Soft': 20, 'Medium': 35, 'Hard': 50}
    total_life = max_laps.get(tire_compound, 30)
    if tire_age > 0:
        wear_per_lap = (100 - tire_life) / tire_age  # actual measured wear rate
        if wear_per_lap > 0:
            laps_left_on_tyre = max(0, round(tire_life / wear_per_lap))
        else:
            laps_left_on_tyre = max(0, total_life - tire_age)
    else:
        laps_left_on_tyre = total_life  # fresh tyre, use compound default

    # Fuel laps remaining — only warn when genuinely critical (< 5 laps)
    fuel_laps_remaining = round(fuel / fuel_per_lap, 1) if fuel_per_lap > 0 else 99.0
    fuel_critical = fuel_laps_remaining < 5

    reasons = []
    urgency = 'green'
    should_pit = False

    # --- Tyre life checks ---
    if tire_life < 15:
        reasons.append(f"tyre life critical at {tire_life:.0f} percent")
        urgency = 'red'
        should_pit = True
    elif tire_life < 30:
        reasons.append(f"tyre life low at {tire_life:.0f} percent")
        urgency = 'yellow'
        if laps_left_on_tyre <= 3:
            urgency = 'red'
            should_pit = True
    elif tire_life < 50:
        if laps_left_on_tyre <= 5:
            reasons.append(f"tyre life at {tire_life:.0f} percent, approaching pit window")
            urgency = 'yellow'

    # --- Fuel check — only flag when genuinely critical ---
    if fuel_critical:
        reasons.append(f"fuel critical, only {fuel_laps_remaining} laps remaining")
        urgency = 'red'

    # --- Undercut risk ---
    if gap_behind < 2.0 and urgency in ('yellow', 'red'):
        reasons.append(f"undercut risk, car behind only {gap_behind}s away")

    # --- Too late to pit ---
    if laps_left <= 3 and tire_life > 20:
        reasons = ["too late to pit, bring it home"]
        should_pit = False
        urgency = 'green'

    reason_str = '; '.join(reasons) if reasons else "tyres and fuel nominal"

    return {
        'should_pit': should_pit,
        'urgency': urgency,
        'reason': reason_str,
        'laps_left_on_tyre': laps_left_on_tyre,
        'fuel_laps_remaining': fuel_laps_remaining,
    }


def format_pit_alert(rec):
    """Returns a formatted string for the terminal, or None if nothing to flag."""
    if rec['urgency'] == 'green':
        return None

    icon = '🟡' if rec['urgency'] == 'yellow' else '🔴'
    action = 'PIT RECOMMENDED' if rec['should_pit'] else 'MONITOR'
    return (
        f"\n{icon} STRATEGY ALERT — {action}\n"
        f"   {rec['reason']}\n"
        f"   Tyres: ~{rec['laps_left_on_tyre']} laps left on this set\n"
    )

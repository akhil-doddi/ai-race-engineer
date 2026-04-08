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

COOLDOWN PROTECTION (two layers):
1. Urgency-change gating — proactive_monitor only speaks when urgency changes
   from the previous poll. This handles most repetition naturally.
2. Per-event cooldowns (this module) — for events that can oscillate back to
   green between laps (gap alerts) or that persist at the same urgency for
   many laps (stint-age pit window), a lap-based cooldown prevents the same
   alert from firing more than once per cooldown window.

   Only gap alerts and the stint-age pit window need this — critical tyre alerts
   (< 15%, < 30%) and safety car overrides are always allowed through immediately.

COOLDOWN CONSTANTS:
- COOLDOWN_GAP_ALERT  : laps between repeated gap (attack/defend) alerts
- COOLDOWN_PIT_WINDOW : laps between repeated stint-age pit window alerts

COOLDOWN STATE:
_cooldowns is a module-level dict mapping event key → last lap it fired.
reset_cooldowns() clears it; call at race start or in tests.
"""

# Seconds lost in a typical F1 pit stop
PIT_STOP_TIME_LOSS = 22.0

# ── Cooldown constants ───────────────────────────────────────────────────────
# Number of laps that must pass before the same alert can fire again.
# Gap alerts: 3 laps prevents noise when a car hovers around the 1.0s mark.
# Pit window: 3 laps prevents repeating "time to box" every lap once the window
# opens and urgency change-detection resets (e.g. if urgency briefly returns
# to green due to a gap fluctuation).
COOLDOWN_GAP_ALERT  = 3   # laps
COOLDOWN_PIT_WINDOW = 3   # laps

# Module-level cooldown state — maps event key → lap number when it last fired.
# Keys used: "gap_ahead", "gap_behind", "pit_window"
_cooldowns: dict = {}


def _on_cooldown(key: str, current_lap: int, cooldown_laps: int) -> bool:
    """
    Return True if this event key is still within its cooldown window.

    WHY -999 AS DEFAULT:
    A key that has never fired has no entry in _cooldowns. Treating the
    default as lap -999 means (current_lap - (-999)) is always >= cooldown_laps,
    so a fresh event passes through immediately without any special-case logic.
    """
    return (current_lap - _cooldowns.get(key, -999)) < cooldown_laps


def _start_cooldown(key: str, current_lap: int) -> None:
    """Record that this event fired on current_lap."""
    _cooldowns[key] = current_lap


def reset_cooldowns() -> None:
    """
    Clear all per-event cooldown state.

    Call this at race start and in unit tests so each test begins with a
    clean slate. NOT called between stints — gap and pit-window cooldowns
    should carry over naturally (you don't want an attack alert 1 lap after
    pitting just because the cooldown was cleared).
    """
    _cooldowns.clear()


# Minimum laps on current set before an SC pit call is meaningful.
# Prevents calling BOX under SC if we just pitted 1-4 laps ago.
SC_MIN_TYRE_AGE = 5            # laps

# Endgame phase thresholds.
#
# ENDGAME_LAP_THRESHOLD — laps remaining at which the system switches from
# strategy mode to survival mode. Track position becomes the primary concern
# and pit recommendations are suppressed unless the tyre is truly finished.
#
# ENDGAME_CRITICAL_TYRE — below this tyre life, even in endgame we allow a
# pit call. The car cannot physically finish on a critically worn tyre, so
# track position must be sacrificed. Above this threshold we stay out.
ENDGAME_LAP_THRESHOLD = 10     # laps remaining
ENDGAME_CRITICAL_TYRE = 15.0   # % tyre life — below this, endgame override is lifted


def _get_race_phase(laps_remaining: int, total_laps: int) -> str:
    """
    Classify the current point in the race into one of three phases.

    early   — first ~35% of the race; tyre optimisation, no urgency on pit timing.
    mid     — main racing phase; aggressive strategy, undercut windows, pit planning.
    endgame — final ENDGAME_LAP_THRESHOLD laps; track position priority,
              pit recommendations suppressed unless tyre is critically worn.

    WHY A FUNCTION AND NOT INLINE LOGIC:
    The phase classification is used in both get_event() and can be read by
    strategy_tracker via the returned event dict. Centralising it here means
    any future phase boundary changes are made in one place.

    Args:
        laps_remaining: Laps left in the race.
        total_laps:     Total scheduled race distance.

    Returns:
        "early" | "mid" | "endgame"
    """
    if laps_remaining <= ENDGAME_LAP_THRESHOLD:
        return "endgame"
    progress = (total_laps - laps_remaining) / max(total_laps, 1)
    if progress < 0.35:
        return "early"
    return "mid"


def get_event(race_state: dict) -> dict:
    """
    Analyse the current race_state and return an event recommendation.

    Args:
        race_state: Clean race_state dict from RaceStateManager.

    Returns:
        Event dict containing:
            - urgency            : 'green' | 'yellow' | 'red'
            - should_pit         : True if pit stop is recommended this lap
            - reason             : Human-readable explanation string
            - laps_left_on_tyre  : Estimated laps remaining on current set
            - fuel_laps_remaining: Estimated laps of fuel remaining
            - safety_car         : True if safety car is currently deployed
            - race_phase         : 'early' | 'mid' | 'endgame'
            - endgame_override   : True when a pit was suppressed due to race phase;
                                   signals strategy_tracker to fire ENDGAME_MANAGE
    """
    tire_life     = race_state["tire_wear"]
    tire_compound = race_state["tire_compound"]
    tire_age      = race_state["tire_age_laps"]
    fuel          = race_state["fuel"]
    laps_left     = race_state["laps_remaining"]
    gap_ahead     = race_state["gap_ahead"]
    gap_behind    = race_state["gap_behind"]
    fuel_per_lap  = race_state["fuel_per_lap"]
    track_status  = race_state.get("track_status", "green")   # "green" | "safety_car" | "virtual_safety_car"
    safety_car    = track_status in ("safety_car", "virtual_safety_car")

    # --- Compound reference tables (shared across all checks below) ---
    # Expected stint length before the tyre drops off — used for pit window
    # timing and for deciding whether the undercut window is open.
    pit_window_age = {"Soft": 15, "Medium": 25, "Hard": 38}
    expected_stint = pit_window_age.get(tire_compound, 25)

    # Maximum laps before compound is completely dead
    max_laps_by_compound = {"Soft": 20, "Medium": 35, "Hard": 50}
    compound_max = max_laps_by_compound.get(tire_compound, 30)

    # --- Estimate laps remaining on this tyre set ---
    # Use actual measured wear rate rather than fixed compound estimates.
    # If we've done 10 laps and worn 40% life, wear rate = 4% per lap,
    # so we have roughly (remaining life / wear_rate) laps left.

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

    # Pit window by stint age — fires when you've been on the set long enough,
    # regardless of remaining tyre life. This is the proactive "time to box" call
    # that the AI cannot make on its own from conversation memory.
    # Only fires if nothing more urgent has already been flagged.
    #
    # WHY STINT AGE AND NOT JUST TYRE LIFE:
    # Medium tyres can still show 70% life at lap 28 if wear rate is slow.
    # The tyre life thresholds above would never trigger. But from a strategy
    # perspective, lap 25-28 on Mediums IS the pit window — the tyres are about
    # to drop off a cliff even if they look fine on paper. Stint age is more
    # reliable than remaining life for deciding WHEN to pit.
    if (tire_age >= expected_stint
            and urgency == "green"
            and laps_left > 5):
        reasons.append(
            f"pit window open, {tire_age} laps on {tire_compound} — recommend boxing this lap"
        )
        urgency = "yellow"
        should_pit = True

    # Safety car pit opportunity — the most strategically important override.
    #
    # WHY THIS TAKES PRIORITY OVER TYRE LIFE:
    # A safety car compresses the field and neutralises the pit stop time loss.
    # In a real race this is almost always the correct moment to box, regardless
    # of what the tyre life number says. A "free" pit stop under SC can turn a
    # two-stop into a one-stop or gain track position over rivals who stay out.
    #
    # CONDITIONS: SC must be deployed, tyres must be at least SC_MIN_TYRE_AGE laps
    # old (prevents calling BOX if we just pitted 1-4 laps ago), and there must be
    # enough laps left for fresh rubber to be worthwhile.
    if safety_car and tire_age >= SC_MIN_TYRE_AGE and laps_left > 8:
        # SC/VSC overrides — a reduced pit stop time loss window.
        # If urgency is already red (critical tyres), keep it red but
        # prepend the SC reason so the driver knows why the call is urgent.
        sc_label = "virtual safety car" if track_status == "virtual_safety_car" \
                   else "safety car"
        sc_reason = (
            f"{sc_label} deployed — free pit window open, "
            f"{tire_age} laps on {tire_compound} at {tire_life:.0f}% life"
        )
        reasons.insert(0, sc_reason)
        if urgency != "red":
            urgency = "yellow"

        if track_status == "virtual_safety_car":
            # VSC: smaller time-loss reduction than a full SC.
            # Only recommend pitting if we are close to the natural pit window
            # OR the tyre is already significantly worn.
            # Above those thresholds the pit stop doesn't save enough time
            # to justify the track position loss — stay out and hold delta.
            near_pit_window = tire_age >= expected_stint - 2
            tyre_low        = tire_life < 35.0
            should_pit      = near_pit_window or tyre_low
        else:
            # Full SC: field compresses, pit loss neutralised — always box.
            should_pit = True

    # Gap alerts — only fire once the race has properly started (tire_age >= 1).
    # Before that, gap values are 0.0 defaults from the UDP listener init,
    # which would trigger a false "attack window" alert on lap 0.
    if tire_age >= 1:
        # Gap ahead <= 1.0s = attack window, get within DRS range
        if gap_ahead <= 1.0 and gap_ahead > 0.0 and urgency == "green":
            reasons.append(f"attack window open, car ahead only {gap_ahead}s away")
            urgency = "yellow"

        # Gap behind <= 1.0s = under pressure, risk of being passed
        if gap_behind <= 1.0 and gap_behind > 0.0 and urgency == "green":
            reasons.append(f"car behind closing, only {gap_behind}s behind, defend position")
            urgency = "yellow"

    # Endgame race phase override.
    #
    # WHY THIS RUNS AFTER ALL OTHER RULES:
    # We let normal tyre/fuel/gap rules compute their natural verdicts first.
    # This gives us an honest picture of the car's condition (urgency, reasons).
    # The override then decides whether to ACT on that verdict (pit) or suppress
    # it and switch to survival mode. Separating evaluation from action prevents
    # the normal rules from needing any awareness of race phase.
    #
    # WHY SAFETY CAR IS EXCLUDED:
    # An SC window neutralises the 22-second pit stop time loss. A "free" pit
    # under safety car is strategically valid even at 9 laps remaining.
    # Suppressing it would actively harm the driver's race position.
    #
    # WHY CRITICAL TYRE IS EXCLUDED:
    # Below ENDGAME_CRITICAL_TYRE (15%) the car is at genuine risk of a
    # blow-out or loss of control. Track position cannot outweigh safety.
    # We lift the override and let the normal pit recommendation stand.
    race_phase       = _get_race_phase(laps_left, race_state.get("total_laps", 58))
    endgame_override = False

    if (race_phase == "endgame"
            and tire_life >= ENDGAME_CRITICAL_TYRE
            and not safety_car
            and should_pit):
        should_pit       = False
        endgame_override = True
        # Downgrade from red to yellow — the situation is worth monitoring
        # and communicating, but no longer demands an immediate pit call.
        if urgency == "red":
            urgency = "yellow"
        # Replace pit-focused reasons with tyre management guidance.
        # The full context is preserved so the AI can brief the driver correctly.
        reasons = [
            f"endgame mode — {laps_left} laps remaining, "
            f"managing {tire_compound} tyres at {tire_life:.0f}% to the flag — no stop"
        ]

    # Final override — 3 laps or fewer remaining.
    # At this point no pit stop can recover the time loss. Bring it home
    # regardless of tyre condition (unless tyre is critically worn, which
    # the endgame block above would not have suppressed anyway).
    if laps_left <= 3 and tire_life > 20:
        reasons = ["too late to pit, bring it home"]
        should_pit = False
        urgency = "green"

    reason_str = "; ".join(reasons) if reasons else "tyres and fuel nominal"

    return {
        "urgency":             urgency,
        "should_pit":          should_pit,
        "reason":              reason_str,
        "laps_left_on_tyre":   laps_left_on_tyre,
        "fuel_laps_remaining": fuel_laps_remaining,
        "safety_car":          safety_car,
        "race_phase":          race_phase,
        "endgame_override":    endgame_override,
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

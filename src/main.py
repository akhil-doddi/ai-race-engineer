"""
src/main.py

Entry point and runtime orchestrator for the AI Race Engineer system.

ARCHITECTURE — TWO INDEPENDENT THREADS + TELEMETRY CONTROLLER:

  TelemetryController
    └── wraps UDPListener / Simulator
    └── contains PitStateMachine
    └── overrides tyre fields during pit stop phases

  Thread A — PROACTIVE MONITOR (runs every 1.0 second):
    Telemetry → Controller.get_snapshot() → Race State → Event → AI speaks
    Also watches: tyre_life < 30% → auto-trigger pit
                  track_status change → SC announcement within ~1s

  Thread B — REACTIVE INPUT (main thread, blocks on driver input):
    Driver text/voice → AI → voice output
    Also detects: "box"/"pit" keywords → trigger pit simulation

HOW THE PIT SIMULATION WORKS:
  1. Any BOX call (proactive or reactive) calls controller.trigger_pit().
  2. Controller.PitStateMachine transitions: RACING → PIT_ENTRY → PIT_STOP
     → PIT_EXIT → RACING.
  3. During PIT_STOP and PIT_EXIT, get_snapshot() returns fresh tyre data
     (tire_wear=100, age=0, new compound) instead of the raw source data.
  4. The raw telemetry generator (udp_sender) keeps running unchanged.
  5. When pit completes, on_pit_complete fires → StrategyTracker resets
     so it monitors the new stint correctly.

HOW TO RUN:
    python3 -m src.main
"""

import threading
import time

from src.telemetry.simulator            import TelemetrySimulator
from src.telemetry.udp_listener         import UDPTelemetryListener
from src.telemetry.telemetry_controller import TelemetryController
from src.race_state.state_manager       import build_race_state
from src.events.event_detector          import (
    get_event, format_alert, ENDGAME_LAP_THRESHOLD,
    reset_cooldowns, _on_cooldown, _start_cooldown, COOLDOWN_GAP_ALERT,
)
from src.strategy.strategy_tracker      import StrategyTracker
from src.communication.response_generator import ask_engineer
from src.voice.tts_engine               import speak
from src.voice.voice_input              import listen


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

_history_lock = threading.Lock()

# Keywords in driver input that should trigger pit simulation immediately.
# Detected in the reactive path so typing "I'm boxing" also works.
_PIT_KEYWORDS = {"box", "pit", "boxing", "pitting", "boxed", "pitted"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_driver_input(mode: str) -> str:
    if mode == "text":
        return input("⌨️  You: ").strip()
    return listen()


def _driver_wants_to_pit(text: str) -> bool:
    """Return True if any pit keyword appears in the driver's input."""
    words = set(text.lower().split())
    return bool(words & _PIT_KEYWORDS)


def speak_proactive(
    trigger: str,
    prompt: str,
    race_state: dict,
    history: list,
    tracker: StrategyTracker,
    controller: TelemetryController,
) -> list:
    """
    Fire a proactive engineer briefing for the given trigger.

    After speaking, any box-type trigger also fires the pit simulation
    via controller.trigger_pit(). This is the feedback path: AI decision
    → telemetry state change.

    NOTE: We do NOT call tracker.reset_pit() here.
    reset_pit() is called exclusively by the on_pit_complete callback when
    the pit simulation physically finishes (PIT_EXIT → RACING transition).
    Calling it here — before the pit is done — would clear _pit_called back
    to False, allowing the same BOX trigger to fire again on the next poll
    while the car is still in the pit lane.
    The proactive_monitor guards against this with `if not controller.is_pitting`
    around tracker.evaluate(), but keeping reset_pit() only in on_pit_complete
    makes the invariant explicit and avoids the race entirely.
    """
    label = {
        "INITIAL_BRIEF":        "📋 RACE BRIEF",
        "PLAN_CHANGED":         "📋 STRATEGY UPDATE",
        "PIT_APPROACHING":      "⚠️  PIT IN 3 LAPS",
        "PIT_NOW":              "🔴 BOX BOX BOX",
        "SC_OPPORTUNITY":       "🟡 SAFETY CAR — FREE PIT WINDOW",
        "VSC_OPPORTUNITY":      "🟡 VIRTUAL SAFETY CAR — ADVISORY",
        "ENDGAME_MANAGE":       "🏁 ENDGAME — TYRE MANAGEMENT",
        "FINISH_RACE":          "🏁 FINISH — NO MORE STOPS",
        "FUEL_SAVE":            "⛽ FUEL MANAGEMENT",
        "PUSH_MODE":            "🏎️  PUSH — CLOSING ON CAR AHEAD",
        "POSITION_GAINED":      "📈 POSITION GAINED",
        "POSITION_LOST":        "📉 POSITION LOST",
        "DRS_ENABLED":          "💨 DRS AVAILABLE",
    }.get(trigger, "📋 ENGINEER")

    print(f"\n{label}")
    # Engineer availability is state-based, not response-based.
    # A single network hiccup should not silence the engineer permanently.
    # Strategy: attempt the AI call up to MAX_RETRIES times with a short
    # pause between attempts. On the first failure show "Standby..." so the
    # driver knows the engineer is still present and working. Only skip the
    # trigger entirely if all retries are exhausted — the next lap's poll
    # will attempt the same class of evaluation fresh.
    _MAX_RETRIES = 2
    for attempt in range(_MAX_RETRIES):
        try:
            reply, history = ask_engineer(prompt, race_state, history)
            print(f"📻 Engineer: {reply}\n")
            speak(reply)
            break   # success — exit retry loop
        except Exception as exc:
            if attempt < _MAX_RETRIES - 1:
                print(f"⏳ Standby — analysing strategy...\n")
                time.sleep(3.0)
            else:
                # All retries exhausted. Continue silently — the proactive
                # monitor is still running and will re-evaluate next lap.
                print(f"📻 Engineer: [no response — continuing]\n")
                return history

    # Trigger pit simulation for any BOX call.
    # tracker.reset_pit() is intentionally NOT called here — see docstring.
    if trigger in ("PIT_NOW", "SC_OPPORTUNITY"):
        controller.trigger_pit(race_state["tire_compound"])

    return history


# ---------------------------------------------------------------------------
# Thread A — Proactive monitor
# ---------------------------------------------------------------------------

def proactive_monitor(
    controller: TelemetryController,
    tracker: StrategyTracker,
    history: list,
    stop_event: threading.Event,
    auto_pit_state: dict,
):
    """
    Polls telemetry every 1.0 second on its own thread.

    WHY 1.0s (down from 3.0s):
    Safety car events must be announced within ~1 second of deployment.
    At 3s poll intervals, a SC could be missed for 3+ seconds. At 1s,
    worst-case delay is 1 second — acceptable for radio communication.
    The proactive thread is lightweight (one dict read + fast comparisons),
    so polling at 1s costs almost nothing.

    AUTO-PIT TRIGGERS (no driver input required):
      1. tyre_life < 30%  → trigger pit immediately (compound wears out)
      2. urgency → red AND should_pit → trigger pit (event_detector confirms)
      3. SC_OPPORTUNITY fires → trigger pit (free stop under safety car)

    auto_pit_state is a shared dict {"triggered": bool} passed in from main()
    so the on_pit_complete callback can reset it between stints.
    """
    last_urgency:      str   = "green"
    last_track_status: str   = "green"
    race_finished:     bool  = False

    while not stop_event.is_set():

        # ── Snapshot → Race State ────────────────────────────────────────────
        # controller.get_snapshot() calls tick() on the PitStateMachine
        # and applies any active overrides before returning.
        raw        = controller.get_snapshot()
        race_state = build_race_state(raw)

        tyre_life   = race_state["tire_wear"]
        compound    = race_state["tire_compound"]
        track       = race_state["track_status"]

        # ── Race finish ──────────────────────────────────────────────────────
        if not race_finished and race_state["laps_remaining"] == 0:
            race_finished = True
            pos = race_state["position"]
            speak(f"Chequered flag. P{pos}, race complete. Fantastic job.")
            print(f"\n🏁 RACE COMPLETE — P{pos}\n")

        if race_finished:
            stop_event.wait(timeout=1.0)
            continue

        # ── Auto-pit: tyre life < 50% ────────────────────────────────────────
        # Fires once per stint. auto_pit_state["triggered"] is reset by
        # on_pit_complete() when the pit simulation finishes, so this fires
        # correctly for every stint — not just the first one.
        #
        # ENDGAME GUARD: suppressed when laps_remaining <= ENDGAME_LAP_THRESHOLD.
        # In the final 10 laps, track position is worth more than fresh rubber.
        # The event_detector endgame override handles the communication — this
        # guard just stops the automatic pit trigger from firing regardless.
        # If the tyre deteriorates to a genuinely critical level (<15%) in
        # endgame, the urgency-change handler below will catch it (because
        # event_detector does NOT suppress should_pit for critically worn tyres).
        if (not auto_pit_state["triggered"]
                and not controller.is_pitting
                and tyre_life < 50.0
                and race_state["laps_remaining"] > ENDGAME_LAP_THRESHOLD):
            auto_pit_state["triggered"] = True
            print(f"\n⚠️  AUTO-PIT — tyre life {tyre_life:.0f}% below 50% threshold")
            controller.trigger_pit(compound)
            tracker.reset_pit()

        # ── Safety car: fast detection ───────────────────────────────────────
        # track_status is polled every 1.0s. When it changes, announce
        # immediately — do not wait for the slow strategy evaluation below.
        if track != last_track_status:
            last_track_status = track
            if track == "safety_car":
                print("\n🟡 SAFETY CAR DEPLOYED — announcing")
            elif track == "virtual_safety_car":
                print("\n🟡 VIRTUAL SAFETY CAR — announcing")
            else:
                print("\n🟢 SAFETY CAR IN — announcing")

        # ── Event detection (urgency change) ─────────────────────────────────
        event = get_event(race_state)

        if event["urgency"] != last_urgency:
            # Update last_urgency FIRST so it always tracks the true urgency level.
            # This is critical for gap-alert cooldown: if a gap alert is suppressed
            # below, last_urgency becomes yellow and stays there for as long as the
            # gap persists — preventing any new green→yellow transitions until the
            # gap genuinely clears. Without this, updating last_urgency after the
            # speak block would reset it to green on every cooldown-suppressed poll.
            last_urgency = event["urgency"]

            # Skip all urgency-change announcements while the car is in the pit lane.
            # During PIT_ENTRY the raw UDP data passes through (0% tyre wear), which
            # causes spurious red-urgency changes that would speak "box box box" while
            # we are already stationary. The pit sim handles comms during this window.
            if not controller.is_pitting:
                alert_text = format_alert(event)
                if alert_text:
                    # Gap alert cooldown — prevents the engineer repeating "attack window"
                    # or "car behind" every time the gap oscillates back above 1.0s and
                    # then drops below it again.
                    #
                    # WHY THE COOLDOWN LIVES HERE AND NOT IN get_event():
                    # If get_event() returned urgency=green during cooldown, last_urgency
                    # would reset to green on the very next poll — creating a new
                    # green→yellow transition every time the cooldown expires (3 laps).
                    # That made alerts MORE frequent, not less. By suppressing only the
                    # SPEAK decision here (while last_urgency is already updated above),
                    # the urgency stays correctly at yellow throughout the gap window and
                    # no new transition fires until the gap genuinely clears.
                    current_lap = race_state.get("lap", 1)
                    reason      = event.get("reason", "")
                    is_gap_alert = (
                        "attack window" in reason or "car behind closing" in reason
                    )
                    if is_gap_alert and _on_cooldown("gap_alert", current_lap, COOLDOWN_GAP_ALERT):
                        # Cooldown active — engineer stays silent this lap.
                        pass
                    else:
                        if is_gap_alert:
                            _start_cooldown("gap_alert", current_lap)
                        print(alert_text)
                        spoken = event["reason"].replace(";", ",").replace("  ", " ")
                        # SC session guard: one pit decision per safety car session.
                        #
                        # Two independent paths can trigger a pit under SC:
                        #   Path A — tracker.evaluate() fires SC_OPPORTUNITY, which sets
                        #             _sc_pit_called=True inside evaluate() itself.
                        #   Path B — this urgency-change handler fires when urgency
                        #             jumps to yellow/red with should_pit=True.
                        #
                        # Without coordination, Path B could fire a second SC pit 5 laps
                        # after Path A (or itself) because tire_age crosses SC_MIN_TYRE_AGE
                        # again and urgency changes again. tracker.mark_sc_pit_used() writes
                        # _sc_pit_called=True so that evaluate() returns [] for the rest of
                        # this SC session, AND pit_prompted_during_sc is True so this same
                        # check blocks correctly on the next urgency-change iteration.
                        sc_pit_already_used = (
                            event.get("safety_car", False) and tracker.pit_prompted_during_sc
                        )
                        # VSC advisory — never auto-pit via urgency-change path.
                        # VSC_OPPORTUNITY (in the tracker path below) handles comms.
                        # Even if should_pit=True under VSC, we leave the decision
                        # to the driver — the advisory tells them the option exists.
                        is_vsc = race_state.get("track_status") == "virtual_safety_car"
                        if (event["should_pit"]
                                and not event.get("endgame_override", False)
                                and not sc_pit_already_used
                                and not is_vsc):
                            # Normal or first-SC pit call — box the car.
                            speak(f"Box box box. {spoken}")
                            controller.trigger_pit(compound)
                            tracker.reset_pit()
                            auto_pit_state["triggered"] = True   # suppress 50% double-trigger
                            # If this pit was triggered under SC, mark the session as used
                            # so no further pit calls fire until safety car comes in.
                            if event.get("safety_car", False):
                                tracker.mark_sc_pit_used()
                        else:
                            # Non-pit alert, endgame suppression, or SC session already used.
                            speak(f"Strategy alert. {spoken}")

        # ── Strategy tracker (proactive pit plan) ────────────────────────────
        # GUARD: skip evaluation while the pit simulation is running.
        #
        # WHY THIS GUARD IS CRITICAL:
        # tracker.evaluate() can fire BOX triggers (PIT_NOW).
        # When a BOX trigger fires, speak_proactive()
        # calls controller.trigger_pit() which starts the pit simulation.
        # Without this guard, the next 1-second poll would call evaluate()
        # again while controller.is_pitting == True, and since _pit_called
        # was reset by speak_proactive, the same BOX trigger would fire a
        # second time — producing a duplicate "box box box" call while the
        # car is already in the pit lane.
        #
        # Skipping evaluation while pitting is safe because:
        #   - The car is physically in the pit lane — no strategy decisions needed.
        #   - on_pit_complete() resets the tracker cleanly when the stop finishes.
        #   - SC detection and urgency changes still run (they are above this guard).
        if not controller.is_pitting:
            proactive_triggers = tracker.evaluate(race_state, event)
            for trigger in proactive_triggers:
                prompt = tracker.build_prompt(trigger, race_state, event)
                with _history_lock:
                    history[:] = speak_proactive(
                        trigger, prompt, race_state, history, tracker, controller
                    )

        stop_event.wait(timeout=1.0)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    print("🏎️  AI Race Engineer — starting up...")
    print("─" * 42)

    # --- Telemetry source ---
    raw_telem = input(
        "Telemetry source — 's' for simulator, 'u' for UDP (PS5/sender): "
    ).strip().lower()

    if raw_telem == "u":
        raw_source = UDPTelemetryListener()
        print("📡 UDP mode — start udp_sender.py in a second terminal if no PS5.")
    else:
        raw_source = TelemetrySimulator()
        print("📡 Simulator mode — no external hardware required.")

    # Wrap in TelemetryController — this is the only line that changed
    # relative to the old architecture. Everything above sees `controller`
    # instead of the raw source, gaining pit simulation for free.
    controller = TelemetryController(raw_source)
    controller.start()

    # --- Input mode ---
    raw_mode = input("Input mode — 'v' for voice, 't' for text: ").strip().lower()
    mode = "voice" if raw_mode == "v" else "text"
    print(f"{'🎙️  Voice' if mode == 'voice' else '⌨️  Text'} mode selected.")
    print("─" * 42)
    print("Engineer is monitoring telemetry. Speak when ready.\n")

    # --- Session state ---
    history    = []
    stop_event = threading.Event()
    tracker    = StrategyTracker()
    reset_cooldowns()   # clear any stale cooldown state from a previous session

    # on_pit_complete: fired by the controller when PIT_EXIT → RACING transition
    # completes. Resets the auto-pit flag via a closure so the next stint's
    # auto-trigger works correctly.
    #
    # NOTE: This flag is declared here and captured by the closures below.
    # Python closures capture by reference, so modifying auto_pit_triggered
    # inside proactive_monitor (a local variable there) won't update this flag.
    # Instead we store a mutable container so the callback can reset it.
    _auto_pit_state = {"triggered": False}

    def on_pit_complete():
        """Called when the full pit sequence finishes. Resets stint monitoring."""
        _auto_pit_state["triggered"] = False
        tracker.reset_pit()
        print("ℹ️  Pit complete — monitoring fresh stint\n")

    controller.on_pit_complete = on_pit_complete

    # --- Proactive monitor thread ---
    monitor_thread = threading.Thread(
        target=proactive_monitor,
        args=(controller, tracker, history, stop_event, _auto_pit_state),
        daemon=True,
        name="ProactiveMonitor",
    )
    monitor_thread.start()

    # --- Reactive input loop (main thread) ---
    try:
        while True:
            driver_input = get_driver_input(mode)
            if not driver_input:
                continue

            if mode == "voice":
                print(f"🎙️  Driver: {driver_input}")

            # Refresh state right before AI call
            race_state = build_race_state(controller.get_snapshot())

            # ── Reactive pit trigger ─────────────────────────────────────────
            # If driver says "box" / "pit" / "boxing" etc. and we're not
            # already pitting, start the pit simulation immediately.
            # This gives the driver agency alongside the automatic triggers.
            if _driver_wants_to_pit(driver_input) and not controller.is_pitting:
                compound = race_state["tire_compound"]
                triggered = controller.trigger_pit(compound)
                if triggered:
                    tracker.reset_pit()
                    _auto_pit_state["triggered"] = True

            print("⏳ Thinking...")
            with _history_lock:
                reply, history[:] = ask_engineer(driver_input, race_state, history)

            print(f"📻 Engineer: {reply}\n")
            speak(reply)

    except KeyboardInterrupt:
        print("\n🛑 Session ended. Shutting down race engineer...")
        stop_event.set()
        monitor_thread.join(timeout=5)
        controller.stop()


if __name__ == "__main__":
    main()

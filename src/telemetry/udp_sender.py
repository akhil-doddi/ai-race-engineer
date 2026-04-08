"""
src/telemetry/udp_sender.py

PS5 stand-in: sends real F1 24 UDP packets to localhost so you can test
the full system without owning the PS5 game.

HOW IT WORKS:
This script simulates a car progressing through a race — tyre wear increases,
fuel burns, lap times change — and broadcasts that data as genuine F1 24 UDP
packets. The udp_listener.py on the other side cannot tell the difference
between these packets and packets from the real PS5 game.

WHY THIS IS USEFUL:
Professional teams building telemetry tools never write code against live
hardware. They build a packet simulator first, develop and test against it,
then connect the real hardware only at the end. This lets you:
  1. Develop on a plane, train, cafe — no PS5 required.
  2. Create extreme scenarios (5% tyre life, 2 laps of fuel) on demand.
  3. Run automated tests against the listener without any real game session.

HOW TO USE:
  Terminal 1 (listener + AI engineer):  python3 -m src.main   → choose 'u' for UDP
  Terminal 2 (this sender):             python3 -m src.telemetry.udp_sender

PACKET TYPES SENT:
  - PacketSessionData    (ID 1)  — total laps, once per ~5 seconds
  - PacketLapData        (ID 2)  — lap, position, times, gaps, every ~2 seconds
  - PacketCarTelemetryData (ID 6) — speed, gear, DRS, every ~2 seconds
  - PacketCarStatusData  (ID 7)  — fuel, compound, tyre age, every ~2 seconds
  - PacketCarDamageData  (ID 10) — tyre wear, every ~2 seconds

FORMAT GUARANTEE:
Every packet uses the exact byte layout from the Codemasters F1 24 UDP spec.
The listener reads these bytes using the same struct formats it will use with
the real PS5 game.
"""

import socket
import struct
import time
import random
import argparse

# ---------------------------------------------------------------------------
# Struct format strings — must match udp_listener.py exactly.
# ---------------------------------------------------------------------------

HEADER_FMT       = "<HBBBBBQfIIBB"
LAP_DATA_FMT     = "<IIHBHBHBHBfffBBBBBBBBBBBBBBBHHBfB"
CAR_TELEMETRY_FMT = "<HfffBbHBBHHHHHBBBBBBBBHffffBBBB"
CAR_STATUS_FMT   = "<BBBBBfffHHBBHBBBbHHfBfffB"
CAR_DAMAGE_FMT   = "<ffff22B"

# Packet IDs — matches the spec's packetId field.
PACKET_ID_SESSION      = 1
PACKET_ID_LAP_DATA     = 2
PACKET_ID_CAR_TELEMETRY = 6
PACKET_ID_CAR_STATUS   = 7
PACKET_ID_CAR_DAMAGE   = 10

# Visual tyre compound codes (F1 24 spec)
COMPOUND_SOFT   = 16
COMPOUND_MEDIUM = 17
COMPOUND_HARD   = 18

# Send to localhost (same machine). Port matches game default and settings.py.
TARGET_HOST = "127.0.0.1"
TARGET_PORT = 20777


# ---------------------------------------------------------------------------
# Race state — this is what we're simulating
# ---------------------------------------------------------------------------

class RaceSimState:
    """
    Tracks a simulated F1 car's state through a race.
    This is separate from TelemetrySimulator — it doesn't need threading
    because we control exactly when each update fires.

    TYRE MODEL (matches simulator.py):
      - Accumulated wear tracked per corner (0 = new tyre, 100 = completely worn)
      - Cliff effect: wear rate doubles once avg accumulated wear exceeds 70%
      - Safety car: wear drops to 0.3–0.8%/lap during SC period

    SAFETY CAR / VSC:
      - Two separate deployment windows, decided at race start, unknown to AI.
      - VSC (40% chance): deploys laps 3–9, lasts 2–3 laps.
        At this early stage tyres are fresh — no pit warranted.
        AI should brief: maintain delta, no stop.
      - Full SC (60% chance): deploys laps 33–43, lasts 3–6 laps.
        Tyres are old enough that a free pit under SC is the correct call.
        AI should brief: box box box.
      - During SC/VSC: gaps compress, lap times slow, track_status set accordingly.
    """

    def __init__(self, total_laps: int = 53, starting_position: int = 8):
        self.total_laps       = total_laps
        self.lap              = 1
        self.position         = starting_position

        # Tyre state — starts fresh (0 = new, 100 = fully worn)
        # Note: the listener converts these to life_remaining = 100 - avg_wear
        self.compound_code    = COMPOUND_MEDIUM
        self.tyre_age_laps    = 0
        self.tyre_wear_fl     = 0.0   # % wear accumulated per corner
        self.tyre_wear_fr     = 0.0
        self.tyre_wear_rl     = 0.0
        self.tyre_wear_rr     = 0.0

        # Fuel — burns at ~1.89 kg/lap for 53 laps (same as simulator.py)
        self.fuel_kg          = 100.0
        self.fuel_per_lap     = round(100.0 / total_laps, 2)

        # Lap times
        self.base_lap_secs    = 92.0   # 1:32.000
        self.last_lap_ms      = int(self.base_lap_secs * 1000)
        self.best_lap_ms      = self.last_lap_ms

        # Gaps in milliseconds (stored as ms for packet packing)
        self.gap_ahead_ms     = int(random.uniform(1000, 4000))
        self.gap_behind_ms    = int(random.uniform(1000, 4000))

        # Track condition — mirrors simulator.py field
        self.track_status     = "green"   # "green" | "safety_car" | "virtual_safety_car"

        # Safety car / VSC timing — decided at race start, unknown to AI.
        # Type chosen first (60% SC / 40% VSC), then deployment lap and duration
        # are picked from separate windows so the AI sees different scenarios:
        #   VSC window  : laps 3–9   — tyres fresh, no pit warranted
        #   Full SC window: laps 33–43 — tyres old, free pit is correct strategy
        self._sc_type         = random.choices(
            ["safety_car", "virtual_safety_car"], weights=[60, 40]
        )[0]
        if self._sc_type == "virtual_safety_car":
            self._sc_deploy_lap = random.randint(3, 9)
            sc_duration         = random.randint(2, 3)
        else:
            self._sc_deploy_lap = random.randint(33, 43)
            sc_duration         = random.randint(3, 6)
        self._sc_end_lap      = self._sc_deploy_lap + sc_duration

        # Instantaneous car state
        self.speed            = 220   # km/h
        self.gear             = 5
        self.drs              = 0

        # Frame counter — increments each packet send
        self.frame            = 0
        self.session_uid      = random.randint(10**15, 10**16)
        self.session_time     = 0.0

    def _wear_this_lap(self) -> float:
        """
        Calculate per-corner tyre wear for this lap using the cliff model.

        Matches simulator.py _tyre_wear_this_lap() logic exactly:
          - Safety car: 0.3–0.8% (slow speeds, gentle cornering)
          - Normal phase (avg_wear ≤ 70%): base rates per compound
          - Cliff phase  (avg_wear  > 70%): base × 1.5–2.5× multiplier
        """
        if self.track_status == "safety_car":
            return random.uniform(0.3, 0.8)

        # Base wear rates per compound (% accumulated per lap, per corner)
        base_rates = {
            COMPOUND_SOFT:   random.uniform(4.5, 6.5),
            COMPOUND_MEDIUM: random.uniform(3.0, 4.5),
            COMPOUND_HARD:   random.uniform(1.5, 2.5),
        }
        base = base_rates.get(self.compound_code, 3.5)

        # Cliff effect: once more than 70% has been used, wear rate spikes
        avg_wear = (self.tyre_wear_fl + self.tyre_wear_fr +
                    self.tyre_wear_rl + self.tyre_wear_rr) / 4
        if avg_wear > 70:
            # life_remaining < 30% → cliff in simulator terms
            life_remaining = 100 - avg_wear
            cliff_multiplier = 1.5 + ((30 - life_remaining) / 30) * 1.0
            base *= cliff_multiplier

        return base

    def _lap_time_secs(self) -> float:
        """
        Derive a realistic lap time matching simulator.py _calculate_lap_time():
          - Safety car laps are ~25% slower
          - Tyre degradation: linear penalty up to cliff, accelerating beyond
          - Fuel benefit: lighter car = faster lap
          - Dirty air penalty: +0.2–0.5s when gap_ahead < 1.0s
        """
        if self.track_status == "safety_car":
            return round(self.base_lap_secs * 1.25 + random.uniform(-0.5, 1.0), 3)

        avg_wear   = (self.tyre_wear_fl + self.tyre_wear_fr +
                      self.tyre_wear_rl + self.tyre_wear_rr) / 4
        tire_life  = 100 - avg_wear   # life remaining (same frame as simulator)

        if tire_life >= 30:
            wear_penalty = ((100 - tire_life) / 10) * 0.3
        else:
            normal_phase      = (70 / 10) * 0.3
            cliff_contribution = ((30 - tire_life) / 10) * 0.9
            wear_penalty = normal_phase + cliff_contribution

        fuel_benefit = ((100 - self.fuel_kg) / 10) * 0.06

        gap_ahead_s = self.gap_ahead_ms / 1000.0
        dirty_air = random.uniform(0.2, 0.5) if 0 < gap_ahead_s < 1.0 else 0.0

        variance = random.uniform(-0.15, 0.2)
        return round(self.base_lap_secs + wear_penalty - fuel_benefit + dirty_air + variance, 3)

    def advance_lap(self):
        """
        Progress the car forward by one lap.
        Updates: safety car status, tyre wear (cliff model), fuel, lap times,
        position, gaps, and instantaneous car state.
        """
        self.lap           += 1   # no cap — loop exits when lap > total_laps
        self.tyre_age_laps += 1

        # ── Safety car / VSC status ────────────────────────────────────────
        if self.lap == self._sc_deploy_lap:
            self.track_status = self._sc_type
            label = "VIRTUAL SAFETY CAR" if self._sc_type == "virtual_safety_car" \
                    else "SAFETY CAR"
            print(f"\n🟡 {label} DEPLOYED — Lap {self.lap}  (clears Lap {self._sc_end_lap})")
        elif self.lap == self._sc_end_lap + 1:
            self.track_status = "green"
            print(f"\n🟢 SAFETY CAR IN — Lap {self.lap}  (green flag)")

        # ── Tyre wear (cliff model) ────────────────────────────────────────
        wear = self._wear_this_lap()
        # Apply with slight corner variation (fronts wear faster than rears)
        self.tyre_wear_fl = min(100.0, self.tyre_wear_fl + wear * random.uniform(1.0, 1.2))
        self.tyre_wear_fr = min(100.0, self.tyre_wear_fr + wear * random.uniform(1.0, 1.2))
        self.tyre_wear_rl = min(100.0, self.tyre_wear_rl + wear * random.uniform(0.8, 1.0))
        self.tyre_wear_rr = min(100.0, self.tyre_wear_rr + wear * random.uniform(0.8, 1.0))

        # ── Fuel burn ─────────────────────────────────────────────────────
        self.fuel_kg = max(0.0, self.fuel_kg - self.fuel_per_lap)

        # ── Lap time ──────────────────────────────────────────────────────
        lap_secs         = self._lap_time_secs()
        self.last_lap_ms = int(lap_secs * 1000)
        if self.last_lap_ms < self.best_lap_ms:
            self.best_lap_ms = self.last_lap_ms

        # ── Position changes ───────────────────────────────────────────────
        # SC period: more frequent (field bunches, easier overtakes at restart)
        overtake_chance = 0.35 if self.track_status == "safety_car" else 0.15
        if random.random() < overtake_chance:
            self.position = max(1, min(20, self.position + random.choice([-1, -1, 1])))

        # ── Gaps ──────────────────────────────────────────────────────────
        if self.track_status == "safety_car":
            # Field compresses under SC — gaps shrink toward 800–1500ms
            target_ms = random.randint(800, 1500)
            self.gap_ahead_ms  = int(self.gap_ahead_ms  * 0.7 + target_ms * 0.3)
            self.gap_behind_ms = int(self.gap_behind_ms * 0.7 + target_ms * 0.3)
        else:
            # Green flag racing: gaps fluctuate naturally
            self.gap_ahead_ms  = max(100, self.gap_ahead_ms  + random.randint(-400, 400))
            self.gap_behind_ms = max(100, self.gap_behind_ms + random.randint(-400, 400))

        # ── Instantaneous car state ────────────────────────────────────────
        self.speed = random.randint(180, 320)
        self.gear  = random.randint(3, 8)
        self.drs   = random.choice([0, 1])

        # ── Timing ────────────────────────────────────────────────────────
        self.frame        += 1
        self.session_time += lap_secs


# ---------------------------------------------------------------------------
# Packet builders — pack Python values into the exact bytes the game sends
# ---------------------------------------------------------------------------

def _build_header(packet_id: int, player_idx: int, state: RaceSimState) -> bytes:
    """Pack a 29-byte PacketHeader."""
    return struct.pack(
        HEADER_FMT,
        2024,              # packetFormat
        24,                # gameYear
        1,                 # gameMajorVersion
        0,                 # gameMinorVersion
        1,                 # packetVersion
        packet_id,         # packetId — what type of packet this is
        state.session_uid, # sessionUID
        state.session_time,# sessionTime
        state.frame,       # frameIdentifier
        state.frame,       # overallFrameIdentifier
        player_idx,        # playerCarIndex — which car in the array is the player
        255,               # secondaryPlayerCarIndex (255 = no secondary player)
    )


def _build_session_packet(state: RaceSimState) -> bytes:
    """
    Packet ID 1 — PacketSessionData.

    We pack the minimum fields the listener needs:
      - totalLaps     (byte 3 after header)
      - safetyCarStatus (byte 124 after header)

    WHY THE LAYOUT CHANGED:
    The previous build repurposed weather byte = 1 to signal SC, and stopped
    after 104 bytes total. The listener now reads safetyCarStatus from its real
    position (offset 124 from after-header = byte 153 from packet start).
    We pad to that position so the listener's struct unpack lands on the right byte.

    safetyCarStatus values written:
      0 = green flag
      1 = full safety car
      2 = virtual safety car (VSC)
    """
    header = _build_header(PACKET_ID_SESSION, 0, state)

    # First 4 bytes after header: weather=0 (clear), trackTemp, airTemp, totalLaps
    # Weather is now honest (0 = clear sky) — SC is carried by safetyCarStatus below.
    early = struct.pack("<BbbB", 0, 30, 25, state.total_laps)

    # Pad from byte 4 to byte 123 (the 120 bytes of trackLength through marshalZones).
    # These fields are not read by the listener, so zeros are safe.
    mid_padding = bytes(120)

    # safetyCarStatus at offset 124 from after-header.
    if state.track_status == "virtual_safety_car":
        sc_byte = 2
    elif state.track_status == "safety_car":
        sc_byte = 1
    else:
        sc_byte = 0
    sc_status = struct.pack("<B", sc_byte)

    # Trailing padding — makes the packet a realistic size (real packet is ~644 bytes)
    # and ensures the listener's size check passes without needing to know exact length.
    tail_padding = bytes(50)

    return header + early + mid_padding + sc_status + tail_padding


def _build_lap_data_packet(state: RaceSimState) -> bytes:
    """
    Packet ID 2 — PacketLapData.
    Contains 22 LapData structs. We fill the player car's entry accurately;
    other cars get zeroed-out entries (no rival detail needed for testing).

    For the gap_behind calculation in the listener, we also fill in car position+1
    with a realistic delta so the listener can find our gap_behind.
    """
    player_idx = 0   # Always car index 0 for our simulated player
    header = _build_header(PACKET_ID_LAP_DATA, player_idx, state)

    # Compute laps remaining
    laps_remaining = state.total_laps - state.lap

    # Build a blank (zeroed) LapData for a car that doesn't exist.
    blank_lap = struct.pack(LAP_DATA_FMT,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0,     # times, deltas
        0.0, 0.0, 0.0,                      # distances, safetyCarDelta
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  # B fields
        0, 0,                               # pitLane times
        0, 0.0, 255,                        # pitPen, speedTrap, speedTrapLap
    )

    # Build the player's accurate LapData entry
    player_lap = struct.pack(LAP_DATA_FMT,
        state.last_lap_ms,      # lastLapTimeInMS
        0,                      # currentLapTimeInMS (not tracking mid-lap time)
        0, 0,                   # sector1TimeInMS, sector1TimeMinutes
        0, 0,                   # sector2TimeInMS, sector2TimeMinutes
        state.gap_ahead_ms,     # deltaToCarInFrontInMS — gap to car ahead
        0,                      # deltaToCarInFrontMinutes
        0, 0,                   # deltaToRaceLeaderInMS, Minutes
        0.0, 0.0, 0.0,          # lapDistance, totalDistance, safetyCarDelta
        state.position,         # carPosition
        state.lap,              # currentLapNum
        0,                      # pitStatus (0 = not pitting)
        0,                      # numPitStops
        2,                      # sector (0=s1, 1=s2, 2=s3)
        0,                      # currentLapInvalid
        0, 0, 0, 0, 0,          # penalties and warnings
        state.position,         # gridPosition (same as current for simplicity)
        4,                      # driverStatus (4 = on track)
        2,                      # resultStatus (2 = active)
        0, 0, 0, 0,             # pit lane timer fields
        250.0, 255,             # speedTrap, speedTrapLap
    )

    # Build a "car behind" entry so the listener can find gap_behind.
    # This car is at position+1 and has gap_behind_ms as its deltaToCarInFront.
    car_behind_lap = struct.pack(LAP_DATA_FMT,
        state.last_lap_ms + random.randint(500, 3000),  # slightly slower
        0, 0, 0, 0, 0,
        state.gap_behind_ms,    # deltaToCarInFrontInMS = gap between them and us
        0, 0, 0,
        0.0, 0.0, 0.0,
        state.position + 1,     # carPosition = player's position + 1
        state.lap,
        0, 0, 2, 0, 0, 0, 0, 0, 0, 0,
        state.position + 1,
        4, 2,
        0, 0, 0,
        240.0, 255,
    )

    # Assemble the 22-car array:
    # Slot 0 = player, slot 1 = car behind, rest = blank
    lap_array = player_lap + car_behind_lap + blank_lap * 20

    # 2 trailing bytes: timeTrialPBCarIdx, timeTrialRivalCarIdx (both 255 = not set)
    trailer = struct.pack("BB", 255, 255)

    return header + lap_array + trailer


def _build_car_telemetry_packet(state: RaceSimState) -> bytes:
    """
    Packet ID 6 — PacketCarTelemetryData.
    We fill the player's slot accurately; all other cars get zeroed entries.
    """
    player_idx = 0
    header = _build_header(PACKET_ID_CAR_TELEMETRY, player_idx, state)

    # Blank telemetry for non-player cars
    blank_telem = struct.pack(CAR_TELEMETRY_FMT,
        0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0,   # brakesTemperature[4]
        0, 0, 0, 0,   # tyresSurfaceTemperature[4]
        0, 0, 0, 0,   # tyresInnerTemperature[4]
        0,            # engineTemperature
        0.0, 0.0, 0.0, 0.0,  # tyresPressure[4]
        0, 0, 0, 0,   # surfaceType[4]
    )

    # Player's accurate telemetry
    player_telem = struct.pack(CAR_TELEMETRY_FMT,
        state.speed,       # speed (km/h)
        0.8,               # throttle (80%)
        0.05,              # steer (slight right)
        0.0,               # brake
        0,                 # clutch
        state.gear,        # gear (int8)
        12000,             # engineRPM
        state.drs,         # drs (0=off, 1=on)
        75,                # revLightsPercent
        0,                 # revLightsBitValue
        120, 122, 118, 119,   # brakesTemperature[4] °C
        90, 91, 88, 89,       # tyresSurfaceTemperature[4] °C
        105, 106, 102, 103,   # tyresInnerTemperature[4] °C
        105,                   # engineTemperature °C
        23.5, 23.5, 22.8, 22.8,  # tyresPressure[4] PSI
        0, 0, 0, 0,            # surfaceType[4] (0 = tarmac)
    )

    # Player is at index 0; 21 blank cars follow. 3 trailing bytes after the array.
    telemetry_array = player_telem + blank_telem * 21
    # mfdPanelIndex, mfdPanelIndexSecondaryPlayer, suggestedGear
    trailer = struct.pack("BBb", 0, 255, 0)

    return header + telemetry_array + trailer


def _build_car_status_packet(state: RaceSimState) -> bytes:
    """
    Packet ID 7 — PacketCarStatusData.
    Contains fuel load, tyre compound, and tyre age for each car.
    """
    player_idx = 0
    header = _build_header(PACKET_ID_CAR_STATUS, player_idx, state)

    blank_status = struct.pack(CAR_STATUS_FMT,
        0, 0, 1, 50, 0,             # TC, ABS, fuelMix, frontBrakeBias, pitLimiter
        0.0, 100.0, 0.0,            # fuelInTank, fuelCapacity, fuelRemainingLaps
        15000, 5000,                 # maxRPM, idleRPM
        8, 0, 0,                     # maxGears, drsAllowed, drsActivationDistance
        COMPOUND_MEDIUM,             # actualTyreCompound
        COMPOUND_MEDIUM,             # visualTyreCompound
        0,                           # tyresAgeLaps
        0,                           # vehicleFiaFlags (int8)
        0, 0,                        # enginePowerICE, enginePowerMGUK (not used by listener)
        4000000.0,                   # ersStoreEnergy (joules)
        2,                           # ersDeployMode
        100000.0, 80000.0, 200000.0, # ers harvested/deployed
        0,                           # networkPaused
    )

    # Compute fuel remaining in laps for the player
    fuel_remaining_laps = state.fuel_kg / state.fuel_per_lap if state.fuel_per_lap > 0 else 0.0

    player_status = struct.pack(CAR_STATUS_FMT,
        2, 1, 1, 55, 0,               # TC=high, ABS=on, fuelMix=standard, frontBias=55%
        state.fuel_kg,                 # fuelInTank (kg)
        100.0,                         # fuelCapacity
        fuel_remaining_laps,           # fuelRemainingLaps
        15000, 4500,                   # maxRPM, idleRPM
        8, 1, 500,                     # maxGears, drsAllowed, drsActivationDistance(m)
        state.compound_code,           # actualTyreCompound
        state.compound_code,           # visualTyreCompound
        state.tyre_age_laps,           # tyresAgeLaps
        0,                             # vehicleFiaFlags
        0, 0,                          # enginePowerICE, MGU-K (not used by listener)
        3500000.0,                     # ersStoreEnergy
        2,                             # ersDeployMode
        95000.0, 75000.0, 180000.0,   # ers fields
        0,                             # networkPaused
    )

    status_array = player_status + blank_status * 21
    return header + status_array


def _build_car_damage_packet(state: RaceSimState) -> bytes:
    """
    Packet ID 10 — PacketCarDamageData.
    Contains tyre wear (% accumulated) for all 4 corners of each car.
    Listener will convert to life_remaining = 100 - average_wear.
    """
    player_idx = 0
    header = _build_header(PACKET_ID_CAR_DAMAGE, player_idx, state)

    # Blank damage for all non-player cars (no wear)
    blank_damage = struct.pack(CAR_DAMAGE_FMT,
        0.0, 0.0, 0.0, 0.0,   # tyresWear[4] = no wear
        *([0] * 22),           # all other damage fields = 0
    )

    # Player's accurate tyre wear
    player_damage = struct.pack(CAR_DAMAGE_FMT,
        state.tyre_wear_fl,   # FL wear %
        state.tyre_wear_fr,   # FR wear %
        state.tyre_wear_rl,   # RL wear %
        state.tyre_wear_rr,   # RR wear %
        *([0] * 22),          # no other damage
    )

    damage_array = player_damage + blank_damage * 21
    return header + damage_array


# ---------------------------------------------------------------------------
# Main sender loop
# ---------------------------------------------------------------------------

def run_sender(total_laps: int = 53, position: int = 8, interval: float = 2.0):
    """
    Send F1 24 UDP packets in a loop, simulating a race.

    Args:
        total_laps: Race distance (default 53 laps)
        position:   Starting grid position (default P8)
        interval:   Seconds between packet bursts (default 2.0s)
    """
    state = RaceSimState(total_laps=total_laps, starting_position=position)
    sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print(f"🚀 UDP Sender — simulating {total_laps}-lap race from P{position}")
    print(f"   Sending to {TARGET_HOST}:{TARGET_PORT} every {interval}s")
    print(f"   Tyre: Medium | Fuel: {state.fuel_kg}kg | Fuel/lap: {state.fuel_per_lap}kg")
    sc_type_label = "VSC" if state._sc_type == "virtual_safety_car" else "SC"
    print(f"   {sc_type_label}: planned Lap {state._sc_deploy_lap}–{state._sc_end_lap}")
    print(f"   Press Ctrl+C to stop.\n")

    lap_timer = 0  # counts how many intervals have passed since last lap advance
    laps_per_interval = 5  # advance one lap every 5 intervals (every 10 seconds)

    try:
        while state.lap <= state.total_laps:
            # --- Send all 5 packet types in rapid succession ---
            packets = [
                _build_session_packet(state),
                _build_lap_data_packet(state),
                _build_car_telemetry_packet(state),
                _build_car_status_packet(state),
                _build_car_damage_packet(state),
            ]
            for pkt in packets:
                sock.sendto(pkt, (TARGET_HOST, TARGET_PORT))

            # Print current state so you can see what's being sent
            avg_wear = (state.tyre_wear_fl + state.tyre_wear_fr +
                        state.tyre_wear_rl + state.tyre_wear_rr) / 4
            life_remaining = 100.0 - avg_wear
            last_lap_str = f"{state.last_lap_ms // 60000}:{(state.last_lap_ms % 60000) / 1000:06.3f}"

            sc_label = " 🟡VSC" if state.track_status == "virtual_safety_car" \
                       else (" 🟡SC" if state.track_status == "safety_car" else "")
            print(
                f"📤 Lap {state.lap:2d}/{state.total_laps} | "
                f"P{state.position:2d} | "
                f"Tyre: {life_remaining:.0f}%{sc_label} | "
                f"Fuel: {state.fuel_kg:.1f}kg | "
                f"Last lap: {last_lap_str} | "
                f"DRS: {'ON' if state.drs else 'off'}"
            )

            # Advance one lap every N intervals
            lap_timer += 1
            if lap_timer >= laps_per_interval:
                state.advance_lap()
                lap_timer = 0

            time.sleep(interval)

        print(f"\n🏁 Race finished after {state.total_laps} laps. Sender exiting.")

    except KeyboardInterrupt:
        print("\n🛑 Sender stopped manually.")
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AI Race Engineer — PS5 UDP packet simulator"
    )
    parser.add_argument("--laps",     type=int,   default=53,  help="Total race laps (default: 53)")
    parser.add_argument("--position", type=int,   default=8,   help="Starting position (default: 8)")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between packet bursts (default: 2.0)")
    args = parser.parse_args()

    run_sender(
        total_laps=args.laps,
        position=args.position,
        interval=args.interval,
    )

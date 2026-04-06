"""
src/telemetry/udp_listener.py

Phase 2 telemetry source: receives live UDP packets from the F1 game on PS5.

HOW IT WORKS (big picture):
The F1 game on PS5 broadcasts packets over your home network to a target IP
and port that you configure in the game settings. This file opens a UDP socket
on that port and listens for those packets. Every packet has a 29-byte header
that tells us what TYPE of packet it is — lap data, car status, fuel, etc.
We route each packet type to its own parser and update our shared data dict.

WHY UDP (not TCP)?
The game sends telemetry at up to 60 packets per second. Speed matters more
than reliability here. If one packet is lost, the next one arrives 16ms later
and overwrites it anyway. TCP's handshaking and retry logic would introduce
unacceptable latency.

PACKET IDs WE CARE ABOUT (Codemasters F1 24 spec):
  ID 1  — PacketSessionData    → total_laps, session type
  ID 2  — PacketLapData        → lap number, position, lap times, gaps
  ID 6  — PacketCarTelemetryData → speed, gear, DRS
  ID 7  — PacketCarStatusData  → fuel, tyre compound, tyre age
  ID 10 — PacketCarDamageData  → tyre wear (%)

HOW TO ACTIVATE ON PS5:
  1. PS5: F1 24 → Settings → Telemetry Settings
  2. UDP Telemetry: ON
  3. Broadcast IP: your PC's local IP (e.g. 192.168.1.42)
  4. Port: 20777
  5. Format: 2024
  In src/main.py: choose 'u' for UDP when asked for telemetry source.

INTERFACE CONTRACT:
get_snapshot() returns the same dict shape as TelemetrySimulator.get_snapshot().
This is the architectural guarantee that lets us swap Phase 1 → Phase 2 in 2 lines.

REFERENCE: https://answers.ea.com/t5/General-Discussion/F1-24-UDP-Specification/td-p/13745220
"""

import socket
import struct
import threading
import time

from config.settings import UDP_HOST, UDP_PORT, TOTAL_LAPS, BASE_LAP_TIME


# ---------------------------------------------------------------------------
# STRUCT FORMATS — each matches the C struct layout in the F1 24 UDP spec.
# '<' prefix = little-endian (Intel byte order, which the game uses).
# ---------------------------------------------------------------------------

# PacketHeader: 29 bytes, present at the start of every packet.
# Fields: packetFormat, gameYear, gameMajorVersion, gameMinorVersion,
#         packetVersion, packetId, sessionUID, sessionTime,
#         frameIdentifier, overallFrameIdentifier,
#         playerCarIndex, secondaryPlayerCarIndex
HEADER_FMT  = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)   # 29 bytes

# Session data — we only need the first 4 bytes after the header:
# weather(B), trackTemperature(b), airTemperature(b), totalLaps(B)
SESSION_EARLY_FMT  = "<BbbB"
SESSION_EARLY_SIZE = struct.calcsize(SESSION_EARLY_FMT)   # 4 bytes

# LapData: 57 bytes per car, 22 cars in the array.
# Contains position, current lap, lap times, gaps, pit status.
LAP_DATA_FMT  = "<IIHBHBHBHBfffBBBBBBBBBBBBBBBHHBfB"
LAP_DATA_SIZE = struct.calcsize(LAP_DATA_FMT)   # 57 bytes

# CarTelemetryData: 60 bytes per car.
# Contains speed, throttle, brake, steer, gear, DRS, tyre temps.
CAR_TELEMETRY_FMT  = "<HfffBbHBBHHHHHBBBBBBBBHffffBBBB"
CAR_TELEMETRY_SIZE = struct.calcsize(CAR_TELEMETRY_FMT)   # 60 bytes

# CarStatusData: 51 bytes per car.
# Contains fuel, tyre compound, tyre age.
CAR_STATUS_FMT  = "<BBBBBfffHHBBHBBBbHHfBfffB"
CAR_STATUS_SIZE = struct.calcsize(CAR_STATUS_FMT)   # 51 bytes

# CarDamageData: 38 bytes per car.
# Contains tyre wear (%) for all 4 corners.
CAR_DAMAGE_FMT  = "<ffff22B"
CAR_DAMAGE_SIZE = struct.calcsize(CAR_DAMAGE_FMT)   # 38 bytes

# Packet ID constants — matches the spec's packetId field in the header.
PACKET_ID_SESSION     = 1
PACKET_ID_LAP_DATA    = 2
PACKET_ID_CAR_TELEMETRY = 6
PACKET_ID_CAR_STATUS  = 7
PACKET_ID_CAR_DAMAGE  = 10

# Visual tyre compound IDs (F1 24 spec) → human-readable names.
# "Visual" compound is what the TV graphics show — simpler than actual compound codes.
TYRE_COMPOUND_MAP = {
    16: "Soft",
    17: "Medium",
    18: "Hard",
    7:  "Inter",
    8:  "Wet",
}


class UDPTelemetryListener:
    """
    Listens for F1 24 UDP telemetry packets and exposes a get_snapshot() interface
    identical to TelemetrySimulator — so main.py works without any changes.

    Lifecycle:
        listener = UDPTelemetryListener()
        listener.start()          # opens socket, starts background thread
        snap = listener.get_snapshot()
        listener.stop()           # closes socket, thread exits
    """

    def __init__(self):
        self.running = False
        self.sock    = None

        # Data store — populated by packet parsers, read by get_snapshot().
        # Pre-filled with safe defaults so the system doesn't crash during
        # the first few seconds before packets arrive.
        self.data = {
            "lap":            1,
            "total_laps":     TOTAL_LAPS,
            "laps_remaining": TOTAL_LAPS - 1,
            "position":       10,
            "gap_ahead":      0.0,
            "gap_behind":     0.0,
            "tire_compound":  "Medium",
            "tire_wear":      100.0,
            "tire_age_laps":  0,
            "fuel":           100.0,
            "fuel_per_lap":   2.0,
            "last_lap_time":  self._fmt_laptime(BASE_LAP_TIME),
            "best_lap_time":  self._fmt_laptime(BASE_LAP_TIME),
            "lap_delta":      "+0.000",
            "speed":          0,
            "gear":           1,
            "drs":            False,
            "track_status":   "green",   # "green" or "safety_car"
        }

        # best_lap_secs tracks personal best for delta calculation.
        self._best_lap_secs: float = BASE_LAP_TIME

        # Lock prevents main thread reading data while background thread writes it.
        # Without this, a get_snapshot() call mid-write could return corrupted data.
        self._lock = threading.Lock()

    # -----------------------------------------------------------------------
    # Public interface (same as TelemetrySimulator)
    # -----------------------------------------------------------------------

    def start(self):
        """Open the UDP socket and start the background listener thread."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # SO_REUSEADDR lets us restart the listener without waiting for OS timeout
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((UDP_HOST, UDP_PORT))
        # 2-second receive timeout so the thread can check self.running and exit cleanly
        self.sock.settimeout(2.0)

        self.running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        print(f"📡 UDP listener bound to port {UDP_PORT} — waiting for PS5 packets...")

    def stop(self):
        """Stop the listener thread and close the socket."""
        self.running = False
        if self.sock:
            self.sock.close()

    def get_snapshot(self) -> dict:
        """
        Return a frozen copy of the current telemetry state.
        Thread-safe: acquires lock so the caller never sees a half-written update.
        """
        with self._lock:
            return self.data.copy()

    # -----------------------------------------------------------------------
    # Background listener loop
    # -----------------------------------------------------------------------

    def _listen_loop(self):
        """
        Main loop — runs in background thread.
        Receives raw UDP bytes, reads the header, routes to the right parser.
        """
        while self.running:
            try:
                # Receive up to 4096 bytes (largest F1 packet is ~1464 bytes)
                raw_bytes, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                # Normal — fires every 2 seconds when no packet received.
                # Lets us check self.running so stop() works cleanly.
                continue
            except OSError:
                # Socket was closed by stop() — exit thread.
                break

            # Every packet starts with the same 29-byte header.
            # If the packet is shorter than the header, it's malformed — skip it.
            if len(raw_bytes) < HEADER_SIZE:
                continue

            try:
                header = struct.unpack_from(HEADER_FMT, raw_bytes, 0)
            except struct.error:
                continue

            # Header fields (by index):
            # 0: packetFormat  1: gameYear  2: gameMajorVersion  3: gameMinorVersion
            # 4: packetVersion 5: packetId  6: sessionUID  7: sessionTime
            # 8: frameIdentifier  9: overallFrameIdentifier
            # 10: playerCarIndex  11: secondaryPlayerCarIndex
            packet_id        = header[5]
            player_car_index = header[10]

            # Route to the correct parser based on packet type.
            # Each parser updates self.data under the lock.
            if packet_id == PACKET_ID_SESSION:
                self._parse_session(raw_bytes)
            elif packet_id == PACKET_ID_LAP_DATA:
                self._parse_lap_data(raw_bytes, player_car_index)
            elif packet_id == PACKET_ID_CAR_TELEMETRY:
                self._parse_car_telemetry(raw_bytes, player_car_index)
            elif packet_id == PACKET_ID_CAR_STATUS:
                self._parse_car_status(raw_bytes, player_car_index)
            elif packet_id == PACKET_ID_CAR_DAMAGE:
                self._parse_car_damage(raw_bytes, player_car_index)
            # Other packet types (Motion, Events, etc.) are ignored for now.

    # -----------------------------------------------------------------------
    # Packet parsers — one per packet type
    # -----------------------------------------------------------------------

    def _parse_session(self, data: bytes):
        """
        ID 1 — PacketSessionData.
        We only need total_laps from this packet.
        It arrives once per second, so no need to parse everything.
        """
        # The first 4 bytes after the header are: weather, trackTemp, airTemp, totalLaps
        offset = HEADER_SIZE
        if len(data) < offset + SESSION_EARLY_SIZE:
            return
        try:
            weather, track_temp, air_temp, total_laps = struct.unpack_from(
                SESSION_EARLY_FMT, data, offset
            )
            # weather byte is repurposed to carry track_status from the sender:
            #   0 = green flag, 1 = safety car deployed.
            # When connected to a real PS5 game, weather carries actual weather
            # (0=clear, 1=light cloud, ...) — SC would never be 1, so the mapping
            # is safe and won't falsely trigger in live mode.
            track_status = "safety_car" if weather == 1 else "green"

            with self._lock:
                if total_laps > 0:
                    self.data["total_laps"] = int(total_laps)
                self.data["track_status"] = track_status
        except struct.error:
            pass

    def _parse_lap_data(self, data: bytes, player_idx: int):
        """
        ID 2 — PacketLapData.
        Contains 22 LapData structs (one per car on grid).
        We extract the player's own entry and optionally the car behind us.

        Key fields extracted:
          - currentLapNum  → lap number
          - carPosition    → race position
          - lastLapTimeInMS → last completed lap time
          - deltaToCarInFrontInMS → gap ahead in ms
          We find gap_behind by locating the car whose position = ours + 1.
        """
        # LapData array starts right after the header
        array_offset = HEADER_SIZE

        # Minimum size check: need at least (player_idx + 1) entries
        min_required = array_offset + (player_idx + 1) * LAP_DATA_SIZE
        if len(data) < min_required:
            return

        # Parse every car's LapData entry (we need position array for gap_behind calc)
        all_laps = []
        for i in range(22):
            offset = array_offset + i * LAP_DATA_SIZE
            if offset + LAP_DATA_SIZE > len(data):
                break
            try:
                entry = struct.unpack_from(LAP_DATA_FMT, data, offset)
                all_laps.append(entry)
            except struct.error:
                all_laps.append(None)

        if player_idx >= len(all_laps) or all_laps[player_idx] is None:
            return

        p = all_laps[player_idx]

        # Unpack the fields we care about by index position in the struct tuple.
        # Indices (0-based): 0=lastLapTimeInMS, 1=currentLapTimeInMS,
        # 2=sector1TimeMS, 3=sector1Min, 4=sector2TimeMS, 5=sector2Min,
        # 6=deltaToCarInFrontMS, 7=deltaToCarFrontMin, 8=deltaToLeaderMS, 9=deltaToLeaderMin
        # 10=lapDistance, 11=totalDistance, 12=safetyCarDelta,
        # 13=carPosition, 14=currentLapNum, ...
        last_lap_ms    = p[0]
        delta_front_ms = p[6]    # gap to car ahead in milliseconds
        car_position   = p[13]
        current_lap    = p[14]

        # Convert milliseconds → seconds
        gap_ahead = round(delta_front_ms / 1000.0, 1)

        # Find the car behind us: the car whose carPosition = player_position + 1
        gap_behind = 0.0
        for entry in all_laps:
            if entry is not None and entry[13] == car_position + 1:
                # This car's deltaToCarInFront is our gap_behind
                gap_behind = round(entry[6] / 1000.0, 1)
                break

        # Format last lap time as M:SS.mmm (only if a lap has been completed)
        if last_lap_ms > 0:
            last_lap_secs = last_lap_ms / 1000.0
            last_lap_str  = self._fmt_laptime(last_lap_secs)

            # Update personal best
            if last_lap_secs < self._best_lap_secs:
                self._best_lap_secs = last_lap_secs
                delta_str = "-{:.3f}".format(self._best_lap_secs - last_lap_secs)
            else:
                delta_str = "+{:.3f}".format(last_lap_secs - self._best_lap_secs)
        else:
            last_lap_str = self.data["last_lap_time"]
            delta_str    = self.data["lap_delta"]

        with self._lock:
            self.data["lap"]            = int(current_lap)
            self.data["laps_remaining"] = max(0, self.data["total_laps"] - int(current_lap))
            self.data["position"]       = int(car_position)
            self.data["gap_ahead"]      = gap_ahead
            self.data["gap_behind"]     = gap_behind
            self.data["last_lap_time"]  = last_lap_str
            self.data["best_lap_time"]  = self._fmt_laptime(self._best_lap_secs)
            self.data["lap_delta"]      = delta_str

    def _parse_car_telemetry(self, data: bytes, player_idx: int):
        """
        ID 6 — PacketCarTelemetryData.
        Contains speed, throttle, brake, steer, gear, DRS for each car.

        Fields extracted:
          - speed (uint16, km/h)
          - gear  (int8, 1-8, 0=neutral, -1=reverse)
          - drs   (uint8, 0=off, 1=on)
        """
        # CarTelemetryData array starts at HEADER_SIZE
        offset = HEADER_SIZE + player_idx * CAR_TELEMETRY_SIZE
        if offset + CAR_TELEMETRY_SIZE > len(data):
            return
        try:
            t = struct.unpack_from(CAR_TELEMETRY_FMT, data, offset)
        except struct.error:
            return

        # Struct tuple indices for CarTelemetryData:
        # 0=speed, 1=throttle, 2=steer, 3=brake, 4=clutch, 5=gear, 6=engineRPM,
        # 7=drs, 8=revLightsPercent, 9=revLightsBitValue, 10-13=brakesTemp[4],
        # 14-17=tyresSurfaceTemp[4], 18-21=tyresInnerTemp[4], 22=engineTemp,
        # 23-26=tyresPressure[4], 27-30=surfaceType[4]
        speed = t[0]
        gear  = t[5]   # int8: 1-8 = forward gears, 0 = neutral, -1 = reverse
        drs   = bool(t[7])   # 0 = off, 1 = on

        with self._lock:
            self.data["speed"] = int(speed)
            self.data["gear"]  = int(gear)
            self.data["drs"]   = drs

    def _parse_car_status(self, data: bytes, player_idx: int):
        """
        ID 7 — PacketCarStatusData.
        Contains fuel load, tyre compound, tyre age, and fuel remaining in laps.

        Fields extracted:
          - fuelInTank          → fuel (kg remaining)
          - fuelRemainingLaps   → used to compute fuel_per_lap
          - visualTyreCompound  → tire_compound (Soft/Medium/Hard/Inter/Wet)
          - tyresAgeLaps        → tire_age_laps
        """
        offset = HEADER_SIZE + player_idx * CAR_STATUS_SIZE
        if offset + CAR_STATUS_SIZE > len(data):
            return
        try:
            s = struct.unpack_from(CAR_STATUS_FMT, data, offset)
        except struct.error:
            return

        # Struct tuple indices for CarStatusData:
        # 0=tractionControl, 1=antiLockBrakes, 2=fuelMix, 3=frontBrakeBias,
        # 4=pitLimiterStatus, 5=fuelInTank, 6=fuelCapacity, 7=fuelRemainingLaps,
        # 8=maxRPM, 9=idleRPM, 10=maxGears, 11=drsAllowed,
        # 12=drsActivationDistance, 13=actualTyreCompound, 14=visualTyreCompound,
        # 15=tyresAgeLaps, 16=vehicleFiaFlags, 17=enginePowerICE,
        # 18=enginePowerMGUK, 19=ersStoreEnergy, 20=ersDeployMode,
        # 21=ersHarvestedThisLapMGUK, 22=ersHarvestedThisLapMGUH,
        # 23=ersDeployedThisLap, 24=networkPaused
        fuel_in_tank         = s[5]
        fuel_remaining_laps  = s[7]
        visual_tyre_compound = s[14]
        tyre_age_laps        = s[15]

        # Map the numeric compound code to a human-readable string.
        # Default to "Unknown" if we get an unexpected code.
        compound_name = TYRE_COMPOUND_MAP.get(visual_tyre_compound, "Unknown")

        # fuel_per_lap = fuel_remaining / laps_remaining (from the game's own model).
        # Guard against division by zero during pit stops or race start.
        fuel_per_lap = round(fuel_in_tank / fuel_remaining_laps, 2) \
                       if fuel_remaining_laps > 0 else self.data["fuel_per_lap"]

        with self._lock:
            self.data["fuel"]          = round(float(fuel_in_tank), 1)
            self.data["fuel_per_lap"]  = float(fuel_per_lap)
            self.data["tire_compound"] = compound_name
            self.data["tire_age_laps"] = int(tyre_age_laps)

    def _parse_car_damage(self, data: bytes, player_idx: int):
        """
        ID 10 — PacketCarDamageData.
        Contains tyre wear for all 4 corners plus various damage percentages.

        Fields extracted:
          - tyresWear[4] → average across 4 corners → tire_wear (% life remaining)

        NOTE: In the F1 24 spec, tyresWear represents WEAR ACCUMULATED (0% = new,
        100% = destroyed). We convert to LIFE REMAINING to match our race_state
        convention (100% = new, 0% = gone).
        """
        offset = HEADER_SIZE + player_idx * CAR_DAMAGE_SIZE
        if offset + CAR_DAMAGE_SIZE > len(data):
            return
        try:
            d = struct.unpack_from(CAR_DAMAGE_FMT, data, offset)
        except struct.error:
            return

        # First 4 values are tyresWear[4]: FL, FR, RL, RR (all floats)
        tyre_wear_fl = d[0]
        tyre_wear_fr = d[1]
        tyre_wear_rl = d[2]
        tyre_wear_rr = d[3]

        # Average the 4 corners for a single representative wear number.
        # Convert from "wear accumulated" to "life remaining".
        avg_wear_pct    = (tyre_wear_fl + tyre_wear_fr + tyre_wear_rl + tyre_wear_rr) / 4.0
        life_remaining  = round(100.0 - avg_wear_pct, 1)
        life_remaining  = max(0.0, min(100.0, life_remaining))  # clamp to [0, 100]

        with self._lock:
            self.data["tire_wear"] = life_remaining

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _fmt_laptime(seconds: float) -> str:
        """Convert raw seconds to M:SS.mmm display format. e.g. 92.456 → '1:32.456'"""
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}:{secs:06.3f}"

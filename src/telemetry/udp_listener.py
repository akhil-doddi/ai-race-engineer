"""
src/telemetry/udp_listener.py

Phase 2 telemetry source: receives live UDP packets from the F1 game on PS5.

STATUS: Placeholder — not yet implemented.

HOW TO ACTIVATE (Phase 2):
1. In the F1 game settings on PS5, go to Telemetry Settings.
2. Set UDP Telemetry to ON.
3. Set your PC's local IP address as the broadcast target.
4. Set port to 20777 (default).
5. Implement the packet parser below and swap TelemetrySimulator
   for UDPTelemetryListener in src/main.py.

WHY UDP:
The F1 game broadcasts telemetry as UDP packets at up to 60Hz.
UDP is used (not TCP) because speed matters more than reliability —
a dropped packet is less damaging than the latency of guaranteed delivery.

PACKET FORMAT:
Codemasters F1 2024 UDP spec defines packet types:
- PacketMotionData       (car position, velocity)
- PacketSessionData      (weather, safety car, lap count)
- PacketLapData          (per-car lap times, positions)
- PacketCarTelemetryData (throttle, brake, speed, gear, DRS)
- PacketCarStatusData    (tyre compound, tyre wear, fuel)

Reference: https://answers.ea.com/t5/General-Discussion/F1-24-UDP-Specification/td-p/13745220
"""

# import socket
# from config.settings import UDP_HOST, UDP_PORT
#
#
# class UDPTelemetryListener:
#     """
#     Listens for UDP telemetry packets from the F1 game on PS5.
#     Parses raw bytes into the same race_state structure as TelemetrySimulator.
#     """
#
#     def __init__(self):
#         self.running = False
#         self.data = {}
#
#     def start(self):
#         self.running = True
#         self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
#         self.sock.bind((UDP_HOST, UDP_PORT))
#         # TODO: start listener thread
#
#     def stop(self):
#         self.running = False
#         self.sock.close()
#
#     def get_snapshot(self) -> dict:
#         return self.data.copy()

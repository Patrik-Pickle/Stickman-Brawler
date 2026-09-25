"""
server.py - server otoritatif Stickman Brawler.

Menjalankan seluruh simulasi pertandingan (dari game_common.py) dan
menyiarkan (broadcast) state hasilnya ke tepat dua client yang terhubung
lewat UDP, 60 kali per detik. Client tidak pernah mensimulasikan fisika
sendiri - mereka hanya mengirim input dan menggambar apa pun yang
dikatakan server sebagai kebenaran. Ini cara paling sederhana dan benar
untuk menjaga dua pemain remote tetap sinkron, dan mencegah kedua sisi
dari kecurangan (mempercepat gerakan, mengklaim hit yang sebenarnya tidak
kena, dsb).

Jalankan dengan:
    python server.py [port]

Port default adalah 5555. Mesin yang menjalankan ini harus bisa dijangkau
oleh kedua pemain - di LAN yang sama, bagikan IP lokal kamu; lewat
internet terbuka, kamu perlu port-forward port UDP ini atau memakai
relay/VPN.

Tidak perlu display, audio, atau sprite di sini - proses ini headless dan
bisa berjalan di server, Raspberry Pi, atau cukup di salah satu mesin
kedua pemain di latar belakang.
"""

import socket
import sys
import time
import json

import game_common as gc

HOST = "0.0.0.0"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5555

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((HOST, PORT))
sock.setblocking(False)

print(f"Stickman Brawler server listening on UDP {HOST}:{PORT}")
print("Waiting for 2 players to connect...")

# addr -> player_num (1 atau 2)
players = {}
# player_num -> dict intent terakhir yang diterima
latest_intent = {1: {}, 2: {}}
latest_card_nav = {1: None, 2: None}

match = gc.Match()


def send_to(addr, payload):
    try:
        sock.sendto(json.dumps(payload).encode("utf-8"), addr)
    except OSError:
        pass


def broadcast(payload):
    for addr in players:
        send_to(addr, payload)


def handle_message(addr, msg):
    global players
    msg_type = msg.get("type")

    if msg_type == "join":
        if addr not in players:
            if len(players) >= 2:
                send_to(addr, {"type": "full"})
                return
            player_num = 1 if 1 not in players.values() else 2
            players[addr] = player_num
            print(f"Player {player_num} connected from {addr}")
            send_to(addr, {"type": "welcome", "player_num": player_num})
            if len(players) == 2:
                print("Both players connected - starting match!")
                match.reset_match()
        else:
            # sudah join - kirim ulang saja assignment-nya (menangani
            # client yang melewatkan balasan "welcome" yang pertama)
            send_to(addr, {"type": "welcome", "player_num": players[addr]})
        return

    player_num = players.get(addr)
    if player_num is None:
        return  # abaikan pesan dari alamat yang tidak dikenal/belum join

    if msg_type == "input":
        latest_intent[player_num] = msg.get("intent", {})
    elif msg_type == "card_nav":
        latest_card_nav[player_num] = msg.get("nav")
    elif msg_type == "reset_match":
        if len(players) == 2:
            match.reset_match()
            print("Match reset (requested by a player)")
    elif msg_type == "config_rounds":
        # Hanya dihormati sebelum pemain kedua join, jadi hanya host
        # (yang pertama connect) yang bisa mengaturnya, dan hanya sebelum
        # pertandingan benar-benar berjalan.
        if len(players) < 2:
            match.configure_rounds_to_win(msg.get("rounds_to_win", gc.ROUNDS_TO_WIN_MATCH))
            print(f"Rounds to win set to {match.rounds_to_win} (player {player_num})")
    elif msg_type == "config_guaranteed_weapons":
        # Aturan yang sama seperti config_rounds di atas: host-only, hanya sebelum pertandingan mulai.
        if len(players) < 2:
            match.configure_guaranteed_weapons(msg.get("enabled", False))
            print(f"Guaranteed Weapons set to {match.guaranteed_weapons} (player {player_num})")


def main_loop():
    tick_duration = 1.0 / gc.FPS
    next_tick = time.perf_counter()

    while True:
        # kuras semua paket masuk yang tertunda tanpa blocking
        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except BlockingIOError:
                break
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            handle_message(addr, msg)

        if len(players) == 2:
            p1_nav = latest_card_nav[1]
            p2_nav = latest_card_nav[2]
            match.step(latest_intent[1], latest_intent[2], p1_nav, p2_nav)
            # card nav bersifat edge-triggered (satu kali "tekan"), jadi
            # bersihkan setelah diterapkan alih-alih membiarkannya
            # berulang tiap tick
            latest_card_nav[1] = None
            latest_card_nav[2] = None

            broadcast(match.to_dict())

        next_tick += tick_duration
        sleep_time = next_tick - time.perf_counter()
        if sleep_time > 0:
            time.sleep(sleep_time)
        else:
            next_tick = time.perf_counter()  # kita ketinggalan - resync


if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\nServer shutting down.")

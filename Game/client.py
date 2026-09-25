"""
client.py - client Stickman Brawler yang terhubung jaringan (networked).

Terhubung ke server.py yang sedang berjalan lewat UDP, mengirim input
keyboard lokalmu setiap frame, dan menggambar state pertandingan apa pun
yang dilaporkan balik oleh server. Semua fisika, deteksi hit, dan aturan
ronde/kartu diputuskan oleh server - client ini hanya menangani
penggambaran, suara, dan penangkapan input, jadi tidak ada pemain yang
bisa curang dengan mengutak-atik client miliknya sendiri.

Jalankan dengan:
    python client.py

Kamu akan ditanya alamat server saat jendelanya terbuka (biarkan kosong
untuk connect ke localhost, misalnya kalau kamu juga menjalankan server
di mesin sendiri untuk uji coba lokal cepat). Untuk pertandingan
sungguhan lewat jaringanmu, siapa pun yang menjalankan server.py
membagikan IP LAN-nya ke pemain lain.

Kontrol (layout keyboard fisik yang sama apa pun slot pemain yang
diberikan server ke kamu):
    A / D      - gerak kiri / kanan
    W          - lompat
    F          - serangan ringan
    G          - serangan berat
    S          - block (tahan)

Tombol global:
    R   - minta server me-reset seluruh pertandingan
    M   - kembali ke menu utama (disconnect)
    ESC - keluar (atau back, dari settings)

Membutuhkan:  pip install pygame
"""

import pygame
import sys
import os
import math
import socket
import threading
import json

import game_common as gc

pygame.init()
try:
    pygame.mixer.init()
except pygame.error:
    pass

# Dibuat di sini, sesegera mungkin setelah pygame.init(), SEBELUM sprite apa
# pun dimuat di bawah (termasuk WEAPON_SPRITES). Surface.convert_alpha()/
# convert() butuh mode display sudah di-set supaya tahu format piksel target;
# kalau dipanggil sebelum ini ada, pygame.error dilempar, tertangkap oleh
# except di _load_weapon_sprite(), dan sprite itu diam-diam jadi None ->
# fallback gambar prosedural dipakai walau file .png-nya ada dan valid. Pakai
# gc.SCREEN_W/gc.SCREEN_H langsung di sini karena alias lokal SCREEN_W/
# SCREEN_H belum didefinisikan sejauh ini di file. Jangan pindahkan
# set_mode() ini ke bawah lagi tanpa memindahkan juga semua pemuatan sprite
# yang terjadi sebelum baris ini.
screen = pygame.display.set_mode((gc.SCREEN_W, gc.SCREEN_H))
pygame.display.set_caption("Stickman Brawler - Online")


def get_base_dir():
    """Folder tempat mencari sprites/music/sfx. Saat berjalan sebagai
    script .py biasa, itu adalah folder script itu sendiri. Saat
    di-freeze jadi .exe oleh PyInstaller, __file__ malah menunjuk ke
    dalam folder ekstraksi temp, jadi kita pakai lokasi .exe yang
    sebenarnya - inilah yang memungkinkan kamu terus menambahkan file
    asset baru di sebelah .exe-nya tanpa perlu build ulang."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()

# ---------------------------------------------------------------------------
# Musik
# ---------------------------------------------------------------------------
MUSIC_DIR = os.path.join(BASE_DIR, "music")
MUSIC_VOLUME = 0.5


def start_background_music():
    if not os.path.isdir(MUSIC_DIR):
        return
    candidates = [f for f in os.listdir(MUSIC_DIR) if f.lower().endswith((".mp3", ".ogg", ".wav"))]
    if not candidates:
        return
    track_path = os.path.join(MUSIC_DIR, sorted(candidates)[0])
    try:
        pygame.mixer.music.load(track_path)
        pygame.mixer.music.set_volume(MUSIC_VOLUME)
        pygame.mixer.music.play(-1)
    except pygame.error as e:
        print(f"Could not play background music: {e}")


# ---------------------------------------------------------------------------
# Efek suara
# ---------------------------------------------------------------------------
SFX_DIR = os.path.join(BASE_DIR, "sfx")
SFX_VOLUME = 0.7


def _load_sfx_variants(action_name):
    folder = os.path.join(SFX_DIR, action_name)
    if not os.path.isdir(folder):
        return []
    sounds = []
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith((".wav", ".ogg")):
            try:
                snd = pygame.mixer.Sound(os.path.join(folder, f))
                snd.set_volume(SFX_VOLUME)
                sounds.append(snd)
            except pygame.error:
                pass
    return sounds


def build_sfx_library():
    return {
        "jump": _load_sfx_variants("jump"),
        "hit": _load_sfx_variants("hit"),
        "crit": _load_sfx_variants("crit"),
        "dodge": _load_sfx_variants("dodge"),
        "shoot": _load_sfx_variants("shoot"),
        "sword": _load_sfx_variants("sword"),
        "spear": _load_sfx_variants("spear"),
    }


def play_sfx(action_name):
    variants = SFX.get(action_name) if SFX else None
    if variants:
        import random as _r
        _r.choice(variants).play()


# ---------------------------------------------------------------------------
# Pemuatan animasi sprite (identik dengan prototipe lokal)
# ---------------------------------------------------------------------------
SPRITES_DIR = os.path.join(BASE_DIR, "sprites")

ANIM_FRAME_DURATION = {
    "idle": 20, "walk": 7, "jump": 10, "punch": 5,
    "kick": 6, "block": 12, "block_walk": 7, "hurt": 8, "death": 14,
    # Dijaga tetap sinkron dengan penambahan yang sama di stickman_brawler.py.
    "heavy_sword": 8, "heavy_spear": 8, "heavy_gun": 5,
    "walk_sword": 7, "walk_spear": 7, "walk_gun": 7,
    "block_sword": 12, "block_spear": 12, "block_gun": 12,
    "block_walk_sword": 7, "block_walk_spear": 7, "block_walk_gun": 7,
    "idle_sword": 20, "idle_spear": 20, "idle_gun": 20,
    # Pose senjata light-attack (serangan dasar), 3 frame dengan frame 0 == frame 2
    # seperti pukulan tangan kosong (windup, thrust, recover). Frame thrust
    # ditata waktu supaya muncul persis saat jendela-hit terbuka di 1/3
    # ayunan (game_common.process_melee_attacks): spear = 3 x 5 = 15 tick
    # (light_duration miliknya); sword = 4 tick/frame, jadi windup 4 + thrust 4,
    # lalu frame recovery ditahan untuk 6 tick sisanya dari total 14 tick.
    # Gun: satu frame membidik yang ditahan sepanjang recoil_duration (10).
    "light_sword": 4, "light_spear": 5, "light_gun": 10,
    # Pose kematian senjata: 3 frame (kena hit berdiri, jatuh setengah, rebah)
    # dengan senjatanya sudah dipanggang (baked) di dalam art, 14 tick/frame
    # seperti "death" biasa (total 42 tick, lalu frame terakhir ditahan).
    "death_sword": 14, "death_spear": 14, "death_gun": 14,
    # Pose lompat senjata: 2 frame (naik, turun), digerakkan oleh vel_y
    # seperti "jump" tangan kosong (lihat WEAPON_JUMP_STATE di bawah) -
    # nilai ini disimpan di sini hanya untuk konsistensi/dokumentasi, sama
    # seperti "jump" itu sendiri.
    "jump_sword": 10, "jump_spear": 10, "jump_gun": 10,
}

WEAPON_HEAVY_STATE = {"sword": "heavy_sword", "spear": "heavy_spear", "gun": "heavy_gun"}
WEAPON_LIGHT_STATE = {"sword": "light_sword", "spear": "light_spear", "gun": "light_gun"}
WEAPON_WALK_STATE = {"sword": "walk_sword", "spear": "walk_spear", "gun": "walk_gun"}
WEAPON_BLOCK_STATE = {"sword": "block_sword", "spear": "block_spear", "gun": "block_gun"}
WEAPON_BLOCK_WALK_STATE = {"sword": "block_walk_sword", "spear": "block_walk_spear", "gun": "block_walk_gun"}
WEAPON_IDLE_STATE = {"sword": "idle_sword", "spear": "idle_spear", "gun": "idle_gun"}

# Ide yang sama lagi, tapi untuk kematian: urutan kematian 3-frame
# khusus-senjata (kena hit berdiri, jatuh setengah, rebah) dengan
# senjatanya sudah dipanggang/dijatuhkan di dalam art, menggantikan pose
# "death" generik (fallback ke "death" kalau art-nya belum dimuat).
# Sebelum ini, senjata fighter bersenjata digambar prosedural di atas
# pose death generik, melayang di tempat tangannya dulu berada.
WEAPON_DEATH_STATE = {"sword": "death_sword", "spear": "death_spear", "gun": "death_gun"}

# Pose lompat khusus-senjata (frame 0 naik, frame 1 turun - lihat
# pemilihan berbasis vel_y di _update_animation_state), fallback ke
# "jump" biasa kalau art-nya belum dimuat. Dijaga tetap sinkron dengan
# stickman_brawler.py.
WEAPON_JUMP_STATE = {"sword": "jump_sword", "spear": "jump_spear", "gun": "jump_gun"}

MIXED_COLOR_ACTIONS = (frozenset(WEAPON_HEAVY_STATE.values()) | frozenset(WEAPON_LIGHT_STATE.values())
                        | frozenset(WEAPON_WALK_STATE.values())
                        | frozenset(WEAPON_BLOCK_STATE.values()) | frozenset(WEAPON_BLOCK_WALK_STATE.values())
                        | frozenset(WEAPON_IDLE_STATE.values())
                        | frozenset(WEAPON_DEATH_STATE.values())
                        | frozenset(WEAPON_JUMP_STATE.values()))


def _load_frames(action_name):
    folder = os.path.join(SPRITES_DIR, action_name)
    if not os.path.isdir(folder):
        return []
    files = [f for f in os.listdir(folder) if f.lower().endswith(".png")]
    def frame_num(fname):
        try:
            return int(os.path.splitext(fname)[0])
        except ValueError:
            return 0
    files.sort(key=frame_num)
    frames = []
    for f in files:
        try:
            img = pygame.image.load(os.path.join(folder, f)).convert_alpha()
            frames.append(img)
        except pygame.error:
            pass
    return frames


def _compute_foot_offset(surf):
    w, h = surf.get_size()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            if surf.get_at((x, y))[3] > 10:
                return h - 1 - y
    return 0


def build_animation_library():
    lib = {}
    for action in ANIM_FRAME_DURATION:
        left_surfaces = _load_frames(action)
        left_frames = [(surf, _compute_foot_offset(surf)) for surf in left_surfaces]
        right_frames = [(pygame.transform.flip(surf, True, False), offset)
                         for surf, offset in left_frames]
        lib[action] = {"right": right_frames, "left": left_frames}
    return lib


# ---------------------------------------------------------------------------
# Rendering peluru (kosmetik saja). Senjata itu sendiri (sword/spear/gun di
# tangan fighter) tidak lagi digambar sebagai overlay prosedural terpisah -
# semua pose sekarang punya art badan sendiri yang sudah menggambar
# senjatanya langsung (lihat WEAPON_*_STATE / MIXED_COLOR_ACTIONS di atas).
# Yang tersisa di sini hanyalah projectile pistol, dicari di
# sprites/weapons/bullet.png dengan fallback lingkaran sederhana kalau file
# itu belum ada. Dijaga tetap sinkron dengan salinan kode ini di
# stickman_brawler.py.
# ---------------------------------------------------------------------------
WEAPONS_DIR = os.path.join(SPRITES_DIR, "weapons")


def _load_weapon_sprite(name):
    path = os.path.join(WEAPONS_DIR, f"{name}.png")
    if os.path.isfile(path):
        try:
            return pygame.image.load(path).convert_alpha()
        except pygame.error:
            pass
    return None


WEAPON_SPRITES = {"bullet": _load_weapon_sprite("bullet")}


def draw_projectiles_net(surf, projectiles, p1_color, p2_color):
    """projectiles: list dict polos dari to_dict() server
    (state['projectiles']) - {'x', 'y', 'kind', 'owner'}."""
    bullet_img = WEAPON_SPRITES.get("bullet")
    for proj in projectiles:
        color = p1_color if proj.get("owner") == 1 else p2_color
        cx, cy = int(proj["x"]), int(proj["y"])
        if bullet_img:
            pygame.draw.circle(surf, color, (cx, cy), 7, 2)
            rect = bullet_img.get_rect(center=(cx, cy))
            surf.blit(bullet_img, rect)
        else:
            pygame.draw.circle(surf, (255, 230, 120), (cx, cy), 5)
            pygame.draw.circle(surf, color, (cx, cy), 5, 2)


# ---------------------------------------------------------------------------
# Jaringan
# ---------------------------------------------------------------------------
class NetClient:
    """Client UDP background-thread. Pengiriman bersifat fire-and-forget;
    thread penerima cuma menyimpan state terbaru supaya render loop bisa
    membacanya tanpa pernah blocking di jaringan."""

    def __init__(self, server_addr):
        self.server_addr = server_addr
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.5)
        self.latest_state = None
        self.player_num = None
        self.connected = False
        self.full_lobby = False
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

    def _send(self, payload):
        try:
            self.sock.sendto(json.dumps(payload).encode("utf-8"), self.server_addr)
        except OSError:
            pass

    def send_join(self):
        self._send({"type": "join"})

    def send_input(self, intent):
        self._send({"type": "input", "intent": intent})

    def send_card_nav(self, nav):
        self._send({"type": "card_nav", "nav": nav})

    def send_reset(self):
        self._send({"type": "reset_match"})

    def send_config_rounds(self, rounds_to_win):
        self._send({"type": "config_rounds", "rounds_to_win": rounds_to_win})

    def send_config_guaranteed_weapons(self, enabled):
        self._send({"type": "config_guaranteed_weapons", "enabled": enabled})

    def _recv_loop(self):
        while self._running:
            try:
                data, _ = self.sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            with self._lock:
                if msg.get("type") == "welcome":
                    self.player_num = msg.get("player_num")
                    self.connected = True
                elif msg.get("type") == "full":
                    self.full_lobby = True
                elif msg.get("type") == "state":
                    self.latest_state = msg

    def get_state(self):
        with self._lock:
            return self.latest_state

    def close(self):
        self._running = False
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Konfigurasi / warna (cocok dengan prototipe lokal)
# ---------------------------------------------------------------------------
SCREEN_W, SCREEN_H = gc.SCREEN_W, gc.SCREEN_H
GROUND_Y = gc.GROUND_Y
FPS = gc.FPS

WHITE = (245, 245, 245)
BG = (30, 32, 40)
GROUND_COLOR = (55, 58, 70)
BLUE = (90, 160, 255)
RED = (255, 100, 100)
YELLOW = (255, 215, 90)
GREEN = (110, 220, 140)
GREY = (120, 122, 130)
CARD_BG = (45, 48, 60)
CARD_BORDER_SEL = (255, 215, 90)
CARD_UNIQUE_COLOR = (190, 120, 255)
CARD_RARE_COLOR = (80, 190, 255)

PLAYER_COLOR_OPTIONS = [
    BLUE, RED, GREEN, YELLOW,
    (255, 140, 0), (210, 90, 230), WHITE, (90, 220, 220),
]
p1_color_idx = 0
p2_color_idx = 1


def recolor_surface(surf, color, ink_only=False):
    # Dijaga tetap sinkron dengan fungsi yang sama di stickman_brawler.py
    # (lihat docstring-nya untuk alasan kenapa ink_only ada:
    # heavy_sword/heavy_spear/heavy_gun memanggang pixel senjata berwarna
    # ke dalam gambar yang sama dengan ink tubuh, jadi BLEND_RGB_MAX di
    # seluruh surface juga akan mewarnai senjatanya).
    tinted = surf.copy()
    if not ink_only:
        tinted.fill(color, special_flags=pygame.BLEND_RGB_MAX)
        return tinted
    w, h = tinted.get_size()
    surf.lock()
    tinted.lock()
    for y in range(h):
        for x in range(w):
            r, g, b, a = surf.get_at((x, y))
            if a > 0 and r < 40 and g < 40 and b < 40:
                tinted.set_at((x, y), (color[0], color[1], color[2], a))
    tinted.unlock()
    surf.unlock()
    return tinted


# ---------------------------------------------------------------------------
# Latar arena pertarungan (dipilih dari Settings) - bersifat lokal/kosmetik,
# sama seperti warna stickman: tidak disinkronkan ke pemain lain lewat
# jaringan.
# ---------------------------------------------------------------------------
def make_gradient_bg(top_color, bottom_color):
    surf = pygame.Surface((SCREEN_W, SCREEN_H))
    for y in range(SCREEN_H):
        t = y / max(1, SCREEN_H - 1)
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * t)
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * t)
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * t)
        pygame.draw.line(surf, (r, g, b), (0, y), (SCREEN_W, y))
    return surf


def make_grid_bg():
    surf = pygame.Surface((SCREEN_W, SCREEN_H))
    surf.fill((24, 26, 34))
    for x in range(0, SCREEN_W, 40):
        pygame.draw.line(surf, (40, 43, 56), (x, 0), (x, SCREEN_H))
    for y in range(0, SCREEN_H, 40):
        pygame.draw.line(surf, (40, 43, 56), (0, y), (SCREEN_W, y))
    return surf


BACKGROUND_PRESETS = {}
BACKGROUND_NAMES = []
background_idx = 0
custom_background_surface = None  # dimuat secara lazy sekali saja setelah pemain memilih foto
custom_background_path = None


def init_background_presets():
    global BACKGROUND_PRESETS, BACKGROUND_NAMES
    BACKGROUND_PRESETS = {
        "Default": None,
        "Sunset": make_gradient_bg((255, 140, 90), (35, 20, 55)),
        "Night Sky": make_gradient_bg((20, 22, 55), (6, 6, 16)),
        "Arena Grid": make_grid_bg(),
    }
    BACKGROUND_NAMES = list(BACKGROUND_PRESETS.keys()) + ["Custom Image"]


def open_background_file_dialog():
    """Pemilih file bawaan OS agar pemain bisa memakai foto dari
    perangkatnya sebagai latar arena. Murni lokal - hanya jendela client ini
    yang berubah, layar pemain lain tidak terpengaruh."""
    global custom_background_surface, custom_background_path, background_idx
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        print("Could not open file picker - tkinter isn't available.")
        return
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Choose a battlefield background image",
        filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All files", "*.*")],
    )
    root.destroy()
    if not path:
        return
    try:
        img = pygame.image.load(path).convert()
        img = pygame.transform.smoothscale(img, (SCREEN_W, SCREEN_H))
    except Exception as e:
        print(f"Could not load background image: {e}")
        return
    custom_background_surface = img
    custom_background_path = path
    background_idx = BACKGROUND_NAMES.index("Custom Image")


def cycle_background(delta):
    global background_idx
    background_idx = (background_idx + delta) % len(BACKGROUND_NAMES)


def current_background_surface():
    name = BACKGROUND_NAMES[background_idx]
    if name == "Custom Image":
        return custom_background_surface
    return BACKGROUND_PRESETS[name]


def current_background_label():
    name = BACKGROUND_NAMES[background_idx]
    if name == "Custom Image" and custom_background_surface is None:
        return "Custom (none picked)"
    return name


# ---------------------------------------------------------------------------
# Overlay wajah (dipilih dari Settings): menempelkan gambar pilihan pemain,
# dipotong menjadi lingkaran, di atas kepala stickman tersebut. Tiap pemain
# memilih gambarnya sendiri secara independen (konsepnya sama dengan warna
# stickman per pemain). Murni kosmetik/lokal - tidak disinkronkan lewat
# jaringan dan tidak memengaruhi deteksi hit.
# ---------------------------------------------------------------------------
FACE_OVERLAY_SIZE = 60  # px, diameter overlay lingkaran
# Jarak (px) lurus ke atas dari titik tumpu kaki petarung (self.y) ke pusat
# kepalanya - lihat konstanta/komentar yang sesuai di stickman_brawler.py untuk
# cara pengukurannya.
FACE_OVERLAY_HEAD_OFFSET_Y = 171

# Pusat lingkaran kepala untuk setiap frame kematian, dalam koordinat piksel
# sprite mentah (menghadap kiri) dari gambar frame itu sendiri - diukur dari
# artnya. FACE_OVERLAY_HEAD_OFFSET_Y yang tetap hanya cocok untuk pose tegak;
# kepala petarung yang sekarat berakhir dekat tanah, jadi state kematian
# menempatkan overlay wajah berdasarkan nilai-nilai ini (lihat
# _face_overlay_center). Ukur ulang kalau art kematian digambar ulang.
DEATH_HEAD_LOCAL = {
    "death":       [(114.0, 60.3), (154.4, 109.9), (175.1, 184.0)],
    "death_sword": [(70.5, 27.7), (166.7, 78.6), (184.7, 28.3)],
    "death_spear": [(78.5, 27.7), (166.7, 27.6), (184.7, 27.3)],
    "death_gun":   [(70.5, 27.7), (166.7, 27.6), (184.7, 27.3)],
}


def _face_overlay_center(anim_state, frame_idx, img_width, rect_left, rect_top, side, x, y):
    """Posisi layar untuk overlay wajah. Pose kematian mengikuti kepala yang
    digambar; state lainnya memakai offset tegak yang tetap."""
    heads = DEATH_HEAD_LOCAL.get(anim_state)
    if heads:
        hx, hy = heads[min(frame_idx, len(heads) - 1)]
        if side == "right":          # frame yang menghadap kanan adalah hasil flip horizontal dari art mentah
            hx = img_width - hx
        return int(rect_left + hx), int(rect_top + hy)
    return int(x), int(y) - FACE_OVERLAY_HEAD_OFFSET_Y

# Di-key berdasarkan nomor pemain (1, 2) - tiap pemain punya pilihannya sendiri
# yang independen.
face_overlay_enabled = {1: False, 2: False}
face_overlay_surface = {1: None, 2: None}  # dipotong melingkar, dimuat secara lazy
face_overlay_path = {1: None, 2: None}


def _make_circular_face_overlay(path, size=FACE_OVERLAY_SIZE):
    img = pygame.image.load(path).convert_alpha()
    w, h = img.get_size()
    side = min(w, h)
    crop_rect = pygame.Rect((w - side) // 2, (h - side) // 2, side, side)
    img = img.subsurface(crop_rect).copy()
    img = pygame.transform.smoothscale(img, (size, size))

    mask = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.circle(mask, (255, 255, 255, 255), (size // 2, size // 2), size // 2)

    circular = pygame.Surface((size, size), pygame.SRCALPHA)
    circular.blit(img, (0, 0))
    circular.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return circular


def open_face_file_dialog(player):
    """Pemilih file bawaan OS agar pemain bisa menempelkan foto di atas
    wajah stickman-nya sendiri. Murni lokal - hanya jendela client ini yang
    berubah, layar pemain lain tidak terpengaruh."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        print("Could not open file picker - tkinter isn't available.")
        return
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title=f"Choose a face image for Player {player}",
        filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All files", "*.*")],
    )
    root.destroy()
    if not path:
        return
    try:
        circular = _make_circular_face_overlay(path)
    except Exception as e:
        print(f"Could not load face image: {e}")
        return
    face_overlay_surface[player] = circular
    face_overlay_path[player] = path
    face_overlay_enabled[player] = True


def toggle_face_overlay(player):
    if face_overlay_surface[player] is not None:
        face_overlay_enabled[player] = not face_overlay_enabled[player]


def current_face_overlay_label(player):
    if face_overlay_surface[player] is None:
        return "No image"
    return "ON" if face_overlay_enabled[player] else "OFF"


clock = pygame.time.Clock()
font_big = pygame.font.SysFont("arial", 48, bold=True)
font_med = pygame.font.SysFont("arial", 28, bold=True)
font_small = pygame.font.SysFont("arial", 20)
init_background_presets()

ANIMATIONS = build_animation_library()
SFX = build_sfx_library()


def adjust_music_volume(delta):
    global MUSIC_VOLUME
    MUSIC_VOLUME = round(min(1.0, max(0.0, MUSIC_VOLUME + delta)), 2)
    try:
        pygame.mixer.music.set_volume(MUSIC_VOLUME)
    except pygame.error:
        pass


def adjust_sfx_volume(delta):
    global SFX_VOLUME
    SFX_VOLUME = round(min(1.0, max(0.0, SFX_VOLUME + delta)), 2)
    for variants in SFX.values():
        for snd in variants:
            snd.set_volume(SFX_VOLUME)


def cycle_color(which, delta):
    global p1_color_idx, p2_color_idx
    n = len(PLAYER_COLOR_OPTIONS)
    if which == 1:
        idx = p1_color_idx
        for _ in range(n):
            idx = (idx + delta) % n
            if idx != p2_color_idx:
                break
        p1_color_idx = idx
    else:
        idx = p2_color_idx
        for _ in range(n):
            idx = (idx + delta) % n
            if idx != p1_color_idx:
                break
        p2_color_idx = idx
    render_p1.rebuild_sprite_cache()
    render_p2.rebuild_sprite_cache()


# ---------------------------------------------------------------------------
# Helper HUD
# ---------------------------------------------------------------------------
def draw_hp_bar(surf, x, y, w, h, hp, max_hp, color, flip=False):
    pygame.draw.rect(surf, (60, 60, 60), (x, y, w, h))
    ratio = max(0, hp / max_hp)
    fill_w = int(w * ratio)
    if flip:
        pygame.draw.rect(surf, color, (x + w - fill_w, y, fill_w, h))
    else:
        pygame.draw.rect(surf, color, (x, y, fill_w, h))
    pygame.draw.rect(surf, WHITE, (x, y, w, h), 2)


def draw_text_center(surf, text, font, color, cx, cy):
    img = font.render(text, True, color)
    rect = img.get_rect(center=(cx, cy))
    surf.blit(img, rect)


class Button:
    def __init__(self, cx, cy, w, h, label, font=None):
        self.rect = pygame.Rect(0, 0, w, h)
        self.rect.center = (cx, cy)
        self.label = label
        self.font = font or font_med

    def draw(self, surf, mouse_pos):
        hovered = self.rect.collidepoint(mouse_pos)
        bg = (70, 74, 90) if hovered else (50, 53, 65)
        border = YELLOW if hovered else GREY
        pygame.draw.rect(surf, bg, self.rect, border_radius=10)
        pygame.draw.rect(surf, border, self.rect, 3, border_radius=10)
        draw_text_center(surf, self.label, self.font, WHITE, self.rect.centerx, self.rect.centery)

    def clicked(self, mouse_pos, mouse_click):
        return mouse_click and self.rect.collidepoint(mouse_pos)


# ---------------------------------------------------------------------------
# Panel statistik dalam pertandingan: tombol kecil di samping tiap bar HP yang
# membuka tampilan statistik petarung saat ini dan semua kartu upgrade yang
# sudah diambilnya. Label/nilai/nada stat berasal dari gc.format_stat_lines()
# sehingga sama persis dengan prototipe lokal. (Kode penggambarannya
# diduplikasi di kedua file, sama seperti rendering senjata - ini murni pygame,
# bukan sumber kebenaran simulasi.)
# ---------------------------------------------------------------------------
STATS_TONE_COLORS = {"good": (120, 220, 140), "bad": (235, 120, 120), "neutral": (215, 215, 215)}

P1_STATS_BTN_RECT = pygame.Rect(30 + 380 + 8, 30, 26, 26)
P2_STATS_BTN_RECT = pygame.Rect(SCREEN_W - 410 - 34, 30, 26, 26)


def draw_stats_toggle(surf, rect, mouse_pos, is_open):
    """Tombol kecil 'i' yang membuka/menutup panel statistik petarung."""
    hovered = rect.collidepoint(mouse_pos)
    if is_open:
        bg, border = (90, 95, 120), YELLOW
    elif hovered:
        bg, border = (70, 74, 90), YELLOW
    else:
        bg, border = (50, 53, 65), GREY
    pygame.draw.rect(surf, bg, rect, border_radius=6)
    pygame.draw.rect(surf, border, rect, 2, border_radius=6)
    draw_text_center(surf, "i", font_small, WHITE, rect.centerx, rect.centery)


def draw_stats_panel(surf, stats, cards, title, anchor_x, anchor_y, align_right=False):
    """Menggambar tampilan statistik + upgrade. anchor_x/anchor_y adalah
    pojok atas panel (kanan atas jika align_right, selain itu kiri atas)
    sehingga panel tiap pemain bisa menggantung di bawah bar HP-nya sendiri
    tanpa keluar layar."""
    stat_lines = gc.format_stat_lines(stats)
    card_counts = {}
    for name in cards:
        card_counts[name] = card_counts.get(name, 0) + 1
    card_lines = [f"{n} x{c}" if c > 1 else n for n, c in card_counts.items()]

    pad = 12
    line_h = 20
    header_h = 26
    width = 260
    body_lines = len(stat_lines) + 1 + max(1, len(card_lines))
    height = header_h + pad + body_lines * line_h + pad + 14

    x = anchor_x - width if align_right else anchor_x
    y = anchor_y
    if y + height > SCREEN_H - 10:
        y = max(10, SCREEN_H - 10 - height)

    panel = pygame.Surface((width, height), pygame.SRCALPHA)
    panel.fill((18, 20, 28, 235))
    surf.blit(panel, (x, y))
    pygame.draw.rect(surf, GREY, (x, y, width, height), 2, border_radius=8)

    cy = y + 8
    draw_text_center(surf, title, font_small, YELLOW, x + width // 2, cy + 8)
    cy += header_h

    for label, value, tone in stat_lines:
        color = STATS_TONE_COLORS.get(tone, WHITE)
        surf.blit(font_small.render(label, True, (170, 172, 185)), (x + pad, cy))
        val_img = font_small.render(value, True, color)
        surf.blit(val_img, (x + width - pad - val_img.get_width(), cy))
        cy += line_h

    cy += 4
    pygame.draw.line(surf, (70, 74, 90), (x + pad, cy), (x + width - pad, cy), 1)
    cy += 6
    surf.blit(font_small.render(f"Upgrades ({len(cards)})", True, YELLOW), (x + pad, cy))
    cy += line_h

    if not card_lines:
        surf.blit(font_small.render("None yet", True, (140, 142, 155)), (x + pad, cy))
    else:
        for line in card_lines:
            surf.blit(font_small.render(line, True, (215, 215, 215)), (x + pad, cy))
            cy += line_h


# ---------------------------------------------------------------------------
# RenderFighter - cermin murni kosmetik dari Fighter di sisi server. Tidak
# memegang logika game sendiri; setiap field fisik ditimpa setiap kali paket
# state baru datang dari server. State animasi/timing frame dan tinting sprite
# tetap dihitung secara lokal, karena itu urusan rendering yang tidak perlu
# diketahui server.
# ---------------------------------------------------------------------------
class RenderFighter:
    def __init__(self, color, player_num=None):
        self.color = color
        self.player_num = player_num  # 1 atau 2 - menentukan overlay wajah petarung ini
        self.x = 0.0
        self.y = GROUND_Y
        self.facing = 1
        self.hp = 100
        self.max_hp = 140
        self.on_ground = True
        self.blocking = False
        self.attack_type = None
        self.attack_timer = 0
        self.is_dead = False
        self.vel_y = 0
        self.cards = []
        self.weapon = None
        self.stats = {}

        self.is_moving = False
        self.hit_flash = 0
        self.crit_flash = 0
        self.overcrit_flash = 0
        self.dodge_flash = 0
        self.negate_flash = 0
        self.anim_state = "idle"
        self.anim_frame_idx = 0
        self.anim_timer = 0

        self.sprite_cache = {}
        self.rebuild_sprite_cache()

    def rebuild_sprite_cache(self):
        cache = {}
        for action, sides in ANIMATIONS.items():
            ink_only = action in MIXED_COLOR_ACTIONS
            cache[action] = {}
            for side, frames in sides.items():
                cache[action][side] = [(recolor_surface(surf, self.color, ink_only=ink_only), off)
                                        for surf, off in frames]
        self.sprite_cache = cache

    def apply_server_update(self, data):
        prev_x = self.x
        self.x, self.y = data["x"], data["y"]
        self.facing = data["facing"]
        self.hp, self.max_hp = data["hp"], data["max_hp"]
        self.on_ground = data["on_ground"]
        self.blocking = data["blocking"]
        self.attack_type = data["attack_type"]
        self.attack_timer = data["attack_timer"]
        self.is_dead = data["is_dead"]
        self.vel_y = data["vel_y"]
        self.cards = data["cards"]
        self.weapon = data.get("weapon")
        # Stat hanya ikut terkirim di sebagian paket (lihat Match.to_dict),
        # jadi simpan set terakhir yang diterima daripada mengosongkan panel.
        if "stats" in data:
            self.stats = data["stats"]
        self.is_moving = abs(self.x - prev_x) > 0.01 and self.attack_timer <= 0

        for ev in data.get("events", []):
            if ev == "jump":
                play_sfx("jump")
            elif ev in ("hit", "second_wind"):
                self.hit_flash = 8
                play_sfx("hit")
            elif ev == "crit":
                self.crit_flash = 24
                play_sfx("crit")
            elif ev == "overcrit":
                self.overcrit_flash = 24  # event "crit" (di atas) sudah menangani sfx-nya
            elif ev == "dodge":
                self.dodge_flash = 24
                play_sfx("dodge")
            elif ev == "negated":
                self.negate_flash = 24
                play_sfx("dodge")  # belum ada sfx khusus - pakai ulang blip "whiff"
            elif ev == "shoot":
                play_sfx("shoot")
            elif ev == "sword":
                play_sfx("sword")
            elif ev == "spear":
                play_sfx("spear")

        self._update_animation_state()

    def _update_animation_state(self):
        if self.hit_flash > 0:
            self.hit_flash -= 1
        if self.crit_flash > 0:
            self.crit_flash -= 1
        if self.overcrit_flash > 0:
            self.overcrit_flash -= 1
        if self.dodge_flash > 0:
            self.dodge_flash -= 1
        if self.negate_flash > 0:
            self.negate_flash -= 1

        if self.is_dead:
            death_state = WEAPON_DEATH_STATE.get(self.weapon)
            has_death_art = bool(self.sprite_cache.get(death_state, {}).get("right")) if death_state else False
            new_state = death_state if has_death_art else "death"
        elif self.hit_flash > 5:
            new_state = "hurt"
        elif self.attack_timer > 0 and self.attack_type == "light":
            light_state = WEAPON_LIGHT_STATE.get(self.weapon)
            has_light_art = bool(self.sprite_cache.get(light_state, {}).get("right")) if light_state else False
            new_state = light_state if has_light_art else "punch"
        elif self.attack_timer > 0 and self.attack_type == "heavy":
            heavy_state = WEAPON_HEAVY_STATE.get(self.weapon)
            has_heavy_art = bool(self.sprite_cache.get(heavy_state, {}).get("right")) if heavy_state else False
            new_state = heavy_state if has_heavy_art else "kick"
        elif self.blocking:
            if self.is_moving:
                block_walk_state = WEAPON_BLOCK_WALK_STATE.get(self.weapon)
                has_weapon_block_walk = (bool(self.sprite_cache.get(block_walk_state, {}).get("right"))
                                          if block_walk_state else False)
                if has_weapon_block_walk:
                    new_state = block_walk_state
                else:
                    has_block_walk = bool(self.sprite_cache.get("block_walk", {}).get("right"))
                    new_state = "block_walk" if has_block_walk else "block"
            else:
                block_state = WEAPON_BLOCK_STATE.get(self.weapon)
                has_weapon_block = bool(self.sprite_cache.get(block_state, {}).get("right")) if block_state else False
                new_state = block_state if has_weapon_block else "block"
        elif not self.on_ground:
            jump_state = WEAPON_JUMP_STATE.get(self.weapon)
            has_jump_art = bool(self.sprite_cache.get(jump_state, {}).get("right")) if jump_state else False
            new_state = jump_state if has_jump_art else "jump"
        elif self.is_moving:
            walk_state = WEAPON_WALK_STATE.get(self.weapon)
            has_walk_art = bool(self.sprite_cache.get(walk_state, {}).get("right")) if walk_state else False
            new_state = walk_state if has_walk_art else "walk"
        else:
            idle_state = WEAPON_IDLE_STATE.get(self.weapon)
            has_idle_art = bool(self.sprite_cache.get(idle_state, {}).get("right")) if idle_state else False
            new_state = idle_state if has_idle_art else "idle"

        if new_state != self.anim_state:
            self.anim_state = new_state
            self.anim_frame_idx = 0
            self.anim_timer = 0
            return

        if self.anim_state == "jump" or self.anim_state in WEAPON_JUMP_STATE.values():
            frames = self.sprite_cache.get(self.anim_state, {}).get("right", [])
            if frames:
                self.anim_frame_idx = 0 if self.vel_y < 0 else min(1, len(frames) - 1)
            return

        self.anim_timer += 1
        duration = ANIM_FRAME_DURATION.get(self.anim_state, 10)
        if self.anim_timer >= duration:
            self.anim_timer = 0
            frames = self.sprite_cache.get(self.anim_state, {}).get("right", [])
            if frames:
                hold_states = (("death", "hurt", "punch", "kick") + tuple(WEAPON_HEAVY_STATE.values())
                               + tuple(WEAPON_LIGHT_STATE.values()) + tuple(WEAPON_DEATH_STATE.values()))
                if self.anim_state in hold_states and self.anim_frame_idx >= len(frames) - 1:
                    pass
                else:
                    self.anim_frame_idx = (self.anim_frame_idx + 1) % len(frames)

    def draw(self, surf):
        frames_dict = self.sprite_cache.get(self.anim_state)
        side = "right" if self.facing == 1 else "left"
        frames = frames_dict[side] if frames_dict else []
        face_center = _face_overlay_center(None, 0, 0, 0, 0, side, self.x, self.y)
        if frames:
            idx = min(self.anim_frame_idx, len(frames) - 1)
            img, foot_offset = frames[idx]
            rect = img.get_rect(midbottom=(int(self.x), int(self.y) + foot_offset))
            surf.blit(img, rect)
            face_center = _face_overlay_center(self.anim_state, idx, img.get_width(),
                                               rect.left, rect.top, side, self.x, self.y)
        else:
            self.draw_procedural(surf)

        if face_overlay_enabled.get(self.player_num) and face_overlay_surface.get(self.player_num) is not None:
            face_img = face_overlay_surface[self.player_num]
            face_rect = face_img.get_rect(center=face_center)
            surf.blit(face_img, face_rect)

        if self.overcrit_flash > 0:
            draw_text_center(surf, "OVERCRIT!", font_small, (255, 60, 60), int(self.x), int(self.y) - 140)
        elif self.crit_flash > 0:
            draw_text_center(surf, "CRIT!", font_small, (255, 140, 40), int(self.x), int(self.y) - 140)
        if self.dodge_flash > 0:
            draw_text_center(surf, "DODGE!", font_small, (120, 220, 255), int(self.x), int(self.y) - 140)
        if self.negate_flash > 0:
            draw_text_center(surf, "NEGATED!", font_small, (255, 215, 90), int(self.x), int(self.y) - 140)

    def draw_procedural(self, surf):
        color = YELLOW if self.hit_flash > 0 else self.color
        cx, cy = int(self.x), int(self.y)
        head_r = 14
        body_top = cy - 90
        body_bottom = cy - 35
        hip = (cx, body_bottom)
        head_c = (cx, cy - 105)
        leg_spread = 14
        arm_len = 22

        if self.on_ground:
            l1 = (cx - leg_spread, cy)
            l2 = (cx + leg_spread, cy)
        else:
            l1 = (cx - 10, cy - 10)
            l2 = (cx + 16, cy - 5)
        pygame.draw.line(surf, color, hip, l1, 5)
        pygame.draw.line(surf, color, hip, l2, 5)
        pygame.draw.line(surf, color, (cx, body_top), hip, 5)

        shoulder = (cx, body_top + 10)
        if self.blocking:
            a1 = (cx + self.facing * 10, body_top - 5)
            a2 = (cx + self.facing * 10, body_top + 20)
        elif self.attack_timer > 0:
            reach = 35 if self.attack_type == "light" else 50
            a1 = (cx + self.facing * reach, shoulder[1] - 5)
            a2 = (cx - self.facing * 8, shoulder[1] + 15)
        else:
            a1 = (cx - arm_len // 2, shoulder[1] + 18)
            a2 = (cx + arm_len // 2, shoulder[1] + 18)
        pygame.draw.line(surf, color, shoulder, a1, 4)
        pygame.draw.line(surf, color, shoulder, a2, 4)
        pygame.draw.circle(surf, color, head_c, head_r)

        if self.blocking:
            shield_x = cx + self.facing * 22
            pygame.draw.circle(surf, GREEN, (shield_x, cy - 65), 10, 2)


render_p1 = RenderFighter(PLAYER_COLOR_OPTIONS[p1_color_idx], player_num=1)
render_p2 = RenderFighter(PLAYER_COLOR_OPTIONS[p2_color_idx], player_num=2)

# ---------------------------------------------------------------------------
# Skema kontrol lokal - sekarang satu keyboard per client, jadi hanya ada satu
# skema apa pun slot pemain yang diberikan server.
# ---------------------------------------------------------------------------
LOCAL_KEYS = {
    "left": pygame.K_a, "right": pygame.K_d, "jump": pygame.K_w,
    "light": pygame.K_f, "heavy": pygame.K_g, "block": pygame.K_s,
}

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
STATE_MAIN_MENU = "main_menu"
STATE_SETTINGS = "settings"
STATE_ENTER_ADDRESS = "enter_address"
STATE_SELECT_ROUNDS = "select_rounds"  # hanya host, ditampilkan setelah memasukkan alamat
STATE_CONNECTING = "connecting"
STATE_GAME = "game"   # render pertandingan sebenarnya - field "state" dari server yang menentukan detailnya

ui_state = STATE_MAIN_MENU
net = None
server_addr = ("127.0.0.1", 5555)
address_input_text = "127.0.0.1:5555"
address_error = ""

# Toggle panel statistik (tombol "i" kecil di samping tiap bar HP).
show_p1_stats = False
show_p2_stats = False

# Alamat yang berarti "saya juga menjalankan server di mesin ini" - artinya
# client ini adalah host, jadi boleh memilih panjang pertandingan.
HOST_ADDRESSES = ("127.0.0.1", "localhost", "::1")
is_host = False
rounds_input_text = str(gc.ROUNDS_TO_WIN_MATCH)
rounds_error = ""
chosen_rounds_to_win = gc.ROUNDS_TO_WIN_MATCH
# Opsi pertandingan "Guaranteed Weapons" - khusus host, diatur di layar Match
# Length yang sama dengan rounds_to_win, dikirim ke server setelah terhubung.
chosen_guaranteed_weapons = False

menu_play_btn = Button(SCREEN_W // 2, 260, 260, 60, "Play")
menu_settings_btn = Button(SCREEN_W // 2, 340, 260, 60, "Settings")
menu_quit_btn = Button(SCREEN_W // 2, 420, 260, 60, "Quit")

settings_music_minus_btn = Button(SCREEN_W // 2 + 90, 110, 50, 50, "-")
settings_music_plus_btn = Button(SCREEN_W // 2 + 160, 110, 50, 50, "+")
settings_sfx_minus_btn = Button(SCREEN_W // 2 + 90, 165, 50, 50, "-")
settings_sfx_plus_btn = Button(SCREEN_W // 2 + 160, 165, 50, 50, "+")
settings_p1_color_prev_btn = Button(SCREEN_W // 2 + 60, 220, 50, 50, "<")
settings_p1_color_next_btn = Button(SCREEN_W // 2 + 200, 220, 50, 50, ">")
settings_p2_color_prev_btn = Button(SCREEN_W // 2 + 60, 275, 50, 50, "<")
settings_p2_color_next_btn = Button(SCREEN_W // 2 + 200, 275, 50, 50, ">")
settings_bg_prev_btn = Button(SCREEN_W // 2 + 60, 330, 50, 50, "<")
settings_bg_next_btn = Button(SCREEN_W // 2 + 200, 330, 50, 50, ">")
settings_bg_load_btn = Button(SCREEN_W // 2 + 355, 330, 200, 46, "Load Image...", font_small)
settings_p1_face_toggle_btn = Button(SCREEN_W // 2 - 310, 435, 130, 48, "P1: OFF")
settings_p1_face_load_btn = Button(SCREEN_W // 2 - 105, 435, 190, 48, "Load P1 Photo")
settings_p2_face_toggle_btn = Button(SCREEN_W // 2 + 130, 435, 130, 48, "P2: OFF")
settings_p2_face_load_btn = Button(SCREEN_W // 2 + 335, 435, 190, 48, "Load P2 Photo")
settings_back_btn = Button(SCREEN_W // 2, 495, 200, 55, "Back")

address_box_rect = pygame.Rect(0, 0, 420, 55)
address_box_rect.center = (SCREEN_W // 2, 260)
address_connect_btn = Button(SCREEN_W // 2, 350, 220, 55, "Connect")
address_back_btn = Button(SCREEN_W // 2, 420, 220, 55, "Back")

rounds_box_rect = pygame.Rect(0, 0, 160, 55)
rounds_box_rect.center = (SCREEN_W // 2, 260)
rounds_guaranteed_weapons_btn = Button(SCREEN_W // 2, 325, 320, 50, "Guaranteed Weapons: OFF")
rounds_confirm_btn = Button(SCREEN_W // 2, 395, 260, 55, "Host Match")
rounds_back_btn = Button(SCREEN_W // 2, 460, 220, 55, "Back")


def parse_server_address(raw):
    raw = raw.strip()
    if not raw:
        return ("127.0.0.1", 5555)
    if ":" in raw:
        host, port_str = raw.rsplit(":", 1)
        return (host, int(port_str))
    return (raw, 5555)


def confirm_rounds_and_connect():
    """Memvalidasi jumlah ronde yang diketik, lalu terhubung sebagai host
    dengan nilai itu."""
    global rounds_error, chosen_rounds_to_win, net, ui_state
    try:
        n = int(rounds_input_text)
    except ValueError:
        rounds_error = "Enter a whole number"
        return
    if n < 1:
        rounds_error = "Must be at least 1"
        return
    chosen_rounds_to_win = min(n, 50)
    pygame.key.stop_text_input()
    net = NetClient(server_addr)
    ui_state = STATE_CONNECTING


start_background_music()

prev_keys = pygame.key.get_pressed()
running = True
while running:
    clock.tick(FPS)
    keys_pressed = pygame.key.get_pressed()
    mouse_pos = pygame.mouse.get_pos()
    mouse_click = False

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mouse_click = True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if ui_state in (STATE_SETTINGS, STATE_ENTER_ADDRESS, STATE_SELECT_ROUNDS):
                pygame.key.stop_text_input()
                ui_state = STATE_MAIN_MENU
            else:
                running = False
        if event.type == pygame.KEYDOWN and event.key == pygame.K_r:
            if ui_state == STATE_GAME and net:
                net.send_reset()
        if event.type == pygame.KEYDOWN and event.key == pygame.K_m:
            if ui_state == STATE_GAME:
                if net:
                    net.close()
                    net = None
                ui_state = STATE_MAIN_MENU
        if ui_state == STATE_ENTER_ADDRESS:
            if event.type == pygame.TEXTINPUT:
                if len(address_input_text) < 40:
                    address_input_text += event.text
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_BACKSPACE:
                    address_input_text = address_input_text[:-1]
                elif event.key == pygame.K_RETURN:
                    try:
                        server_addr = parse_server_address(address_input_text)
                        address_error = ""
                        if server_addr[0] in HOST_ADDRESSES:
                            is_host = True
                            rounds_input_text = str(chosen_rounds_to_win)
                            rounds_error = ""
                            ui_state = STATE_SELECT_ROUNDS
                        else:
                            is_host = False
                            pygame.key.stop_text_input()
                            net = NetClient(server_addr)
                            ui_state = STATE_CONNECTING
                    except ValueError:
                        address_error = "Invalid address - use host or host:port"
        elif ui_state == STATE_SELECT_ROUNDS:
            if event.type == pygame.TEXTINPUT:
                if event.text.isdigit() and len(rounds_input_text) < 3:
                    rounds_input_text += event.text
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_BACKSPACE:
                    rounds_input_text = rounds_input_text[:-1]
                elif event.key == pygame.K_RETURN:
                    confirm_rounds_and_connect()

    bg_surf = current_background_surface()
    if bg_surf is not None:
        screen.blit(bg_surf, (0, 0))
        ground_strip = pygame.Surface((SCREEN_W, SCREEN_H - GROUND_Y), pygame.SRCALPHA)
        ground_strip.fill((*GROUND_COLOR, 170))
        screen.blit(ground_strip, (0, GROUND_Y))
    else:
        screen.fill(BG)
        pygame.draw.rect(screen, GROUND_COLOR, (0, GROUND_Y, SCREEN_W, SCREEN_H - GROUND_Y))

    # ------------------------------------------------------------------
    if ui_state == STATE_MAIN_MENU:
        render_p1.draw(screen)
        render_p2.draw(screen)
        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        screen.blit(overlay, (0, 0))

        draw_text_center(screen, "STICKMAN BRAWLER - ONLINE", font_big, YELLOW, SCREEN_W // 2, 150)

        for btn in (menu_play_btn, menu_settings_btn, menu_quit_btn):
            btn.draw(screen, mouse_pos)

        if menu_play_btn.clicked(mouse_pos, mouse_click):
            pygame.key.start_text_input()
            ui_state = STATE_ENTER_ADDRESS
        elif menu_settings_btn.clicked(mouse_pos, mouse_click):
            ui_state = STATE_SETTINGS
        elif menu_quit_btn.clicked(mouse_pos, mouse_click):
            running = False

    # ------------------------------------------------------------------
    elif ui_state == STATE_ENTER_ADDRESS:
        render_p1.draw(screen)
        render_p2.draw(screen)
        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        screen.blit(overlay, (0, 0))

        draw_text_center(screen, "SERVER ADDRESS", font_big, YELLOW, SCREEN_W // 2, 150)
        draw_text_center(screen, "e.g. 127.0.0.1:5555, a LAN IP, or a playit.gg address",
                          font_small, GREY, SCREEN_W // 2, 195)

        box_color = (70, 74, 90)
        pygame.draw.rect(screen, box_color, address_box_rect, border_radius=8)
        pygame.draw.rect(screen, YELLOW, address_box_rect, 2, border_radius=8)
        draw_text_center(screen, address_input_text, font_med, WHITE,
                          address_box_rect.centerx, address_box_rect.centery)

        if address_error:
            draw_text_center(screen, address_error, font_small, RED, SCREEN_W // 2, 300)

        address_connect_btn.draw(screen, mouse_pos)
        address_back_btn.draw(screen, mouse_pos)

        if address_connect_btn.clicked(mouse_pos, mouse_click):
            try:
                server_addr = parse_server_address(address_input_text)
                address_error = ""
                if server_addr[0] in HOST_ADDRESSES:
                    is_host = True
                    rounds_input_text = str(chosen_rounds_to_win)
                    rounds_error = ""
                    ui_state = STATE_SELECT_ROUNDS
                else:
                    is_host = False
                    pygame.key.stop_text_input()
                    net = NetClient(server_addr)
                    ui_state = STATE_CONNECTING
            except ValueError:
                address_error = "Invalid address - use host or host:port"
        elif address_back_btn.clicked(mouse_pos, mouse_click):
            pygame.key.stop_text_input()
            ui_state = STATE_MAIN_MENU

    # ------------------------------------------------------------------
    elif ui_state == STATE_SELECT_ROUNDS:
        render_p1.draw(screen)
        render_p2.draw(screen)
        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        screen.blit(overlay, (0, 0))

        draw_text_center(screen, "MATCH LENGTH", font_big, YELLOW, SCREEN_W // 2, 150)
        draw_text_center(screen, "You're hosting - how many round wins takes the match?",
                          font_small, GREY, SCREEN_W // 2, 195)

        box_color = (70, 74, 90)
        pygame.draw.rect(screen, box_color, rounds_box_rect, border_radius=8)
        pygame.draw.rect(screen, YELLOW, rounds_box_rect, 2, border_radius=8)
        draw_text_center(screen, rounds_input_text, font_med, WHITE,
                          rounds_box_rect.centerx, rounds_box_rect.centery)

        if rounds_error:
            draw_text_center(screen, rounds_error, font_small, RED, SCREEN_W // 2, 300)

        rounds_guaranteed_weapons_btn.label = (
            f"Guaranteed Weapons: {'ON' if chosen_guaranteed_weapons else 'OFF'}")
        rounds_guaranteed_weapons_btn.draw(screen, mouse_pos)
        rounds_confirm_btn.draw(screen, mouse_pos)
        rounds_back_btn.draw(screen, mouse_pos)

        if rounds_guaranteed_weapons_btn.clicked(mouse_pos, mouse_click):
            chosen_guaranteed_weapons = not chosen_guaranteed_weapons
        elif rounds_confirm_btn.clicked(mouse_pos, mouse_click):
            confirm_rounds_and_connect()
        elif rounds_back_btn.clicked(mouse_pos, mouse_click):
            ui_state = STATE_ENTER_ADDRESS

    # ------------------------------------------------------------------
    elif ui_state == STATE_SETTINGS:
        render_p1.draw(screen)
        render_p2.draw(screen)
        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        screen.blit(overlay, (0, 0))

        draw_text_center(screen, "SETTINGS", font_big, YELLOW, SCREEN_W // 2, 55)
        draw_text_center(screen, f"Music Volume: {int(MUSIC_VOLUME * 100)}%",
                          font_med, WHITE, SCREEN_W // 2 - 90, 110)
        draw_text_center(screen, f"SFX Volume: {int(SFX_VOLUME * 100)}%",
                          font_med, WHITE, SCREEN_W // 2 - 90, 165)

        draw_text_center(screen, "Player 1 Color:", font_med, WHITE, SCREEN_W // 2 - 130, 220)
        pygame.draw.circle(screen, PLAYER_COLOR_OPTIONS[p1_color_idx], (SCREEN_W // 2 + 130, 220), 18)
        pygame.draw.circle(screen, WHITE, (SCREEN_W // 2 + 130, 220), 18, 2)

        draw_text_center(screen, "Player 2 Color:", font_med, WHITE, SCREEN_W // 2 - 130, 275)
        pygame.draw.circle(screen, PLAYER_COLOR_OPTIONS[p2_color_idx], (SCREEN_W // 2 + 130, 275), 18)
        pygame.draw.circle(screen, WHITE, (SCREEN_W // 2 + 130, 275), 18, 2)

        draw_text_center(screen, "Background:", font_med, WHITE, SCREEN_W // 2 - 130, 330)
        draw_text_center(screen, current_background_label(), font_small, WHITE, SCREEN_W // 2 + 130, 330)

        draw_text_center(screen, "Face Overlay (per player):", font_small, WHITE, SCREEN_W // 2, 390)
        settings_p1_face_toggle_btn.label = f"P1: {current_face_overlay_label(1)}"
        settings_p2_face_toggle_btn.label = f"P2: {current_face_overlay_label(2)}"

        for btn in (settings_music_minus_btn, settings_music_plus_btn,
                    settings_sfx_minus_btn, settings_sfx_plus_btn,
                    settings_p1_color_prev_btn, settings_p1_color_next_btn,
                    settings_p2_color_prev_btn, settings_p2_color_next_btn,
                    settings_bg_prev_btn, settings_bg_next_btn, settings_bg_load_btn,
                    settings_p1_face_toggle_btn, settings_p1_face_load_btn,
                    settings_p2_face_toggle_btn, settings_p2_face_load_btn,
                    settings_back_btn):
            btn.draw(screen, mouse_pos)

        if settings_music_minus_btn.clicked(mouse_pos, mouse_click):
            adjust_music_volume(-0.1)
        elif settings_music_plus_btn.clicked(mouse_pos, mouse_click):
            adjust_music_volume(0.1)
        elif settings_sfx_minus_btn.clicked(mouse_pos, mouse_click):
            adjust_sfx_volume(-0.1)
        elif settings_sfx_plus_btn.clicked(mouse_pos, mouse_click):
            adjust_sfx_volume(0.1)
        elif settings_p1_color_prev_btn.clicked(mouse_pos, mouse_click):
            cycle_color(1, -1)
        elif settings_p1_color_next_btn.clicked(mouse_pos, mouse_click):
            cycle_color(1, 1)
        elif settings_p2_color_prev_btn.clicked(mouse_pos, mouse_click):
            cycle_color(2, -1)
        elif settings_p2_color_next_btn.clicked(mouse_pos, mouse_click):
            cycle_color(2, 1)
        elif settings_bg_prev_btn.clicked(mouse_pos, mouse_click):
            cycle_background(-1)
        elif settings_bg_next_btn.clicked(mouse_pos, mouse_click):
            cycle_background(1)
        elif settings_bg_load_btn.clicked(mouse_pos, mouse_click):
            open_background_file_dialog()
        elif settings_p1_face_toggle_btn.clicked(mouse_pos, mouse_click):
            toggle_face_overlay(1)
        elif settings_p1_face_load_btn.clicked(mouse_pos, mouse_click):
            open_face_file_dialog(1)
        elif settings_p2_face_toggle_btn.clicked(mouse_pos, mouse_click):
            toggle_face_overlay(2)
        elif settings_p2_face_load_btn.clicked(mouse_pos, mouse_click):
            open_face_file_dialog(2)
        elif settings_back_btn.clicked(mouse_pos, mouse_click):
            ui_state = STATE_MAIN_MENU

    # ------------------------------------------------------------------
    elif ui_state == STATE_CONNECTING:
        net.send_join()
        if is_host:
            net.send_config_rounds(chosen_rounds_to_win)
            net.send_config_guaranteed_weapons(chosen_guaranteed_weapons)
        draw_text_center(screen, "CONNECTING...", font_big, YELLOW, SCREEN_W // 2, SCREEN_H // 2 - 40)
        if net.player_num:
            draw_text_center(screen, f"You are Player {net.player_num} - waiting for opponent...",
                              font_med, WHITE, SCREEN_W // 2, SCREEN_H // 2 + 10)
        elif net.full_lobby:
            draw_text_center(screen, "Server is full (2 players already connected)",
                              font_med, RED, SCREEN_W // 2, SCREEN_H // 2 + 10)
        else:
            draw_text_center(screen, f"Contacting {server_addr[0]}:{server_addr[1]} ...",
                              font_med, WHITE, SCREEN_W // 2, SCREEN_H // 2 + 10)
        draw_text_center(screen, "[ESC] cancel", font_small, GREY, SCREEN_W // 2, SCREEN_H - 30)

        state = net.get_state()
        if state and state.get("state") != gc.Match.STATE_WAITING:
            ui_state = STATE_GAME

    # ------------------------------------------------------------------
    elif ui_state == STATE_GAME:
        state = net.get_state()

        intent = {
            "left": keys_pressed[LOCAL_KEYS["left"]],
            "right": keys_pressed[LOCAL_KEYS["right"]],
            "jump": keys_pressed[LOCAL_KEYS["jump"]],
            "light": keys_pressed[LOCAL_KEYS["light"]],
            "heavy": keys_pressed[LOCAL_KEYS["heavy"]],
            "block": keys_pressed[LOCAL_KEYS["block"]],
        }
        net.send_input(intent)

        if state is None:
            draw_text_center(screen, "Waiting for server...", font_med, WHITE, SCREEN_W // 2, SCREEN_H // 2)
        else:
            render_p1.apply_server_update(state["p1"])
            render_p2.apply_server_update(state["p2"])
            render_p1.draw(screen)
            render_p2.draw(screen)
            draw_projectiles_net(screen, state.get("projectiles", []), render_p1.color, render_p2.color)

            you_are = net.player_num
            server_state = state["state"]

            if server_state in (gc.Match.STATE_FIGHT, gc.Match.STATE_ROUND_END):
                draw_hp_bar(screen, 30, 30, 380, 26, state["p1"]["hp"], state["p1"]["max_hp"], GREEN)
                draw_hp_bar(screen, SCREEN_W - 410, 30, 380, 26, state["p2"]["hp"], state["p2"]["max_hp"], GREEN, flip=True)
                p1_label = "P1 (YOU)" if you_are == 1 else "P1"
                p2_label = "P2 (YOU)" if you_are == 2 else "P2"
                if render_p1.weapon:
                    p1_label += f" - {render_p1.weapon.capitalize()}"
                if render_p2.weapon:
                    p2_label += f" - {render_p2.weapon.capitalize()}"
                draw_text_center(screen, p1_label, font_small, WHITE, 30 + 40, 62)
                draw_text_center(screen, p2_label, font_small, WHITE, SCREEN_W - 30 - 40, 62)

                if mouse_click:
                    if P1_STATS_BTN_RECT.collidepoint(mouse_pos):
                        show_p1_stats = not show_p1_stats
                    elif P2_STATS_BTN_RECT.collidepoint(mouse_pos):
                        show_p2_stats = not show_p2_stats
                draw_stats_toggle(screen, P1_STATS_BTN_RECT, mouse_pos, show_p1_stats)
                draw_stats_toggle(screen, P2_STATS_BTN_RECT, mouse_pos, show_p2_stats)
                if show_p1_stats:
                    draw_stats_panel(screen, render_p1.stats, render_p1.cards,
                                      "P1 (YOU)" if you_are == 1 else "Player 1", 30, 80)
                if show_p2_stats:
                    draw_stats_panel(screen, render_p2.stats, render_p2.cards,
                                      "P2 (YOU)" if you_are == 2 else "Player 2",
                                      SCREEN_W - 30, 80, align_right=True)

                secs_left = max(0, state["round_time_left"] // FPS)
                draw_text_center(screen, str(secs_left), font_med, WHITE, SCREEN_W // 2, 45)
                rounds_line = (f"Rounds: {state['p1_round_wins']} - {state['p2_round_wins']}"
                               f" (first to {state.get('rounds_to_win', gc.ROUNDS_TO_WIN_MATCH)})")
                if state.get("guaranteed_weapons"):
                    rounds_line += "  \u2022  Guaranteed Weapons"
                draw_text_center(screen, rounds_line, font_small, GREY, SCREEN_W // 2, 80)
                draw_text_center(screen, "[R] reset match   [M] main menu", font_small, GREY,
                                  SCREEN_W // 2, SCREEN_H - 20)

                if server_state == gc.Match.STATE_ROUND_END:
                    draw_text_center(screen, state["round_end_message"], font_big, YELLOW,
                                      SCREEN_W // 2, SCREEN_H // 2 - 30)

            elif server_state == gc.Match.STATE_CARD_SELECT:
                overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
                overlay.fill((0, 0, 0, 180))
                screen.blit(overlay, (0, 0))

                picker_num = state["card_picker_num"]
                is_my_pick = (picker_num == you_are)
                who = f"PLAYER {picker_num}" + (" (YOU)" if is_my_pick else "")
                draw_text_center(screen, f"{who} LOST THE ROUND - PICKING AN UPGRADE",
                                  font_med, WHITE, SCREEN_W // 2, 90)

                card_options = state["card_options"]
                card_selected_idx = state["card_selected_idx"]
                card_w, card_h = 220, 260
                gap = 40
                total_w = card_w * len(card_options) + gap * (len(card_options) - 1)
                start_x = SCREEN_W // 2 - total_w // 2
                y = 150

                if is_my_pick:
                    just_left = keys_pressed[LOCAL_KEYS["left"]] and not prev_keys[LOCAL_KEYS["left"]]
                    just_right = keys_pressed[LOCAL_KEYS["right"]] and not prev_keys[LOCAL_KEYS["right"]]
                    just_confirm = keys_pressed[LOCAL_KEYS["light"]] and not prev_keys[LOCAL_KEYS["light"]]
                    if just_left:
                        net.send_card_nav("left")
                    elif just_right:
                        net.send_card_nav("right")
                    elif just_confirm:
                        net.send_card_nav("confirm")

                for i, card in enumerate(card_options):
                    cx = start_x + i * (card_w + gap)
                    rect = pygame.Rect(cx, y, card_w, card_h)
                    pygame.draw.rect(screen, CARD_BG, rect, border_radius=12)
                    tier_color = {"unique": CARD_UNIQUE_COLOR, "rare": CARD_RARE_COLOR}.get(card["category"])
                    if i == card_selected_idx:
                        border_color = CARD_BORDER_SEL
                    elif tier_color:
                        border_color = tier_color
                    else:
                        border_color = GREY
                    border_w = 4 if i == card_selected_idx else 2
                    pygame.draw.rect(screen, border_color, rect, border_w, border_radius=12)

                    if tier_color:
                        draw_text_center(screen, card["category"].upper(), font_small, tier_color,
                                          cx + card_w // 2, y + 18)
                    draw_text_center(screen, card["name"], font_small, WHITE, cx + card_w // 2, y + 45)

                    line = ""
                    ty = y + 100
                    for word in card["desc"].split(" "):
                        test = (line + " " + word).strip()
                        if font_small.size(test)[0] > card_w - 20:
                            draw_text_center(screen, line, font_small, GREY, cx + card_w // 2, ty)
                            ty += 26
                            line = word
                        else:
                            line = test
                    if line:
                        draw_text_center(screen, line, font_small, GREY, cx + card_w // 2, ty)

                if is_my_pick:
                    draw_text_center(screen, "Use MOVE keys to choose, light attack to confirm",
                                      font_small, GREY, SCREEN_W // 2, SCREEN_H - 30)
                else:
                    draw_text_center(screen, "Waiting for opponent to choose...",
                                      font_small, GREY, SCREEN_W // 2, SCREEN_H - 30)

            elif server_state == gc.Match.STATE_MATCH_END:
                winner = "PLAYER 1" if state["p1_round_wins"] > state["p2_round_wins"] else "PLAYER 2"
                draw_text_center(screen, f"{winner} WINS THE MATCH!", font_big, YELLOW,
                                  SCREEN_W // 2, SCREEN_H // 2 - 40)
                draw_text_center(screen, "Press ENTER for a rematch, [M] for main menu", font_small, WHITE,
                                  SCREEN_W // 2, SCREEN_H // 2 + 20)
                if keys_pressed[pygame.K_RETURN]:
                    net.send_reset()

    pygame.display.flip()
    prev_keys = keys_pressed

if net:
    net.close()
pygame.quit()
sys.exit()

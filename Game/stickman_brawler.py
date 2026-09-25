"""
Stickman Brawler - Prototipe Lokal
------------------------------------
Game stickman fighting 1v1 lokal (satu keyboard untuk berdua) dengan sistem
ronde. Setelah tiap ronde, PEMAIN YANG KALAH di ronde itu memilih satu dari
tiga kartu upgrade acak, yang terbawa ke ronde berikutnya.

Kontrol:
    Player 1 (Biru, sisi kiri):
        A / D      - gerak kiri / kanan
        W          - lompat
        F          - serangan ringan
        G          - serangan berat
        S          - blok (tahan)

    Player 2 (Merah, sisi kanan):
        LEFT/RIGHT - gerak kiri / kanan
        UP         - lompat
        J          - serangan ringan
        K          - serangan berat
        DOWN       - blok (tahan)

Saat pemilihan kartu, pemain yang KALAH memakai tombol geraknya sendiri
(A/D atau LEFT/RIGHT) untuk menyorot kartu dan tombol serangan ringannya
(F atau J) untuk mengonfirmasi pilihan.

Tombol global (berfungsi kapan saja saat pertandingan):
    R   - reset seluruh pertandingan (menghapus kemenangan ronde dan kartu upgrade)
    M   - kembali ke menu utama
    ESC - keluar (atau kembali, dari layar settings)

Game dibuka di menu utama dengan tombol Play / Settings / Quit (dikontrol
mouse). Settings memungkinkan kamu mengatur volume musik dan SFX.

Jalankan dengan:  python stickman_brawler.py
Membutuhkan:      pip install pygame
"""

import pygame
import random
import sys
import os
import math

# Aturan simulasi bersama yang aman untuk jaringan (stat, fisika, pool kartu,
# perhitungan combat) berada di game_common.py supaya prototipe lokal dan
# client/server online tidak bisa menyimpang satu sama lain. Modul ini hanya
# menambahkan hal khusus pygame di atasnya: rendering, sprite, suara, menu, dan
# input satu keyboard.
from game_common import (
    SCREEN_W, SCREEN_H, GROUND_Y, FPS,
    ROUNDS_TO_WIN_MATCH, ROUND_TIME_LIMIT,
    WEAPON_MELEE, UNARMED_REACH,
    format_stat_lines,
    random_cards as _shared_random_cards,
    process_melee_attacks, spawn_projectiles, update_projectiles,
    Fighter as BaseFighter,
)

pygame.init()
try:
    pygame.mixer.init()
except pygame.error:
    pass  # perangkat audio tidak tersedia (mis. lingkungan headless) - game tetap jalan, hanya tanpa suara

# Dibuat di sini, sesegera mungkin setelah pygame.init(), SEBELUM sprite apa
# pun dimuat di bawah (termasuk WEAPON_SPRITES). Surface.convert_alpha()/
# convert() butuh mode display sudah di-set supaya tahu format piksel target;
# kalau dipanggil sebelum ini ada, pygame.error dilempar, tertangkap oleh
# except di _load_weapon_sprite(), dan sprite itu diam-diam jadi None ->
# fallback gambar prosedural dipakai walau file .png-nya ada dan valid. Jangan
# pindahkan set_mode() ini ke bawah lagi tanpa memindahkan juga semua
# pemuatan sprite yang terjadi sebelum baris ini.
screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
pygame.display.set_caption("Stickman Brawler - Local Prototype")


def get_base_dir():
    """Folder tempat mencari sprite/musik/sfx. Saat dijalankan sebagai
    script .py biasa, itu adalah folder script itu sendiri. Saat dibekukan
    menjadi .exe oleh PyInstaller, __file__ malah menunjuk ke direktori
    ekstraksi sementara, jadi kita memakai lokasi .exe yang sebenarnya -
    inilah yang memungkinkan kamu terus menaruh file aset baru di samping
    .exe tanpa membangunnya ulang."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()

# ---------------------------------------------------------------------------
# Musik
# ---------------------------------------------------------------------------
# Taruh file musik ke folder "music" di samping script ini, mis.:
# music/fight_theme.mp3
# File .mp3/.ogg/.wav apa pun di folder itu otomatis dipakai sebagai lagu
# latar yang diputar berulang. Kalau foldernya kosong atau tidak ada, game
# tetap jalan tanpa musik.

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
        pygame.mixer.music.play(-1)  # ulang terus-menerus
    except pygame.error as e:
        print(f"Could not play background music: {e}")


# ---------------------------------------------------------------------------
# Efek suara
# ---------------------------------------------------------------------------
# Taruh SFX ke folder "sfx" di samping script ini, dikelompokkan per aksi:
# sfx/jump/Jump1.wav, sfx/jump/Jump2.wav, ...
# sfx/hit/Hit.wav,    sfx/hit/Hit4.wav,   ...
# Jumlah file per folder bebas - satu dipilih acak setiap kali suara itu
# diputar, jadi lompatan/pukulan berulang tidak terdengar identik. Folder yang
# hilang atau kosong dilewati begitu saja (tanpa suara, tanpa crash).

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
        random.choice(variants).play()


# ---------------------------------------------------------------------------
# Pemuatan animasi sprite
# ---------------------------------------------------------------------------
# Taruh frame PNG (menghadap KIRI, latar transparan) ke dalam:
# sprites/idle/0.png, sprites/idle/1.png, ...
# sprites/walk/0.png, sprites/walk/1.png, ...
# sprites/jump/0.png, sprites/jump/1.png
# sprites/punch/0.png, sprites/punch/1.png, sprites/punch/2.png
# sprites/kick/0.png,  sprites/kick/1.png,  sprites/kick/2.png
# sprites/block/0.png (opsional 1.png)
# sprites/hurt/0.png
# sprites/death/0.png, sprites/death/1.png
#
# Pose bawaan khusus senjata (lihat WEAPON_HEAVY_STATE / WEAPON_WALK_STATE /
# WEAPON_BLOCK_STATE / WEAPON_BLOCK_WALK_STATE di bawah) - masing-masing
# fallback ke pose generik di atas jika foldernya tidak ada/kosong:
# sprites/heavy_sword/0-2.png, sprites/heavy_spear/0-2.png, sprites/heavy_gun/0-1.png
# sprites/walk_sword/0-3.png, sprites/walk_spear/0-3.png, sprites/walk_gun/0-3.png
# sprites/block_sword/0.png, sprites/block_spear/0.png, sprites/block_gun/0.png
# sprites/block_walk_sword/0-3.png, sprites/block_walk_spear/0-3.png, sprites/block_walk_gun/0-3.png
# sprites/idle_sword/0.png, sprites/idle_spear/0.png, sprites/idle_gun/0.png
# sprites/light_sword/0-2.png, sprites/light_spear/0-2.png, sprites/light_gun/0.png
# sprites/death_sword/0-2.png, sprites/death_spear/0-2.png, sprites/death_gun/0-2.png
# sprites/jump_sword/0-1.png, sprites/jump_spear/0-1.png, sprites/jump_gun/0-1.png
#
# Folder mana pun yang tidak ada (atau kosong) akan fallback ke gambar garis
# prosedural sehingga game tetap jalan selagi art masih dikerjakan.

SPRITES_DIR = os.path.join(BASE_DIR, "sprites")

ANIM_FRAME_DURATION = {   # jumlah tick game untuk menampilkan tiap frame
    "idle": 20,
    "walk": 7,
    "jump": 10,
    "punch": 5,
    "kick": 6,
    "block": 12,
    "block_walk": 7,
    "hurt": 8,
    "death": 14,
    # Pose serangan berat senjata: art seluruh badan dengan senjata sudah
    # tergambar di dalamnya (bukan overlay prosedural terpisah - lihat
    # WEAPON_HEAVY_STATE di bawah). Durasinya habis dibagi
    # heavy_duration/recoil_duration tiap senjata (game_common.WEAPON_MELEE /
    # GUN_STATS) sehingga pose selesai tepat saat jendela serangan berakhir,
    # lalu ditahan seperti punch/kick.
    "heavy_sword": 8,   # 3 frame x 8 = 24 tick (heavy_duration pedang)
    "heavy_spear": 8,   # 3 frame x 8 = 24 tick (heavy_duration tombak)
    "heavy_gun": 5,     # 2 frame x 5 = 10 tick (recoil_duration pistol)
    # Pose jalan dengan senjata: art seluruh badan petarung berjalan sambil
    # membawa senjata (idenya sama dengan pose heavy di atas, tetapi berupa
    # loop 4 frame, bukan serangan sekali jalan). Durasinya sama dengan siklus
    # "walk" tanpa senjata karena menggantikan state itu frame demi frame,
    # tidak terikat pada timing serangan apa pun.
    "walk_sword": 7,
    "walk_spear": 7,
    "walk_gun": 7,
    # Pose blok dengan senjata: art seluruh badan petarung yang memblok dengan
    # senjata diangkat untuk bertahan (sudah tergambar di dalamnya, idenya sama
    # dengan pose heavy/walk di atas). block_* adalah satu frame yang ditahan
    # (seperti "block" tanpa senjata); block_walk_* adalah loop 4 frame
    # (seperti "block_walk" tanpa senjata) untuk memblok sambil bergerak.
    "block_sword": 12,
    "block_spear": 12,
    "block_gun": 12,
    "block_walk_sword": 7,
    "block_walk_spear": 7,
    "block_walk_gun": 7,
    # Pose idle dengan senjata: art seluruh badan petarung berdiri diam sambil
    # memegang senjata (sudah tergambar di dalamnya). Satu frame yang ditahan
    # per senjata, idenya sama dengan pose block_* di atas tetapi untuk state
    # idle biasa.
    "idle_sword": 20,
    "idle_spear": 20,
    "idle_gun": 20,
    # Pose serangan ringan (serangan dasar) dengan senjata: art seluruh badan
    # dengan senjata sudah tergambar di dalamnya. Melee = 3 frame dengan frame
    # 0 == frame 2, seperti punch tanpa senjata (windup, tusukan, recover).
    # Frame tusukan diatur muncul tepat saat jendela hit terbuka di 1/3 ayunan
    # (process_melee_attacks): tombak = 3 x 5 = 15 tick (light_duration-nya);
    # pedang = 4 tick/frame, jadi windup 4 + tusukan 4, lalu frame recovery
    # ditahan selama sisa 6 dari 14 tick-nya. Pistol: satu frame membidik yang
    # ditahan selama recoil_duration (10).
    "light_sword": 4,
    "light_spear": 5,
    "light_gun": 10,
    # Pose kematian dengan senjata: 3 frame (terkena hit sambil berdiri, jatuh,
    # tergeletak) dengan senjata sudah tergambar, 14 tick/frame seperti "death"
    # biasa (total 42 tick, lalu frame terakhir ditahan).
    "death_sword": 14,
    "death_spear": 14,
    "death_gun": 14,
    # Pose lompat dengan senjata: 2 frame (naik, turun) dengan senjata sudah
    # tergambar, idenya sama dengan pose senjata lainnya. Sebenarnya tidak
    # digerakkan timer (lihat WEAPON_JUMP_STATE / pemilihan frame berbasis
    # vel_y di bawah, sama seperti "jump" tanpa senjata) - dipertahankan di
    # sini dengan nilai 10 tick yang sama seperti "jump" murni demi
    # konsistensi/dokumentasi.
    "jump_sword": 10,
    "jump_spear": 10,
    "jump_gun": 10,
}

# Memetakan senjata yang dipakai ke state animasi serangan berat seluruh badan
# custom yang menggantikan pose "kick" generik untuk senjata itu (fallback ke
# "kick" jika art-nya tidak dimuat, mis. tanpa senjata). Frame-frame ini sudah
# menggambar senjata di dalam pose itu sendiri, jadi draw_weapon() dilewati
# selama state aktif - lihat pengecekan skip di Fighter.draw() di bawah.
WEAPON_HEAVY_STATE = {"sword": "heavy_sword", "spear": "heavy_spear", "gun": "heavy_gun"}

# Ide yang sama untuk serangan ringan (dasar): menggantikan pose "punch"
# generik + overlay draw_weapon() prosedural dengan pose bawaan khusus senjata
# (fallback ke "punch" + overlay jika art-nya tidak dimuat).
WEAPON_LIGHT_STATE = {"sword": "light_sword", "spear": "light_spear", "gun": "light_gun"}

# Ide yang sama dengan WEAPON_HEAVY_STATE tetapi untuk siklus jalan biasa:
# menggantikan pose "walk" generik dengan pose jalan-sambil-membawa-senjata
# khusus senjata (fallback ke "walk" jika art-nya tidak dimuat).
WEAPON_WALK_STATE = {"sword": "walk_sword", "spear": "walk_spear", "gun": "walk_gun"}

# Ide yang sama lagi, tetapi untuk memblok: pose blok-tahan khusus senjata
# (fallback ke "block" generik) dan pose blok-sambil-bergerak khusus senjata
# (fallback ke "block_walk" generik, lalu ke "block" jika itu pun tidak
# dimuat).
WEAPON_BLOCK_STATE = {"sword": "block_sword", "spear": "block_spear", "gun": "block_gun"}
WEAPON_BLOCK_WALK_STATE = {"sword": "block_walk_sword", "spear": "block_walk_spear", "gun": "block_walk_gun"}

# Ide yang sama lagi, tetapi untuk diam berdiri: pose idle khusus senjata
# (fallback ke "idle" generik jika art-nya tidak dimuat).
WEAPON_IDLE_STATE = {"sword": "idle_sword", "spear": "idle_spear", "gun": "idle_gun"}

# Ide yang sama sekali lagi, tetapi untuk mati: urutan kematian 3 frame khusus
# senjata (terkena hit sambil berdiri, jatuh, tergeletak) dengan senjata
# tergambar / terjatuh, menggantikan pose "death" generik (fallback ke "death"
# jika art-nya tidak dimuat). Sebelumnya, senjata petarung bersenjata
# di-overlay secara prosedural di atas pose kematian generik, sehingga melayang
# di tempat tangannya dulu berada.
WEAPON_DEATH_STATE = {"sword": "death_sword", "spear": "death_spear", "gun": "death_gun"}

# Ide yang sama sekali lagi, tetapi untuk melompat: pose 2 frame khusus senjata
# (frame 0 naik, frame 1 turun - lihat pemilihan berbasis vel_y di
# _update_animation_state) dengan senjata tergambar, menggantikan pose "jump"
# generik (fallback ke "jump" jika art-nya tidak dimuat).
WEAPON_JUMP_STATE = {"sword": "jump_sword", "spear": "jump_spear", "gun": "jump_gun"}

# State yang art bawaannya mencampur tinta badan dengan piksel senjata berwarna
# dalam satu gambar, sehingga rebuild_sprite_cache() harus me-retint tinta saja
# (lihat parameter ink_only di recolor_surface), bukan tint seluruh permukaan
# yang normal.
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
    """Jarak (px) dari tepi bawah gambar ke piksel buram terendahnya. Frame
    dipotong ke kotak bersama agar tetap sejajar horizontal, yang bisa
    menyisakan ruang kosong di bawah kaki dengan jumlah berbeda di tiap
    frame - offset ini mengoreksinya saat menggambar sehingga kaki selalu
    mendarat tepat di tanah apa pun padding per frame-nya.
    """
    w, h = surf.get_size()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            if surf.get_at((x, y))[3] > 10:
                return h - 1 - y
    return 0


def build_animation_library():
    """Memuat semua frame tiap aksi sekali saja, sudah di-flip untuk arah
    hadap sebaliknya.

    Tiap frame disimpan sebagai (surface, foot_offset) - foot_offset
    mengoreksi padding kosong di bawah kaki sehingga kaki setiap frame
    mendarat di garis tanah yang sama apa pun posenya.

    CATATAN: art sumber digambar menghadap KIRI (sesuai yang diberikan),
    jadi frame mentah yang dimuat disimpan di bawah "left" dan versi "right"
    dibuat dengan flip. Kalau nanti kamu menggambar frame baru yang
    menghadap kanan, cukup tukar key mana yang mendapat frame mentah vs.
    frame hasil flip di bawah.
    """
    lib = {}
    for action in ANIM_FRAME_DURATION:
        left_surfaces = _load_frames(action)
        left_frames = [(surf, _compute_foot_offset(surf)) for surf in left_surfaces]
        right_frames = [(pygame.transform.flip(surf, True, False), offset)
                         for surf, offset in left_frames]
        lib[action] = {"right": right_frames, "left": left_frames}
    return lib


# ---------------------------------------------------------------------------
# Rendering peluru (hanya kosmetik). Senjata itu sendiri (pedang/tombak/
# pistol di tangan petarung) tidak lagi digambar sebagai overlay prosedural
# terpisah - semua pose sekarang punya art badan sendiri yang sudah
# menggambar senjatanya langsung (lihat WEAPON_*_STATE / MIXED_COLOR_ACTIONS
# di atas). Yang tersisa di sini hanyalah proyektil pistol, dicari di
# sprites/weapons/bullet.png dengan fallback lingkaran sederhana jika
# filenya tidak ada. Dijaga tetap sinkron dengan salinan kode ini di
# client.py (diduplikasi terpisah, dengan alasan yang sama seperti
# recolor_surface/draw_procedural di atas) - ini murni rendering, bukan
# sumber kebenaran simulasi, jadi boleh ada di kedua file, tetapi
# tampilannya harus sama di keduanya.
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


def draw_projectiles_local(surf, projectiles, p1_color, p2_color):
    """projectiles: daftar objek game_common.Projectile yang aktif (ini
    prototipe lokal, jadi tidak seperti client online, ia punya objek
    simulasi yang sebenarnya, bukan dict hasil serialisasi)."""
    bullet_img = WEAPON_SPRITES.get("bullet")
    for proj in projectiles:
        color = p1_color if proj.owner_num == 1 else p2_color
        cx, cy = int(proj.x), int(proj.y)
        if bullet_img:
            pygame.draw.circle(surf, color, (cx, cy), 7, 2)
            rect = bullet_img.get_rect(center=(cx, cy))
            surf.blit(bullet_img, rect)
        else:
            pygame.draw.circle(surf, (255, 230, 120), (cx, cy), 5)
            pygame.draw.circle(surf, color, (cx, cy), 5, 2)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# SCREEN_W, SCREEN_H, GROUND_Y, FPS, GRAVITY, ROUNDS_TO_WIN_MATCH,
# ROUND_TIME_LIMIT, dan konstanta tuning freeze/burn/thorns sekarang berasal
# dari game_common (diimpor di atas) sehingga tidak bisa menyimpang dari nilai
# versi online.

WHITE = (245, 245, 245)
BLACK = (20, 20, 20)
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

# Warna stickman yang bisa dipilih (dipakai di Settings). Sprite berupa tinta
# hitam murni dengan alpha, jadi pewarnaan ulang cukup menukar RGB - lihat
# recolor_surface().
PLAYER_COLOR_OPTIONS = [
    BLUE, RED, GREEN, YELLOW,
    (255, 140, 0),    # oranye
    (210, 90, 230),   # magenta
    WHITE,
    (90, 220, 220),   # cyan
]
p1_color_idx = 0
p2_color_idx = 1


def recolor_surface(surf, color, ink_only=False):
    """Mewarnai ulang surface tinta-hitam/alpha ke `color`, alpha tidak
    berubah.

    Mengandalkan art sumber yang berwarna hitam murni (0,0,0): BLEND_RGB_MAX
    mengambil nilai maksimum tiap kanal RGB antara surface dan warna isian,
    dan karena hitam bernilai 0 di setiap kanal, hasilnya persis `color` di
    mana pun ada tinta - sementara BLEND_RGB (bukan RGBA) membiarkan kanal
    alpha, dan karena itu bentuk garisnya, sama sekali tidak tersentuh.

    ink_only=True beralih ke jalur per-piksel untuk frame
    MIXED_COLOR_ACTIONS (heavy_sword/heavy_spear/heavy_gun), yang art-nya
    menyatukan piksel senjata berwarna (bilah/gagang/tongkat) ke gambar yang
    sama dengan tinta badan. BLEND_RGB_MAX akan menyeret warna senjata itu
    ke arah tint petarung juga (mis. petarung merah membuat bilah abu-abu
    jadi pink), jadi di sini hanya piksel hampir-hitam yang diwarnai ulang;
    sisanya dibiarkan persis seperti yang digambar. Lebih lambat
    (per-piksel), tetapi hanya berjalan saat membangun cache, bukan
    per-frame.
    """
    tinted = surf.copy()
    if not ink_only:
        tinted.fill(color, special_flags=pygame.BLEND_RGB_MAX)
        return tinted
    w, h = tinted.get_size()
    # Lock/unlock eksplisit di sekitar loop: kalau tidak, get_at/set_at
    # diam-diam me-lock dan meng-unlock surface di setiap panggilan, yang tidak
    # masalah untuk beberapa piksel tetapi mengubah frame ~30 ribu piksel
    # menjadi operasi yang memakan waktu beberapa menit. Hanya berjalan saat
    # membangun cache (pembuatan petarung / ganti warna), tidak pernah
    # per-frame, tetapi tetap harus cepat.
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
# Latar arena pertarungan (dipilih dari Settings)
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


# "Default" = None -> fallback ke isian BG polos + bar tanah persis seperti
# sebelum fitur ini ada. Diisi oleh init_background_presets() begitu display
# siap (surface gradient/grid membutuhkannya di sebagian setup).
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
    perangkatnya sebagai latar arena. Murni lokal/kosmetik - client tiap
    pemain menggambar pilihannya sendiri, sama seperti warna stickman."""
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
# jaringan dan tidak memengaruhi deteksi hit (Fighter.rect tidak disentuh).
# ---------------------------------------------------------------------------
FACE_OVERLAY_SIZE = 60  # px, diameter overlay lingkaran
# Jarak (px) lurus ke atas dari titik tumpu kaki petarung (self.y) ke pusat
# kepalanya, diukur dari sprites/idle/0.png (lingkaran kepala membentang
# sekitar baris 33-85 dari kanvas setinggi 240px, yang kakinya berada 9px di
# atas dasar kanvas -> pusat-kepala-ke-kaki = (230 - 59) = 171). Pose digambar
# dengan torso tetap terpusat/terkunci antar frame (lihat "Weapon heavy-attack
# poses" di catatan proyek), jadi offset tetap cukup akurat mengikuti kepala di
# semua state animasi untuk overlay kosmetik.
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
    wajah stickman-nya sendiri. Murni lokal/kosmetik - client tiap pemain
    menggambar pilihannya sendiri, sama seperti latar arena custom."""
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


def cycle_player_color(player_num, delta):
    """Mengganti warna stickman pemain tersebut ke warna berikutnya,
    melewati warna yang sedang dipakai pemain lain agar keduanya tidak
    pernah sama."""
    global p1_color_idx, p2_color_idx
    n = len(PLAYER_COLOR_OPTIONS)
    if player_num == 1:
        idx = p1_color_idx
        for _ in range(n):
            idx = (idx + delta) % n
            if idx != p2_color_idx:
                break
        p1_color_idx = idx
        p1.color = PLAYER_COLOR_OPTIONS[p1_color_idx]
        p1.rebuild_sprite_cache()
    else:
        idx = p2_color_idx
        for _ in range(n):
            idx = (idx + delta) % n
            if idx != p1_color_idx:
                break
        p2_color_idx = idx
        p2.color = PLAYER_COLOR_OPTIONS[p2_color_idx]
        p2.rebuild_sprite_cache()


# ---------------------------------------------------------------------------
# Kartu upgrade
# ---------------------------------------------------------------------------
# Pool kartu (PERMANENT_CARDS/RARE_CARDS/UNIQUE_CARDS), CARD_TIER_WEIGHT, dan
# logika pengundian berbobot sekarang berada di game_common.py dan diimpor di
# atas, sehingga versi lokal dan online selalu menawarkan kartu yang sama
# dengan peluang yang sama.

used_unique_cards = set()   # nama kartu unik yang sudah diklaim di pertandingan ini
projectiles = []            # objek game_common.Projectile yang sedang melayang (tembakan pistol)


def random_cards(n=3, exclude_weapons=False, guarantee_weapon=False):
    """Wrapper lokal tipis: pool/bobot/sampling yang sebenarnya dipakai
    bersama (game_common.random_cards); hanya used_unique_cards yang menjadi
    state lokal di sini."""
    return _shared_random_cards(used_unique_cards, n, exclude_weapons=exclude_weapons,
                                 guarantee_weapon=guarantee_weapon)


# ---------------------------------------------------------------------------
# Fighter
# ---------------------------------------------------------------------------
class Fighter(BaseFighter):
    """Fighter lokal/pygame: mewarisi semua stat, efek kartu upgrade, dan
    matematika combat/fisika dari game_common.Fighter (satu-satunya sumber
    kebenaran yang dipakai bersama client/server online), dan hanya
    menambahkan hal yang khusus untuk prototipe hotseat lokal - sprite,
    state animasi, efek suara, dan input satu keyboard."""

    def __init__(self, x, color, facing, keys, player_num=None):
        self.color = color
        self.keys = keys  # dict konstanta tombol pygame
        self.player_num = player_num  # 1 atau 2 - menentukan overlay wajah petarung ini
        self.crit_flash = 0   # timer teks "CRIT!" (hanya lokal, tidak ada padanannya di server)
        self.overcrit_flash = 0  # timer teks "OVERCRIT!" (crit_chance melewati 100% karena Chaos)
        self.dodge_flash = 0  # timer teks "DODGE!"
        self.negate_flash = 0  # timer teks "NEGATED!" (Guardian's Blessing menyerap satu hit)

        self.sprite_cache = {}  # salinan ANIMATIONS milik petarung ini sendiri yang sudah di-tint
        # anim_state/anim_frame_idx/anim_timer diatur oleh reset_for_round() di
        # bawah, yang dipanggil oleh __init__() basis untuk kita.

        super().__init__(x, facing)  # mengatur base_x, facing, semua stat/kartu, dan memanggil reset_for_round()
        self.rebuild_sprite_cache()

    def rebuild_sprite_cache(self):
        """Mewarnai ulang setiap frame animasi yang dimuat ke warna petarung
        ini saat ini. Dipanggil sekali saat pembuatan dan lagi setiap warna
        berubah (mis. dari layar Settings)."""
        cache = {}
        for action, sides in ANIMATIONS.items():
            ink_only = action in MIXED_COLOR_ACTIONS
            cache[action] = {}
            for side, frames in sides.items():
                cache[action][side] = [(recolor_surface(surf, self.color, ink_only=ink_only), offset)
                                        for surf, offset in frames]
        self.sprite_cache = cache

    def reset_for_round(self):
        super().reset_for_round()
        self.crit_flash = 0
        self.overcrit_flash = 0
        self.dodge_flash = 0
        self.negate_flash = 0
        # state animasi (dipakai untuk memilih frame sprite, jika ada yang
        # dimuat)
        self.anim_state = "idle"
        self.anim_frame_idx = 0
        self.anim_timer = 0

    # apply_card / _apply_single_effect / _apply_chaos / CHAOS_STAT_RANGES
    # semuanya diwarisi tanpa perubahan dari game_common.Fighter.

    # -- geometri -----------------------------------------------------
    # Di-override hanya untuk mengembalikan pygame.Rect (dibutuhkan panggilan
    # collision dan drawing pygame) menggantikan Rect ringan milik game_common.
    # Matematikanya sendiri harus tetap identik dengan game_common.Fighter.rect
    # (dicerminkan manual di bawah; attack_rect hanya mendelegasikan).
    @property
    def rect(self):
        # Dijaga sinkron dengan game_common.Fighter.rect (lihat komentarnya
        # untuk alasan mengapa nilainya 190, bukan 110 yang lama - sekarang
        # mencakup seluruh tinggi sprite, tidak berhenti di tengah badan).
        return pygame.Rect(int(self.x - 18), int(self.y - 190), 36, 190)

    @property
    def attack_rect(self):
        # Mendelegasikan ke game_common.Fighter.attack_rect sehingga jangkauan
        # senjata (WEAPON_MELEE) dan range_mult dihitung di satu tempat saja -
        # dulu ini adalah "55 * range_mult" hasil salin manual, yang diam-diam
        # mengabaikan senjata yang dipakai di versi hotseat lokal.
        r = super().attack_rect
        return pygame.Rect(r.x, r.y, r.w, r.h)

    # -- update ---------------------------------------------------------
    def handle_input(self, keys_pressed, opponent):
        """Mengadaptasi state tombol pygame per frame menjadi dict intent
        polos yang diharapkan game_common.Fighter.handle_input() - bentuk
        yang sama dengan yang dikirim client jaringan dari penekanan tombol
        pemain jarak jauh."""
        intent = {
            "left": keys_pressed[self.keys["left"]],
            "right": keys_pressed[self.keys["right"]],
            "block": keys_pressed[self.keys["block"]],
            "jump": keys_pressed[self.keys["jump"]],
            "light": keys_pressed[self.keys["light"]],
            "heavy": keys_pressed[self.keys["heavy"]],
        }
        super().handle_input(intent)

    def handle_cpu_input(self, intent):
        """Sama seperti handle_input(), tetapi untuk petarung yang
        dikendalikan CPU: dict intent-nya langsung datang dari
        CPUBot.intent(), bukan dibangun dari state tombol pygame per frame.
        Key yang hilang default ke False, bentuk yang sama dengan yang
        dikirim client jaringan."""
        full_intent = {
            "left": False, "right": False, "block": False,
            "jump": False, "light": False, "heavy": False,
        }
        full_intent.update(intent)
        super().handle_input(full_intent)

    def _do_jump(self):
        super()._do_jump()
        play_sfx("jump")

    # _start_attack diwarisi tanpa perubahan dari game_common.Fighter.

    def physics_update(self):
        super().physics_update()  # gravitasi, timer serangan/cooldown, hit_flash, tick slow/burn
        if self.crit_flash > 0:
            self.crit_flash -= 1
        if self.overcrit_flash > 0:
            self.overcrit_flash -= 1
        if self.dodge_flash > 0:
            self.dodge_flash -= 1
        if self.negate_flash > 0:
            self.negate_flash -= 1
        self._update_animation_state()

    def _update_animation_state(self):
        if self.is_dead:
            death_state = WEAPON_DEATH_STATE.get(self.weapon)
            has_death_art = bool(ANIMATIONS.get(death_state, {}).get("right")) if death_state and ANIMATIONS else False
            new_state = death_state if has_death_art else "death"
        elif self.hit_flash > 0 and self.hit_flash > 5:
            new_state = "hurt"
        elif self.attack_timer > 0 and self.attack_type == "light":
            light_state = WEAPON_LIGHT_STATE.get(self.weapon)
            has_light_art = bool(ANIMATIONS.get(light_state, {}).get("right")) if light_state and ANIMATIONS else False
            new_state = light_state if has_light_art else "punch"
        elif self.attack_timer > 0 and self.attack_type == "heavy":
            heavy_state = WEAPON_HEAVY_STATE.get(self.weapon)
            has_heavy_art = bool(ANIMATIONS.get(heavy_state, {}).get("right")) if heavy_state and ANIMATIONS else False
            new_state = heavy_state if has_heavy_art else "kick"
        elif self.blocking:
            if self.is_moving:
                block_walk_state = WEAPON_BLOCK_WALK_STATE.get(self.weapon)
                has_weapon_block_walk = (bool(ANIMATIONS.get(block_walk_state, {}).get("right"))
                                          if block_walk_state and ANIMATIONS else False)
                if has_weapon_block_walk:
                    new_state = block_walk_state
                else:
                    has_block_walk = bool(ANIMATIONS.get("block_walk", {}).get("right")) if ANIMATIONS else False
                    new_state = "block_walk" if has_block_walk else "block"
            else:
                block_state = WEAPON_BLOCK_STATE.get(self.weapon)
                has_weapon_block = bool(ANIMATIONS.get(block_state, {}).get("right")) if block_state and ANIMATIONS else False
                new_state = block_state if has_weapon_block else "block"
        elif not self.on_ground:
            jump_state = WEAPON_JUMP_STATE.get(self.weapon)
            has_jump_art = bool(ANIMATIONS.get(jump_state, {}).get("right")) if jump_state and ANIMATIONS else False
            new_state = jump_state if has_jump_art else "jump"
        elif self.is_moving:
            walk_state = WEAPON_WALK_STATE.get(self.weapon)
            has_walk_art = bool(ANIMATIONS.get(walk_state, {}).get("right")) if walk_state and ANIMATIONS else False
            new_state = walk_state if has_walk_art else "walk"
        else:
            idle_state = WEAPON_IDLE_STATE.get(self.weapon)
            has_idle_art = bool(ANIMATIONS.get(idle_state, {}).get("right")) if idle_state and ANIMATIONS else False
            new_state = idle_state if has_idle_art else "idle"

        if new_state != self.anim_state:
            self.anim_state = new_state
            self.anim_frame_idx = 0
            self.anim_timer = 0
        else:
            self.anim_timer += 1

        # Lompat digerakkan langsung oleh kecepatan vertikal, bukan timer:
        # frame 0 (jumpup) saat naik, frame 1 (jumpdown) setelah jatuh. Aturan
        # yang sama untuk pose lompat khusus senjata (WEAPON_JUMP_STATE).
        if self.anim_state == "jump" or self.anim_state in WEAPON_JUMP_STATE.values():
            frames = ANIMATIONS.get(self.anim_state, {}).get("right", []) if ANIMATIONS else []
            if frames:
                self.anim_frame_idx = 0 if self.vel_y < 0 else min(1, len(frames) - 1)
            return

        duration = ANIM_FRAME_DURATION.get(self.anim_state, 10)
        if self.anim_timer >= duration:
            self.anim_timer = 0
            frames = ANIMATIONS.get(self.anim_state, {}).get("right", []) if ANIMATIONS else []
            if frames:
                # animasi death/hurt menahan frame terakhirnya, bukan berulang
                hold_states = (("death", "hurt", "punch", "kick") + tuple(WEAPON_HEAVY_STATE.values())
                               + tuple(WEAPON_LIGHT_STATE.values()) + tuple(WEAPON_DEATH_STATE.values()))
                if self.anim_state in hold_states and self.anim_frame_idx >= len(frames) - 1:
                    pass
                else:
                    self.anim_frame_idx = (self.anim_frame_idx + 1) % len(frames)

    # _apply_dot diwarisi tanpa perubahan dari game_common.Fighter (tick burn
    # dan pantulan thorns sama-sama melewati blok/perisai di sana).

    def take_hit(self, dmg):
        dealt = super().take_hit(dmg)
        play_sfx("hit")
        return dealt

    # -- menggambar ---------------------------------------------------------
    def draw(self, surf):
        frames_dict = self.sprite_cache.get(self.anim_state) if self.sprite_cache else None
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

        # kaki
        if self.on_ground:
            l1 = (cx - leg_spread, cy)
            l2 = (cx + leg_spread, cy)
        else:
            l1 = (cx - 10, cy - 10)
            l2 = (cx + 16, cy - 5)
        pygame.draw.line(surf, color, hip, l1, 5)
        pygame.draw.line(surf, color, hip, l2, 5)

        # badan
        pygame.draw.line(surf, color, (cx, body_top), hip, 5)

        # lengan (pose berubah sesuai state)
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

        # kepala
        pygame.draw.circle(surf, color, head_c, head_r)

        # petunjuk perisai saat memblok
        if self.blocking:
            shield_x = cx + self.facing * 22
            pygame.draw.circle(surf, GREEN, (shield_x, cy - 65), 10, 2)

        # hitbox debug (jadikan komentar jika tidak diinginkan)
        # pygame.draw.rect(surf, (255,0,0), self.rect, 1)


# ---------------------------------------------------------------------------
# Lawan CPU (mode single-player)
#
# Membaca bentuk intent yang sama dengan yang dihasilkan handle_input() pemain
# manusia dan yang dikirim client jaringan - tidak punya akses khusus, hanya
# stat game_common yang juga bisa dihitungnya dari dict state yang
# disinkronkan. Secara garis besar berdasarkan bot skrip di balance_sim.py,
# tetapi dibuat untuk benar-benar dilawan, bukan untuk pengukuran balance: ia
# bereaksi terhadap peluru yang datang (blok atau lompat menghindar) serta
# ayunan melee, sesekali menyelipkan lompatan pengecoh, dan tingkat
# agresi/reaksi/kesalahannya dapat diatur per tingkat kesulitan, bukan tetap.
# ---------------------------------------------------------------------------
CPU_DIFFICULTIES = ("Easy", "Normal", "Hard")

CPU_DIFFICULTY_PARAMS = {
    # block_chance:  peluang memblok ayunan melee yang terlihat akan datang (telegraphed)
    # bullet_react:  peluang merespons peluru yang datang sama sekali
    # mistake:       peluang ragu-ragu alih-alih menyerang saat dalam jangkauan
    # jump_chance:   peluang per frame untuk lompatan pengecoh saat di tanah & diam
    # aggression:    mengatur seberapa agresif ia mendekat/menjaga jarak
    "Easy":   dict(block_chance=0.25, bullet_react=0.15, mistake=0.35, jump_chance=0.004, aggression=0.85),
    "Normal": dict(block_chance=0.50, bullet_react=0.40, mistake=0.15, jump_chance=0.008, aggression=1.00),
    "Hard":   dict(block_chance=0.78, bullet_react=0.65, mistake=0.03, jump_chance=0.012, aggression=1.15),
}

CARD_TIER_RANK = {"unique": 2, "rare": 1, "permanent": 0}
# Peluang bot mengabaikan tier dan asal mengambil kartu acak, per tingkat
# kesulitan - menjaga Easy terasa bisa dikalahkan dan Hard terasa jauh lebih
# tajam dalam memilih upgrade.
CPU_CARD_RANDOM_CHANCE = {"Easy": 0.60, "Normal": 0.25, "Hard": 0.05}


class CPUBot:
    """Menggerakkan `handle_cpu_input()` satu Fighter setiap frame, dan
    memilih kartu upgrade-nya saat ia kalah di satu ronde."""

    def __init__(self, difficulty="Normal"):
        self.difficulty = difficulty if difficulty in CPU_DIFFICULTY_PARAMS else "Normal"
        self.params = CPU_DIFFICULTY_PARAMS[self.difficulty]
        self.block_ticks = 0

    @staticmethod
    def _reach(fighter):
        if fighter.weapon == "gun":
            return 450
        base = WEAPON_MELEE[fighter.weapon]["reach"] if fighter.weapon in WEAPON_MELEE else UNARMED_REACH
        return int(base * fighter.range_mult) + 18

    def _incoming_projectile(self, me, opp, projectiles):
        for proj in projectiles:
            if proj.owner_num != opp.player_num:
                continue
            heading_at_me = (proj.vx > 0) == (proj.x < me.x)
            if heading_at_me and abs(proj.x - me.x) < 260:
                return proj
        return None

    def intent(self, me, opp, projectiles):
        p = self.params
        dx = opp.x - me.x
        dist = abs(dx)
        it = {}

        my_reach, opp_reach = self._reach(me), self._reach(opp)
        melee_threat = opp.attack_timer > 0 and opp.weapon != "gun" and dist <= opp_reach + 20
        bullet = self._incoming_projectile(me, opp, projectiles)

        # sudah sedang memblok dari keputusan frame sebelumnya
        if self.block_ticks > 0:
            self.block_ticks -= 1
            return {"block": True}

        # bereaksi terhadap peluru yang datang: blok atau lompati
        if bullet is not None and random.random() < p["bullet_react"]:
            if random.random() < 0.5:
                return {"jump": True}
            self.block_ticks = 10
            return {"block": True}

        # bereaksi terhadap ayunan melee yang telegraphed
        if melee_threat and random.random() < p["block_chance"]:
            self.block_ticks = 14
            return {"block": True}

        # sesekali lompatan pengecoh saat tidak ada hal lain yang terjadi
        if me.on_ground and me.attack_timer <= 0 and random.random() < p["jump_chance"]:
            it["jump"] = True

        # menyerang saat dalam jangkauan
        want = my_reach * 0.9 if me.weapon != "gun" else 300
        if me.attack_cooldown <= 0 and me.attack_timer <= 0 and dist <= my_reach * 0.97:
            if random.random() < p["mistake"]:
                return it  # ragu-ragu - sebuah "kesalahan" yang bisa dihukum lawan manusia
            it["light" if random.random() < 0.6 else "heavy"] = True
            return it

        # posisi: tutup jarak, atau mundur/kiting dengan senjata berjangkauan
        if dist > want:
            it["right" if dx > 0 else "left"] = True
        elif me.weapon in ("gun", "spear") and dist < want * 0.55 and random.random() < 0.5 * p["aggression"]:
            it["left" if dx > 0 else "right"] = True
        return it

    def choose_card(self, card_options):
        """Mengembalikan indeks ke card_options. Umumnya memilih tier
        terbaik yang ditawarkan (unique > rare > permanent); kadang asal
        mengambil satu secara acak, dengan peluang yang naik sesuai tingkat
        kesulitan."""
        if random.random() < CPU_CARD_RANDOM_CHANCE[self.difficulty]:
            return random.randrange(len(card_options))
        return max(range(len(card_options)),
                   key=lambda i: CARD_TIER_RANK.get(card_options[i]["category"], 0))


# ---------------------------------------------------------------------------
# HUD
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
    """Tombol persegi panjang sederhana yang bisa diklik dengan sorotan saat
    hover."""

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
# sudah diambilnya. Label/nilai/nada stat berasal dari
# game_common.format_stat_lines() sehingga versi lokal dan online menampilkan
# angka yang identik.
#
# Diduplikasi (sama seperti rendering senjata) antara file ini dan client.py
# karena ini murni penggambaran pygame, bukan sumber kebenaran simulasi -
# bagian bersama yang benar-benar penting ada di game_common.
# ---------------------------------------------------------------------------
STATS_TONE_COLORS = {"good": (120, 220, 140), "bad": (235, 120, 120), "neutral": (215, 215, 215)}


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
    stat_lines = format_stat_lines(stats)
    card_counts = {}
    for name in cards:
        card_counts[name] = card_counts.get(name, 0) + 1
    card_lines = [f"{n} x{c}" if c > 1 else n for n, c in card_counts.items()]

    pad = 12
    line_h = 20
    header_h = 26
    width = 260
    # blok stat + pemisah + header "Upgrades" + satu baris per kartu
    body_lines = len(stat_lines) + 1 + max(1, len(card_lines))
    height = header_h + pad + body_lines * line_h + pad + 14

    x = anchor_x - width if align_right else anchor_x
    y = anchor_y
    # jaga panel tetap di layar walaupun kartunya banyak
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


def draw_match_hud(surf, mouse_pos):
    """Bar HP, label pemain, dan dua tombol toggle panel statistik (plus
    panel mana pun yang sedang terbuka). Dipakai bersama oleh state FIGHT
    dan ROUND_END agar panel tidak lenyap seketika saat ronde berakhir."""
    draw_hp_bar(surf, 30, 30, 380, 26, p1.hp, p1.max_hp, GREEN)
    draw_hp_bar(surf, SCREEN_W - 410, 30, 380, 26, p2.hp, p2.max_hp, GREEN, flip=True)
    p1_label = "P1" + (f" - {p1.weapon.capitalize()}" if p1.weapon else "")
    p2_label = ("CPU" if vs_cpu else "P2") + (f" - {p2.weapon.capitalize()}" if p2.weapon else "")
    draw_text_center(surf, p1_label, font_small, WHITE, 30 + 40, 62)
    draw_text_center(surf, p2_label, font_small, WHITE, SCREEN_W - 30 - 40, 62)

    draw_stats_toggle(surf, P1_STATS_BTN_RECT, mouse_pos, show_p1_stats)
    draw_stats_toggle(surf, P2_STATS_BTN_RECT, mouse_pos, show_p2_stats)

    if show_p1_stats:
        draw_stats_panel(surf, p1.stats_dict(), p1.cards, "Player 1", 30, 80)
    if show_p2_stats:
        draw_stats_panel(surf, p2.stats_dict(), p2.cards, "Player 2",
                          SCREEN_W - 30, 80, align_right=True)


# ---------------------------------------------------------------------------
# Setup game
# ---------------------------------------------------------------------------
P1_KEYS = {
    "left": pygame.K_a, "right": pygame.K_d, "jump": pygame.K_w,
    "light": pygame.K_f, "heavy": pygame.K_g, "block": pygame.K_s,
}
P2_KEYS = {
    "left": pygame.K_LEFT, "right": pygame.K_RIGHT, "jump": pygame.K_UP,
    "light": pygame.K_j, "heavy": pygame.K_k, "block": pygame.K_DOWN,
}

p1 = Fighter(SCREEN_W * 0.25, PLAYER_COLOR_OPTIONS[p1_color_idx], 1, P1_KEYS, player_num=1)
p2 = Fighter(SCREEN_W * 0.75, PLAYER_COLOR_OPTIONS[p2_color_idx], -1, P2_KEYS, player_num=2)

p1_round_wins = 0
p2_round_wins = 0

STATE_MAIN_MENU = "main_menu"
STATE_SETTINGS = "settings"
STATE_MODE_SELECT = "mode_select"
STATE_SELECT_ROUNDS = "select_rounds"
STATE_FIGHT = "fight"
STATE_ROUND_END = "round_end"
STATE_CARD_SELECT = "card_select"
STATE_MATCH_END = "match_end"

state = STATE_MAIN_MENU
round_end_timer = 0
round_time_left = ROUND_TIME_LIMIT * FPS
round_end_message = ""

rounds_to_win = ROUNDS_TO_WIN_MATCH  # dipilih di layar Match Length setiap kali Play
rounds_input_text = str(ROUNDS_TO_WIN_MATCH)
rounds_error = ""
guaranteed_weapons = False  # opsi pertandingan "Guaranteed Weapons", di-toggle di layar yang sama

# -- State single-player (vs CPU) --------------------------------------------
vs_cpu = False                # True selama sisa pertandingan ini jika 1 Player dipilih
cpu_difficulty = "Normal"     # dipilih di layar Mode Select
cpu_bot = None                # instance CPUBot yang menggerakkan P2, diatur di reset_match()
cpu_card_delay = 0            # jeda "berpikir" singkat sebelum bot mengunci pilihan kartu

# -- Tombol menu / settings -------------------------------------------------
menu_play_btn = Button(SCREEN_W // 2, 260, 260, 60, "Play")
menu_settings_btn = Button(SCREEN_W // 2, 340, 260, 60, "Settings")
menu_quit_btn = Button(SCREEN_W // 2, 420, 260, 60, "Quit")

mode_1p_btn = Button(SCREEN_W // 2, 190, 320, 60, "1 Player (vs CPU)")
mode_2p_btn = Button(SCREEN_W // 2, 260, 320, 60, "2 Player (Local)")
mode_diff_btns = {
    "Easy": Button(SCREEN_W // 2 - 180, 355, 150, 55, "Easy"),
    "Normal": Button(SCREEN_W // 2, 355, 150, 55, "Normal"),
    "Hard": Button(SCREEN_W // 2 + 180, 355, 150, 55, "Hard"),
}
mode_confirm_btn = Button(SCREEN_W // 2, 415, 260, 55, "Continue")
mode_back_btn = Button(SCREEN_W // 2, 480, 200, 55, "Back")
mode_1p_selected = False  # cabang layar ini yang sedang tampil (1P juga perlu memilih kesulitan)

rounds_box_rect = pygame.Rect(0, 0, 160, 55)
rounds_box_rect.center = (SCREEN_W // 2, 250)
rounds_guaranteed_weapons_btn = Button(SCREEN_W // 2, 315, 320, 50, "Guaranteed Weapons: OFF")
rounds_confirm_btn = Button(SCREEN_W // 2, 385, 260, 55, "Start Match")
rounds_back_btn = Button(SCREEN_W // 2, 450, 200, 55, "Back")

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

card_options = []
card_selected_idx = 0
card_picker = None  # objek fighter yang sedang memilih
card_confirm_cooldown = 0

# Toggle panel statistik (satu per pemain) - tombol "i" kecil yang berada di
# samping tiap bar HP selama pertandingan.
P1_STATS_BTN_RECT = pygame.Rect(30 + 380 + 8, 30, 26, 26)
P2_STATS_BTN_RECT = pygame.Rect(SCREEN_W - 410 - 34, 30, 26, 26)
show_p1_stats = False
show_p2_stats = False


def start_new_round():
    global round_time_left, state, projectiles
    p1.reset_for_round()
    p2.reset_for_round()
    round_time_left = ROUND_TIME_LIMIT * FPS
    projectiles = []  # jangan bawa peluru nyasar melewati transisi KO/ronde
    state = STATE_FIGHT


def reset_match():
    """Menghapus kemenangan ronde dan kartu upgrade, lalu memulai
    pertandingan yang benar-benar baru."""
    global p1, p2, p1_round_wins, p2_round_wins, pending_loser, cpu_bot
    p1_round_wins = 0
    p2_round_wins = 0
    pending_loser = None
    used_unique_cards.clear()
    p1 = Fighter(SCREEN_W * 0.25, PLAYER_COLOR_OPTIONS[p1_color_idx], 1, P1_KEYS, player_num=1)
    p2 = Fighter(SCREEN_W * 0.75, PLAYER_COLOR_OPTIONS[p2_color_idx], -1, P2_KEYS, player_num=2)
    cpu_bot = CPUBot(cpu_difficulty) if vs_cpu else None
    start_new_round()


def confirm_round_selection():
    """Memvalidasi jumlah ronde yang diketik, lalu memulai pertandingan
    dengan nilai itu."""
    global rounds_to_win, rounds_error
    try:
        n = int(rounds_input_text)
    except ValueError:
        rounds_error = "Enter a whole number"
        return
    if n < 1:
        rounds_error = "Must be at least 1"
        return
    rounds_to_win = min(n, 50)
    pygame.key.stop_text_input()
    reset_match()


def end_round(winner_name):
    global state, round_end_timer, round_end_message
    round_end_message = winner_name
    round_end_timer = FPS * 2
    state = STATE_ROUND_END


def begin_card_select(loser):
    global state, card_options, card_selected_idx, card_picker, card_confirm_cooldown, cpu_card_delay
    card_options = random_cards(3, exclude_weapons=loser.weapon is not None,
                                 guarantee_weapon=guaranteed_weapons)
    card_picker = loser
    card_confirm_cooldown = FPS // 2  # jeda singkat agar tidak terpicu ulang seketika
    if vs_cpu and loser is p2:
        card_selected_idx = cpu_bot.choose_card(card_options)  # diputuskan sekarang, diterapkan setelah jeda "berpikir" singkat
        cpu_card_delay = FPS  # ~1 detik, agar pilihan itu terasa seperti sebuah ketukan, bukan instan
    else:
        card_selected_idx = 1
    state = STATE_CARD_SELECT


def check_round_outcome():
    """Mengembalikan True jika ronde berakhir pada frame ini."""
    if p1.hp <= 0 or p2.hp <= 0:
        if p1.hp <= 0 and p2.hp <= 0:
            msg = "DRAW!"
            loser = None
        elif p1.hp <= 0:
            msg = ("CPU" if vs_cpu else "PLAYER 2") + " WINS THE ROUND"
            p2_round_wins_inc()
            loser = p1
        else:
            msg = "PLAYER 1 WINS THE ROUND"
            p1_round_wins_inc()
            loser = p2
        end_round(msg)
        return True, loser
    return False, None


def p1_round_wins_inc():
    global p1_round_wins
    p1_round_wins += 1


def p2_round_wins_inc():
    global p2_round_wins
    p2_round_wins += 1


def resolve_timeout():
    global round_end_message
    if p1.hp > p2.hp:
        msg = "PLAYER 1 WINS THE ROUND (TIME)"
        p1_round_wins_inc()
        loser = p2
    elif p2.hp > p1.hp:
        msg = ("CPU" if vs_cpu else "PLAYER 2") + " WINS THE ROUND (TIME)"
        p2_round_wins_inc()
        loser = p1
    else:
        msg = "DRAW! (TIME)"
        loser = None
    end_round(msg)
    return loser


# ---------------------------------------------------------------------------
# Loop utama
# ---------------------------------------------------------------------------
pending_loser = None
start_background_music()

running = True
while running:
    dt = clock.tick(FPS)
    keys_pressed = pygame.key.get_pressed()
    mouse_pos = pygame.mouse.get_pos()
    mouse_click = False

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mouse_click = True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if state in (STATE_SETTINGS, STATE_SELECT_ROUNDS, STATE_MODE_SELECT):
                pygame.key.stop_text_input()
                state = STATE_MAIN_MENU
            elif state == STATE_MAIN_MENU:
                running = False
            else:
                running = False
        if event.type == pygame.KEYDOWN and event.key == pygame.K_r:
            if state in (STATE_FIGHT, STATE_ROUND_END, STATE_CARD_SELECT, STATE_MATCH_END):
                reset_match()
        if event.type == pygame.KEYDOWN and event.key == pygame.K_m:
            if state in (STATE_FIGHT, STATE_ROUND_END, STATE_CARD_SELECT, STATE_MATCH_END):
                state = STATE_MAIN_MENU
        if state == STATE_SELECT_ROUNDS:
            if event.type == pygame.TEXTINPUT:
                if event.text.isdigit() and len(rounds_input_text) < 3:
                    rounds_input_text += event.text
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_BACKSPACE:
                    rounds_input_text = rounds_input_text[:-1]
                elif event.key == pygame.K_RETURN:
                    confirm_round_selection()

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
    if state == STATE_MAIN_MENU:
        p1.draw(screen)
        p2.draw(screen)
        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        screen.blit(overlay, (0, 0))

        draw_text_center(screen, "STICKMAN BRAWLER", font_big, YELLOW, SCREEN_W // 2, 150)

        for btn in (menu_play_btn, menu_settings_btn, menu_quit_btn):
            btn.draw(screen, mouse_pos)

        if menu_play_btn.clicked(mouse_pos, mouse_click):
            mode_1p_selected = False
            state = STATE_MODE_SELECT
        elif menu_settings_btn.clicked(mouse_pos, mouse_click):
            state = STATE_SETTINGS
        elif menu_quit_btn.clicked(mouse_pos, mouse_click):
            running = False

    # ------------------------------------------------------------------
    elif state == STATE_MODE_SELECT:
        p1.draw(screen)
        p2.draw(screen)
        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        screen.blit(overlay, (0, 0))

        draw_text_center(screen, "GAME MODE", font_big, YELLOW, SCREEN_W // 2, 100)

        mode_1p_btn.draw(screen, mouse_pos)
        mode_2p_btn.draw(screen, mouse_pos)

        if mode_1p_btn.clicked(mouse_pos, mouse_click):
            mode_1p_selected = True
        elif mode_2p_btn.clicked(mouse_pos, mouse_click):
            vs_cpu = False
            rounds_input_text = str(rounds_to_win)
            rounds_error = ""
            pygame.key.start_text_input()
            state = STATE_SELECT_ROUNDS

        if mode_1p_selected:
            draw_text_center(screen, "Choose a difficulty", font_small, GREY, SCREEN_W // 2, 320)
            for name, btn in mode_diff_btns.items():
                if name == cpu_difficulty:
                    pygame.draw.rect(screen, YELLOW, btn.rect.inflate(8, 8), 3, border_radius=12)
                btn.draw(screen, mouse_pos)
                if btn.clicked(mouse_pos, mouse_click):
                    cpu_difficulty = name
            mode_confirm_btn.draw(screen, mouse_pos)
            if mode_confirm_btn.clicked(mouse_pos, mouse_click):
                vs_cpu = True
                rounds_input_text = str(rounds_to_win)
                rounds_error = ""
                pygame.key.start_text_input()
                state = STATE_SELECT_ROUNDS

        mode_back_btn.draw(screen, mouse_pos)
        if mode_back_btn.clicked(mouse_pos, mouse_click):
            state = STATE_MAIN_MENU

    # ------------------------------------------------------------------
    elif state == STATE_SELECT_ROUNDS:
        p1.draw(screen)
        p2.draw(screen)
        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        screen.blit(overlay, (0, 0))

        draw_text_center(screen, "MATCH LENGTH", font_big, YELLOW, SCREEN_W // 2, 150)
        draw_text_center(screen, "How many round wins takes the match?",
                          font_small, GREY, SCREEN_W // 2, 195)

        box_color = (70, 74, 90)
        pygame.draw.rect(screen, box_color, rounds_box_rect, border_radius=8)
        pygame.draw.rect(screen, YELLOW, rounds_box_rect, 2, border_radius=8)
        draw_text_center(screen, rounds_input_text, font_med, WHITE,
                          rounds_box_rect.centerx, rounds_box_rect.centery)

        if rounds_error:
            draw_text_center(screen, rounds_error, font_small, RED, SCREEN_W // 2, 300)

        rounds_guaranteed_weapons_btn.label = (
            f"Guaranteed Weapons: {'ON' if guaranteed_weapons else 'OFF'}")
        rounds_guaranteed_weapons_btn.draw(screen, mouse_pos)
        rounds_confirm_btn.draw(screen, mouse_pos)
        rounds_back_btn.draw(screen, mouse_pos)

        if rounds_guaranteed_weapons_btn.clicked(mouse_pos, mouse_click):
            guaranteed_weapons = not guaranteed_weapons
        elif rounds_confirm_btn.clicked(mouse_pos, mouse_click):
            confirm_round_selection()
        elif rounds_back_btn.clicked(mouse_pos, mouse_click):
            pygame.key.stop_text_input()
            state = STATE_MODE_SELECT

    # ------------------------------------------------------------------
    elif state == STATE_SETTINGS:
        p1.draw(screen)
        p2.draw(screen)
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
            cycle_player_color(1, -1)
        elif settings_p1_color_next_btn.clicked(mouse_pos, mouse_click):
            cycle_player_color(1, 1)
        elif settings_p2_color_prev_btn.clicked(mouse_pos, mouse_click):
            cycle_player_color(2, -1)
        elif settings_p2_color_next_btn.clicked(mouse_pos, mouse_click):
            cycle_player_color(2, 1)
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
            state = STATE_MAIN_MENU

    # ------------------------------------------------------------------
    elif state == STATE_FIGHT:
        if mouse_click:
            if P1_STATS_BTN_RECT.collidepoint(mouse_pos):
                show_p1_stats = not show_p1_stats
            elif P2_STATS_BTN_RECT.collidepoint(mouse_pos):
                show_p2_stats = not show_p2_stats

        p1.events = []
        p2.events = []

        p1.handle_input(keys_pressed, p2)
        if vs_cpu:
            p2.handle_cpu_input(cpu_bot.intent(p2, p1, projectiles))
        else:
            p2.handle_input(keys_pressed, p1)
        p1.physics_update()
        p2.physics_update()

        # saling berhadapan
        p1.facing = 1 if p1.x <= p2.x else -1
        p2.facing = -1 if p2.x >= p1.x else 1
        # (tetap izinkan arah gerak eksplisit menimpa secara ringan - sengaja
        # disederhanakan)

        # resolusi serangan (ayunan melee + tembakan pistol) - dipakai bersama
        # dengan server online lewat game_common.py, sehingga tidak bisa keluar
        # dari sinkron dengan apa yang dilakukan versi jaringan.
        process_melee_attacks(p1, p2)
        spawn_projectiles(p1, p2, projectiles)
        projectiles[:] = update_projectiles(projectiles, p1, p2)

        # resolve_hit() (di dalam fungsi-fungsi di atas) mencatat apa yang
        # terjadi lewat daftar .events tiap petarung - mekanisme yang sama
        # dengan yang dipakai client jaringan untuk membaca event kiriman
        # server. Secara lokal kita cukup langsung bereaksi terhadap daftar
        # kita sendiri, bukan lewat jaringan.
        for fighter in (p1, p2):
            for ev in fighter.events:
                if ev == "crit":
                    fighter.crit_flash = 24
                    play_sfx("crit")
                elif ev == "overcrit":
                    fighter.overcrit_flash = 24  # event "crit" (diputar di atas) sudah menangani sfx-nya
                elif ev == "dodge":
                    fighter.dodge_flash = 24
                    play_sfx("dodge")
                elif ev == "negated":
                    fighter.negate_flash = 24
                    play_sfx("dodge")  # belum ada sfx khusus - pakai ulang blip "whiff"
                elif ev == "shoot":
                    play_sfx("shoot")
                elif ev == "sword":
                    play_sfx("sword")
                elif ev == "spear":
                    play_sfx("spear")

        round_time_left -= 1
        ended, loser = check_round_outcome()
        if not ended and round_time_left <= 0:
            loser = resolve_timeout()
        if ended or round_time_left <= 0:
            pending_loser = loser

        # gambar petarung
        p1.draw(screen)
        p2.draw(screen)
        draw_projectiles_local(screen, projectiles, p1.color, p2.color)

        # HUD
        draw_match_hud(screen, mouse_pos)

        secs_left = max(0, round_time_left // FPS)
        draw_text_center(screen, str(secs_left), font_med, WHITE, SCREEN_W // 2, 45)
        rounds_line = f"Rounds: {p1_round_wins} - {p2_round_wins} (first to {rounds_to_win})"
        if guaranteed_weapons:
            rounds_line += "  \u2022  Guaranteed Weapons"
        draw_text_center(screen, rounds_line, font_small, GREY, SCREEN_W // 2, 80)
        hint = "[R] reset match   [M] main menu"
        if vs_cpu:
            hint = f"vs CPU ({cpu_difficulty})   " + hint
        draw_text_center(screen, hint, font_small, GREY, SCREEN_W // 2, SCREEN_H - 20)

    # ------------------------------------------------------------------
    elif state == STATE_ROUND_END:
        if mouse_click:
            if P1_STATS_BTN_RECT.collidepoint(mouse_pos):
                show_p1_stats = not show_p1_stats
            elif P2_STATS_BTN_RECT.collidepoint(mouse_pos):
                show_p2_stats = not show_p2_stats
        p1.physics_update()
        p2.physics_update()
        p1.draw(screen)
        p2.draw(screen)
        draw_projectiles_local(screen, projectiles, p1.color, p2.color)
        draw_match_hud(screen, mouse_pos)
        draw_text_center(screen, round_end_message, font_big, YELLOW, SCREEN_W // 2, SCREEN_H // 2 - 30)
        round_end_timer -= 1
        if round_end_timer <= 0:
            if p1_round_wins >= rounds_to_win or p2_round_wins >= rounds_to_win:
                state = STATE_MATCH_END
            else:
                if pending_loser is not None:
                    begin_card_select(pending_loser)
                else:
                    start_new_round()

    # ------------------------------------------------------------------
    elif state == STATE_CARD_SELECT:
        p1.draw(screen)
        p2.draw(screen)
        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        screen.blit(overlay, (0, 0))

        cpu_picking = vs_cpu and card_picker is p2
        who = "PLAYER 1" if card_picker is p1 else ("CPU" if cpu_picking else "PLAYER 2")
        draw_text_center(screen, f"{who} LOST THE ROUND - PICK AN UPGRADE", font_med, WHITE, SCREEN_W // 2, 90)

        card_w, card_h = 220, 260
        gap = 40
        total_w = card_w * 3 + gap * 2
        start_x = SCREEN_W // 2 - total_w // 2
        y = 150

        if card_confirm_cooldown > 0:
            card_confirm_cooldown -= 1
        elif cpu_picking:
            # card_selected_idx sudah diputuskan (dan ditampilkan tersorot) di
            # begin_card_select() - ini hanya jeda "berpikir" sebelum pilihan
            # terkunci, agar tidak terasa instan.
            if cpu_card_delay > 0:
                cpu_card_delay -= 1
            else:
                chosen_card = card_options[card_selected_idx]
                card_picker.apply_card(chosen_card)
                if chosen_card["category"] == "unique":
                    used_unique_cards.add(chosen_card["name"])
                pending_loser = None
                start_new_round()
        else:
            picker_keys = card_picker.keys
            if keys_pressed[picker_keys["left"]]:
                card_selected_idx = max(0, card_selected_idx - 1)
                card_confirm_cooldown = 10
            elif keys_pressed[picker_keys["right"]]:
                card_selected_idx = min(2, card_selected_idx + 1)
                card_confirm_cooldown = 10
            elif keys_pressed[picker_keys["light"]]:
                chosen_card = card_options[card_selected_idx]
                card_picker.apply_card(chosen_card)
                if chosen_card["category"] == "unique":
                    used_unique_cards.add(chosen_card["name"])
                pending_loser = None
                start_new_round()

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
                draw_text_center(screen, card["category"].upper(), font_small, tier_color, cx + card_w // 2, y + 18)

            draw_text_center(screen, card["name"], font_small, WHITE, cx + card_w // 2, y + 45)
            desc_lines = card["desc"].split(" ")
            # wrap-around sederhana
            line = ""
            ty = y + 100
            for word in desc_lines:
                test = (line + " " + word).strip()
                if font_small.size(test)[0] > card_w - 20:
                    draw_text_center(screen, line, font_small, GREY, cx + card_w // 2, ty)
                    ty += 26
                    line = word
                else:
                    line = test
            if line:
                draw_text_center(screen, line, font_small, GREY, cx + card_w // 2, ty)

        select_hint = "Choosing..." if cpu_picking else "Use your MOVE keys to choose, ATTACK (light) to confirm"
        draw_text_center(screen, select_hint, font_small, GREY, SCREEN_W // 2, SCREEN_H - 30)

    # ------------------------------------------------------------------
    elif state == STATE_MATCH_END:
        p1.draw(screen)
        p2.draw(screen)
        winner = "PLAYER 1" if p1_round_wins > p2_round_wins else ("CPU" if vs_cpu else "PLAYER 2")
        draw_text_center(screen, f"{winner} WINS THE MATCH!", font_big, YELLOW, SCREEN_W // 2, SCREEN_H // 2 - 40)
        draw_text_center(screen, "Press ENTER to play again, [M] for main menu", font_small, WHITE,
                          SCREEN_W // 2, SCREEN_H // 2 + 20)
        if keys_pressed[pygame.K_RETURN]:
            reset_match()

    pygame.display.flip()

pygame.quit()
sys.exit()

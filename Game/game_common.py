"""
game_common.py - simulasi game bersama (network-safe) untuk Stickman Brawler.

Modul ini SAMA SEKALI TIDAK bergantung pada pygame, display, sprite, atau
suara - hanya memakai class Rect kecil buatan sendiri untuk perhitungan
hit-box. Itu sebabnya server khusus bisa mengimpor modul ini dan berjalan
sepenuhnya headless, tanpa perlu library GUI terpasang.

Baik server.py (simulasi otoritatif) maupun client.py (rendering/input/
audio) mengimpor modul ini, jadi aturan permainannya hanya hidup di satu
tempat saja.
"""

import random


class Rect:
    """Rect AABB minimal, cukup untuk deteksi tabrakan/hit. Menghindari
    ketergantungan penuh ke pygame/SDL di server yang headless."""
    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h

    def colliderect(self, other):
        return not (
            self.x + self.w <= other.x or other.x + other.w <= self.x
            or self.y + self.h <= other.y or other.y + other.h <= self.y
        )

# ---------------------------------------------------------------------------
# Konfigurasi (dipakai bersama server + client agar hitbox/timing/perhitungan
# layar selalu cocok)
# ---------------------------------------------------------------------------
SCREEN_W, SCREEN_H = 960, 540
GROUND_Y = SCREEN_H - 80
FPS = 60
GRAVITY = 0.9
ROUNDS_TO_WIN_MATCH = 3
ROUND_TIME_LIMIT = 60  # detik

# Tuning untuk Freezing Strikes / Burning Strikes / Thorny / Second Wind
FREEZE_SLOW_DURATION = 40   # jumlah tick lawan melambat setelah kena hit (~0,67 detik @60 FPS)
FREEZE_SLOW_FACTOR = 0.5    # pengali kecepatan gerak selagi melambat (slowed)
FREEZE_ATTACK_SPEED_FACTOR = 0.65  # selagi slowed, cooldown serangan hanya berkurang sebesar
                                   # fraksi ini dari laju normal (0.65 -> ~54% lebih lama nunggu)
BURN_DURATION = 90          # jumlah tick lawan terbakar (burning) setelah kena hit (~1,5 detik)
BURN_DPS = 0.12             # damage burn per tick (~10.8 total selama durasi penuh)
THORNS_REFLECT_NORMAL = 0.20    # fraksi damage yang diterima yang dipantulkan balik
THORNS_REFLECT_BLOCKED = 0.50   # fraksi damage (setelah dikurangi block) yang dipantulkan balik saat sedang block
SECOND_WIND_HP_FRACTION = 0.30  # fraksi max HP yang dipulihkan saat Second Wind membangkitkan

# Tuning dodge chance / overcrit (balance pass Sept 2026)
DODGE_CHANCE_CAP = 0.70    # kartu biasa (Evasion, Killer Instinct, Evasive Maneuvers,
                            # kartu senjata) tidak bisa mendorong dodge_chance melebihi ini.
                            # Chaos melewati semua batas stat sepenuhnya (lihat
                            # _apply_single_effect), jadi itu satu-satunya cara tembus 70%.
OVERCRIT_THRESHOLD = 1.0    # crit_chance di atas ini (hanya lewat Chaos) berubah jadi peluang overcrit
OVERCRIT_BONUS_DMG = 2.0    # pengali flat tambahan di atas crit_dmg_mult saat
                            # overcrit terjadi (misalnya crit dmg 1.5x -> jadi 3.5x saat overcrit)


# ---------------------------------------------------------------------------
# Senjata
# ---------------------------------------------------------------------------
# Baseline tangan kosong (dipakai setiap fighter sebelum/tanpa kartu senjata).
UNARMED_REACH = 55
UNARMED_LIGHT_DMG, UNARMED_HEAVY_DMG = 8, 16
UNARMED_LIGHT_DURATION, UNARMED_HEAVY_DURATION = 16, 26
UNARMED_LIGHT_COOLDOWN, UNARMED_HEAVY_COOLDOWN = 22, 40

# Senjata melee (sword, spear): bentuk ayunan/deteksi-hit sama seperti
# serangan tangan kosong (attack_rect vs. defender.rect selama ayunan),
# hanya beda reach/damage/timing. "reach" menggantikan UNARMED_REACH sebelum
# range_mult diterapkan; damage light/heavy menggantikan base_dmg tangan
# kosong sebelum dmg_mult/crit diterapkan.
#
# "reach" adalah jangkauan HASIL GAMBAR senjata: attack_rect mulai 18px dari
# x milik fighter, jadi reach = (jarak dari x fighter ke ujung senjata di
# frame thrust sprites/light_<weapon>/1.png) - 18.
#   sword: ujung 138px -> 120      spear: ujung 170px -> 152
# Itu hitbox standarnya; kartu senjata TIDAK menambah range_mult - hanya
# upgrade jangkauan milik pemain sendiri (Longer Reach dll.) yang
# menskalakannya. Jika art serangan ringan pernah digambar ulang, ukur
# ulang angka ini (dicek oleh test_light_weapon_render.py).
WEAPON_MELEE = {
    "sword": {
        "reach": 120, "light_dmg": 11, "heavy_dmg": 24,
        "light_duration": 14, "heavy_duration": 24,
        "light_cooldown": 20, "heavy_cooldown": 36,
    },
    "spear": {
        "reach": 152, "light_dmg": 10, "heavy_dmg": 21,
        "light_duration": 15, "heavy_duration": 24,
        "light_cooldown": 22, "heavy_cooldown": 34,
    },
}

# Senjata jarak jauh (gun): serangannya memunculkan Projectile, bukan
# memakai attack_rect melee - lihat Fighter._start_attack(),
# spawn_projectiles(), dan update_projectiles() di bawah.
GUN_STATS = {
    "light_dmg": 9, "heavy_dmg": 16,
    "light_cooldown": 22, "heavy_cooldown": 40,
    "recoil_duration": 10,   # penguncian attack_timer singkat untuk animasi, bukan hitbox melee
    "bullet_speed": 16,      # px/tick
    "bullet_radius": 5,
}

# Titik sebenarnya sebuah peluru muncul, supaya keluar dari moncong senjata,
# bukan dari mana pun fighter.x/y kebetulan berada (kira-kira tinggi
# pinggang/torso). wants_to_shoot dikonsumsi oleh spawn_projectiles() pada
# tick pertama serangan (lihat Fighter._start_attack()), jadi pose yang
# tampil di layar saat itu selalu frame 0 dari pose gun full-body yang
# sudah dipanggang (baked) - sprites/light_gun/0.png untuk tembakan light,
# sprites/heavy_gun/0.png untuk yang heavy (frame ke-2 heavy_gun tidak
# pernah relevan - tembakan sudah keluar sebelum frame itu tampil).
# (forward, up) dalam pixel, diukur langsung dari kedua PNG sumber
# tersebut: jarak pixel moncong senjata dari garis-tengah torso canvas
# (forward, mengikuti Fighter.facing) dan dari garis bawah canvas / kaki
# (up). Ukur ulang keduanya kalau art itu pernah digambar ulang atau
# di-rescale.
GUN_MUZZLE_OFFSET = {
    "light": (77, 145),   # sprites/light_gun/0.png (canvas 162x211)
    "heavy": (56, 137),   # sprites/heavy_gun/0.png (canvas 124x206)
}

WEAPON_NAMES = ("sword", "spear", "gun")  # juga cocok dengan prefix key sprite/anim

# ---------------------------------------------------------------------------
# Kartu upgrade - sistem pool/rarity yang sama persis dengan prototipe lokal.
# ---------------------------------------------------------------------------
PERMANENT_CARDS = [
    {"name": "Extra HP", "desc": "+25 max HP", "category": "permanent",
     "effect": "max_hp", "value": 25},
    {"name": "Speed Boost", "desc": "+20% move speed", "category": "permanent",
     "effect": "speed_mult", "value": 1.2},
    {"name": "Heavy Hitter", "desc": "+30% attack damage", "category": "permanent",
     "effect": "dmg_mult", "value": 1.3},
    {"name": "Longer Reach", "desc": "+20% attack range", "category": "permanent",
     "effect": "range_mult", "value": 1.2},
    {"name": "Double Jump", "desc": "Gain an extra mid-air jump", "category": "permanent",
     "effect": "double_jump", "value": True},
    {"name": "Lifesteal", "desc": "Heal 20% of damage dealt", "category": "permanent",
     "effect": "lifesteal", "value": 0.2},
    {"name": "Quick Hands", "desc": "-25% attack cooldown", "category": "permanent",
     "effect": "cooldown_mult", "value": 0.75},
    {"name": "Iron Guard", "desc": "Blocking is 30% more effective", "category": "permanent",
     "effect": "block_mult", "value": 0.7},
    {"name": "Featherweight", "desc": "+15% jump height", "category": "permanent",
     "effect": "jump_mult", "value": 1.15},
    {"name": "Precision", "desc": "+15% critical hit chance", "category": "permanent",
     "effect": "crit_chance", "value": 0.15},
    {"name": "Brutal Strikes", "desc": "+25% critical hit damage", "category": "permanent",
     "effect": "crit_dmg", "value": 0.25},
    {"name": "Evasion", "desc": "+10% dodge chance", "category": "permanent",
     "effect": "dodge_chance", "value": 0.10},
]

RARE_CARDS = [
    {"name": "Aegis' Pact", "desc": "Blocking is 60% more effective, -10% attack damage", "category": "rare",
     "effect": "multi", "value": [("block_mult", 0.4), ("dmg_mult", 0.9)]},
    {"name": "Glass Cannon", "desc": "+60% attack damage, -30 max HP", "category": "rare",
     "effect": "multi", "value": [("dmg_mult", 1.6), ("max_hp", -30)]},
    {"name": "Iron Skin", "desc": "+50 max HP, -15% move speed, +15% attack cooldown", "category": "rare",
     "effect": "multi", "value": [("max_hp", 50), ("speed_mult", 0.85), ("cooldown_mult", 1.15)]},
    {"name": "Adrenaline Rush", "desc": "+40% move speed, -25% attack cooldown, blocking is 30% less effective",
     "category": "rare", "effect": "multi", "value": [("speed_mult", 1.4), ("cooldown_mult", 0.75), ("block_mult", 1.3)]},
    {"name": "Berserker's Edge", "desc": "+40% critical hit damage, -15% critical hit chance",
     "category": "rare", "effect": "multi", "value": [("crit_dmg", 0.40), ("crit_chance", -0.15)]},
    {"name": "Killer Instinct", "desc": "+25% critical hit chance, -15% dodge chance",
     "category": "rare", "effect": "multi", "value": [("crit_chance", 0.25), ("dodge_chance", -0.15)]},
    {"name": "Evasive Maneuvers", "desc": "+20% dodge chance, -30% critical hit damage",
     "category": "rare", "effect": "multi", "value": [("dodge_chance", 0.20), ("crit_dmg", -0.30)]},
    {"name": "Chaos", "desc": "Randomly boosts 3 stats and weakens 3 different stats, "
                              "each by a random amount. Ignores stat limits.", "category": "rare", "effect": "chaos", "value": None},
]

UNIQUE_CARDS = [
    {"name": "Second Wind", "desc": "Revive with 30% HP when defeated (once per round)",
     "category": "unique", "effect": "second_wind", "value": True},
    {"name": "Vampiric Curse", "desc": "Heal 50% of damage dealt", "category": "unique",
     "effect": "lifesteal", "value": 0.5},
    {"name": "Guardian's Blessing", "desc": "The first unblocked hit you take each round is completely negated",
     "category": "unique", "effect": "first_hit_shield", "value": True},
    {"name": "Freezing Strikes", "desc": "Your hits briefly slow the opponent's movement and attack speed",
     "category": "unique", "effect": "freezing_strikes", "value": True},
    {"name": "Burning Strikes", "desc": "Your hits burn the opponent over time",
     "category": "unique", "effect": "burning_strikes", "value": True},
    {"name": "Thorny", "desc": "Hits you take deal some damage back - more if you block them",
     "category": "unique", "effect": "thorns", "value": True},
]

# Kartu senjata: tier unique (diambil dari pool/peluang yang sama dengan
# kartu unique lain di atas), tapi dengan pembatasan tambahan per-fighter
# di atas aturan match-wide biasa "hilang begitu ada yang mengambilnya":
# setiap fighter hanya boleh memasang SATU senjata saja, seumur
# pertandingan (lihat parameter exclude_weapons di random_cards() dan
# Fighter.weapon). Meski begitu, 2 pemain BISA berakhir dengan 2 senjata
# berbeda - pembatasannya per pemain, bukan "cuma ada 1 senjata di seluruh
# pertandingan".
#
# effect "weapon": value berupa {"weapon": <nama>, "stats": [(stat, val), ...]}
# - memasang senjata yang disebut (Fighter.weapon) DAN menerapkan bonus
# stat yang terdaftar, bentuknya sama seperti value list kartu "multi".
WEAPON_CARDS = [
    {"name": "Sword", "desc": "Equip a sword: faster, harder-hitting melee "
                               "swings with extra reach and crit chance. "
                               "You can only ever equip one weapon.",
     "category": "unique", "effect": "weapon",
     "value": {"weapon": "sword", "stats": [("dmg_mult", 1.15), ("crit_chance", 0.08)]}},
    {"name": "Spear", "desc": "Equip a spear: much longer melee reach, "
                               "modest damage. You can only ever equip one weapon.",
     "category": "unique", "effect": "weapon",
     "value": {"weapon": "spear", "stats": [("dmg_mult", 1.05)]}},
    {"name": "Gun", "desc": "Equip a gun: fire ranged shots instead of "
                             "melee attacks. You can only ever equip one weapon.",
     "category": "unique", "effect": "weapon",
     "value": {"weapon": "gun", "stats": [("dmg_mult", 1.05), ("crit_chance", 0.05)]}},
]

UNIQUE_CARDS = UNIQUE_CARDS + WEAPON_CARDS

CARD_TIER_WEIGHT = {"permanent": 10, "rare": 4, "unique": 3}


def _weighted_sample_without_replacement(items, weights, k):
    pool = list(zip(items, weights))
    chosen = []
    for _ in range(min(k, len(pool))):
        total = sum(w for _, w in pool)
        r = random.uniform(0, total)
        upto = 0
        for i, (item, w) in enumerate(pool):
            upto += w
            if upto >= r:
                chosen.append(item)
                pool.pop(i)
                break
    return chosen


def random_cards(used_unique_cards, n=3, exclude_weapons=False, guarantee_weapon=False):
    """exclude_weapons: isi True saat fighter yang memilih sudah punya
    senjata (Fighter.weapon bukan None) - slot senjatanya sudah terpakai,
    jadi ketiga kartu senjata tidak boleh ditawarkan ke dia, meskipun
    pemain lain mungkin masih bisa mengambil salah satunya.

    guarantee_weapon: opsi match "Guaranteed Weapons" (lihat
    Match.guaranteed_weapons). Kalau True - dan pemilih belum punya
    senjata, serta masih ada minimal satu kartu senjata yang belum
    diambil siapa pun - salah satu dari n slot dipaksa jadi kartu senjata
    acak yang masih tersedia, sisanya diambil secara normal (berbobot,
    dan tetap bisa saja kebagian kartu senjata kedua secara kebetulan).
    Tidak berefek kalau exclude_weapons True atau sudah tidak ada kartu
    senjata tersisa."""
    available = (
        PERMANENT_CARDS
        + RARE_CARDS
        + [c for c in UNIQUE_CARDS
           if c["name"] not in used_unique_cards
           and not (exclude_weapons and c["effect"] == "weapon")]
    )
    if guarantee_weapon and not exclude_weapons and n > 0:
        weapon_pool = [c for c in available if c["effect"] == "weapon"]
        if weapon_pool:
            guaranteed = random.choice(weapon_pool)
            rest_pool = [c for c in available if c is not guaranteed]
            rest_weights = [CARD_TIER_WEIGHT[c["category"]] for c in rest_pool]
            rest = _weighted_sample_without_replacement(rest_pool, rest_weights, n - 1)
            chosen = [guaranteed] + rest
            random.shuffle(chosen)  # supaya kartu senjata tidak selalu di slot yang sama
            return chosen
    weights = [CARD_TIER_WEIGHT[c["category"]] for c in available]
    return _weighted_sample_without_replacement(available, weights, n)


# ---------------------------------------------------------------------------
# Projectile - hanya pernah dibuat oleh fighter yang memegang gun. Disimulasikan
# sebagai peluru garis lurus tanpa gravitasi: bergerak dengan kecepatan
# konstan tiap tick, hilang begitu keluar layar atau kena sesuatu.
# ---------------------------------------------------------------------------
class Projectile:
    __slots__ = ("x", "y", "vx", "owner_num", "dmg", "radius", "kind", "alive")

    def __init__(self, x, y, vx, owner_num, dmg, radius, kind):
        self.x, self.y, self.vx = x, y, vx
        self.owner_num = owner_num  # 1 atau 2 - tembakan siapa ini, supaya tidak kena penembaknya sendiri
        self.dmg = dmg              # damage dasar (sebelum dmg_mult/crit - diresolusi sama seperti hit melee)
        self.radius = radius
        self.kind = kind            # "light" atau "heavy" - cuma untuk visual
        self.alive = True

    @property
    def rect(self):
        r = self.radius
        return Rect(int(self.x - r), int(self.y - r), r * 2, r * 2)

    def update(self):
        self.x += self.vx
        if self.x < -50 or self.x > SCREEN_W + 50:
            self.alive = False

    def to_dict(self):
        return {"x": round(self.x, 1), "y": round(self.y, 1),
                "kind": self.kind, "owner": self.owner_num}


BASE_STATS = {
    "max_hp": 140, "speed_mult": 1.0, "dmg_mult": 1.0, "range_mult": 1.0,
    "cooldown_mult": 1.0, "block_mult": 1.0, "jump_mult": 1.0,
    "extra_jumps": 0, "lifesteal": 0.0, "crit_chance": 0.0,
    "crit_dmg_mult": 1.5, "dodge_chance": 0.0,
}


def _step_clamped(current, delta, lo=None, hi=None):
    """Menambahkan delta ke current, sambil menghormati batas bawah (lo) /
    batas atas (hi) stat - tapi hanya di arah yang didorong perubahannya.
    Buff tidak pernah bisa mengangkat stat melebihi hi, dan debuff tidak
    pernah bisa menjatuhkannya di bawah lo, tapi stat yang SUDAH berada di
    luar [lo, hi] (Chaos bisa melakukan itu, lihat Fighter._apply_chaos)
    tidak pernah langsung ditarik paksa kembali ke dalam range oleh kartu
    biasa; ia hanya bergerak menuju range sebesar nilai kartunya (lalu
    berhenti di batasnya)."""
    new = current + delta
    if delta > 0 and hi is not None:
        new = min(new, max(hi, current))
    elif delta < 0 and lo is not None:
        new = max(new, min(lo, current))
    return new


def _pct_delta(value, base):
    """String gaya +30% / -15% untuk stat yang bersifat perkalian."""
    delta = (value / base - 1.0) * 100 if base else 0.0
    return f"{delta:+.0f}%"


# ---------------------------------------------------------------------------
# Fighter - simulasi fisika/stat murni. Tidak ada gambar, suara, atau
# konstanta key pygame: input masuk sebagai dict boolean biasa (lihat
# step()), jadi class ini bekerja identik baik digerakkan oleh keyboard
# lokal (seperti di prototipe awal) maupun oleh paket input yang diterima
# lewat jaringan.
# ---------------------------------------------------------------------------
class Fighter:
    def __init__(self, base_x, facing):
        self.base_x = base_x
        self.facing = facing  # 1 = kanan, -1 = kiri

        # Stat dasar (diubah oleh kartu upgrade)
        self.max_hp = 140
        self.speed_mult = 1.0
        self.dmg_mult = 1.0
        self.range_mult = 1.0
        self.cooldown_mult = 1.0
        self.block_mult = 1.0
        self.jump_mult = 1.0
        self.extra_jumps = 0
        self.lifesteal = 0.0
        self.first_hit_shield = False
        self.has_second_wind = False
        self.second_wind_used = False
        self.crit_chance = 0.0
        self.crit_dmg_mult = 1.5   # pengali bonus dasar saat critical hit
        self.dodge_chance = 0.0
        self.freezing_strikes = False  # hit-mu memperlambat lawan
        self.burning_strikes = False   # hit-mu membakar lawan
        self.thorns = False            # hit yang kamu terima memantulkan sebagian ke penyerang
        self.slow_timer = 0            # sisa tick selagi melambat kena hit freezing lawan
        self.burn_timer = 0            # sisa tick kena damage burn
        self._cooldown_slow_acc = 0.0  # progres pecahan (fractional) cooldown serangan selagi frozen
        self.weapon = None             # None, "sword", "spear", atau "gun" - lihat WEAPON_CARDS
        self.wants_to_shoot = None     # diisi "light"/"heavy" selama satu tick saat pemegang gun menembak
        self._attack_total = 0         # durasi serangan saat ini, untuk timing jendela-hit
        self.cards = []

        self.reset_for_round()

    def reset_for_round(self):
        self.x = self.base_x
        self.y = GROUND_Y
        self.vel_y = 0
        self.on_ground = True
        self.jumps_used = 0
        self.hp = self.max_hp
        self.attack_timer = 0
        self.attack_cooldown = 0
        self.attack_type = None
        self.hit_flash = 0
        self.blocking = False
        self.hit_this_swing = False
        self.is_dead = False
        self.shield_used_this_round = False
        self.second_wind_used = False  # Second Wind hanya sekali per RONDE, bukan per pertandingan
        self.is_moving = False
        self.slow_timer = 0
        self.burn_timer = 0
        self._cooldown_slow_acc = 0.0
        self.wants_to_shoot = None
        self._attack_total = 0
        # flag event sekali-pakai untuk tick ini, dibaca + dibersihkan oleh
        # loop server lalu diteruskan ke client supaya tahu kapan harus
        # memutar suara
        self.events = []

    def _apply_single_effect(self, eff, val, bypass_caps=False):
        """bypass_caps=True (khusus Chaos) melewati batas 0%/100% pada
        crit chance, dodge chance, dan lifesteal, serta batas bawah x1.0
        pada crit damage, jadi sebuah roll selalu diterapkan penuh dan
        stat bisa berakhir di atas 100% atau di bawah 0. Max HP tetap
        punya batas bawah 1 apa pun yang terjadi (0 HP berarti KO instan
        tiap ronde); stat yang bersifat perkalian memang dari awal tidak
        punya batas. Nilai di luar range normal baru diamankan pas
        DIPAKAI (resolve_hit), bukan di sini."""
        if eff == "max_hp":
            self.max_hp = max(1, self.max_hp + val)
            self.hp = min(self.hp, self.max_hp)
        elif eff == "speed_mult":
            self.speed_mult *= val
        elif eff == "dmg_mult":
            self.dmg_mult *= val
        elif eff == "range_mult":
            self.range_mult *= val
        elif eff == "cooldown_mult":
            self.cooldown_mult *= val
        elif eff == "block_mult":
            self.block_mult *= val
        elif eff == "jump_mult":
            self.jump_mult *= val
        elif eff == "double_jump":
            self.extra_jumps += 1
        elif eff == "lifesteal":
            self.lifesteal = (self.lifesteal + val if bypass_caps
                              else _step_clamped(self.lifesteal, val, lo=0.0))
        elif eff == "second_wind":
            self.has_second_wind = True
        elif eff == "first_hit_shield":
            self.first_hit_shield = True
        elif eff == "crit_chance":
            self.crit_chance = (self.crit_chance + val if bypass_caps
                                else _step_clamped(self.crit_chance, val, lo=0.0, hi=1.0))
        elif eff == "crit_dmg":
            self.crit_dmg_mult = (self.crit_dmg_mult + val if bypass_caps
                                  else _step_clamped(self.crit_dmg_mult, val, lo=1.0))
        elif eff == "dodge_chance":
            self.dodge_chance = (self.dodge_chance + val if bypass_caps
                                 else _step_clamped(self.dodge_chance, val, lo=0.0, hi=DODGE_CHANCE_CAP))
        elif eff == "freezing_strikes":
            self.freezing_strikes = True
        elif eff == "burning_strikes":
            self.burning_strikes = True
        elif eff == "thorns":
            self.thorns = True

    def apply_card(self, card):
        eff, val = card["effect"], card["value"]
        if eff == "multi":
            for sub_eff, sub_val in val:
                self._apply_single_effect(sub_eff, sub_val)
        elif eff == "chaos":
            self._apply_chaos()
        elif eff == "weapon":
            self.weapon = val["weapon"]
            for sub_eff, sub_val in val.get("stats", []):
                self._apply_single_effect(sub_eff, sub_val)
        else:
            self._apply_single_effect(eff, val)
        self.cards.append(card["name"])

    # Stat yang bisa di-roll Chaos, masing-masing punya range yang
    # menguntungkan fighter (buff) dan range yang merugikan (debuff). Stat
    # perkalian pakai faktor di sekitar 1.0; stat penjumlahan pakai delta
    # +/- kecil. cooldown_mult dan block_mult itu terbalik - makin rendah
    # makin bagus untuk keduanya.
    CHAOS_STAT_RANGES = {
        "max_hp":        ((15, 40), (-40, -15)),
        "speed_mult":     ((1.05, 1.25), (0.80, 0.95)),
        "dmg_mult":       ((1.10, 1.35), (0.70, 0.90)),
        "range_mult":     ((1.10, 1.30), (0.75, 0.90)),
        "cooldown_mult":  ((0.75, 0.90), (1.10, 1.30)),
        "block_mult":     ((0.60, 0.85), (1.15, 1.40)),
        "jump_mult":      ((1.10, 1.30), (0.75, 0.90)),
        "crit_chance":    ((0.08, 0.18), (-0.18, -0.08)),
        "crit_dmg":       ((0.15, 0.35), (-0.35, -0.15)),
        "dodge_chance":   ((0.08, 0.18), (-0.18, -0.08)),
        "lifesteal":      ((0.10, 0.25), (-0.25, -0.10)),
    }

    def _apply_chaos(self):
        """Di-roll ulang tiap kali Chaos diambil: 3 stat acak naik, 3 stat
        acak lain (berbeda) turun, masing-masing sebesar jumlah acak.
        Chaos melewati batas stat biasa (bypass_caps=True), jadi debuff ke
        stat yang belum kamu punya tetap kena (jadi negatif, dan buff
        berikutnya harus "melunasi" itu dulu) dan buff yang melewati 100%
        tetap disimpan sebagai cadangan."""
        stats = random.sample(list(self.CHAOS_STAT_RANGES.keys()), 6)
        for name in stats[:3]:
            lo, hi = self.CHAOS_STAT_RANGES[name][0]
            self._apply_single_effect(name, random.uniform(lo, hi), bypass_caps=True)
        for name in stats[3:]:
            lo, hi = self.CHAOS_STAT_RANGES[name][1]
            self._apply_single_effect(name, random.uniform(lo, hi), bypass_caps=True)

    @property
    def rect(self):
        # Mencakup seluruh tinggi sprite (kepala sampai kaki, ~197-202px
        # di art aslinya), bukan cuma bagian bawah/tengah tubuh. Dulunya
        # berhenti di 110px, yang kebetulan tidak kelihatan selagi di
        # tanah (attack_rect melee dan tinggi spawn gun sama-sama di
        # y-70, masih nyaman di dalam 0..-110), tapi artinya tembakan yang
        # dilepaskan dari posisi lebih tinggi - misalnya pemegang gun
        # sedang lompat - bisa melesat tembus kepala/bahu lawan tanpa
        # pernah masuk ke hurtbox mereka.
        return Rect(int(self.x - 18), int(self.y - 190), 36, 190)

    @property
    def attack_rect(self):
        """Hitbox melee untuk ayunan saat ini. Sama sekali tidak dipakai
        untuk gun - serangan gun diresolusi lewat tabrakan Projectile
        (lihat spawn_projectiles()/update_projectiles())."""
        base_reach = WEAPON_MELEE[self.weapon]["reach"] if self.weapon in WEAPON_MELEE else UNARMED_REACH
        reach = base_reach * self.range_mult
        w = int(reach)
        h = 24
        x = self.x + 18 if self.facing == 1 else self.x - 18 - w
        y = self.y - 70
        return Rect(int(x), int(y), w, h)

    def base_attack_damage(self, kind):
        """Damage sebelum dmg_mult/crit diterapkan - tergantung senjata
        yang dipakai saat ini (atau tanpa senjata sama sekali). Dipakai
        bersama oleh resolusi hit melee dan resolusi hit projectile,
        supaya angka sword/spear/gun hanya hidup di satu tempat ini."""
        if self.weapon == "gun":
            return GUN_STATS["light_dmg"] if kind == "light" else GUN_STATS["heavy_dmg"]
        if self.weapon in WEAPON_MELEE:
            w = WEAPON_MELEE[self.weapon]
            return w["light_dmg"] if kind == "light" else w["heavy_dmg"]
        return UNARMED_LIGHT_DMG if kind == "light" else UNARMED_HEAVY_DMG

    def handle_input(self, intent):
        """intent: dict dengan key boolean left/right/jump/light/heavy/block."""
        move_speed = 5 * self.speed_mult
        if self.slow_timer > 0:
            move_speed *= FREEZE_SLOW_FACTOR
        self.blocking = False
        self.is_moving = False

        if self.attack_timer <= 0:
            if intent.get("left"):
                self.x -= move_speed
                self.facing = -1
                self.is_moving = True
            if intent.get("right"):
                self.x += move_speed
                self.facing = 1
                self.is_moving = True

        if intent.get("block") and self.attack_timer <= 0:
            self.blocking = True

        if intent.get("jump"):
            max_jumps = 1 + self.extra_jumps
            if self.on_ground:
                self._do_jump()
            elif self.jumps_used < max_jumps and self.vel_y > -5:
                self._do_jump()

        if self.attack_cooldown <= 0 and self.attack_timer <= 0:
            if intent.get("light"):
                self._start_attack("light")
            elif intent.get("heavy"):
                self._start_attack("heavy")

        self.x = max(30, min(SCREEN_W - 30, self.x))

    def _do_jump(self):
        self.vel_y = -16 * self.jump_mult
        self.on_ground = False
        self.jumps_used += 1
        self.events.append("jump")

    def _start_attack(self, kind):
        self.attack_type = kind
        self.hit_this_swing = False

        if self.weapon == "gun":
            # Sama sekali tidak ada hitbox melee - cuma penguncian
            # recoil/animasi singkat, dan sebuah flag supaya loop
            # Match/ronde tahu harus memunculkan Projectile.
            self.wants_to_shoot = kind
            self.attack_timer = GUN_STATS["recoil_duration"]
            self._attack_total = self.attack_timer
            cd = GUN_STATS["light_cooldown"] if kind == "light" else GUN_STATS["heavy_cooldown"]
            self.attack_cooldown = int(cd * self.cooldown_mult)
            self.hit_this_swing = True  # tidak pernah diresolusi sebagai ayunan melee
            return

        if self.weapon in WEAPON_MELEE:
            w = WEAPON_MELEE[self.weapon]
            self.attack_timer = w["light_duration"] if kind == "light" else w["heavy_duration"]
            cd = w["light_cooldown"] if kind == "light" else w["heavy_cooldown"]
            # Sfx ayunan senjata - satu event per mulainya ayunan (light
            # atau heavy), dinamai sesuai nama senjatanya supaya cocok
            # dengan sfx/sword/, sfx/spear/, sama seperti "shoot" cocok
            # dengan sfx/shoot/.
            self.events.append(self.weapon)
        else:
            self.attack_timer = UNARMED_LIGHT_DURATION if kind == "light" else UNARMED_HEAVY_DURATION
            cd = UNARMED_LIGHT_COOLDOWN if kind == "light" else UNARMED_HEAVY_COOLDOWN
        self._attack_total = self.attack_timer
        self.attack_cooldown = int(cd * self.cooldown_mult)

    def physics_update(self):
        self.vel_y += GRAVITY
        self.y += self.vel_y
        if self.y >= GROUND_Y:
            self.y = GROUND_Y
            self.vel_y = 0
            self.on_ground = True
            self.jumps_used = 0

        if self.attack_timer > 0:
            self.attack_timer -= 1
            if self.attack_timer == 0:
                self.attack_type = None
        if self.attack_cooldown > 0:
            if self.slow_timer > 0:
                # Freezing Strikes juga memperlambat kecepatan serangan:
                # selagi slowed, cooldown cuma berkurang di sebagian
                # tick saja. (Ayunannya sendiri tidak diutak-atik - animasi
                # dan jendela-hit-nya terikat ke jumlah tick tetap - jadi
                # ini cuma memperlebar jeda antar serangan, untuk semua
                # senjata termasuk gun.)
                self._cooldown_slow_acc += FREEZE_ATTACK_SPEED_FACTOR
                if self._cooldown_slow_acc >= 1.0:
                    self._cooldown_slow_acc -= 1.0
                    self.attack_cooldown -= 1
            else:
                self.attack_cooldown -= 1
        if self.hit_flash > 0:
            self.hit_flash -= 1
        if self.slow_timer > 0:
            self.slow_timer -= 1
        if self.burn_timer > 0:
            self.burn_timer -= 1
            self._apply_dot(BURN_DPS)

    def _try_second_wind(self):
        """Second Wind: saat HP pertama kali menyentuh 0 di sebuah ronde,
        bangkit lagi dengan SECOND_WIND_HP_FRACTION dari max HP alih-alih
        mati. Diperbarui setiap ronde (reset_for_round membersihkan
        second_wind_used). Mengembalikan True kalau efeknya terpicu."""
        if self.has_second_wind and not self.second_wind_used:
            self.second_wind_used = True
            self.hp = max(1.0, self.max_hp * SECOND_WIND_HP_FRACTION)
            self.is_dead = False
            self.events.append("second_wind")
            return True
        return False

    def _apply_dot(self, dmg):
        """Damage yang melewati block/shield - dipakai untuk tick burn,
        pantulan thorns, dan recoil lifesteal negatif, karena tidak ada
        satu pun dari itu yang merupakan serangan langsung masuk."""
        self.hp -= dmg
        if self.hp <= 0 and not self._try_second_wind():
            self.hp = 0
            self.is_dead = True

    def try_negate_hit(self):
        """Guardian's Blessing: hit TAK-TERBLOK pertama yang diterima tiap
        ronde sepenuhnya dinegasikan (tanpa damage, tanpa status effect,
        tanpa lifesteal, tanpa thorns - resolve_hit langsung keluar saat
        ini mengembalikan True). Hit yang diblok tidak memakai blessing
        ini, begitu juga hit yang di-dodge (dodge di-roll sebelum ini)
        atau damage burn/thorns (keduanya tidak pernah lewat sini)."""
        if self.first_hit_shield and not self.shield_used_this_round and not self.blocking:
            self.shield_used_this_round = True
            self.events.append("negated")
            return True
        return False

    def take_hit(self, dmg):
        if self.blocking:
            dmg = dmg * 0.15 * self.block_mult
        self.hp -= dmg
        if self.hp <= 0 and not self._try_second_wind():
            self.hp = 0
            self.is_dead = True
        self.hit_flash = 8
        self.events.append("hit")
        return dmg

    def stats_dict(self):
        """Snapshot numerik ringkas dari semua stat yang ditampilkan panel
        stats. Dikirim lewat jaringan (lihat to_dict) dan diformat untuk
        ditampilkan oleh format_stat_lines() - disimpan sebagai angka
        mentah, bukan string siap-pakai, supaya paketnya tetap kecil dan
        format tampilannya hidup di satu tempat bersama. Nilai yang masih
        di default awalnya DIHILANGKAN (format_stat_lines mengisinya
        kembali dari BASE_STATS), yang menjaga paket state tetap jauh di
        bawah MTU UDP ~1500 byte."""
        full = {
            "max_hp": round(self.max_hp, 1),
            "speed_mult": round(self.speed_mult, 3),
            "dmg_mult": round(self.dmg_mult, 3),
            "range_mult": round(self.range_mult, 3),
            "cooldown_mult": round(self.cooldown_mult, 3),
            "block_mult": round(self.block_mult, 3),
            "jump_mult": round(self.jump_mult, 3),
            "extra_jumps": self.extra_jumps,
            "lifesteal": round(self.lifesteal, 3),
            "crit_chance": round(self.crit_chance, 3),
            "crit_dmg_mult": round(self.crit_dmg_mult, 3),
            "dodge_chance": round(self.dodge_chance, 3),
        }
        out = {k: v for k, v in full.items()
               if abs(v - BASE_STATS[k]) > 1e-9}
        if self.weapon:
            out["weapon"] = self.weapon
        for key, val in (("freezing_strikes", self.freezing_strikes),
                          ("burning_strikes", self.burning_strikes),
                          ("thorns", self.thorns),
                          ("first_hit_shield", self.first_hit_shield),
                          ("has_second_wind", self.has_second_wind),
                          ("second_wind_used", self.second_wind_used)):
            if val:
                out[key] = True
        return out

    def to_dict(self, include_stats=True):
        """include_stats=False menghilangkan blok stats (yang relatif
        besar). Stat hanya berubah saat sebuah kartu diterapkan, jadi
        server mengirimnya sesekali saja dan client menyimpan (cache) set
        terakhir yang dilihatnya - lihat Match.to_dict(). Mengirimnya
        setiap tick mendorong paket melebihi MTU ~1500-byte dan
        menyebabkan fragmentasi UDP."""
        d = {
            "x": round(self.x, 1), "y": round(self.y, 1),
            "facing": self.facing, "hp": round(self.hp, 1), "max_hp": self.max_hp,
            "on_ground": self.on_ground, "blocking": self.blocking,
            "attack_type": self.attack_type, "attack_timer": self.attack_timer,
            "is_dead": self.is_dead, "vel_y": round(self.vel_y, 2),
            "cards": self.cards, "events": self.events, "weapon": self.weapon,
        }
        if include_stats:
            d["stats"] = self.stats_dict()
        return d


# ---------------------------------------------------------------------------
# Format panel stats - dipakai bersama supaya prototipe lokal dan client
# online menampilkan label, urutan, dan format angka yang sama persis.
# Menerima dict polos dari Fighter.stats_dict() (lokal: langsung dari
# objeknya; online: langsung dari paket state server), jadi kedua sisi
# tidak perlu objek Fighter yang hidup untuk menggambar panelnya.
# ---------------------------------------------------------------------------
def format_stat_lines(stats):
    """Mengembalikan list tuple (label, teks_nilai, tone) untuk ditampilkan.
    tone berupa "good" / "bad" / "neutral" supaya UI bisa memberi warna
    apakah sebuah stat saat ini lebih baik atau lebih buruk dari nilai
    awal fighter - termasuk untuk cooldown_mult/block_mult, yang
    TERBALIK (makin rendah makin bagus), detail yang paling mungkin
    membingungkan pemain yang membaca angka mentahnya. Key yang tidak ada
    memakai fallback ke BASE_STATS, karena stats_dict() menghilangkan
    nilai yang belum berubah demi menjaga paket jaringan tetap kecil."""
    s = dict(BASE_STATS)
    s.update(stats or {})
    lines = []

    def tone_from(value, base, lower_is_better=False):
        if abs(value - base) < 1e-6:
            return "neutral"
        better = value < base if lower_is_better else value > base
        return "good" if better else "bad"

    lines.append(("Max HP", f"{s['max_hp']:g}",
                  tone_from(s["max_hp"], BASE_STATS["max_hp"])))
    lines.append(("Damage", _pct_delta(s["dmg_mult"], 1.0),
                  tone_from(s["dmg_mult"], 1.0)))
    lines.append(("Move speed", _pct_delta(s["speed_mult"], 1.0),
                  tone_from(s["speed_mult"], 1.0)))
    lines.append(("Attack range", _pct_delta(s["range_mult"], 1.0),
                  tone_from(s["range_mult"], 1.0)))
    # terbalik: cooldown_mult lebih rendah = serangan lebih cepat, jadi ditampilkan sebagai "attack speed"
    lines.append(("Attack speed", _pct_delta(1.0 / s["cooldown_mult"], 1.0)
                  if s["cooldown_mult"] else "+0%",
                  tone_from(s["cooldown_mult"], 1.0, lower_is_better=True)))
    # terbalik: block_mult lebih rendah = chip damage yang diterima saat blocking lebih kecil
    lines.append(("Block strength", _pct_delta(1.0 / s["block_mult"], 1.0)
                  if s["block_mult"] else "+0%",
                  tone_from(s["block_mult"], 1.0, lower_is_better=True)))
    lines.append(("Jump height", _pct_delta(s["jump_mult"], 1.0),
                  tone_from(s["jump_mult"], 1.0)))
    lines.append(("Extra jumps", f"{s['extra_jumps']}",
                  tone_from(s["extra_jumps"], 0)))
    lines.append(("Crit chance", f"{s['crit_chance'] * 100:.0f}%",
                  tone_from(s["crit_chance"], 0.0)))
    lines.append(("Crit damage", f"x{s['crit_dmg_mult']:.2f}",
                  tone_from(s["crit_dmg_mult"], 1.5)))
    lines.append(("Dodge chance", f"{s['dodge_chance'] * 100:.0f}%",
                  tone_from(s["dodge_chance"], 0.0)))
    lines.append(("Lifesteal", f"{s['lifesteal'] * 100:.0f}%",
                  tone_from(s["lifesteal"], 0.0)))

    weapon = s.get("weapon")
    lines.append(("Weapon", weapon.capitalize() if weapon else "None",
                  "good" if weapon else "neutral"))

    # efek on/off, cuma ditampilkan saat benar-benar aktif, biar panelnya tetap ringkas
    for key, label in (("freezing_strikes", "Freezing Strikes"),
                        ("burning_strikes", "Burning Strikes"),
                        ("thorns", "Thorny"),
                        ("first_hit_shield", "Guardian's Blessing")):
        if s.get(key):
            lines.append((label, "Active", "good"))
    if s.get("has_second_wind"):
        used = s.get("second_wind_used")
        lines.append(("Second Wind", "Used" if used else "Ready",
                      "bad" if used else "good"))
    return lines


# ---------------------------------------------------------------------------
# Resolusi combat - fungsi level-modul (bukan method Match) supaya baik
# Match.step() (main online server-authoritative) maupun loop ronde
# hotseat lokal di stickman_brawler.py bisa memanggil kode yang sama
# persis alih-alih masing-masing mengimplementasikan ulang deteksi hit.
# Ini bagian yang seharusnya jadi jebakan "logika terduplikasi" berikutnya
# kalau sampai ditulis dua kali.
# ---------------------------------------------------------------------------
def resolve_hit(attacker, defender, base_dmg):
    """Matematika resolusi-hit bersama: peluang dodge, Guardian's
    Blessing, peluang/damage crit, blocking, lifesteal, dan efek kartu
    unique freeze/burn/thorns. Dipakai baik untuk ayunan melee yang kena
    maupun peluru yang kena, jadi tidak satu pun sistem itu butuh
    penanganan khusus per senjata.

    Stat yang didorong keluar dari range normalnya oleh Chaos diamankan
    di sini: peluang di atas 1 / di bawah 0 berperilaku sebagai
    selalu/tidak pernah (random() ada di [0, 1)), damage crit tidak bisa
    turun di bawah x0 (crit tidak boleh sampai menyembuhkan target), dan
    lifesteal negatif berubah jadi recoil."""
    if random.random() < defender.dodge_chance:
        defender.events.append("dodge")
        return
    if defender.try_negate_hit():
        return
    dmg = base_dmg * attacker.dmg_mult
    if random.random() < attacker.crit_chance:
        crit_mult = max(0.0, attacker.crit_dmg_mult)
        defender.events.append("crit")
        # Overcrit: cuma bisa tercapai kalau Chaos sudah mendorong
        # crit_chance melewati 100% (kartu biasa dibatasi di 1.0, lihat
        # _apply_single_effect). Crit-nya sendiri sudah pasti terjadi
        # pada titik itu (roll di atas selalu < crit_chance begitu
        # crit_chance >= 1.0); kelebihannya menjadi roll kedua yang
        # independen untuk bonus x2 di atas pengali crit normal -
        # misalnya crit dmg 1.5x -> jadi 3.5x saat overcrit.
        overcrit_chance = attacker.crit_chance - OVERCRIT_THRESHOLD
        if overcrit_chance > 0 and random.random() < overcrit_chance:
            crit_mult += OVERCRIT_BONUS_DMG
            defender.events.append("overcrit")
        dmg *= crit_mult
    was_blocking = defender.blocking
    dealt = defender.take_hit(dmg)
    if attacker.lifesteal > 0:
        attacker.hp = min(attacker.max_hp, attacker.hp + dealt * attacker.lifesteal)
    elif attacker.lifesteal < 0:
        # Hanya bisa tercapai lewat Chaos (lihat _apply_chaos): lifesteal
        # negatif melukai penyerang sebesar sebagian dari damage yang
        # dia berikan. Lanjut lewat _apply_dot agar bisa KO dan
        # menghormati Second Wind.
        attacker._apply_dot(dealt * -attacker.lifesteal)
    if attacker.freezing_strikes:
        defender.slow_timer = FREEZE_SLOW_DURATION
        defender.events.append("frozen")
    if attacker.burning_strikes:
        defender.burn_timer = BURN_DURATION
        defender.events.append("burning")
    if defender.thorns:
        # Kedua cabang sekarang memakai `dealt` (damage sebenarnya yang
        # diterima defender setelah mitigasi) bukan damage mentah
        # pre-block milik penyerang - blocking cuma dapat rate pantulan
        # yang lebih tinggi.
        reflect = dealt * (THORNS_REFLECT_BLOCKED if was_blocking else THORNS_REFLECT_NORMAL)
        attacker._apply_dot(reflect)
        attacker.events.append("thorns")


def process_melee_attacks(p1, p2):
    """Mengecek ayunan melee aktif kedua fighter terhadap hurtbox lawannya
    dan meresolusi yang kena di tengah-tengah ayunan. Melewati (skip)
    fighter yang sedang memegang gun - serangan mereka bersifat ranged,
    ditangani oleh spawn_projectiles()/update_projectiles()."""
    for attacker, defender in ((p1, p2), (p2, p1)):
        if attacker.weapon == "gun":
            continue
        if attacker.attack_timer > 0 and not attacker.hit_this_swing:
            total = attacker._attack_total or 1
            if total - attacker.attack_timer >= total // 3:
                if attacker.attack_rect.colliderect(defender.rect):
                    attacker.hit_this_swing = True
                    resolve_hit(attacker, defender, attacker.base_attack_damage(attacker.attack_type))


def spawn_projectiles(p1, p2, projectiles):
    """Mengubah tembakan gun yang tertunda (Fighter.wants_to_shoot, diisi
    oleh Fighter._start_attack()) menjadi objek Projectile yang
    ditambahkan ke list yang diberikan. Panggil sekali per tick, setelah
    handle_input()."""
    for num, fighter in ((1, p1), (2, p2)):
        if fighter.wants_to_shoot:
            kind = fighter.wants_to_shoot
            fighter.wants_to_shoot = None
            forward, up = GUN_MUZZLE_OFFSET.get(kind, GUN_MUZZLE_OFFSET["light"])
            x = fighter.x + fighter.facing * forward
            y = fighter.y - up
            vx = GUN_STATS["bullet_speed"] * fighter.facing
            projectiles.append(Projectile(x, y, vx, num, fighter.base_attack_damage(kind),
                                           GUN_STATS["bullet_radius"], kind))
            fighter.events.append("shoot")


def update_projectiles(projectiles, p1, p2):
    """Memajukan tiap projectile satu tick dan meresolusi yang kena
    fighter lawan (lewat resolve_hit() yang sama dipakai ayunan melee,
    jadi dodge/crit/block/lifesteal/freeze/burn/thorns semua berlaku
    identik). Mengembalikan list projectile yang masih melayang - sebuah
    peluru dihapus baik saat terbang keluar layar maupun saat kena
    (bahkan tembakan yang di-dodge pun tetap habis)."""
    still_alive = []
    for proj in projectiles:
        proj.update()
        attacker = p1 if proj.owner_num == 1 else p2
        defender = p2 if proj.owner_num == 1 else p1
        if proj.alive and proj.rect.colliderect(defender.rect):
            resolve_hit(attacker, defender, proj.dmg)
            continue
        if proj.alive:
            still_alive.append(proj)
    return still_alive


# ---------------------------------------------------------------------------
# Match - mesin state ronde/kartu/pertandingan secara lengkap, terpisah
# dari event loop atau display pygame. server.py memanggil .step() sekali
# tiap tick jaringan.
# ---------------------------------------------------------------------------
class Match:
    STATE_WAITING = "waiting"
    STATE_FIGHT = "fight"
    STATE_ROUND_END = "round_end"
    STATE_CARD_SELECT = "card_select"
    STATE_MATCH_END = "match_end"

    def __init__(self):
        self.p1 = Fighter(SCREEN_W * 0.25, 1)
        self.p2 = Fighter(SCREEN_W * 0.75, -1)
        self.p1_round_wins = 0
        self.p2_round_wins = 0
        self.rounds_to_win = ROUNDS_TO_WIN_MATCH
        self.guaranteed_weapons = False  # opsi match "Guaranteed Weapons", diatur host - lihat configure_guaranteed_weapons()
        self.used_unique_cards = set()
        self.state = self.STATE_WAITING
        self.round_time_left = ROUND_TIME_LIMIT * FPS
        self.round_end_timer = 0
        self.round_end_message = ""
        self.card_options = []
        self.card_selected_idx = 1
        self.card_picker_num = None  # 1 atau 2 - pemain mana yang sedang memilih
        self.card_confirm_cooldown = 0
        self.pending_loser_num = None
        self.projectiles = []  # objek Projectile yang sedang melayang (tembakan gun)
        # Stats hanya berubah saat kartu diambil, jadi dikirim saat berubah
        # ditambah "heartbeat" pelan (bukan tiap tick) untuk menjaga paket
        # state tetap di bawah MTU ~1500-byte. Lihat to_dict().
        self._tick = 0
        self.stats_dirty = True

    def configure_rounds_to_win(self, n):
        """Mengatur berapa banyak kemenangan ronde yang dibutuhkan untuk
        memenangkan pertandingan. Dipilih host sebelum pertandingan
        dimulai; sengaja tidak disentuh oleh reset_match() supaya rematch
        (R) tetap memakai target yang sudah dipilih host."""
        try:
            n = int(n)
        except (TypeError, ValueError):
            return
        self.rounds_to_win = max(1, min(50, n))

    def configure_guaranteed_weapons(self, enabled):
        """Mengaktif/nonaktifkan opsi match "Guaranteed Weapons". Dipilih
        host sebelum pertandingan dimulai, sama seperti
        configure_rounds_to_win(); juga sengaja tidak disentuh oleh
        reset_match() supaya rematch (R) tetap memakai pengaturan yang
        sudah dipilih host."""
        self.guaranteed_weapons = bool(enabled)

    # -- siklus hidup ----------------------------------------------------
    def reset_match(self):
        self.p1 = Fighter(SCREEN_W * 0.25, 1)
        self.p2 = Fighter(SCREEN_W * 0.75, -1)
        self.p1_round_wins = 0
        self.p2_round_wins = 0
        self.used_unique_cards.clear()
        self.pending_loser_num = None
        self.stats_dirty = True  # fighter baru sama sekali - kirim stat (reset) mereka keluar
        self.start_new_round()

    def start_new_round(self):
        self.p1.reset_for_round()
        self.p2.reset_for_round()
        self.round_time_left = ROUND_TIME_LIMIT * FPS
        self.projectiles = []  # jangan bawa peluru nyasar melewati transisi KO/ronde
        self.stats_dirty = True  # Second Wind diperbarui tiap ronde - kirim keluar flag reset-nya
        self.state = self.STATE_FIGHT

    def _end_round(self, message):
        self.round_end_message = message
        self.round_end_timer = FPS * 2
        self.state = self.STATE_ROUND_END

    def _begin_card_select(self, loser_num):
        picker = self.p1 if loser_num == 1 else self.p2
        self.card_options = random_cards(self.used_unique_cards, 3,
                                          exclude_weapons=picker.weapon is not None,
                                          guarantee_weapon=self.guaranteed_weapons)
        self.card_selected_idx = 1
        self.card_picker_num = loser_num
        self.card_confirm_cooldown = FPS // 2
        self.state = self.STATE_CARD_SELECT

    def _check_round_outcome(self):
        p1, p2 = self.p1, self.p2
        if p1.hp <= 0 or p2.hp <= 0:
            if p1.hp <= 0 and p2.hp <= 0:
                self._end_round("DRAW!")
                return None
            elif p1.hp <= 0:
                self.p2_round_wins += 1
                self._end_round("PLAYER 2 WINS THE ROUND")
                return 1
            else:
                self.p1_round_wins += 1
                self._end_round("PLAYER 1 WINS THE ROUND")
                return 2
        return "none"  # sentinel: ronde belum berakhir

    def _resolve_timeout(self):
        p1, p2 = self.p1, self.p2
        if p1.hp > p2.hp:
            self.p1_round_wins += 1
            self._end_round("PLAYER 1 WINS THE ROUND (TIME)")
            return 2
        elif p2.hp > p1.hp:
            self.p2_round_wins += 1
            self._end_round("PLAYER 2 WINS THE ROUND (TIME)")
            return 1
        else:
            self._end_round("DRAW! (TIME)")
            return None

    # -- update per-tick ---------------------------------------------------
    def step(self, p1_intent, p2_intent, p1_card_nav=None, p2_card_nav=None):
        """Memajukan simulasi satu tick.
        p1_intent/p2_intent: dict intent gerakan/serangan (hanya dipakai saat FIGHT).
        p1_card_nav/p2_card_nav: "left"/"right"/"confirm"/None (hanya
        dipakai selama CARD_SELECT, dan hanya dihormati dari pemain mana
        pun yang sedang jadi card_picker_num saat ini).
        """
        self.p1.events = []
        self.p2.events = []
        self._tick += 1

        if self.state == self.STATE_FIGHT:
            self.p1.handle_input(p1_intent or {})
            self.p2.handle_input(p2_intent or {})
            self.p1.physics_update()
            self.p2.physics_update()

            self.p1.facing = 1 if self.p1.x <= self.p2.x else -1
            self.p2.facing = -1 if self.p2.x >= self.p1.x else 1

            process_melee_attacks(self.p1, self.p2)
            spawn_projectiles(self.p1, self.p2, self.projectiles)
            self.projectiles = update_projectiles(self.projectiles, self.p1, self.p2)

            if "second_wind" in self.p1.events or "second_wind" in self.p2.events:
                self.stats_dirty = True  # panel langsung berubah Ready -> Used

            self.round_time_left -= 1
            loser_num = self._check_round_outcome()
            if loser_num == "none" and self.round_time_left <= 0:
                loser_num = self._resolve_timeout()
            if loser_num != "none":
                self.pending_loser_num = loser_num

        elif self.state == self.STATE_ROUND_END:
            self.p1.physics_update()
            self.p2.physics_update()
            self.round_end_timer -= 1
            if self.round_end_timer <= 0:
                if self.p1_round_wins >= self.rounds_to_win or self.p2_round_wins >= self.rounds_to_win:
                    self.state = self.STATE_MATCH_END
                elif self.pending_loser_num is not None:
                    self._begin_card_select(self.pending_loser_num)
                else:
                    self.start_new_round()

        elif self.state == self.STATE_CARD_SELECT:
            if self.card_confirm_cooldown > 0:
                self.card_confirm_cooldown -= 1
            else:
                nav = p1_card_nav if self.card_picker_num == 1 else p2_card_nav
                if nav == "left":
                    self.card_selected_idx = max(0, self.card_selected_idx - 1)
                    self.card_confirm_cooldown = 10
                elif nav == "right":
                    self.card_selected_idx = min(len(self.card_options) - 1, self.card_selected_idx + 1)
                    self.card_confirm_cooldown = 10
                elif nav == "confirm":
                    chosen = self.card_options[self.card_selected_idx]
                    picker = self.p1 if self.card_picker_num == 1 else self.p2
                    picker.apply_card(chosen)
                    self.stats_dirty = True  # sebuah kartu mengubah stat fighter ini
                    if chosen["category"] == "unique":
                        self.used_unique_cards.add(chosen["name"])
                    self.pending_loser_num = None
                    self.start_new_round()

        elif self.state == self.STATE_MATCH_END:
            pass  # menunggu pemanggilan reset_match() eksplisit dari server

    # -- serialisasi ---------------------------------------------------------
    def to_dict(self):
        # Stats itu besar dan jarang berubah, jadi hanya disertakan saat
        # sebuah kartu baru saja diterapkan (stats_dirty) atau pada
        # heartbeat 2x/detik - cukup untuk self-heal kalau paket UDP yang
        # membawanya hilang, tanpa mendorong tiap paket melebihi MTU.
        # Client menyimpan (cache) set terakhir yang diterimanya (lihat
        # RenderFighter.apply_server_update).
        send_stats = self.stats_dirty or (self._tick % (FPS // 2) == 0)
        if self.stats_dirty:
            self.stats_dirty = False
        return {
            "type": "state",
            "state": self.state,
            "p1": self.p1.to_dict(include_stats=send_stats),
            "p2": self.p2.to_dict(include_stats=send_stats),
            "p1_round_wins": self.p1_round_wins,
            "p2_round_wins": self.p2_round_wins,
            "rounds_to_win": self.rounds_to_win,
            "guaranteed_weapons": self.guaranteed_weapons,
            "round_time_left": self.round_time_left,
            "round_end_message": self.round_end_message,
            "card_options": self.card_options,
            "card_selected_idx": self.card_selected_idx,
            "card_picker_num": self.card_picker_num,
            "projectiles": [p.to_dict() for p in self.projectiles],
        }

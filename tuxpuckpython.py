#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TuxPuck Python - un remake en un solo fichero del clasico TuxPuck (clon libre de
Shufflepuck Cafe), reescrito desde cero en Python 3 sobre pygame-ce.

    pip install pygame-ce
    python tuxpuckpython.py

Sin assets externos: tablero, personajes, cristal, marcador, sonidos y musica se
generan por codigo al arrancar.  Vease README.md para la especificacion completa.

Secciones del fichero (buscar "# ---- N." para saltar):
   0. Cabecera, imports, comprobacion de dependencias
   1. Constantes del tablero y de la fisica (valores del TuxPuck 0.8.2 original)
   2. Utilidades
   3. Perspective
   4. Profile (ajustes, mutadores, estadisticas, JSON atomico)
   5. Synth / AudioBank / Music
   6. Art (generadores de superficies procedurales)
   7. Entity / Pad / Puck / fisica del golpe
   8. Glass / Shards / FX
   9. Board
  10. Controllers (humano e IA)
  11. OPPONENTS (fichas de los seis rivales)
  12. UI (texto, menus, bocadillos, marcador, HUD)
  13. Scenes
  14. App
  15. selftest()
  16. main()

Licencia MIT (ver LICENSE).  Las constantes de fisica y la maquina de estados de
la IA se han tomado como referencia de comportamiento del codigo C de TuxPuck
(Jacob Kroon, 2001-2002, GPL-2).  No se incluye ningun asset del original.
"""
# ---- 0. Cabecera, imports, comprobacion de dependencias ----------------------
from __future__ import annotations

import argparse
from array import array
import json
import math
import os
import random
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict, fields
from typing import Callable, Optional

VERSION = "1.0.0"
APP_NAME = "tuxpuckpython"

if sys.version_info < (3, 10):
    sys.stderr.write("TuxPuck Python necesita Python 3.10 o superior.\n")
    sys.exit(1)

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
try:
    import pygame
except ImportError:  # pragma: no cover - mensaje para humanos
    sys.stderr.write(
        "Falta pygame-ce.  Instalelo con:\n\n    pip install pygame-ce\n\n"
        "(numpy es opcional y acelera la generacion de audio)\n")
    sys.exit(1)

try:
    import numpy as np
except ImportError:  # numpy es opcional
    np = None
if os.environ.get("TUXPUCK_NO_NUMPY"):   # para probar el camino sin numpy
    np = None


# ---- 1. Constantes del tablero y de la fisica --------------------------------
# Todos los valores con nombre en mayusculas y comentario "C:" vienen del codigo
# original (tuxpuck.h, entity.c, player.c, arcana.c, glass.c).

S = 1.5                                   # factor de escala 640x480 -> 960x720
LOGICAL_W, LOGICAL_H = 960, 720           # lienzo logico
BOARD_TOP = 175.0 * S                     # C: BOARD_TOP     (y de pantalla del fondo)
BOARD_BOTTOM = 465.0 * S                  # C: BOARD_BOTTOM  (y de pantalla del frente)
BOARD_LEFT = 203.0 * S                    # C: BOARD_LEFT
BOARD_RIGHT = 517.0 * S                   # C: BOARD_RIGHT
PX_PER_UNIT = 12.7 * S                    # C: "scale * e->x * 12.7"

PAD_W = 9.4                               # C: PAD_W
PAD_H = 0.02                              # C: PAD_H
PUCK_W = 2.8                              # C: PUCK_W
AREA_X1, AREA_X2 = -20.0, 20.0            # C: MoveArea {-20, 20, -42, 42}
AREA_Z1, AREA_Z2 = -42.0, 42.0
PAD1_Z1, PAD1_Z2 = -40.0, -20.8           # C: pad_create(1)
PAD2_Z1, PAD2_Z2 = 20.8, 40.0             # C: pad_create(2)
PAD_HOME_Z = 36.0                         # C: pad->z = +-36
GOAL_Z = 40.0                             # C: _get_scorer: puck_z > 40 / < -40
SERVE_Z = 21.0                            # C: entity_move_towards(puck, 0, +-21, 0.05)
SERVE_SPEED = 0.05

MAX_PUCK_SPEED = 0.20                     # C: MAX_PUCK_SPEED  (unidades/ms)
HIT_FRICTION = 0.60                       # C: HIT_FRICTION
THRESHOLD = 0.02                          # C: THRESHOLD
MIN_MOUSE_SPEED = 0.05                    # C: MIN_MOUSE_SPEED
MAX_MOUSE_SPEED = 0.50                    # C: MAX_MOUSE_SPEED
GLASS_GRAVITY = -0.001                    # C: GRAVITY (glass.c)

# Saque en espiral de Arcana (arcana.c)
SWIRL_RADIUS = 10.0                       # C: SWIRL_RADIUS
FLOAT_SPEED = 0.01                        # C: FLOAT_SPEED
COUNTER_ACC = 0.01                        # C: COUNTER_ACC
RELEASE_SPEED = 0.5                       # C: RELEASE_SPEED

FIXED_DT = 4                              # ms por paso de fisica (nuevo)
DEFAULT_POINTS = 15                       # C: "if points >= 15"
KEY_PAD_SPEED = 0.13                      # unidades/ms con teclado (nuevo)
POWER_HIT_MS = 120                        # golpe fuerte (nuevo)
POWER_HIT_FACTOR = 1.6
POWER_HIT_COOLDOWN = 400
SPIN_STRENGTH = 0.0013                    # fisica arcade (nuevo): curva por efecto
SPIN_DECAY = 0.998
KMH_PER_UNIT_MS = 500.0                   # "km/h de bar" para el velocimetro

OPPONENT_KEYS = ["bit", "tux", "arcana", "vera", "nullbyte", "daemon"]
POINT_CHOICES = [7, 11, 15, 21]


# ---- 2. Utilidades -----------------------------------------------------------
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(t):
    t = clamp(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def mix_color(c1, c2, t):
    t = clamp(t, 0.0, 1.0)
    return tuple(int(round(lerp(a, b, t))) for a, b in zip(c1[:3], c2[:3]))


def scale_color(c, k):
    return tuple(int(clamp(v * k, 0, 255)) for v in c[:3])


def with_alpha(c, a):
    return (c[0], c[1], c[2], int(clamp(a, 0, 255)))


def fmod(a, b):
    """C: _mod() de player.c (modulo con el signo del divisor)."""
    return a - math.floor(a / b) * b


def config_dir() -> str:
    env = os.environ.get("TUXPUCK_HOME")
    if env:
        return env
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME)


def timestamp_name(prefix="tuxpuck", ext="png"):
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}.{ext}"


class Timer:
    """Temporizador acumulativo en ms; `done` cuando supera la duracion."""

    def __init__(self, duration_ms: float, running: bool = True):
        self.duration = float(duration_ms)
        self.elapsed = 0.0
        self.running = running

    def start(self, duration_ms: Optional[float] = None):
        if duration_ms is not None:
            self.duration = float(duration_ms)
        self.elapsed = 0.0
        self.running = True

    def update(self, dt):
        if self.running:
            self.elapsed += dt
        return self.done

    @property
    def done(self):
        return self.elapsed >= self.duration

    @property
    def t(self):
        return clamp(self.elapsed / self.duration, 0.0, 1.0) if self.duration > 0 else 1.0


# ---- 3. Perspective ----------------------------------------------------------
class Perspective:
    """Proyeccion pseudo-3D del tablero (board.c: _coeff[0..3]).

    y(z) = c0 / (c1 + z)        scale(z) = c2 / (c3 + z)
    x_pantalla = LOGICAL_W/2 + scale(z) * x * PX_PER_UNIT
    """

    def __init__(self, top=BOARD_TOP, bottom=BOARD_BOTTOM, left=BOARD_LEFT, right=BOARD_RIGHT):
        self.c0 = 80 * (bottom * top) / (bottom - top)
        self.c1 = 40 * (bottom + top) / (bottom - top)
        ratio = left / right
        self.c2 = 80 * ratio / (1.0 - ratio)
        self.c3 = 40 * (1.0 + ratio) / (1.0 - ratio)
        self.cx = LOGICAL_W / 2.0

    def y(self, z):
        return self.c0 / (self.c1 + z)

    def scale(self, z):
        return self.c2 / (self.c3 + z)

    def x(self, x, z):
        return self.cx + self.scale(z) * x * PX_PER_UNIT

    def project(self, x, z):
        s = self.scale(z)
        return (self.cx + s * x * PX_PER_UNIT, self.c0 / (self.c1 + z), s)

    def z_from_y(self, y):
        return self.c0 / y - self.c1

    def unproject(self, sx, sy):
        z = self.z_from_y(max(sy, 1.0))
        s = self.scale(z)
        return ((sx - self.cx) / (s * PX_PER_UNIT), z)

    def quad(self, x1, x2, z1, z2):
        """Cuatro esquinas de pantalla de un rectangulo del tablero (cerca -> lejos)."""
        return [self.project(x1, z1)[:2], self.project(x2, z1)[:2],
                self.project(x2, z2)[:2], self.project(x1, z2)[:2]]


PERSP = Perspective()


# ---- 4. Profile --------------------------------------------------------------
@dataclass
class Settings:
    fullscreen: bool = False
    window_scale: float = 1.0
    sfx_volume: float = 0.8
    music_volume: float = 0.45
    mouse_speed: int = 5
    grab_mouse: bool = True
    power_hit: bool = True
    show_fps: bool = False
    muted: bool = False
    player_name: str = "Jugador"
    p1_input: str = "mouse"      # mouse | wasd | arrows | gamepad
    p2_input: str = "arrows"


@dataclass
class Mutators:
    speed: float = 1.0
    pucks: int = 1
    pad_scale: float = 1.0
    fog: bool = False
    arcade_physics: bool = False
    points: int = DEFAULT_POINTS


@dataclass
class Stats:
    matches: int = 0
    wins: int = 0
    losses: int = 0
    per_opponent: dict = field(default_factory=dict)
    streak: int = 0
    best_streak: int = 0
    longest_rally: int = 0
    fastest_shot: float = 0.0
    play_time_s: float = 0.0
    tournament_progress: int = 0      # indice del proximo rival
    tournament_best: int = 0          # mejor indice alcanzado (6 = completado)
    tournament_wins: int = 0
    nightmare_unlocked: bool = False
    nightmare_wins: int = 0

    def opponent(self, key):
        return self.per_opponent.setdefault(key, {"played": 0, "won": 0, "lost": 0})

    def record_match(self, key, won, rally, fastest, seconds):
        self.matches += 1
        rec = self.opponent(key)
        rec["played"] += 1
        if won:
            self.wins += 1
            rec["won"] += 1
            self.streak += 1
            self.best_streak = max(self.best_streak, self.streak)
        else:
            self.losses += 1
            rec["lost"] += 1
            self.streak = 0
        self.longest_rally = max(self.longest_rally, rally)
        self.fastest_shot = max(self.fastest_shot, fastest)
        self.play_time_s += seconds


def _dataclass_from_dict(cls, data):
    obj = cls()
    if not isinstance(data, dict):
        return obj
    for f in fields(cls):
        if f.name in data:
            val = data[f.name]
            cur = getattr(obj, f.name)
            if isinstance(cur, bool):
                val = bool(val)
            elif isinstance(cur, int) and not isinstance(cur, bool):
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    continue
            elif isinstance(cur, float):
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    continue
            elif isinstance(cur, str):
                val = str(val)
            elif isinstance(cur, dict):
                if not isinstance(val, dict):
                    continue
            setattr(obj, f.name, val)
    return obj


class Profile:
    """Ajustes + mutadores + estadisticas, en JSON atomico."""

    def __init__(self, path: Optional[str] = None):
        self.path = path if path is not None else os.path.join(config_dir(), "profile.json")
        self.settings = Settings()
        self.mutators = Mutators()
        self.stats = Stats()
        self.dirty = False

    def load(self) -> bool:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("perfil no es un objeto")
        except (OSError, ValueError):
            return False
        self.settings = _dataclass_from_dict(Settings, data.get("settings"))
        self.mutators = _dataclass_from_dict(Mutators, data.get("mutators"))
        self.stats = _dataclass_from_dict(Stats, data.get("stats"))
        self.settings.mouse_speed = int(clamp(self.settings.mouse_speed, 1, 10))
        self.settings.sfx_volume = clamp(self.settings.sfx_volume, 0.0, 1.0)
        self.settings.music_volume = clamp(self.settings.music_volume, 0.0, 1.0)
        if self.mutators.points not in POINT_CHOICES:
            self.mutators.points = DEFAULT_POINTS
        self.mutators.pucks = int(clamp(self.mutators.pucks, 1, 3))
        return True

    def to_dict(self):
        return {"version": VERSION, "settings": asdict(self.settings),
                "mutators": asdict(self.mutators), "stats": asdict(self.stats)}

    def save(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="profile-", suffix=".json",
                                       dir=os.path.dirname(self.path) or ".")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.dirty = False
            return True
        except OSError:
            return False

    def reset(self):
        self.settings = Settings()
        self.mutators = Mutators()
        self.stats = Stats()
        self.dirty = True

    def mouse_units_per_px(self):
        """C: human_set_speed()."""
        return MIN_MOUSE_SPEED + (MAX_MOUSE_SPEED - MIN_MOUSE_SPEED) * self.settings.mouse_speed / 10.0


# ---- 5. Synth / AudioBank / Music -------------------------------------------
SAMPLE_RATE = 22050


class Synth:
    """Sintetizador minimo.  Genera buffers de float en [-1, 1] (numpy si esta,
    array('f') si no) y los convierte en pygame.mixer.Sound de 16 bits mono."""

    def __init__(self, rate=SAMPLE_RATE, seed=0):
        self.rate = rate
        self.rng = random.Random(seed)
        self.np_rng = np.random.default_rng(seed) if np is not None else None

    # -- primitivas ------------------------------------------------------------
    def _n(self, dur):
        return max(1, int(dur * self.rate))

    def tone(self, freq, dur, wave="sine", amp=1.0, attack=0.004, decay=0.0,
             sweep=None, vibrato=None):
        """Oscilador con envolvente ataque/decaimiento exponencial.
        sweep: frecuencia final (barrido lineal).  vibrato: (hz, profundidad)."""
        n = self._n(dur)
        f_end = freq if sweep is None else sweep
        if np is not None:
            t = np.arange(n, dtype=np.float32) / self.rate
            f = np.linspace(freq, f_end, n, dtype=np.float32)
            if vibrato:
                f = f * (1.0 + vibrato[1] * np.sin(2 * np.pi * vibrato[0] * t))
            phase = 2 * np.pi * np.cumsum(f) / self.rate
            if wave == "sine":
                s = np.sin(phase)
            elif wave == "square":
                s = np.sign(np.sin(phase))
            elif wave == "saw":
                s = 2.0 * ((phase / (2 * np.pi)) % 1.0) - 1.0
            else:  # tri
                s = 2.0 * np.abs(2.0 * ((phase / (2 * np.pi)) % 1.0) - 1.0) - 1.0
            env = np.ones(n, dtype=np.float32)
            if attack > 0:
                env = np.minimum(1.0, t / attack)
            if decay > 0:
                env = env * np.exp(-t * decay)
            env = env * np.minimum(1.0, (n - np.arange(n)) / max(1, int(0.002 * self.rate)))
            return (s * env * amp).astype(np.float32)
        out = array("f", bytes(4 * n))
        phase = 0.0
        twopi = 2 * math.pi
        fade = max(1, int(0.002 * self.rate))
        for i in range(n):
            t = i / self.rate
            f = freq + (f_end - freq) * i / n
            if vibrato:
                f *= 1.0 + vibrato[1] * math.sin(twopi * vibrato[0] * t)
            phase += twopi * f / self.rate
            if wave == "sine":
                s = math.sin(phase)
            elif wave == "square":
                s = 1.0 if math.sin(phase) >= 0 else -1.0
            elif wave == "saw":
                s = 2.0 * ((phase / twopi) % 1.0) - 1.0
            else:
                s = 2.0 * abs(2.0 * ((phase / twopi) % 1.0) - 1.0) - 1.0
            env = min(1.0, t / attack) if attack > 0 else 1.0
            if decay > 0:
                env *= math.exp(-t * decay)
            rem = n - i
            if rem < fade:
                env *= rem / fade
            out[i] = s * env * amp
        return out

    def noise(self, dur, amp=1.0, attack=0.001, decay=0.0, lowpass=0.0):
        """Ruido blanco con envolvente; lowpass en (0,1) suaviza (ruido 'rosa')."""
        n = self._n(dur)
        if np is not None:
            s = self.np_rng.uniform(-1.0, 1.0, n).astype(np.float32)
            if lowpass > 0:
                a = lowpass
                y = np.empty(n, dtype=np.float32)
                acc = 0.0
                # filtro IIR de primer orden (bucle corto, n pequeno para sfx)
                for i in range(n):
                    acc = acc * a + s[i] * (1 - a)
                    y[i] = acc
                s = y * (1.0 / max(0.05, 1 - a))
            t = np.arange(n, dtype=np.float32) / self.rate
            env = np.minimum(1.0, t / attack) if attack > 0 else np.ones(n, dtype=np.float32)
            if decay > 0:
                env = env * np.exp(-t * decay)
            env = env * np.minimum(1.0, (n - np.arange(n)) / max(1, int(0.002 * self.rate)))
            return (s * env * amp).astype(np.float32)
        out = array("f", bytes(4 * n))
        acc = 0.0
        fade = max(1, int(0.002 * self.rate))
        rnd = self.rng.uniform
        for i in range(n):
            s = rnd(-1.0, 1.0)
            if lowpass > 0:
                acc = acc * lowpass + s * (1 - lowpass)
                s = acc / max(0.05, 1 - lowpass)
            t = i / self.rate
            env = min(1.0, t / attack) if attack > 0 else 1.0
            if decay > 0:
                env *= math.exp(-t * decay)
            rem = n - i
            if rem < fade:
                env *= rem / fade
            out[i] = s * env * amp
        return out

    def silence(self, dur):
        n = self._n(dur)
        return np.zeros(n, dtype=np.float32) if np is not None else array("f", bytes(4 * n))

    def mix(self, parts, length=None):
        """parts: lista de (buffer, offset_segundos).  Suma en un buffer comun."""
        total = 0
        for buf, off in parts:
            total = max(total, int(off * self.rate) + len(buf))
        if length is not None:
            total = max(total, self._n(length))
        if np is not None:
            out = np.zeros(total, dtype=np.float32)
            for buf, off in parts:
                o = int(off * self.rate)
                out[o:o + len(buf)] += buf
            return out
        out = array("f", bytes(4 * total))
        for buf, off in parts:
            o = int(off * self.rate)
            for i, v in enumerate(buf):
                out[o + i] += v
        return out

    def concat(self, bufs):
        if np is not None:
            return np.concatenate(bufs).astype(np.float32)
        out = array("f")
        for b in bufs:
            out.extend(b)
        return out

    def normalize(self, buf, peak=0.9):
        if np is not None:
            m = float(np.max(np.abs(buf))) if len(buf) else 0.0
            return buf * (peak / m) if m > 0 else buf
        m = max((abs(v) for v in buf), default=0.0)
        if m > 0:
            k = peak / m
            for i in range(len(buf)):
                buf[i] *= k
        return buf

    def to_sound(self, buf, volume=1.0):
        if np is not None:
            pcm = (np.clip(buf, -1.0, 1.0) * 32767.0 * volume).astype(np.int16)
            raw = pcm.tobytes()
        else:
            pcm = array("h", (int(clamp(v, -1.0, 1.0) * 32767.0 * volume) for v in buf))
            raw = pcm.tobytes()
        return pygame.mixer.Sound(buffer=raw)

    # -- recetas ---------------------------------------------------------------
    def build_sfx(self) -> dict:
        s = self
        sfx = {}
        sfx["nock"] = s.mix([(s.tone(180, 0.09, "sine", 0.9, 0.002, 45), 0),
                             (s.noise(0.02, 0.5, 0.001, 120), 0)])
        sfx["tick"] = s.mix([(s.tone(420, 0.04, "sine", 0.6, 0.001, 90), 0),
                             (s.noise(0.012, 0.35, 0.001, 200), 0)])
        tinkle = [(s.tone(f, 0.5, "sine", 0.18, 0.002, 7.0), 0.03 * i)
                  for i, f in enumerate([2093, 2637, 3136, 3951, 4699, 5588])]
        sfx["crash"] = s.mix([(s.noise(0.7, 0.9, 0.001, 6.0), 0)] + tinkle)
        sfx["whoosh"] = s.noise(0.22, 0.5, 0.06, 12.0, lowpass=0.85)
        sfx["swirl"] = s.tone(220, 1.2, "sine", 0.35, 0.3, 1.5, sweep=1320, vibrato=(9, 0.02))
        sfx["cheer"] = s.mix([(s.noise(0.9, 0.7, 0.25, 3.0, lowpass=0.6), 0),
                              (s.tone(523, 0.35, "tri", 0.2, 0.05, 6.0), 0.1),
                              (s.tone(659, 0.35, "tri", 0.2, 0.05, 6.0), 0.2)])
        sfx["boo"] = s.tone(110, 0.7, "saw", 0.45, 0.05, 2.5, sweep=90, vibrato=(5, 0.04))
        sfx["menu_move"] = s.tone(880, 0.04, "sine", 0.5, 0.002, 60)
        sfx["menu_select"] = s.mix([(s.tone(660, 0.08, "square", 0.25, 0.002, 30), 0),
                                    (s.tone(990, 0.12, "square", 0.25, 0.002, 25), 0.06)])
        sfx["point"] = s.mix([(s.tone(660, 0.12, "tri", 0.5, 0.005, 12), 0),
                              (s.tone(990, 0.25, "tri", 0.5, 0.005, 9), 0.12)])
        sfx["lose_point"] = s.mix([(s.tone(330, 0.15, "tri", 0.45, 0.005, 10), 0),
                                   (s.tone(247, 0.3, "tri", 0.45, 0.005, 8), 0.14)])
        fan = [(s.tone(f, 0.22, "square", 0.22, 0.005, 8), 0.11 * i)
               for i, f in enumerate([523, 659, 784, 1047])]
        fan.append((s.tone(1047, 0.7, "tri", 0.4, 0.01, 3), 0.44))
        sfx["fanfare"] = s.mix(fan)
        sfx["lose"] = s.mix([(s.tone(f, 0.3, "saw", 0.25, 0.01, 6), 0.16 * i)
                             for i, f in enumerate([440, 415, 392, 370, 349])])
        sfx["serve"] = s.tone(440, 0.15, "sine", 0.4, 0.01, 10, sweep=880)
        sfx["power"] = s.mix([(s.tone(90, 0.12, "sine", 0.8, 0.002, 25, sweep=50), 0),
                              (s.noise(0.05, 0.4, 0.001, 70), 0)])
        sfx["glitch"] = s.mix([(s.tone(1200 + 300 * i, 0.03, "square", 0.25, 0.001, 50), 0.035 * i)
                               for i in range(4)])
        sfx["pause"] = s.tone(500, 0.1, "tri", 0.4, 0.005, 20, sweep=250)
        return sfx

    def build_music(self, bars=8):
        """Secuenciador de 16avos a 96 BPM: bajo cuadrado, arpegio triangular,
        charles de ruido y bombo.  Cada semilla, un tema distinto."""
        step = 60.0 / 96.0 / 4.0
        roots = [110.0, 87.31, 130.81, 98.0]          # A2 F2 C3 G2
        chords = [[0, 3, 7], [0, 4, 7], [0, 4, 7], [0, 4, 7]]
        rng = self.rng
        parts = []
        kick = self.tone(120, 0.18, "sine", 0.9, 0.001, 18, sweep=40)
        hat = self.noise(0.04, 0.22, 0.001, 90)
        hat_soft = self.noise(0.025, 0.1, 0.001, 140)
        bass_pat = [1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 1, 0]
        if rng.random() < 0.5:
            bass_pat = [1, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1]
        for bar in range(bars):
            chord_i = bar % 4
            root = roots[chord_i]
            chord = chords[chord_i]
            arp = [root * 2 * 2 ** (c / 12.0) for c in chord] + [root * 4 * 2 ** (c / 12.0) for c in chord[:2]]
            if rng.random() < 0.5:
                arp = arp[::-1]
            base = bar * 16 * step
            for st in range(16):
                t0 = base + st * step
                if bass_pat[st]:
                    note = root if st % 8 != 6 else root * 2 ** (7 / 12.0)
                    parts.append((self.tone(note, step * 1.6, "square", 0.22, 0.004, 9), t0))
                parts.append((self.tone(arp[st % len(arp)], step * 0.9, "tri", 0.16, 0.003, 14), t0))
                if st % 4 == 2:
                    parts.append((hat, t0))
                elif st % 2 == 1:
                    parts.append((hat_soft, t0))
                if st in (0, 8) or (st == 10 and bar % 2 == 1):
                    parts.append((kick, t0))
        buf = self.mix(parts, length=bars * 16 * step)
        return self.normalize(buf, 0.8)


class AudioBank:
    """Catalogo de sonidos + musica.  Si el mixer no esta disponible, no hace nada."""

    def __init__(self, enabled=True, seed=0, music=True):
        self.enabled = False
        self.sfx = {}
        self.music_sound = None
        self.music_channel = None
        self.sfx_volume = 0.8
        self.music_volume = 0.45
        self.muted = False
        self.want_music = music
        if not enabled:
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(SAMPLE_RATE, -16, 1, 512)
            pygame.mixer.set_num_channels(24)
            self.enabled = True
        except pygame.error:
            self.enabled = False
            return
        synth = Synth(SAMPLE_RATE, seed)
        try:
            self.sfx = {k: synth.to_sound(v) for k, v in synth.build_sfx().items()}
            if music:
                self.music_sound = synth.to_sound(synth.build_music(8))
        except (pygame.error, ValueError, MemoryError):
            self.enabled = False

    def configure(self, settings: Settings):
        self.sfx_volume = settings.sfx_volume
        self.music_volume = settings.music_volume
        self.muted = settings.muted
        self._apply_music_volume()

    def play(self, name, volume=1.0):
        if not self.enabled or self.muted:
            return
        snd = self.sfx.get(name)
        if snd is not None:
            snd.set_volume(clamp(volume * self.sfx_volume, 0.0, 1.0))
            snd.play()

    def start_music(self):
        if not self.enabled or self.music_sound is None or not self.want_music:
            return
        if self.music_channel is None or not self.music_channel.get_busy():
            self.music_channel = self.music_sound.play(loops=-1, fade_ms=800)
        self._apply_music_volume()

    def stop_music(self, fade_ms=600):
        if self.music_channel is not None:
            self.music_channel.fadeout(fade_ms)
            self.music_channel = None

    def _apply_music_volume(self):
        if self.music_channel is not None:
            self.music_channel.set_volume(0.0 if self.muted else self.music_volume)

    def set_muted(self, muted):
        self.muted = muted
        self._apply_music_volume()


# ---- 6. Art ------------------------------------------------------------------
PORTRAIT_W, PORTRAIT_H = int(144 * S), int(160 * S)   # 216 x 240 (tux.png era 144x160)
_FONT_CACHE: dict = {}
_GLOW_CACHE: dict = {}


def get_font(size: int, bold=False) -> pygame.font.Font:
    key = (size, bold)
    f = _FONT_CACHE.get(key)
    if f is None:
        f = pygame.font.Font(None, size)
        f.set_bold(bold)
        _FONT_CACHE[key] = f
    return f


def radial_glow(radius: int, color, strength=1.0) -> pygame.Surface:
    """Halo aditivo: superficie RGB con rampa radial de intensidad, para blitear
    con BLEND_RGB_ADD (cacheada por radio/color/intensidad)."""
    radius = max(2, int(radius))
    key = ("add", radius, tuple(color[:3]), round(strength, 2))
    s = _GLOW_CACHE.get(key)
    if s is None:
        s = pygame.Surface((radius * 2, radius * 2))
        s.fill((0, 0, 0))
        steps = max(4, min(radius, 24))
        for i in range(steps, 0, -1):
            r = int(radius * i / steps)
            k = strength * (1 - i / steps) ** 2
            pygame.draw.circle(s, scale_color(color, k), (radius, radius), r)
        _GLOW_CACHE[key] = s
    return s


def soft_disc(radius: int, color, alpha=1.0) -> pygame.Surface:
    """Disco con alfa decreciente hacia el borde, para blits normales (estelas,
    brillos sobre superficies con transparencia)."""
    radius = max(2, int(radius))
    key = ("alpha", radius, tuple(color[:3]), round(alpha, 2))
    s = _GLOW_CACHE.get(key)
    if s is None:
        s = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        steps = max(4, min(radius, 24))
        for i in range(steps, 0, -1):
            r = int(radius * i / steps)
            a = int(255 * alpha * (1 - i / steps) ** 2)
            pygame.draw.circle(s, with_alpha(color, a), (radius, radius), r)
        _GLOW_CACHE[key] = s
    return s


def vgradient(size, top, bottom) -> pygame.Surface:
    w, h = size
    s = pygame.Surface((w, h))
    for y in range(h):
        pygame.draw.line(s, mix_color(top, bottom, y / max(1, h - 1)), (0, y), (w, y))
    return s


def neon_text(text, size, color, glow=None, bold=True) -> pygame.Surface:
    font = get_font(size, bold)
    core = font.render(text, True, (255, 255, 255))
    tint = font.render(text, True, color)
    glow = glow or color
    pad = max(6, size // 6)
    out = pygame.Surface((core.get_width() + pad * 2, core.get_height() + pad * 2), pygame.SRCALPHA)
    halo = font.render(text, True, glow)
    for radius, alpha in ((pad, 28), (pad // 2, 60), (2, 110)):
        halo.set_alpha(alpha)
        for ang in range(0, 360, 45):
            dx = math.cos(math.radians(ang)) * radius
            dy = math.sin(math.radians(ang)) * radius
            out.blit(halo, (pad + dx, pad + dy))
    tint.set_alpha(230)
    out.blit(tint, (pad, pad))
    core.set_alpha(120)
    out.blit(core, (pad, pad))
    return out


def shadow_text(surf, text, pos, size, color=(255, 255, 255), align="left", bold=False,
                shadow=(0, 0, 0), alpha=255):
    font = get_font(size, bold)
    img = font.render(text, True, color)
    x, y = pos
    if align == "center":
        x -= img.get_width() // 2
    elif align == "right":
        x -= img.get_width()
    if shadow is not None:
        sh = font.render(text, True, shadow)
        sh.set_alpha(alpha * 0.6)
        surf.blit(sh, (x + 2, y + 2))
    if alpha < 255:
        img.set_alpha(alpha)
    surf.blit(img, (x, y))
    return pygame.Rect(x, y, img.get_width(), img.get_height())


class Art:
    """Generadores de superficies.  Todo vectorial, nada cargado de disco."""

    WOOD = (152, 101, 54)
    WOOD_DARK = (118, 76, 38)
    RAIL = (64, 38, 22)
    RAIL_LIGHT = (120, 80, 48)
    BRASS = (214, 170, 70)
    PUCK_TOP = (214, 48, 40)
    PUCK_SIDE = (110, 18, 16)
    PAD_COLORS = {1: (90, 170, 255), 2: (255, 120, 90)}

    # -- fondo: bar + mesa ----------------------------------------------------
    @classmethod
    def make_background(cls, persp: Perspective, rng: random.Random) -> pygame.Surface:
        bg = vgradient((LOGICAL_W, LOGICAL_H), (12, 9, 18), (34, 20, 16))
        cls._draw_wall(bg, rng)
        cls._draw_window(bg, rng)
        cls._draw_shelf(bg, rng)
        cls._draw_couch(bg)
        cls._draw_side_table(bg)
        cls._draw_table(bg, persp, rng)
        sign = neon_text("TUXPUCK CAFE", 54, (255, 90, 160), (255, 40, 120))
        bg.blit(sign, (22, 18))
        sub = neon_text("python edition", 22, (120, 230, 255), (40, 180, 255))
        bg.blit(sub, (sign.get_width() - sub.get_width() + 10, 66))
        return bg

    @staticmethod
    def _draw_wall(bg, rng):
        # tablones horizontales oscuros con vineta
        for y in range(0, 300, 34):
            shade = rng.randint(-6, 6)
            pygame.draw.rect(bg, (38 + shade, 24 + shade // 2, 18), (0, y, LOGICAL_W, 33))
            pygame.draw.line(bg, (22, 14, 10), (0, y + 33), (LOGICAL_W, y + 33), 2)
        vign = pygame.Surface((LOGICAL_W, 300), pygame.SRCALPHA)
        for i in range(12):
            a = int(14 * i)
            pygame.draw.rect(vign, (0, 0, 0, a), (0, 300 - 25 * (i + 1), LOGICAL_W, 25))
        bg.blit(vign, (0, 0))
        # luz calida de la lampara sobre la mesa
        glow = radial_glow(260, (255, 180, 90), 0.35)
        bg.blit(glow, (LOGICAL_W // 2 - 260, 120), special_flags=pygame.BLEND_RGB_ADD)

    @staticmethod
    def _draw_window(bg, rng):
        frame = pygame.Rect(28, 104, 212, 146)
        pygame.draw.rect(bg, (60, 38, 24), frame.inflate(14, 14), border_radius=4)
        pygame.draw.rect(bg, (8, 10, 28), frame)
        sky = vgradient((frame.w, frame.h), (10, 14, 48), (40, 20, 60))
        bg.blit(sky, frame.topleft)
        # skyline
        x = frame.left
        while x < frame.right:
            w = rng.randint(10, 26)
            h = rng.randint(30, 95)
            pygame.draw.rect(bg, (14, 12, 30), (x, frame.bottom - h, w, h))
            for wy in range(frame.bottom - h + 4, frame.bottom - 4, 7):
                for wx in range(x + 2, x + w - 3, 6):
                    if rng.random() < 0.45:
                        pygame.draw.rect(bg, rng.choice([(255, 220, 120), (200, 240, 255), (255, 160, 90)]),
                                         (wx, wy, 3, 4))
            x += w + rng.randint(2, 6)
        # luces difusas de la ciudad
        for _ in range(16):
            c = rng.choice([(255, 90, 140), (90, 200, 255), (255, 200, 90), (120, 255, 160)])
            r = rng.randint(6, 18)
            g = radial_glow(r, c, 0.8)
            bg.blit(g, (rng.randint(frame.left, frame.right - r * 2), rng.randint(frame.top, frame.bottom - 40)),
                    special_flags=pygame.BLEND_RGB_ADD)
        # cruceta
        pygame.draw.line(bg, (60, 38, 24), (frame.centerx, frame.top), (frame.centerx, frame.bottom), 5)
        pygame.draw.line(bg, (60, 38, 24), (frame.left, frame.centery), (frame.right, frame.centery), 5)
        # reflejo del neon en el cristal
        refl = pygame.Surface(frame.size, pygame.SRCALPHA)
        pygame.draw.polygon(refl, (255, 255, 255, 18), [(0, 0), (70, 0), (0, 90)])
        bg.blit(refl, frame.topleft)

    @staticmethod
    def _draw_shelf(bg, rng):
        shelf = pygame.Rect(700, 196, 246, 10)
        pygame.draw.rect(bg, (70, 44, 26), shelf)
        pygame.draw.rect(bg, (40, 24, 14), (shelf.x, shelf.bottom, shelf.w, 4))
        x = shelf.x + 12
        while x < shelf.right - 24:
            w = rng.randint(14, 22)
            h = rng.randint(44, 70)
            col = rng.choice([(40, 120, 70), (150, 60, 30), (60, 70, 140), (170, 130, 40), (90, 30, 60)])
            body = pygame.Rect(x, shelf.top - h, w, h)
            pygame.draw.rect(bg, col, body, border_radius=4)
            pygame.draw.rect(bg, scale_color(col, 0.7), (x + w // 3, shelf.top - h - 14, w // 3, 16), border_radius=2)
            pygame.draw.line(bg, mix_color(col, (255, 255, 255), 0.5), (x + 3, shelf.top - h + 8), (x + 3, shelf.top - 6), 2)
            # etiqueta
            pygame.draw.rect(bg, (230, 220, 190), (x + 2, shelf.top - h // 2 - 6, w - 4, 12))
            x += w + rng.randint(8, 16)

    @staticmethod
    def _draw_couch(bg):
        leather = (74, 30, 92)
        light = (120, 60, 140)
        back = [(715, 300), (950, 282), (960, 420), (720, 440)]
        pygame.draw.polygon(bg, leather, back)
        pygame.draw.polygon(bg, light, back, 3)
        seat = [(700, 430), (960, 410), (960, 520), (690, 520)]
        pygame.draw.polygon(bg, scale_color(leather, 0.8), seat)
        pygame.draw.polygon(bg, light, seat, 2)
        for i in range(3):
            cx = 760 + i * 70
            pygame.draw.ellipse(bg, light, (cx - 24, 330, 48, 70), 2)
            pygame.draw.ellipse(bg, mix_color(light, (255, 255, 255), 0.4), (cx - 10, 338, 14, 20))
        arm = [(690, 440), (730, 430), (730, 520), (680, 525)]
        pygame.draw.polygon(bg, scale_color(leather, 0.65), arm)
        neon = radial_glow(120, (120, 60, 255), 0.5)
        bg.blit(neon, (780, 250), special_flags=pygame.BLEND_RGB_ADD)

    @staticmethod
    def _draw_side_table(bg):
        top = [(0, 318), (225, 300), (252, 352), (0, 380)]
        pygame.draw.polygon(bg, (92, 58, 30), top)
        pygame.draw.polygon(bg, (140, 92, 48), top, 2)
        pygame.draw.polygon(bg, (60, 36, 18), [(0, 380), (252, 352), (252, 372), (0, 400)])
        # botella
        pygame.draw.rect(bg, (40, 110, 70), (36, 236, 26, 88), border_radius=5)
        pygame.draw.rect(bg, (34, 90, 60), (44, 212, 10, 28))
        pygame.draw.rect(bg, (200, 170, 60), (42, 208, 14, 7))
        pygame.draw.rect(bg, (230, 215, 170), (38, 262, 22, 24))
        pygame.draw.line(bg, (160, 230, 190), (41, 244), (41, 316), 2)
        # vaso
        pygame.draw.polygon(bg, (170, 200, 220), [(90, 280), (116, 280), (112, 326), (94, 326)])
        pygame.draw.polygon(bg, (230, 245, 255), [(90, 280), (116, 280), (112, 326), (94, 326)], 1)
        pygame.draw.rect(bg, (210, 150, 60), (95, 300, 16, 24))
        # cenicero
        pygame.draw.ellipse(bg, (150, 150, 160), (120, 328, 56, 20))
        pygame.draw.ellipse(bg, (90, 90, 100), (128, 331, 40, 12))
        pygame.draw.line(bg, (230, 230, 230), (150, 326), (168, 318), 3)

    @classmethod
    def _draw_table(cls, bg, persp: Perspective, rng):
        # sombra bajo la mesa
        sh = pygame.Surface((LOGICAL_W, 60), pygame.SRCALPHA)
        for i in range(60):
            pygame.draw.line(sh, (0, 0, 0, int(160 * (1 - i / 60))), (0, i), (LOGICAL_W, i))
        bg.blit(sh, (0, int(persp.y(AREA_Z1)) - 6))
        # superficie de madera
        pygame.draw.polygon(bg, cls.WOOD, persp.quad(AREA_X1, AREA_X2, AREA_Z1, AREA_Z2))
        planks = 10
        pw = (AREA_X2 - AREA_X1) / planks
        grain = pygame.Surface((LOGICAL_W, LOGICAL_H), pygame.SRCALPHA)
        for i in range(planks):
            x1 = AREA_X1 + i * pw
            shade = rng.randint(-14, 14)
            col = tuple(clamp(c + shade, 0, 255) for c in cls.WOOD)
            pygame.draw.polygon(bg, col, persp.quad(x1, x1 + pw, AREA_Z1, AREA_Z2))
            for _ in range(5):
                gx = x1 + rng.uniform(0.2, pw - 0.2)
                a = rng.randint(25, 70)
                wobble = rng.uniform(-0.3, 0.3)
                pts = [persp.project(gx + wobble * math.sin(k / 3.0), AREA_Z1 + k * (AREA_Z2 - AREA_Z1) / 12)[:2]
                       for k in range(13)]
                pygame.draw.lines(grain, (40, 20, 8, a), False, pts, 1)
            pygame.draw.line(grain, (30, 16, 6, 110), persp.project(x1, AREA_Z1)[:2], persp.project(x1, AREA_Z2)[:2], 1)
        bg.blit(grain, (0, 0))
        # brillo de la lampara sobre la madera
        gl = pygame.Surface((LOGICAL_W, LOGICAL_H), pygame.SRCALPHA)
        pygame.draw.polygon(gl, (255, 230, 180, 26), persp.quad(-9, 9, -30, 25))
        pygame.draw.polygon(gl, (255, 230, 180, 18), persp.quad(-15, 15, -38, 36))
        bg.blit(gl, (0, 0))
        # lineas transversales (saque, centro) y lineas de gol
        for z in (-SERVE_Z, 0.0, SERVE_Z):
            pygame.draw.polygon(bg, (30, 18, 10), persp.quad(AREA_X1, AREA_X2, z - 0.35, z + 0.35))
        for z in (-GOAL_Z, GOAL_Z):
            pygame.draw.polygon(bg, (200, 160, 90), persp.quad(AREA_X1, AREA_X2, z - 0.2, z + 0.2))
        # barandillas laterales
        for sign in (-1, 1):
            inner = AREA_X1 if sign < 0 else AREA_X2
            outer = inner + sign * 3.2
            top_face = persp.quad(min(inner, outer), max(inner, outer), AREA_Z1 - 1.0, AREA_Z2 + 1.0)
            pygame.draw.polygon(bg, cls.RAIL_LIGHT, top_face)
            side = [persp.project(outer, AREA_Z1 - 1.0)[:2], persp.project(outer, AREA_Z2 + 1.0)[:2]]
            side_poly = [side[0], side[1], (side[1][0], side[1][1] + 6), (side[0][0], side[0][1] + 22)]
            pygame.draw.polygon(bg, cls.RAIL, side_poly)
            pygame.draw.line(bg, (200, 150, 90), persp.project(inner, AREA_Z1 - 1.0)[:2],
                             persp.project(inner, AREA_Z2 + 1.0)[:2], 2)
        # frente de la mesa
        near_y = persp.y(AREA_Z1)
        front = [persp.project(AREA_X1 - 3.2, AREA_Z1 - 1.0)[:2], persp.project(AREA_X2 + 3.2, AREA_Z1 - 1.0)[:2]]
        pygame.draw.polygon(bg, cls.RAIL, [front[0], front[1], (front[1][0], near_y + 40), (front[0][0], near_y + 40)])
        pygame.draw.line(bg, cls.RAIL_LIGHT, front[0], front[1], 3)
        # bolas de laton en las esquinas
        for (x, z) in ((AREA_X1 - 1.6, AREA_Z1 - 1.0), (AREA_X2 + 1.6, AREA_Z1 - 1.0),
                       (AREA_X1 - 1.6, AREA_Z2 + 1.0), (AREA_X2 + 1.6, AREA_Z2 + 1.0)):
            sx, sy, sc = persp.project(x, z)
            r = int(15 * sc) + 4
            pygame.draw.rect(bg, (90, 60, 30), (sx - r // 2, sy - r * 2, r, r * 2))
            pygame.draw.circle(bg, scale_color(cls.BRASS, 0.55), (int(sx), int(sy - r * 2)), r)
            pygame.draw.circle(bg, cls.BRASS, (int(sx - r * 0.2), int(sy - r * 2.2)), int(r * 0.75))
            pygame.draw.circle(bg, (255, 240, 200), (int(sx - r * 0.35), int(sy - r * 2.4)), max(2, int(r * 0.25)))
        # cristales de las porterias (tenues)
        cls.draw_glass_pane(bg, persp, 0.0, GOAL_Z, 34)
        cls.draw_glass_pane(bg, persp, 0.0, -GOAL_Z, 9)
        return bg

    @staticmethod
    def draw_glass_pane(surf, persp: Perspective, x, z, alpha, crack=None):
        sx1, sy, sc = persp.project(AREA_X1, z)
        sx2 = persp.x(AREA_X2, z)
        h = int(134 * S * sc * 0.55)
        pane = pygame.Surface((int(sx2 - sx1), h), pygame.SRCALPHA)
        pane.fill((170, 215, 255, alpha))
        pygame.draw.rect(pane, (230, 245, 255, min(255, alpha * 3)), pane.get_rect(), 2)
        pygame.draw.line(pane, (255, 255, 255, min(255, alpha * 2)), (pane.get_width() * 0.15, h - 4),
                         (pane.get_width() * 0.45, 4), 2)
        if crack is not None:
            cx = int((crack - AREA_X1) / (AREA_X2 - AREA_X1) * pane.get_width())
            for ang in range(0, 360, 36):
                ex = cx + math.cos(math.radians(ang)) * h
                ey = h * 0.5 + math.sin(math.radians(ang)) * h
                pygame.draw.line(pane, (255, 255, 255, 170), (cx, h * 0.5), (ex, ey), 1)
        surf.blit(pane, (int(sx1), int(sy - h)))

    @staticmethod
    def make_fog(persp: Perspective) -> pygame.Surface:
        fog = pygame.Surface((LOGICAL_W, LOGICAL_H), pygame.SRCALPHA)
        y_far = int(persp.y(AREA_Z2 + 1))
        y_near = int(persp.y(0.0))
        for y in range(y_far, y_near):
            t = (y_near - y) / max(1, (y_near - y_far))
            a = int(215 * smoothstep(t))
            z = persp.z_from_y(y)
            half = 23.5 * persp.scale(z) * PX_PER_UNIT
            pygame.draw.line(fog, (6, 4, 10, a), (persp.cx - half, y), (persp.cx + half, y))
        return fog

    # -- disco y palas ---------------------------------------------------------
    @classmethod
    def draw_puck(cls, surf, persp: Perspective, x, z, alpha=255, speed=0.0, color=None,
                  trail=None, offset=(0, 0)):
        color = color or cls.PUCK_TOP
        side = scale_color(color, 0.5)
        if trail:
            n = len(trail)
            for i, (tx, tz) in enumerate(trail):
                sx, sy, sc = persp.project(tx, tz)
                rx = PUCK_W / 2 * PX_PER_UNIT * sc
                a = int(alpha * 0.35 * (i + 1) / n)
                g = soft_disc(int(rx * 1.1) + 1, color, a / 255.0)
                surf.blit(g, (sx - g.get_width() / 2 + offset[0], sy - rx * 0.55 - g.get_height() / 2 + offset[1]))
        sx, sy, sc = persp.project(x, z)
        sx += offset[0]
        sy += offset[1]
        rx = PUCK_W / 2 * PX_PER_UNIT * sc
        ry = rx * 0.62
        thick = max(2, rx * 0.35)
        cy = sy - ry * 0.5
        shadow = pygame.Surface((int(rx * 2.6), int(ry * 2.2)), pygame.SRCALPHA)
        pygame.draw.ellipse(shadow, (0, 0, 0, int(alpha * 0.45)), shadow.get_rect())
        surf.blit(shadow, (sx - rx * 1.1, cy - ry * 0.8 + thick))
        if speed > 0.11:
            k = clamp((speed - 0.11) / 0.1, 0, 1)
            g = radial_glow(int(rx * 2.2), (255, 120, 60), 0.6 * k)
            surf.blit(g, (sx - g.get_width() / 2, cy - g.get_height() / 2), special_flags=pygame.BLEND_RGB_ADD)
        body = pygame.Surface((int(rx * 2) + 2, int(ry * 2 + thick) + 2), pygame.SRCALPHA)
        pygame.draw.ellipse(body, with_alpha(side, alpha), (1, 1 + thick, rx * 2, ry * 2))
        pygame.draw.rect(body, with_alpha(side, alpha), (1, 1 + ry, rx * 2, thick))
        pygame.draw.ellipse(body, with_alpha(color, alpha), (1, 1, rx * 2, ry * 2))
        pygame.draw.ellipse(body, with_alpha(mix_color(color, (255, 255, 255), 0.45), alpha),
                            (1 + rx * 0.35, 1 + ry * 0.3, rx * 1.3, ry * 1.1), 2)
        pygame.draw.ellipse(body, with_alpha(mix_color(color, (255, 255, 255), 0.7), int(alpha * 0.8)),
                            (1 + rx * 0.5, 1 + ry * 0.35, rx * 0.5, ry * 0.35))
        surf.blit(body, (sx - rx - 1, cy - ry - 1))

    @classmethod
    def draw_pad(cls, surf, persp: Perspective, x, z, width_units, color, alpha=128, flash=0.0,
                 offset=(0, 0)):
        sx, sy, sc = persp.project(x, z)
        sx += offset[0]
        sy += offset[1]
        w = max(6, int(width_units * PX_PER_UNIT * sc))
        h = max(4, int(w * 50 / 120))
        pad = pygame.Surface((w + 4, h + 4), pygame.SRCALPHA)
        c = mix_color(color, (255, 255, 255), clamp(flash, 0, 1) * 0.8)
        pygame.draw.ellipse(pad, with_alpha(c, alpha), (2, 2, w, h))
        pygame.draw.ellipse(pad, with_alpha(mix_color(c, (255, 255, 255), 0.5), min(255, alpha + 70)), (2, 2, w, h), 2)
        pygame.draw.ellipse(pad, with_alpha((255, 255, 255), int(alpha * 0.6)), (2 + w * 0.2, 2 + h * 0.18, w * 0.35, h * 0.3))
        surf.blit(pad, (sx - w / 2 - 2, sy - h - 2))   # C: rect.y = y - h * y_scale(1.0)
        if flash > 0.05:
            g = radial_glow(int(w * 0.7), c, 0.5 * flash)
            surf.blit(g, (sx - g.get_width() / 2, sy - h / 2 - g.get_height() / 2), special_flags=pygame.BLEND_RGB_ADD)

    # -- retratos ---------------------------------------------------------------
    @staticmethod
    def _eyes(surf, cx, cy, spacing, r, eye, mood, iris=(30, 30, 40), white=(250, 250, 250), pupil_k=0.45):
        ex, ey = eye
        for sgn in (-1, 1):
            x = cx + sgn * spacing
            pygame.draw.circle(surf, white, (x, cy), r)
            px = x + ex * r * 0.45
            py = cy + ey * r * 0.4
            pygame.draw.circle(surf, iris, (int(px), int(py)), int(r * pupil_k))
            pygame.draw.circle(surf, (255, 255, 255), (int(px - r * 0.15), int(py - r * 0.18)), max(1, int(r * 0.14)))
            if mood in ("angry", "lose"):
                a = r * 1.1
                pygame.draw.polygon(surf, (0, 0, 0, 0), [(x - a, cy - a), (x + a, cy - a), (x + a, cy - a)])
                pygame.draw.line(surf, (40, 30, 30), (x - r, cy - r * (0.9 if sgn < 0 else 0.3)),
                                 (x + r, cy - r * (0.3 if sgn < 0 else 0.9)), max(2, int(r * 0.22)))
            elif mood in ("happy", "win"):
                pygame.draw.arc(surf, (40, 30, 30), (x - r, cy - r * 1.4, r * 2, r * 1.4), 0.3, math.pi - 0.3, max(2, int(r * 0.2)))

    @staticmethod
    def _mouth(surf, cx, cy, w, mood, color=(60, 20, 30), t=0.0):
        if mood in ("happy", "win"):
            pygame.draw.arc(surf, color, (cx - w / 2, cy - w * 0.35, w, w * 0.7), math.pi, 2 * math.pi, 4)
            if mood == "win":
                pygame.draw.ellipse(surf, (120, 30, 40), (cx - w * 0.3, cy, w * 0.6, w * 0.3 + 4 * abs(math.sin(t / 150))))
        elif mood in ("angry", "lose"):
            pygame.draw.arc(surf, color, (cx - w / 2, cy, w, w * 0.6), 0.2, math.pi - 0.2, 4)
        else:
            pygame.draw.line(surf, color, (cx - w * 0.4, cy + 2), (cx + w * 0.4, cy + 2), 4)

    @classmethod
    def portrait_bit(cls, surf, t, mood, eye, rng):
        cx, cy = PORTRAIT_W // 2, PORTRAIT_H // 2 + 20
        bob = math.sin(t / 400.0) * 4
        pygame.draw.ellipse(surf, (40, 40, 48), (cx - 90, cy + 60, 180, 40))
        body = pygame.Rect(cx - 80, cy - 60 + bob, 160, 130)
        pygame.draw.ellipse(surf, (150, 155, 165), body)
        pygame.draw.ellipse(surf, (200, 205, 215), body.inflate(-30, -40))
        pygame.draw.ellipse(surf, (90, 95, 105), body, 4)
        # antena
        pygame.draw.line(surf, (120, 120, 130), (cx + 40, cy - 55 + bob), (cx + 56, cy - 100 + bob), 5)
        blink = (math.sin(t / 300.0) > 0.6) or mood == "win"
        pygame.draw.circle(surf, (255, 80, 80) if blink else (120, 40, 40), (cx + 58, cy - 104 + int(bob)), 8)
        # visor
        visor = pygame.Rect(cx - 56, cy - 30 + bob, 112, 36)
        pygame.draw.rect(surf, (20, 24, 34), visor, border_radius=14)
        ex, ey = eye
        if mood in ("lose", "angry"):
            col = (255, 90, 60)
        elif mood in ("win", "happy"):
            col = (90, 255, 140)
        else:
            col = (90, 200, 255)
        px = visor.centerx + ex * 34
        py = visor.centery + ey * 8
        pygame.draw.rect(surf, col, (px - 16, py - 6, 32, 12), border_radius=6)
        g = soft_disc(26, col, 0.6)
        surf.blit(g, (px - 26, py - 26))
        # boca de segmentos
        segs = 7
        for i in range(segs):
            on = (mood in ("win", "happy") and (i + int(t / 120)) % 2 == 0) or (mood == "idle" and i % 2 == 0) \
                or (mood in ("lose", "angry") and abs(i - segs // 2) < 2)
            pygame.draw.rect(surf, (255, 170, 60) if on else (60, 50, 40), (cx - 40 + i * 12, cy + 22 + bob, 9, 6))
        shadow_text(surf, "BIT-9000", (cx, cy + 40 + bob), 16, (80, 84, 92), "center", shadow=None)

    @classmethod
    def portrait_tux(cls, surf, t, mood, eye, rng):
        cx, cy = PORTRAIT_W // 2, PORTRAIT_H // 2 + 10
        breathe = math.sin(t / 600.0) * 3
        # pies
        for sgn in (-1, 1):
            pygame.draw.ellipse(surf, (240, 160, 30), (cx + sgn * 40 - 30, cy + 92, 60, 24))
        body = pygame.Rect(cx - 82, cy - 90 + breathe, 164, 200 - breathe)
        pygame.draw.ellipse(surf, (18, 18, 24), body)
        belly = pygame.Rect(cx - 58, cy - 20, 116, 118)
        pygame.draw.ellipse(surf, (245, 245, 240), belly)
        # alas
        flap = math.sin(t / 120.0) * 14 if mood == "win" else 0
        for sgn in (-1, 1):
            pts = [(cx + sgn * 70, cy - 20), (cx + sgn * 108, cy + 40 + flap * sgn), (cx + sgn * 62, cy + 70)]
            pygame.draw.polygon(surf, (18, 18, 24), pts)
        # cara
        pygame.draw.ellipse(surf, (245, 245, 240), (cx - 50, cy - 86 + breathe, 100, 80))
        cls._eyes(surf, cx, cy - 50 + int(breathe), 22, 13, eye, mood)
        # pico
        beak_open = 10 if mood in ("win", "lose") else 2
        pygame.draw.polygon(surf, (250, 180, 30), [(cx - 26, cy - 30 + breathe), (cx + 26, cy - 30 + breathe), (cx, cy - 6 + breathe)])
        pygame.draw.polygon(surf, (210, 130, 20), [(cx - 20, cy - 26 + breathe + beak_open), (cx + 20, cy - 26 + breathe + beak_open),
                                                  (cx, cy - 4 + breathe + beak_open)])
        if mood == "lose":
            # gota de sudor
            pygame.draw.ellipse(surf, (120, 200, 255), (cx + 48, cy - 80 + (t / 20 % 30), 10, 16))

    @classmethod
    def portrait_arcana(cls, surf, t, mood, eye, rng):
        cx, cy = PORTRAIT_W // 2, PORTRAIT_H // 2
        sway = math.sin(t / 900.0) * 3
        # capa
        cape = [(cx - 100, PORTRAIT_H), (cx - 40, cy - 70 + sway), (cx, cy - 100 + sway), (cx + 40, cy - 70 + sway), (cx + 100, PORTRAIT_H)]
        pygame.draw.polygon(surf, (150, 20, 40), cape)
        pygame.draw.polygon(surf, (22, 26, 60), [(cx - 82, PORTRAIT_H), (cx - 36, cy - 66 + sway), (cx, cy - 96 + sway),
                                                 (cx + 36, cy - 66 + sway), (cx + 82, PORTRAIT_H)])
        # pelo
        pygame.draw.ellipse(surf, (40, 60, 160), (cx - 50, cy - 92 + sway, 100, 150))
        # cara
        pygame.draw.ellipse(surf, (236, 222, 210), (cx - 32, cy - 78 + sway, 64, 84))
        hair_strand = [(cx - 44, cy - 60 + sway), (cx - 30, cy - 90 + sway), (cx + 10, cy - 92 + sway), (cx + 40, cy - 70 + sway), (cx + 22, cy - 60 + sway)]
        pygame.draw.polygon(surf, (50, 80, 190), hair_strand)
        cls._eyes(surf, cx, cy - 42 + int(sway), 13, 7, eye, mood, iris=(60, 220, 255), pupil_k=0.6)
        # labios
        cls._mouth(surf, cx, cy - 10 + sway, 22, mood, (150, 40, 70), t)
        # traje
        pygame.draw.polygon(surf, (40, 44, 60), [(cx - 44, cy + 10 + sway), (cx + 44, cy + 10 + sway), (cx + 70, PORTRAIT_H), (cx - 70, PORTRAIT_H)])
        pygame.draw.line(surf, (200, 200, 220), (cx - 20, cy + 30), (cx + 20, cy + 30), 2)
        # orbe magico
        pulse = 0.5 + 0.5 * math.sin(t / 180.0)
        orb_r = 14 + int(4 * pulse)
        ox, oy = cx, cy + 70 + sway
        g = soft_disc(orb_r * 3, (120, 80, 255), 0.6 + 0.3 * pulse)
        surf.blit(g, (ox - orb_r * 3, oy - orb_r * 3))
        pygame.draw.circle(surf, (170, 140, 255), (int(ox), int(oy)), orb_r)
        pygame.draw.circle(surf, (240, 230, 255), (int(ox - 4), int(oy - 5)), max(2, orb_r // 3))
        for k in range(3):
            ang = t / 300.0 + k * 2.09
            pygame.draw.circle(surf, (220, 200, 255), (int(ox + math.cos(ang) * orb_r * 1.8), int(oy + math.sin(ang) * orb_r * 0.6)), 3)
        for sgn in (-1, 1):
            pygame.draw.ellipse(surf, (236, 222, 210), (ox + sgn * 26 - 10, oy - 6, 20, 14))

    @classmethod
    def portrait_vera(cls, surf, t, mood, eye, rng):
        cx, cy = PORTRAIT_W // 2, PORTRAIT_H // 2
        bob = math.sin(t / 500.0) * 2
        # hombros / camisa
        pygame.draw.polygon(surf, (30, 30, 36), [(cx - 90, PORTRAIT_H), (cx - 60, cy + 40), (cx + 60, cy + 40), (cx + 90, PORTRAIT_H)])
        pygame.draw.polygon(surf, (200, 40, 90), [(cx - 24, cy + 40), (cx + 24, cy + 40), (cx + 10, cy + 100), (cx - 10, cy + 100)])
        # brazo hidraulico (derecho)
        arm = [(cx + 60, cy + 50), (cx + 96, cy + 30), (cx + 104, cy + 44), (cx + 70, cy + 68)]
        pygame.draw.polygon(surf, (150, 155, 170), arm)
        pygame.draw.polygon(surf, (90, 95, 110), arm, 2)
        pygame.draw.circle(surf, (120, 125, 140), (cx + 100, cy + 36), 9)
        pygame.draw.circle(surf, (255, 200, 80), (cx + 100, cy + 36), 4)
        # cuello
        pygame.draw.rect(surf, (220, 190, 170), (cx - 14, cy + 10, 28, 36))
        # cara
        pygame.draw.ellipse(surf, (228, 196, 176), (cx - 36, cy - 76 + bob, 72, 96))
        # pelo bob magenta
        pygame.draw.ellipse(surf, (200, 40, 160), (cx - 46, cy - 92 + bob, 92, 70))
        pygame.draw.rect(surf, (200, 40, 160), (cx - 46, cy - 60 + bob, 14, 60))
        pygame.draw.rect(surf, (200, 40, 160), (cx + 32, cy - 60 + bob, 14, 60))
        pygame.draw.ellipse(surf, (228, 196, 176), (cx - 36, cy - 66 + bob, 72, 86))
        pygame.draw.polygon(surf, (200, 40, 160), [(cx - 36, cy - 66 + bob), (cx + 36, cy - 66 + bob), (cx + 10, cy - 48 + bob), (cx - 30, cy - 52 + bob)])
        # ojos: uno humano, uno cibernetico
        ex, ey = eye
        cls._eyes(surf, cx - 14, cy - 36 + int(bob), 0, 7, eye, mood, iris=(80, 60, 40))
        rx, ry = cx + 15, cy - 36 + int(bob)
        pygame.draw.circle(surf, (40, 40, 48), (rx, ry), 9)
        pygame.draw.circle(surf, (255, 60, 40) if mood in ("angry", "lose") else (255, 150, 40), (rx, ry), 6)
        pygame.draw.circle(surf, (20, 10, 10), (int(rx + ex * 3), int(ry + ey * 3)), 2)
        pygame.draw.arc(surf, (255, 220, 160), (rx - 12, ry - 12, 24, 24), t / 200.0, t / 200.0 + 1.5, 2)
        cls._mouth(surf, cx, cy - 4 + bob, 24, mood, (160, 40, 60), t)
        # pendientes de neon
        for sgn in (-1, 1):
            pygame.draw.line(surf, (80, 240, 255), (cx + sgn * 38, cy - 30 + bob), (cx + sgn * 38, cy - 6 + bob), 2)
        # copa en la mano
        pygame.draw.polygon(surf, (200, 230, 255), [(cx + 90, cy + 8), (cx + 112, cy + 8), (cx + 108, cy + 30), (cx + 94, cy + 30)])
        pygame.draw.rect(surf, (240, 120, 60), (cx + 96, cy + 18, 10, 11))

    @classmethod
    def portrait_nullbyte(cls, surf, t, mood, eye, rng):
        cx, cy = PORTRAIT_W // 2, PORTRAIT_H // 2
        jitter = rng.randint(-3, 3) if rng.random() < 0.15 else 0
        head = pygame.Rect(cx - 70 + jitter, cy - 80, 140, 150)
        pygame.draw.rect(surf, (8, 12, 10), head)
        pygame.draw.rect(surf, (40, 255, 120), head, 3)
        # scanlines
        for y in range(head.top + 4, head.bottom - 4, 6):
            pygame.draw.line(surf, (20, 70, 40), (head.left + 3, y), (head.right - 3, y), 1)
        # cuerpo "cable"
        for i in range(5):
            x = cx - 40 + i * 20 + int(6 * math.sin(t / 200.0 + i))
            pygame.draw.line(surf, (40, 200, 100), (x, head.bottom), (x + rng.randint(-2, 2), PORTRAIT_H), 2)
        # ojos como caracteres
        font = get_font(52, True)
        ex, ey = eye
        if mood in ("win", "happy"):
            l, r = "^", "^"
        elif mood in ("lose", "angry"):
            l, r = "X", "X"
        else:
            l, r = ("0", "1") if int(t / 400) % 2 == 0 else ("1", "0")
        for ch, sgn in ((l, -1), (r, 1)):
            img = font.render(ch, True, (90, 255, 140))
            surf.blit(img, (cx + sgn * 32 - img.get_width() // 2 + int(ex * 8) + jitter, cy - 50 + int(ey * 6) - img.get_height() // 2))
        # boca: barra de progreso / onda
        mouth_y = cy + 24
        if mood in ("win", "happy"):
            pts = [(cx - 44 + i * 8, mouth_y + int(8 * math.sin(t / 60.0 + i))) for i in range(12)]
            pygame.draw.lines(surf, (90, 255, 140), False, pts, 3)
        elif mood in ("lose", "angry"):
            for i in range(6):
                pygame.draw.rect(surf, (255, 60, 60), (cx - 42 + i * 14, mouth_y - 4, 10, 8))
        else:
            w = int(40 + 36 * (0.5 + 0.5 * math.sin(t / 700.0)))
            pygame.draw.rect(surf, (20, 90, 50), (cx - 44, mouth_y - 5, 88, 10))
            pygame.draw.rect(surf, (90, 255, 140), (cx - 44, mouth_y - 5, w, 10))
        # glitch: desplazar una franja horizontal
        if rng.random() < 0.08:
            y = rng.randint(head.top, head.bottom - 12)
            band = surf.subsurface((head.left, y, head.width, 10)).copy()
            surf.blit(band, (head.left + rng.randint(-14, 14), y))
        shadow_text(surf, "SIGSEGV", (cx, head.bottom + 6), 18, (40, 200, 100), "center", shadow=None,
                    alpha=160 if int(t / 500) % 3 else 60)

    @classmethod
    def portrait_daemon(cls, surf, t, mood, eye, rng):
        cx, cy = PORTRAIT_W // 2, PORTRAIT_H // 2
        # humo
        for i in range(8):
            ph = (t / 1400.0 + i * 0.37) % 1.0
            x = cx + int(60 * math.sin(i * 1.9 + t / 900.0)) + int(20 * math.sin(ph * 6))
            y = int(PORTRAIT_H - ph * PORTRAIT_H)
            r = int(10 + 24 * ph)
            g = soft_disc(r, (120, 20, 40), 0.35 * (1 - ph))
            surf.blit(g, (x - r, y - r))
        glow = soft_disc(120, (255, 60, 20), 0.35 + 0.1 * math.sin(t / 250.0))
        surf.blit(glow, (cx - 120, cy - 120))
        # silueta
        body = [(cx - 90, PORTRAIT_H), (cx - 60, cy + 10), (cx - 50, cy - 60), (cx - 20, cy - 90), (cx + 20, cy - 90),
                (cx + 50, cy - 60), (cx + 60, cy + 10), (cx + 90, PORTRAIT_H)]
        pygame.draw.polygon(surf, (26, 6, 10), body)
        pygame.draw.polygon(surf, (90, 20, 30), body, 2)
        # cuernos
        for sgn in (-1, 1):
            horn = [(cx + sgn * 30, cy - 80), (cx + sgn * 70, cy - 130 + int(3 * math.sin(t / 300.0))), (cx + sgn * 48, cy - 70)]
            pygame.draw.polygon(surf, (60, 12, 20), horn)
            pygame.draw.polygon(surf, (160, 40, 50), horn, 2)
        # ojos de brasa
        ex, ey = eye
        for sgn in (-1, 1):
            x = cx + sgn * 26 + int(ex * 6)
            y = cy - 40 + int(ey * 5)
            g = soft_disc(22, (255, 120, 30), 0.9)
            surf.blit(g, (x - 22, y - 22))
            if mood in ("angry", "lose"):
                pygame.draw.polygon(surf, (255, 230, 120), [(x - 12, y - 2 * sgn), (x + 12, y + 2 * sgn), (x + 4, y + 7), (x - 4, y + 7)])
            else:
                pygame.draw.ellipse(surf, (255, 230, 120), (x - 11, y - 5, 22, 10))
            pygame.draw.circle(surf, (40, 0, 0), (x + int(ex * 4), y), 3)
        # boca de dientes
        my = cy + 6
        if mood in ("win", "happy"):
            pygame.draw.polygon(surf, (120, 10, 20), [(cx - 40, my - 6), (cx + 40, my - 6), (cx + 26, my + 26), (cx - 26, my + 26)])
            for i in range(7):
                pygame.draw.polygon(surf, (240, 230, 200), [(cx - 36 + i * 12, my - 6), (cx - 28 + i * 12, my - 6), (cx - 32 + i * 12, my + 8)])
        elif mood in ("lose", "angry"):
            pygame.draw.polygon(surf, (120, 10, 20), [(cx - 30, my + 10), (cx + 30, my + 10), (cx + 18, my - 6), (cx - 18, my - 6)])
            for i in range(5):
                pygame.draw.polygon(surf, (240, 230, 200), [(cx - 24 + i * 12, my + 10), (cx - 16 + i * 12, my + 10), (cx - 20 + i * 12, my - 2)])
        else:
            pygame.draw.line(surf, (160, 40, 50), (cx - 30, my + 4), (cx + 30, my + 4), 3)
            for i in range(6):
                pygame.draw.line(surf, (240, 230, 200), (cx - 25 + i * 10, my + 4), (cx - 25 + i * 10, my + 10), 2)


# ---- 7. Entity / Pad / Puck / fisica del golpe ------------------------------
class Entity:
    """Objeto sobre el tablero (entity.c).  Posiciones en unidades de tablero,
    velocidades en unidades/ms."""

    __slots__ = ("x", "z", "dx", "dz", "w", "h", "area", "prev_x", "prev_z", "alpha")

    def __init__(self, w, h, x=0.0, z=0.0):
        self.x, self.z = float(x), float(z)
        self.dx = self.dz = 0.0
        self.w, self.h = float(w), float(h)
        self.area = [AREA_X1, AREA_X2, AREA_Z1, AREA_Z2]   # C: MoveArea
        self.prev_x, self.prev_z = self.x, self.z
        self.alpha = 255

    def snapshot(self):
        self.prev_x, self.prev_z = self.x, self.z

    def set_position(self, x, z):
        self.x, self.z = float(x), float(z)

    def set_velocity(self, dx, dz):
        self.dx, self.dz = float(dx), float(dz)

    def interp(self, alpha):
        return (self.prev_x + (self.x - self.prev_x) * alpha, self.prev_z + (self.z - self.prev_z) * alpha)

    def move(self, dt):
        """C: entity_move().  Devuelve (rebote_x, rebote_z)."""
        x1, x2, z1, z2 = self.area
        self.x += self.dx * dt
        self.z += self.dz * dt
        bx = bz = False
        hw, hh = self.w / 2, self.h / 2
        if self.x < x1 + hw:
            self.x = x1 + hw
            self.dx = -self.dx
            bx = True
        elif self.x > x2 - hw:
            self.x = x2 - hw
            self.dx = -self.dx
            bx = True
        if self.z < z1 + hh:
            self.z = z1 + hh
            self.dz = -self.dz
            bz = True
        elif self.z > z2 - hh:
            self.z = z2 - hh
            self.dz = -self.dz
            bz = True
        return bx, bz

    def move_towards(self, x, z, speed, dt) -> bool:
        """C: entity_move_towards().  True cuando ha llegado."""
        dx = dz = 0.0
        if x > self.x:
            dx = speed
            if x < self.x + dx * dt:
                self.x = x
                dx = 0.0
        elif x < self.x:
            dx = -speed
            if x > self.x + dx * dt:
                self.x = x
                dx = 0.0
        if z > self.z:
            dz = speed
            if z < self.z + dz * dt:
                self.z = z
                dz = 0.0
        elif z < self.z:
            dz = -speed
            if z > self.z + dz * dt:
                self.z = z
                dz = 0.0
        self.dx, self.dz = dx, dz
        self.move(dt)
        return self.x == x and self.z == z

    @property
    def speed(self):
        return math.hypot(self.dx, self.dz)


class Pad(Entity):
    __slots__ = ("side", "color", "flash", "boost", "cooldown", "hits", "sign", "home_z")

    def __init__(self, side: int, scale=1.0):
        super().__init__(PAD_W * scale, PAD_H)
        self.side = side
        self.sign = 1.0 if side == 2 else -1.0     # direccion "hacia el rival"
        if side == 1:
            self.area[2], self.area[3] = PAD1_Z1, PAD1_Z2
            self.z = -PAD_HOME_Z
        else:
            self.area[2], self.area[3] = PAD2_Z1, PAD2_Z2
            self.z = PAD_HOME_Z
        self.home_z = self.z
        self.prev_z = self.z
        self.alpha = 128                            # C: pad->alpha = 128
        self.color = Art.PAD_COLORS[side]
        self.flash = 0.0
        self.boost = 0.0
        self.cooldown = 0.0
        self.hits = 0

    def tick_fx(self, dt):
        self.flash = max(0.0, self.flash - dt / 220.0)
        self.boost = max(0.0, self.boost - dt)
        self.cooldown = max(0.0, self.cooldown - dt)

    def power_hit(self) -> bool:
        if self.cooldown > 0:
            return False
        self.boost = POWER_HIT_MS
        self.cooldown = POWER_HIT_COOLDOWN
        return True


class Puck(Entity):
    __slots__ = ("alive", "spin", "trail", "state", "turn", "uid", "last_hit", "glass", "serve_x")

    def __init__(self, uid=0):
        super().__init__(PUCK_W, PUCK_W)
        self.alive = False                           # C: PUCK_STATE_DEAD al crear
        self.spin = 0.0
        self.trail = []
        self.state = "new"                           # new | play | crash
        self.turn = 1                                # 1: saca el humano (abajo), 2: el rival
        self.uid = uid
        self.last_hit = 0
        self.glass = None
        self.serve_x = 0.0

    def move_puck(self, dt, arcade=False):
        """C: puck_move() + efecto arcade."""
        if arcade and self.spin:
            self.dx += self.spin * SPIN_STRENGTH * abs(self.dz) * dt
            self.spin *= SPIN_DECAY ** dt
            if abs(self.spin) < 1e-3:
                self.spin = 0.0
        bx, bz = self.move(dt)
        if bx and arcade:
            self.spin *= -0.5
        return bx, bz

    def push_trail(self):
        if self.speed > 0.06:
            self.trail.append((self.x, self.z))
            if len(self.trail) > 9:
                self.trail.pop(0)
        elif self.trail:
            self.trail.pop(0)


def pad_will_hit(puck: Puck, pad: Pad, dt) -> bool:
    """C: puck_will_get_hit_by()."""
    pad_nx = pad.x + pad.dx * dt
    pad_nz = pad.z + pad.dz * dt
    puck_nx = puck.x + puck.dx * dt
    puck_nz = puck.z + puck.dz * dt
    half = PUCK_W / 2
    if pad.area[2] < 0:
        if pad.area[3] < puck_nz - half:
            return False
    elif pad.area[2] > puck_nz + half:
        return False
    if abs(pad_nx - puck_nx) > pad.w / 2 + half:
        return False
    if pad.z < puck.z:
        return pad_nz + PAD_H / 2 > puck_nz - half
    return pad_nz - PAD_H / 2 < puck_nz + half


def pad_hits(puck: Puck, pad: Pad, dt, max_speed=MAX_PUCK_SPEED, arcade=False):
    """C: puck_get_hit_by().  El disco pierde el 40 % y gana el 60 % de la pala."""
    pdx, pdz = pad.dx, pad.dz
    if pad.boost > 0:
        pdz *= POWER_HIT_FACTOR
    old_dx = puck.dx
    puck.dx += pdx * HIT_FRICTION
    puck.dx *= HIT_FRICTION
    half = PUCK_W / 2 + PAD_H / 2 + THRESHOLD
    if pad.z < puck.z:
        if puck.dz < 0:
            puck.dz = -puck.dz
        puck.z = pad.z + pdz * dt + half
    else:
        if puck.dz > 0:
            puck.dz = -puck.dz
        puck.z = pad.z + pdz * dt - half
    puck.dz += pdz * HIT_FRICTION
    puck.dz *= HIT_FRICTION
    sp = math.hypot(puck.dx, puck.dz)
    if sp > max_speed:
        puck.dx = puck.dx / sp * max_speed
        puck.dz = puck.dz / sp * max_speed
    if arcade:
        puck.spin = clamp((pdx - old_dx) * 5.0, -1.0, 1.0)
    puck.last_hit = pad.side
    pad.hits += 1
    pad.flash = 1.0


def calc_puck_x(x, z, dx, dz, ax):
    """C: _calc_puck_x() (Ernst Moritz Hahn).  x de llegada del disco a z=ax
    contando rebotes en las bandas, por el metodo del modulo."""
    board_w = 40 - PUCK_W
    n = (ax - z) / dz
    raw_x = n * dx + x + board_w / 2
    rx = fmod(raw_x, board_w)
    g = raw_x / board_w
    h = abs(fmod(g, 2))
    if h > 1:
        rx = board_w - rx
    return rx - board_w / 2


def predict_path(x, z, dx, dz, spin=0.0, arcade=False, max_steps=1200, dt=FIXED_DT, stop_z=None):
    """Integra numericamente la trayectoria del disco hasta cruzar |z|>GOAL_Z o
    stop_z (en valor absoluto).  Devuelve lista de (x, z)."""
    pts = []
    e = Entity(PUCK_W, PUCK_W, x, z)
    e.dx, e.dz = dx, dz
    limit = GOAL_Z if stop_z is None else abs(stop_z)
    for _ in range(max_steps):
        if arcade and spin:
            e.dx += spin * SPIN_STRENGTH * abs(e.dz) * dt
            spin *= SPIN_DECAY ** dt
        bx, _bz = e.move(dt)
        if bx and arcade:
            spin *= -0.5
        pts.append((e.x, e.z))
        if abs(e.z) >= limit or (e.dx == 0 and e.dz == 0):
            break
    return pts


# ---- 8. Glass / Shards / FX --------------------------------------------------
# Piezas grandes del cristal (glass.c: _piece_reset), (x, y, dx, dy) relativas
GLASS_PIECES = [(-2.5, 0.0, -0.010, 0.25), (-1.9, 0.2, -0.008, 0.28), (0.5, 0.0, 0.001, 0.30),
                (1.0, 0.7, 0.007, 0.27), (1.1, 0.1, 0.012, 0.26)]
PIECE_PX = 3.0 * S   # pixeles por unidad de altura de las piezas (dramatizado)


class GlassSmash:
    """Cristal de la porteria que estalla cuando entra el disco."""

    def __init__(self):
        self.active = False
        self.x = 0.0
        self.z = GOAL_Z
        self.pieces = []
        self.shards = []
        self.age = 0.0

    def smash(self, x, z, puck_dx, rng: random.Random):
        self.x = x - 1.2                                    # C: glass_smash
        self.z = -GOAL_Z if z < 0 else GOAL_Z
        self.active = True
        self.age = 0.0
        self.pieces = []
        for (px, py, pdx, pdy) in GLASS_PIECES:
            poly = [(rng.uniform(-12, 12) * S, rng.uniform(-12, 12) * S) for _ in range(rng.randint(3, 5))]
            self.pieces.append({"x": self.x + px, "y": py, "dx": pdx + puck_dx * 0.1, "dy": pdy,
                                "poly": poly, "rot": rng.uniform(0, 6.28), "spin": rng.uniform(-0.004, 0.004), "big": True})
        for _ in range(40):
            poly = [(rng.uniform(-4, 4) * S, rng.uniform(-4, 4) * S) for _ in range(3)]
            self.pieces.append({"x": self.x + rng.uniform(-6, 6), "y": rng.uniform(0, 3),
                                "dx": rng.uniform(-0.02, 0.02) + puck_dx * 0.15, "dy": rng.uniform(0.08, 0.34),
                                "poly": poly, "rot": rng.uniform(0, 6.28), "spin": rng.uniform(-0.01, 0.01), "big": False})

    def update(self, dt) -> bool:
        """C: glass_update().  True mientras quede alguna pieza en el aire."""
        if not self.active:
            return False
        self.age += dt
        alive = False
        for p in self.pieces:
            p["dy"] += GLASS_GRAVITY * dt
            p["x"] += p["dx"] * dt
            p["y"] += p["dy"] * dt
            p["rot"] += p["spin"] * dt
            if p["y"] >= -10:
                alive = True
        if not alive:
            self.active = False
        return self.active

    def draw(self, surf, persp: Perspective, offset=(0, 0)):
        if not self.active:
            return
        Art.draw_glass_pane(surf, persp, self.x, self.z, 70, crack=self.x)
        ox, oy = offset
        for p in self.pieces:
            if p["y"] < -10:
                continue
            sx, sy, sc = persp.project(p["x"], self.z)
            sy -= p["y"] * PIECE_PX * sc
            c, s = math.cos(p["rot"]), math.sin(p["rot"])
            pts = [(sx + ox + (vx * c - vy * s) * sc, sy + oy + (vx * s + vy * c) * sc) for vx, vy in p["poly"]]
            col = (210, 235, 255) if p["big"] else (160, 210, 255)
            pygame.draw.polygon(surf, col, pts)
            if p["big"]:
                pygame.draw.polygon(surf, (255, 255, 255), pts, 1)


class Shaker:
    def __init__(self):
        self.amp = 0.0
        self.rng = random.Random(1)

    def kick(self, amount):
        self.amp = max(self.amp, amount)

    def update(self, dt):
        self.amp *= 0.92 ** (dt / 4.0)
        if self.amp < 0.3:
            self.amp = 0.0

    def offset(self):
        if self.amp <= 0:
            return (0, 0)
        return (self.rng.uniform(-self.amp, self.amp), self.rng.uniform(-self.amp, self.amp))


class Sparks:
    """Chispas en espacio de pantalla (golpes de pala, bandas)."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.parts = []

    def burst(self, sx, sy, color, n=10, speed=0.25):
        for _ in range(n):
            a = self.rng.uniform(0, 6.283)
            v = self.rng.uniform(0.3, 1.0) * speed
            self.parts.append([sx, sy, math.cos(a) * v, math.sin(a) * v - 0.1, self.rng.uniform(180, 380), color])

    def update(self, dt):
        keep = []
        for p in self.parts:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[3] += 0.0008 * dt
            p[4] -= dt
            if p[4] > 0:
                keep.append(p)
        self.parts = keep

    def draw(self, surf, offset=(0, 0)):
        for x, y, _vx, _vy, life, col in self.parts:
            r = 1 + int(life / 120)
            pygame.draw.circle(surf, col, (int(x + offset[0]), int(y + offset[1])), r)


# ---- 9. Board ------------------------------------------------------------------
class Board:
    """Arbitro: palas, discos, cristal, estados (board.c) y lista de eventos."""

    def __init__(self, rng: random.Random, mutators: Mutators, first_turn=1):
        self.rng = rng
        self.mut = mutators
        self.arcade = mutators.arcade_physics
        self.max_speed = MAX_PUCK_SPEED * mutators.speed
        self.pads = {1: Pad(1, mutators.pad_scale), 2: Pad(2, mutators.pad_scale)}
        n = int(clamp(mutators.pucks, 1, 3))
        self.pucks = []
        for i in range(n):
            p = Puck(i)
            p.turn = first_turn if i % 2 == 0 else 3 - first_turn
            p.serve_x = (i - (n - 1) / 2.0) * 8.0
            p.set_position(p.serve_x, 0.0)
            p.glass = GlassSmash()
            self.pucks.append(p)
        self.events = []          # (tipo, datos...)  consumidos por la escena
        self.rally = 0
        self.score = {1: 0, 2: 0}
        self.frozen = False

    # -- consulta ---------------------------------------------------------------
    def any_in_play(self):
        return any(p.state == "play" for p in self.pucks)

    def puck_for(self, side):
        """Disco mas relevante para el jugador `side`: vivo y acercandose, si no el mas cercano."""
        sign = 1.0 if side == 2 else -1.0
        best, best_key = None, None
        for p in self.pucks:
            if p.state != "play":
                continue
            approaching = p.dz * sign > 0
            dist = abs(p.z * sign - PAD_HOME_Z)
            key = (0 if approaching else 1, dist)
            if best_key is None or key < best_key:
                best, best_key = p, key
        if best is None:
            return min(self.pucks, key=lambda p: abs(p.z * sign - PAD_HOME_Z))
        return best

    # -- simulacion -------------------------------------------------------------
    def update(self, dt):
        """Un paso de fisica.  Devuelve lista de ganadores de punto (1 o 2)."""
        scorers = []
        if self.frozen:
            return scorers
        for p in self.pucks:
            p.snapshot()
        for pad in self.pads.values():
            pad.snapshot()
        for p in self.pucks:
            if p.state == "play":
                hit_pad = None
                if pad_will_hit(p, self.pads[1], dt):
                    hit_pad = self.pads[1]
                elif pad_will_hit(p, self.pads[2], dt):
                    hit_pad = self.pads[2]
                if hit_pad is not None:
                    pad_hits(p, hit_pad, dt, self.max_speed, self.arcade)
                    self.rally += 1
                    self.events.append(("hit", hit_pad.side, p.speed, p.x, p.z))
                bx, bz = p.move_puck(dt, self.arcade)
                if bx:
                    self.events.append(("wall", p.x, p.z))
                p.push_trail()
                scorer = self._scorer(p)
                if scorer:
                    p.state = "crash"
                    p.alive = False
                    p.glass.smash(p.x, p.z, p.dx, self.rng)
                    p.trail.clear()
                    self.score[scorer] += 1
                    self.rally = 0
                    self.events.append(("goal", scorer, p.x, p.z))
                    scorers.append(scorer)
            elif p.state == "crash":
                if not p.glass.update(dt):
                    p.state = "new"
                    p.turn = 2 if p.turn == 1 else 1          # C: alterna el saque
                    p.spin = 0.0
            elif p.state == "new":
                target_z = -SERVE_Z if p.turn == 1 else SERVE_Z
                if p.move_towards(p.serve_x, target_z, SERVE_SPEED, dt):
                    p.state = "play"
                    p.alive = True
                    p.set_velocity(0.0, 0.0)
                    self.events.append(("serve", p.turn))
        for pad in self.pads.values():
            pad.move(dt)
            pad.tick_fx(dt)
        return scorers

    def _scorer(self, p: Puck):
        """C: _get_scorer()."""
        if not p.alive:
            return 0
        if p.z > GOAL_Z:
            return 1
        if p.z < -GOAL_Z:
            return 2
        return 0

    def drain_events(self):
        ev = self.events
        self.events = []
        return ev


# ---- 10. Controllers ----------------------------------------------------------
class FrameInput:
    """Entrada recogida por la escena una vez por frame y leida por los controladores."""

    __slots__ = ("mouse_rel", "frame_dt", "keys", "joy_axes", "power_events")

    def __init__(self):
        self.mouse_rel = (0, 0)
        self.frame_dt = 16.0
        self.keys = None
        self.joy_axes = (0.0, 0.0)
        self.power_events = set()      # esquemas que han pedido golpe fuerte este frame


class HumanController:
    """Ratón (modo relativo, como el original), teclado WASD/flechas o mando."""

    KEYMAPS = {
        "wasd": (pygame.K_a, pygame.K_d, pygame.K_w, pygame.K_s, pygame.K_SPACE),
        "arrows": (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN, pygame.K_KP_ENTER),
    }

    def __init__(self, pad: Pad, scheme: str, units_per_px: float, power_enabled=True):
        self.pad = pad
        self.scheme = scheme
        self.units_per_px = units_per_px
        self.power_enabled = power_enabled
        self.vx = self.vz = 0.0
        self.target_vx = self.target_vz = 0.0
        self.is_ai = False

    def begin_frame(self, fi: FrameInput):
        if self.scheme == "mouse":
            dt = max(1.0, fi.frame_dt)
            self.target_vx = fi.mouse_rel[0] / dt * self.units_per_px      # C: human_update()
            self.target_vz = -fi.mouse_rel[1] / dt * self.units_per_px
            self.vx, self.vz = self.target_vx, self.target_vz
        elif self.scheme in self.KEYMAPS:
            left, right, up, down, _power = self.KEYMAPS[self.scheme]
            keys = fi.keys
            kx = (1 if keys and keys[right] else 0) - (1 if keys and keys[left] else 0)
            kz = (1 if keys and keys[up] else 0) - (1 if keys and keys[down] else 0)
            if kx and kz:
                kx *= 0.7071
                kz *= 0.7071
            self.target_vx = kx * KEY_PAD_SPEED
            self.target_vz = kz * KEY_PAD_SPEED
        else:  # gamepad
            ax, ay = fi.joy_axes
            dead = 0.15
            ax = 0.0 if abs(ax) < dead else ax
            ay = 0.0 if abs(ay) < dead else ay
            self.target_vx = ax * KEY_PAD_SPEED
            self.target_vz = -ay * KEY_PAD_SPEED
        if self.scheme in fi.power_events and self.power_enabled:
            self.pad.power_hit()

    def step(self, dt):
        if self.scheme != "mouse":
            k = min(1.0, dt / 55.0)
            self.vx += (self.target_vx - self.vx) * k
            self.vz += (self.target_vz - self.vz) * k
        vz = self.vz
        if self.pad.boost > 0 and vz * self.pad.sign > 0:
            vz *= POWER_HIT_FACTOR
        self.pad.set_velocity(self.vx, vz)


@dataclass
class AIParams:
    speed: float = 3.0            # C: player->speed
    hit_power: float = 2.5        # C: player->hit_power
    reaction_ms: float = 120.0    # retardo de percepcion (nuevo)
    aim_error: float = 2.0        # sesgo gaussiano en unidades (nuevo)
    predict: bool = True          # usa _calc_puck_x con rebotes; si no, extrapola recto
    idle: str = "smart"           # dumb | smart   (player.c)
    backup: str = "dumb"          # dumb | smart
    serves: dict = field(default_factory=lambda: {"dumb": 1.0})   # dumb | swirl | bank | feint
    bank_shot: float = 0.0        # prob. de tirar a banda (nuevo)
    feint: float = 0.0            # prob. de amagar (nuevo)
    plan_shots: bool = False      # se coloca para elegir el angulo (nuevo)
    celebrate_ms: float = 700.0

    def scaled(self, speed_mult=1.0, nightmare=False):
        p = AIParams(**asdict(self))
        p.hit_power *= speed_mult
        if nightmare:
            p.speed *= 1.25
            p.hit_power *= 1.15
            p.reaction_ms = min(p.reaction_ms, 30.0)
            p.aim_error *= 0.3
            p.predict = True
            p.plan_shots = True
            p.bank_shot = max(p.bank_shot, 0.3)
        return p


class AIController:
    """Port de player.c / tux.c / arcana.c con extensiones.  Trabaja en un marco
    'espejado' en el que el rival esta siempre en z > 0 (sign = -1 lo coloca abajo)."""

    IDLE, SERVE, BACKUP, AIM, HIT, FEINT, WIN_POINT, LOSE_POINT, WIN_GAME, LOSE_GAME = range(10)

    def __init__(self, pad: Pad, board: Board, params: AIParams, rng: random.Random, side=2):
        self.pad = pad
        self.board = board
        self.p = params
        self.rng = rng
        self.side = side
        self.sign = 1.0 if side == 2 else -1.0
        self.state = self.IDLE
        self.counter = math.pi / 2                    # C: static float counter = M_PI_2
        self.history = []                             # (t, x, z, dx, dz) en marco espejado
        self.time = 0.0
        self.ready_timer = Timer(params.celebrate_ms)
        self.ready_timer.elapsed = params.celebrate_ms
        self.error_bias = 0.0
        self.react_mult = 1.0
        self.err_mult = 1.0
        self.puck = board.puck_for(side)
        self.force_bank = False
        self.force_feint = False
        self.shot_target_x = 0.0
        self.shot_bank = False
        self.feint_timer = Timer(60)
        self.feint_phase = 0
        self.serve_kind = "dumb"
        self.swirl = {"state": 0, "counter": 0.0, "speed": 0.0, "limit": 0.0, "corner": 0}
        self.mood = "idle"
        self.mood_timer = Timer(1200)
        self.is_ai = True
        self._new_error()

    # -- utilidades del marco espejado ---------------------------------------
    def _new_error(self):
        self.error_bias = self.rng.gauss(0.0, self.p.aim_error * self.err_mult)

    def _pad_pos(self):
        return self.pad.x, self.pad.z * self.sign

    def _puck_now(self):
        p = self.puck
        return p.x, p.z * self.sign, p.dx, p.dz * self.sign

    def _puck_seen(self):
        """Estado del disco hace reaction_ms (percepcion retardada)."""
        delay = self.p.reaction_ms * self.react_mult
        if delay <= 0 or not self.history:
            return self._puck_now()
        t_target = self.time - delay
        for rec in self.history:
            if rec[0] >= t_target:
                return rec[1:]
        return self.history[-1][1:]

    def _set_vel(self, dx, dz):
        self.pad.set_velocity(dx, dz * self.sign)

    def _move_towards(self, x, z, speed, dt) -> bool:
        return self.pad.move_towards(x, z * self.sign, speed, dt)

    def _predict_x(self, x, z, dx, dz, az):
        """x de llegada a z=az (marco espejado) segun capacidad de prediccion."""
        if dz == 0:
            return x
        if self.p.predict:
            if self.board.arcade and self.puck.spin:
                pts = predict_path(x, z * self.sign, dx, dz * self.sign, self.puck.spin, True,
                                   stop_z=az)
                return pts[-1][0] if pts else x
            return calc_puck_x(x, z, dx, dz, az)
        raw = x + dx * (az - z) / dz
        return clamp(raw, AREA_X1 + PUCK_W / 2, AREA_X2 - PUCK_W / 2)

    def _pad_x_limits(self):
        return AREA_X1 + self.pad.w / 2, AREA_X2 - self.pad.w / 2

    # -- eventos externos -------------------------------------------------------
    def on_point(self, won: bool):
        self.state = self.WIN_POINT if won else self.LOSE_POINT
        self.ready_timer.start(self.p.celebrate_ms)
        self.mood = "win" if won else "lose"
        self.mood_timer.start(1500)
        self._new_error()

    def on_game_over(self, won: bool):
        self.state = self.WIN_GAME if won else self.LOSE_GAME
        self.mood = "win" if won else "lose"
        self.mood_timer.start(1e9)

    def adapt(self, my_score, their_score):
        diff = my_score - their_score
        if diff <= -5:
            self.react_mult, self.err_mult = 0.8, 0.7
        elif diff >= 5:
            self.react_mult, self.err_mult = 1.3, 1.4
        else:
            self.react_mult, self.err_mult = 1.0, 1.0

    # -- bucle -------------------------------------------------------------------
    def update(self, dt):
        self.time += dt
        self.puck = self.board.puck_for(self.side)
        x, z, dx, dz = self._puck_now()
        self.history.append((self.time, x, z, dx, dz))
        if len(self.history) > 160:
            del self.history[:40]
        if self.mood_timer.update(dt) and self.mood != "idle":
            self.mood = "idle"
        st = self.state
        if st == self.IDLE:
            self._idle(dt)
        elif st == self.SERVE:
            self._serve(dt)
        elif st == self.BACKUP:
            self._backup(dt)
        elif st == self.AIM:
            self._aim(dt)
        elif st == self.FEINT:
            self._feint(dt)
        elif st == self.HIT:
            self._hit(dt)
        elif st in (self.WIN_POINT, self.LOSE_POINT):
            self._dumb_idle(dt)
            if self.ready_timer.update(dt) and self.puck.state == "play":
                if self.puck.turn == self.side:
                    self._enter_serve()
                else:
                    self.state = self.IDLE
        else:
            self._dumb_idle(dt)

    # -- estrategias (player.c) -------------------------------------------------
    def _dumb_idle(self, dt):
        """C: _dumb_really_idle()."""
        self.counter += 0.005 * dt
        px, pz = self._pad_pos()
        x, z, dx, dz = self._puck_seen()
        vx = (math.sin(self.counter / 2.0) * 0.03 + (x - px) * 0.005) * self.p.speed
        vz = (math.sin(self.counter / 4.0) * 0.01 + (35 - pz) * 0.005) * self.p.speed
        self._set_vel(vx, vz)

    def _smart_idle(self, dt):
        """C: _smart_really_idle() + error de punteria."""
        self.counter += 0.005 * dt
        px, pz = self._pad_pos()
        x, z, dx, dz = self._puck_seen()
        if dz == 0:
            self._dumb_idle(dt)
            return
        if dz > 0:
            tx = self._predict_x(x, z, dx, dz, 35) + self.error_bias
            vx = ((tx - px) * 0.005) * self.p.speed
        else:
            vx = (0 - px) * 0.005
        vz = ((35 - pz) * 0.005) * self.p.speed
        self._set_vel(vx, vz)

    def _idle(self, dt):
        if self.p.idle == "smart":
            self._smart_idle(dt)
        else:
            self._dumb_idle(dt)
        x, z, dx, dz = self._puck_seen()
        approaching = self.puck.dz * self.sign >= 0        # (nuevo) no perseguir un disco que se aleja
        if z > 20 and approaching and self.puck.state == "play":   # C: if (puck_z > 20) -> BACKUP
            self._plan_shot()
            self.state = self.BACKUP

    def _plan_shot(self):
        """Elige a donde tirar: recto al hueco o a banda."""
        human = self.board.pads[1 if self.side == 2 else 2]
        away = -12.0 if human.x > 0 else 12.0
        self.shot_target_x = away + self.error_bias * 0.5
        roll = self.rng.random()
        self.shot_bank = self.force_bank or (roll < self.p.bank_shot)
        self.force_bank = False

    def _planned_pad_x(self, x, z):
        """x de la pala en z=38 para que la linea pala->disco apunte al objetivo."""
        tz = -GOAL_Z
        tx = self.shot_target_x
        if self.shot_bank:
            wall = -(AREA_X2 - PUCK_W / 2) if tx > 0 else (AREA_X2 - PUCK_W / 2)
            tx = 2 * wall - tx
        denom = (tz - z)
        if abs(denom) < 1e-6:
            return x
        k = (38.0 - z) / (-denom)
        px = x - k * (tx - x)
        lo, hi = self._pad_x_limits()
        return clamp(px, lo, hi)

    def _backup(self, dt):
        """C: dumb_backup() / smart_backup() + colocacion planificada."""
        px, pz = self._pad_pos()
        x, z, dx, dz = self._puck_seen()
        if self.puck.state != "play" or (self.puck.dz * self.sign < 0 and z < 20):
            self.state = self.IDLE
            return
        speed = 0.03 * self.p.speed
        if self.p.backup == "smart" and abs(dx) > 0.15:
            tx = self._predict_x(x, z, dx, dz, 35) + self.error_bias
            self._move_towards(tx, 35, speed, dt)
            return
        target_x = px
        if self.p.plan_shots:
            xr, zr, _, _ = self._puck_now()
            target_x = self._planned_pad_x(xr, zr)
        if self._move_towards(target_x, 38, speed, dt):
            self.state = self.AIM
        elif self.p.plan_shots and abs(pz - 38) < 0.5 and z < 26 and dz < 0:
            self.state = self.AIM                      # el disco se escapa: golpear ya

    def _aim(self, dt):
        """C: dumb_aim() + amago."""
        px, pz = self._pad_pos()
        x, z, dx, dz = self._puck_now()
        vx, vz = x - px, z - pz
        if vz > -0.4:                                      # C: if (dz > -0.4) -> BACKUP
            self.state = self.BACKUP
            return
        if z < 14 or self.puck.state != "play":            # (nuevo) el disco ya se fue: no golpear al aire
            self.state = self.IDLE
            return
        if (self.force_feint or self.rng.random() < self.p.feint) and z > 24:
            self.force_feint = False
            self.feint_phase = 0
            self.feint_timer.start(70)
            self.state = self.FEINT
            return
        dist = math.hypot(vx, vz)
        k = self.p.hit_power * 0.1 / dist
        self._set_vel(vx * k, vz * k)
        self.state = self.HIT

    def _feint(self, dt):
        px, pz = self._pad_pos()
        x, z, dx, dz = self._puck_now()
        if z < 21:
            self.state = self.IDLE
            return
        if self.feint_phase == 0:
            vx, vz = x - px, z - pz
            dist = max(0.1, math.hypot(vx, vz))
            k = self.p.hit_power * 0.04 / dist
            self._set_vel(vx * k, vz * k)
            if self.feint_timer.update(dt):
                self.feint_phase = 1
                self.feint_timer.start(90)
        else:
            self._set_vel(0.0, 0.0)
            if self.feint_timer.update(dt):
                self.state = self.AIM

    def _hit(self, dt):
        """C: dumb_hit()."""
        px, pz = self._pad_pos()
        if pz < 21:
            self.state = self.IDLE

    # -- saques --------------------------------------------------------------------
    def _enter_serve(self):
        kinds = list(self.p.serves.items())
        total = sum(w for _, w in kinds) or 1.0
        r = self.rng.random() * total
        kind = kinds[-1][0]
        for k, w in kinds:
            r -= w
            if r <= 0:
                kind = k
                break
        self.serve_kind = kind
        self.state = self.SERVE
        if kind == "swirl":
            self.swirl = {"state": 1, "counter": 0.0, "speed": 0.0, "limit": 0.0, "corner": 0}
        elif kind == "bank":
            self.force_bank = True
        elif kind == "feint":
            self.force_feint = True

    def _serve(self, dt):
        if self.serve_kind != "swirl":
            self._plan_shot()
            self.state = self.BACKUP                      # C: dumb_serve()
            return
        self._serve_swirl(dt)

    def _serve_swirl(self, dt):
        """C: _arcana_serve().  El disco flota, da cinco vueltas y sale disparado."""
        sw = self.swirl
        puck = self.puck
        self._move_towards(0, 35, 0.005, dt)
        if puck.state != "play":
            self.state = self.IDLE
            return
        if sw["state"] == 1:
            target_z = (10 - SWIRL_RADIUS) * self.sign
            if puck.move_towards(0.0, target_z, FLOAT_SPEED, dt):
                sw["state"] = 2
                sw["counter"] = -math.pi / 2
                sw["speed"] = 0.005
                sw["corner"] = 1 if self.rng.random() < 0.5 else 0
                sw["limit"] = 2 * math.pi * 5 + math.pi + (math.pi / 4 if sw["corner"] == 1 else 0.0)
            puck.set_velocity(0.0, 0.0)
        elif sw["state"] == 2:
            sw["speed"] += dt * COUNTER_ACC / 1000.0
            sw["counter"] += sw["speed"] * dt
            c = sw["counter"]
            if c > sw["limit"]:
                c = sw["limit"]
                sw["state"] = 3
            puck.set_position(SWIRL_RADIUS * math.cos(c), (10 + SWIRL_RADIUS * math.sin(c)) * self.sign)
            puck.set_velocity(0.0, 0.0)
        else:
            if sw["corner"] == 1:
                puck.set_velocity(RELEASE_SPEED * 0.477, RELEASE_SPEED * -0.879 * self.sign)
            else:
                puck.set_velocity(RELEASE_SPEED * -0.140, RELEASE_SPEED * -0.990 * self.sign)
            puck.trail.clear()
            self.board.events.append(("swirl_release", self.side))
            self.state = self.IDLE

    @property
    def swirling(self):
        return self.state == self.SERVE and self.serve_kind == "swirl" and self.swirl["state"] in (1, 2)


# ---- 11. OPPONENTS ------------------------------------------------------------
@dataclass
class OpponentSpec:
    key: str
    name: str
    title: str
    color: tuple
    portrait: Callable
    ai: AIParams
    taunts: dict
    description: str = ""


OPPONENTS: dict = {}


def _add_opponent(spec: OpponentSpec):
    OPPONENTS[spec.key] = spec


_add_opponent(OpponentSpec(
    key="bit", name="Bit", title="Robot de limpieza. Nadie le pidio opinion.", color=(120, 200, 255),
    portrait=Art.portrait_bit,
    ai=AIParams(speed=1.8, hit_power=1.6, reaction_ms=260, aim_error=4.5, predict=False,
                idle="smart", backup="dumb", serves={"dumb": 1.0}, celebrate_ms=900),
    taunts={"intro": ["BIP. Modo torneo activado.", "Calculando... error de calculo.", "Mi firmware es de 2002."],
            "win_point": ["BIP BIP. Punto registrado.", "Aspirando tu moral.", "Esto no estaba en mi manual."],
            "lose_point": ["ERROR 404: disco no encontrado.", "Reiniciando...", "Mi sensor dice que eso es trampa."],
            "win_game": ["Victoria. Procedo a limpiar el tablero.", "He ganado. Nadie lo esperaba. Yo tampoco."],
            "lose_game": ["Derrota aceptada. Volviendo a la base de carga.", "Al menos el tablero quedo limpio."]},
    description="Persigue el disco sin predecir rebotes. El tutorial con patas."))

_add_opponent(OpponentSpec(
    key="tux", name="Tux", title="El pinguino. Parametros del TuxPuck original.", color=(255, 200, 60),
    portrait=Art.portrait_tux,
    ai=AIParams(speed=3.0, hit_power=2.5, reaction_ms=120, aim_error=2.2, predict=True,
                idle="smart", backup="dumb", serves={"dumb": 1.0}, celebrate_ms=800),
    taunts={"intro": ["Cuak. Que gane el mejor.", "Llevo desde 2002 esperando una revancha.", "Sin hielo no hay miedo."],
            "win_point": ["Cuak!", "Eso es nadar en aguas frias.", "Un punto mas para el pinguino."],
            "lose_point": ["Hmpf. Suerte.", "Eso ha sido... inesperado.", "Mis aletas resbalan."],
            "win_game": ["Gane. Vuelve cuando sepas patinar.", "Linux 1, humanos 0."],
            "lose_game": ["Buen partido. Arcana te espera.", "Me has ganado. Respeto."]},
    description="Predice rebotes y remata regular. Aplaude y abuchea."))

_add_opponent(OpponentSpec(
    key="arcana", name="Arcana", title="La hechicera del saque en espiral.", color=(170, 120, 255),
    portrait=Art.portrait_arcana,
    ai=AIParams(speed=2.5, hit_power=2.5, reaction_ms=100, aim_error=2.0, predict=True,
                idle="dumb", backup="smart", serves={"dumb": 0.5, "swirl": 0.5}, celebrate_ms=600),
    taunts={"intro": ["Mira bien el disco. Ahora no lo mires.", "Mis saques no son de este mundo.", "Un hechizo por punto."],
            "win_point": ["Ilusion y resultado.", "Lo viste venir? No lo creo.", "El disco obedece."],
            "lose_point": ["Un desliz del destino.", "Interesante. Sigue.", "El orbe se ha nublado."],
            "win_game": ["El cafe es mio esta noche.", "Vuelve con mas magia."],
            "lose_game": ["Has roto el hechizo.", "Vera Volt no sera tan amable."]},
    description="Saque en espiral al 50 %. Se coloca antes de que llegues."))

_add_opponent(OpponentSpec(
    key="vera", name="Vera Volt", title="Camarera ciborg. Brazo hidraulico.", color=(255, 80, 180),
    portrait=Art.portrait_vera,
    ai=AIParams(speed=3.4, hit_power=3.0, reaction_ms=80, aim_error=1.4, predict=True,
                idle="smart", backup="smart", serves={"dumb": 0.5, "bank": 0.5}, bank_shot=0.4,
                plan_shots=True, celebrate_ms=500),
    taunts={"intro": ["Que te pongo? Una paliza?", "Mi brazo esta calibrado.", "Las bandas son mis amigas."],
            "win_point": ["A banda y dentro.", "Pssst. Punto.", "Mi brazo no se cansa."],
            "lose_point": ["Vaya. Hoy el hidraulico va lento.", "Tomo nota.", "Eso no se repetira."],
            "win_game": ["La cuenta la pagas tu.", "Cierro el bar. Fuera."],
            "lose_game": ["Vale, invito yo.", "Nullbyte te va a comer. Suerte."]},
    description="Tira a banda a proposito. Calcula el rebote para esquivarte."))

_add_opponent(OpponentSpec(
    key="nullbyte", name="Nullbyte", title="Un fallo de segmentacion con cara.", color=(90, 255, 140),
    portrait=Art.portrait_nullbyte,
    ai=AIParams(speed=3.8, hit_power=3.2, reaction_ms=50, aim_error=1.0, predict=True,
                idle="smart", backup="smart", serves={"dumb": 0.4, "bank": 0.3, "feint": 0.3}, bank_shot=0.35,
                feint=0.35, plan_shots=True, celebrate_ms=400),
    taunts={"intro": ["01001000 01101111 01101100 01100001.", "No soy un bug. Soy una feature.", "core dumped."],
            "win_point": ["SIGSEGV en tu defensa.", "null pointer: tu pala.", "stack overflow."],
            "lose_point": ["kernel panic...", "eso fue un race condition.", "rm -rf mi dignidad"],
            "win_game": ["Proceso terminado. Exit 1.", "git push --force a tu moral."],
            "lose_game": ["Segmentation fault. Fin.", "El Daemon no perdona. Yo si."]},
    description="Amaga: se lanza, frena y golpea en el segundo impulso."))

_add_opponent(OpponentSpec(
    key="daemon", name="Daemon", title="Lo que vive en /dev/null.", color=(255, 90, 40),
    portrait=Art.portrait_daemon,
    ai=AIParams(speed=4.2, hit_power=3.6, reaction_ms=30, aim_error=0.5, predict=True,
                idle="smart", backup="smart", serves={"dumb": 0.3, "bank": 0.35, "feint": 0.35}, bank_shot=0.45,
                feint=0.3, plan_shots=True, celebrate_ms=300),
    taunts={"intro": ["Nadie sale de aqui con el disco.", "Yo no juego. Yo ejecuto.", "Tu turno es un proceso zombi."],
            "win_point": ["Devorado.", "kill -9.", "Siguiente."],
            "lose_point": ["...", "Un fallo en mi demonio.", "Eso lo has soñado."],
            "win_game": ["Vuelve cuando seas root.", "El cafe cierra. Para siempre."],
            "lose_game": ["Imposible... has ganado.", "El Cafe es tuyo. Por ahora."]},
    description="Prediccion casi perfecta, bandas y amagos. Sus golpes tambien pierden el 40 %: tiros blandos en diagonal lo descolocan."))


# ---- 12. UI -------------------------------------------------------------------
UI_BORDER = (170, 140, 110)        # C: BORDER_COLOR (menu.c)
UI_HEADER = (90, 230, 160)
UI_SELECTED = (255, 150, 60)
UI_TEXT = (240, 236, 228)
UI_DIM = (150, 145, 135)


def wrap_text(text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if font.size(test)[0] <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_panel(surf, rect, alpha=150, border=UI_BORDER, radius=10):
    panel = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel, (0, 0, 0, alpha), panel.get_rect(), border_radius=radius)
    surf.blit(panel, rect.topleft)
    pygame.draw.rect(surf, border, rect, 3, border_radius=radius)          # C: BORDER_SIZE 5 -> 3
    pygame.draw.rect(surf, scale_color(border, 0.5), rect.inflate(6, 6), 1, border_radius=radius + 3)


def draw_bubble(surf, text, anchor, max_w=300, size=22, color=(30, 24, 30), fg=(250, 245, 240), alpha=235):
    font = get_font(size)
    lines = wrap_text(text, font, max_w - 24)
    w = max(font.size(l)[0] for l in lines) + 24
    h = len(lines) * font.get_linesize() + 18
    ax, ay = anchor
    x = int(clamp(ax - w / 2, 8, LOGICAL_W - w - 8))
    y = int(ay - h - 16)
    bub = pygame.Surface((w, h + 16), pygame.SRCALPHA)
    pygame.draw.rect(bub, with_alpha(color, alpha), (0, 0, w, h), border_radius=12)
    pygame.draw.rect(bub, with_alpha((255, 255, 255), 90), (0, 0, w, h), 2, border_radius=12)
    tail_x = int(clamp(ax - x, 20, w - 20))
    pygame.draw.polygon(bub, with_alpha(color, alpha), [(tail_x - 10, h - 1), (tail_x + 10, h - 1), (tail_x, h + 14)])
    for i, l in enumerate(lines):
        img = font.render(l, True, fg)
        bub.blit(img, (12, 9 + i * font.get_linesize()))
    surf.blit(bub, (x, y))


class MenuItem:
    def __init__(self, label, action=None, values=None, index=0, fmt=None, on_change=None, header=False,
                 hint=None):
        self.label = label
        self.action = action
        self.values = values
        self.index = index
        self.fmt = fmt or (lambda v: str(v))
        self.on_change = on_change
        self.header = header
        self.hint = hint
        self.rect = pygame.Rect(0, 0, 0, 0)

    @property
    def value(self):
        return self.values[self.index] if self.values else None

    def text(self):
        if self.values:
            return f"{self.label}:  < {self.fmt(self.value)} >"
        return self.label

    def cycle(self, step):
        if not self.values:
            return
        self.index = (self.index + step) % len(self.values)
        if self.on_change:
            self.on_change(self.value)


class Menu:
    """Menu vertical con teclado (flechas/WASD, Enter, Esc) y raton (hover, clic)."""

    def __init__(self, items, title=None, size=34, audio=None, center=(LOGICAL_W // 2, LOGICAL_H // 2),
                 min_width=320, hint_size=20):
        self.items = items
        self.title = title
        self.size = size
        self.audio = audio
        self.center = center
        self.min_width = min_width
        self.hint_size = hint_size
        self.selected = 0
        self.rect = pygame.Rect(0, 0, 10, 10)
        self._fix_selection(1)

    def _fix_selection(self, step):
        n = len(self.items)
        for _ in range(n):
            if not self.items[self.selected].header:
                return
            self.selected = (self.selected + step) % n

    def move(self, step):
        self.selected = (self.selected + step) % len(self.items)
        self._fix_selection(step)
        if self.audio:
            self.audio.play("menu_move")

    def handle_event(self, e):
        """Devuelve ('select', item) | ('back', None) | None."""
        if e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_UP, pygame.K_w):
                self.move(-1)
            elif e.key in (pygame.K_DOWN, pygame.K_s):
                self.move(1)
            elif e.key in (pygame.K_LEFT, pygame.K_a):
                self._cycle(-1)
            elif e.key in (pygame.K_RIGHT, pygame.K_d):
                self._cycle(1)
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                return self._activate()
            elif e.key == pygame.K_ESCAPE:
                return ("back", None)
        elif e.type == pygame.MOUSEMOTION:
            for i, it in enumerate(self.items):
                if not it.header and it.rect.collidepoint(e.pos):
                    if i != self.selected:
                        self.selected = i
                        if self.audio:
                            self.audio.play("menu_move", 0.5)
        elif e.type == pygame.MOUSEBUTTONDOWN:
            if e.button == 1:
                for i, it in enumerate(self.items):
                    if not it.header and it.rect.collidepoint(e.pos):
                        self.selected = i
                        return self._activate()
            elif e.button == 3:
                return ("back", None)
            elif e.button == 4:
                self.move(-1)
            elif e.button == 5:
                self.move(1)
        return None

    def _cycle(self, step):
        it = self.items[self.selected]
        if it.values:
            it.cycle(step)
            if self.audio:
                self.audio.play("menu_move")

    def _activate(self):
        it = self.items[self.selected]
        if self.audio:
            self.audio.play("menu_select")
        if it.values and it.action is None:
            it.cycle(1)
            return ("change", it)
        if it.action:
            it.action()
        return ("select", it)

    def draw(self, surf, t=0.0):
        font = get_font(self.size, True)
        lh = font.get_linesize() + 12
        texts = [it.text() for it in self.items]
        w = max([font.size(s)[0] for s in texts] + [self.min_width]) + 80
        h = len(self.items) * lh + 50
        tfont = None
        if self.title:
            tfont = get_font(self.size + 10, True)
            h += tfont.get_linesize() + 14
        cx, cy = self.center
        self.rect = pygame.Rect(int(cx - w / 2), int(cy - h / 2), int(w), int(h))
        draw_panel(surf, self.rect, 170)
        y = self.rect.top + 24
        if self.title:
            img = neon_text(self.title, self.size + 10, (255, 120, 180), (255, 60, 140))
            surf.blit(img, (cx - img.get_width() // 2, y - 10))
            y += tfont.get_linesize() + 14
        for i, it in enumerate(self.items):
            if it.header:
                color = UI_HEADER
            elif i == self.selected:
                color = UI_SELECTED
            else:
                color = UI_TEXT
            img = font.render(texts[i], True, color)
            x = cx - img.get_width() // 2
            it.rect = pygame.Rect(self.rect.left + 10, y - 4, self.rect.width - 20, lh)
            if i == self.selected and not it.header:
                hl = pygame.Surface((it.rect.width, it.rect.height), pygame.SRCALPHA)
                pygame.draw.rect(hl, (255, 150, 60, 36 + int(14 * math.sin(t / 150.0))), hl.get_rect(), border_radius=6)
                surf.blit(hl, it.rect.topleft)
                arrow = font.render(">", True, UI_SELECTED)
                surf.blit(arrow, (x - 36 + int(4 * math.sin(t / 120.0)), y))
            sh = font.render(texts[i], True, (0, 0, 0))
            surf.blit(sh, (x + 2, y + 2))
            surf.blit(img, (x, y))
            y += lh
        hint = self.items[self.selected].hint
        if hint:
            hf = get_font(self.hint_size)
            lines = wrap_text(hint, hf, self.rect.width - 40)
            hy = self.rect.bottom + 10
            for l in lines:
                shadow_text(surf, l, (cx, hy), self.hint_size, UI_DIM, "center")
                hy += hf.get_linesize()


class Scoreboard:
    """Marcador de palotes (scoreboard.c), nombres, peloteo, velocimetro, barra de raton."""

    POS = (int(460 * S) - 22, 6)                      # C: SCOREBOARD_POSITION 460
    SIZE = (int(152 * S) + 50, int(85 * S) + 20)

    def __init__(self):
        self.mousebar_alpha = 0.0
        self.mousebar_counter = 0.0
        self.mouse_speed = 5
        self.pop = {1: 0.0, 2: 0.0}

    def show_mousebar(self, speed):
        self.mouse_speed = speed
        self.mousebar_alpha = 1.0
        self.mousebar_counter = 0.0

    def point_scored(self, who):
        self.pop[who] = 1.0

    def update(self, dt):
        if self.mousebar_alpha > 0:                       # C: MOUSEBAR_TIMEOUT 2000, fade 0.001/ms
            self.mousebar_counter += dt
            if self.mousebar_counter > 2000:
                self.mousebar_alpha = max(0.0, self.mousebar_alpha - dt * 0.001)
        for k in self.pop:
            self.pop[k] = max(0.0, self.pop[k] - dt / 500.0)

    def draw(self, surf, names, score, target, rally, speed_units, elapsed_ms, alpha=255, colors=None):
        x0, y0 = self.POS
        w, h = self.SIZE
        rect = pygame.Rect(x0, y0, w, h)
        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(panel, (10, 8, 14, 185), panel.get_rect(), border_radius=8)
        pygame.draw.rect(panel, with_alpha(UI_BORDER, 255), panel.get_rect(), 2, border_radius=8)
        colors = colors or {1: Art.PAD_COLORS[1], 2: Art.PAD_COLORS[2]}
        for who in (1, 2):
            row = 12 + (who - 1) * 44
            pop = self.pop[who]
            name = names[who]
            nf = get_font(24, True)
            col = mix_color(colors[who], (255, 255, 255), pop)
            panel.blit(nf.render(name[:10], True, col), (12, row))
            # palotes: grupos de 5 (4 verticales + 1 cruzado), C: _blit_point()
            pts = score[who]
            gx = 128
            for p in range(pts):
                grp, idx = divmod(p, 5)
                bx = gx + grp * 34 + idx * 6
                if idx < 4:
                    pygame.draw.line(panel, (255, 255, 255), (bx, row + 2), (bx, row + 24), 3)
                else:
                    pygame.draw.line(panel, (255, 230, 120), (gx + grp * 34 - 3, row + 20), (gx + grp * 34 + 22, row + 6), 3)
            sf = get_font(26, True)
            num = sf.render(str(pts), True, col)
            panel.blit(num, (w - 16 - num.get_width(), row - 2 - int(6 * pop)))
        pygame.draw.line(panel, (70, 60, 50), (10, 98), (w - 10, 98), 1)
        info = get_font(18)
        kmh = speed_units * KMH_PER_UNIT_MS
        panel.blit(info.render(f"peloteo {rally}", True, UI_DIM), (12, 105))
        panel.blit(info.render(f"{kmh:3.0f} km/h", True, (255, 200, 120) if kmh > 60 else UI_DIM), (100, 105))
        if target:
            panel.blit(info.render(f"a {target}", True, UI_DIM), (176, 105))
        secs = int(elapsed_ms // 1000)
        panel.blit(info.render(f"{secs // 60:02d}:{secs % 60:02d}", True, UI_DIM), (w - 54, 105))
        if alpha < 255:
            panel.set_alpha(alpha)
        surf.blit(panel, rect.topleft)
        # velocimetro
        bar = pygame.Rect(x0, y0 + h + 6, w, 8)
        pygame.draw.rect(surf, (30, 26, 34), bar, border_radius=4)
        k = clamp(speed_units / 0.5, 0, 1)
        pygame.draw.rect(surf, mix_color((90, 200, 120), (255, 80, 60), k), (bar.x, bar.y, int(bar.w * k), bar.h), border_radius=4)
        # barra de velocidad del raton (scoreboard.c: mousebar)
        if self.mousebar_alpha > 0:
            mb = pygame.Rect(x0, y0 + h + 20, w, 26)
            s = pygame.Surface(mb.size, pygame.SRCALPHA)
            a = int(self.mousebar_alpha * 255)
            pygame.draw.rect(s, (0, 0, 0, a // 2), s.get_rect(), border_radius=6)
            pygame.draw.rect(s, (0, 0, 160, a), (8, 6, int((mb.w - 16) * self.mouse_speed / 10.0), 14), border_radius=4)
            pygame.draw.rect(s, (200, 200, 255, a), (8, 6, mb.w - 16, 14), 1, border_radius=4)
            lab = get_font(18).render(f"raton {self.mouse_speed}/10  (F5/F6)", True, (255, 255, 255))
            lab.set_alpha(a)
            s.blit(lab, (mb.w // 2 - lab.get_width() // 2, 5))
            surf.blit(s, mb.topleft)


class Fade:
    """Transicion de fundido (video_fade / alpha del original)."""

    def __init__(self, duration=600, start=1.0, end=0.0):
        self.timer = Timer(duration)
        self.start, self.end = start, end

    def reset(self, duration, start, end):
        self.timer.start(duration)
        self.start, self.end = start, end

    def update(self, dt):
        return self.timer.update(dt)

    @property
    def value(self):
        return lerp(self.start, self.end, smoothstep(self.timer.t))

    @property
    def done(self):
        return self.timer.done

    def draw(self, surf, color=(0, 0, 0)):
        v = self.value
        if v <= 0.002:
            return
        ov = pygame.Surface((LOGICAL_W, LOGICAL_H))
        ov.fill(color)
        ov.set_alpha(int(255 * v))
        surf.blit(ov, (0, 0))


class Toast:
    """Mensaje grande centrado que aparece y se desvanece (PUNTO, SAQUE...)."""

    def __init__(self):
        self.text = ""
        self.color = (255, 255, 255)
        self.timer = Timer(1, running=False)
        self.timer.elapsed = 1
        self.size = 64

    def show(self, text, color=(255, 255, 255), ms=1200, size=64):
        self.text, self.color, self.size = text, color, size
        self.timer.start(ms)

    def update(self, dt):
        self.timer.update(dt)

    def draw(self, surf, y=LOGICAL_H // 2 - 60):
        if self.timer.done or not self.text:
            return
        t = self.timer.t
        a = 1.0 if t < 0.7 else (1 - (t - 0.7) / 0.3)
        scale = 1.0 + 0.15 * (1 - smoothstep(min(1.0, t * 5)))
        img = neon_text(self.text, int(self.size * scale), self.color, self.color)
        img.set_alpha(int(255 * a))
        surf.blit(img, (LOGICAL_W // 2 - img.get_width() // 2, y - img.get_height() // 2))


# ---- 13. Scenes ----------------------------------------------------------------
class Scene:
    overlay = False          # True: se dibuja encima de la escena anterior
    grab_mouse = False       # True: captura el raton (modo relativo)

    def __init__(self, app: "App"):
        self.app = app
        self.t = 0.0

    def enter(self):
        pass

    def exit(self):
        pass

    def resume(self):
        pass

    def handle_event(self, e):
        pass

    def update(self, dt):
        self.t += dt

    def draw(self, surf):
        pass


def draw_dim(surf, alpha=140):
    ov = pygame.Surface((LOGICAL_W, LOGICAL_H), pygame.SRCALPHA)
    ov.fill((0, 0, 0, alpha))
    surf.blit(ov, (0, 0))


def input_label(v):
    return {"mouse": "raton", "wasd": "teclado WASD", "arrows": "teclado flechas", "gamepad": "mando"}.get(v, v)


@dataclass
class MatchConfig:
    mode: str = "quick"                 # quick | tournament | versus | practice | demo
    opponent: Optional[str] = "tux"
    opponent_bottom: Optional[str] = None   # solo demo (IA abajo)
    points: int = DEFAULT_POINTS
    mutators: Mutators = field(default_factory=Mutators)
    p1_scheme: str = "mouse"
    p2_scheme: str = "arrows"
    nightmare: bool = False
    tournament_index: int = 0

    def clone(self) -> "MatchConfig":
        d = asdict(self)
        d["mutators"] = Mutators(**d["mutators"])
        return MatchConfig(**d)


# -- Titulo -------------------------------------------------------------------------
class TitleScene(Scene):
    CREDITS = ["un remake de TuxPuck (Jacob Kroon, 2001-2002)", "graficos originales de m0ns00n",
               "reescrito desde cero en Python, 2026", "pulsa Enter o haz clic para empezar"]

    def enter(self):
        self.puck = Puck()
        self.puck.set_position(-8, -10)
        self.puck.set_velocity(0.09, 0.13)
        self.puck.alive = True
        self.idle = 0.0
        self.credit_i = 0
        self.credit_t = 0.0
        self.fade = Fade(1200, 1.0, 0.0)
        self.app.audio.start_music()

    def handle_event(self, e):
        if e.type == pygame.KEYDOWN and e.key not in (pygame.K_f, pygame.K_m, pygame.K_F3, pygame.K_F12):
            if e.key == pygame.K_ESCAPE:
                self.app.quit()
            else:
                self.app.replace(MainMenuScene(self.app))
        elif e.type == pygame.MOUSEBUTTONDOWN:
            self.app.replace(MainMenuScene(self.app))

    def update(self, dt):
        super().update(dt)
        self.fade.update(dt)
        self.idle += dt
        self.credit_t += dt
        if self.credit_t > 3200:
            self.credit_t = 0.0
            self.credit_i = (self.credit_i + 1) % len(self.CREDITS)
        steps = int(dt // 4) + 1
        for _ in range(steps):
            self.puck.snapshot()
            bx, bz = self.puck.move_puck(4)
            if bx or bz:
                self.app.audio.play("tick", 0.3)
        self.puck.push_trail()
        if self.idle > 40000 and not self.app.headless:
            self.idle = 0.0
            self.app.push(DemoScene(self.app, MatchConfig(mode="demo")))

    def draw(self, surf):
        surf.blit(self.app.background, (0, 0))
        Art.draw_puck(surf, self.app.persp, self.puck.x, self.puck.z, 255, self.puck.speed, trail=self.puck.trail)
        logo = neon_text("TUXPUCK", 150, (255, 100, 170), (255, 40, 130))
        surf.blit(logo, (LOGICAL_W // 2 - logo.get_width() // 2, 150 + int(6 * math.sin(self.t / 600.0))))
        sub = neon_text("P Y T H O N", 48, (120, 230, 255), (40, 180, 255))
        surf.blit(sub, (LOGICAL_W // 2 - sub.get_width() // 2, 300))
        shadow_text(surf, f"v{VERSION}", (LOGICAL_W - 16, LOGICAL_H - 30), 20, UI_DIM, "right")
        k = self.credit_t / 3200.0
        a = int(255 * (1 - abs(2 * k - 1)) ** 0.6)
        shadow_text(surf, self.CREDITS[self.credit_i], (LOGICAL_W // 2, LOGICAL_H - 80), 28, (235, 210, 170), "center", alpha=a)
        self.fade.draw(surf)


# -- Menu principal ---------------------------------------------------------------
class MainMenuScene(Scene):
    def enter(self):
        app = self.app
        prof = app.profile
        tb = prof.stats.tournament_best
        items = [
            MenuItem("Partida rapida", lambda: app.push(OpponentSelectScene(app, "quick")),
                     hint="Elige rival y puntos. Cuenta para las estadisticas."),
            MenuItem("Torneo", lambda: app.push(TournamentScene(app)),
                     hint=f"La escalera de seis rivales. Mejor marca: {tb}/6."),
            MenuItem("Versus local", lambda: app.push(VersusSetupScene(app)),
                     hint="Dos humanos en la misma maquina."),
            MenuItem("Practica", lambda: app.push(OpponentSelectScene(app, "practice")),
                     hint="Sin fin, con la trayectoria del disco dibujada."),
            MenuItem("Demo", lambda: app.push(DemoScene(app, MatchConfig(mode="demo"))),
                     hint="IA contra IA. Cualquier tecla vuelve."),
            MenuItem("Arcade", lambda: app.push(ArcadeScene(app)),
                     hint="Mutadores: velocidad, discos, palas, niebla, efecto."),
            MenuItem("Ajustes", lambda: app.push(SettingsScene(app))),
            MenuItem("Estadisticas", lambda: app.push(StatsScene(app))),
            MenuItem("Ayuda", lambda: app.push(HelpScene(app))),
            MenuItem("Creditos", lambda: app.push(CreditsScene(app))),
            MenuItem("Salir", app.quit),
        ]
        self.menu = Menu(items, "TUXPUCK", 30, app.audio, center=(LOGICAL_W // 2, LOGICAL_H // 2 + 10))
        app.audio.start_music()

    def resume(self):
        self.enter()

    def handle_event(self, e):
        res = self.menu.handle_event(e)
        if res and res[0] == "back":
            self.app.replace(TitleScene(self.app))

    def draw(self, surf):
        surf.blit(self.app.background, (0, 0))
        draw_dim(surf, 110)
        self.menu.draw(surf, self.t)
        name = self.app.profile.settings.player_name
        shadow_text(surf, f"perfil: {name}", (16, LOGICAL_H - 30), 20, UI_DIM)


# -- Seleccion de rival -------------------------------------------------------------
class OpponentSelectScene(Scene):
    def __init__(self, app, mode="quick"):
        super().__init__(app)
        self.mode = mode
        self.index = 1
        self.points_i = POINT_CHOICES.index(app.profile.mutators.points) if app.profile.mutators.points in POINT_CHOICES else 2
        self.anim = 0.0
        self.rng = random.Random(3)

    def handle_event(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_LEFT, pygame.K_a):
                self.index = (self.index - 1) % len(OPPONENT_KEYS)
                self.app.audio.play("menu_move")
                self.anim = 1.0
            elif e.key in (pygame.K_RIGHT, pygame.K_d):
                self.index = (self.index + 1) % len(OPPONENT_KEYS)
                self.app.audio.play("menu_move")
                self.anim = 1.0
            elif e.key in (pygame.K_UP, pygame.K_w):
                self.points_i = (self.points_i + 1) % len(POINT_CHOICES)
                self.app.audio.play("menu_move")
            elif e.key in (pygame.K_DOWN, pygame.K_s):
                self.points_i = (self.points_i - 1) % len(POINT_CHOICES)
                self.app.audio.play("menu_move")
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self.start()
            elif e.key == pygame.K_ESCAPE:
                self.app.pop()
        elif e.type == pygame.MOUSEBUTTONDOWN:
            if e.button == 1:
                x, y = e.pos
                if y > LOGICAL_H - 120:
                    self.start()
                elif x < LOGICAL_W // 3:
                    self.index = (self.index - 1) % len(OPPONENT_KEYS)
                    self.anim = 1.0
                elif x > LOGICAL_W * 2 // 3:
                    self.index = (self.index + 1) % len(OPPONENT_KEYS)
                    self.anim = 1.0
                else:
                    self.points_i = (self.points_i + 1) % len(POINT_CHOICES)
            elif e.button == 3:
                self.app.pop()

    def start(self):
        self.app.audio.play("menu_select")
        prof = self.app.profile
        prof.mutators.points = POINT_CHOICES[self.points_i]
        prof.dirty = True
        cfg = MatchConfig(mode=self.mode, opponent=OPPONENT_KEYS[self.index], points=prof.mutators.points,
                          mutators=Mutators(**asdict(prof.mutators)) if self.mode == "quick" else Mutators(),
                          p1_scheme=prof.settings.p1_input)
        self.app.push(MatchScene(self.app, cfg))

    def update(self, dt):
        super().update(dt)
        self.anim = max(0.0, self.anim - dt / 250.0)

    def draw(self, surf):
        app = self.app
        surf.blit(app.background, (0, 0))
        draw_dim(surf, 150)
        key = OPPONENT_KEYS[self.index]
        spec = OPPONENTS[key]
        title = "PRACTICA" if self.mode == "practice" else "ELIGE RIVAL"
        img = neon_text(title, 54, (255, 120, 180), (255, 60, 140))
        surf.blit(img, (LOGICAL_W // 2 - img.get_width() // 2, 30))
        # retrato grande
        port = pygame.Surface((PORTRAIT_W, PORTRAIT_H), pygame.SRCALPHA)
        eye = (math.sin(self.t / 700.0) * 0.6, math.cos(self.t / 900.0) * 0.3)
        spec.portrait(port, self.t, "idle", eye, self.rng)
        big = pygame.transform.smoothscale(port, (int(PORTRAIT_W * 1.5), int(PORTRAIT_H * 1.5)))
        px = LOGICAL_W // 2 - big.get_width() // 2 + int(40 * self.anim * math.sin(self.anim * 6))
        py = 120
        frame = pygame.Rect(px - 10, py - 10, big.get_width() + 20, big.get_height() + 20)
        draw_panel(surf, frame, 120, spec.color)
        surf.blit(big, (px, py))
        # flechas
        for sgn, ch in ((-1, "<"), (1, ">")):
            x = LOGICAL_W // 2 + sgn * 320 + int(sgn * 6 * math.sin(self.t / 200.0))
            shadow_text(surf, ch, (x, 260), 90, UI_SELECTED, "center", True)
        # nombre y datos
        shadow_text(surf, f"{self.index + 1}/{len(OPPONENT_KEYS)}  {spec.name}", (LOGICAL_W // 2, 500), 48, spec.color, "center", True)
        shadow_text(surf, spec.title, (LOGICAL_W // 2, 545), 26, UI_TEXT, "center")
        font = get_font(22)
        y = 578
        for line in wrap_text(spec.description, font, 700):
            shadow_text(surf, line, (LOGICAL_W // 2, y), 22, UI_DIM, "center")
            y += font.get_linesize()
        rec = app.profile.stats.opponent(key)
        ai = spec.ai
        shadow_text(surf, f"velocidad {ai.speed:.1f}   potencia {ai.hit_power:.1f}   reaccion {ai.reaction_ms:.0f} ms"
                    f"   |   jugadas {rec['played']}  ganadas {rec['won']}", (LOGICAL_W // 2, 626), 20, (200, 190, 170), "center")
        pts = POINT_CHOICES[self.points_i]
        txt = "Enter: practicar" if self.mode == "practice" else f"Enter: jugar a {pts} puntos   (arriba/abajo cambia)"
        shadow_text(surf, txt, (LOGICAL_W // 2, 670), 26, UI_SELECTED, "center", True)
        shadow_text(surf, "Esc: volver", (LOGICAL_W // 2, 698), 18, UI_DIM, "center")


# -- Versus local -----------------------------------------------------------------
class VersusSetupScene(Scene):
    def enter(self):
        app = self.app
        st = app.profile.settings
        schemes = ["mouse", "wasd", "arrows", "gamepad"]
        self.p1 = MenuItem("Jugador 1 (abajo)", values=schemes, index=schemes.index(st.p1_input) if st.p1_input in schemes else 0,
                           fmt=input_label)
        self.p2 = MenuItem("Jugador 2 (arriba)", values=schemes, index=schemes.index(st.p2_input) if st.p2_input in schemes else 2,
                           fmt=input_label)
        self.pts = MenuItem("Puntos", values=POINT_CHOICES, index=POINT_CHOICES.index(app.profile.mutators.points)
                            if app.profile.mutators.points in POINT_CHOICES else 2)
        items = [MenuItem("VERSUS LOCAL", header=True), self.p1, self.p2, self.pts,
                 MenuItem("Jugar", self.start, hint="Los dos juegan con la misma camara. El de arriba ve el juego como la IA."),
                 MenuItem("Volver", app.pop)]
        self.menu = Menu(items, None, 30, app.audio)

    def start(self):
        app = self.app
        st = app.profile.settings
        st.p1_input, st.p2_input = self.p1.value, self.p2.value
        app.profile.mutators.points = self.pts.value
        app.profile.dirty = True
        if self.p1.value == self.p2.value and self.p1.value in ("mouse", "gamepad", "wasd", "arrows"):
            app.toast("Los dos jugadores usan el mismo control", (255, 120, 90))
            return
        cfg = MatchConfig(mode="versus", opponent=None, points=self.pts.value,
                          mutators=Mutators(**asdict(app.profile.mutators)), p1_scheme=self.p1.value, p2_scheme=self.p2.value)
        app.replace(MatchScene(app, cfg))

    def handle_event(self, e):
        res = self.menu.handle_event(e)
        if res and res[0] == "back":
            self.app.pop()

    def draw(self, surf):
        surf.blit(self.app.background, (0, 0))
        draw_dim(surf, 130)
        self.menu.draw(surf, self.t)


# -- Partida -------------------------------------------------------------------------
class MatchScene(Scene):
    grab_mouse = True

    def __init__(self, app, cfg: MatchConfig):
        super().__init__(app)
        self.cfg = cfg

    def enter(self):
        app = self.app
        cfg = self.cfg
        self.rng = random.Random(app.rng.random())
        self.board = Board(self.rng, cfg.mutators, first_turn=1)
        self.persp = app.persp
        self.spec = OPPONENTS.get(cfg.opponent) if cfg.opponent else None
        self.spec_bottom = None
        units_px = app.profile.mouse_units_per_px()
        power = app.profile.settings.power_hit
        self.controllers = {}
        self.names = {1: app.profile.settings.player_name or "Jugador", 2: "Rival"}
        self.colors = {1: Art.PAD_COLORS[1], 2: Art.PAD_COLORS[2]}
        mult = cfg.mutators.speed
        if cfg.mode == "demo":
            keys = OPPONENT_KEYS[:]
            top = cfg.opponent or self.rng.choice(keys)
            bottom = cfg.opponent_bottom or self.rng.choice([k for k in keys if k != top])
            self.spec = OPPONENTS[top]
            self.spec_bottom = OPPONENTS[bottom]
            self.controllers[1] = AIController(self.board.pads[1], self.board, self.spec_bottom.ai.scaled(mult), self.rng, side=1)
            self.controllers[2] = AIController(self.board.pads[2], self.board, self.spec.ai.scaled(mult), self.rng, side=2)
            self.names = {1: self.spec_bottom.name, 2: self.spec.name}
        elif cfg.mode == "versus":
            self.controllers[1] = HumanController(self.board.pads[1], cfg.p1_scheme, units_px, power)
            self.controllers[2] = HumanController(self.board.pads[2], cfg.p2_scheme, units_px, power)
            self.names = {1: "Jugador 1", 2: "Jugador 2"}
        else:
            self.controllers[1] = HumanController(self.board.pads[1], cfg.p1_scheme, units_px, power)
            self.controllers[2] = AIController(self.board.pads[2], self.board,
                                               self.spec.ai.scaled(mult, cfg.nightmare), self.rng, side=2)
            self.names[2] = self.spec.name
        if self.spec:
            self.colors[2] = self.spec.color
        if self.spec_bottom:
            self.colors[1] = self.spec_bottom.color
        self.scoreboard = Scoreboard()
        self.toast = Toast()
        self.shaker = Shaker()
        self.sparks = Sparks(self.rng)
        self.flash = 0.0
        self.fog = Art.make_fog(self.persp) if cfg.mutators.fog else None
        self.portrait = pygame.Surface((PORTRAIT_W, PORTRAIT_H), pygame.SRCALPHA)
        self.portrait_rng = random.Random(7)
        self.bubble = None
        self.bubble_timer = Timer(1, running=False)
        self.intro_alpha = 0.0                 # C: fundido de 1 s de entidades y marcador
        self.phase = "intro"                   # intro | play | over
        self.over_timer = Timer(2600, running=False)
        self.won = None
        self.acc = 0.0
        self.elapsed = 0.0
        self.fastest = 0.0
        self.longest_rally = 0
        self.fi = FrameInput()
        self.help_timer = Timer(9000)
        self.fade = Fade(500, 1.0, 0.0)
        self.was_swirling = False
        self.target = cfg.points if cfg.mode not in ("practice",) else 10 ** 6
        if cfg.mode != "demo":
            self.app.audio.stop_music()
        self.show_intro_taunt()

    def show_intro_taunt(self):
        if self.spec and self.cfg.mode != "demo":
            self.say(self.rng.choice(self.spec.taunts["intro"]), 2600)

    def say(self, text, ms=2200):
        self.bubble = text
        self.bubble_timer.start(ms)

    def exit(self):
        self.app.set_grab(False)

    def resume(self):
        self.app.set_grab(self.phase == "play")
        self.fi = FrameInput()

    # -- eventos ---------------------------------------------------------------------
    def handle_event(self, e):
        app = self.app
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                if self.cfg.mode == "demo":
                    app.pop()
                elif self.phase == "play":
                    app.audio.play("pause")
                    app.push(PauseScene(app, self))
                return
            if self.cfg.mode == "demo":
                app.pop()
                return
            if e.key == pygame.K_F5:
                self._mouse_speed(-1)
            elif e.key == pygame.K_F6:
                self._mouse_speed(1)
            elif e.key == pygame.K_F1:
                app.push(HelpScene(app))
            elif e.key == pygame.K_SPACE:
                self.fi.power_events.add("wasd")
            elif e.key == pygame.K_KP_ENTER:
                self.fi.power_events.add("arrows")
            elif self.phase == "intro" and e.key in (pygame.K_RETURN, pygame.K_SPACE):
                self.intro_alpha = 1.0
        elif e.type == pygame.MOUSEBUTTONDOWN:
            if self.cfg.mode == "demo":
                app.pop()
                return
            if self.phase == "intro":
                self.intro_alpha = 1.0                      # C: clic salta el fundido
            elif e.button == 1:
                self.fi.power_events.add("mouse")
        elif e.type == pygame.JOYBUTTONDOWN:
            if e.button in (0, 1):
                self.fi.power_events.add("gamepad")
            elif e.button in (6, 7) and self.phase == "play":
                app.push(PauseScene(app, self))

    def _mouse_speed(self, step):
        st = self.app.profile.settings
        st.mouse_speed = int(clamp(st.mouse_speed + step, 1, 10))
        self.app.profile.dirty = True
        units = self.app.profile.mouse_units_per_px()
        for c in self.controllers.values():
            if not c.is_ai:
                c.units_per_px = units
        self.scoreboard.show_mousebar(st.mouse_speed)

    # -- bucle ---------------------------------------------------------------------------
    def update(self, dt):
        super().update(dt)
        app = self.app
        self.fade.update(dt)
        self.scoreboard.update(dt)
        self.toast.update(dt)
        self.shaker.update(dt)
        self.sparks.update(dt)
        self.flash = max(0.0, self.flash - dt / 90.0)
        self.help_timer.update(dt)
        if self.bubble and self.bubble_timer.update(dt):
            self.bubble = None
        if self.phase == "intro":
            self.intro_alpha += dt * 0.001                          # C: alpha += elapsed * 0.001
            self._ai_only_steps(dt)
            if self.intro_alpha >= 1.0:
                self.intro_alpha = 1.0
                self.phase = "play"
                app.set_grab(True)
                pygame.mouse.get_rel()
                self.scoreboard.show_mousebar(app.profile.settings.mouse_speed)
                if self.cfg.mode == "practice":
                    self.toast.show("PRACTICA", (120, 230, 255), 1500, 54)
            return
        if self.phase == "over":
            self._ai_only_steps(dt)
            if self.over_timer.update(dt):
                self.over_timer.running = False
                app.push(ResultScene(app, self))
            return
        # entrada por frame
        fi = self.fi
        fi.mouse_rel = pygame.mouse.get_rel() if app.grabbed else (0, 0)
        fi.frame_dt = dt
        fi.keys = pygame.key.get_pressed()
        fi.joy_axes = app.joy_axes()
        for c in self.controllers.values():
            if not c.is_ai:
                c.begin_frame(fi)
        fi.power_events = set()
        self.elapsed += dt
        self.acc += dt
        while self.acc >= FIXED_DT:
            self.acc -= FIXED_DT
            self._step(FIXED_DT)
            if self.phase != "play":
                break
        self.frame_alpha = self.acc / FIXED_DT

    def _ai_only_steps(self, dt):
        self.acc += dt
        while self.acc >= FIXED_DT:
            self.acc -= FIXED_DT
            for c in self.controllers.values():
                if c.is_ai:
                    c.update(FIXED_DT)
            for pad in self.board.pads.values():
                pad.snapshot()
                pad.move(FIXED_DT)
                pad.tick_fx(FIXED_DT)
            for p in self.board.pucks:
                p.snapshot()
                if p.state == "crash":
                    p.glass.update(FIXED_DT)
        self.frame_alpha = self.acc / FIXED_DT

    def _step(self, dt):
        board = self.board
        for c in self.controllers.values():
            if c.is_ai:
                c.update(dt)
            else:
                c.step(dt)
        scorers = board.update(dt)
        for ev in board.drain_events():
            self._on_event(ev)
        for who in scorers:
            self._on_point(who)
        # chispas al golpear fuerte
        ai2 = self.controllers.get(2)
        sw = bool(ai2 and ai2.is_ai and ai2.swirling)
        if sw and not self.was_swirling:
            self.app.audio.play("swirl", 0.7)
        self.was_swirling = sw

    def _on_event(self, ev):
        kind = ev[0]
        audio = self.app.audio
        if kind == "hit":
            _, side, speed, x, z = ev
            audio.play("nock", clamp(0.4 + speed * 3, 0.3, 1.0))
            if speed > 0.15:
                audio.play("whoosh", 0.6)
            sx, sy, _ = self.persp.project(x, z)
            self.sparks.burst(sx, sy - 10, mix_color(self.colors[side], (255, 255, 255), 0.5), 6 + int(speed * 40), 0.2 + speed)
            if self.board.pads[side].boost > 0:
                audio.play("power", 0.8)
            self.fastest = max(self.fastest, speed)
            self.longest_rally = max(self.longest_rally, self.board.rally)
            if self.board.rally and self.board.rally % 10 == 0:
                self.toast.show(f"PELOTEO x{self.board.rally}", (255, 220, 120), 900, 40)
        elif kind == "wall":
            audio.play("tick", 0.5)
            sx, sy, _ = self.persp.project(ev[1], ev[2])
            self.sparks.burst(sx, sy - 8, (255, 230, 160), 4, 0.15)
        elif kind == "goal":
            _, who, x, z = ev
            audio.play("crash")
            self.shaker.kick(14)
            self.flash = 1.0
        elif kind == "serve":
            audio.play("serve", 0.5)
        elif kind == "swirl_release":
            audio.play("whoosh", 1.0)

    def _on_point(self, who):
        board = self.board
        self.scoreboard.point_scored(who)
        for side, c in self.controllers.items():
            if c.is_ai:
                c.on_point(side == who)
                c.adapt(board.score[side], board.score[3 - side])
        if self.cfg.mode == "demo":
            self.toast.show(f"PUNTO {self.names[who].upper()}", self.colors[who], 1200, 50)
        elif who == 1:
            self.toast.show("PUNTO", (120, 255, 160), 1100)
            self.app.audio.play("point")
            if self.spec:
                self.app.audio.play("boo", 0.5)
                self.say(self.rng.choice(self.spec.taunts["lose_point"]))
        else:
            self.toast.show("PUNTO RIVAL" if self.cfg.mode != "versus" else "PUNTO J2", (255, 120, 90), 1100)
            self.app.audio.play("lose_point")
            if self.spec:
                self.app.audio.play("cheer", 0.6)
                self.say(self.rng.choice(self.spec.taunts["win_point"]))
        if board.score[who] >= self.target:
            self._game_over(who)

    def _game_over(self, who):
        self.phase = "over"
        self.won = (who == 1)
        self.board.frozen = True
        for side, c in self.controllers.items():
            if c.is_ai:
                c.on_game_over(side == who)
        self.over_timer.start(2600)
        self.app.set_grab(False)
        if self.cfg.mode == "demo":
            self.toast.show(f"GANA {self.names[who].upper()}", self.colors[who], 2500, 60)
            return
        if self.cfg.mode == "versus":
            self.toast.show(f"GANA JUGADOR {who}", self.colors[who], 2500, 60)
            self.app.audio.play("fanfare")
            return
        if self.won:
            self.toast.show("VICTORIA", (120, 255, 160), 2500, 72)
            self.app.audio.play("fanfare")
            self.say(self.rng.choice(self.spec.taunts["lose_game"]), 4000)
        else:
            self.toast.show("DERROTA", (255, 100, 80), 2500, 72)
            self.app.audio.play("lose")
            self.say(self.rng.choice(self.spec.taunts["win_game"]), 4000)
        self._record_stats()

    def _record_stats(self):
        prof = self.app.profile
        st = prof.stats
        st.record_match(self.cfg.opponent, self.won, self.longest_rally, self.fastest, self.elapsed / 1000.0)
        if self.cfg.mode == "tournament":
            if self.won:
                if self.cfg.nightmare:
                    if self.cfg.tournament_index + 1 >= len(OPPONENT_KEYS):
                        st.nightmare_wins += 1
                else:
                    st.tournament_progress = self.cfg.tournament_index + 1
                    st.tournament_best = max(st.tournament_best, st.tournament_progress)
                    if st.tournament_progress >= len(OPPONENT_KEYS):
                        st.tournament_wins += 1
                        st.nightmare_unlocked = True
                        st.tournament_progress = 0
        prof.dirty = True
        prof.save()

    def surrender(self):
        """Desde el menu de pausa: cuenta como derrota."""
        if self.phase != "play" or self.cfg.mode in ("practice", "demo", "versus"):
            self.app.pop_to_menu()
            return
        self.board.score[2] = max(self.board.score[2], self.target)
        self._game_over(2)

    # -- dibujo -------------------------------------------------------------------------------
    def draw(self, surf):
        app = self.app
        board = self.board
        persp = self.persp
        off = self.shaker.offset()
        surf.blit(app.background, (int(off[0]), int(off[1])))
        if self.fog is not None:
            surf.blit(self.fog, (int(off[0]), int(off[1])))
        a = int(255 * self.intro_alpha)
        alpha = getattr(self, "frame_alpha", 0.0)
        # trayectoria en practica
        if self.cfg.mode == "practice":
            for p in board.pucks:
                if p.state == "play" and p.speed > 0.001:
                    pts = predict_path(p.x, p.z, p.dx, p.dz, p.spin, board.arcade, 900)
                    scr = [persp.project(x, z)[:2] for x, z in pts[::6]]
                    if len(scr) > 1:
                        pygame.draw.lines(surf, (120, 230, 255), False, scr, 2)
        # cristales rotos del fondo (z > 0) antes de las entidades
        for p in board.pucks:
            if p.state == "crash" and p.glass.z > 0:
                p.glass.draw(surf, persp, off)
        # entidades ordenadas de lejos a cerca
        drawables = []
        for p in board.pucks:
            if p.state != "crash":
                x, z = p.interp(alpha)
                drawables.append((z, "puck", p, x))
        for pad in board.pads.values():
            x, z = pad.interp(alpha)
            drawables.append((z, "pad", pad, x))
        drawables.sort(key=lambda d: -d[0])
        for z, kind, obj, x in drawables:
            if kind == "puck":
                Art.draw_puck(surf, persp, x, z, a, obj.speed, trail=obj.trail, offset=off)
            else:
                Art.draw_pad(surf, persp, x, z, obj.w, self.colors[obj.side], int(128 * self.intro_alpha), obj.flash, off)
        for p in board.pucks:
            if p.state == "crash" and p.glass.z < 0:
                p.glass.draw(surf, persp, off)
        self.sparks.draw(surf, off)
        # retrato del rival
        self._draw_portrait(surf, a)
        # marcador
        self.scoreboard.draw(surf, self.names, board.score, self.target if self.target < 10 ** 5 else 0,
                             board.rally, max((p.speed for p in board.pucks), default=0.0), self.elapsed, a, self.colors)
        if self.bubble and a > 200:
            anchor = (LOGICAL_W // 2, 60) if self.spec else (LOGICAL_W // 2, 120)
            draw_bubble(surf, self.bubble, (anchor[0] + 120, anchor[1] + 40))
        if self.flash > 0:
            ov = pygame.Surface((LOGICAL_W, LOGICAL_H))
            ov.fill((255, 255, 255))
            ov.set_alpha(int(160 * self.flash))
            surf.blit(ov, (0, 0))
        self.toast.draw(surf)
        if self.phase == "intro":
            shadow_text(surf, "clic o Enter para empezar", (LOGICAL_W // 2, LOGICAL_H - 50), 26, (240, 220, 180), "center")
        elif not self.help_timer.done and self.cfg.mode != "demo":
            msg = {"mouse": "mueve el raton  |  clic: golpe fuerte  |  Esc: pausa  |  F5/F6: velocidad",
                   "wasd": "WASD mueve  |  Espacio: golpe fuerte  |  Esc: pausa",
                   "arrows": "flechas mueven  |  Intro num.: golpe fuerte  |  Esc: pausa",
                   "gamepad": "stick izq. mueve  |  A: golpe fuerte  |  Start: pausa"}[self.cfg.p1_scheme]
            shadow_text(surf, msg, (LOGICAL_W // 2, LOGICAL_H - 36), 20, UI_DIM, "center")
        if self.cfg.mode == "demo":
            shadow_text(surf, "DEMO  -  cualquier tecla vuelve", (LOGICAL_W // 2, LOGICAL_H - 36), 22, (255, 200, 120), "center")
        self.fade.draw(surf)

    def _draw_portrait(self, surf, alpha):
        if not self.spec:
            if self.cfg.mode == "versus":
                img = neon_text("VS", 60, (255, 120, 180))
                surf.blit(img, (LOGICAL_W // 2 - img.get_width() // 2, 120))
            return
        puck = self.board.puck_for(2)
        ex = clamp(puck.x / 20.0, -1, 1)
        ey = clamp((puck.z - 10) / 40.0, -1, 1) * -1
        ai = self.controllers.get(2)
        mood = ai.mood if (ai and ai.is_ai) else "idle"
        self.portrait.fill((0, 0, 0, 0))
        self.spec.portrait(self.portrait, self.t, mood, (ex, ey), self.portrait_rng)
        if alpha < 255:
            self.portrait.set_alpha(alpha)
        else:
            self.portrait.set_alpha(255)
        x = LOGICAL_W // 2 - PORTRAIT_W // 2                      # C: rect.x = SCREEN_W/2 - w/2
        y = int(BOARD_TOP) - PORTRAIT_H + 4                        # C: rect.y = BOARD_TOP - h
        surf.blit(self.portrait, (x, y))
        shadow_text(surf, self.spec.name, (LOGICAL_W // 2, y + PORTRAIT_H - 2), 22, self.spec.color, "center", True)
        if self.spec_bottom:
            shadow_text(surf, f"abajo: {self.spec_bottom.name}", (LOGICAL_W // 2, LOGICAL_H - 62), 22, self.spec_bottom.color, "center", True)


# -- Pausa -----------------------------------------------------------------------------
class PauseScene(Scene):
    overlay = True

    def __init__(self, app, match: MatchScene):
        super().__init__(app)
        self.match = match

    def enter(self):
        app = self.app
        st = app.profile.settings
        items = [MenuItem("PAUSA", header=True),
                 MenuItem("Continuar", app.pop),                                    # C: "Continue"
                 MenuItem("Velocidad raton", values=list(range(1, 11)), index=st.mouse_speed - 1,
                          on_change=self._mouse),
                 MenuItem("Sonido", values=[True, False], index=0 if not st.muted else 1,
                          fmt=lambda v: "si" if v else "no", on_change=self._sound),
                 MenuItem("Rendirse", self._surrender, hint="Cuenta como derrota."),          # C: "Surrender"
                 MenuItem("Menu principal", app.pop_to_menu, hint="Abandona sin registrar la partida.")]
        self.menu = Menu(items, None, 30, app.audio)
        app.set_grab(False)

    def _mouse(self, v):
        self.app.profile.settings.mouse_speed = v
        self.match._mouse_speed(0)

    def _sound(self, v):
        self.app.set_muted(not v)

    def _surrender(self):
        self.app.pop()
        self.match.surrender()

    def handle_event(self, e):
        res = self.menu.handle_event(e)
        if res and res[0] == "back":
            self.app.pop()

    def draw(self, surf):
        draw_dim(surf, 120)
        self.menu.draw(surf, self.t)


# -- Resultado ------------------------------------------------------------------------------
class ResultScene(Scene):
    overlay = True

    def __init__(self, app, match: MatchScene):
        super().__init__(app)
        self.match = match

    def enter(self):
        app = self.app
        m = self.match
        cfg = m.cfg
        items = []
        if cfg.mode == "demo":
            items.append(MenuItem("Otra demo", lambda: app.replace_below(DemoScene(app, MatchConfig(mode="demo")))))
            items.append(MenuItem("Menu principal", app.pop_to_menu))
        elif cfg.mode == "tournament":
            if m.won:
                nxt = cfg.tournament_index + 1
                if nxt < len(OPPONENT_KEYS):
                    items.append(MenuItem(f"Siguiente rival: {OPPONENTS[OPPONENT_KEYS[nxt]].name}",
                                          lambda: app.replace_below(MatchScene(app, MatchConfig(
                                              mode="tournament", opponent=OPPONENT_KEYS[nxt], points=cfg.points,
                                              p1_scheme=cfg.p1_scheme, nightmare=cfg.nightmare, tournament_index=nxt)))))
                else:
                    items.append(MenuItem("CAFE CONQUISTADO", header=True))
            else:
                items.append(MenuItem("Reintentar", lambda: app.replace_below(MatchScene(app, cfg.clone()))))
            items.append(MenuItem("Escalera del torneo", lambda: app.pop_to(TournamentScene)))
            items.append(MenuItem("Menu principal", app.pop_to_menu))
        else:
            items.append(MenuItem("Jugar otra vez", lambda: app.replace_below(MatchScene(app, cfg.clone()))))   # C: "Play Again"
            if cfg.mode == "quick":
                items.append(MenuItem("Otro rival", lambda: app.pop_to(OpponentSelectScene)))
            items.append(MenuItem("Menu principal", app.pop_to_menu))                                                      # C: "Main Menu"
        self.menu = Menu(items, None, 30, app.audio, center=(LOGICAL_W // 2, LOGICAL_H // 2 + 90))

    def handle_event(self, e):
        res = self.menu.handle_event(e)
        if res and res[0] == "back":
            self.app.pop_to_menu()

    def draw(self, surf):
        draw_dim(surf, 110)
        m = self.match
        s = m.board.score
        if m.cfg.mode == "demo" or m.cfg.mode == "versus":
            title, col = (f"{m.names[1] if s[1] > s[2] else m.names[2]} gana", (255, 220, 120))
        elif m.won:
            title, col = ("VICTORIA", (120, 255, 160))
        else:
            title, col = ("DERROTA", (255, 100, 80))
        img = neon_text(title, 80, col, col)
        surf.blit(img, (LOGICAL_W // 2 - img.get_width() // 2, 150))
        shadow_text(surf, f"{m.names[1]} {s[1]}  -  {s[2]} {m.names[2]}", (LOGICAL_W // 2, 260), 40, UI_TEXT, "center", True)
        secs = int(m.elapsed // 1000)
        shadow_text(surf, f"peloteo mas largo {m.longest_rally}   |   tiro mas rapido {m.fastest * KMH_PER_UNIT_MS:.0f} km/h"
                    f"   |   {secs // 60:02d}:{secs % 60:02d}", (LOGICAL_W // 2, 310), 24, UI_DIM, "center")
        self.menu.draw(surf, self.t)


# -- Torneo -------------------------------------------------------------------------------
class TournamentScene(Scene):
    def enter(self):
        self.rng = random.Random(11)
        self.nightmare = False
        self._build()

    def resume(self):
        self._build()

    def _build(self):
        app = self.app
        st = app.profile.stats
        prog = st.tournament_progress
        nxt = OPPONENTS[OPPONENT_KEYS[min(prog, len(OPPONENT_KEYS) - 1)]]
        items = [MenuItem(f"Luchar contra {nxt.name}", self.fight,
                          hint="Perder no borra lo conquistado. Cada victoria se guarda.")]
        if st.nightmare_unlocked:
            self.nm_item = MenuItem("Modo Pesadilla", values=[False, True], index=1 if self.nightmare else 0,
                                    fmt=lambda v: "si" if v else "no", on_change=self._set_nm,
                                    hint="Todos los rivales con reaccion y potencia al maximo. Se juega del tiron.")
            items.append(self.nm_item)
        if prog > 0:
            items.append(MenuItem("Reiniciar escalera", self.reset, hint="Vuelve a empezar desde Bit."))
        items.append(MenuItem("Volver", app.pop))
        self.menu = Menu(items, None, 28, app.audio, center=(LOGICAL_W // 2, 560), min_width=420)

    def _set_nm(self, v):
        self.nightmare = v

    def reset(self):
        self.app.profile.stats.tournament_progress = 0
        self.app.profile.dirty = True
        self._build()

    def fight(self):
        app = self.app
        st = app.profile.stats
        idx = 0 if self.nightmare else st.tournament_progress
        cfg = MatchConfig(mode="tournament", opponent=OPPONENT_KEYS[idx], points=DEFAULT_POINTS,
                          p1_scheme=app.profile.settings.p1_input, nightmare=self.nightmare, tournament_index=idx)
        app.push(MatchScene(app, cfg))

    def handle_event(self, e):
        res = self.menu.handle_event(e)
        if res and res[0] == "back":
            self.app.pop()

    def draw(self, surf):
        app = self.app
        surf.blit(app.background, (0, 0))
        draw_dim(surf, 150)
        img = neon_text("TORNEO", 60, (255, 120, 180), (255, 60, 140))
        surf.blit(img, (LOGICAL_W // 2 - img.get_width() // 2, 24))
        st = app.profile.stats
        prog = st.tournament_progress
        n = len(OPPONENT_KEYS)
        cell = 150
        x0 = LOGICAL_W // 2 - (n * cell) // 2
        for i, key in enumerate(OPPONENT_KEYS):
            spec = OPPONENTS[key]
            x = x0 + i * cell
            y = 120
            port = pygame.Surface((PORTRAIT_W, PORTRAIT_H), pygame.SRCALPHA)
            beaten = i < prog
            current = i == prog
            mood = "lose" if beaten else ("angry" if current else "idle")
            eye = (math.sin(self.t / 600.0 + i), 0.2)
            spec.portrait(port, self.t, mood, eye, self.rng)
            small = pygame.transform.smoothscale(port, (int(PORTRAIT_W * 0.62), int(PORTRAIT_H * 0.62)))
            if not beaten and not current:
                small.fill((70, 70, 70, 255), special_flags=pygame.BLEND_RGBA_MULT)
            frame = pygame.Rect(x + 4, y, cell - 8, int(PORTRAIT_H * 0.62) + 60)
            draw_panel(surf, frame, 120, UI_SELECTED if current else (spec.color if beaten else (80, 70, 60)))
            surf.blit(small, (x + cell // 2 - small.get_width() // 2, y + 8))
            shadow_text(surf, spec.name, (x + cell // 2, y + int(PORTRAIT_H * 0.62) + 14), 24, spec.color if (beaten or current) else UI_DIM, "center", True)
            status = "vencido" if beaten else ("siguiente" if current else "bloqueado")
            shadow_text(surf, status, (x + cell // 2, y + int(PORTRAIT_H * 0.62) + 38), 18, UI_DIM, "center")
            if i < n - 1:
                shadow_text(surf, ">", (x + cell - 4, y + 70), 36, UI_DIM, "center", True)
        shadow_text(surf, f"progreso {prog}/{n}   |   mejor {st.tournament_best}/{n}   |   cafes conquistados {st.tournament_wins}"
                    + (f"   |   pesadillas {st.nightmare_wins}" if st.nightmare_unlocked else ""),
                    (LOGICAL_W // 2, 400), 22, (220, 210, 190), "center")
        shadow_text(surf, "Los seis, en orden. A 15 puntos, sin mutadores.", (LOGICAL_W // 2, 430), 22, UI_DIM, "center")
        self.menu.draw(surf, self.t)


# -- Arcade (mutadores) -----------------------------------------------------------------
class ArcadeScene(Scene):
    def enter(self):
        app = self.app
        mut = app.profile.mutators
        spd = [0.6, 0.8, 1.0, 1.25, 1.5]
        pads = [0.6, 1.0, 1.4]
        self.items = {
            "speed": MenuItem("Velocidad del disco", values=spd, index=spd.index(mut.speed) if mut.speed in spd else 2,
                              fmt=lambda v: f"{v:.2g}x", hint="Multiplica el tope de velocidad y la potencia de todos."),
            "pucks": MenuItem("Discos", values=[1, 2, 3], index=mut.pucks - 1, hint="Cada disco puntua por separado."),
            "pad": MenuItem("Pala", values=pads, index=pads.index(mut.pad_scale) if mut.pad_scale in pads else 1,
                            fmt=lambda v: f"{v:.2g}x", hint="A 0.6x el Daemon se vuelve humano."),
            "fog": MenuItem("Niebla", values=[False, True], index=1 if mut.fog else 0, fmt=lambda v: "si" if v else "no",
                            hint="La mitad lejana se oscurece: hay que leer el disco por el sonido."),
            "phys": MenuItem("Fisica", values=["Clasica", "Arcade"], index=1 if mut.arcade_physics else 0,
                             hint="Arcade: la velocidad lateral de la pala imprime efecto y el disco curva."),
            "pts": MenuItem("Puntos", values=POINT_CHOICES, index=POINT_CHOICES.index(mut.points) if mut.points in POINT_CHOICES else 2),
        }
        items = [MenuItem("ARCADE", header=True)] + list(self.items.values()) + [
            MenuItem("Restablecer", self.reset), MenuItem("Volver", self.back,
                     hint="Se aplican a Partida rapida y Versus. El Torneo siempre es limpio.")]
        self.menu = Menu(items, None, 30, app.audio, min_width=460)

    def reset(self):
        self.app.profile.mutators = Mutators()
        self.app.profile.dirty = True
        self.enter()

    def back(self):
        it = self.items
        mut = Mutators(speed=it["speed"].value, pucks=it["pucks"].value, pad_scale=it["pad"].value, fog=it["fog"].value,
                       arcade_physics=(it["phys"].value == "Arcade"), points=it["pts"].value)
        self.app.profile.mutators = mut
        self.app.profile.dirty = True
        self.app.profile.save()
        self.app.pop()

    def handle_event(self, e):
        res = self.menu.handle_event(e)
        if res and res[0] == "back":
            self.back()

    def draw(self, surf):
        surf.blit(self.app.background, (0, 0))
        draw_dim(surf, 130)
        self.menu.draw(surf, self.t)


# -- Ajustes ------------------------------------------------------------------------------
class SettingsScene(Scene):
    def enter(self):
        app = self.app
        st = app.profile.settings
        vols = [round(i / 10, 1) for i in range(11)]
        schemes = ["mouse", "wasd", "arrows", "gamepad"]
        self.confirm_reset = False
        self.items = {
            "full": MenuItem("Pantalla completa", values=[False, True], index=1 if st.fullscreen else 0,
                             fmt=lambda v: "si" if v else "no", on_change=lambda v: app.set_fullscreen(v)),
            "sfx": MenuItem("Volumen efectos", values=vols, index=int(round(st.sfx_volume * 10)),
                            fmt=lambda v: f"{int(v * 100)} %", on_change=self._sfx),
            "music": MenuItem("Volumen musica", values=vols, index=int(round(st.music_volume * 10)),
                              fmt=lambda v: f"{int(v * 100)} %", on_change=self._music),
            "mouse": MenuItem("Velocidad raton", values=list(range(1, 11)), index=st.mouse_speed - 1,
                              on_change=lambda v: setattr(st, "mouse_speed", v)),
            "input": MenuItem("Control jugador", values=schemes, index=schemes.index(st.p1_input) if st.p1_input in schemes else 0,
                              fmt=input_label, on_change=lambda v: setattr(st, "p1_input", v)),
            "grab": MenuItem("Capturar raton", values=[True, False], index=0 if st.grab_mouse else 1,
                             fmt=lambda v: "si" if v else "no", on_change=lambda v: setattr(st, "grab_mouse", v),
                             hint="Si tu compositor Wayland no soporta el modo relativo, ponlo en no."),
            "power": MenuItem("Golpe fuerte", values=[True, False], index=0 if st.power_hit else 1,
                              fmt=lambda v: "si" if v else "no", on_change=lambda v: setattr(st, "power_hit", v),
                              hint="Clic/Espacio: 120 ms de pala x1.6 hacia delante. Desactivalo para jugar purista."),
            "fps": MenuItem("Mostrar FPS", values=[False, True], index=1 if st.show_fps else 0,
                            fmt=lambda v: "si" if v else "no", on_change=lambda v: setattr(st, "show_fps", v)),
            "name": MenuItem(f"Nombre: {st.player_name}", self._rename, hint="Escribe el nombre y pulsa Enter."),
            "reset": MenuItem("Borrar perfil", self._reset, hint="Estadisticas, torneo y ajustes a cero."),
        }
        items = [MenuItem("AJUSTES", header=True)] + list(self.items.values()) + [MenuItem("Volver", self.back)]
        self.menu = Menu(items, None, 28, app.audio, min_width=480)
        self.typing = False
        self.buffer = ""

    def _sfx(self, v):
        self.app.profile.settings.sfx_volume = v
        self.app.audio.configure(self.app.profile.settings)
        self.app.audio.play("nock")

    def _music(self, v):
        self.app.profile.settings.music_volume = v
        self.app.audio.configure(self.app.profile.settings)

    def _rename(self):
        self.typing = True
        self.buffer = self.app.profile.settings.player_name
        if hasattr(pygame.key, "start_text_input"):
            pygame.key.start_text_input()

    def _reset(self):
        if not self.confirm_reset:
            self.confirm_reset = True
            self.items["reset"].label = "Seguro?  Enter para confirmar"
            return
        self.app.profile.reset()
        self.app.profile.save()
        self.app.audio.configure(self.app.profile.settings)
        self.enter()
        self.app.toast("Perfil borrado", (255, 120, 90))

    def back(self):
        self.app.profile.dirty = True
        self.app.profile.save()
        self.app.pop()

    def handle_event(self, e):
        if self.typing:
            if e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    name = self.buffer.strip()[:14] or "Jugador"
                    self.app.profile.settings.player_name = name
                    self.items["name"].label = f"Nombre: {name}"
                    self.typing = False
                elif e.key == pygame.K_ESCAPE:
                    self.typing = False
                elif e.key == pygame.K_BACKSPACE:
                    self.buffer = self.buffer[:-1]
                elif e.unicode and e.unicode.isprintable() and len(self.buffer) < 14 and e.key != pygame.K_RETURN:
                    self.buffer += e.unicode
            return
        res = self.menu.handle_event(e)
        if res and res[0] == "back":
            self.back()
        elif res and res[0] in ("select", "change") and res[1] is not self.items["reset"]:
            if self.confirm_reset:
                self.confirm_reset = False
                self.items["reset"].label = "Borrar perfil"

    def draw(self, surf):
        surf.blit(self.app.background, (0, 0))
        draw_dim(surf, 130)
        self.menu.draw(surf, self.t)
        if self.typing:
            rect = pygame.Rect(LOGICAL_W // 2 - 220, LOGICAL_H // 2 - 50, 440, 100)
            draw_panel(surf, rect, 230)
            shadow_text(surf, "Nombre del jugador", (rect.centerx, rect.top + 12), 22, UI_DIM, "center")
            caret = "_" if int(self.t / 400) % 2 == 0 else " "
            shadow_text(surf, self.buffer + caret, (rect.centerx, rect.top + 44), 36, UI_SELECTED, "center", True)


# -- Estadisticas -----------------------------------------------------------------------
class StatsScene(Scene):
    def handle_event(self, e):
        if e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
            self.app.pop()

    def draw(self, surf):
        surf.blit(self.app.background, (0, 0))
        draw_dim(surf, 160)
        st = self.app.profile.stats
        img = neon_text("ESTADISTICAS", 56, (120, 230, 255), (40, 180, 255))
        surf.blit(img, (LOGICAL_W // 2 - img.get_width() // 2, 24))
        rect = pygame.Rect(90, 110, LOGICAL_W - 180, 540)
        draw_panel(surf, rect, 160)
        y = rect.top + 20
        pct = (100.0 * st.wins / st.matches) if st.matches else 0.0
        hours = st.play_time_s / 3600.0
        rows = [("Partidas", f"{st.matches}   (ganadas {st.wins}, perdidas {st.losses}, {pct:.0f} %)"),
                ("Racha", f"actual {st.streak}   mejor {st.best_streak}"),
                ("Peloteo mas largo", f"{st.longest_rally} golpes"),
                ("Tiro mas rapido", f"{st.fastest_shot * KMH_PER_UNIT_MS:.0f} km/h  ({st.fastest_shot:.3f} u/ms)"),
                ("Tiempo jugado", f"{hours:.1f} h" if hours >= 1 else f"{st.play_time_s / 60:.0f} min"),
                ("Torneo", f"progreso {st.tournament_progress}/6   mejor {st.tournament_best}/6   conquistados {st.tournament_wins}"
                           + ("   pesadillas " + str(st.nightmare_wins) if st.nightmare_unlocked else ""))]
        for label, val in rows:
            shadow_text(surf, label, (rect.left + 30, y), 26, UI_HEADER, "left", True)
            shadow_text(surf, val, (rect.left + 300, y), 26, UI_TEXT)
            y += 36
        y += 14
        shadow_text(surf, "Por rival", (rect.left + 30, y), 26, UI_HEADER, "left", True)
        y += 34
        for key in OPPONENT_KEYS:
            spec = OPPONENTS[key]
            rec = st.opponent(key)
            p = rec["played"]
            w = rec["won"]
            bar_w = 300
            shadow_text(surf, spec.name, (rect.left + 30, y), 24, spec.color, "left", True)
            shadow_text(surf, f"{p} jugadas   {w} ganadas", (rect.left + 170, y), 22, UI_TEXT)
            pygame.draw.rect(surf, (40, 36, 44), (rect.left + 440, y + 4, bar_w, 14), border_radius=6)
            if p:
                pygame.draw.rect(surf, spec.color, (rect.left + 440, y + 4, int(bar_w * w / p), 14), border_radius=6)
            y += 32
        shadow_text(surf, "cualquier tecla: volver", (LOGICAL_W // 2, LOGICAL_H - 40), 20, UI_DIM, "center")


# -- Ayuda / creditos ----------------------------------------------------------------------
class TextScene(Scene):
    overlay = False
    title = ""
    color = (255, 120, 180)
    lines: list = []

    def handle_event(self, e):
        if e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
            self.app.pop()

    def draw(self, surf):
        surf.blit(self.app.background, (0, 0))
        draw_dim(surf, 170)
        img = neon_text(self.title, 56, self.color, self.color)
        surf.blit(img, (LOGICAL_W // 2 - img.get_width() // 2, 20))
        rect = pygame.Rect(70, 100, LOGICAL_W - 140, 570)
        draw_panel(surf, rect, 170)
        y = rect.top + 18
        for line in self.lines:
            if line.startswith("#"):
                shadow_text(surf, line[1:].strip(), (rect.left + 26, y), 26, UI_HEADER, "left", True)
                y += 32
            elif line == "":
                y += 10
            else:
                shadow_text(surf, line, (rect.left + 26, y), 22, UI_TEXT)
                y += 26
        shadow_text(surf, "cualquier tecla: volver", (LOGICAL_W // 2, LOGICAL_H - 40), 20, UI_DIM, "center")


class HelpScene(TextScene):
    overlay = True
    title = "AYUDA"
    lines = ["# Objetivo",
             "Cuela el disco tras la pala del rival. El cristal de su porteria estalla y sumas un punto.",
             "Gana quien llegue primero a los puntos acordados (15 por defecto). El saque alterna cada punto.",
             "",
             "# Controles",
             "Raton: mueve la pala (modo relativo, F5/F6 cambian la velocidad).   Clic izquierdo: golpe fuerte.",
             "Teclado: WASD o flechas.   Espacio / Intro numerico: golpe fuerte.   Mando: stick izquierdo, A.",
             "Esc: pausa.   F: pantalla completa.   M: silencio.   F3: FPS.   F12: captura.   F1: esta ayuda.",
             "",
             "# Fisica (la del original)",
             "El disco pierde el 40 % de velocidad en cada golpe y gana el 60 % de la velocidad de la pala.",
             "Un disco muerto se empuja; uno rapido se amortigua. La pala es una linea: no puedes 'pisar' el disco.",
             "Las bandas devuelven el disco sin perdida. Tope de velocidad: 0.20 unidades/ms (100 km/h de bar).",
             "",
             "# Consejos",
             "Golpea en diagonal cuando el rival se haya colocado: la pala solo reacciona con retardo.",
             "Contra Vera y el Daemon, los tiros blandos y cruzados valen mas que los fuertes.",
             "En Practica se dibuja la trayectoria prevista del disco: aprende a tirar a banda."]


class CreditsScene(TextScene):
    title = "CREDITOS"
    color = (120, 230, 255)
    lines = ["# Shufflepuck Cafe (1988)", "Christopher Gross, Broderbund. La idea original.",
             "",
             "# TuxPuck 0.8.2 (2001-2002)",
             "Jacob 'noork' Kroon: codigo.   m0ns00n (Hogne Titlestad): graficos.",
             "Ernst Moritz Hahn: prediccion de rebotes.   Sven Velt: manpage.   Urs Streidl: Tux.",
             "'Uriah' y 'Gio': sonidos.   SDL_rotozoom y SDL_image: zoom y carga.",
             "Mantenimiento en Debian: Markus Koschany y el Debian Games Team.",
             "",
             "# TuxPuck Python (2026)",
             "Reimplementacion desde cero en un solo fichero sobre pygame-ce, por davidvivo-ia.",
             "Fisica, perspectiva y maquina de estados de la IA fieles al codigo C original.",
             "Graficos, retratos, sonidos y musica generados por codigo. Sin assets externos.",
             "",
             "Licencia MIT.  Codigo en github.com/davidvivo-ia/tuxpuckpython",
             "",
             "\"Anyone remember Shufflepuck Cafe for the Amiga/AtariST?  I do.\"  -  tuxpuck(6), 2002"]


# -- Demo (IA contra IA) ------------------------------------------------------------------
class DemoScene(MatchScene):
    grab_mouse = False

    def enter(self):
        super().enter()
        self.intro_alpha = 1.0
        self.phase = "play"
        self.help_timer.elapsed = 1e9
        self.app.audio.start_music()


# ---- 14. App --------------------------------------------------------------------------
class App:
    """Ventana, lienzo logico, pila de escenas, bucle principal y teclas globales."""

    def __init__(self, args, profile: Profile):
        self.args = args
        self.profile = profile
        self.headless = os.environ.get("SDL_VIDEODRIVER", "") == "dummy"
        pygame.init()
        st = profile.settings
        self.fullscreen = bool(st.fullscreen) and not self.headless
        self.screen = self._make_window(self.fullscreen)
        pygame.display.set_caption(f"TuxPuck Python v{VERSION}")
        self._set_icon()
        seed = args.seed if args.seed is not None else int(time.time() * 1000) & 0xFFFFFFFF
        self.seed = seed
        self.rng = random.Random(seed)
        self.persp = PERSP
        self.background = Art.make_background(self.persp, random.Random(seed ^ 0x5EED))
        self.audio = AudioBank(enabled=not args.mute, seed=seed % 1000, music=not args.no_music)
        self.audio.configure(st)
        self.scenes: list = []
        self.running = True
        self.clock = pygame.time.Clock()
        self.grabbed = False
        self.joystick = None
        try:
            pygame.joystick.init()
            if pygame.joystick.get_count() > 0:
                self.joystick = pygame.joystick.Joystick(0)
                self.joystick.init()
        except pygame.error:
            self.joystick = None
        self._toast = Toast()
        self.fps = 0.0
        self.frame_ms = 0.0
        self.autoplay_timer = Timer(args.autoplay * 1000.0) if args.autoplay else None

    # -- ventana ------------------------------------------------------------------------
    def _make_window(self, fullscreen):
        size = (LOGICAL_W, LOGICAL_H)
        flags = pygame.SCALED | pygame.RESIZABLE
        if fullscreen:
            flags |= pygame.FULLSCREEN
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return pygame.display.set_mode(size, flags, vsync=1)
        except (pygame.error, TypeError):
            pass
        try:
            return pygame.display.set_mode(size, flags)
        except pygame.error:
            return pygame.display.set_mode(size)

    def _set_icon(self):
        icon = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.draw.ellipse(icon, Art.PUCK_SIDE, (2, 10, 28, 18))
        pygame.draw.ellipse(icon, Art.PUCK_TOP, (2, 4, 28, 18))
        pygame.draw.ellipse(icon, (255, 200, 190), (8, 7, 8, 5))
        try:
            pygame.display.set_icon(icon)
        except pygame.error:
            pass

    def set_fullscreen(self, on: bool):
        on = bool(on)
        self.profile.settings.fullscreen = on
        self.profile.dirty = True
        if self.headless or on == self.fullscreen:
            return
        self.fullscreen = on
        try:
            pygame.display.toggle_fullscreen()
        except pygame.error:
            self.screen = self._make_window(on)

    def set_grab(self, on: bool):
        on = bool(on) and self.profile.settings.grab_mouse and not self.headless
        if on == self.grabbed:
            return
        self.grabbed = on
        try:
            pygame.event.set_grab(on)
            pygame.mouse.set_visible(not on)
            if hasattr(pygame.mouse, "set_relative_mode"):
                pygame.mouse.set_relative_mode(on)
        except pygame.error:
            pass
        pygame.mouse.get_rel()

    def set_muted(self, muted: bool):
        self.profile.settings.muted = bool(muted)
        self.profile.dirty = True
        self.audio.set_muted(bool(muted))
        self.toast("silencio" if muted else "sonido", (200, 200, 220))

    def toast(self, text, color=(255, 255, 255), ms=1300):
        self._toast.show(text, color, ms, 36)

    def joy_axes(self):
        if self.joystick is None:
            return (0.0, 0.0)
        try:
            return (self.joystick.get_axis(0), self.joystick.get_axis(1))
        except pygame.error:
            return (0.0, 0.0)

    def screenshot(self, path=None):
        path = path or timestamp_name()
        try:
            pygame.image.save(self.screen, path)
            self.toast(f"captura: {os.path.basename(path)}", (180, 255, 200))
            return path
        except pygame.error:
            return None

    # -- pila de escenas ------------------------------------------------------------------
    @property
    def top(self):
        return self.scenes[-1] if self.scenes else None

    def push(self, scene: Scene):
        self.scenes.append(scene)
        scene.enter()
        self._apply_grab()

    def pop(self):
        if not self.scenes:
            return
        s = self.scenes.pop()
        s.exit()
        if self.top is not None:
            self.top.resume()
        else:
            self.running = False
        self._apply_grab()

    def replace(self, scene: Scene):
        if self.scenes:
            self.scenes.pop().exit()
        self.push(scene)

    def replace_below(self, scene: Scene):
        """Cierra el overlay superior y sustituye la escena de debajo (p. ej. nueva partida)."""
        if self.scenes:
            self.scenes.pop().exit()
        self.replace(scene)

    def pop_to(self, cls):
        while len(self.scenes) > 1 and not isinstance(self.top, cls):
            self.scenes.pop().exit()
        if self.top is not None:
            self.top.resume()
        self._apply_grab()

    def pop_to_menu(self):
        if any(isinstance(s, MainMenuScene) for s in self.scenes):
            self.pop_to(MainMenuScene)
        else:
            while self.scenes:
                self.scenes.pop().exit()
            self.push(MainMenuScene(self))

    def _apply_grab(self):
        top = self.top
        self.set_grab(bool(top and top.grab_mouse and getattr(top, "phase", "play") == "play"))

    def quit(self):
        self.running = False

    # -- bucle -------------------------------------------------------------------------------
    def _global_key(self, e) -> bool:
        if getattr(self.top, "typing", False):
            return False
        if e.key == pygame.K_f:
            self.set_fullscreen(not self.fullscreen)
        elif e.key == pygame.K_m:
            self.set_muted(not self.profile.settings.muted)
        elif e.key == pygame.K_F3:
            self.profile.settings.show_fps = not self.profile.settings.show_fps
        elif e.key == pygame.K_F12:
            self.screenshot()
        else:
            return False
        return True

    def run(self, first_scene: Scene):
        self.push(first_scene)
        while self.running and self.scenes:
            dt = self.clock.tick(240)
            dt = min(dt, 100)
            self.frame_ms = self.frame_ms * 0.9 + dt * 0.1 if self.frame_ms else dt
            self.fps = 1000.0 / self.frame_ms if self.frame_ms else 0.0
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    self.running = False
                elif e.type == pygame.KEYDOWN and self._global_key(e):
                    continue
                elif self.top is not None:
                    self.top.handle_event(e)
            if not self.running or not self.scenes:
                break
            self.top.update(dt)
            self._toast.update(dt)
            self._draw()
            if self.autoplay_timer is not None and self.autoplay_timer.update(dt):
                if self.args.screenshot:
                    pygame.image.save(self.screen, self.args.screenshot)
                self.running = False
        self.set_grab(False)
        self.profile.save()
        pygame.quit()

    def _draw(self):
        if not self.scenes:
            return
        base = len(self.scenes) - 1
        while base > 0 and self.scenes[base].overlay:
            base -= 1
        for s in self.scenes[base:]:
            s.draw(self.screen)
        self._toast.draw(self.screen, 90)
        if self.profile.settings.show_fps:
            shadow_text(self.screen, f"{self.fps:5.1f} fps  {self.frame_ms:4.1f} ms  semilla {self.seed}",
                        (8, 8), 18, (180, 255, 180))
        pygame.display.flip()


# ---- 15. selftest ----------------------------------------------------------------------
def selftest(verbose=True) -> int:
    """Pruebas internas sin ventana: fisica, prediccion, IA, perfil, sintetizador."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.init()
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))
        if verbose:
            print(("  ok   " if cond else "  FAIL ") + name)

    t0 = time.time()
    # perspectiva
    p = Perspective()
    check("y(-40) == 465*S", abs(p.y(-40) - 465 * S) < 1e-6)
    check("y(+40) == 175*S", abs(p.y(40) - 175 * S) < 1e-6)
    check("scale(-40) == 1", abs(p.scale(-40) - 1.0) < 1e-9)
    check("scale(+40) == 203/517", abs(p.scale(40) - 203 / 517) < 1e-9)
    x, z = p.unproject(*p.project(7.5, -12.0)[:2])
    check("unproject(project(x,z)) ida y vuelta", abs(x - 7.5) < 1e-6 and abs(z + 12) < 1e-6)
    # rebote en banda
    e = Puck()
    e.set_position(18.0, 0.0)
    e.set_velocity(0.1, 0.0)
    bx, bz = e.move(40)
    check("rebote en banda invierte dx y pega al borde", bx and e.dx == -0.1 and abs(e.x - (20 - PUCK_W / 2)) < 1e-9)
    # golpe frontal: friccion 0.6
    puck = Puck()
    puck.alive = True
    puck.state = "play"
    puck.set_position(0.0, -34.6)
    puck.set_velocity(0.0, -0.1)
    pad = Pad(1)
    pad.set_position(0.0, -36.0)
    pad.set_velocity(0.0, 0.05)
    hit = pad_will_hit(puck, pad, FIXED_DT)
    check("deteccion predictiva de golpe", hit)
    pad_hits(puck, pad, FIXED_DT)
    check("golpe: dz = (0.1 + 0.05*0.6)*0.6", abs(puck.dz - (0.1 + 0.05 * 0.6) * 0.6) < 1e-9 and puck.dx == 0.0)
    check("golpe: disco colocado delante de la pala", puck.z > pad.z + PUCK_W / 2)
    # tope de velocidad
    puck2 = Puck()
    puck2.set_position(0.0, -34.6)
    puck2.set_velocity(0.0, -0.1)
    pad2 = Pad(1)
    pad2.set_position(0.0, -36.0)
    pad2.set_velocity(0.9, 1.0)
    pad_hits(puck2, pad2, FIXED_DT)
    check("tope MAX_PUCK_SPEED", abs(puck2.speed - MAX_PUCK_SPEED) < 1e-9)
    # pala lateral no golpea
    puck3 = Puck()
    puck3.set_position(10.0, -34.6)
    puck3.set_velocity(0.0, -0.1)
    pad3 = Pad(1)
    pad3.set_position(0.0, -36.0)
    check("fuera de alcance lateral no golpea", not pad_will_hit(puck3, pad3, FIXED_DT))
    # prediccion de rebotes vs simulacion
    rng = random.Random(5)
    worst = 0.0
    for _ in range(40):
        x0 = rng.uniform(-15, 15)
        z0 = rng.uniform(-30, 10)
        dx0 = rng.uniform(-0.18, 0.18)
        dz0 = rng.uniform(0.05, 0.2)
        ax = 35.0
        pred = calc_puck_x(x0, z0, dx0, dz0, ax)
        pts = predict_path(x0, z0, dx0, dz0, stop_z=ax)
        worst = max(worst, abs(pred - pts[-1][0]))
    check(f"_calc_puck_x coincide con la simulacion (error max {worst:.2f})", worst < 3.0)   # el clamp del rebote discreto acumula ~0.7/rebote
    check("_calc_puck_x sin rebote", abs(calc_puck_x(0, 0, 0.0, 0.1, 35) - 0.0) < 1e-9)
    # efecto arcade curva
    pts_c = predict_path(0, -20, 0.0, 0.15, spin=0.0, arcade=False, stop_z=35)
    pts_a = predict_path(0, -20, 0.0, 0.15, spin=1.0, arcade=True, stop_z=35)
    check("fisica arcade: el efecto curva la trayectoria", abs(pts_c[-1][0]) < 1e-9 and abs(pts_a[-1][0]) > 2.0)
    # tablero: saque, gol y alternancia
    b = Board(random.Random(1), Mutators())
    steps = 0
    while b.pucks[0].state != "play" and steps < 20000:
        b.update(FIXED_DT)
        steps += 1
    check("el disco llega al punto de saque del humano", b.pucks[0].state == "play" and abs(b.pucks[0].z + SERVE_Z) < 1e-6)
    b.pucks[0].set_velocity(0.0, 0.2)
    b.pads[2].set_position(15.0, 36.0)                  # la pala rival, apartada
    scorer = []
    for _ in range(5000):
        scorer += b.update(FIXED_DT)
        if scorer:
            break
    check("gol del humano al cruzar z>40", scorer == [1] and b.pucks[0].state == "crash")
    for _ in range(3000):
        b.update(FIXED_DT)
        if b.pucks[0].state == "play":
            break
    check("tras el gol alterna el saque (rival)", b.pucks[0].state == "play" and abs(b.pucks[0].z - SERVE_Z) < 1e-6)
    # tres discos
    b3 = Board(random.Random(2), Mutators(pucks=3, speed=1.5, arcade_physics=True, fog=True))
    for p in b3.pucks:
        p.set_velocity(0.05, 0.1)
    for _ in range(6000):
        b3.update(FIXED_DT)
    check("tablero con 3 discos y mutadores no falla", len(b3.pucks) == 3)
    # IA contra IA: cada rival marca contra Bit sin colgarse
    for key in OPPONENT_KEYS:
        spec = OPPONENTS[key]
        rng = random.Random(42)
        board = Board(rng, Mutators())
        bottom = AIController(board.pads[1], board, OPPONENTS["bit"].ai, rng, side=1)
        top = AIController(board.pads[2], board, spec.ai, rng, side=2)
        goals = 0
        hung = False
        since_goal = 0
        swirls = 0
        for step in range(150000):
            bottom.update(FIXED_DT)
            top.update(FIXED_DT)
            if top.swirling:
                swirls += 1
            sc = board.update(FIXED_DT)
            board.drain_events()
            if sc:
                goals += len(sc)
                since_goal = 0
                for who in sc:
                    bottom.on_point(who == 1)
                    top.on_point(who == 2)
                if goals >= 30:
                    break
            else:
                since_goal += 1
                if since_goal > 60000 // FIXED_DT:       # 60 s sin gol = colgado
                    hung = True
                    break
        ok = goals >= 30 and not hung and (board.score[2] >= board.score[1] or key == "bit")
        check(f"IA vs IA {key:8s} contra Bit: {goals} puntos, marcador {board.score[1]}-{board.score[2]}", ok)
        if key == "arcana":
            check("Arcana ha sacado en espiral alguna vez", swirls > 0)
    # Tux contra Tux: simetrico y sin excepciones (puede ser un peloteo eterno, es el original)
    rng = random.Random(4)
    board = Board(rng, Mutators())
    a1 = AIController(board.pads[1], board, OPPONENTS["tux"].ai, rng, side=1)
    a2 = AIController(board.pads[2], board, OPPONENTS["tux"].ai, rng, side=2)
    hits = 0
    for step in range(20000 // FIXED_DT):
        a1.update(FIXED_DT)
        a2.update(FIXED_DT)
        sc = board.update(FIXED_DT)
        hits += sum(1 for ev in board.drain_events() if ev[0] == "hit")
        for who in sc:
            a1.on_point(who == 1)
            a2.on_point(who == 2)
    check(f"Tux contra Tux 20 s: {hits} golpes, {sum(board.score.values())} puntos", hits > 10)
    # el Daemon con pesadilla gana a Bit
    rng = random.Random(9)
    board = Board(rng, Mutators())
    bottom = AIController(board.pads[1], board, OPPONENTS["bit"].ai, rng, side=1)
    top = AIController(board.pads[2], board, OPPONENTS["daemon"].ai.scaled(1.0, True), rng, side=2)
    for step in range(120000):
        bottom.update(FIXED_DT)
        top.update(FIXED_DT)
        sc = board.update(FIXED_DT)
        board.drain_events()
        for who in sc:
            bottom.on_point(who == 1)
            top.on_point(who == 2)
        if max(board.score.values()) >= 15:
            break
    check(f"Daemon (pesadilla) vence a Bit: {board.score[2]}-{board.score[1]}", board.score[2] > board.score[1])
    # perfil
    tmpdir = tempfile.mkdtemp(prefix="tuxpuck-test-")
    path = os.path.join(tmpdir, "profile.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ esto no es json")
    prof = Profile(path)
    check("perfil corrupto se ignora", prof.load() is False and prof.settings.mouse_speed == 5)
    prof.settings.mouse_speed = 8
    prof.stats.record_match("tux", True, 12, 0.19, 90.0)
    check("perfil se guarda", prof.save())
    prof2 = Profile(path)
    check("perfil ida y vuelta", prof2.load() and prof2.settings.mouse_speed == 8 and prof2.stats.opponent("tux")["won"] == 1
          and prof2.stats.longest_rally == 12)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"settings": {"mouse_speed": 99, "player_name": 5}, "stats": {"wins": "x"}}, fh)
    prof3 = Profile(path)
    prof3.load()
    check("perfil con tipos raros se sanea", prof3.settings.mouse_speed == 10 and prof3.settings.player_name == "5" and prof3.stats.wins == 0)
    try:
        os.remove(path)
        os.rmdir(tmpdir)
    except OSError:
        pass
    # sintetizador
    synth = Synth(SAMPLE_RATE, 1)
    sfx = synth.build_sfx()
    check("sintetizador: catalogo completo", len(sfx) >= 15 and all(len(v) > 100 for v in sfx.values()))
    music = synth.build_music(2)
    check("secuenciador: 2 compases de musica", len(music) > SAMPLE_RATE)
    # controlador humano por teclado
    pad = Pad(1)
    hc = HumanController(pad, "wasd", 0.2)
    fi = FrameInput()
    keys = {k: False for k in range(600)}
    keys[pygame.K_w] = True
    fi.keys = keys
    hc.begin_frame(fi)
    for _ in range(60):
        hc.step(FIXED_DT)
    check("teclado acelera la pala hacia delante", pad.dz > KEY_PAD_SPEED * 0.9)
    # arte y UI sin ventana
    pygame.font.init()
    bg = Art.make_background(PERSP, random.Random(1))
    check("fondo procedural 960x720", bg.get_size() == (LOGICAL_W, LOGICAL_H))
    port = pygame.Surface((PORTRAIT_W, PORTRAIT_H), pygame.SRCALPHA)
    ok = True
    for key in OPPONENT_KEYS:
        for mood in ("idle", "win", "lose", "happy", "angry"):
            try:
                OPPONENTS[key].portrait(port, 1234.0, mood, (0.3, -0.2), random.Random(3))
            except Exception as exc:   # pragma: no cover
                ok = False
                print("   retrato", key, mood, "->", exc)
    check("los seis retratos se dibujan en todos los estados", ok)
    Art.draw_puck(bg, PERSP, 3.0, -10.0, 255, 0.19, trail=[(2, -12), (2.5, -11)])
    Art.draw_pad(bg, PERSP, 0.0, -36.0, PAD_W, Art.PAD_COLORS[1], 128, 0.5)
    g = GlassSmash()
    g.smash(2.0, 41.0, 0.1, random.Random(4))
    for _ in range(50):
        g.update(FIXED_DT)
    g.draw(bg, PERSP)
    check("disco, pala y cristal roto se dibujan", g.active)
    passed = sum(1 for _, c in checks if c)
    total = len(checks)
    print(f"\n{passed}/{total} comprobaciones correctas en {time.time() - t0:.1f} s")
    return 0 if passed == total else 1


# ---- 16. main ----------------------------------------------------------------------------
def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="tuxpuckpython.py",
                                 description="TuxPuck Python: remake en un fichero del clon libre de Shufflepuck Cafe.")
    ap.add_argument("--version", action="version", version=f"TuxPuck Python {VERSION}")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--fullscreen", action="store_true", help="arrancar a pantalla completa")
    g.add_argument("--windowed", action="store_true", help="arrancar en ventana")
    ap.add_argument("--opponent", choices=OPPONENT_KEYS, help="saltar los menus y jugar contra este rival")
    ap.add_argument("--points", type=int, choices=POINT_CHOICES, help="puntos para ganar")
    ap.add_argument("--mute", action="store_true", help="sin sonido")
    ap.add_argument("--no-music", action="store_true", help="sin musica (efectos si)")
    ap.add_argument("--seed", type=int, help="semilla del generador aleatorio")
    ap.add_argument("--autoplay", type=float, metavar="SECONDS", help="demo IA vs IA durante N segundos y salir")
    ap.add_argument("--screenshot", metavar="FILE", help="con --autoplay: guardar el ultimo frame")
    ap.add_argument("--selftest", action="store_true", help="ejecutar las pruebas internas y salir")
    ap.add_argument("--scale", type=float, default=None, help="escala inicial de la ventana (p. ej. 1.5)")
    ap.add_argument("--profile", metavar="FILE", help="ruta alternativa del perfil JSON")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.selftest:
        return selftest()
    if args.autoplay and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY") \
            and not sys.platform.startswith(("win", "darwin")):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    profile = Profile(args.profile)
    profile.load()
    if args.fullscreen:
        profile.settings.fullscreen = True
    elif args.windowed:
        profile.settings.fullscreen = False
    if args.scale:
        profile.settings.window_scale = clamp(args.scale, 0.5, 4.0)
        os.environ.setdefault("SDL_VIDEO_WINDOW_POS", "centered")
    if args.points:
        profile.mutators.points = args.points
    try:
        app = App(args, profile)
    except pygame.error as exc:
        sys.stderr.write(f"No se pudo abrir la ventana: {exc}\n"
                         "Pruebe con SDL_VIDEODRIVER=x11 o SDL_VIDEODRIVER=wayland, o --autoplay para modo headless.\n")
        return 2
    if args.autoplay:
        first = DemoScene(app, MatchConfig(mode="demo", opponent=args.opponent, points=args.points or DEFAULT_POINTS))
    elif args.opponent:
        cfg = MatchConfig(mode="quick", opponent=args.opponent, points=args.points or profile.mutators.points,
                          mutators=Mutators(**asdict(profile.mutators)), p1_scheme=profile.settings.p1_input)
        app.scenes.append(MainMenuScene(app))
        app.scenes[-1].enter()
        first = MatchScene(app, cfg)
    else:
        first = TitleScene(app)
    app.run(first)
    return 0


if __name__ == "__main__":
    sys.exit(main())

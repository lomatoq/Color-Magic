"""Dependency-free scene-linear RGB ↔ OKLab/OKLCH helpers."""
import colorsys
import math
from typing import Sequence, Tuple
Color4 = Tuple[float, float, float, float]

def clamp(v: float, lo: float=0.0, hi: float=1.0) -> float:
    return max(lo, min(hi, float(v)))

def color4(v: Sequence[float], alpha: float=1.0) -> Color4:
    if v is None or len(v) < 3:
        return (0.0, 0.0, 0.0, alpha)
    a = v[3] if len(v) > 3 else alpha
    return (clamp(v[0]), clamp(v[1]), clamp(v[2]), clamp(a))

def srgb_to_linear_channel(v: float) -> float:
    v = clamp(v)
    return v / 12.92 if v <= 0.04045 else pow((v + 0.055) / 1.055, 2.4)


def linear_to_srgb_channel(v: float) -> float:
    v = max(0.0, float(v))
    return 12.92 * v if v <= 0.0031308 else 1.055 * pow(v, 1.0 / 2.4) - 0.055

def linear_rgb_to_srgb(rgb: Sequence[float]):
    return tuple((clamp(linear_to_srgb_channel(v)) for v in rgb[:3]))

def _cbrt(v: float) -> float:
    return pow(v, 1.0 / 3.0) if v >= 0.0 else -pow(-v, 1.0 / 3.0)

def linear_rgb_to_oklab(rgb: Sequence[float]):
    r, g, b = map(float, rgb[:3])
    l = _cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = _cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = _cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s)

def oklab_to_linear_rgb(lab: Sequence[float]):
    L, a, b = map(float, lab[:3])
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.291485548 * b
    l, m, s = (l_ ** 3, m_ ** 3, s_ ** 3)
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s)

def oklab_to_oklch(lab: Sequence[float]):
    L, a, b = map(float, lab[:3])
    return (L, math.hypot(a, b), math.atan2(b, a))

def oklch_to_oklab(lch: Sequence[float]):
    L, C, h = map(float, lch[:3])
    return (L, C * math.cos(h), C * math.sin(h))

def shortest_angle_delta(target: float, source: float) -> float:
    return (target - source + math.pi) % (2.0 * math.pi) - math.pi

def _in_gamut(rgb: Sequence[float], tol: float=1e-06) -> bool:
    return all((-tol <= float(v) <= 1.0 + tol for v in rgb[:3]))

def gamut_map_oklch(lch: Sequence[float]):
    L, C, h = (clamp(lch[0]), max(0.0, float(lch[1])), float(lch[2]))
    rgb = oklab_to_linear_rgb(oklch_to_oklab((L, C, h)))
    if _in_gamut(rgb):
        return tuple((clamp(v) for v in rgb))
    lo, hi = (0.0, C)
    best = oklab_to_linear_rgb(oklch_to_oklab((L, 0.0, h)))
    for _ in range(18):
        mid = (lo + hi) * 0.5
        candidate = oklab_to_linear_rgb(oklch_to_oklab((L, mid, h)))
        if _in_gamut(candidate):
            lo, best = (mid, candidate)
        else:
            hi = mid
    return tuple((clamp(v) for v in best))

def relative_oklch(selected_family, captured_family, captured_material) -> Color4:
    selected = color4(selected_family)
    family = color4(captured_family)
    material = color4(captured_material)
    sL, sC, sh = oklab_to_oklch(linear_rgb_to_oklab(selected[:3]))
    fL, fC, fh = oklab_to_oklch(linear_rgb_to_oklab(family[:3]))
    mL, mC, mh = oklab_to_oklch(linear_rgb_to_oklab(material[:3]))
    L = clamp(sL + (mL - fL))
    C = sC * max(0.0, min(3.0, mC / fC)) if fC > 0.015 else sC
    h = sh + shortest_angle_delta(mh, fh) if fC > 0.015 and mC > 0.015 else sh
    r, g, b = gamut_map_oklch((L, C, h))
    return (r, g, b, material[3])

def relative_rgb_gain(selected_family, captured_family, captured_material) -> Color4:
    selected, family, material = map(color4, (selected_family, captured_family, captured_material))
    out = []
    for s, f, m in zip(selected[:3], family[:3], material[:3]):
        out.append(clamp(s * max(0.0, min(4.0, m / max(f, 0.025)))))
    return (out[0], out[1], out[2], material[3])

def apply_inheritance(selected_family, captured_family, captured_material, mode='OKLCH') -> Color4:
    selected, material = (color4(selected_family), color4(captured_material))
    mode = (mode or 'OKLCH').upper()
    if mode == 'DIRECT':
        return (selected[0], selected[1], selected[2], material[3])
    if mode == 'RGB_GAIN':
        return relative_rgb_gain(selected, captured_family, material)
    return relative_oklch(selected, captured_family, material)

def color_to_hex(v: Sequence[float], include_alpha=False) -> str:
    rgba = color4(v)
    values = [int(round(clamp(c) * 255.0)) for c in linear_rgb_to_srgb(rgba[:3])]
    if include_alpha:
        values.append(int(round(rgba[3] * 255.0)))
    return '#' + ''.join(('{:02X}'.format(x) for x in values))

def display_hsv(v: Sequence[float]):
    return colorsys.rgb_to_hsv(*linear_rgb_to_srgb(color4(v)[:3]))

def family_feature(v: Sequence[float]):
    L, C, h = oklab_to_oklch(linear_rgb_to_oklab(color4(v)[:3]))
    hue_weight = min(1.0, C / 0.12) if C > 1e-07 else 0.0
    return (math.cos(h) * hue_weight, math.sin(h) * hue_weight, min(1.0, C / 0.25))

def feature_distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum(((x - y) ** 2 for x, y in zip(a, b))))

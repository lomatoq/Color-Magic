"""Deterministic, dependency-free reference-image palette extraction.

The implementation is deliberately self-contained so Color Prime keeps working in
stock Blender installations without Pillow, OpenCV, PyTorch or network access.
It combines:

* regular-grid image sampling;
* alpha-aware and border-aware background rejection;
* deterministic weighted k-means++-style clustering in OKLab;
* hue-family merging so lighting/shadow variants do not become fake accents;
* perceptual pair scoring for Main/Accent roles;
* hue-preserving UI cleanup in OKLCH with gamut compression.

All RGB values accepted and returned by this module are scene-linear floats.
"""
from dataclasses import dataclass, field
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .color_math import (
    clamp,
    color4,
    gamut_map_oklch,
    linear_rgb_to_oklab,
    oklab_to_linear_rgb,
    oklab_to_oklch,
    shortest_angle_delta,
)

Color3 = Tuple[float, float, float]
Color4 = Tuple[float, float, float, float]
Lab3 = Tuple[float, float, float]


@dataclass
class HistogramPoint:
    lab: Lab3
    rgb: Color3
    weight: float
    border_weight: float
    side_mask: int
    saliency: float
    count: int


@dataclass
class ColorCluster:
    lab: Lab3
    rgb: Color3
    weight: float
    coverage: float
    border_share: float
    side_mask: int
    saliency: float
    members: List[int] = field(default_factory=list)
    is_background: bool = False

    @property
    def lightness(self) -> float:
        return self.lab[0]

    @property
    def chroma(self) -> float:
        return math.hypot(self.lab[1], self.lab[2])

    @property
    def hue(self) -> float:
        return math.atan2(self.lab[2], self.lab[1])

    @property
    def side_count(self) -> int:
        return sum(1 for bit in (1, 2, 4, 8) if self.side_mask & bit)


@dataclass
class ColorFamily:
    lab: Lab3
    rgb: Color3
    weight: float
    coverage: float
    saliency: float
    cluster_indices: List[int]
    main_score: float = 0.0

    @property
    def lightness(self) -> float:
        return self.lab[0]

    @property
    def chroma(self) -> float:
        return math.hypot(self.lab[1], self.lab[2])

    @property
    def hue(self) -> float:
        return math.atan2(self.lab[2], self.lab[1])


@dataclass
class PaletteProposal:
    name: str
    main: Color4
    accent: Color4
    score: float
    confidence: float
    reason: str
    main_coverage: float
    accent_coverage: float
    variant: str


@dataclass
class PaletteAnalysis:
    proposals: List[PaletteProposal]
    clusters: List[ColorCluster]
    families: List[ColorFamily]
    sampled_pixels: int
    accepted_pixels: int
    transparent_fraction: float
    background_rgb: Optional[Color3]
    background_confidence: float
    summary: str


def _distance_sq(a: Sequence[float], b: Sequence[float]) -> float:
    return sum((float(x) - float(y)) ** 2 for x, y in zip(a[:3], b[:3]))


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(_distance_sq(a, b))


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    if edge1 < edge0:
        return 1.0 - _smoothstep(edge1, edge0, x)
    if edge1 == edge0:
        return 1.0 if x >= edge1 else 0.0
    t = clamp((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _weighted_mean(points: Sequence[HistogramPoint], indices: Sequence[int], centerspace: str = 'lab'):
    total = sum(max(points[i].weight, 1e-12) for i in indices)
    if total <= 0.0:
        return (0.0, 0.0, 0.0)
    values = [0.0, 0.0, 0.0]
    for i in indices:
        p = points[i]
        source = p.lab if centerspace == 'lab' else p.rgb
        w = max(p.weight, 1e-12)
        for c in range(3):
            values[c] += source[c] * w
    return (values[0] / total, values[1] / total, values[2] / total)


def _lab_to_rgb(lab: Sequence[float]) -> Color3:
    rgb = oklab_to_linear_rgb(lab)
    if all(-1e-6 <= x <= 1.0 + 1e-6 for x in rgb):
        return tuple(clamp(x) for x in rgb)
    return tuple(gamut_map_oklch(oklab_to_oklch(lab)))


def _sample_grid(width: int, height: int, pixels: Sequence[float], max_samples: int,
                 alpha_threshold: float, premultiplied: bool):
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ValueError('Reference image has no pixels')
    if len(pixels) < width * height * 4:
        raise ValueError('Reference image pixel buffer is incomplete')
    max_samples = max(256, int(max_samples))
    aspect = width / float(max(height, 1))
    grid_w = min(width, max_samples, max(1, int(math.sqrt(max_samples * aspect))))
    grid_h = min(height, max(1, max_samples // grid_w))

    raw = []
    transparent = 0
    sampled = 0
    for gy in range(grid_h):
        y = min(height - 1, int((gy + 0.5) * height / grid_h))
        row = []
        for gx in range(grid_w):
            x = min(width - 1, int((gx + 0.5) * width / grid_w))
            idx = (y * width + x) * 4
            r = max(0.0, float(pixels[idx]))
            g = max(0.0, float(pixels[idx + 1]))
            b = max(0.0, float(pixels[idx + 2]))
            a = clamp(float(pixels[idx + 3]))
            sampled += 1
            if a <= alpha_threshold:
                transparent += 1
                row.append(None)
                continue
            if premultiplied and a > 1e-6:
                r, g, b = (clamp(r / a), clamp(g / a), clamp(b / a))
            rgb = (clamp(r), clamp(g), clamp(b))
            lab = linear_rgb_to_oklab(rgb)
            side_mask = 0
            border_thickness_x = max(1, int(round(grid_w * 0.055)))
            border_thickness_y = max(1, int(round(grid_h * 0.055)))
            if gx < border_thickness_x:
                side_mask |= 1
            if gx >= grid_w - border_thickness_x:
                side_mask |= 2
            if gy < border_thickness_y:
                side_mask |= 4
            if gy >= grid_h - border_thickness_y:
                side_mask |= 8
            row.append({'rgb': rgb, 'lab': lab, 'alpha': a, 'side_mask': side_mask})
        raw.append(row)

    accepted = 0
    points = []
    # Local contrast is used only as a mild saliency prior. It must not turn
    # anti-aliased borders or texture noise into dominant colors.
    for gy, row in enumerate(raw):
        for gx, item in enumerate(row):
            if item is None:
                continue
            accepted += 1
            neighbor_distances = []
            for nx, ny in ((gx - 1, gy), (gx + 1, gy), (gx, gy - 1), (gx, gy + 1)):
                if 0 <= nx < grid_w and 0 <= ny < grid_h:
                    other = raw[ny][nx]
                    if other is not None:
                        neighbor_distances.append(_distance(item['lab'], other['lab']))
            local = sum(neighbor_distances) / len(neighbor_distances) if neighbor_distances else 0.0
            saliency = clamp(local / 0.16)
            alpha = item['alpha']
            weight = alpha * (0.82 + 0.18 * saliency)
            border_weight = weight if item['side_mask'] else 0.0
            points.append((item['lab'], item['rgb'], weight, border_weight, item['side_mask'], saliency))
    return points, sampled, accepted, transparent / float(max(sampled, 1))


def _histogram(sampled_points) -> List[HistogramPoint]:
    bins: Dict[Tuple[int, int, int], list] = {}
    for lab, rgb, weight, border_weight, side_mask, saliency in sampled_points:
        key = (
            int(round(lab[0] / 0.018)),
            int(round(lab[1] / 0.018)),
            int(round(lab[2] / 0.018)),
        )
        item = bins.get(key)
        if item is None:
            item = [0.0] * 10
            bins[key] = item
        item[0] += lab[0] * weight
        item[1] += lab[1] * weight
        item[2] += lab[2] * weight
        item[3] += rgb[0] * weight
        item[4] += rgb[1] * weight
        item[5] += rgb[2] * weight
        item[6] += weight
        item[7] += border_weight
        item[8] = int(item[8]) | int(side_mask)
        item[9] += saliency * weight
    out = []
    for item in bins.values():
        w = max(item[6], 1e-12)
        out.append(HistogramPoint(
            lab=(item[0] / w, item[1] / w, item[2] / w),
            rgb=(item[3] / w, item[4] / w, item[5] / w),
            weight=w,
            border_weight=item[7],
            side_mask=int(item[8]),
            saliency=item[9] / w,
            count=1,
        ))
    out.sort(key=lambda p: (-p.weight, p.lab[0], p.lab[1], p.lab[2]))
    return out


def _initial_centers(points: Sequence[HistogramPoint], count: int) -> List[Lab3]:
    if not points:
        return []
    count = min(max(1, count), len(points))
    centers = [points[0].lab]
    chosen = {0}
    while len(centers) < count:
        best_index = None
        best_score = -1.0
        for i, point in enumerate(points):
            if i in chosen:
                continue
            d2 = min(_distance_sq(point.lab, center) for center in centers)
            # Deterministic weighted farthest-point version of D² seeding.
            score = d2 * pow(max(point.weight, 1e-12), 0.30)
            if score > best_score:
                best_score = score
                best_index = i
        if best_index is None:
            break
        chosen.add(best_index)
        centers.append(points[best_index].lab)
    return centers


def _cluster_points(points: Sequence[HistogramPoint], cluster_count: int) -> List[ColorCluster]:
    if not points:
        return []
    centers = _initial_centers(points, cluster_count)
    assignment = [-1] * len(points)
    for _ in range(18):
        changed = False
        groups = [[] for _ in centers]
        for i, point in enumerate(points):
            nearest = min(range(len(centers)), key=lambda c: (_distance_sq(point.lab, centers[c]), c))
            groups[nearest].append(i)
            if assignment[i] != nearest:
                assignment[i] = nearest
                changed = True
        next_centers = []
        for center, indices in zip(centers, groups):
            if not indices:
                next_centers.append(center)
                continue
            provisional = _weighted_mean(points, indices, 'lab')
            # Trim the farthest 12% of cluster mass. This suppresses JPEG/AA
            # contamination without throwing away legitimate smaller shades.
            ranked = sorted(indices, key=lambda i: (_distance_sq(points[i].lab, provisional), i))
            target = sum(points[i].weight for i in ranked) * 0.88
            kept = []
            acc = 0.0
            for i in ranked:
                kept.append(i)
                acc += points[i].weight
                if acc >= target:
                    break
            next_centers.append(_weighted_mean(points, kept, 'lab'))
        movement = max((_distance(a, b) for a, b in zip(centers, next_centers)), default=0.0)
        centers = next_centers
        if not changed or movement < 1e-5:
            break

    groups = [[] for _ in centers]
    for i, point in enumerate(points):
        nearest = min(range(len(centers)), key=lambda c: (_distance_sq(point.lab, centers[c]), c))
        groups[nearest].append(i)
    total_weight = sum(p.weight for p in points)
    total_border = sum(p.border_weight for p in points)
    clusters = []
    for center, indices in zip(centers, groups):
        if not indices:
            continue
        weight = sum(points[i].weight for i in indices)
        border = sum(points[i].border_weight for i in indices)
        side_mask = 0
        saliency = 0.0
        for i in indices:
            side_mask |= points[i].side_mask
            saliency += points[i].saliency * points[i].weight
        lab = center
        clusters.append(ColorCluster(
            lab=lab,
            rgb=_lab_to_rgb(lab),
            weight=weight,
            coverage=weight / max(total_weight, 1e-12),
            border_share=border / max(total_border, 1e-12),
            side_mask=side_mask,
            saliency=saliency / max(weight, 1e-12),
            members=list(indices),
        ))
    clusters.sort(key=lambda c: (-c.weight, c.lab[0], c.lab[1], c.lab[2]))
    return clusters


def _merge_close_clusters(clusters: Sequence[ColorCluster]) -> List[ColorCluster]:
    work = list(clusters)
    changed = True
    while changed and len(work) > 1:
        changed = False
        best = None
        for i in range(len(work)):
            for j in range(i + 1, len(work)):
                a, b = work[i], work[j]
                dist = _distance(a.lab, b.lab)
                al, ac, ah = oklab_to_oklch(a.lab)
                bl, bc, bh = oklab_to_oklch(b.lab)
                hue = abs(shortest_angle_delta(ah, bh))
                close = dist < 0.028 or (hue < math.radians(7.0) and abs(al - bl) < 0.045 and abs(ac - bc) < 0.035)
                if close and (best is None or dist < best[0]):
                    best = (dist, i, j)
        if best is None:
            break
        _, i, j = best
        a, b = work[i], work[j]
        w = a.weight + b.weight
        lab = tuple((a.lab[k] * a.weight + b.lab[k] * b.weight) / max(w, 1e-12) for k in range(3))
        merged = ColorCluster(
            lab=lab,
            rgb=_lab_to_rgb(lab),
            weight=w,
            coverage=a.coverage + b.coverage,
            border_share=a.border_share + b.border_share,
            side_mask=a.side_mask | b.side_mask,
            saliency=(a.saliency * a.weight + b.saliency * b.weight) / max(w, 1e-12),
            members=a.members + b.members,
        )
        work.pop(j)
        work.pop(i)
        work.append(merged)
        work.sort(key=lambda c: (-c.weight, c.lab[0], c.lab[1], c.lab[2]))
        changed = True
    return work


def _detect_background(clusters: Sequence[ColorCluster], mode: str):
    mode = (mode or 'AUTO').upper()
    if not clusters or mode == 'KEEP':
        return (None, 0.0)
    ranked = []
    for i, cluster in enumerate(clusters):
        side_support = cluster.side_count / 4.0
        score = 0.63 * clamp(cluster.border_share) + 0.25 * clamp(cluster.coverage / 0.65) + 0.12 * side_support
        qualifies = cluster.border_share >= 0.42 and cluster.coverage >= 0.055 and cluster.side_count >= 2
        if mode == 'FORCE_BORDER':
            qualifies = cluster.border_share >= 0.20 and cluster.side_count >= 1
        if qualifies:
            ranked.append((score, i))
    if not ranked:
        return (None, 0.0)
    score, index = max(ranked, key=lambda x: (x[0], clusters[x[1]].coverage, -x[1]))
    if mode == 'AUTO' and score < 0.52:
        return (None, score)
    clusters[index].is_background = True
    return (index, clamp((score - 0.40) / 0.55))


def _family_compatible(a: ColorFamily, cluster: ColorCluster) -> bool:
    aL, aC, ah = oklab_to_oklch(a.lab)
    bL, bC, bh = oklab_to_oklch(cluster.lab)
    if aC < 0.030 and bC < 0.030:
        return abs(aL - bL) <= 0.20
    if min(aC, bC) < 0.012:
        return False
    hue = abs(shortest_angle_delta(ah, bh))
    # Large lightness changes are accepted only with exceptionally stable hue.
    light_limit = 0.42 if hue < math.radians(9.0) else 0.28
    chroma_ratio = min(aC, bC) / max(aC, bC, 1e-9)
    return hue <= math.radians(20.0) and abs(aL - bL) <= light_limit and chroma_ratio >= 0.18


def _weighted_quantile(values: Sequence[Tuple[float, float]], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values, key=lambda item: item[0])
    total = sum(max(0.0, w) for _, w in ordered)
    target = clamp(quantile) * total
    acc = 0.0
    for value, weight in ordered:
        acc += max(0.0, weight)
        if acc >= target:
            return value
    return ordered[-1][0]


def _recompute_family(clusters: Sequence[ColorCluster], indices: Sequence[int]) -> ColorFamily:
    selected = [clusters[i] for i in indices]
    weight = sum(c.weight for c in selected)
    coverage = sum(c.coverage for c in selected)
    saliency = sum(c.saliency * c.weight for c in selected) / max(weight, 1e-12)
    l_values = [(c.lightness, c.weight) for c in selected]
    c_values = [(c.chroma, c.weight) for c in selected]
    L = _weighted_quantile(l_values, 0.58)
    C = _weighted_quantile(c_values, 0.68)
    hx = 0.0
    hy = 0.0
    for c in selected:
        cw = c.weight * max(c.chroma, 0.008)
        hx += math.cos(c.hue) * cw
        hy += math.sin(c.hue) * cw
    if math.hypot(hx, hy) <= 1e-9:
        a = sum(c.lab[1] * c.weight for c in selected) / max(weight, 1e-12)
        b = sum(c.lab[2] * c.weight for c in selected) / max(weight, 1e-12)
        h = math.atan2(b, a)
    else:
        h = math.atan2(hy, hx)
    if C < 0.012:
        C = 0.0
    lab = (L, C * math.cos(h), C * math.sin(h))
    return ColorFamily(lab=lab, rgb=_lab_to_rgb(lab), weight=weight, coverage=coverage,
                       saliency=saliency, cluster_indices=list(indices))


def _build_families(clusters: Sequence[ColorCluster]) -> List[ColorFamily]:
    foreground = [i for i, c in enumerate(clusters) if not c.is_background]
    foreground.sort(key=lambda i: (-clusters[i].weight, i))
    groups: List[List[int]] = []
    families: List[ColorFamily] = []
    for index in foreground:
        cluster = clusters[index]
        match = None
        match_distance = None
        for gi, family in enumerate(families):
            if _family_compatible(family, cluster):
                hue_delta = abs(shortest_angle_delta(family.hue, cluster.hue))
                score = hue_delta + 0.35 * abs(family.lightness - cluster.lightness)
                if match is None or score < match_distance:
                    match = gi
                    match_distance = score
        if match is None:
            groups.append([index])
            families.append(_recompute_family(clusters, groups[-1]))
        else:
            groups[match].append(index)
            families[match] = _recompute_family(clusters, groups[match])
    total = sum(f.coverage for f in families)
    if total > 1e-12:
        for family in families:
            family.coverage /= total
    for family in families:
        area = math.sqrt(clamp(family.coverage))
        chroma = _smoothstep(0.018, 0.16, family.chroma)
        mid = clamp(1.0 - abs(family.lightness - 0.56) / 0.56)
        extreme_penalty = _smoothstep(0.84, 0.97, family.lightness) + _smoothstep(0.12, 0.01, family.lightness)
        family.main_score = clamp(0.61 * area + 0.16 * chroma + 0.14 * mid + 0.09 * family.saliency - 0.15 * extreme_penalty)
    families.sort(key=lambda f: (-f.main_score, -f.coverage, f.lightness))
    return families


def _accent_score(main: ColorFamily, accent: ColorFamily) -> float:
    distance = clamp(_distance(main.lab, accent.lab) / 0.34)
    area = clamp(math.sqrt(accent.coverage * 3.0))
    chroma = _smoothstep(0.018, 0.17, accent.chroma)
    contrast = clamp(abs(main.lightness - accent.lightness) / 0.34)
    if main.chroma > 0.025 and accent.chroma > 0.025:
        hue = clamp(abs(shortest_angle_delta(main.hue, accent.hue)) / math.pi)
    else:
        hue = contrast * 0.65
    tiny_penalty = _smoothstep(0.018, 0.002, accent.coverage)
    same_family_penalty = _smoothstep(0.11, 0.035, _distance(main.lab, accent.lab))
    return clamp(0.24 * area + 0.33 * distance + 0.18 * chroma + 0.11 * hue +
                 0.09 * contrast + 0.05 * accent.saliency - 0.16 * tiny_penalty - 0.20 * same_family_penalty)


def _pair_candidates(families: Sequence[ColorFamily]):
    pairs = []
    for mi, main in enumerate(families):
        for ai, accent in enumerate(families):
            if mi == ai:
                continue
            accent_score = _accent_score(main, accent)
            role_order = clamp((main.coverage - accent.coverage + 0.20) / 0.45)
            score = clamp(0.52 * main.main_score + 0.43 * accent_score + 0.05 * role_order)
            pairs.append((score, mi, ai))
    pairs.sort(key=lambda item: (-item[0], item[1], item[2]))
    return pairs


def _polish(rgb: Sequence[float], strength: float, role: str, bold: bool = False) -> Color3:
    """Cleanup is bounded and preserves intentional neutrals/pastels.

    Only Expressive/Bold is allowed to substantially increase chroma. No minimum
    chroma floor: beige is not a dirty yellow and gray is not a failed accent.
    """
    strength = clamp(strength)
    L, C, h = oklab_to_oklch(linear_rgb_to_oklab(rgb[:3]))
    if not bold:
        if C < .025:
            return tuple(rgb[:3])
        C += min(.009 * strength, C * .10 * strength)
    else:
        if C > .014:
            C += min(.055 * strength, C * .40 * strength)
        L += (clamp(L,.18,.90)-L) * strength
    return tuple(gamut_map_oklch((L,C,h)))


def _separate_pair(main: Color3, accent: Color3, strength: float) -> Tuple[Color3, Color3]:
    main_lab = linear_rgb_to_oklab(main)
    accent_lab = linear_rgb_to_oklab(accent)
    distance = _distance(main_lab, accent_lab)
    if distance >= 0.105:
        return (main, accent)
    mL, mC, mh = oklab_to_oklch(main_lab)
    aL, aC, ah = oklab_to_oklch(accent_lab)
    direction = -1.0 if aL <= mL else 1.0
    if abs(aL - mL) < 0.035:
        direction = 1.0 if mL < 0.58 else -1.0
    needed = (0.105 - distance) * (0.85 + 0.35 * clamp(strength))
    aL = clamp(aL + direction * max(0.055, needed), 0.16, 0.90)
    accent = tuple(gamut_map_oklch((aL, aC, ah)))
    return (main, accent)


def _fallback_single_family(family: ColorFamily, clean_strength: float):
    main = _polish(family.rgb, clean_strength, 'MAIN', False)
    L, C, h = oklab_to_oklch(linear_rgb_to_oklab(main))
    target_l = L - 0.18 if L >= 0.55 else L + 0.20
    accent = tuple(gamut_map_oklch((clamp(target_l, 0.16, 0.90), C * 0.70, h)))
    return main, accent


def _proposal(name: str, variant: str, main_rgb: Color3, accent_rgb: Color3,
              score: float, main_cov: float, accent_cov: float, reason: str) -> PaletteProposal:
    confidence = clamp((score - 0.38) / 0.55)
    return PaletteProposal(
        name=name,
        main=(main_rgb[0], main_rgb[1], main_rgb[2], 1.0),
        accent=(accent_rgb[0], accent_rgb[1], accent_rgb[2], 1.0),
        score=clamp(score),
        confidence=confidence,
        reason=reason,
        main_coverage=clamp(main_cov),
        accent_coverage=clamp(accent_cov),
        variant=variant,
    )


def analyze_rgba_pixels(width: int, height: int, pixels: Sequence[float], max_samples: int = 10000,
                        cluster_count: int = 8, alpha_threshold: float = 0.04,
                        clean_strength: float = 0.68, background_mode: str = 'AUTO',
                        premultiplied: bool = False) -> PaletteAnalysis:
    sampled_points, sampled, accepted, transparent_fraction = _sample_grid(
        width, height, pixels, max_samples, alpha_threshold, premultiplied)
    if accepted < 8:
        raise ValueError('Reference image has too few visible pixels')
    histogram = _histogram(sampled_points)
    clusters = _cluster_points(histogram, min(max(3, int(cluster_count)), 12))
    clusters = _merge_close_clusters(clusters)
    background_index, background_confidence = _detect_background(clusters, background_mode)
    families = _build_families(clusters)
    if not families:
        # An over-eager background decision should never make analysis useless.
        for cluster in clusters:
            cluster.is_background = False
        background_index = None
        background_confidence = 0.0
        families = _build_families(clusters)
    if not families:
        raise ValueError('Could not find a stable foreground color family')

    proposals = []
    pairs = _pair_candidates(families)
    if pairs:
        top_score, mi, ai = pairs[0]
        main_family, accent_family = families[mi], families[ai]
        faithful_main, faithful_accent = main_family.rgb, accent_family.rgb
        clean_main = _polish(faithful_main, clean_strength, 'MAIN', False)
        clean_accent = _polish(faithful_accent, clean_strength, 'ACCENT', False)
        proposals.append(_proposal(
            'Clean Pick', 'CLEAN', clean_main, clean_accent, min(1.0, top_score + 0.035),
            main_family.coverage, accent_family.coverage,
            'dominant foreground family + distinct salient family; shadows merged by hue'))
        proposals.append(_proposal(
            'Faithful', 'FAITHFUL', faithful_main, faithful_accent, top_score,
            main_family.coverage, accent_family.coverage,
            'trimmed perceptual cluster representatives from the reference'))
        bold_main = _polish(faithful_main, min(1.0, clean_strength + 0.22), 'MAIN', True)
        bold_accent = _polish(faithful_accent, min(1.0, clean_strength + 0.22), 'ACCENT', True)
        bold_main, bold_accent = _separate_pair(bold_main, bold_accent, 1.0)
        proposals.append(_proposal(
            'Expressive', 'BOLD', bold_main, bold_accent, max(0.0, top_score - 0.025),
            main_family.coverage, accent_family.coverage,
            'intentional expressive variation; stronger chroma, not literal extraction'))
        # Add one genuinely different pair, not merely Main/Accent swapped.
        for alt_score, alt_mi, alt_ai in pairs[1:]:
            alt_main = families[alt_mi]
            alt_accent = families[alt_ai]
            if alt_mi == ai and alt_ai == mi:
                continue
            if _distance(alt_main.lab, main_family.lab) < 0.045 and _distance(alt_accent.lab, accent_family.lab) < 0.045:
                continue
            am = _polish(alt_main.rgb, clean_strength, 'MAIN', False)
            aa = _polish(alt_accent.rgb, clean_strength, 'ACCENT', False)
            am, aa = _separate_pair(am, aa, clean_strength)
            proposals.append(_proposal(
                'Alternative', 'ALTERNATIVE', am, aa, alt_score,
                alt_main.coverage, alt_accent.coverage,
                'next-best structurally different Main/Accent interpretation'))
            break
    else:
        main, accent = _fallback_single_family(families[0], clean_strength)
        proposals.append(_proposal(
            'Monochrome Pair', 'DERIVED', main, accent, 0.48,
            families[0].coverage, 0.0,
            'reference contains one stable hue family; Accent is a derived value variant'))

    # Keep the three named interpretations even when a flat source image
    # makes their numeric difference subtle. Only an optional Alternative is
    # removed when it is effectively identical to an existing interpretation.
    unique = []
    for proposal in proposals:
        if proposal.variant != 'ALTERNATIVE':
            unique.append(proposal)
            continue
        duplicate = any(
            _distance(linear_rgb_to_oklab(proposal.main[:3]), linear_rgb_to_oklab(other.main[:3])) < 0.006 and
            _distance(linear_rgb_to_oklab(proposal.accent[:3]), linear_rgb_to_oklab(other.accent[:3])) < 0.006
            for other in unique
        )
        if not duplicate:
            unique.append(proposal)
    proposals = unique[:4]
    background_rgb = clusters[background_index].rgb if background_index is not None else None
    summary = '{} samples · {} color clusters · {} hue families · {} proposal(s)'.format(
        accepted, len(clusters), len(families), len(proposals))
    if background_index is not None:
        summary += ' · border background ignored'
    elif transparent_fraction >= 0.08:
        summary += ' · alpha background ignored'
    return PaletteAnalysis(
        proposals=proposals,
        clusters=clusters,
        families=families,
        sampled_pixels=sampled,
        accepted_pixels=accepted,
        transparent_fraction=transparent_fraction,
        background_rgb=background_rgb,
        background_confidence=background_confidence,
        summary=summary,
    )

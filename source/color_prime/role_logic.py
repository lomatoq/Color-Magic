"""Pure decision layer for automatic Main/Accent role inference.

Keeping the decision graph Blender-free makes it deterministic and testable.  The
Blender integration supplies structural signals (node-group lineage, material
identity, object names and camera/surface weights) and this module resolves them
with explicit confidence/reason values.
"""
from dataclasses import dataclass
from typing import Dict, List, Sequence


@dataclass
class RoleSignal:
    key: str
    weight: float = 1.0
    explicit: str = ''
    name_hint: str = ''
    lineage: str = ''
    material_group: str = ''
    fixed: bool = False
    sort_key: str = ''


@dataclass
class RoleDecision:
    key: str
    family: str
    confidence: float
    reason: str


def _opposite(family: str) -> str:
    return 'ACCENT' if family == 'MAIN' else 'MAIN'


def _assign_two_groups(signals: Sequence[RoleSignal], unresolved: Sequence[RoleSignal], attr: str,
                       label: str, decisions: Dict[str, RoleDecision]) -> bool:
    # Include already resolved members: the old implementation only grouped
    # unresolved signals, so its explicit-anchor branch could never run.
    groups = {}
    for signal in signals:
        if signal.fixed or signal.explicit in {'FIXED', 'IGNORE'}:
            continue
        decided = decisions.get(signal.key)
        if decided is not None and decided.family in {'FIXED', 'IGNORE'}:
            continue
        value = str(getattr(signal, attr, '') or '')
        if value:
            groups.setdefault(value, []).append(signal)
    if not groups:
        return False

    anchored = {}
    conflicting = set()
    for key, group in groups.items():
        known = {decisions[s.key].family for s in group
                 if s.key in decisions and decisions[s.key].family in {'MAIN', 'ACCENT'}}
        if len(known) > 1:
            conflicting.add(key)
        elif known:
            anchored[key] = next(iter(known))

    # Propagate an anchor even when only one lineage is present. A conflicting
    # source is withheld rather than assigning its remaining children by size.
    for key, group in groups.items():
        for signal in group:
            if signal.key in decisions:
                continue
            if key in conflicting:
                decisions[signal.key] = RoleDecision(
                    signal.key, 'IGNORE', 0.0, 'conflicting role anchors in {}; review required'.format(label))
            elif key in anchored:
                decisions[signal.key] = RoleDecision(
                    signal.key, anchored[key], 0.99 if attr == 'lineage' else 0.95,
                    '{} role inherited from an explicit anchor'.format(label))

    if len(groups) != 2 or conflicting:
        return bool(anchored or conflicting)
    keys = sorted(groups)
    # An ungrouped subject is not evidence for a two-family partition.
    if any(s.key not in decisions and not str(getattr(s, attr, '') or '') for s in unresolved):
        return bool(anchored)
    weights = {k: sum(max(s.weight, 1e-9) for s in group) for k, group in groups.items()}
    if len(anchored) == 2:
        mapping = dict(anchored)  # equal explicit roles are valid: never force Accent
    elif len(anchored) == 1:
        key = next(iter(anchored))
        mapping = {k: anchored[key] if k == key else _opposite(anchored[key]) for k in keys}
    else:
        main_key = sorted(keys, key=lambda k: (-weights[k], k))[0]
        mapping = {k: ('MAIN' if k == main_key else 'ACCENT') for k in keys}
    ordered_weights = sorted(weights.values(), reverse=True)
    dominance = min(1.0, max(0.0, ordered_weights[0] / max(ordered_weights[1], 1e-9) - 1.0))
    confidence = (0.99 if attr == 'lineage' else 0.95) if anchored else (
        (0.78 if attr == 'lineage' else 0.70) + (0.16 if attr == 'lineage' else 0.18) * dominance)
    reason = ('two {} groups with an explicit role anchor'.format(label) if anchored
              else 'two {} groups; larger projected group proposed as Main'.format(label))
    for key, group in groups.items():
        for signal in group:
            if signal.key not in decisions:
                decisions[signal.key] = RoleDecision(signal.key, mapping[key], confidence, reason)
    return True


def infer_roles(signals: Sequence[RoleSignal]) -> List[RoleDecision]:
    """Infer roles in descending order of evidence strength.

    Priority:
      1. hard Fixed / explicit metadata;
      2. unambiguous Main/Accent name hints;
      3. exactly two inherited node-group lineages;
      4. exactly two material-identity groups;
      5. exactly two geometry subjects;
      6. conservative dominant-vs-detail geometry split.
    """
    decisions: Dict[str, RoleDecision] = {}
    ordered = list(signals)
    for signal in ordered:
        if signal.fixed:
            decisions[signal.key] = RoleDecision(signal.key, 'FIXED', 1.0, 'explicit fixed-name marker')
        elif signal.explicit in {'MAIN', 'ACCENT', 'FIXED', 'IGNORE'}:
            decisions[signal.key] = RoleDecision(signal.key, signal.explicit, 1.0, 'explicit saved role')
    for signal in ordered:
        if signal.key in decisions:
            continue
        if signal.name_hint in {'MAIN', 'ACCENT'}:
            decisions[signal.key] = RoleDecision(signal.key, signal.name_hint, 0.94, 'unambiguous semantic name')

    unresolved = [s for s in ordered if s.key not in decisions]
    if unresolved:
        _assign_two_groups(ordered, unresolved, 'lineage', 'inherited shader-family', decisions)
    unresolved = [s for s in ordered if s.key not in decisions]
    if unresolved:
        _assign_two_groups(ordered, unresolved, 'material_group', 'material-identity', decisions)
    unresolved = [s for s in ordered if s.key not in decisions]
    if len(unresolved) == 2:
        sorted_two = sorted(unresolved, key=lambda s: (-max(s.weight, 1e-9), s.sort_key or s.key))
        ratio = max(sorted_two[0].weight, 1e-9) / max(sorted_two[1].weight, 1e-9)
        confidence = 0.58 + 0.30 * min(1.0, max(0.0, ratio - 1.0))
        qualifier = 'larger visible subject' if ratio >= 1.08 else 'deterministic tie-break between similarly sized subjects'
        decisions[sorted_two[0].key] = RoleDecision(
            sorted_two[0].key, 'MAIN', confidence, 'two geometry subjects; {} proposed as Main'.format(qualifier))
        decisions[sorted_two[1].key] = RoleDecision(
            sorted_two[1].key, 'ACCENT', confidence, 'two geometry subjects; the other subject proposed as Accent')
    unresolved = [s for s in ordered if s.key not in decisions]
    if unresolved:
        ranked = sorted(unresolved, key=lambda s: (-max(s.weight, 1e-9), s.sort_key or s.key))
        total = sum(max(s.weight, 1e-9) for s in ranked)
        cumulative = 0.0
        max_weight = max(ranked[0].weight, 1e-9)
        for index, signal in enumerate(ranked):
            normalized = max(signal.weight, 1e-9) / max(total, 1e-9)
            # Keep large shell/body pieces together as Main until they cover
            # roughly two thirds of visible geometry. Remaining details become
            # Accent, but never assign the only subject to Accent.
            choose_main = index == 0 or cumulative < 0.66 or signal.weight >= max_weight * 0.52
            family = 'MAIN' if choose_main else 'ACCENT'
            confidence = 0.68 if family == 'MAIN' else 0.61
            reason = 'dominant visible geometry' if family == 'MAIN' else 'secondary/detail geometry'
            decisions[signal.key] = RoleDecision(signal.key, family, confidence, reason)
            cumulative += normalized
        # If everything ended up Main and there is more than one subject, force
        # the smallest plausible detail to Accent; this is a proposal, not a
        # semantic claim, so confidence remains deliberately moderate.
        assigned = [decisions[s.key] for s in ranked]
        if len(assigned) > 1 and not any(d.family == 'ACCENT' for d in assigned):
            smallest = ranked[-1]
            decisions[smallest.key] = RoleDecision(
                smallest.key, 'ACCENT', 0.56, 'smallest detail chosen to complete a two-family setup')

    return [decisions[s.key] for s in ordered if s.key in decisions]

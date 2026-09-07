from typing import Dict, Iterable, List, Sequence, Set, Tuple
import re
import bpy
from .color_math import color4, family_feature, feature_distance
from .constants import ACCENT_NAME_TOKENS, ICON_COLLECTION_TOKENS, MAIN_NAME_TOKENS
from .targets import apply_spec_to_binding, find_material_target, read_binding_color
from .utils import data_block_identity, iter_collection_tree, material_usage_for_objects, objects_from_icon_item

def _contains(value, tokens: Iterable[str]):
    value = (value or '').casefold()
    return any((t.casefold() in value for t in tokens))

def _has_mesh_obj(root):
    if root is None:
        return False
    stack, seen = ([root], set())
    while stack:
        obj = stack.pop()
        ptr = obj.as_pointer()
        if ptr in seen:
            continue
        seen.add(ptr)
        if obj.type == 'MESH':
            return True
        stack.extend(list(obj.children))
    return False

def _has_mesh_coll(coll):
    if coll is None:
        return False
    try:
        return any((o.type == 'MESH' for o in coll.all_objects))
    except Exception:
        return any((o.type == 'MESH' for o in coll.objects))

def _identity(kind, obj=None, coll=None):
    return kind + '::' + data_block_identity(coll if kind == 'COLLECTION' else obj)

def _candidate_mesh_signature(kind, obj=None, coll=None):
    meshes = []
    if kind == 'OBJECT' and obj is not None:
        stack, seen = [obj], set()
        while stack:
            item = stack.pop()
            ptr = item.as_pointer()
            if ptr in seen:
                continue
            seen.add(ptr)
            if item.type == 'MESH':
                meshes.append(ptr)
            stack.extend(list(item.children))
    elif kind == 'COLLECTION' and coll is not None:
        try:
            objects = coll.all_objects
        except Exception:
            objects = coll.objects
        meshes.extend(o.as_pointer() for o in objects if o.type == 'MESH')
    return frozenset(meshes)

def add_icon_root(settings, obj=None, collection=None, name='', auto_detected=False):
    kind = 'COLLECTION' if collection is not None else 'OBJECT'
    key = _identity(kind, obj, collection)
    for item in settings.icon_sets:
        if key == _identity(item.root_kind, item.object_root, item.collection_root):
            return False
    item = settings.icon_sets.add()
    item.root_kind = kind
    item.object_root = obj
    item.collection_root = collection
    source = collection.name if collection else obj.name if obj else 'Icon'
    item.name = name or (source[len('Anchor_'):] if source.startswith('Anchor_') else source)
    item.enabled = True
    item.auto_detected = auto_detected
    item.status = 'Detected' if auto_detected else 'Marked'
    return True

def detect_icon_sets(scene, settings, replace=True):
    previous = {}
    for item in settings.icon_sets:
        previous[_identity(item.root_kind, item.object_root, item.collection_root)] = (item.name, item.enabled)
    if replace:
        settings.icon_sets.clear()
    mode, candidates = (settings.detection_mode, [])
    if mode in {'TAGGED', 'SAFE', 'SMART'}:
        for coll in iter_collection_tree(scene.collection):
            if coll != scene.collection and bool(coll.get('color_prime_icon_set', False)) and _has_mesh_coll(coll):
                candidates.append(('COLLECTION', None, coll, coll.name, 'tagged collection'))
        for obj in scene.objects:
            if bool(obj.get('color_prime_icon_set', False)) and _has_mesh_obj(obj):
                candidates.append(('OBJECT', obj, None, obj.name, 'tagged object'))
    if mode in {'LEGACY', 'SAFE', 'SMART'}:
        for obj in scene.objects:
            if obj.name.startswith('Anchor_') and _has_mesh_obj(obj):
                candidates.append(('OBJECT', obj, None, obj.name[len('Anchor_'):], 'legacy Anchor_ root'))
    if mode in {'SAFE', 'SMART'}:
        for coll in scene.collection.children:
            if _contains(coll.name, ICON_COLLECTION_TOKENS) and _has_mesh_coll(coll):
                children = [c for c in coll.children if _has_mesh_coll(c)]
                if children:
                    candidates.extend((('COLLECTION', None, c, c.name, 'child of icon collection') for c in children))
                else:
                    candidates.append(('COLLECTION', None, coll, coll.name, 'named icon collection'))
    if mode == 'SMART':
        for obj in scene.objects:
            if obj.parent is None and obj.type == 'EMPTY' and _has_mesh_obj(obj):
                candidates.append(('OBJECT', obj, None, obj.name, 'top-level mesh empty'))
        for coll in scene.collection.children:
            if _has_mesh_coll(coll):
                candidates.append(('COLLECTION', None, coll, coll.name, 'top-level mesh collection'))
    seen, seen_meshes, claimed_meshes, added, reasons = (set(), set(), set(), 0, [])
    for kind, obj, coll, name, reason in candidates:
        key = _identity(kind, obj, coll)
        mesh_sig = _candidate_mesh_signature(kind, obj, coll)
        # Auto-detected icon sets must be disjoint. This prevents a common
        # SMART-mode failure where two specific Empty roots are found first and
        # their containing top-level Collection is then added as a third,
        # duplicate aggregate icon. Manual additions remain unrestricted.
        if key in seen or (mesh_sig and (mesh_sig in seen_meshes or bool(mesh_sig & claimed_meshes))):
            continue
        seen.add(key)
        if mesh_sig:
            seen_meshes.add(mesh_sig)
            claimed_meshes.update(mesh_sig)
        if add_icon_root(settings, obj, coll, name, True):
            item = settings.icon_sets[len(settings.icon_sets) - 1]
            if key in previous:
                item.name, item.enabled = previous[key]
            item.status = reason
            added += 1
            reasons.append(item.name + ': ' + reason)
    settings.icon_set_index = 0 if settings.icon_sets else -1
    return (added, reasons)

def _old_bindings(settings):
    out = {}
    for b in settings.bindings:
        out[b.material_identity or data_block_identity(b.material)] = {'family': b.family, 'locked': b.locked, 'manual': b.manual_family, 'enabled': b.enabled, 'inheritance': b.inheritance_mode, 'family_color': tuple(b.captured_family_color), 'material_color': tuple(b.captured_material_color)}
    return out

def _usage(scene, settings, local_only=False):
    total, membership = ({}, {})
    for i, icon in enumerate(settings.icon_sets):
        if not icon.enabled:
            continue
        for mat, weight in material_usage_for_objects(objects_from_icon_item(icon, scene), None if local_only else scene, getattr(settings, 'geometry_analysis_mode', 'WORLD')).items():
            total[mat] = total.get(mat, 0.0) + weight
            membership.setdefault(mat, set()).add(i)
    return (total, membership)

def _material_family_tag(mat):
    try:
        value = str(mat.get('color_prime_family', '')).upper()
        source = str(mat.get('color_prime_family_source', '')).upper()
    except Exception:
        return ('', '')
    if value not in {'MAIN', 'ACCENT', 'FIXED', 'IGNORE'}:
        return ('', '')
    if source in {'MANUAL', 'USER'}:
        return (value, 'MANUAL')
    if source in {'AUTO_GEOMETRY', 'BOOTSTRAP'}:
        return (value, 'AUTO_GEOMETRY')
    return ('', '')


def _geometry_tag_evidence(mat):
    try:
        confidence = float(mat.get('color_prime_family_confidence', 0.72))
        reason = str(mat.get('color_prime_family_reason', '') or '')
    except Exception:
        return (0.72, 'camera/geometry bootstrap assignment')
    confidence = max(0.0, min(1.0, confidence))
    return (confidence, reason or 'camera/geometry bootstrap assignment')

def _semantic_blender_name(name):
    # Blender adds .001/.002 when duplicating datablocks. The opt-out marker
    # should survive that automatic suffix: Gold!.001 is still Fixed.
    return re.sub(r'\.\d{3,}$', '', str(name or ''))


def _has_fixed_name_marker(mat, settings):
    suffix = str(getattr(settings, 'fixed_name_suffix', '!') or '').strip()
    return bool(suffix and _semantic_blender_name(mat.name).endswith(suffix))


def _fixed_reason(mat, spec, settings):
    suffix = str(getattr(settings, 'fixed_name_suffix', '!') or '').strip()
    if _has_fixed_name_marker(mat, settings):
        return "material name ends with '{}'".format(suffix)
    if mat.library is not None:
        return 'linked library material is read-only'
    if spec.protected_shader:
        return 'glass/volume/holdout shader'
    if settings.auto_protect_textures and spec.texture_driven:
        return 'linked or texture-driven Base Color'
    return ''

def _name_hint(name):
    main, accent = (_contains(name, MAIN_NAME_TOKENS), _contains(name, ACCENT_NAME_TOKENS))
    return 'MAIN' if main and (not accent) else 'ACCENT' if accent and (not main) else ''

def _lineage_key(item):
    spec = item.get('spec')
    stem = str(getattr(spec, 'source_group_stem', '') or '').strip().casefold() if spec else ''
    if stem:
        return 'group::' + stem
    return ''


def _assign_two_inherited_families(items):
    """If the scene exposes exactly two inherited node-group lineages, use
    that structure directly instead of guessing from RGB distance or names.
    """
    candidates = [x for x in items if x.get('spec') is not None and x['spec'].writable and x.get('family') not in {'FIXED', 'IGNORE'}]
    if len(candidates) < 2:
        return False
    keyed = [(x, _lineage_key(x)) for x in candidates]
    # Structural inference is only trusted when every recolorable candidate
    # belongs to a detectable group lineage.
    if any(not key for _x, key in keyed):
        return False
    groups = {}
    for x, key in keyed:
        groups.setdefault(key, []).append(x)
    if len(groups) != 2:
        return False
    keys = sorted(groups)
    known = {}
    for key in keys:
        fams = {x.get('family') for x in groups[key] if x.get('family') in {'MAIN', 'ACCENT'}}
        known[key] = next(iter(fams)) if len(fams) == 1 else ''
    anchored = False
    if known[keys[0]] and known[keys[1]] and known[keys[0]] != known[keys[1]]:
        family_for = dict(known)
        anchored = True
    elif known[keys[0]] in {'MAIN', 'ACCENT'} and not known[keys[1]]:
        family_for = {keys[0]: known[keys[0]], keys[1]: 'ACCENT' if known[keys[0]] == 'MAIN' else 'MAIN'}
        anchored = True
    elif known[keys[1]] in {'MAIN', 'ACCENT'} and not known[keys[0]]:
        family_for = {keys[1]: known[keys[1]], keys[0]: 'ACCENT' if known[keys[1]] == 'MAIN' else 'MAIN'}
        anchored = True
    else:
        weights = {key: sum(max(x['weight'], 1e-06) for x in group) for key, group in groups.items()}
        main_key = sorted(keys, key=lambda key: (-weights[key], key))[0]
        family_for = {key: ('MAIN' if key == main_key else 'ACCENT') for key in keys}
    weights = {key: sum(max(x['weight'], 1e-06) for x in group) for key, group in groups.items()}
    ordered_weights = sorted(weights.values(), reverse=True)
    dominance = min(1.0, max(0.0, ordered_weights[0] / max(ordered_weights[1], 1e-06) - 1.0))
    confidence = 0.99 if anchored else 0.78 + 0.16 * dominance
    reason = ('two inherited node-group lineages with explicit role anchor' if anchored
              else 'two inherited node-group lineages; larger visible family proposed as Main')
    for key, group in groups.items():
        fam = family_for[key]
        for x in group:
            if not x.get('family'):
                x.update(family=fam, confidence=confidence, reason=reason, source='node group lineage')
    return True


def _center(items, assign, cluster):
    selected = [x for x in items if assign[id(x)] == cluster]
    total = sum((max(x['weight'], 1e-06) for x in selected))
    return tuple((sum((x['feature'][i] * max(x['weight'], 1e-06) for x in selected)) / total for i in range(3))) if selected else (0, 0, 0)

def _cluster(items):
    if not items:
        return
    if len(items) == 1:
        items[0].update(family='MAIN', confidence=0.68, reason='only recolorable color cluster')
        return
    first = max(items, key=lambda x: x['weight'])
    second = max(items, key=lambda x: feature_distance(x['feature'], first['feature']))
    if feature_distance(first['feature'], second['feature']) < 0.28:
        for x in items:
            x.update(family='MAIN', confidence=0.62, reason='single coherent color family')
        return
    centers = [first['feature'], second['feature']]
    assign = {}
    for _ in range(12):
        changed = False
        for x in items:
            ds = [feature_distance(x['feature'], c) for c in centers]
            c = 0 if ds[0] <= ds[1] else 1
            if assign.get(id(x)) != c:
                assign[id(x)] = c
                changed = True
        centers = [_center(items, assign, 0), _center(items, assign, 1)]
        if not changed:
            break
    weights = [sum((x['weight'] for x in items if assign[id(x)] == c)) for c in (0, 1)]
    main = 0 if weights[0] >= weights[1] else 1
    for x in items:
        c = assign[id(x)]
        own = feature_distance(x['feature'], centers[c])
        other = feature_distance(x['feature'], centers[1 - c])
        x.update(family='MAIN' if c == main else 'ACCENT', confidence=min(0.93, 0.58 + max(0, other - own) * 0.35), reason='dominant color cluster' if c == main else 'secondary color cluster')

def _reference(items, family, current, existing_parent=None):
    if existing_parent is not None and existing_parent.get('cp_family_master','')==family:
        from .family_links import shared_socket
        socket=shared_socket(existing_parent)
        if socket is not None:return (color4(socket.default_value),existing_parent)
    candidates = [x for x in items if x.get('family') == family]
    parents = [x for x in candidates if not x['material'].get('color_prime_child',False)]
    if parents:
        candidates = parents
    if existing_parent is not None and not existing_parent.get('color_prime_child',False) and (
            existing_parent.get('color_prime_parent','') == family or
            any(x['material'].get('color_prime_child',False) for x in items if x.get('family')==family)):
        return (color4(current), existing_parent)
    if not candidates:
        return (color4(current), None)
    if existing_parent is not None:
        for x in candidates:
            if x['material'] == existing_parent:
                return (color4(x['spec'].color), x['material'])
    preferred = [x for x in candidates if x.get('source') in {'manual', 'material tag', 'legacy family group', 'node group lineage', 'material name'}]
    chosen = max(preferred or candidates, key=lambda x: x['weight'])
    return (color4(chosen['spec'].color), chosen['material'])

def scan_material_bindings(scene, settings, recapture=True, local_only=False):
    old = _old_bindings(settings)
    total, membership = _usage(scene, settings, local_only)
    # Also bind library children before their first face assignment.
    for child in getattr(settings, 'children', ()):
        if child.material is not None:
            total.setdefault(child.material, 0.0)
    mats = sorted(total, key=lambda m: (-total[m], m.name.casefold()))
    settings.suppress_callbacks = True
    try:
        settings.bindings.clear()
        items = []
        for mat in mats:
            ident = data_block_identity(mat)
            prior = old.get(ident, {})
            marker_fixed = _has_fixed_name_marker(mat, settings)
            selective = any(settings.icon_sets[i].selective_colors for i in membership.get(mat, set()))
            selective_fixed = selective and not mat.get('cp_selective_child',False) and not mat.get('color_prime_child',False) and not mat.get('cp_family_master','')
            from .transaction import protected
            fixed_prior = prior.get('family') in {'FIXED', 'IGNORE'} and (prior.get('locked') or prior.get('manual'))
            can_localize = bool(settings.auto_localize_legacy and not selective_fixed and not protected(mat, settings) and not fixed_prior and not marker_fixed and mat.library is None)
            spec = find_material_target(mat, localize_legacy=can_localize)
            b = settings.bindings.add()
            b.material = mat
            b.enabled = prior.get('enabled', True)
            b.material_identity = ident
            b.usage_weight = total[mat]
            b.used_by_icon_count = len(membership.get(mat, set()))
            b.inheritance_mode = prior.get('inheritance', 'OKLCH')
            b.locked = prior.get('locked', False)
            b.manual_family = prior.get('manual', False)
            apply_spec_to_binding(b, spec)
            # CollectionProperty.add can relocate earlier RNA items. Retain an
            # index, never a PropertyGroup pointer across subsequent additions.
            x = {'binding_index': len(settings.bindings)-1, 'material': mat, 'spec': spec, 'weight': max(total[mat], 1e-06), 'feature': family_feature(spec.color), 'family': '', 'confidence': 0.0, 'reason': '', 'source': ''}
            oldfam = prior.get('family', '')
            tag, tag_source = _material_family_tag(mat)
            fixed = _fixed_reason(mat, spec, settings)
            x['name_hint'] = _name_hint(mat.name)
            # The explicit suffix is a hard opt-out and wins even over stale
            # manual metadata from an earlier Color Prime session.
            if selective_fixed:
                x.update(family='FIXED',confidence=1.0,reason='Outside selected recolorable parts',source='selection')
                b.locked=True;b.manual_family=True
            elif fixed and _has_fixed_name_marker(mat, settings):
                x.update(family='FIXED', confidence=1.0, reason=fixed, source='name marker')
            elif b.locked and oldfam in {'MAIN', 'ACCENT', 'FIXED', 'IGNORE'}:
                x.update(family=oldfam, confidence=1, reason='locked manual assignment', source='manual')
            elif b.manual_family and oldfam in {'MAIN', 'ACCENT', 'FIXED', 'IGNORE'}:
                x.update(family=oldfam, confidence=1, reason='manual assignment', source='manual')
            elif tag:
                if tag_source == 'AUTO_GEOMETRY':
                    tag_confidence, tag_reason = _geometry_tag_evidence(mat)
                    x.update(family=tag, confidence=tag_confidence, reason=tag_reason, source='geometry bootstrap')
                else:
                    x.update(family=tag, confidence=1, reason='manual material Color Prime tag', source='material tag')
            elif fixed:
                x.update(family='FIXED', confidence=0.98, reason=fixed, source='protection')
            elif spec.kind == 'NONE' or not spec.writable:
                x.update(family='IGNORE', confidence=1, reason=spec.reason, source='unresolved')
            elif spec.family_hint:
                x.update(family=spec.family_hint, confidence=0.99, reason='legacy family group', source='legacy family group')
            items.append(x)
        _assign_two_inherited_families(items)
        for x in items:
            if not x['family'] and x.get('name_hint'):
                x.update(family=x['name_hint'], confidence=0.94, reason='material name', source='material name')
        _cluster([x for x in items if not x['family']])
        main_ref, main_parent = _reference(items, 'MAIN', settings.main_reference_color, settings.main_parent_material)
        accent_ref, accent_parent = _reference(items, 'ACCENT', settings.accent_reference_color, settings.accent_parent_material)
        settings.main_reference_color = main_ref
        settings.accent_reference_color = accent_ref
        settings.main_parent_material = main_parent
        settings.accent_parent_material = accent_parent
        counts = {k: 0 for k in ('MAIN', 'ACCENT', 'FIXED', 'IGNORE')}
        for x in items:
            b = settings.bindings[x['binding_index']]
            fam = x['family'] or 'IGNORE'
            b.family = fam
            b.confidence = x['confidence']
            b.auto_reason = x['reason']
            b.status = x['spec'].reason
            counts[fam] += 1
            prior = old.get(b.material_identity, {})
            if recapture or not prior:
                b.captured_material_color = x['spec'].color
                b.captured_family_color = main_ref if fam == 'MAIN' else accent_ref if fam == 'ACCENT' else x['spec'].color
            else:
                b.captured_material_color = prior.get('material_color', x['spec'].color)
                b.captured_family_color = prior.get('family_color', main_ref if fam == 'MAIN' else accent_ref if fam == 'ACCENT' else x['spec'].color)
            try:
                if fam in {'FIXED','IGNORE'}:
                    continue
                b.material['color_prime_family'] = fam
                b.material['color_prime_family_source'] = 'MANUAL' if x['source'] in {'manual', 'material tag'} else 'AUTO_GEOMETRY' if x['source'] == 'geometry bootstrap' else 'AUTO'
                b.material['color_prime_locked'] = bool(b.locked)
            except Exception:
                pass
        settings.binding_index = 0 if settings.bindings else -1
        settings.setup_complete = bool(settings.icon_sets and settings.bindings)
        settings.last_setup_summary = '{} icons · {} materials · {} Main · {} Accent · {} Fixed · {} Ignore'.format(len(settings.icon_sets), len(settings.bindings), counts['MAIN'], counts['ACCENT'], counts['FIXED'], counts['IGNORE'])
        return {'materials': len(settings.bindings), 'family_counts': counts, 'summary': settings.last_setup_summary}
    finally:
        settings.suppress_callbacks = False

def recapture_binding(binding, settings):
    current = read_binding_color(binding)
    if current is None:
        return False
    binding.captured_material_color = current
    binding.captured_family_color = settings.main_reference_color if binding.family == 'MAIN' else settings.accent_reference_color if binding.family == 'ACCENT' else current
    return True

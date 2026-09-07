"""Blender integration for one-click scene preparation.

This layer does not pretend to solve arbitrary semantic 3D segmentation.  It
uses evidence that is strong and inspectable in production icon scenes:
material/node lineage, hierarchy, explicit markers, camera-projected size,
world-space size and disconnected mesh islands.  Every automatic assignment is
stored with a confidence and reason, and all mutations are covered by Blender's
Undo stack through the calling operator.
"""
from dataclasses import dataclass
import re
from typing import Dict, Iterable, List, Optional, Tuple

import bpy

from .constants import ACCENT_NAME_TOKENS, MAIN_NAME_TOKENS
from .geometry import material_usage_for_objects, object_visual_weight
from .role_logic import RoleSignal, infer_roles
from .targets import find_material_target
from .utils import data_block_identity, objects_from_icon_item, sanitize_filename


@dataclass
class BootstrapReport:
    objects_analyzed: int = 0
    roles_suggested: int = 0
    materials_created: int = 0
    materials_copied: int = 0
    mesh_data_copied: int = 0
    slots_changed: int = 0
    loose_objects_split: int = 0
    ambiguous_objects: int = 0

    def summary(self):
        return ('{} objects · {} role suggestions · {} materials created · {} material copies · '
                '{} mesh copies · {} slots changed · {} disconnected meshes assigned').format(
                    self.objects_analyzed, self.roles_suggested, self.materials_created,
                    self.materials_copied, self.mesh_data_copied, self.slots_changed,
                    self.loose_objects_split)


def _semantic_name(name):
    return re.sub(r'\.\d{3,}$', '', str(name or ''))


def _contains(name, tokens):
    value = str(name or '').casefold()
    return any(token.casefold() in value for token in tokens)


def _name_hint(*names):
    value = ' '.join(str(name or '') for name in names)
    main = _contains(value, MAIN_NAME_TOKENS)
    accent = _contains(value, ACCENT_NAME_TOKENS)
    return 'MAIN' if main and not accent else 'ACCENT' if accent and not main else ''


def _fixed_name(name, settings):
    suffix = str(getattr(settings, 'fixed_name_suffix', '!') or '').strip()
    return bool(suffix and _semantic_name(name).endswith(suffix))


def _saved_role(block):
    if block is None:
        return ''
    try:
        family = str(block.get('color_prime_family', '') or '').upper()
        source = str(block.get('color_prime_family_source', '') or '').upper()
    except Exception:
        return ''
    if family not in {'MAIN', 'ACCENT', 'FIXED', 'IGNORE'}:
        return ''
    # AUTO is the ordinary scanner output and should be recalculated. Manual
    # and geometry-bootstrap decisions are intentional persistent evidence.
    return family if source in {'MANUAL', 'USER', 'AUTO_GEOMETRY', 'BOOTSTRAP'} else ''


def _dominant_material(obj, scene, mode):
    usage = material_usage_for_objects([obj], scene, mode)
    if usage:
        return max(usage.items(), key=lambda item: (item[1], item[0].name.casefold()))[0]
    for slot in getattr(obj, 'material_slots', ()):
        if slot.material:
            return slot.material
    return None


def _object_signal(obj, scene, settings):
    mat = _dominant_material(obj, scene, settings.geometry_analysis_mode)
    explicit = _saved_role(obj) or _saved_role(mat)
    fixed = bool(mat and _fixed_name(mat.name, settings)) or explicit == 'FIXED'
    lineage = ''
    material_group = ''
    if mat is not None:
        material_group = data_block_identity(mat)
        try:
            spec = find_material_target(mat, False)
            lineage = str(spec.source_group_stem or '').casefold()
        except Exception:
            lineage = ''
    weight = object_visual_weight(obj, scene, settings.geometry_analysis_mode)
    return RoleSignal(
        key=str(obj.as_pointer()),
        weight=max(float(weight), 1e-8),
        explicit=explicit,
        name_hint=_name_hint(obj.name, mat.name if mat else ''),
        lineage=lineage,
        material_group=material_group,
        fixed=fixed,
        sort_key=data_block_identity(obj),
    )


def _mesh_objects(scene, settings):
    objects = []
    seen = set()
    for icon in settings.icon_sets:
        if not icon.enabled:
            continue
        for obj in objects_from_icon_item(icon, scene):
            if getattr(obj, 'type', None) != 'MESH':
                continue
            ptr = obj.as_pointer()
            if ptr not in seen:
                seen.add(ptr)
                objects.append(obj)
    return objects


def _set_principled_color(material, color):
    try:
        material.diffuse_color = color
    except Exception:
        pass
    if not material.use_nodes:
        material.use_nodes = True
    tree = material.node_tree
    if tree is None:
        return False
    principled = next((node for node in tree.nodes if node.bl_idname == 'ShaderNodeBsdfPrincipled'), None)
    if principled is None:
        return False
    try:
        socket = principled.inputs.get('Base Color')
        if socket is not None and not socket.is_linked:
            socket.default_value = color
            return True
    except Exception:
        pass
    return False


def _new_family_material(settings, family, base_name='Auto', confidence=0.72, reason='material created by geometry bootstrap'):
    family_title = 'Main' if family == 'MAIN' else 'Accent'
    material = bpy.data.materials.new(name=family_title+' Base')
    from .material_names import name_material
    name_material(material,family)
    color = settings.main_reference_color if family == 'MAIN' else settings.accent_reference_color
    if not _set_principled_color(material, color):
        raise RuntimeError('Could not initialize a writable material for '+str(base_name))
    material['color_prime_family'] = family
    material['color_prime_family_source'] = 'AUTO_GEOMETRY'
    material['color_prime_family_confidence'] = float(confidence)
    material['color_prime_family_reason'] = str(reason or '')
    material['color_prime_locked'] = False
    return material


def _copy_for_family(material, family, owner_name, confidence=0.72, reason='material localized by geometry bootstrap'):
    copied = material.copy()
    from .material_names import name_material
    name_material(copied,family)
    copied['color_prime_family'] = family
    copied['color_prime_family_source'] = 'AUTO_GEOMETRY'
    copied['color_prime_family_confidence'] = float(confidence)
    copied['color_prime_family_reason'] = str(reason or '')
    copied['color_prime_locked'] = False
    return copied


def _tag_material(material, family, confidence=0.72, reason='geometry bootstrap assignment'):
    if material is None:
        return
    try:
        source = str(material.get('color_prime_family_source', '')).upper()
        if source in {'MANUAL', 'USER'} or bool(material.get('color_prime_locked', False)):
            return
        material['color_prime_family'] = family
        material['color_prime_family_source'] = 'AUTO_GEOMETRY'
        material['color_prime_family_confidence'] = float(max(0.0, min(1.0, confidence)))
        material['color_prime_family_reason'] = str(reason or '')
        material['color_prime_locked'] = False
    except Exception:
        pass



def _ensure_local_mesh_data(obj, report):
    """Make material-slot edits object-local when mesh data is shared."""
    data = getattr(obj, 'data', None)
    if data is None or getattr(data, 'library', None) is not None:
        return data
    try:
        shared = int(getattr(data, 'users', 1)) > 1
    except Exception:
        shared = False
    if not shared:
        return data
    try:
        copied = data.copy()
        copied.name = '{}__CP_Local'.format(data.name)
        obj.data = copied
        report.mesh_data_copied += 1
        return copied
    except Exception:
        return data


def _material_components(mesh):
    polygons = list(getattr(mesh, 'polygons', ()))
    vertex_count = len(getattr(mesh, 'vertices', ()))
    if not polygons or vertex_count <= 0:
        return []
    parent = list(range(vertex_count))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for poly in polygons:
        vertices = list(poly.vertices)
        if not vertices:
            continue
        first = vertices[0]
        for vertex in vertices[1:]:
            union(first, vertex)
    groups = {}
    for poly in polygons:
        vertices = list(poly.vertices)
        if not vertices:
            continue
        groups.setdefault(find(vertices[0]), []).append(poly)
    out = []
    for group in groups.values():
        area = sum(max(float(getattr(poly, 'area', 0.0)), 1e-9) for poly in group)
        out.append((area, group))
    out.sort(key=lambda item: -item[0])
    return out


def _split_loose_parts(obj, settings, report):
    mode = str(getattr(settings, 'loose_part_mode', 'TWO_ONLY') or 'TWO_ONLY')
    if mode == 'OFF' or getattr(obj, 'library', None) is not None:
        return False
    data = getattr(obj, 'data', None)
    if data is None or getattr(data, 'library', None) is not None:
        return False
    current_materials = [slot.material for slot in getattr(obj, 'material_slots', ()) if slot.material]
    if any(_fixed_name(mat.name, settings) or _saved_role(mat) or
           getattr(mat, 'library', None) is not None for mat in current_materials):
        return False
    # Do not rewrite an already authored multi-material mesh.
    if len({mat.as_pointer() for mat in current_materials}) > 1:
        return False
    components = _material_components(data)
    if len(components) < 2:
        return False
    if mode == 'TWO_ONLY' and len(components) != 2:
        return False
    original_data = data
    data = _ensure_local_mesh_data(obj, report)
    if data is not original_data:
        components = _material_components(data)
    source = current_materials[0] if current_materials else None
    if source is not None:
        main_mat = _copy_for_family(source, 'MAIN', obj.name)
        accent_mat = _copy_for_family(source, 'ACCENT', obj.name)
        report.materials_copied += 2
    else:
        main_mat = _new_family_material(settings, 'MAIN', obj.name)
        accent_mat = _new_family_material(settings, 'ACCENT', obj.name)
        report.materials_created += 2
    data.materials.clear()
    data.materials.append(main_mat)
    data.materials.append(accent_mat)
    total = sum(area for area, _ in components)
    cumulative = 0.0
    for index, (area, polygons) in enumerate(components):
        if len(components) == 2:
            family_index = 0 if index == 0 else 1
        else:
            family_index = 0 if index == 0 or cumulative < total * 0.66 else 1
        for poly in polygons:
            poly.material_index = family_index
        cumulative += area
    obj['color_prime_geometry_split'] = True
    obj['color_prime_geometry_split_components'] = len(components)
    report.loose_objects_split += 1
    report.slots_changed += len(components)
    return True



def _material_signals_for_object(obj, object_decision, scene, settings):
    """Build material-level evidence for one object.

    Object-level classification alone is insufficient for authored meshes that
    already contain two materials, and it used to overwrite both slots with the
    dominant object's family.  This second pass resolves every material slot
    independently, preserving explicit/bootstrap roles and using per-material
    camera/world coverage for unlabelled slots.
    """
    usage = material_usage_for_objects([obj], scene, settings.geometry_analysis_mode)
    materials = []
    seen = set()
    for slot in getattr(obj, 'material_slots', ()):
        material = getattr(slot, 'material', None)
        if material is None:
            continue
        ptr = material.as_pointer()
        if ptr in seen:
            continue
        seen.add(ptr)
        materials.append(material)
    if not materials:
        return [], {}

    signals = []
    for material in materials:
        explicit = _saved_role(material)
        fixed = _fixed_name(material.name, settings) or explicit == 'FIXED'
        lineage = ''
        try:
            spec = find_material_target(material, False)
            lineage = str(spec.source_group_stem or '').casefold()
        except Exception:
            lineage = ''
        signals.append(RoleSignal(
            key=str(material.as_pointer()),
            weight=max(float(usage.get(material, 1.0)), 1e-8),
            explicit=explicit,
            name_hint=_name_hint(material.name),
            lineage=lineage,
            material_group=data_block_identity(material),
            fixed=fixed,
            sort_key=data_block_identity(material),
        ))

    # A single unlabelled recolorable material should follow the already
    # inferred object role.  This is the only case where object-level evidence
    # is stronger than a material-only split.
    recolorable = [signal for signal in signals if not signal.fixed and signal.explicit not in {'FIXED', 'IGNORE'}]
    if len(recolorable) == 1 and not recolorable[0].explicit and not recolorable[0].name_hint:
        family = getattr(object_decision, 'family', '') if object_decision is not None else ''
        if family in {'MAIN', 'ACCENT'}:
            recolorable[0].explicit = family

    decisions = infer_roles(signals)
    by_ptr = {}
    for decision in decisions:
        try:
            ptr = int(decision.key)
        except (TypeError, ValueError):
            continue
        by_ptr[ptr] = decision
    return materials, by_ptr


def _decision_for_material(material, decisions, object_decision=None):
    decision = decisions.get(material.as_pointer())
    if decision is not None:
        return decision
    # Last-resort fallback is deliberately limited to Main/Accent object roles.
    # Fixed/Ignore must never leak onto an unrelated empty or unresolved slot.
    if object_decision is not None and object_decision.family in {'MAIN', 'ACCENT'}:
        from .role_logic import RoleDecision
        return RoleDecision(str(material.as_pointer()), object_decision.family,
                            min(float(object_decision.confidence), 0.62),
                            'fallback to object-level geometry role')
    return None

def _match_automatic_object_roles(objects,signals,decisions):
    """Keep unlabelled copies in one family even when their scales differ.

    Authored roles, name evidence and multi-material boundaries take priority.
    This only consolidates the otherwise size-based fallback for simple meshes.
    """
    from .shape_matching import describe,group_shapes
    candidates=[]
    for obj,signal in zip(objects,signals):
        if signal.fixed or signal.explicit or signal.name_hint:continue
        if len({p.material_index for p in obj.data.polygons})>1:continue
        candidates.append((obj,signal))
    candidates.sort(key=lambda pair:(-pair[1].weight,pair[0].name))
    if len(candidates)<2:return
    shapes=[describe([tuple(obj.matrix_world@v.co) for v in obj.data.vertices],
                     [tuple(f.vertices) for f in obj.data.polygons]) for obj,signal in candidates]
    groups,_=group_shapes(shapes)
    for group in groups:
        if len(group)<2:continue
        first=decisions.get(candidates[group[0]][1].key)
        if first is None or first.family not in {'MAIN','ACCENT'}:continue
        for index in group[1:]:
            decision=decisions.get(candidates[index][1].key)
            if decision is not None:
                decision.family=first.family
                decision.reason='matching copy of '+candidates[group[0]][0].name
                decision.confidence=min(first.confidence,.8)


def auto_prepare_materials(scene, settings, objects=None):
    report = BootstrapReport()
    objects = _mesh_objects(scene, settings) if objects is None else objects
    report.objects_analyzed = len(objects)
    if not objects:
        return report

    studio=getattr(settings,'studio',None)
    if studio is not None and getattr(studio,'surface_regions','OFF')!='OFF':
        from .surface_adapter import auto_regions
        studio.region_note=''
        for obj in objects:
            zones=auto_regions(obj,settings)
            if zones:
                report.slots_changed+=zones
                report.roles_suggested+=zones

    if getattr(settings, 'loose_part_mode', 'OFF') != 'OFF' and (studio is None or getattr(studio,'surface_regions','AUTO')!='OFF'):
        for obj in objects:
            try:
                _split_loose_parts(obj, settings, report)
            except Exception as exc:
                raise RuntimeError('Disconnected-part setup failed on '+obj.name+': '+str(exc)) from exc

    signals = [_object_signal(obj, scene, settings) for obj in objects]
    object_decisions = {decision.key: decision for decision in infer_roles(signals)}
    if studio is not None and studio.match_similar_parts:
        _match_automatic_object_roles(objects,signals,object_decisions)
    report.roles_suggested = len(object_decisions)
    object_roles = {}
    for obj in objects:
        decision = object_decisions.get(str(obj.as_pointer()))
        if decision is None:
            report.ambiguous_objects += 1
            continue
        object_roles[obj.as_pointer()] = decision.family
        try:
            obj['color_prime_suggested_family'] = decision.family
            obj['color_prime_suggestion_confidence'] = float(decision.confidence)
            obj['color_prime_suggestion_reason'] = decision.reason
        except Exception:
            pass

    # Resolve roles per material, not merely per object. This is essential for
    # a single mesh with two authored materials and for loose-part splitting.
    material_decisions_by_object = {}
    for obj in objects:
        object_decision = object_decisions.get(str(obj.as_pointer()))
        _materials, material_decisions = _material_signals_for_object(
            obj, object_decision, scene, settings)
        material_decisions_by_object[obj.as_pointer()] = material_decisions

    # Determine whether one shared material is asked to serve both inferred
    # roles. It must be localized before any tag is written; otherwise changing
    # one cube would silently change another cube that shares the datablock.
    roles_per_material = {}
    for obj in objects:
        object_decision = object_decisions.get(str(obj.as_pointer()))
        material_decisions = material_decisions_by_object.get(obj.as_pointer(), {})
        for slot in getattr(obj, 'material_slots', ()):
            material = getattr(slot, 'material', None)
            if material is None or _fixed_name(material.name, settings):
                continue
            decision = _decision_for_material(material, material_decisions, object_decision)
            if decision is not None and decision.family in {'MAIN', 'ACCENT'}:
                roles_per_material.setdefault(material.as_pointer(), set()).add(decision.family)

    copies = {}
    for obj in objects:
        object_decision = object_decisions.get(str(obj.as_pointer()))
        object_role = object_roles.get(obj.as_pointer(), '')
        material_decisions = material_decisions_by_object.get(obj.as_pointer(), {})
        slots = list(getattr(obj, 'material_slots', ()))
        existing = [slot for slot in slots if getattr(slot, 'material', None) is not None]

        # Blank objects inherit their object-level role and receive a working
        # Principled material. No material-name convention is required.
        if not slots or not existing:
            if object_role not in {'MAIN', 'ACCENT'}:
                report.ambiguous_objects += 1
                continue
            try:
                _ensure_local_mesh_data(obj, report)
                material = _new_family_material(
                    settings, object_role, obj.name,
                    confidence=float(getattr(object_decision, 'confidence', 0.62)),
                    reason=str(getattr(object_decision, 'reason', 'blank object geometry role')))
                report.materials_created += 1
                if len(getattr(obj.data, 'materials', ())) == 0:
                    obj.data.materials.append(material)
                    report.slots_changed += 1
                else:
                    filled = False
                    for slot in obj.material_slots:
                        if slot.material is None:
                            slot.material = material
                            filled = True
                            report.slots_changed += 1
                    if not filled:
                        obj.data.materials.append(material)
                        report.slots_changed += 1
            except Exception as exc:
                raise RuntimeError('Material creation failed on '+obj.name+': '+str(exc)) from exc
            continue

        crossing_here = False
        for slot in slots:
            material = getattr(slot, 'material', None)
            if material is None or _fixed_name(material.name, settings):
                continue
            if len(roles_per_material.get(material.as_pointer(), set())) > 1:
                crossing_here = True
                break
        if crossing_here and getattr(settings, 'auto_split_shared_materials', True):
            _ensure_local_mesh_data(obj, report)

        for slot in obj.material_slots:
            material = slot.material
            if material is None:
                continue
            if _fixed_name(material.name, settings):
                continue
            decision = _decision_for_material(material, material_decisions, object_decision)
            if decision is None or decision.family not in {'MAIN', 'ACCENT'}:
                continue
            role = decision.family
            crossing = len(roles_per_material.get(material.as_pointer(), set())) > 1
            if crossing and getattr(settings, 'auto_split_shared_materials', True):
                key = (material.as_pointer(), role,
                       obj.as_pointer() if getattr(settings, 'localize_per_icon', False) else 0)
                if key not in copies:
                    copies[key] = _copy_for_family(
                        material, role, obj.name,
                        confidence=decision.confidence, reason=decision.reason)
                    report.materials_copied += 1
                if slot.material != copies[key]:
                    slot.material = copies[key]
                    report.slots_changed += 1
            elif not crossing:
                _tag_material(material, role, decision.confidence, decision.reason)
            else:
                # Splitting was explicitly disabled: leave the shared source
                # untagged rather than lying that it has one unambiguous role.
                report.ambiguous_objects += 1
    return report

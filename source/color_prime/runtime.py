from typing import Dict, Optional, Sequence, Set
import bpy
import re
from .compat import iter_scenes, update_view_layer
from .color_math import apply_inheritance, color4
from .targets import make_snapshot, restore_snapshot, write_binding_color, TargetSnapshot
from .utils import all_icon_objects, log, material_usage_for_objects, redraw_ui
_PREVIEWS: Dict[int, list] = {}

def _key(scene):
    return scene.as_pointer()

def material_pointers_for_objects(objects):
    return {m.as_pointer() for m in material_usage_for_objects(objects)}

def _protected_now(material, settings):
    suffix = str(getattr(settings, 'fixed_name_suffix', '!') or '').strip()
    if suffix and re.sub(r'\.\d{3,}$', '', material.name).endswith(suffix):
        return True
    try:
        return str(material.get('color_prime_family', '')).upper() in {'FIXED', 'IGNORE'}
    except (AttributeError, ReferenceError):
        return False


def snapshot_bindings(settings, allowed: Optional[Set[int]]=None):
    out = []
    seen = set()
    for b in settings.bindings:
        m = b.material
        if not m or not b.enabled or b.family not in {'MAIN', 'ACCENT'} or _protected_now(m, settings):
            continue
        ptr = m.as_pointer()
        if allowed is not None and ptr not in allowed:
            continue
        from .family_links import shared_socket
        shared=shared_socket(m)
        target = ('SHARED',shared.as_pointer()) if shared else (ptr, b.target_kind, b.target_node_name, b.target_group_node_name, b.target_inner_node_name, b.target_socket_name, b.target_socket_index, getattr(b, 'target_path_json','[]'))
        if target in seen:
            continue
        seen.add(target)
        snap = make_snapshot(b)
        if snap:
            out.append(snap)
    return out

def restore_snapshots(snaps):
    return sum((1 for s in reversed(list(snaps)) if restore_snapshot(s)))

def apply_family_colors(settings, main_color, accent_color, allowed: Optional[Set[int]]=None):
    changed = 0
    failures = []
    shared_written=set()
    for b in settings.bindings:
        m = b.material
        if not m or not b.enabled or b.family not in {'MAIN', 'ACCENT'} or _protected_now(m, settings):
            continue
        if allowed is not None and m.as_pointer() not in allowed:
            continue
        selected = main_color if b.family == 'MAIN' else accent_color
        from .family_links import shared_socket
        shared=shared_socket(m)
        if shared:
            if shared.as_pointer() not in shared_written:
                shared.default_value=color4(selected);shared_written.add(shared.as_pointer())
            changed+=1
            continue
        result = apply_inheritance(selected, b.captured_family_color, b.captured_material_color, b.inheritance_mode)
        if m.get('color_prime_child', False):
            from .child_materials import child_color
            result = child_color(m, result)
        if write_binding_color(b, result):
            changed += 1
        else:
            failures.append(m.name)
    return (changed, failures)

def _persist_preview(settings, snaps):
    studio = getattr(settings,'studio',None)
    if studio is None or not hasattr(studio,'preview_values'):
        return
    studio.preview_values.clear()
    for snap in snaps:
        item = studio.preview_values.add()
        item.material = snap.material_ref
        for field in ('kind','node_name','socket_name','socket_index','group_node_name','inner_node_name','color','path_json'):
            setattr(item,field,getattr(snap,field))


def _durable_preview(settings):
    studio = getattr(settings,'studio',None)
    if studio is None or not hasattr(studio,'preview_values'):
        return []
    out=[]
    for item in studio.preview_values:
        material=item.material
        if material is None:
            continue
        lib=getattr(getattr(material,'library',None),'filepath','')
        out.append(TargetSnapshot(material.name,lib,item.kind,item.node_name,item.socket_name,item.socket_index,
            item.group_node_name,item.inner_node_name,tuple(item.color),material,item.path_json))
    return out


def restore_preview(scene):
    from .guide_runtime import cancel_pending
    cancel_pending()
    settings = getattr(scene, 'color_prime', None)
    cached = _PREVIEWS.pop(_key(scene), [])
    snaps = _durable_preview(settings) or cached if settings else cached
    failed = []
    restored = 0
    for snap in reversed(snaps):
        if restore_snapshot(snap):
            restored += 1
        else:
            failed.append(snap)
    if failed:
        _PREVIEWS[_key(scene)] = failed
    if settings:
        _persist_preview(settings, failed)
        settings.preview_active = bool(failed)
        if hasattr(settings,'workspace_preview'):settings.workspace_preview=False
        if failed:
            settings.last_error = '{} preview target(s) could not be restored. Restore renamed/removed target nodes or Revert Setup; the recovery values were retained.'.format(len(failed))
            log(settings.last_error, 'ERROR')
    try:
        update_view_layer()
    except Exception:
        pass
    redraw_ui()
    return restored

def apply_preview(scene, settings, main_color, accent_color):
    restore_preview(scene)
    if settings.preview_active:
        raise RuntimeError('Previous preview recovery is incomplete; new colors were not applied')
    allowed = material_pointers_for_objects(all_icon_objects(settings, scene, True))
    snaps = snapshot_bindings(settings, allowed)
    _PREVIEWS[_key(scene)] = snaps
    _persist_preview(settings, snaps)
    try:
        changed, failures = apply_family_colors(settings, color4(main_color), color4(accent_color), allowed)
        if failures:
            raise RuntimeError('Preview rolled back: invalid target(s): ' + ', '.join(failures))
    except Exception:
        # Roll back already-written values if a later binding fails unexpectedly.
        restore_preview(scene)
        raise
    if not changed:
        restore_preview(scene)
        return (0, failures)
    settings.preview_active = True
    try:
        update_view_layer()
    except Exception:
        pass
    redraw_ui()
    log('Applied preview to {} material target(s)'.format(changed))
    return (changed, failures)

def restore_all_previews():
    # Must also be safe while Blender exposes _RestrictData during add-on lifecycle.
    return sum((restore_preview(scene) for scene in iter_scenes()))

def clear_runtime_state():
    _PREVIEWS.clear()

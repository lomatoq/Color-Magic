import hashlib
import json
import os
import re
import traceback
from datetime import datetime, timezone
from typing import Dict, Iterable, Iterator, List, Optional, Set
import bpy
from .constants import LOG_TEXT_NAME
_INVALID = re.compile('[\\\\/*?:"<>|\\x00-\\x1f]+')

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def sanitize_filename(value, fallback='Unnamed'):
    value = _INVALID.sub('_', (value or '').strip())
    value = re.sub('\\s+', '_', value)
    value = re.sub('_+', '_', value).strip(' ._')
    return value or fallback

def data_block_identity(block):
    if block is None:
        return 'missing'
    library = getattr(getattr(block, 'library', None), 'filepath', '') or ''
    return '{}::{}'.format(library, getattr(block, 'name_full', getattr(block, 'name', '')))

def short_hash(value, length=16):
    return hashlib.sha1(value.encode('utf-8', errors='replace')).hexdigest()[:length]

def log(message, level='INFO', exception=None):
    line = '[{}] [{}] {}'.format(utc_now_iso(), level.upper(), message)
    print('Color Prime ' + line)
    try:
        text = bpy.data.texts.get(LOG_TEXT_NAME) or bpy.data.texts.new(LOG_TEXT_NAME)
        text.write(line + '\n')
        if exception:
            text.write(''.join(traceback.format_exception(type(exception), exception, exception.__traceback__)))
    except Exception:
        pass

def report_exception(operator, prefix, exception):
    log(prefix + ': ' + str(exception), 'ERROR', exception)
    operator.report({'ERROR'}, '{}: {}'.format(prefix, exception))

def scene_contains_object(scene, obj):
    if not scene or not obj:
        return False
    try:
        return scene.objects.get(obj.name) == obj
    except Exception:
        return False

def iter_object_tree(root):
    if root is None:
        return
    stack, seen = ([root], set())
    while stack:
        obj = stack.pop()
        ptr = obj.as_pointer()
        if ptr in seen:
            continue
        seen.add(ptr)
        yield obj
        stack.extend(reversed(list(getattr(obj, 'children', ()))))

def iter_collection_tree(root):
    if root is None:
        return
    stack, seen = ([root], set())
    while stack:
        coll = stack.pop()
        ptr = coll.as_pointer()
        if ptr in seen:
            continue
        seen.add(ptr)
        yield coll
        stack.extend(reversed(list(getattr(coll, 'children', ()))))

def objects_from_collection(root, scene=None):
    out, seen = ([], set())
    for coll in iter_collection_tree(root):
        for obj in getattr(coll, 'objects', ()):
            ptr = obj.as_pointer()
            if ptr in seen or (scene is not None and (not scene_contains_object(scene, obj))):
                continue
            seen.add(ptr)
            out.append(obj)
    return out

def objects_from_icon_item(item, scene):
    if item.root_kind == 'COLLECTION':
        return objects_from_collection(item.collection_root, scene)
    return [o for o in iter_object_tree(item.object_root) if scene_contains_object(scene, o)]

def collections_from_icon_item(item):
    if item.root_kind == 'COLLECTION':
        return list(iter_collection_tree(item.collection_root))
    out, seen = ([], set())
    for obj in iter_object_tree(item.object_root):
        for coll in getattr(obj, 'users_collection', ()):
            ptr = coll.as_pointer()
            if ptr not in seen:
                seen.add(ptr)
                out.append(coll)
    return out

def icon_item_display_name(item):
    if item.name:
        return item.name
    root = item.collection_root if item.root_kind == 'COLLECTION' else item.object_root
    return root.name if root else 'Missing icon set'

def icon_item_is_valid(item, scene):
    return bool(item.collection_root) if item.root_kind == 'COLLECTION' else scene_contains_object(scene, item.object_root)

def material_usage_for_objects(objects: Iterable, scene=None, analysis_mode='WORLD'):
    """Return material importance weights.

    With a scene this delegates to the camera/world geometry analyzer.  The
    old local polygon-area implementation remains as a restriction-safe
    fallback for tests, unusual procedural objects and legacy call sites.
    """
    if scene is not None:
        try:
            from .geometry import material_usage_for_objects as geometry_usage
            result = geometry_usage(objects, scene, analysis_mode)
            if result:
                return result
        except Exception:
            pass
    usage = {}
    for obj in objects:
        if getattr(obj, 'type', None) != 'MESH':
            continue
        slots = list(getattr(obj, 'material_slots', ()))
        if not slots:
            continue
        weights = [0.0] * len(slots)
        for poly in getattr(getattr(obj, 'data', None), 'polygons', ()):
            idx = int(getattr(poly, 'material_index', 0))
            if 0 <= idx < len(weights):
                weights[idx] += max(float(getattr(poly, 'area', 0.0) or 0.0), 1e-05)
        for idx, slot in enumerate(slots):
            mat = getattr(slot, 'material', None)
            if mat:
                usage[mat] = usage.get(mat, 0.0) + (weights[idx] if weights[idx] > 0 else 1.0)
    return usage

def all_icon_objects(settings, scene, enabled_only=True):
    out, seen = ([], set())
    for item in settings.icon_sets:
        if enabled_only and (not item.enabled):
            continue
        for obj in objects_from_icon_item(item, scene):
            ptr = obj.as_pointer()
            if ptr not in seen:
                seen.add(ptr)
                out.append(obj)
    return out

def active_item(collection, index):
    return collection[index] if 0 <= index < len(collection) else None

def ensure_directory(path):
    resolved = bpy.path.abspath(path or '')
    if not resolved:
        raise ValueError('Output folder is empty')
    os.makedirs(resolved, exist_ok=True)
    if not os.path.isdir(resolved):
        raise OSError('Output path is not a directory: ' + resolved)
    return resolved

def queue_signature(tasks):
    stable = [{k: t[k] for k in ('icon_key', 'main_key', 'accent_key', 'resolution_key')} for t in tasks]
    return short_hash(json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(',', ':')))

def redraw_ui(context=None):
    context = context or bpy.context
    wm = getattr(context, 'window_manager', None)
    if not wm:
        return
    for window in getattr(wm, 'windows', ()):
        for area in getattr(getattr(window, 'screen', None), 'areas', ()):
            if area.type in {'PROPERTIES', 'VIEW_3D', 'NODE_EDITOR'}:
                area.tag_redraw()

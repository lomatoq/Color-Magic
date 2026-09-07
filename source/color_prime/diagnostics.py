import os
import string
from collections import defaultdict
import bpy
from .targets import binding_target_is_valid
from .utils import icon_item_display_name, icon_item_is_valid, objects_from_icon_item

def clear(settings):
    settings.diagnostics.clear()
    settings.diagnostic_index = -1

def add(settings, level, message, data_name='', code=''):
    x = settings.diagnostics.add()
    x.level = level
    x.message = message
    x.data_name = data_name
    x.code = code

def _enabled(coll):
    return sum((1 for x in coll if x.enabled))

def run_diagnostics(scene, settings):
    clear(settings)
    counts = defaultdict(int)
    if scene.camera is None:
        add(settings, 'ERROR', 'Scene has no active camera', code='NO_CAMERA')
    still_formats = {'BMP', 'IRIS', 'PNG', 'JPEG', 'JPEG2000', 'TARGA', 'TARGA_RAW', 'CINEON', 'DPX', 'OPEN_EXR_MULTILAYER', 'OPEN_EXR', 'HDR', 'TIFF', 'WEBP'}
    if scene.render.image_settings.file_format not in still_formats:
        add(settings, 'ERROR', 'Choose a still-image output format for batch icon rendering', scene.render.image_settings.file_format, 'NON_STILL_FORMAT')
    if not settings.icon_sets:
        add(settings, 'ERROR', 'No icon sets are configured', code='NO_ICONS')
    memberships = defaultdict(list)
    enabled_icons = 0
    for icon in settings.icon_sets:
        name = icon_item_display_name(icon)
        if not icon_item_is_valid(icon, scene):
            add(settings, 'ERROR', 'Icon root is missing', name, 'MISSING_ICON_ROOT')
            continue
        enabled_icons += int(icon.enabled)
        objects = objects_from_icon_item(icon, scene)
        if not any((o.type == 'MESH' for o in objects)):
            add(settings, 'WARNING', 'Icon set contains no mesh objects', name, 'EMPTY_ICON')
        for obj in objects:
            memberships[obj.as_pointer()].append(name)
        if icon.root_kind == 'COLLECTION' and icon.collection_root.hide_render:
            add(settings, 'WARNING', 'Root collection is render-disabled; it will be enabled temporarily', name, 'HIDDEN_COLLECTION')
    if settings.icon_sets and (not enabled_icons):
        add(settings, 'ERROR', 'All icon sets are disabled', code='NO_ENABLED_ICONS')
    overlaps = [sorted(set(v)) for v in memberships.values() if len(set(v)) > 1]
    for names in overlaps[:8]:
        add(settings, 'WARNING', 'Object belongs to multiple icon sets: ' + ', '.join(names), code='ICON_OVERLAP')
    if len(overlaps) > 8:
        add(settings, 'WARNING', '{} additional overlapping objects omitted'.format(len(overlaps) - 8), code='ICON_OVERLAP_SUMMARY')
    if not _enabled(settings.main_colors):
        add(settings, 'ERROR', 'Main palette has no enabled colors', code='NO_MAIN_COLORS')
    if not _enabled(settings.accent_colors):
        add(settings, 'ERROR', 'Accent palette has no enabled colors', code='NO_ACCENT_COLORS')
    if not _enabled(settings.resolutions):
        add(settings, 'ERROR', 'No render resolution is enabled', code='NO_RESOLUTION')
    if settings.reference_image is not None:
        if not settings.reference_pairs:
            add(settings, 'WARNING', 'Reference image is loaded but has not produced a color pair; run Analyze Reference', getattr(settings.reference_image, 'name', ''), 'REFERENCE_NOT_ANALYZED')
        else:
            best = settings.reference_pairs[0]
            if best.confidence < 0.45:
                add(settings, 'WARNING', 'Reference interpretation has weak heuristic evidence ({:.2f})'.format(best.confidence), best.name, 'LOW_REFERENCE_CONFIDENCE')
    if not settings.bindings:
        add(settings, 'ERROR', 'No material bindings; run Auto Setup', code='NO_BINDINGS')
    if settings.main_parent_material is None:
        add(settings, 'WARNING', 'Main Parent is not set; the stored Main Reference color will be used', code='NO_MAIN_PARENT')
    if settings.accent_parent_material is None:
        add(settings, 'WARNING', 'Accent Parent is not set; the stored Accent Reference color will be used', code='NO_ACCENT_PARENT')
    if settings.main_parent_material is not None and settings.main_parent_material == settings.accent_parent_material:
        add(settings, 'ERROR', 'Main Parent and Accent Parent cannot be the same material', settings.main_parent_material.name, 'SAME_PARENT')
    family_by_material = {binding.material: binding.family for binding in settings.bindings if binding.material is not None}
    if settings.main_parent_material is not None and family_by_material.get(settings.main_parent_material) != 'MAIN':
        add(settings, 'WARNING', 'Main Parent is not assigned to the Main family', settings.main_parent_material.name, 'MAIN_PARENT_MISMATCH')
    if settings.accent_parent_material is not None and family_by_material.get(settings.accent_parent_material) != 'ACCENT':
        add(settings, 'WARNING', 'Accent Parent is not assigned to the Accent family', settings.accent_parent_material.name, 'ACCENT_PARENT_MISMATCH')
    fam = defaultdict(int)
    for b in settings.bindings:
        m = b.material
        fam[b.family] += 1
        if m is None:
            add(settings, 'ERROR', 'Binding points to missing material', code='MISSING_MATERIAL')
            continue
        if b.family in {'MAIN', 'ACCENT'}:
            if not b.enabled:
                add(settings, 'WARNING', 'Recolor binding is disabled', m.name, 'DISABLED_BINDING')
            if not binding_target_is_valid(b):
                add(settings, 'ERROR', 'Recolor material has no valid writable target', m.name, 'INVALID_TARGET')
            if b.confidence < 0.67 and (not b.manual_family):
                add(settings, 'WARNING', 'Weak heuristic evidence for family assignment ({:.2f})'.format(b.confidence), m.name, 'LOW_CONFIDENCE')
        if b.used_by_icon_count > 1:
            add(settings, 'INFO', 'Material is shared by {} icon sets'.format(b.used_by_icon_count), m.name, 'SHARED_MATERIAL')
    if not fam['MAIN']:
        add(settings, 'ERROR', 'No material is assigned to Main', code='NO_MAIN_BINDINGS')
    if not fam['ACCENT']:
        add(settings, 'WARNING', 'No material is assigned to Accent; Accent colors have no effect', code='NO_ACCENT_BINDINGS')
    try:
        output = bpy.path.abspath(settings.output_folder)
    except Exception:
        output = ''
    if not output:
        add(settings, 'ERROR', 'Output folder is empty', code='NO_OUTPUT')
    elif os.path.exists(output) and (not os.path.isdir(output)):
        add(settings, 'ERROR', 'Output path is not a folder', output, 'BAD_OUTPUT')
    try:
        fields = {f for _, f, _, _ in string.Formatter().parse(settings.filename_template) if f}
        unknown = fields - {'icon', 'main', 'accent', 'resolution', 'index'}
        if unknown:
            add(settings, 'ERROR', 'Unknown filename fields: ' + ', '.join(sorted(unknown)), code='BAD_TEMPLATE')
    except ValueError as exc:
        add(settings, 'ERROR', 'Invalid filename template: ' + str(exc), code='BAD_TEMPLATE')
    if not settings.diagnostics:
        add(settings, 'INFO', 'No issues found', code='OK')
    for x in settings.diagnostics:
        counts[x.level] += 1
    settings.diagnostic_index = 0 if settings.diagnostics else -1
    return {'errors': counts['ERROR'], 'warnings': counts['WARNING'], 'info': counts['INFO'], 'total': len(settings.diagnostics)}

def has_blocking_errors(settings):
    return any((x.level == 'ERROR' for x in settings.diagnostics))

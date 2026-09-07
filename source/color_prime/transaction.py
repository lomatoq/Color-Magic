"""Durable copy-on-write material setup.

Only selected icon mesh datablocks and recolorable materials are localized.
Original mesh/slot pointers live in Scene RNA, surviving save/reopen and Undo.
No bpy.ops, no transform/geometry rewrite, no deletion of an author's datablock.
A rollback conflict fails CLOSED rather than replacing an unrelated mesh.
"""
import json
import re
import uuid
import bpy
from .runtime import restore_preview
from .compat import rna_is_array
from .utils import all_icon_objects

OWNER_KEY = 'cp_studio_owner'
SOURCE_KEY = 'cp_studio_source'
_ID_COLLECTIONS = {'Object':'objects', 'Material':'materials', 'Mesh':'meshes',
                   'Collection':'collections', 'Image':'images', 'Scene':'scenes', 'NodeTree':'node_groups'}
_SKIP = {'rna_type','studio','suppress_callbacks','preview_active','render_running','render_paused',
         'render_stop_requested','render_current','render_total'}


def _alive(value):
    try:
        return value is not None and bool(value.as_pointer())
    except (ReferenceError, AttributeError):
        return False


def hard_protected(material, settings):
    if material is None:
        return False
    suffix = str(settings.fixed_name_suffix or '').strip()
    if suffix and re.sub(r'\.\d{3,}$', '', material.name).endswith(suffix):
        return True
    return material.library is not None


def protected(material, settings):
    if material is None:
        return False
    if hard_protected(material, settings):
        return True
    source = str(material.get('color_prime_family_source','')).upper()
    role = str(material.get('color_prime_family','')).upper()
    return role in {'FIXED','IGNORE'} and (source in {'USER','MANUAL'} or bool(material.get('color_prime_locked',False)))


def _encode_id(value):
    if value is None:
        return None
    for type_name, coll in _ID_COLLECTIONS.items():
        cls = getattr(bpy.types, type_name, None)
        if cls and isinstance(value, cls):
            return {'$id': coll, 'name': value.name, 'library': getattr(getattr(value,'library',None),'filepath','')}
    return None


def snapshot_settings(settings):
    """Serialize only declared RNA properties; no arbitrary Python evaluation."""
    out = {}
    for prop in settings.bl_rna.properties:
        key = prop.identifier
        if key in _SKIP or prop.is_readonly and prop.type not in {'COLLECTION','POINTER'}:
            continue
        value = getattr(settings, key)
        if prop.type == 'COLLECTION':
            out[key] = [snapshot_settings(item) for item in value]
        elif prop.type == 'POINTER':
            encoded = _encode_id(value)
            if value is None or encoded is not None:
                out[key] = encoded
        elif rna_is_array(prop):
            out[key] = list(value)
        elif prop.type == 'ENUM' and getattr(prop, 'is_enum_flag', False):
            out[key] = sorted(value)
        elif isinstance(value, (str, float, int, bool)):
            out[key] = value
    return out


def _decode_id(value):
    coll = getattr(bpy.data, value['$id'], None)
    if coll is None:
        return None
    library = value.get('library','')
    for block in coll:
        if block.name == value['name'] and getattr(getattr(block,'library',None),'filepath','') == library:
            return block
    return None


def restore_settings(settings, data):
    for key, value in data.items():
        prop = settings.bl_rna.properties.get(key)
        if prop is None or key in _SKIP:
            continue
        if prop.type == 'COLLECTION':
            coll = getattr(settings, key)
            coll.clear()
            for row in value:
                restore_settings(coll.add(), row)
        elif prop.type == 'POINTER':
            setattr(settings,key,_decode_id(value) if isinstance(value,dict) and '$id' in value else None)
        elif prop.type == 'ENUM' and getattr(prop, 'is_enum_flag', False):
            setattr(settings, key, set(value))
        elif not prop.is_readonly:
            setattr(settings,key,value)


def _cp_metadata(obj):
    out = {}
    for key in obj.keys():
        if key.startswith('color_prime_') or key.startswith('cp_studio_'):
            value = obj[key]
            if hasattr(value, 'to_dict'):
                value = value.to_dict()
            elif hasattr(value, 'to_list'):
                value = value.to_list()
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError):
                continue
            out[key] = value
    return out


def _restore_metadata(obj, data):
    for key in list(obj.keys()):
        if key.startswith('color_prime_') or key.startswith('cp_studio_'):
            # Preserve unsupported author metadata instead of silently deleting it.
            value = obj[key]
            if hasattr(value, 'to_dict'): value = value.to_dict()
            elif hasattr(value, 'to_list'): value = value.to_list()
            try: json.dumps(value, allow_nan=False)
            except (TypeError, ValueError): continue
            del obj[key]
    for key,value in data.items():
        obj[key] = value


def refresh_staged_pointers(settings):
    """Bootstrap may further copy a staged mesh; retain its current owner."""
    stage = settings.studio
    for item in stage.backup_objects:
        if _alive(item.object):
            mesh = item.object.data
            if _alive(mesh) and mesh != item.original_mesh:
                mesh[OWNER_KEY] = stage.stage_id
                item.staged_mesh = mesh


def start(scene, settings, settings_before=None, objects=None):
    stage = settings.studio
    if stage.stage_status != 'NONE':
        raise RuntimeError('A setup is already staged. Accept it or Revert Setup before starting another.')
    if scene is None or getattr(bpy.context,'mode','OBJECT') != 'OBJECT':
        raise RuntimeError('Switch to Object Mode before staging material changes.')
    if settings.render_running or stage.preview_running:
        raise RuntimeError('A render job is using this scene.')
    restore_preview(scene)
    if settings.preview_active:
        raise RuntimeError('Previous preview could not be restored; recover it before staging changes.')
    scope = [o for o in (all_icon_objects(settings,scene,True) if objects is None else objects) if o.type == 'MESH']
    if not scope:
        raise ValueError('No mesh objects in enabled icon sets. Select your Empty and use Add Selected.')
    readonly = [o.name for o in scope if o.library is not None or o.override_library is not None or o.data.library is not None]
    if readonly:
        raise ValueError('Library/override objects are not edited automatically: ' + ', '.join(readonly[:8]))
    # Snapshot before allocating any data so even a MemoryError can be rolled back.
    before = settings_before if settings_before is not None else snapshot_settings(settings)
    stage.stage_settings_json = json.dumps(before, ensure_ascii=False, allow_nan=False)
    stage.stage_id = uuid.uuid4().hex
    stage.stage_status = 'RECOVERY'
    copies = {}
    for obj in sorted(scope,key=lambda o:o.name):
        entry = stage.backup_objects.add()
        entry.object = obj
        entry.original_mesh = obj.data
        entry.active_material_index = obj.active_material_index
        entry.custom_json = json.dumps(_cp_metadata(obj),ensure_ascii=False)
        for slot in obj.material_slots:
            backup = entry.slots.add()
            backup.material = slot.material
            backup.link = slot.link
        local = obj.data.copy()
        local.name = obj.data.name + ' [CP working]'
        local[OWNER_KEY] = stage.stage_id
        entry.staged_mesh = local
        obj.data = local
        for slot in obj.material_slots:
            material = slot.material
            if material is None or protected(material,settings):
                continue
            if stage.reuse_family_library and (material.get('cp_family_master','') or material.get('color_prime_child',False)):
                continue
            key = material.as_pointer()
            if key not in copies:
                copy = material.copy()
                # Do not rename away Main/Accent hints. Blender supplies a suffix.
                copy[OWNER_KEY] = stage.stage_id
                copy[SOURCE_KEY] = material.get(SOURCE_KEY, material.name)
                copies[key] = copy
                rec = stage.backup_materials.add()
                rec.original, rec.staged = material, copy
            slot.material = copies[key]
    # Unassigned library entries need the same isolated graph as assigned ones.
    if not stage.reuse_family_library:
        for child in getattr(settings,'children',()):
            material=child.material
            if not _alive(material) or protected(material,settings) or material.as_pointer() in copies:continue
            copy=material.copy();copy[OWNER_KEY]=stage.stage_id;copy[SOURCE_KEY]=material.get(SOURCE_KEY,material.name)
            copies[material.as_pointer()]=copy
            rec=stage.backup_materials.add();rec.original=material;rec.staged=copy
    # Parent and saved bindings must follow their working copies, preserving manual locks.
    for rec in stage.backup_materials:
        if rec.original and rec.staged and rec.original.name==rec.original.get('color_prime_generated_name',''):
            rec.staged['color_prime_generated_name']=rec.staged.name
    for attr in ('main_parent_material','accent_parent_material'):
        parent = getattr(settings,attr)
        if _alive(parent) and parent.as_pointer() in copies:
            setattr(settings,attr,copies[parent.as_pointer()])
    settings.suppress_callbacks = True
    try:
        from .utils import data_block_identity
        for binding in settings.bindings:
            mat = binding.material
            if _alive(mat) and mat.as_pointer() in copies:
                binding.material = copies[mat.as_pointer()]
                binding.material_identity = data_block_identity(binding.material)
        for child in getattr(settings,'children',()):
            mat=child.material
            if _alive(mat) and mat.as_pointer() in copies:
                child.material=copies[mat.as_pointer()]
    finally:
        settings.suppress_callbacks = False
    stage.stage_status = 'STAGED'
    stage.stage_note = '{} object(s) isolated; originals retained until Accept Setup'.format(len(scope))
    return len(scope)


def extend(scene,settings,objects):
    """Add new meshes to an active transaction without touching its library."""
    stage=settings.studio
    if stage.stage_status!='STAGED':raise ValueError('No active model setup.')
    if bpy.context.mode!='OBJECT':raise ValueError('Switch to Object Mode first.')
    scope=[o for o in objects if o.type=='MESH' and all(e.object!=o for e in stage.backup_objects)]
    if any(o.library or o.override_library or o.data.library for o in scope):raise ValueError('Linked models must be made local first.')
    copies={}
    for obj in scope:
        entry=stage.backup_objects.add();entry.object=obj;entry.original_mesh=obj.data
        entry.active_material_index=obj.active_material_index;entry.custom_json=json.dumps(_cp_metadata(obj),ensure_ascii=False)
        for slot in obj.material_slots:
            backup=entry.slots.add();backup.material=slot.material;backup.link=slot.link
        local=obj.data.copy();local[OWNER_KEY]=stage.stage_id;entry.staged_mesh=local;obj.data=local
        for slot in obj.material_slots:
            mat=slot.material
            if mat is None or protected(mat,settings) or mat.get('cp_family_master','') or mat.get('color_prime_child',False):continue
            if mat not in copies:
                copy=mat.copy();copy[OWNER_KEY]=stage.stage_id;copy[SOURCE_KEY]=mat.get(SOURCE_KEY,mat.name)
                copies[mat]=copy;rec=stage.backup_materials.add();rec.original=mat;rec.staged=copy
            slot.material=copies[mat]
    return scope


def tag_created(settings, before_materials, before_groups):
    """Tag only newly allocated blocks inside a synchronous setup operation."""
    owner = settings.studio.stage_id
    for mat in bpy.data.materials:
        if mat.as_pointer() not in before_materials:
            mat[OWNER_KEY] = owner
    for tree in bpy.data.node_groups:
        if tree.as_pointer() not in before_groups:
            tree[OWNER_KEY] = owner
    refresh_staged_pointers(settings)


def _cleanup_owned(owner):
    # Remove only zero-user working copies. Keep anything the user linked elsewhere.
    for name in ('meshes','materials','node_groups'):
        for block in list(getattr(bpy.data,name)):
            if block.get(OWNER_KEY,'') == owner and block.users == 0:
                getattr(bpy.data,name).remove(block)
    # Removing a group can orphan a nested owned group.
    for _ in range(3):
        orphan = [g for g in bpy.data.node_groups if g.get(OWNER_KEY,'') == owner and g.users == 0]
        if not orphan:
            break
        for g in orphan:
            bpy.data.node_groups.remove(g)


def rollback(scene, settings):
    stage = settings.studio
    if stage.stage_status == 'NONE':
        return 0
    # Replacing an object's data while Blender owns its edit BMesh is unsafe.
    # The new face correction UI can deliberately leave the user in Edit Mode.
    if getattr(bpy.context, 'mode', 'OBJECT') != 'OBJECT':
        raise RuntimeError('Switch to Object Mode (Tab) before reverting the setup.')
    if settings.render_running or stage.preview_running:
        raise RuntimeError('Stop the render job before reverting its scene.')
    conflicts = [e.object.name for e in stage.backup_objects if _alive(e.object) and
                 _alive(e.staged_mesh) and e.object.data != e.staged_mesh]
    if conflicts:
        stage.stage_status = 'RECOVERY'
        raise RuntimeError('Revert stopped: mesh was replaced outside Color Prime on ' + ', '.join(conflicts))
    restore_preview(scene)
    owner = stage.stage_id
    if getattr(stage,'rig_setup_owner','')==owner and owner:
        from .studio_rig import remove
        remove(scene,settings)
    elif getattr(stage,'rig_stage_pose_json',''):
        from .studio_rig import restore_pose
        problems=restore_pose(settings,json.loads(stage.rig_stage_pose_json))
        if problems:raise RuntimeError('; '.join(problems))
        stage.rig_stage_pose_json=''
    before = json.loads(stage.stage_settings_json or '{}')
    if stage.reuse_family_library:
        from .family_links import parent,shared_socket
        for family,key in (('MAIN','main_reference_color'),('ACCENT','accent_reference_color')):
            socket=shared_socket(parent(settings,family))
            if socket is not None and key in before:socket.default_value=before[key]
    restored = 0
    for entry in stage.backup_objects:
        obj = entry.object
        if not _alive(obj) or not _alive(entry.original_mesh):
            continue
        obj.data = entry.original_mesh
        for idx, backup in enumerate(entry.slots):
            if idx < len(obj.material_slots):
                obj.material_slots[idx].link = backup.link
                # DATA values already live in the retained original mesh.
                if backup.link == 'OBJECT':
                    obj.material_slots[idx].material = backup.material
        obj.active_material_index = min(entry.active_material_index,max(0,len(obj.material_slots)-1))
        _restore_metadata(obj,json.loads(entry.custom_json))
        restored += 1
    settings.suppress_callbacks = True
    try:
        from .model_library import restore_hierarchy
        restore_hierarchy(settings)
        restore_settings(settings,before)
    finally:
        settings.suppress_callbacks = False
    # Retained original meshes/materials make failed working-node snapshots irrelevant after full rollback.
    from . import runtime
    runtime._PREVIEWS.pop(scene.as_pointer(), None)
    stage.preview_values.clear(); settings.preview_active = False
    stage.backup_objects.clear()
    stage.backup_materials.clear()
    stage.stage_id = ''
    stage.stage_settings_json = ''
    stage.stage_status = 'NONE'
    stage.reuse_family_library = False
    stage.stage_note = 'Reverted {} object(s): original meshes, slots and settings restored'.format(restored)
    _cleanup_owned(owner)
    if getattr(stage, 'guide_selection_collection', None) is not None:
        from .guided_ops import discard_selection_collection
        discard_selection_collection(settings)
    return restored


def accept(scene, settings):
    """Accept structure, not transient preview colors. This keeps inherited baselines stable."""
    stage = settings.studio
    if settings.render_running or stage.preview_running:
        raise RuntimeError('Finish the render job before accepting setup.')
    if stage.stage_status != 'STAGED':
        raise RuntimeError('No complete staged setup to accept.')
    restore_preview(scene)
    if settings.preview_active:
        raise RuntimeError('Preview restoration is incomplete; originals were retained. Use Revert Setup.')
    owner = stage.stage_id
    stage.backup_objects.clear()
    stage.backup_materials.clear()
    # Original datablocks are NOT deleted; purge remains the author's decision.
    stage.model_backups.clear();stage.model_created.clear()
    if hasattr(stage, 'guide_selection_collection'):
        stage.guide_selection_collection = None  # committed logical set stays referenced by icon_sets
    stage.rig_setup_owner = ''
    stage.rig_stage_pose_json = ''
    stage.stage_status = 'NONE'
    stage.reuse_family_library = False
    stage.stage_id = ''
    stage.stage_settings_json = ''
    stage.stage_note = 'Setup accepted. Palette previews remain separately reversible.'
    _cleanup_owned(owner)

import json
import bpy
from .compat import data_get
from bpy.props import BoolProperty, EnumProperty, StringProperty
from .constants import FAMILY_ITEMS, SCHEMA_VERSION, VERSION_STRING
from .diagnostics import run_diagnostics
from .discovery import add_icon_root, detect_icon_sets, recapture_binding, scan_material_bindings
from .reference_workflow import analyze_reference_image, apply_pair_to_palettes, selected_reference_pair
from .scene_intelligence import auto_prepare_materials
from .runtime import apply_preview, restore_preview
from .targets import find_material_target, read_binding_color
from .utils import active_item, icon_item_display_name, log, objects_from_icon_item, report_exception, sanitize_filename

def ensure_resolution_default(settings):
    if not settings.resolutions:
        x = settings.resolutions.add()
        x.name = '1x'
        x.mode = 'SCALE'
        x.scale_percent = 100
        settings.resolution_index = 0


def _usable_image(value):
    if value is None:
        return None
    try:
        return value if len(value.pixels) >= int(value.size[0]) * int(value.size[1]) * 4 else None
    except Exception:
        return None


def reference_image_from_context(context):
    """Find an image the user has just opened or dragged into Blender.

    This keeps the common workflow one-click: dragging an image into the 3D
    View creates an Image Empty, while opening it in the Image Editor exposes
    it on the current space. No filename convention is required.
    """
    obj = getattr(context, 'active_object', None)
    if obj is not None and getattr(obj, 'type', '') == 'EMPTY':
        image = _usable_image(getattr(obj, 'data', None))
        if image is not None:
            return image
    space = getattr(context, 'space_data', None)
    image = _usable_image(getattr(space, 'image', None))
    if image is not None and not image.get('cp_studio_internal', False):
        return image
    settings = getattr(getattr(context, 'scene', None), 'color_prime', None)
    return _usable_image(getattr(settings, 'reference_image', None)) if settings else None


def _color_close(a, b, eps=1e-4):
    try:
        return all(abs(float(a[i]) - float(b[i])) <= eps for i in range(4))
    except Exception:
        return False


def _looks_like_old_placeholder_palette(settings):
    if len(settings.main_colors) != 1 or len(settings.accent_colors) != 1:
        return False
    main = settings.main_colors[0]
    accent = settings.accent_colors[0]
    return (main.name == 'Main 01' and accent.name == 'Accent 01' and
            _color_close(main.color, (0.18, 0.8, 0.28, 1.0)) and
            _color_close(accent.color, (0.8, 0.18, 0.55, 1.0)))


def seed_palettes_from_detected_scene(settings, force=False):
    """Seed palette #1 from the detected scene without clobbering users.

    Fresh/empty palettes and the old untouched green/pink placeholders are
    replaced. Existing custom palettes from 2.0.x are preserved.
    """
    if settings.palette_initialized_from_scene and not force:
        return False
    has_existing = bool(settings.main_colors or settings.accent_colors)
    replace = force or (not has_existing) or _looks_like_old_placeholder_palette(settings)
    if not replace:
        settings.palette_initialized_from_scene = True
        return False
    settings.main_colors.clear()
    main = settings.main_colors.add()
    main.name = 'Main 01'
    main.color = settings.main_reference_color
    main.enabled = True
    settings.main_color_index = 0
    settings.accent_colors.clear()
    accent = settings.accent_colors.add()
    accent.name = 'Accent 01'
    accent.color = settings.accent_reference_color
    accent.enabled = True
    settings.accent_color_index = 0
    settings.palette_initialized_from_scene = True
    return True

class COLORPRIME_OT_auto_setup(bpy.types.Operator):
    bl_idname = 'color_prime.auto_setup'
    bl_label = 'Auto Setup'
    bl_description = 'Detect icon roots, bind materials, classify Main/Accent/Fixed and capture inheritance'
    bl_options = {'REGISTER', 'UNDO'}
    redetect_icons: BoolProperty(default=False)

    def execute(self, context):
        from .studio_ops import run_auto
        return run_auto(self, context, getattr(self, "redetect_icons", False))


class COLORPRIME_OT_swap_families(bpy.types.Operator):
    bl_idname = 'color_prime.swap_families'
    bl_label = 'Swap Main / Accent Parts'
    bl_description = 'Swap which managed materials are Main and Accent while keeping the selected palette colors in place'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(getattr(context, 'scene', None), 'color_prime', None)
        return bool(settings and any(b.family == 'MAIN' for b in settings.bindings) and
                    any(b.family == 'ACCENT' for b in settings.bindings))

    def execute(self, context):
        scene = context.scene
        settings = scene.color_prime
        preview_was_active = bool(settings.preview_active)
        restore_preview(scene)
        old_main_ref = tuple(settings.main_reference_color)
        old_accent_ref = tuple(settings.accent_reference_color)
        old_main_parent = settings.main_parent_material
        old_accent_parent = settings.accent_parent_material
        changed = 0
        settings.suppress_callbacks = True
        try:
            for binding in settings.bindings:
                if binding.family not in {'MAIN', 'ACCENT'}:
                    continue
                binding.family = 'ACCENT' if binding.family == 'MAIN' else 'MAIN'
                binding.manual_family = True
                binding.locked = True
                try:
                    binding.material['color_prime_family'] = binding.family
                    binding.material['color_prime_family_source'] = 'MANUAL'
                    binding.material['color_prime_locked'] = True
                except Exception:
                    pass
                changed += 1
            settings.main_reference_color = old_accent_ref
            settings.accent_reference_color = old_main_ref
            settings.main_parent_material = old_accent_parent
            settings.accent_parent_material = old_main_parent
            for binding in settings.bindings:
                if binding.family in {'MAIN', 'ACCENT'}:
                    recapture_binding(binding, settings)
        finally:
            settings.suppress_callbacks = False
        if settings.studio.ui_mode == 'GUIDED' and settings.studio.looks:
            from .lookbook import preview
            preview(scene, settings)
        elif preview_was_active or settings.auto_preview_after_setup:
            main = active_item(settings.main_colors, settings.main_color_index)
            accent = active_item(settings.accent_colors, settings.accent_color_index)
            if main is not None and accent is not None:
                apply_preview(scene, settings, main.color, accent.color)
        self.report({'INFO'}, 'Swapped and locked {} Main/Accent material binding(s)'.format(changed))
        return {'FINISHED'}


class COLORPRIME_OT_detect_icons(bpy.types.Operator):
    bl_idname = 'color_prime.detect_icons'
    bl_label = 'Detect Icon Sets'
    bl_options = {'REGISTER', 'UNDO'}
    replace: BoolProperty(default=True)

    def execute(self, context):
        try:
            n, _ = detect_icon_sets(context.scene, context.scene.color_prime, self.replace)
            self.report({'INFO'}, 'Detected {} icon set(s)'.format(n))
            return {'FINISHED'}
        except Exception as exc:
            report_exception(self, 'Icon detection failed', exc)
            return {'CANCELLED'}

class COLORPRIME_OT_add_selected_icon(bpy.types.Operator):
    bl_idname = 'color_prime.add_selected_icon'
    bl_label = 'Add Selected Root'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected = list(context.selected_objects)
        if not selected:
            self.report({'WARNING'}, 'Select an object root first')
            return {'CANCELLED'}
        ptrs = {o.as_pointer() for o in selected}
        roots = [o for o in selected if o.parent is None or o.parent.as_pointer() not in ptrs]
        n = 0
        s = context.scene.color_prime
        for obj in roots:
            obj['color_prime_icon_set'] = True
            n += int(add_icon_root(s, obj=obj, name=obj.name))
        s.icon_set_index = len(s.icon_sets) - 1 if s.icon_sets else -1
        self.report({'INFO'}, 'Added {} root(s)'.format(n))
        return {'FINISHED'}

class COLORPRIME_OT_add_active_collection(bpy.types.Operator):
    bl_idname = 'color_prime.add_active_collection'
    bl_label = 'Add Active Collection'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        lc = getattr(context.view_layer, 'active_layer_collection', None)
        coll = getattr(lc, 'collection', None)
        if coll is None or coll == context.scene.collection:
            self.report({'WARNING'}, 'Choose a child collection in the Outliner')
            return {'CANCELLED'}
        coll['color_prime_icon_set'] = True
        s = context.scene.color_prime
        add_icon_root(s, collection=coll, name=coll.name)
        s.icon_set_index = len(s.icon_sets) - 1
        return {'FINISHED'}

class COLORPRIME_OT_icon_action(bpy.types.Operator):
    bl_idname = 'color_prime.icon_action'
    bl_label = 'Icon Action'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('UP', 'Up', ''), ('DOWN', 'Down', ''), ('REMOVE', 'Remove', ''), ('ALL_ON', 'All', ''), ('ALL_OFF', 'None', '')))

    def execute(self, context):
        s = context.scene.color_prime
        c = s.icon_sets
        i = s.icon_set_index
        if self.action == 'UP' and i > 0:
            c.move(i, i - 1)
            s.icon_set_index -= 1
        elif self.action == 'DOWN' and 0 <= i < len(c) - 1:
            c.move(i, i + 1)
            s.icon_set_index += 1
        elif self.action == 'REMOVE' and 0 <= i < len(c):
            root = c[i].collection_root if c[i].root_kind == 'COLLECTION' else c[i].object_root
            try:
                if root and root.get('color_prime_icon_set', False):
                    del root['color_prime_icon_set']
            except Exception:
                pass
            c.remove(i)
            s.icon_set_index = min(i, len(c) - 1) if c else -1
        elif self.action == 'ALL_ON':
            for x in c:
                x.enabled = True
        elif self.action == 'ALL_OFF':
            for x in c:
                x.enabled = False
        return {'FINISHED'}

class COLORPRIME_OT_scan_materials(bpy.types.Operator):
    bl_idname = 'color_prime.scan_materials'
    bl_label = 'Refresh Materials'
    bl_options = {'REGISTER', 'UNDO'}
    recapture: BoolProperty(default=False)

    def execute(self, context):
        s = context.scene.color_prime
        if not s.icon_sets:
            self.report({'ERROR'}, 'Configure icon sets first')
            return {'CANCELLED'}
        try:
            restore_preview(context.scene)
            r = scan_material_bindings(context.scene, s, self.recapture)
            run_diagnostics(context.scene, s)
            self.report({'INFO'}, r['summary'])
            return {'FINISHED'}
        except Exception as exc:
            report_exception(self, 'Material scan failed', exc)
            return {'CANCELLED'}

class COLORPRIME_OT_recapture(bpy.types.Operator):
    bl_idname = 'color_prime.recapture'
    bl_label = 'Recapture Relationships'
    bl_options = {'REGISTER', 'UNDO'}
    scope: EnumProperty(items=(('ACTIVE', 'Active', ''), ('ALL', 'All', '')), default='ALL')

    def execute(self, context):
        s = context.scene.color_prime
        restore_preview(context.scene)
        items = [active_item(s.bindings, s.binding_index)] if self.scope == 'ACTIVE' else list(s.bindings)
        n = sum((1 for b in items if b and recapture_binding(b, s)))
        self.report({'INFO'}, 'Recaptured {} material(s)'.format(n))
        return {'FINISHED'}

class COLORPRIME_OT_binding_family(bpy.types.Operator):
    bl_idname = 'color_prime.binding_family'
    bl_label = 'Set Family'
    bl_options = {'REGISTER', 'UNDO'}
    family: EnumProperty(items=FAMILY_ITEMS, default='MAIN')

    def execute(self, context):
        s = context.scene.color_prime
        b = active_item(s.bindings, s.binding_index)
        if not b:
            return {'CANCELLED'}
        restore_preview(context.scene)
        b.family = self.family
        b.manual_family = True
        recapture_binding(b, s)
        return {'FINISHED'}

class COLORPRIME_OT_make_icon_materials_single_user(bpy.types.Operator):
    bl_idname = 'color_prime.make_icon_materials_single_user'
    bl_label = 'Make Icon Materials Local'
    bl_description = 'Duplicate materials that are also used outside the active icon'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        s = scene.color_prime
        icon = active_item(s.icon_sets, s.icon_set_index)
        if not icon:
            return {'CANCELLED'}
        target = objects_from_icon_item(icon, scene)
        ptrs = {o.as_pointer() for o in target}
        external = set()
        for obj in scene.objects:
            if obj.type == 'MESH' and obj.as_pointer() not in ptrs:
                external.update((slot.material.as_pointer() for slot in obj.material_slots if slot.material))
        copies = {}
        changed = 0
        suffix = sanitize_filename(icon_item_display_name(icon))
        for obj in target:
            if obj.type != 'MESH':
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if mat and mat.as_pointer() in external:
                    if mat.as_pointer() not in copies:
                        copies[mat.as_pointer()] = mat.copy()
                        copies[mat.as_pointer()].name = mat.name + '__' + suffix
                    slot.material = copies[mat.as_pointer()]
                    changed += 1
        if changed:
            scan_material_bindings(scene, s, True)
            run_diagnostics(scene, s)
        self.report({'INFO'}, 'Localized {} material slot(s)'.format(changed))
        return {'FINISHED'}


class COLORPRIME_OT_mark_active_node_target(bpy.types.Operator):
    bl_idname = 'color_prime.mark_active_node_target'
    bl_label = 'Use Active Node as Family Target'
    bl_description = 'Tag the active material node as the only Color Prime color target and assign its family'
    bl_options = {'REGISTER', 'UNDO'}
    family: EnumProperty(items=FAMILY_ITEMS, default='MAIN')

    @classmethod
    def poll(cls, context):
        obj = getattr(context, 'object', None)
        material = getattr(obj, 'active_material', None) if obj else None
        space = getattr(context, 'space_data', None)
        return bool(material and material.use_nodes and space and getattr(space, 'tree_type', '') == 'ShaderNodeTree')

    def execute(self, context):
        material = context.object.active_material
        tree = material.node_tree
        node = tree.nodes.active if tree else None
        if node is None:
            self.report({'ERROR'}, 'Select a node in the material Shader Editor')
            return {'CANCELLED'}
        if getattr(node, 'id_data', None) != tree:
            self.report({'ERROR'}, 'Select a node in the material root graph, not inside a shared node group')
            return {'CANCELLED'}
        color_capable = node.type == 'RGB' or any(getattr(socket, 'type', '') == 'RGBA' and not socket.is_linked for socket in node.inputs)
        if not color_capable:
            self.report({'ERROR'}, 'The active node has no safe unlinked color value')
            return {'CANCELLED'}
        for candidate in tree.nodes:
            try:
                if candidate.get('color_prime_target', False):
                    del candidate['color_prime_target']
            except Exception:
                pass
        node['color_prime_target'] = True
        scene = context.scene
        settings = scene.color_prime
        restore_preview(scene)
        scan_material_bindings(scene, settings, False)
        binding = next((item for item in settings.bindings if item.material == material), None)
        if binding is None or binding.target_kind == 'NONE':
            self.report({'ERROR'}, 'The selected node could not be resolved as a writable target')
            return {'CANCELLED'}
        binding.family = self.family
        binding.manual_family = True
        binding.locked = True
        recapture_binding(binding, settings)
        settings.binding_index = next((index for index, item in enumerate(settings.bindings) if item.material == material), 0)
        self.report({'INFO'}, "{} is now a locked {} target".format(material.name, self.family.title()))
        return {'FINISHED'}

class COLORPRIME_OT_sync_parent_references(bpy.types.Operator):
    bl_idname = 'color_prime.sync_parent_references'
    bl_label = 'Sync from Parent Materials'
    bl_description = 'Read current parent-material colors and recapture all family relationships'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        s = scene.color_prime
        restore_preview(scene)
        updated = 0
        for family, parent, prop in (('MAIN', s.main_parent_material, 'main_reference_color'), ('ACCENT', s.accent_parent_material, 'accent_reference_color')):
            if parent is None:
                continue
            binding = next((b for b in s.bindings if b.material == parent), None)
            color = read_binding_color(binding) if binding else find_material_target(parent, False).color
            if color is not None:
                setattr(s, prop, color)
                updated += 1
        for b in s.bindings:
            if b.family in {'MAIN', 'ACCENT'}:
                recapture_binding(b, s)
        self.report({'INFO'}, 'Synced {} parent reference(s)'.format(updated))
        return {'FINISHED'}

class COLORPRIME_OT_preview(bpy.types.Operator):
    bl_idname = 'color_prime.preview'
    bl_label = 'Preview Selected Colors'

    @classmethod
    def poll(cls, context):
        s = context.scene.color_prime
        return not s.render_running and active_item(s.main_colors, s.main_color_index) is not None and (active_item(s.accent_colors, s.accent_color_index) is not None)

    def execute(self, context):
        s = context.scene.color_prime
        main = active_item(s.main_colors, s.main_color_index)
        accent = active_item(s.accent_colors, s.accent_color_index)
        n, failed = apply_preview(context.scene, s, main.color, accent.color)
        if not n:
            self.report({'WARNING'}, 'No target changed')
            return {'CANCELLED'}
        self.report({'WARNING'} if failed else {'INFO'}, 'Previewed {} target(s){}'.format(n, '; {} failed'.format(len(failed)) if failed else ''))
        return {'FINISHED'}

class COLORPRIME_OT_restore_preview(bpy.types.Operator):
    bl_idname = 'color_prime.restore_preview'
    bl_label = 'Restore Preview'

    @classmethod
    def poll(cls, context):
        return context.scene.color_prime.preview_active

    def execute(self, context):
        self.report({'INFO'}, 'Restored {} target(s)'.format(restore_preview(context.scene)))
        return {'FINISHED'}

class COLORPRIME_OT_palette_add(bpy.types.Operator):
    bl_idname = 'color_prime.palette_add'
    bl_label = 'Add Color'
    bl_options = {'REGISTER', 'UNDO'}
    palette: EnumProperty(items=(('MAIN', 'Main', ''), ('ACCENT', 'Accent', '')), default='MAIN')

    def execute(self, context):
        s = context.scene.color_prime
        c = s.main_colors if self.palette == 'MAIN' else s.accent_colors
        x = c.add()
        x.name = '{} {:02d}'.format(self.palette.title(), len(c))
        x.color = s.main_reference_color if self.palette == 'MAIN' else s.accent_reference_color
        setattr(s, 'main_color_index' if self.palette == 'MAIN' else 'accent_color_index', len(c) - 1)
        return {'FINISHED'}

class COLORPRIME_OT_palette_action(bpy.types.Operator):
    bl_idname = 'color_prime.palette_action'
    bl_label = 'Palette Action'
    bl_options = {'REGISTER', 'UNDO'}
    palette: EnumProperty(items=(('MAIN', 'Main', ''), ('ACCENT', 'Accent', '')), default='MAIN')
    action: EnumProperty(items=(('REMOVE', 'Remove', ''), ('UP', 'Up', ''), ('DOWN', 'Down', '')))

    def execute(self, context):
        s = context.scene.color_prime
        c = s.main_colors if self.palette == 'MAIN' else s.accent_colors
        prop = 'main_color_index' if self.palette == 'MAIN' else 'accent_color_index'
        i = getattr(s, prop)
        if not 0 <= i < len(c):
            return {'CANCELLED'}
        if self.action == 'REMOVE':
            c.remove(i)
            setattr(s, prop, min(i, len(c) - 1) if c else -1)
        elif self.action == 'UP' and i > 0:
            c.move(i, i - 1)
            setattr(s, prop, i - 1)
        elif self.action == 'DOWN' and i < len(c) - 1:
            c.move(i, i + 1)
            setattr(s, prop, i + 1)
        return {'FINISHED'}


class COLORPRIME_OT_copy_palette(bpy.types.Operator):
    bl_idname = 'color_prime.copy_palette'
    bl_label = 'Copy Palette'
    bl_options = {'REGISTER', 'UNDO'}
    direction: EnumProperty(items=(('MAIN_TO_ACCENT', 'Main to Accent', ''), ('ACCENT_TO_MAIN', 'Accent to Main', '')), default='MAIN_TO_ACCENT')

    def execute(self, context):
        settings = context.scene.color_prime
        source = settings.main_colors if self.direction == 'MAIN_TO_ACCENT' else settings.accent_colors
        target = settings.accent_colors if self.direction == 'MAIN_TO_ACCENT' else settings.main_colors
        target_index = 'accent_color_index' if self.direction == 'MAIN_TO_ACCENT' else 'main_color_index'
        target.clear()
        for source_item in source:
            item = target.add()
            item.name = source_item.name
            item.enabled = source_item.enabled
            item.color = source_item.color
        setattr(settings, target_index, 0 if target else -1)
        self.report({'INFO'}, 'Copied {} color(s)'.format(len(source)))
        return {'FINISHED'}

class COLORPRIME_OT_palette_save(bpy.types.Operator):
    bl_idname = 'color_prime.palette_save'
    bl_label = 'Save Palette'
    palette: EnumProperty(items=(('MAIN', 'Main', ''), ('ACCENT', 'Accent', '')))
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def invoke(self, context, event):
        self.filepath = bpy.path.abspath('//{}_palette.json'.format(self.palette.lower()))
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        c = context.scene.color_prime.main_colors if self.palette == 'MAIN' else context.scene.color_prime.accent_colors
        path = self.filepath if self.filepath.lower().endswith('.json') else self.filepath + '.json'
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'format': 'color-prime-palette', 'version': 2, 'palette_type': self.palette.lower(), 'colors': [{'name': x.name, 'enabled': x.enabled, 'color': list(x.color)} for x in c]}, f, indent=2, ensure_ascii=False)
            return {'FINISHED'}
        except Exception as exc:
            report_exception(self, 'Palette save failed', exc)
            return {'CANCELLED'}

class COLORPRIME_OT_palette_load(bpy.types.Operator):
    bl_idname = 'color_prime.palette_load'
    bl_label = 'Load Palette'
    bl_options = {'REGISTER', 'UNDO'}
    palette: EnumProperty(items=(('MAIN', 'Main', ''), ('ACCENT', 'Accent', '')))
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        s = context.scene.color_prime
        c = s.main_colors if self.palette == 'MAIN' else s.accent_colors
        prop = 'main_color_index' if self.palette == 'MAIN' else 'accent_color_index'
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            parsed = []
            for d in data.get('colors', []):
                if isinstance(d, dict) and isinstance(d.get('color'), list) and (len(d['color']) >= 3):
                    rgba = list(d['color'][:4])
                    rgba += [1.0] * (4 - len(rgba))
                    parsed.append((str(d.get('name', 'Color')), bool(d.get('enabled', True)), rgba))
            if not parsed:
                raise ValueError('No valid colors')
            c.clear()
            for name, enabled, rgba in parsed:
                x = c.add()
                x.name = name
                x.enabled = enabled
                x.color = [max(0, min(1, float(v))) for v in rgba]
            setattr(s, prop, 0)
            return {'FINISHED'}
        except Exception as exc:
            report_exception(self, 'Palette load failed', exc)
            return {'CANCELLED'}

class COLORPRIME_OT_resolution_add(bpy.types.Operator):
    bl_idname = 'color_prime.resolution_add'
    bl_label = 'Add Resolution'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.color_prime
        x = s.resolutions.add()
        x.name = '{}x'.format(len(s.resolutions))
        x.scale_percent=min(1600,len(s.resolutions)*100)
        s.resolution_index = len(s.resolutions) - 1
        return {'FINISHED'}

class COLORPRIME_OT_resolution_action(bpy.types.Operator):
    bl_idname = 'color_prime.resolution_action'
    bl_label = 'Resolution Action'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('REMOVE', 'Remove', ''), ('UP', 'Up', ''), ('DOWN', 'Down', '')))

    def execute(self, context):
        s = context.scene.color_prime
        c = s.resolutions
        i = s.resolution_index
        if not 0 <= i < len(c):
            return {'CANCELLED'}
        if self.action == 'REMOVE':
            c.remove(i)
            s.resolution_index = min(i, len(c) - 1) if c else -1
        elif self.action == 'UP' and i > 0:
            c.move(i, i - 1)
            s.resolution_index -= 1
        elif self.action == 'DOWN' and i < len(c) - 1:
            c.move(i, i + 1)
            s.resolution_index += 1
        return {'FINISHED'}

class COLORPRIME_OT_run_diagnostics(bpy.types.Operator):
    bl_idname = 'color_prime.run_diagnostics'
    bl_label = 'Run Diagnostics'

    def execute(self, context):
        r = run_diagnostics(context.scene, context.scene.color_prime)
        self.report({'ERROR'} if r['errors'] else {'WARNING'} if r['warnings'] else {'INFO'}, '{} errors, {} warnings'.format(r['errors'], r['warnings']))
        return {'FINISHED'}

class COLORPRIME_OT_export_diagnostics(bpy.types.Operator):
    bl_idname = 'color_prime.export_diagnostics'
    bl_label = 'Export Diagnostics'
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def invoke(self, context, event):
        self.filepath = bpy.path.abspath('//color_prime_diagnostics.json')
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        s = context.scene.color_prime
        run_diagnostics(context.scene, s)
        path = self.filepath if self.filepath.lower().endswith('.json') else self.filepath + '.json'
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'addon_version': VERSION_STRING, 'blender_version': bpy.app.version_string, 'scene': context.scene.name, 'setup_summary': s.last_setup_summary, 'diagnostics': [{'level': x.level, 'code': x.code, 'message': x.message, 'data': x.data_name} for x in s.diagnostics]}, f, indent=2, ensure_ascii=False)
            return {'FINISHED'}
        except Exception as exc:
            report_exception(self, 'Diagnostic export failed', exc)
            return {'CANCELLED'}

class COLORPRIME_OT_import_legacy(bpy.types.Operator):
    bl_idname = 'color_prime.import_legacy'
    bl_label = 'Import v1 Scene Settings'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return hasattr(context.scene, 'color_prime_props')

    def execute(self, context):
        scene = context.scene
        old = scene.color_prime_props
        s = scene.color_prime
        try:
            s.main_colors.clear()
            s.accent_colors.clear()
            s.icon_sets.clear()
            s.resolutions.clear()
            for source in old.base_colors:
                x = s.main_colors.add()
                x.name = source.name
                x.color = source.color
            for source in old.accent_colors:
                x = s.accent_colors.add()
                x.name = source.name
                x.color = source.color
            for source in old.anchor_objects:
                obj = data_get('objects', source.original_name)
                if obj:
                    add_icon_root(s, obj=obj, name=source.name)
                    s.icon_sets[len(s.icon_sets) - 1].enabled = source.include
            if old.color_export_folder:
                s.output_folder = old.color_export_folder
            r = s.resolutions.add()
            r.name = '{}%'.format(old.resolution_multiplier)
            r.scale_percent = old.resolution_multiplier
            s.main_color_index = 0 if s.main_colors else -1
            s.accent_color_index = 0 if s.accent_colors else -1
            s.resolution_index = 0
            scan_material_bindings(scene, s, True)
            run_diagnostics(scene, s)
            return {'FINISHED'}
        except Exception as exc:
            report_exception(self, 'Legacy import failed', exc)
            return {'CANCELLED'}


class COLORPRIME_OT_prepare_materials(bpy.types.Operator):
    bl_idname = 'color_prime.prepare_materials'
    bl_label = 'Auto Prepare Materials'
    bl_description = 'Infer object roles from shader lineage and camera/surface geometry, create missing materials and split cross-role sharing'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .studio_ops import run_auto
        return run_auto(self, context, getattr(self, "redetect_icons", False))


class COLORPRIME_OT_reference_load(bpy.types.Operator):
    bl_idname = 'color_prime.reference_load'
    bl_label = 'Load Reference Image'
    bl_options = {'REGISTER', 'UNDO'}
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.png;*.jpg;*.jpeg;*.webp;*.tif;*.tiff;*.bmp;*.exr;*.hdr', options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.filepath:
            self.report({'ERROR'}, 'Choose an image file')
            return {'CANCELLED'}
        try:
            try:
                image = bpy.data.images.load(self.filepath, check_existing=True)
            except TypeError:
                image = bpy.data.images.load(self.filepath)
            settings = context.scene.color_prime
            settings.reference_image = image
            analysis = analyze_reference_image(image, settings)
            self.report({'INFO'}, analysis.summary)
            return {'FINISHED'}
        except Exception as exc:
            report_exception(self, 'Reference image load failed', exc)
            return {'CANCELLED'}


class COLORPRIME_OT_reference_analyze(bpy.types.Operator):
    bl_idname = 'color_prime.reference_analyze'
    bl_label = 'Analyze Reference'
    bl_description = 'Extract stable hue families and score clean Main/Accent pairs locally, without network or external models'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return getattr(context.scene.color_prime, 'reference_image', None) is not None

    def execute(self, context):
        settings = context.scene.color_prime
        try:
            analysis = analyze_reference_image(settings.reference_image, settings)
            self.report({'INFO'}, analysis.summary)
            return {'FINISHED'}
        except Exception as exc:
            report_exception(self, 'Reference analysis failed', exc)
            return {'CANCELLED'}


class COLORPRIME_OT_reference_use_pair(bpy.types.Operator):
    bl_idname = 'color_prime.reference_use_pair'
    bl_label = 'Use Reference Pair'
    bl_options = {'REGISTER', 'UNDO'}
    append: BoolProperty(name='Append to Palettes', default=False)

    @classmethod
    def poll(cls, context):
        return selected_reference_pair(context.scene.color_prime) is not None

    def execute(self, context):
        settings = context.scene.color_prime
        pair = selected_reference_pair(settings)
        if pair is None:
            return {'CANCELLED'}
        if not apply_pair_to_palettes(settings, pair, self.append):
            return {'CANCELLED'}
        self.report({'INFO'}, '{} applied to {} palette slots'.format(pair.name, 'new' if self.append else 'active'))
        return {'FINISHED'}


class COLORPRIME_OT_reference_preview_pair(bpy.types.Operator):
    bl_idname = 'color_prime.reference_preview_pair'
    bl_label = 'Preview Reference Pair'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        settings = context.scene.color_prime
        return not settings.render_running and selected_reference_pair(settings) is not None

    def execute(self, context):
        settings = context.scene.color_prime
        pair = selected_reference_pair(settings)
        changed, failed = apply_preview(context.scene, settings, pair.main_color, pair.accent_color)
        if not changed:
            self.report({'WARNING'}, 'No managed material target changed')
            return {'CANCELLED'}
        self.report({'WARNING'} if failed else {'INFO'}, 'Previewed {} material target(s)'.format(changed))
        return {'FINISHED'}


class COLORPRIME_OT_reference_clear(bpy.types.Operator):
    bl_idname = 'color_prime.reference_clear'
    bl_label = 'Clear Reference Results'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.color_prime
        settings.reference_pairs.clear()
        settings.reference_pair_index = -1
        settings.reference_last_summary = 'No reference analyzed'
        return {'FINISHED'}

CLASSES = (COLORPRIME_OT_auto_setup, COLORPRIME_OT_swap_families, COLORPRIME_OT_prepare_materials, COLORPRIME_OT_reference_load, COLORPRIME_OT_reference_analyze, COLORPRIME_OT_reference_use_pair, COLORPRIME_OT_reference_preview_pair, COLORPRIME_OT_reference_clear, COLORPRIME_OT_detect_icons, COLORPRIME_OT_add_selected_icon, COLORPRIME_OT_add_active_collection, COLORPRIME_OT_icon_action, COLORPRIME_OT_scan_materials, COLORPRIME_OT_recapture, COLORPRIME_OT_binding_family, COLORPRIME_OT_make_icon_materials_single_user, COLORPRIME_OT_mark_active_node_target, COLORPRIME_OT_sync_parent_references, COLORPRIME_OT_preview, COLORPRIME_OT_restore_preview, COLORPRIME_OT_palette_add, COLORPRIME_OT_palette_action, COLORPRIME_OT_copy_palette, COLORPRIME_OT_palette_save, COLORPRIME_OT_palette_load, COLORPRIME_OT_resolution_add, COLORPRIME_OT_resolution_action, COLORPRIME_OT_run_diagnostics, COLORPRIME_OT_export_diagnostics, COLORPRIME_OT_import_legacy)


def _protect_operator(cls):
    original_poll = getattr(cls, 'poll', None)
    original_execute = cls.execute
    def poll(klass, context):
        from .studio_ops import idle
        return idle(context) and (bool(original_poll(context)) if original_poll else True)
    def execute(self, context):
        from .studio_ops import idle
        if not idle(context):
            self.report({'ERROR'}, 'Finish the render before changing Color Prime data')
            return {'CANCELLED'}
        return original_execute(self, context)
    cls.poll = classmethod(poll)
    cls.execute = execute
for _operator_class in CLASSES:
    _protect_operator(_operator_class)

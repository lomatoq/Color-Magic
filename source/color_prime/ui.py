import bpy
from .color_math import color_to_hex
from .rendering import count_render_tasks
from .utils import active_item


class COLORPRIME_UL_colors(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'enabled', text='')
        row.prop(item, 'name', text='', emboss=False)
        row.prop(item, 'color', text='')
        row.label(text=color_to_hex(item.color))


class COLORPRIME_UL_reference_pairs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.name, icon='COLOR')
        row.prop(item, 'main_color', text='')
        row.prop(item, 'accent_color', text='')
        row.label(text='evidence {:.2f}'.format(item.confidence))


class COLORPRIME_UL_icons(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'enabled', text='')
        row.prop(item, 'name', text='', emboss=False, icon='OUTLINER_COLLECTION' if item.root_kind == 'COLLECTION' else 'OBJECT_DATA')
        root = item.collection_root if item.root_kind == 'COLLECTION' else item.object_root
        if root is None:
            row.label(text='Missing', icon='ERROR')
        elif item.auto_detected:
            row.label(text='Auto')


class COLORPRIME_UL_bindings(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'enabled', text='')
        row.label(text=item.material.name if item.material else 'Missing', icon='MATERIAL' if item.material else 'ERROR')
        row.prop(item, 'family', text='')
        if item.locked:
            row.label(text='', icon='LOCKED')
        elif item.target_kind == 'NONE' and item.family in {'MAIN', 'ACCENT'}:
            row.label(text='', icon='ERROR')
        elif not item.manual_family:
            row.label(text='evidence {:.2f}'.format(item.confidence))


class COLORPRIME_UL_resolutions(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'enabled', text='')
        row.prop(item, 'name', text='', emboss=False)
        row.label(text='{} × {}'.format(item.width, item.height) if item.mode == 'ABSOLUTE' else '{}%'.format(item.scale_percent))


class COLORPRIME_UL_diagnostics(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.message, icon={'INFO': 'INFO', 'WARNING': 'ERROR', 'ERROR': 'CANCEL'}.get(item.level, 'INFO'))
        if item.data_name:
            row.label(text=item.data_name)


def _palette(layout, settings, family):
    main = family == 'MAIN'
    collection = settings.main_colors if main else settings.accent_colors
    index = settings.main_color_index if main else settings.accent_color_index
    collection_name = 'main_colors' if main else 'accent_colors'
    index_name = 'main_color_index' if main else 'accent_color_index'
    box = layout.box()
    head = box.row(align=True)
    head.label(text=('Main' if main else 'Accent') + ' Palette', icon='COLOR')
    from .palette_sets import draw
    draw(box,settings,family)
    op = head.operator('color_prime.palette_save', text='', icon='FILE_TICK')
    op.palette = family
    op = head.operator('color_prime.palette_load', text='', icon='FILE_FOLDER')
    op.palette = family
    row = box.row()
    row.template_list('COLORPRIME_UL_colors', family.lower(), settings, collection_name, settings, index_name, rows=3)
    buttons = row.column(align=True)
    op = buttons.operator('color_prime.palette_add', text='', icon='ADD')
    op.palette = family
    for action, icon in (('REMOVE', 'REMOVE'), ('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN')):
        op = buttons.operator('color_prime.palette_action', text='', icon=icon)
        op.palette = family
        op.action = action
    item = active_item(collection, index)
    if item:
        row = box.row(align=True)
        row.prop(item, 'name', text='')
        row.prop(item, 'color', text='')
        row.prop(item, 'enabled', text='Use')


def _draw_autopilot(layout, settings):
    box = layout.box()
    head = box.row(align=True)
    head.label(text='1 · Autopilot', icon='MODIFIER')
    head.label(text='offline')
    row = box.row()
    row.scale_y = 1.35
    op = row.operator('color_prime.auto_setup', text='AUTO SETUP EVERYTHING', icon='MODIFIER')
    op.redetect_icons = not bool(settings.icon_sets)
    box.label(text='Detect icons → infer Main/Accent → create/split materials → analyze reference', icon='INFO')
    box.label(text='Tip: drag an image into the 3D View, select it, then press Auto Setup.')
    box.label(text="End any material name with '{}' to keep it unchanged, e.g. Gold{}".format(settings.fixed_name_suffix, settings.fixed_name_suffix))
    if settings.autopilot_last_summary:
        box.label(text=settings.autopilot_last_summary)
    row = box.row(align=True)
    row.prop(settings, 'auto_prepare_materials')
    row.operator('color_prime.prepare_materials', text='Re-run Materials', icon='FILE_REFRESH')
    row = box.row(align=True)
    row.operator('color_prime.swap_families', text='Wrong side? Swap Main / Accent', icon='ARROW_LEFTRIGHT')


def _draw_reference(layout, settings):
    box = layout.box()
    head = box.row(align=True)
    head.label(text='2 · Reference Image → Clean Color Pairs', icon='IMAGE_DATA')
    head.operator('color_prime.reference_load', text='Load', icon='FILE_FOLDER')
    row = box.row(align=True)
    row.prop(settings, 'reference_image', text='')
    row.operator('color_prime.reference_analyze', text='Analyze', icon='VIEWZOOM')
    row.operator('color_prime.reference_clear', text='', icon='X')
    box.prop(settings, 'reference_auto_use')
    box.prop(settings, 'auto_preview_after_setup')
    if settings.reference_image is not None:
        try:
            preview = box.column()
            preview.scale_y = 0.55
            preview.template_preview(settings.reference_image, show_buttons=False)
        except Exception:
            pass
    box.label(text=settings.reference_last_summary, icon='INFO')
    if settings.reference_pairs:
        box.template_list('COLORPRIME_UL_reference_pairs', 'reference_pairs', settings, 'reference_pairs', settings, 'reference_pair_index', rows=min(4, len(settings.reference_pairs)))
        pair = active_item(settings.reference_pairs, settings.reference_pair_index)
        if pair:
            detail = box.box()
            row = detail.row(align=True)
            row.label(text=pair.name)
            row.label(text='rank {:.2f}'.format(pair.score))
            row = detail.row(align=True)
            row.prop(pair, 'main_color', text='Main')
            row.prop(pair, 'accent_color', text='Accent')
            detail.label(text='Coverage: Main {:.0f}% · Accent {:.0f}%'.format(pair.main_coverage * 100.0, pair.accent_coverage * 100.0))
            detail.label(text=pair.reason)
            row = detail.row(align=True)
            row.operator('color_prime.reference_preview_pair', text='Preview Pair', icon='HIDE_OFF')
            op = row.operator('color_prime.reference_use_pair', text='Use in Active Palette', icon='CHECKMARK')
            op.append = False
            op = row.operator('color_prime.reference_use_pair', text='Add Pair', icon='ADD')
            op.append = True


def _draw_colors(layout, settings):
    box = layout.box()
    box.label(text='3 · Palette & Preview', icon='COLOR')
    box.prop(settings, 'combination_mode')
    _palette(box, settings, 'MAIN')
    _palette(box, settings, 'ACCENT')
    row = box.row(align=True)
    row.scale_y = 1.2
    row.operator('color_prime.preview', text='Preview Selected', icon='HIDE_OFF')
    restore = row.row(align=True)
    restore.enabled = settings.preview_active
    restore.operator('color_prime.restore_preview', text='Restore', icon='LOOP_BACK')


def _draw_icon_sets(layout, settings):
    box = layout.box()
    head = box.row(align=True)
    head.label(text='Icon Sets', icon='OUTLINER_COLLECTION')
    op = head.operator('color_prime.detect_icons', text='Re-detect', icon='VIEWZOOM')
    op.replace = True
    op = head.operator('color_prime.icon_action', text='All')
    op.action = 'ALL_ON'
    op = head.operator('color_prime.icon_action', text='None')
    op.action = 'ALL_OFF'
    row = box.row(align=True)
    row.operator('color_prime.add_selected_icon', icon='OBJECT_DATA')
    row.operator('color_prime.add_active_collection', icon='OUTLINER_COLLECTION')
    row = box.row()
    row.template_list('COLORPRIME_UL_icons', 'icons', settings, 'icon_sets', settings, 'icon_set_index', rows=4)
    buttons = row.column(align=True)
    for action, icon in (('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN'), ('REMOVE', 'X')):
        op = buttons.operator('color_prime.icon_action', text='', icon=icon)
        op.action = action
    item = active_item(settings.icon_sets, settings.icon_set_index)
    if item:
        detail = box.box()
        detail.prop(item, 'name')
        detail.prop(item, 'root_kind')
        detail.prop(item, 'collection_root' if item.root_kind == 'COLLECTION' else 'object_root')
        detail.label(text=item.status or 'Manual')
        detail.operator('color_prime.make_icon_materials_single_user', icon='DUPLICATE')


def _draw_materials(layout, settings):
    box = layout.box()
    head = box.row(align=True)
    head.label(text='Material Families', icon='MATERIAL')
    op = head.operator('color_prime.scan_materials', text='Refresh', icon='FILE_REFRESH')
    op.recapture = False
    box.template_list('COLORPRIME_UL_bindings', 'bindings', settings, 'bindings', settings, 'binding_index', rows=7)
    binding = active_item(settings.bindings, settings.binding_index)
    if binding:
        detail = box.box()
        detail.prop(binding, 'family')
        detail.prop(binding, 'locked')
        detail.prop(binding, 'inheritance_mode')
        detail.label(text='Target: ' + binding.target_kind.replace('_', ' ').title())
        detail.label(text=binding.status or 'No status')
        detail.label(text='Auto: {} (evidence {:.2f})'.format(binding.auto_reason or 'manual', binding.confidence))
        if binding.family in {'MAIN', 'ACCENT'}:
            detail.prop(binding, 'captured_family_color')
            detail.prop(binding, 'captured_material_color')
        row = detail.row(align=True)
        for family, label in (('MAIN', 'Main'), ('ACCENT', 'Accent'), ('FIXED', 'Fixed'), ('IGNORE', 'Ignore')):
            op = row.operator('color_prime.binding_family', text=label)
            op.family = family
        op = detail.operator('color_prime.recapture', text='Recapture This', icon='EYEDROPPER')
        op.scope = 'ACTIVE'
    row = box.row(align=True)
    op = row.operator('color_prime.recapture', text='Recapture All Relationships', icon='EYEDROPPER')
    op.scope = 'ALL'
    row.operator('color_prime.sync_parent_references', text='Sync Parents', icon='FILE_REFRESH')


def _draw_advanced(layout, settings):
    box = layout.box()
    box.prop(settings, 'show_advanced', text='Advanced', icon='TRIA_DOWN' if settings.show_advanced else 'TRIA_RIGHT', emboss=False)
    if not settings.show_advanced:
        return
    col = box.column(align=True)
    col.prop(settings, 'detection_mode')
    col.prop(settings, 'auto_localize_legacy')
    col.prop(settings, 'auto_protect_textures')
    col.prop(settings, 'fixed_name_suffix')
    col.prop(settings, 'auto_split_shared_materials')
    col.prop(settings, 'localize_per_icon')
    col.prop(settings, 'geometry_analysis_mode')
    col.prop(settings, 'loose_part_mode')
    col.separator()
    col.prop(settings, 'reference_input_encoding')
    col.prop(settings, 'reference_buffer_alpha')
    col.prop(settings, 'reference_background_mode')
    col.prop(settings, 'reference_clean_strength')
    col.prop(settings, 'reference_cluster_count')
    col.prop(settings, 'reference_max_samples')
    col.prop(settings, 'reference_alpha_threshold')
    col.separator()
    col.prop(settings, 'main_parent_material')
    col.prop(settings, 'accent_parent_material')
    col.prop(settings, 'main_reference_color')
    col.prop(settings, 'accent_reference_color')
    _draw_icon_sets(box, settings)
    _draw_materials(box, settings)


def _draw_resolutions(layout, settings):
    box = layout.box()
    box.label(text='4 · Resolutions', icon='IMAGE_DATA')
    row = box.row()
    row.template_list('COLORPRIME_UL_resolutions', 'resolutions', settings, 'resolutions', settings, 'resolution_index', rows=3)
    buttons = row.column(align=True)
    buttons.operator('color_prime.resolution_add', text='', icon='ADD')
    for action, icon in (('REMOVE', 'REMOVE'), ('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN')):
        op = buttons.operator('color_prime.resolution_action', text='', icon=icon)
        op.action = action
    item = active_item(settings.resolutions, settings.resolution_index)
    if item:
        box.prop(item, 'name')
        box.prop(item, 'mode')
        if item.mode == 'ABSOLUTE':
            row = box.row(align=True)
            row.prop(item, 'width')
            row.prop(item, 'height')
        else:
            box.prop(item, 'scale_percent')


def _draw_render(layout, scene, settings):
    box = layout.box()
    box.label(text='5 · Render', icon='RENDER_STILL')
    box.prop(settings, 'output_folder')
    box.prop(settings, 'filename_template')
    row = box.row(align=True)
    row.prop(settings, 'transparency')
    row.prop(settings, 'skip_existing')
    box.prop(settings, 'force_show_target')
    box.prop(settings, 'write_manifest')
    box.prop(settings, 'error_policy')
    try:
        total = count_render_tasks(scene, settings)
    except Exception:
        total = 0
    box.label(text='{} render task(s)'.format(total))
    if settings.render_running:
        if hasattr(box, 'progress'):
            box.progress(factor=settings.render_current / max(settings.render_total, 1), text='{} / {}'.format(settings.render_current, settings.render_total))
        else:
            box.label(text='{} / {}'.format(settings.render_current, settings.render_total))
        row = box.row(align=True)
        row.operator('color_prime.pause_render', text='Resume' if settings.render_paused else 'Pause', icon='PLAY' if settings.render_paused else 'PAUSE')
        row.operator('color_prime.stop_render', icon='CANCEL')
    else:
        row = box.row(align=True)
        row.scale_y = 1.2
        op = row.operator('color_prime.render_variations', text='Render All', icon='RENDER_ANIMATION')
        op.resume = False
        op = row.operator('color_prime.render_variations', text='Resume', icon='PLAY')
        op.resume = True
    if settings.last_error:
        box.label(text=settings.last_error, icon='ERROR')


def draw_full(layout, context):
    scene = context.scene
    settings = scene.color_prime
    row = layout.row(align=True)
    row.label(text='Color Prime 3', icon='COLOR')
    row.label(text=settings.addon_version)
    layout.label(text=settings.last_setup_summary)
    if hasattr(scene, 'color_prime_props'):
        box = layout.box()
        box.label(text='Color Prime v1 data detected', icon='INFO')
        box.operator('color_prime.import_legacy', icon='IMPORT')
    _draw_autopilot(layout, settings)
    _draw_reference(layout, settings)
    _draw_colors(layout, settings)
    _draw_advanced(layout, settings)
    _draw_resolutions(layout, settings)
    _draw_render(layout, scene, settings)
    diagnostics = layout.box()
    head = diagnostics.row(align=True)
    head.label(text='Diagnostics', icon='CHECKMARK')
    head.operator('color_prime.run_diagnostics', text='Run', icon='FILE_REFRESH')
    head.operator('color_prime.export_diagnostics', text='', icon='EXPORT')
    diagnostics.template_list('COLORPRIME_UL_diagnostics', 'diagnostics', settings, 'diagnostics', settings, 'diagnostic_index', rows=4)


class COLORPRIME_PT_view3d(bpy.types.Panel):
    bl_label = 'Color Prime'
    bl_idname = 'COLORPRIME_PT_view3d'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Color Prime'

    @classmethod
    def poll(cls,context): return context.scene.color_prime.studio.ui_mode=='EXPERT'

    def draw(self, context):
        draw_full(self.layout, context)


class COLORPRIME_PT_output(bpy.types.Panel):
    bl_label = 'Color Prime 3'
    bl_idname = 'COLORPRIME_PT_output'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'output'

    @classmethod
    def poll(cls,context): return context.scene.color_prime.studio.ui_mode=='EXPERT'

    def draw(self, context):
        settings = context.scene.color_prime
        layout = self.layout
        layout.label(text=settings.last_setup_summary)
        op = layout.operator('color_prime.auto_setup', text='Auto Setup Everything', icon='MODIFIER')
        op.redetect_icons = not bool(settings.icon_sets)
        layout.prop(settings, 'reference_image')
        row = layout.row(align=True)
        row.operator('color_prime.reference_load', text='Load Reference', icon='FILE_FOLDER')
        row.operator('color_prime.reference_analyze', text='Analyze', icon='VIEWZOOM')
        layout.prop(settings, 'output_folder')
        row = layout.row(align=True)
        row.operator('color_prime.preview', icon='HIDE_OFF')
        row.operator('color_prime.restore_preview', icon='LOOP_BACK')
        if settings.render_running:
            layout.label(text='Rendering {} / {}'.format(settings.render_current, settings.render_total))
            row = layout.row(align=True)
            row.operator('color_prime.pause_render')
            row.operator('color_prime.stop_render')
        else:
            row = layout.row(align=True)
            op = row.operator('color_prime.render_variations', text='Render All', icon='RENDER_ANIMATION')
            op.resume = False
            op = row.operator('color_prime.render_variations', text='Resume', icon='PLAY')
            op.resume = True
        layout.label(text='Full controls: 3D View → N → Color Prime', icon='INFO')


class COLORPRIME_PT_shader(bpy.types.Panel):
    bl_label = 'Color Prime Target'
    bl_idname = 'COLORPRIME_PT_shader'
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Color Prime'

    @classmethod
    def poll(cls, context):
        space = getattr(context, 'space_data', None)
        return bool(space and getattr(space, 'tree_type', '') == 'ShaderNodeTree')

    def draw(self, context):
        layout = self.layout
        obj = getattr(context, 'object', None)
        material = getattr(obj, 'active_material', None) if obj else None
        if material is None:
            layout.label(text='No active material', icon='INFO')
            return
        layout.label(text=material.name, icon='MATERIAL')
        layout.label(text='Select a root material node with an unlinked color value.')
        row = layout.row(align=True)
        for family, label in (('MAIN', 'Main'), ('ACCENT', 'Accent'), ('FIXED', 'Fixed')):
            operator = row.operator('color_prime.mark_active_node_target', text=label)
            operator.family = family


CLASSES = (
    COLORPRIME_UL_colors,
    COLORPRIME_UL_reference_pairs,
    COLORPRIME_UL_icons,
    COLORPRIME_UL_bindings,
    COLORPRIME_UL_resolutions,
    COLORPRIME_UL_diagnostics,
    COLORPRIME_PT_shader,
)

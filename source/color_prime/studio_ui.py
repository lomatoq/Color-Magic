"""One full workspace in both Output Properties and the N sidebar."""
import textwrap
import bpy
from .utils import active_item
from .ui import _palette, _draw_icon_sets, _draw_materials, _draw_resolutions


def message(layout, text, width=49, icon=None):
    for i, line in enumerate(textwrap.wrap(str(text), width) or ['']):
        if i == 0 and icon:
            layout.label(text=line, icon=icon)
        else:
            layout.label(text=line)


class COLORPRIME_UL_looks(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'enabled', text='')
        row.prop(item, 'name', text='', emboss=False)
        row.prop(item, 'main_color', text='')
        row.prop(item, 'accent_color', text='')
        if item.image and item.rendered_signature:
            row.label(text='%.0f' % (item.score * 100) if item.score >= 0 else 'Rendered', icon='IMAGE_DATA')
        else:
            row.label(text='New' if not item.image else 'Stale')


def looks(layout, context, s):
    st = s.studio
    ref = layout.box()
    ref.label(text='Reference image (optional)', icon='IMAGE_DATA')
    row = ref.row(align=True)
    row.prop(s, 'reference_image', text='')
    row.operator('color_prime.reference_load', text='Load', icon='FILE_FOLDER')
    row = ref.row(align=True)
    row.operator('color_prime.reference_analyze', text='Extract Pairs', icon='VIEWZOOM')
    row.operator('color_prime.generate_looks', text='Generate Looks', icon='FILE_REFRESH')
    if s.reference_image:
        message(ref, s.reference_last_summary)
    row = layout.row(align=True)
    row.scale_y = 1.4
    row.operator('color_prime.generate_looks', text='Generate', icon='COLOR')
    op = row.operator('color_prime.studio_render', text='Render Comparison', icon='RENDER_STILL')
    op.mode = 'LOOKS'
    if st.looks:
        layout.template_list('COLORPRIME_UL_looks', 'studio_looks', st, 'looks', st, 'look_index', rows=min(6, len(st.looks)))
        look = active_item(st.looks, st.look_index)
        if look:
            box = layout.box()
            box.prop(look, 'name', text='Look')
            row = box.row(align=True)
            row.prop(look, 'main_color', text='Main')
            row.prop(look, 'accent_color', text='Accent')
            message(box, look.reason)
            row = box.row(align=True)
            row.operator('color_prime.select_look', text='Preview This Look', icon='HIDE_OFF')
            row.operator('color_prime.restore_preview', text='Restore Colors', icon='LOOP_BACK')
            if look.image:
                message(box, 'Rendered snapshot. Re-render after editing camera, lights, parts or colors.')
                try:
                    p = look.image.preview_ensure()
                    if p.icon_id:
                        box.template_icon(icon_value=p.icon_id, scale=9.0)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            if look.score >= 0:
                message(box, 'Readability: %.0f/100 — heuristic, not a quality certificate.' % (look.score * 100))
        row = layout.row(align=True)
        row.operator('color_prime.open_sheet', text='Open Comparison Sheet', icon='IMAGE_DATA')
        row.operator('color_prime.bake_look', text='Keep Look in Scene', icon='CHECKMARK')
    elif not s.reference_pairs:
        message(layout, 'Select your icon Empty, click Safe Auto Setup, then compare real renders.')
    options = layout.box()
    options.prop(st, 'background', text='Target UI background')
    row = options.row(align=True)
    row.prop(st, 'preview_size')
    row.prop(st, 'preview_samples')
    options.prop(st, 'refine_best')
    options.prop(st, 'generate_inverted')
    layout.prop(st, 'show_expert', text='Individual Palettes & Reference Settings')
    if st.show_expert:
        _palette(layout, s, 'MAIN'); _palette(layout, s, 'ACCENT')
        row = layout.row(align=True)
        row.operator('color_prime.preview', text='Preview Palette')
        row.operator('color_prime.generate_looks', text='Rebuild Looks from Palettes')
        box = layout.box()
        box.prop(s, 'reference_clean_strength')
        box.prop(s, 'reference_background_mode')
        box.prop(st, 'reference_spatial_background')
        box.prop(st, 'reference_background_tolerance')
        box.prop(st, 'reference_roi')
        box.prop(s, 'reference_input_encoding')
        box.prop(s, 'reference_buffer_alpha')
        box.prop(st, 'ray_grid')


def parts(layout, context, s):
    box = layout.box()
    box.label(text='Keep a material unchanged', icon='LOCKED')
    box.prop(s, 'fixed_name_suffix', text='Name ends with')
    message(box, 'Examples: Gold! or Glass!.001. Gold without ! is a normal material.')
    row = layout.row(align=True)
    for family, title in (('MAIN', 'Selected → Main'), ('ACCENT', 'Selected → Accent'), ('FIXED', 'Selected → Fixed')):
        op = row.operator('color_prime.assign_selected', text=title)
        op.family = family
    message(layout, 'Corrections affect selected mesh objects inside enabled icons; shared materials are isolated and locked.')
    _draw_icon_sets(layout, s)
    _draw_materials(layout, s)
    box = layout.box()
    box.prop(s, 'main_parent_material')
    box.prop(s, 'accent_parent_material')
    box.operator('color_prime.sync_parent_references', text='Capture Relationships from Parents')
    box.operator('color_prime.swap_families', text='Swap Main / Accent Assignments')
    message(box, 'Same shader does not imply the same color family. Ambiguous graphs need an explicit target.')


def export(layout, context, s):
    st = s.studio
    layout.prop(st, 'batch_from_looks')
    if not st.batch_from_looks:
        layout.prop(s, 'combination_mode')
        _palette(layout, s, 'MAIN'); _palette(layout, s, 'ACCENT')
    _draw_resolutions(layout, s)
    box = layout.box()
    box.prop(s, 'output_folder')
    box.prop(s, 'filename_template')
    box.prop(s, 'transparency')
    box.prop(s, 'force_show_target')
    box.prop(st, 'overwrite_outputs')
    box.prop(s, 'error_policy')
    message(box, 'Outputs use the scene camera, engine and color management. Existing files are protected by default.')
    try:
        from .rendering import count_render_tasks
        box.label(text='{} planned renders'.format(count_render_tasks(context.scene, s)))
    except Exception as exc:
        message(box, str(exc), icon='ERROR')
    box.operator('color_prime.studio_preflight', text='Check Before Export', icon='CHECKMARK')
    row = layout.row(align=True)
    row.scale_y = 1.5
    op = row.operator('color_prime.studio_render', text='Render All', icon='RENDER_ANIMATION'); op.mode = 'EXPORT'
    op = row.operator('color_prime.studio_render', text='Verified Resume', icon='PLAY'); op.mode = 'EXPORT'; op.resume = True
    message(layout, 'Resume requires an unchanged saved .blend and matching output hashes. No silent index-based skipping.')
    if st.last_manifest:
        layout.prop(st, 'last_manifest', text='Manifest')
    box = layout.box()
    row=box.row(align=True)
    row.operator('color_prime.selftest', text='Core Test', icon='CHECKMARK')
    op=row.operator('color_prime.selftest', text='Real Render Test', icon='RENDER_STILL');op.render_test=True
    message(box, st.selftest_status)
    message(box, 'Core checks data safety. Real Render Test also renders a small scratch scene with CPU Cycles. Your scene is not used as test input.')


def draw_workspace(layout, context):
    s = getattr(context.scene, 'color_prime', None)
    if s is None:
        return
    from .appearance_workspace import draw
    return draw(layout,context,s)


class COLORPRIME_PT_studio_view3d(bpy.types.Panel):
    bl_label = 'Color Prime Studio'; bl_idname = 'COLORPRIME_PT_studio_view3d'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'Color Prime'
    def draw(self, context): draw_workspace(self.layout, context)


class COLORPRIME_PT_studio_output(bpy.types.Panel):
    bl_label = 'Color Prime Studio'; bl_idname = 'COLORPRIME_PT_studio_output'
    bl_space_type = 'PROPERTIES'; bl_region_type = 'WINDOW'; bl_context = 'output'
    def draw(self, context): draw_workspace(self.layout, context)


class COLORPRIME_PT_studio_image(bpy.types.Panel):
    bl_label = 'Color Prime Studio'; bl_idname = 'COLORPRIME_PT_studio_image'
    bl_space_type = 'IMAGE_EDITOR'; bl_region_type = 'UI'; bl_category = 'Color Prime'
    def draw(self, context): draw_workspace(self.layout, context)


CLASSES = (COLORPRIME_UL_looks, COLORPRIME_PT_studio_view3d, COLORPRIME_PT_studio_output, COLORPRIME_PT_studio_image)

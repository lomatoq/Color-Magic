"""Small, explicit actions for the three-step workflow; no implicit rendering."""
from pathlib import Path
import uuid
import bpy
from bpy.props import EnumProperty, StringProperty
from .guided_state import prepared, selection_roots, mesh_descendants, tr
from .studio_ops import idle


def fail(operator, context, exc):
    s = context.scene.color_prime
    s.last_error = str(exc)
    operator.report({'ERROR'}, str(exc))
    return {'CANCELLED'}


def _report_ok(operator, context, english, belarusian):
    s = context.scene.color_prime
    s.last_error = ''
    s.studio.guide_notice = tr(s, english, belarusian)
    operator.report({'INFO'}, s.studio.guide_notice)


def discard_selection_collection(settings):
    st = settings.studio
    coll = st.guide_selection_collection
    st.guide_selection_collection = None
    if coll is not None and coll.get('cp_guided_scope', False) and coll.users == 0:
        # It was an unlinked logical set, never an author's collection. Removing
        # the collection does NOT remove its member objects from their scenes.
        bpy.data.collections.remove(coll)


class COLORPRIME_OT_guide_step(bpy.types.Operator):
    bl_idname = 'color_prime.guide_step'
    bl_label = 'Open Step'
    step: EnumProperty(items=(('MODEL','Model',''),('COLORS','Colors',''),('EXPORT','Export','')), default='MODEL')
    @classmethod
    def poll(cls, context): return idle(context)
    def execute(self, context):
        s = context.scene.color_prime
        if self.step != 'MODEL' and not prepared(s):
            return fail(self, context, tr(s, 'Prepare the model in step 1 first.', 'Спачатку падрыхтуй мадэль у кроку 1.'))
        if self.step == 'EXPORT' and not s.studio.looks:
            return fail(self, context, tr(s, 'Add a color pair in step 2 first.', 'Спачатку дадай пару колераў у кроку 2.'))
        s.studio.guide_step = self.step
        s.last_error = ''
        return {'FINISHED'}


class COLORPRIME_OT_guide_prepare(bpy.types.Operator):
    bl_idname = 'color_prime.guide_prepare'
    bl_label = 'Prepare My Model'
    bl_description = 'Prepare only the displayed scope, keep original meshes/materials, then open Colors. No render starts.'
    bl_options = {'REGISTER', 'UNDO'}
    @classmethod
    def poll(cls, context): return idle(context) and getattr(context, 'mode', 'OBJECT') == 'OBJECT'
    def execute(self, context):
        from . import transaction
        from .discovery import add_icon_root, detect_icon_sets
        from .studio_ops import run_auto
        s = context.scene.color_prime
        st = s.studio
        if st.stage_status != 'NONE':
            return fail(self, context, tr(s, 'A safe setup is already active. Continue to Colors, or undo the setup first.',
                                          'Бяспечнае наладжванне ўжо актыўнае. Пераходзь да колераў або спачатку скасуй яго.'))
        before = None
        scope_collection = None
        try:
            before = transaction.snapshot_settings(s)
            if st.guide_scope == 'SELECTED':
                roots = selection_roots(context.selected_objects)
                if not roots:
                    raise ValueError(tr(s, 'Select an Empty containing meshes, or select the meshes of one icon.',
                                          'Выберы Empty з мадэллю або Mesh-аб’екты адной іконкі.'))
                s.icon_sets.clear()
                loose = []
                for root in roots:
                    if root.type == 'EMPTY':
                        add_icon_root(s, obj=root)
                    else:
                        loose.append(root)
                if len(loose) == 1:
                    add_icon_root(s, obj=loose[0])
                elif loose:
                    scope_collection = bpy.data.collections.new('CP Icon — Selection')
                    scope_collection['cp_guided_scope'] = True
                    # Logical set only: do not reparent or relink scene geometry.
                    seen = set()
                    for root in loose:
                        for obj in mesh_descendants(root):
                            key = obj.as_pointer()
                            if key not in seen:
                                scope_collection.objects.link(obj)
                                seen.add(key)
                    add_icon_root(s, collection=scope_collection, name='Selected Icon')
                    st.guide_selection_collection = scope_collection
            elif st.guide_scope == 'AUTO':
                from .model_library import discover
                discover(context.scene,s)
            if not any(i.enabled for i in s.icon_sets):
                raise ValueError(tr(s, 'No icons found. Select your model and choose Selection.',
                                      'Іконкі не знойдзеныя. Выберы мадэль і рэжым Selection.'))
            result = run_auto(self, context, before_settings=before)
            if 'FINISHED' not in result:
                if scope_collection and st.stage_status == 'NONE':
                    discard_selection_collection(s)
                return result
            st.guide_step = 'COLORS'
            st.guide_export_success = False
            st.guide_help = True
            _report_ok(self, context, 'Ready. Change Main / Accent, or load a reference image.',
                       'Гатова. Мяняй Main / Accent або загрузі карцінку-рэф.')
            from .studio_rig import show_view
            show_view(context,s)
            return {'FINISHED'}
        except Exception as exc:
            if st.stage_status == 'NONE' and before is not None:
                old_suppress = s.suppress_callbacks
                s.suppress_callbacks = True
                try: transaction.restore_settings(s, before)
                finally: s.suppress_callbacks = old_suppress
                if scope_collection: discard_selection_collection(s)
            return fail(self, context, exc)


class COLORPRIME_OT_guide_reference(bpy.types.Operator):
    bl_idname = 'color_prime.guide_reference'
    bl_label = 'Use Colors from Picture'
    bl_description = 'Load/analyze one image, generate suggestions, and preview the first suggestion. Edited looks are retained.'
    bl_options = {'REGISTER', 'UNDO'}
    source: EnumProperty(items=(('FILE','Load image',''),('SELECTED','Selected image',''),('CURRENT','Current reference','')), default='FILE')
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.png;*.jpg;*.jpeg;*.webp;*.tif;*.tiff;*.bmp;*.exr;*.hdr', options={'HIDDEN'})
    @classmethod
    def poll(cls, context): return idle(context)
    def invoke(self, context, event):
        if self.source == 'FILE':
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}
        return self.execute(context)
    def execute(self, context):
        from .operators import reference_image_from_context
        from .reference_workflow import analyze_reference_image
        from .lookbook import generate, preview
        from .guide_runtime import cancel_pending
        s = context.scene.color_prime
        image = None
        try:
            if self.source == 'FILE':
                if not self.filepath or not Path(self.filepath).is_file():
                    raise ValueError(tr(s, 'Choose an existing image file.', 'Выберы файл карцінкі.'))
                image = bpy.data.images.load(self.filepath, check_existing=True)
            elif self.source == 'SELECTED':
                image = reference_image_from_context(context)
            else:
                image = s.reference_image
            if image is None:
                raise ValueError(tr(s, 'Select an Image Reference object, or use Load Picture.',
                                      'Выберы Image Reference у сцэне або націсні «Загрузіць карцінку».'))
            # Reference decoding fails before rebuilding looks, so existing looks
            # and the model are not thrown away when an unsupported image fails.
            analyze_reference_image(image, s)
            s.reference_image = image
            cancel_pending()
            generate(s)
            if prepared(s):
                preview(context.scene, s, 0)
                s.studio.guide_step = 'COLORS'
            _report_ok(self, context, 'Picture analyzed. Select a suggestion; edited looks are kept.',
                       'Колеры знойдзеныя. Выберы варыянт; твае адрэдагаваныя пары захаваныя.')
            return {'FINISHED'}
        except Exception as exc:
            return fail(self, context, exc)


class COLORPRIME_OT_guide_look(bpy.types.Operator):
    bl_idname = 'color_prime.guide_look'
    bl_label = 'Manage Color Pair'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('COPY','Copy pair',''),('REMOVE','Remove pair',''),('SWAP','Swap palette colors','')), default='COPY')
    @classmethod
    def poll(cls, context): return idle(context) and bool(context.scene.color_prime.studio.looks)
    def execute(self, context):
        from .guide_runtime import cancel_pending, request
        from .lookbook import selected
        s = context.scene.color_prime
        st = s.studio
        look = selected(s)
        if look is None: return {'CANCELLED'}
        cancel_pending()
        old = s.suppress_callbacks
        s.suppress_callbacks = True
        try:
            if self.action == 'COPY':
                name, main, accent = look.name, tuple(look.main_color), tuple(look.accent_color)
                item = st.looks.add()
                item.uid = uuid.uuid4().hex
                item.name = name + ' Copy'
                item.main_color, item.accent_color = main, accent
                item.variant = 'CUSTOM'
                item.user_edited = True
                item.reason = 'Your editable color pair'
                st.look_index = len(st.looks) - 1
            elif self.action == 'REMOVE':
                if len(st.looks) <= 1:
                    raise ValueError(tr(s, 'Keep at least one pair; edit its two color swatches.',
                                          'Пакінь хаця б адну пару; яе колеры можна змяніць.'))
                st.looks.remove(st.look_index)
                st.look_index = min(st.look_index, len(st.looks) - 1)
            else:
                main, accent = tuple(look.main_color), tuple(look.accent_color)
                look.main_color, look.accent_color = accent, main
                look.user_edited = True
            st.guide_export_success = False
            s.last_error = ''
        except Exception as exc:
            return fail(self, context, exc)
        finally:
            s.suppress_callbacks = old
        request(context.scene)
        return {'FINISHED'}


class COLORPRIME_OT_guide_message(bpy.types.Operator):
    bl_idname = 'color_prime.guide_message'
    bl_label = 'Color Prime Help'
    action: EnumProperty(items=(('HELP','Help',''),('CLEAR','Dismiss error','')), default='HELP')
    def execute(self, context):
        s = context.scene.color_prime
        if self.action == 'CLEAR': s.last_error = ''
        else:
            s.studio.guide_help = True
            s.studio.ui_mode = 'GUIDED'
        return {'FINISHED'}


class COLORPRIME_OT_guide_back(bpy.types.Operator):
    bl_idname = 'color_prime.guide_back'
    bl_label = 'Back to Model'
    bl_description = 'Return from the comparison to the previous editor, with Color Prime visible'
    @classmethod
    def poll(cls, context): return getattr(context, 'area', None) is not None
    def execute(self, context):
        st = context.scene.color_prime.studio
        kind = st.comparison_return_space
        if kind not in {'VIEW_3D', 'PROPERTIES'}: kind = 'VIEW_3D'
        context.area.type = kind
        space = context.area.spaces.active
        if kind == 'VIEW_3D':
            space.show_region_ui = True
        elif kind == 'PROPERTIES':
            space.context = 'OUTPUT'
        return {'FINISHED'}


class COLORPRIME_OT_guide_open_folder(bpy.types.Operator):
    bl_idname = 'color_prime.guide_open_folder'
    bl_label = 'Open Export Folder'
    def execute(self, context):
        s = context.scene.color_prime
        path = Path(bpy.path.abspath(s.studio.guide_export_folder or s.output_folder))
        if not path.is_dir():
            return fail(self, context, tr(s, 'The folder has not been created yet. Export first.',
                                          'Папка яшчэ не створаная. Спачатку запусці экспарт.'))
        bpy.ops.wm.path_open(filepath=str(path))
        return {'FINISHED'}


class COLORPRIME_OT_guide_support(bpy.types.Operator):
    bl_idname = 'color_prime.guide_support'
    bl_label = 'Copy Diagnostic Report'
    bl_description = 'Copy version, import health and the last error to the clipboard; no network request or scene upload'
    def execute(self, context):
        import json
        import platform
        import sys
        from . import compat
        from .constants import VERSION_STRING
        s=context.scene.color_prime
        report={
            'color_prime':VERSION_STRING,
            'blender':bpy.app.version_string,
            'python':sys.version.split()[0],
            'platform':platform.system(),
            'compat_source':str(getattr(compat,'__file__','')),
            'compositor_tree_available':callable(getattr(compat,'compositor_tree',None)),
            'stage':s.studio.stage_status,
            'icons':sum(bool(i.enabled) for i in s.icon_sets),
            'looks':len(s.studio.looks),
            'last_error':s.last_error,
            'selftest':s.studio.selftest_status,
        }
        context.window_manager.clipboard=json.dumps(report,ensure_ascii=False,indent=2)
        self.report({'INFO'},tr(s,'Diagnostic report copied. No files were uploaded.',
                               'Справаздача скапіяваная. Файлы нікуды не адпраўляліся.'))
        return {'FINISHED'}


CLASSES = (COLORPRIME_OT_guide_step, COLORPRIME_OT_guide_prepare, COLORPRIME_OT_guide_reference,
           COLORPRIME_OT_guide_look, COLORPRIME_OT_guide_message, COLORPRIME_OT_guide_back,
           COLORPRIME_OT_guide_open_folder, COLORPRIME_OT_guide_support)

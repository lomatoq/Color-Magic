"""Three-step native Blender UI. Drawing is read-only: never creates or renders.

Full power remains available through Advanced. The guide explains the next
actual action instead of requiring a separate README or a click-through tour.
"""
import textwrap
import bpy
from .guided_state import (tr, prepared, valid_step, selection_roots, selection_counts,
                           material_counts, export_count, export_dimensions)
from .utils import active_item


def lines(layout, text, context=None, icon=None):
    if context and getattr(context.scene,'color_prime',None):
        from .workspace_locale import translate
        text=translate(text,context.scene.color_prime)
    region = getattr(context, 'region', None)
    width = getattr(region, 'width', 360)
    # Conservative wrapping in Blender UI units; no assumption of a wide sidebar.
    dpi = 1.0
    try: dpi = context.preferences.system.ui_scale
    except (AttributeError, TypeError): pass
    chars = max(22, min(72, int(width / max(1., dpi) / 7.8) - 6))
    for index, line in enumerate(textwrap.wrap(str(text), chars) or ['']):
        if icon and index == 0: layout.label(text=line, icon=icon)
        else: layout.label(text=line)


def button(layout, identifier, text, icon='NONE', enabled=True, large=False):
    row = layout.row()
    row.enabled = enabled
    if large: row.scale_y = 1.6
    return row.operator(identifier, text=text, icon=icon)


def help_box(layout, context, s, title, description):
    if not s.studio.guide_help: return
    box = layout.box()
    lines(box, title, context, 'INFO')
    lines(box, description, context)


def _camera(layout, context, s):
    from .studio_rig import is_active,missing_parts,framed_for_model,current_model
    st=s.studio
    box=layout.box()
    active=is_active(s)
    target=current_model(s)
    if target:lines(box,'Модель: '+target.name,context,'OUTLINER_OB_MESH')
    if active:
        lines(box,tr(s,'Studio camera + 3 softboxes','Студыя: камера + 3 софтбоксы'),context,'LIGHT_AREA')
        if not framed_for_model(s):
            lines(box,'Студия сцены ещё не настроена под эту модель.',context,'INFO')
        box.prop(st,'rig_preset',text=tr(s,'Angle','Ракурс'))
        box.prop(st,'rig_margin',text=tr(s,'Margin','Палі кадра'))
        box.prop(st,'rig_auto_frame',text=tr(s,'Fit model to frame before each render','Подгонять модель под кадр перед каждым рендером'))
        button(box,'color_prime.studio_setup',tr(s,'Update Angle / Fit','Абнавіць ракурс / кадр') if framed_for_model(s) else 'Настроить камеру и свет под эту модель','CAMERA_DATA')
        button(box,'color_prime.studio_remove',tr(s,'Restore Original Camera / Lights','Вярнуць ранейшую камеру / святло'),'LOOP_BACK')
    else:
        if st.rig_id:
            lines(box,'Студия удалена или неполная: '+', '.join(missing_parts(s))+'.',context,'INFO')
        lines(box,tr(s,'Your camera / lights are kept unless you ask for a studio.','Твая камера і святло не мяняюцца без гэтай кнопкі.'),context)
        box.prop(st,'rig_preset',text=tr(s,'Studio angle','Ракурс студыі'))
        button(box,'color_prime.studio_setup','Восстановить камеру и студийный свет' if st.rig_id else tr(s,'Create Studio Camera + Lights','Стварыць камеру і студыйнае святло'),'LIGHT_AREA',large=True)
        if st.rig_id:button(box,'color_prime.studio_remove','Отменить студию / вернуть настройки сцены','LOOP_BACK')
        box.prop(context.scene,'camera',text=tr(s,'Camera','Камера'))
        if context.scene.camera is None:
            lines(box,tr(s,'No camera yet. The button creates and frames it; color preview needs no camera.','Камеры няма. Кнопка створыць яе і змесціць мадэль у кадр; колеры можна правяраць і без камеры.'),context,'INFO')
    if st.rig_preset=='CUSTOM':
        row=box.row(align=True);row.prop(st,'rig_yaw');row.prop(st,'rig_elevation')
    box.prop(st,'rig_auto_frame',text=tr(s,'Fit model before each render','Падганяць мадэль перад кожным рэндэрам'))
    box.prop(st,'rig_margin',text=tr(s,'Frame margin','Палі кадра'),slider=True)
    box.prop(st,'rig_lighting')
    row=box.row(align=True);row.prop(st,'rig_intensity');row.prop(st,'rig_softness')
    row=box.row(align=True)
    for field in ('rig_key_color','rig_fill_color','rig_rim_color'):row.prop(st,field,text='')
    box.prop(st,'rig_backdrop')
    if st.rig_backdrop:
        box.prop(st,'rig_background_mode')
        if st.rig_background_mode=='MATERIAL':box.prop(st,'rig_background_material')
        elif st.rig_background_mode=='COLOR':box.prop(st,'rig_background_color')
        box.label(text='Плоскость видна и при прозрачном PNG.')
    if active:button(box,'color_prime.studio_setup','Применить настройки студии','CHECKMARK')
    return context.scene.camera is not None


def model(layout, context, s):
    st = s.studio
    ready = prepared(s)
    help_box(layout, context, s,
        tr(s, '1 · Choose what to color', '1 · Што афарбоўваем?'),
        tr(s, 'Select the icon Empty, or the mesh parts of one icon. Main is the base color; Accent is the detail color.',
              'Выберы Empty іконкі або яе Mesh-часткі. Main — асноўны колер; Accent — колер дэталяў.'))
    if st.stage_status == 'RECOVERY':
        box = layout.box(); box.alert = True
        lines(box, tr(s, 'The previous setup needs recovery. Do not run another setup over it.',
                     'Папярэдняе наладжванне патрабуе аднаўлення. Не запускай новае паверх яго.'), context, 'ERROR')
        lines(box, st.stage_note, context)
        op = button(box, 'color_prime.stage_action', tr(s, 'Restore Original Model', 'Вярнуць арыгінальную мадэль'), 'LOOP_BACK', large=True)
        op.action = 'REVERT'
        return
    if ready:
        count = sum(i.enabled for i in s.icon_sets)
        lines(layout, tr(s, '{} icon(s) are prepared.'.format(count), 'Падрыхтавана іконак: {}.'.format(count)), context, 'CHECKMARK')
        op = button(layout, 'color_prime.guide_step', tr(s, 'Continue to Colors', 'Перайсці да колераў'), 'FORWARD', large=True)
        op.step = 'COLORS'
        if st.stage_status == 'STAGED':
            lines(layout, tr(s, 'Your original meshes and materials are retained. No need to accept anything before exporting.',
                         'Арыгінальныя Mesh і матэрыялы захаваныя. Для экспарту не трэба нічога асобна прымаць.'), context)
            op = button(layout, 'color_prime.stage_action', tr(s, 'Undo Setup', 'Скасаваць наладжванне'), 'LOOP_BACK')
            op.action = 'REVERT'
        return
    layout.prop(st, 'guide_scope', text=tr(s, 'Source', 'Крыніца'))
    eligible = True
    if st.guide_scope == 'SELECTED':
        roots = selection_roots(getattr(context, 'selected_objects', ()))
        icons, meshes = selection_counts(roots)
        eligible = bool(meshes)
        if roots:
            lines(layout, tr(s, 'Selection: {} icon(s), {} mesh part(s).'.format(icons, meshes),
                         'Выбрана: {} іконак, {} Mesh-частак.'.format(icons, meshes)), context, 'OUTLINER_OB_MESH')
            lines(layout, ', '.join(o.name for o in roots[:4]), context)
        else:
            lines(layout, tr(s, 'Nothing to color selected. Click your Empty or mesh in the viewport / Outliner.',
                         'Нічога не выбрана. Клікні Empty або Mesh у сцэне / Outliner.'), context, 'RESTRICT_SELECT_OFF')
    elif st.guide_scope == 'SAVED':
        eligible = any(i.enabled for i in s.icon_sets)
        lines(layout, tr(s, '{} enabled icon(s) in your saved list.'.format(sum(i.enabled for i in s.icon_sets)),
                     'У захаваным спісе ўключана іконак: {}.'.format(sum(i.enabled for i in s.icon_sets))), context)
    else:
        lines(layout, tr(s, 'Find icon roots in this scene, then prepare the detected sets. Review the model after setup.',
                     'Знайсці іконкі ў гэтай сцэне і падрыхтаваць іх. Пасля наладжвання правер мадэль.'), context)
    object_mode = getattr(context, 'mode', 'OBJECT') == 'OBJECT'
    if not object_mode:
        lines(layout, tr(s, 'Switch to Object Mode first (Tab).', 'Спачатку перайдзі ў Object Mode (Tab).'), context, 'ERROR')
    options=layout.box()
    options.prop(st,'create_studio_on_prepare',text=tr(s,'Also create a studio camera + lights','Адразу стварыць камеру і студыйнае святло'))
    if st.create_studio_on_prepare:
        options.prop(st,'rig_preset',text=tr(s,'Angle','Ракурс'))
        lines(options,tr(s,'Softboxes, auto-framing and transparent PNG setup. Existing setup is retained for restore.','Софтбоксы, аўтакадр і празрысты фон. Ранейшыя камера і святло захоўваюцца для адкату.'),context)
    options.prop(st,'surface_regions',text=tr(s,'One solid mesh','Суцэльны mesh'))
    lines(options,tr(s,'Suggest material zones from real shape boundaries. No vertex movement or cutting.','Зоны матэрыялаў паводле межаў формы. Без разразання або перамяшчэння вяршынь.'),context)
    button(layout, 'color_prime.guide_prepare', tr(s, 'Prepare My Model', 'Падрыхтаваць мадэль'), 'MODIFIER', eligible and object_mode, True)
    lines(layout, tr(s, 'Missing materials are created; shared colors can be split safely. Material names ending with ! stay unchanged.',
                 'Аддон створыць адсутныя матэрыялы і бяспечна падзеліць агульныя. Матэрыялы з ! у канцы назвы не мяняюцца.'), context)
    layout.separator()
    button(layout, 'color_prime.create_demo', tr(s, 'Try on a Separate Demo Scene', 'Паспрабаваць на асобнай дэмасцэне'), 'SCENE_DATA')


def colors(layout, context, s):
    st = s.studio
    help_box(layout, context, s,
        tr(s, '2 · Pick the colors you like', '2 · Выберы колеры'),
        tr(s, 'Click the two swatches, or load a picture. Selecting a pair updates the model; no render starts automatically.',
              'Націсні на каляровыя палі або загрузі карцінку. Выбар пары абнаўляе мадэль, але не запускае рэндэр.'))
    ref = layout.box()
    button(ref, 'color_prime.guide_reference', tr(s, 'Get Colors from a Picture', 'Узяць колеры з карцінкі'), 'FILE_FOLDER').source = 'FILE'
    if s.reference_image:
        lines(ref, s.reference_image.name, context, 'IMAGE_DATA')
        # Always available for an image dragged into Blender, without requiring an Image Editor.
    button(ref, 'color_prime.guide_reference', tr(s, 'Use Selected Image Reference', 'Узяць выбраны Image Reference'), 'EYEDROPPER').source = 'SELECTED'
    look = active_item(st.looks, st.look_index)
    if look is None:
        lines(layout, tr(s, 'No pairs yet. Generate suggestions or load a reference.',
                     'Пакуль няма пар. Ствары варыянты або загрузі рэф.'), context)
        button(layout, 'color_prime.generate_looks', tr(s, 'Generate Color Pairs', 'Стварыць пары колераў'), 'COLOR', large=True)
        return
    box = layout.box()
    box.prop(look, 'name', text=tr(s, 'Pair', 'Пара'))
    row = box.row(align=True); row.scale_y = 1.4
    row.prop(look, 'main_color', text='Main')
    row.prop(look, 'accent_color', text='Accent')
    box.prop(st, 'live_preview', text=tr(s, 'Update the model while editing', 'Абнаўляць мадэль пры змене колераў'))
    if not st.live_preview or not s.preview_active:
        button(box, 'color_prime.select_look', tr(s, 'Show These Colors', 'Паказаць гэтыя колеры'), 'HIDE_OFF')
    row = box.row(align=True)
    row.operator('color_prime.guide_look', text=tr(s, 'Copy Pair', 'Капіяваць'), icon='DUPLICATE').action='COPY'
    row.operator('color_prime.guide_look', text=tr(s, 'Swap Colors', 'Памяняць колеры'), icon='ARROW_LEFTRIGHT').action='SWAP'
    layout.template_list('COLORPRIME_UL_looks', 'guided_looks', st, 'looks', st, 'look_index', rows=min(5,max(2,len(st.looks))))
    lines(layout, tr(s, 'Tick pairs to include them in an all-pairs export.', 'Птушачкі адзначаюць пары для экспарту ўсіх варыянтаў.'), context)
    button(layout, 'color_prime.guide_look', tr(s, 'Remove Selected Pair', 'Выдаліць выбраную пару'), 'REMOVE', len(st.looks)>1).action='REMOVE'
    if st.region_note:
        lines(layout,tr(s,'Surface analysis: ','Аналіз паверхні: ')+st.region_note,context,'MESH_DATA')
    counts = material_counts(s)
    layout.operator('color_prime.create_children',text=tr(s,'Create Main / Accent Children…','Стварыць нашчадкаў Main / Accent…'),icon='DUPLICATE')
    lines(layout,tr(s,'Assign children in the Child Materials panel below. Existing zones and separate meshes are supported.',
                    'Прызначай нашчадкаў у панэлі Child Materials ніжэй. Падтрымліваюцца існыя зоны і асобныя mesh.'),context)
    lines(layout, 'Main: {}   Accent: {}   Fixed: {}'.format(counts['MAIN'], counts['ACCENT'], counts['FIXED']), context, 'MATERIAL')
    if counts['REVIEW']:
        lines(layout, tr(s, '{} assignment(s) need a visual check. Automatic roles are suggestions.'.format(counts['REVIEW']),
                     'Правер размеркаванне {} матэрыялаў: аўтаматычныя ролі — гэта прапанова.'.format(counts['REVIEW'])), context, 'INFO')
    layout.prop(st, 'guide_parts_open', text=tr(s, 'Wrong parts are colored?', 'Афарбаваныя не тыя часткі?'))
    if st.guide_parts_open:
        box = layout.box()
        button(box, 'color_prime.swap_families', tr(s, 'Swap Main / Accent Parts', 'Памяняць ролі Main / Accent'), 'ARROW_LEFTRIGHT')
        lines(box, tr(s, 'For one correction, select a mesh in the model and choose its role:',
                     'Каб паправіць адну частку, выберы яе Mesh і націсні ролю:'), context)
        for role, en, be in [('MAIN','Selected = Main','Выбранае = Main'),('ACCENT','Selected = Accent','Выбранае = Accent'),('FIXED','Keep Selected Unchanged','Не мяняць выбранае')]:
            op=button(box,'color_prime.assign_selected',tr(s,en,be),enabled=st.stage_status=='STAGED' and bool(getattr(context,'selected_objects',())))
            op.family=role
        if st.stage_status != 'STAGED':
            lines(box, tr(s, 'Parts were already committed. Use Advanced > Safe Auto Setup to start another reversible edit.',
                         'Часткі ўжо прынятыя. У Advanced запусці Safe Auto Setup для новага зваротнага рэдагавання.'), context)
        lines(box, tr(s, 'Gold! / Glass!.001 always stay protected; names without ! have no special meaning.',
                     'Gold! / Glass!.001 заўсёды абароненыя; назвы без ! не маюць асаблівага значэння.'), context)
    from .surface_adapter import region_count
    obj=getattr(context,'active_object',None)
    zones=region_count(obj)
    if obj is not None and getattr(obj,'type','')=='MESH' and st.stage_status=='STAGED':
        regionbox=layout.box()
        if zones>1:
            lines(regionbox,tr(s,'One mesh · {} material zones'.format(zones),'Адзін mesh · {} зон матэрыялаў'.format(zones)),context)
            regionbox.prop(st,'region_index',text=tr(s,'Zone number','Нумар зоны'))
            button(regionbox,'color_prime.show_region',tr(s,'Highlight This Zone','Паказаць гэтую зону'),'FACESEL',st.region_index<=zones)
            row=regionbox.row(align=True)
            for role in ('MAIN','ACCENT','FIXED'):
                op=button(row,'color_prime.region_role',role.title(),enabled=getattr(context,'mode','OBJECT')=='OBJECT' and st.region_index<=zones)
                op.family=role;op.source='REGION'
        lines(regionbox,tr(s,'Manual: Tab → face select (3) → select faces → choose Main / Accent / Fixed below.','Уручную: Tab → рэжым граняў (3) → выберы грані → націсні Main / Accent / Fixed ніжэй.'),context)
        row=regionbox.row(align=True)
        for role in ('MAIN','ACCENT','FIXED'):
            op=button(row,'color_prime.region_role',tr(s,'Faces → '+role.title(),'Грані → '+role.title()),enabled=getattr(context,'mode','OBJECT')=='EDIT_MESH')
            op.family=role;op.source='FACES'
    camera_ready = _camera(layout, context, s)
    renderbox = layout.box()
    op=button(renderbox,'color_prime.studio_render',tr(s,'Compare Real Renders (Optional)','Параўнаць рэндэры (неабавязкова)'), 'RENDER_STILL',camera_ready)
    op.mode='LOOKS'
    lines(renderbox,tr(s,'Uses the current camera and lights, including the optional Studio rig.',
                       'Выкарыстоўвае актыўную камеру і святло, у тым ліку створаную студыю.'),context)
    if st.sheet:
        button(renderbox,'color_prime.open_sheet',tr(s,'Open Rendered Comparison','Адкрыць параўнанне'),'IMAGE_DATA')
    if look.image:
        try:
            preview=look.image.preview_ensure()
            if preview.icon_id:renderbox.template_icon(icon_value=preview.icon_id,scale=7.0)
        except (AttributeError,RuntimeError,TypeError):pass
        if not look.rendered_signature:
            lines(renderbox,tr(s,'Colors changed: this thumbnail is from an earlier render.',
                               'Колеры змяніліся: гэта мініяцюра папярэдняга рэндэру.'),context,'INFO')
    op=button(layout,'color_prime.guide_step',tr(s,'Next: Export PNGs','Далей: экспарт PNG'),'FORWARD',large=True)
    op.step='EXPORT'
    button(layout,'color_prime.restore_preview',tr(s,'Show Original Colors','Паказаць зыходныя колеры'),'LOOP_BACK')
    lines(layout,tr(s,'Preview is temporary. To save the appearance in the .blend, use Keep This Look in step 3.',
                   'Preview часовы. Каб захаваць афарбоўку ў .blend, у кроку 3 націсні «Замацаваць афарбоўку». '),context)


def export(layout, context, s):
    st=s.studio
    help_box(layout,context,s,tr(s,'3 · Save your images','3 · Захавай выявы'),
             tr(s,'Choose a folder and size. The next dialog shows the exact image count before rendering starts.',
                  'Выберы папку і памер. Перад рэндэрам убачыш дакладную колькасць выяў і пацвердзіш запуск.'))
    layout.prop(s,'output_folder',text=tr(s,'Save to','Папка'))
    layout.prop(st,'guide_export_selection',text=tr(s,'Export','Варыянты'))
    layout.prop(st,'guide_export_size',text=tr(s,'Size','Памер'))
    layout.prop(st,'guide_transparent',text=tr(s,'Transparent background','Празрысты фон'))
    camera_ready=_camera(layout,context,s)
    width,height=export_dimensions(context.scene,st.guide_export_size)
    count=export_count(s)
    lines(layout,tr(s,'{} PNG(s) · {} x {} px'.format(count,width,height),
                   '{} PNG · {} x {} px'.format(count,width,height)),context,'OUTPUT')
    folder_ok=bool(str(s.output_folder or '').strip())
    if folder_ok and str(s.output_folder).startswith('//') and not bpy.data.filepath:
        folder_ok=False
        lines(layout,tr(s,'The .blend is unsaved. Choose an absolute output folder using the folder icon.',
                       'Файл .blend яшчэ не захаваны. Выберы папку праз значок папкі.'),context,'ERROR')
    if not folder_ok and not str(s.output_folder or '').strip():
        lines(layout,tr(s,'Choose an output folder first.','Спачатку выберы папку.'),context,'ERROR')
    if not count:
        lines(layout,tr(s,'No pairs enabled. Choose Current look, or tick pairs in step 2.',
                       'Няма ўключаных пар. Выберы Current look або адзнач пары ў кроку 2.'),context,'ERROR')
    op=button(layout,'color_prime.studio_render',tr(s,'Export {} PNG(s)…'.format(count),'Экспартаваць {} PNG…'.format(count)),
              'RENDER_ANIMATION',camera_ready and folder_ok and count>0,True)
    op.mode='EXPORT';op.guided=True
    lines(layout,tr(s,'No silent overwrite. Your expert resolution presets and palettes are not replaced by Simple mode.',
                   'Без ціхага перазапісу. Прэсэты памераў і палітры ў Advanced застаюцца нязменнымі.'),context)
    if st.overwrite_outputs:
        warning=layout.box();warning.alert=True
        lines(warning,tr(s,'WARNING: Replace Existing Files is enabled in Advanced.',
                         'УВАГА: у Advanced уключаны перазапіс існых файлаў.'),context,'ERROR')
    if st.guide_export_success:
        done=layout.box()
        lines(done,tr(s,'Last export: {} PNG(s). Scene restored.'.format(st.guide_export_count),
                      'Апошні экспарт: {} PNG. Сцэна адноўленая.'.format(st.guide_export_count)),context,'CHECKMARK')
        button(done,'color_prime.guide_open_folder',tr(s,'Open Output Folder','Адкрыць папку'),'FILE_FOLDER')
    layout.separator()
    lines(layout,tr(s,'Optional: keep this appearance in the .blend instead of a temporary preview.',
                   'Неабавязкова: замацаваць афарбоўку ў .blend замест часовага preview.'),context)
    button(layout,'color_prime.bake_look',tr(s,'Keep This Look in the Model','Замацаваць афарбоўку ў мадэлі'),'CHECKMARK')
    if st.stage_status=='STAGED':
        op=button(layout,'color_prime.stage_action',tr(s,'Undo Setup and Return Originals','Скасаваць наладжванне'),'LOOP_BACK')
        op.action='REVERT'


def draw_guide(layout, context, s):
    from .appearance_workspace import draw
    draw(layout,context,s)


class COLORPRIME_PT_comparison(bpy.types.Panel):
    bl_label='Color Prime · Comparison'
    bl_idname='COLORPRIME_PT_comparison'
    bl_space_type='IMAGE_EDITOR';bl_region_type='UI';bl_category='Color Prime'
    @classmethod
    def poll(cls,context):
        s=getattr(getattr(context,'scene',None),'color_prime',None)
        image=getattr(getattr(context,'space_data',None),'image',None)
        return bool(s and image and (image==s.studio.sheet or any(l.image==image for l in s.studio.looks)))
    def draw(self,context):
        s=context.scene.color_prime;layout=self.layout
        button(layout,'color_prime.guide_back',tr(s,'Back to Model','Вярнуцца да мадэлі'),'BACK',large=True)
        lines(layout,tr(s,'Rendered snapshots. Choose a pair, then return to the model.',
                       'Гэта здымкі рэндэраў. Выберы пару і вярніся да мадэлі.'),context)
        layout.template_list('COLORPRIME_UL_looks','comparison_looks',s.studio,'looks',s.studio,'look_index',rows=4)
        button(layout,'color_prime.select_look',tr(s,'Apply Selected Pair','Паказаць выбраную пару'),'CHECKMARK')


CLASSES=(COLORPRIME_PT_comparison,)

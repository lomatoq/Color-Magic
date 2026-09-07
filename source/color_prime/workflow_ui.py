"""One task per expandable block. UI drawing never analyzes or mutates a model."""
import bpy
from bpy.props import EnumProperty
from .guided_state import tr, prepared, selection_counts, export_count
from .guided_ui import lines, button
from .studio_ops import idle

BLOCKS=(('MODEL','1 · Model','1 · Мадэль'),('ZONES','2 · Zones','2 · Зоны'),
        ('COLORS','3 · Colors & materials','3 · Колеры і матэрыялы'),
        ('ASSIGN','4 · Assign','4 · Прызначыць'),('SAVE','5 · Save','5 · Захаваць'))


class COLORPRIME_OT_workflow_block(bpy.types.Operator):
    bl_idname='color_prime.workflow_block';bl_label='Open Block'
    block: EnumProperty(items=tuple((k,en,'') for k,en,be in BLOCKS))
    def execute(self,context):
        st=context.scene.color_prime.studio
        st.workflow_block=self.block;st.workflow_options=False
        return {'FINISHED'}


class COLORPRIME_OT_workflow_prepare(bpy.types.Operator):
    bl_idname='color_prime.workflow_prepare';bl_label='Prepare Model';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):return idle(context) and context.mode=='OBJECT'
    def execute(self,context):
        s=context.scene.color_prime;st=s.studio
        saved=(st.surface_regions,s.loose_part_mode,st.create_studio_on_prepare)
        try:
            # Region creation is an observable, separate step in this workflow.
            st.surface_regions='OFF';s.loose_part_mode='OFF';st.create_studio_on_prepare=False
            result=bpy.ops.color_prime.guide_prepare()
            if result=={'FINISHED'}:
                st.workflow_block='ZONES';st.workflow_analyzed=False;st.assignment_summary=''
            return result
        finally:
            st.surface_regions,s.loose_part_mode,st.create_studio_on_prepare=saved


class COLORPRIME_OT_workflow_regions(bpy.types.Operator):
    bl_idname='color_prime.workflow_regions';bl_label='Find Model Zones';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        from .guided_state import prepared
        return idle(context) and context.mode=='OBJECT' and context.scene.color_prime.studio.stage_status!='RECOVERY' and prepared(context.scene.color_prime)
    def execute(self,context):
        from .child_materials import assignment_objects,_refresh,_error
        from .surface_adapter import auto_regions,region_count
        from .runtime import restore_preview
        s=context.scene.color_prime;st=s.studio
        try:
            if st.stage_status=='NONE':
                from . import transaction
                before=transaction.snapshot_settings(s);st.reuse_family_library=True
                transaction.start(context.scene,s,before)
            scope=st.assignment_scope;st.assignment_scope='MODEL'
            try:objects=assignment_objects(context)
            finally:st.assignment_scope=scope
            if not objects:raise ValueError(tr(s,'No prepared meshes.','Няма падрыхтаваных mesh.'))
            restore_preview(context.scene)
            if s.preview_active:raise ValueError('Restore the previous preview first.')
            from .material_names import normalize_generated_names
            normalize_generated_names(s)
            st.region_note=''
            from .authored_setup import process_zones
            process_zones(context.scene,s,objects)
            if st.zones_assign_library and not st.adoption_pending and any(c.material and c.auto_assign for c in s.children):
                from .child_materials import auto_assign
                auto_assign(context.scene,s,objects,find_missing=False)
            _refresh(context)
            st.workflow_analyzed=True;s.last_error=''
            self.report({'INFO'},tr(s,'Zones ready. Select a zone number and highlight it.',
                'Зоны гатовыя. Выберы нумар і вылучы зону.'))
            return {'FINISHED'}
        except Exception as exc:return _error(self,context,exc)


class COLORPRIME_OT_workflow_object_mode(bpy.types.Operator):
    bl_idname='color_prime.workflow_object_mode';bl_label='Finish Inspecting Zones'
    @classmethod
    def poll(cls,context):return idle(context) and context.mode=='EDIT_MESH'
    def execute(self,context):
        bpy.ops.object.mode_set(mode='OBJECT')
        return {'FINISHED'}


class COLORPRIME_OT_material_names(bpy.types.Operator):
    bl_idname='color_prime.material_names';bl_label='Shorten Generated Material Names';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        return idle(context) and context.mode=='OBJECT' and context.scene.color_prime.studio.stage_status=='STAGED'
    def execute(self,context):
        from .runtime import restore_preview
        from .material_names import normalize_generated_names
        from .child_materials import _refresh,_error
        try:
            s=context.scene.color_prime;restore_preview(context.scene)
            count=normalize_generated_names(s);_refresh(context)
            s.last_error='';s.studio.assignment_summary=''
            self.report({'INFO'},tr(s,'Shortened {} generated names','Скарочана назваў: {}').format(count))
            return {'FINISHED'}
        except Exception as exc:return _error(self,context,exc)


def _next(layout,s,block):
    _,en,be=next(row for row in BLOCKS if row[0]==block)
    button(layout,'color_prime.workflow_block',tr(s,'Next: ','Далей: ')+tr(s,en[4:],be[4:]),'FORWARD').block=block


def _fold(layout,st,field,label):
    layout.prop(st,field,text=label,icon='TRIA_DOWN' if getattr(st,field) else 'TRIA_RIGHT',emboss=False)
    return getattr(st,field)


def _model(layout,context,s,objects):
    st=s.studio
    button(layout,'color_prime.workflow_block','Перекрасить только выбранные части','FACESEL').block='ASSIGN'
    from .model_library import draw_models
    draw_models(layout,context,s)
    from .family_links import ready
    if ready(s) and st.stage_status!='RECOVERY':
        button(layout,'color_prime.add_library_model',tr(s,'Add Selected Model + Find Zones','Дадаць выбраную мадэль + знайсці зоны'),'ADD',context.mode=='OBJECT',True)
        layout.prop(st,'zones_assign_library',text=tr(s,'Also assign library materials','Адразу прызначыць матэрыялы бібліятэкі'))
    if st.stage_status=='STAGED':
        lines(layout,tr(s,'Prepared meshes: {}','Падрыхтавана mesh: {}').format(len(objects)),context,'CHECKMARK')
        _next(layout,s,'ZONES')
        if _fold(layout,st,'workflow_options',tr(s,'Setup options','Налады падрыхтоўкі')):
            button(layout,'color_prime.stage_action',tr(s,'Return Original Model','Вярнуць зыходную мадэль'),'LOOP_BACK').action='REVERT'
        return
    if st.stage_status=='RECOVERY':
        lines(layout,tr(s,'Restore the interrupted setup first.','Спачатку аднаві перарванае наладжванне.'),context,'ERROR')
        button(layout,'color_prime.stage_action',tr(s,'Restore Model','Аднавіць мадэль'),'LOOP_BACK').action='REVERT'
        return
    lines(layout,tr(s,'Select the model in the viewport or Outliner.','Выберы мадэль у сцэне або Outliner.'),context)
    if not s.icon_sets:layout.prop(st,'guide_scope',text=tr(s,'Model source','Адкуль мадэль'))
    button(layout,'color_prime.workflow_prepare',tr(s,'Prepare Model','Падрыхтаваць мадэль'),'MODIFIER',context.mode=='OBJECT',True)


def _zones(layout,context,s,objects):
    from .surface_adapter import region_count
    st=s.studio;obj=context.active_object
    total=sum(region_count(o) for o in objects)
    layout.prop(st,'zone_policy',text='')
    if st.zone_policy=='REPLACE':
        layout.prop(st,'replace_zones');layout.prop(st,'replace_materials')
    lines(layout,tr(s,'Use existing zones; find missing boundaries by shape.',
        'Існыя зоны захоўваем; адсутныя шукаем па форме.'),context)
    button(layout,'color_prime.workflow_regions',tr(s,'Find Model Zones','Знайсці зоны мадэлі'),'MESH_DATA',context.mode=='OBJECT',True)
    if s.children:layout.prop(st,'zones_assign_library',text=tr(s,'Also assign library materials','Адразу прызначыць матэрыялы бібліятэкі'))
    lines(layout,tr(s,'{} zones across {} meshes','{} зон на {} mesh').format(total,len(objects)),context)
    count=region_count(obj) if obj in objects else 0
    if count:
        layout.prop(st,'region_index',text=tr(s,'Inspect zone','Паказаць зону'))
        button(layout,'color_prime.show_region',tr(s,'Highlight Zone','Вылучыць зону'),'FACESEL',st.region_index<=count)
    if context.mode=='EDIT_MESH':
        button(layout,'color_prime.workflow_object_mode',tr(s,'Finish Inspecting','Скончыць прагляд'),'CHECKMARK',large=True)
    if st.workflow_analyzed and not total:
        lines(layout,tr(s,'No reliable boundary: each whole mesh stays one part.',
            'Надзейных межаў няма: кожны mesh застаецца цэлай часткай.'),context)
    if _fold(layout,st,'workflow_options',tr(s,'Search settings & result','Налады і вынік пошуку')):
        layout.prop(st,'surface_regions',text=tr(s,'Search','Пошук'))
        layout.prop(st,'region_max',text=tr(s,'Zone limit','Ліміт зон'))
        if st.region_note:lines(layout,st.region_note,context)
    _next(layout,s,'COLORS')


def _colors(layout,context,s,objects):
    from .family_ui import draw_colors
    draw_colors(layout,context,s)
    _next(layout,s,'ASSIGN')


def _assign(layout,context,s,objects):
    from .selected_colors import draw
    draw(layout,context,s)
    if _fold(layout,s.studio,'workflow_options','Библиотека и автоматическое распределение'):
        _assign_advanced(layout,context,s,objects)
    if context.mode=='EDIT_MESH':button(layout,'color_prime.workflow_object_mode','Завершить выбор граней','CHECKMARK')
    _next(layout,s,'SAVE')


def _assign_advanced(layout,context,s,objects):
    from .child_materials import assignment_objects
    from .surface_adapter import region_count
    st=s.studio;obj=context.active_object
    count=sum(bool(c.material) and c.auto_assign for c in s.children)
    if count:
        layout.prop(st,'assignment_scope',text=tr(s,'Apply to','Куды'))
        lines(layout,tr(s,'{} meshes · {} checked variants','{} mesh · {} адзначаных варыянтаў').format(len(assignment_objects(context)),count),context)
        layout.prop(st,'match_similar_parts',text=tr(s,'Same shape → same variant','Падобная форма → адзін варыянт'))
        lines(layout,tr(s,'Copies, mirrored parts and scaled copies are grouped within Main / Accent.',
            'Копіі, люстраныя і маштабаваныя часткі групуюцца ўнутры Main / Accent.'),context)
        button(layout,'color_prime.auto_assign_children',tr(s,'Review Distribution…','Праверыць размеркаванне…'),'MATERIAL',context.mode=='OBJECT',True)
        if st.assignment_summary:lines(layout,st.assignment_summary,context,'CHECKMARK')
    else:
        lines(layout,tr(s,'Main / Accent already color the model. Extra materials are optional.',
            'Main / Accent ужо афарбоўваюць мадэль. Дадатковыя матэрыялы неабавязковыя.'),context)
        button(layout,'color_prime.apply_palettes',tr(s,'Apply Palette Colors','Ужыць колеры палітры'),'COLOR')
        button(layout,'color_prime.workflow_block',tr(s,'Create Material Variants','Стварыць варыянты матэрыялаў'),'DUPLICATE').block='COLORS'
    if _fold(layout,st,'workflow_options',tr(s,'Correct one part','Паправіць адну частку')):
        valid=obj in objects
        zones=region_count(obj) if valid else 0
        source='FACES' if context.mode=='EDIT_MESH' else 'REGION' if zones else 'OBJECT'
        lines(layout,tr(s,{'FACES':'Target: selected faces','REGION':'Target: zone on active mesh','OBJECT':'Target: selected meshes'}[source],
            {'FACES':'Мэта: выбраныя грані','REGION':'Мэта: зона актыўнага mesh','OBJECT':'Мэта: выбраныя mesh'}[source]),context)
        if source=='REGION':layout.prop(st,'region_index',text=tr(s,'Zone','Зона'))
        row=layout.row(align=True);row.enabled=valid
        for role in ('MAIN','ACCENT','FIXED'):
            op=row.operator('color_prime.assign_selected' if source=='OBJECT' else 'color_prime.region_role',text=role.title())
            op.family=role
            if source!='OBJECT':op.source=source
        if s.children:
            layout.template_list('COLORPRIME_UL_children','manual',s,'children',s,'child_index',rows=2)
            button(layout,'color_prime.assign_child',tr(s,'Assign Selected Variant','Прызначыць выбраны варыянт'),'MATERIAL',valid).source=source
    if context.mode=='EDIT_MESH':button(layout,'color_prime.workflow_object_mode',tr(s,'Finish Editing Faces','Скончыць выбар граняў'),'CHECKMARK')
    _next(layout,s,'SAVE')


def _save(layout,context,s,objects):
    st=s.studio
    button(layout,'color_prime.bake_look',tr(s,'Keep Look in Model','Замацаваць афарбоўку'),'CHECKMARK',large=True)
    lines(layout,tr(s,'Then save your .blend as usual (Ctrl+S).','Потым захавай .blend як звычайна (Ctrl+S).'),context)
    if _fold(layout,st,'workflow_camera',tr(s,'Camera & lighting','Камера і святло')):
        from .guided_ui import _camera
        _camera(layout,context,s)
    if _fold(layout,st,'workflow_options',tr(s,'Export PNG','Экспарт PNG')):
        from .export_ui import draw_export
        draw_export(layout,context,s)


def draw_workflow(layout,context,s):
    from .utils import all_icon_objects
    from .transaction import OWNER_KEY
    st=s.studio
    from .constants import VERSION_STRING
    row=layout.row(align=True);row.label(text=VERSION_STRING,icon='COLOR');row.prop(st,'guide_language',text='')
    if s.render_running:
        lines(layout,st.preview_progress if st.preview_running else st.export_progress,context,'RENDER_STILL')
        layout.operator('color_prime.stop_render',text=tr(s,'Stop after current','Спыніць пасля бягучага'))
        return
    if s.last_error:
        box=layout.box();box.alert=True;lines(box,s.last_error,context,'ERROR')
        button(box,'color_prime.guide_message',tr(s,'Dismiss','Схаваць'),'X').action='CLEAR'
    objects=[o for o in all_icon_objects(s,context.scene,True) if o.type=='MESH' and o.data.get(OWNER_KEY,'')==st.stage_id]
    drawers={'MODEL':_model,'ZONES':_zones,'COLORS':_colors,'ASSIGN':_assign,'SAVE':_save}
    for key,en,be in BLOCKS:
        box=layout.box();opened=st.workflow_block==key
        op=box.operator('color_prime.workflow_block',text=tr(s,en,be),icon='TRIA_DOWN' if opened else 'TRIA_RIGHT',emboss=False)
        op.block=key
        if opened:
            if key not in {'MODEL','ASSIGN'} and not prepared(s):
                lines(box,tr(s,'Prepare the model in block 1 first.','Спачатку падрыхтуй мадэль у блоку 1.'),context)
            else:drawers[key](box,context,s,objects)
    if _fold(layout,st,'workflow_tools',tr(s,'Settings & diagnostics','Налады і дыягностыка')):
        layout.prop(st,'ui_mode',text=tr(s,'Interface','Інтэрфейс'))
        button(layout,'color_prime.selftest',tr(s,'Check Add-on','Праверыць аддон'),'CHECKMARK',context.mode=='OBJECT').render_test=True
        if st.selftest_status!='Not run in this Blender':lines(layout,st.selftest_status,context)
        button(layout,'color_prime.guide_support',tr(s,'Copy Error Report','Скапіяваць справаздачу'),'COPYDOWN')


CLASSES=(COLORPRIME_OT_workflow_block,COLORPRIME_OT_workflow_prepare,COLORPRIME_OT_workflow_regions,
         COLORPRIME_OT_workflow_object_mode,COLORPRIME_OT_material_names)

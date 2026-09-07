"""Complete palette/size export controls shared by the compact workflow."""
import bpy
from bpy.props import IntProperty,FloatProperty
from .guided_state import tr,guided_pairs,guided_resolutions,export_dimensions,export_count
from .guided_ui import lines,button
from .studio_ops import idle


class COLORPRIME_OT_scale_toggle(bpy.types.Operator):
    bl_idname='color_prime.scale_toggle';bl_label='Include Export Scale';bl_options={'REGISTER','UNDO'}
    percent:IntProperty(default=100,min=1,max=1600)
    @classmethod
    def poll(cls,context):return idle(context)
    def execute(self,context):
        from .operators import ensure_resolution_default
        s=context.scene.color_prime;ensure_resolution_default(s)
        entries=[(i,r) for i,r in enumerate(s.resolutions) if r.mode=='SCALE' and r.scale_percent==self.percent]
        if entries:
            enabled=not any(r.enabled for i,r in entries)
            for i,r in entries:r.enabled=False
            i,r=entries[0];r.enabled=enabled;s.resolution_index=i
        else:
            r=s.resolutions.add();r.name='{:g}x'.format(self.percent/100.);r.mode='SCALE';r.scale_percent=self.percent
            s.resolution_index=len(s.resolutions)-1
        return {'FINISHED'}


class COLORPRIME_OT_scale_custom(bpy.types.Operator):
    bl_idname='color_prime.scale_custom';bl_label='Add Custom Scale';bl_options={'REGISTER','UNDO'}
    factor:FloatProperty(name='Scale x',default=2.5,min=.01,max=16.,precision=2)
    @classmethod
    def poll(cls,context):return idle(context)
    def invoke(self,context,event):return context.window_manager.invoke_props_dialog(self,width=320)
    def draw(self,context):
        self.layout.prop(self,'factor')
        s=context.scene.color_prime;base=export_dimensions(context.scene,s.studio.guide_export_size,s.studio)
        self.layout.label(text='{} × {} px'.format(*(max(1,round(v*round(self.factor*100)/100.)) for v in base)))
    def execute(self,context):
        from .operators import ensure_resolution_default
        s=context.scene.color_prime;ensure_resolution_default(s);percent=round(self.factor*100)
        r=next((r for r in s.resolutions if r.mode=='SCALE' and r.scale_percent==percent),None)
        if r is None:r=s.resolutions.add();r.name='{:g}x'.format(percent/100.);r.mode='SCALE';r.scale_percent=percent
        r.enabled=True;s.resolution_index=next(i for i,item in enumerate(s.resolutions) if item==r)
        return {'FINISHED'}


class COLORPRIME_UL_export_sizes(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index=0):
        row=layout.row(align=True);row.prop(item,'enabled',text='')
        row.label(text='{:g}×'.format(item.scale_percent/100.) if item.mode=='SCALE' else item.name)
        try:
            res=guided_resolutions(context.scene,context.scene.color_prime,include_disabled=True)[index]
            row.label(text='{} × {} px'.format(res.width,res.height))
        except ValueError:row.label(text='Size exceeds 65536 px',icon='ERROR')


def draw_export(layout,context,s):
    st=s.studio
    layout.prop(s,'output_folder',text=tr(s,'Folder','Папка'))
    layout.prop(st,'guide_export_selection',text=tr(s,'Colors','Колеры'))
    if st.guide_export_selection=='PALETTES':
        layout.prop(s,'combination_mode',text=tr(s,'Combinations','Спалучэнні'))
        from .palette_sets import export_colors
        main=len(export_colors(s,'MAIN'));accent=len(export_colors(s,'ACCENT'))
        lines(layout,tr(s,'Checked: {} Main × {} Accent','Адзначана: {} Main × {} Accent').format(main,accent),context)
        lines(layout,tr(s,'Edit colors in the palette block above.','Рэдагуй колеры ў блоку палітры вышэй.'),context)
    layout.separator()
    layout.prop(st,'guide_export_size',text=tr(s,'Base size (1×)','Базавы памер (1×)'))
    if st.guide_export_size=='CUSTOM':
        row=layout.row(align=True);row.prop(st,'guide_export_width',text='W');row.prop(st,'guide_export_height',text='H')
    base=export_dimensions(context.scene,st.guide_export_size,st)
    lines(layout,'1× = {} × {} px'.format(*base),context)
    row=layout.row(align=True)
    for percent in (100,200,300):
        selected=any(r.enabled and r.mode=='SCALE' and r.scale_percent==percent for r in s.resolutions) or (not s.resolutions and percent==100)
        row.operator('color_prime.scale_toggle',text='{}×'.format(percent//100),depress=selected,
            icon='CHECKBOX_HLT' if selected else 'CHECKBOX_DEHLT').percent=percent
    row.operator('color_prime.scale_custom',text=tr(s,'Custom…','Свой…'),icon='ADD')
    layout.operator('color_prime.resolution_add',text='Добавить размер',icon='ADD')
    if s.resolutions:
        row=layout.row()
        row.template_list('COLORPRIME_UL_export_sizes','sizes',s,'resolutions',s,'resolution_index',rows=min(5,max(2,len(s.resolutions))))
        buttons=row.column(align=True)
        for action,icon in (('REMOVE','REMOVE'),('UP','TRIA_UP'),('DOWN','TRIA_DOWN')):
            buttons.operator('color_prime.resolution_action',text='',icon=icon).action=action
        if 0<=s.resolution_index<len(s.resolutions):
            res=s.resolutions[s.resolution_index]
            layout.prop(res,'mode',text=tr(s,'Size type','Тып памеру'))
            row=layout.row(align=True)
            if res.mode=='SCALE':row.prop(res,'scale_factor',text=tr(s,'Selected scale ×','Выбраны маштаб ×'))
            else:row.prop(res,'width',text='W');row.prop(res,'height',text='H')
    layout.prop(st,'guide_transparent',text=tr(s,'Transparent PNG','Празрысты PNG'))
    layout.prop(st,'overwrite_outputs',text=tr(s,'Replace existing files','Замяніць існыя файлы'))
    try:
        pairs=guided_pairs(s);sizes=guided_resolutions(context.scene,s)
        from .batch_export import make_guided_tasks
        tasks=make_guided_tasks(context.scene,s);count=len(tasks)
        icons=len({(t['spec']['kind'],t['spec']['root']) for t in tasks})
        lines(layout,tr(s,'{} models × {} pairs × {} sizes = {} PNG','{} мадэляў × {} пар × {} памераў = {} PNG').format(icons,len(pairs),len(sizes),count),context)
        if pairs and sizes:
            from .batch_export import make_guided_tasks
            from pathlib import Path
            task=tasks[0]
            root=Path(bpy.path.abspath(s.output_folder)).resolve()
            lines(layout,tr(s,'Example path:','Прыклад шляху:'),context)
            for depth,part in enumerate(task['target'].relative_to(root).parts):
                lines(layout,'  '*depth+part,context)
        if not count:lines(layout,tr(s,'Check colors, models and at least one size.','Адзнач колеры, мадэлі і хаця б адзін памер.'),context,'ERROR')
    except ValueError as exc:
        count=0;lines(layout,str(exc),context,'ERROR')
    op=button(layout,'color_prime.studio_render',tr(s,'Render {} PNG…','Рэндэрыць {} PNG…').format(count),
        'RENDER_STILL',bool(context.scene.camera) and bool(s.output_folder) and count>0,True)
    op.mode='EXPORT';op.guided=True
    if not context.scene.camera:lines(layout,tr(s,'Add a camera in Camera & lighting.','Дадай камеру ў блоку «Камера і святло».'),context)
    if st.guide_export_success:
        lines(layout,tr(s,'Saved {} PNG','Захавана {} PNG').format(st.guide_export_count),context,'CHECKMARK')
        button(layout,'color_prime.guide_open_folder',tr(s,'Open Output Folder','Адкрыць папку'),'FILE_FOLDER')


CLASSES=(COLORPRIME_OT_scale_toggle,COLORPRIME_OT_scale_custom,COLORPRIME_UL_export_sizes)

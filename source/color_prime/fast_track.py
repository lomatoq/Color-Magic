"""Three bounded routes built from the same tested workspace operations."""
import bpy
from bpy.props import EnumProperty
from .guided_state import tr

def run(context,route):
    from .appearance_workspace import meshes,source_model,capture,apply
    from .workspace_actions import prepare,find_zones
    s=context.scene.color_prime
    source=source_model(s) if route=='TRANSFER' else None
    items=[i for i in s.icon_sets if i.enabled and i!=source and meshes(i,context.scene)]
    if not items:raise ValueError(tr(s,'Check at least one target model.','Адзнач хаця б адну мадэль-атрымальніка.'))
    if route=='TRANSFER':
        if source is None or not meshes(source,context.scene):raise ValueError(tr(s,'Choose a source model.','Выберы мадэль-крыніцу.'))
        p=capture(s,source,reuse=True);apply(context,p,items,True)
        s.appearance_mode='MODEL';s.appearance_materials=True
    elif route=='BATCH':
        for item in items:prepare(context,item)
        s.appearance_mode='PALETTE';s.studio.guide_export_selection='CURRENT';s.studio.rig_auto_frame=True
        from .studio_rig import create
        create(context.scene,s);s.appearance_export=True
    elif route=='IMPORT':
        policy=s.studio.zone_policy;s.studio.zone_policy='PRESERVE'
        try:
            for item in items:prepare(context,item);find_zones(context,item)
        finally:s.studio.zone_policy=policy
    else:raise ValueError('Unknown route')
    s.last_error=''
    s.appearance_note=tr(s,'Fast Track complete: {} model(s). Nothing was rendered.','Fast Track завершаны: {} мадэляў. Рэндэр не запускаўся.').format(len(items))
    return len(items)

class COLORPRIME_OT_fast_track(bpy.types.Operator):
    bl_idname='color_prime.fast_track';bl_label='Fast Track';bl_options={'REGISTER','UNDO'}
    route:EnumProperty(items=(('BATCH','Batch',''),('TRANSFER','Transfer',''),('IMPORT','Import','')))
    @classmethod
    def poll(cls,context):
        from .studio_ops import idle
        return idle(context) and context.mode=='OBJECT' and context.scene.color_prime.studio.stage_status!='RECOVERY'
    def execute(self,context):
        try:run(context,self.route);return {'FINISHED'}
        except Exception as exc:context.scene.color_prime.last_error=str(exc);self.report({'ERROR'},str(exc));return {'CANCELLED'}

def draw(layout,context,s):
    from .guided_ui import lines,button
    from .appearance_workspace import meshes
    lines(layout,tr(s,'Choose a route. Only checked models are processed; rendering is always a separate step.','Выберы маршрут. Апрацоўваюцца толькі адзначаныя мадэлі; рэндэр заўсёды асобны крок.'),context)
    layout.prop(s,'fast_route',text='')
    button(layout,'color_prime.models',tr(s,'Find models','Знайсці мадэлі'),'VIEWZOOM').action='FIND'
    for item in s.icon_sets:
        box=layout.box();box.prop(item,'enabled',text=item.name)
        lines(box,tr(s,'{} meshes','{} мешаў').format(len(meshes(item,context.scene))),context)
    route=s.fast_route
    if route=='TRANSFER':layout.prop_search(s,'appearance_source',s,'icon_sets',text=tr(s,'Copy from','Скапіяваць з'))
    details={
      'BATCH':('Prepare collections and fit each icon to the same frame occupancy. Mesh scales stay unchanged. Export current appearance at the selected sizes.','Падрыхтаваць калекцыі і аднолькавае запаўненне кадра. Маштаб мешаў не мяняецца. Экспарт бягучага выгляду ў выбраных памерах.'),
      'TRANSFER':('Copy the source colors and surfaces to checked recipients. Existing zones and the source model are preserved.','Скапіяваць колеры і паверхні крыніцы на адзначаных атрымальнікаў. Існыя зоны і мадэль-крыніца захоўваюцца.'),
      'IMPORT':('Create a collection/root and find missing zones. Preserve existing materials; choose colors afterward in Workspace.','Стварыць калекцыю/корань і знайсці адсутныя зоны. Захаваць існыя матэрыялы; колеры выбіраюцца пазней у працоўнай вобласці.')}
    lines(layout,tr(s,*details[route]),context)
    targets=[i for i in s.icon_sets if i.enabled and meshes(i,context.scene) and (route!='TRANSFER' or i.name!=s.appearance_source)]
    ready=bool(targets) and (route!='TRANSFER' or bool(s.appearance_source))
    button(layout,'color_prime.fast_track',tr(s,'Prepare batch','Падрыхтаваць серыю') if route=='BATCH' else tr(s,'Transfer appearance','Перанесці выгляд') if route=='TRANSFER' else tr(s,'Find model zones','Знайсці зоны мадэлі'),'CHECKMARK',ready,True).route=route
    if not ready:lines(layout,tr(s,'Check targets; for transfer, choose a different source model.','Адзнач атрымальнікаў; для пераносу выберы асобную мадэль-крыніцу.'),context,'INFO')
    if s.appearance_note.startswith('Fast Track'):lines(layout,s.appearance_note,context,'INFO')
    if route=='BATCH':
        from .studio_rig import is_active
        if is_active(s) and s.studio.guide_export_selection=='CURRENT':
            from .export_ui import draw_export
            draw_export(layout,context,s)
        else:lines(layout,tr(s,'Prepare batch first. Folder, sizes and the Render button will appear here.','Спачатку падрыхтуй серыю. Тут з’явяцца папка, памеры і кнопка рэндэру.'),context,'INFO')
    lines(layout,tr(s,'Use Workspace for detailed colors, materials and manual corrections. Ctrl+Z undoes preparation.','Працоўная вобласць — для колераў, матэрыялаў і ручных выпраўленняў. Ctrl+Z адмяняе падрыхтоўку.'),context)

CLASSES=(COLORPRIME_OT_fast_track,)

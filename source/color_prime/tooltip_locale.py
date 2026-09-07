"""Translate owned RNA metadata, not merely the drawn labels.

Definitions retain their callbacks and storage; no values are copied/reset.
Blender RNA metadata is global, so the currently edited scene selects its language.
"""
import bpy
FIELDS={
 'appearance_materials':('Transfer surfaces','Перанесці паверхні','Copy surface variants on first transfer; later color edits preserve assignments.','Скапіяваць паверхні пры першым пераносе; пазнейшыя змены колеру захоўваюць прызначэнні.'),
 'workspace_auto_zones':('Find missing zones','Знайсці адсутныя зоны','Find missing boundaries on Apply. Preserve existing manual assignments.','Знайсці адсутныя межы пры ўжыванні. Захаваць існыя ручныя прызначэнні.'),
 'appearance_source':('Source model','Мадэль-крыніца','Choose the model whose colors and surfaces will be copied. It is excluded from recipients.','Выбраць мадэль, чые колеры і паверхні будуць скапіяваныя. Яна выключаецца з атрымальнікаў.'),
 'variants_count':('Variant count','Колькасць экземпляраў','Number of new library materials to create; creation does not assign them to zones.','Колькасць новых матэрыялаў бібліятэкі; стварэнне не прызначае іх зонам.'),
 'variants_style':('Surface set','Набор паверхняў','Varied creates matte, glossy, metallic, light and dark surfaces; neutral gives an editable base.','Розны набор стварае матавыя, глянцавыя, металічныя, светлыя і цёмныя паверхні; нейтральны дае аснову для рэдагавання.'),
 'variants_family':('Parent family','Бацькоўскае сямейства','New variants inherit only the Main or Accent color; their surface settings remain independent.','Новыя экземпляры наследуюць колер Main або Accent; налады паверхняў незалежныя.'),
 'auto_assign':('Include in Autoassign','Уключыць у аўтапрызначэнне','Use this variant when distributing checked materials across model zones.','Выкарыстоўваць экземпляр пры размеркаванні адзначаных матэрыялаў па зонах.'),
 'enabled':('Include','Уключыць','Include this entry in the operation or export. Visibility is controlled separately by the eye.','Уключыць элемент у аперацыю або экспарт. Бачнасць асобна кіруецца вокам.'),
 'color':('Color','Колер','Edit this palette color. The color picker also accepts HEX. Preview changes only when preview is active.','Змяніць колер палітры. Можна ўвесці HEX. Прагляд змяняецца толькі калі ён уключаны.'),
 'name':('Name','Назва','Editable name, preserved in the blend file and used in export names where applicable.','Рэдагуемая назва, захоўваецца ў blend і выкарыстоўваецца ў назвах экспарту.'),
 'region_index':('Zone number','Нумар зоны','Number of the active mesh zone to inspect or assign. Other zones stay unchanged.','Нумар зоны актыўнага меша для прагляду або прызначэння. Астатнія зоны не мяняюцца.'),
 'zone_policy':('Zone policy','Правіла разметкі','Keep authored zones by default. Rebuild changes only checked models and the selected replacement options.','Па змаўчанні захоўваць ручныя зоны. Перабудова змяняе толькі адзначаныя мадэлі і выбраныя параметры замены.'),
 'replace_zones':('Rebuild zones','Перабудаваць зоны','Replace zone boundaries on checked working models. Use Undo to revert.','Замяніць межы зон адзначаных працоўных мадэляў. Адмена вяртае змены.'),
 'replace_materials':('Replace materials','Замяніць матэрыялы','Allow replacement of material assignments when rebuilding selected models.','Дазволіць замену прызначэнняў матэрыялаў пры перабудове выбраных мадэляў.'),
 'match_similar_parts':('Match similar parts','Супаставіць падобныя дэталі','Keep copies, mirrored and scaled matching parts on the same material variant.','Прызначаць аднолькавы экземпляр копіям, люстраным і маштабаваным падобным дэталям.'),
 'output_folder':('Output folder','Папка экспарту','Destination for model/color/size folders. An unsaved blend needs an absolute path.','Месца папак мадэляў, колераў і памераў. Незахаванаму blend патрэбны абсалютны шлях.'),
 'overwrite_outputs':('Replace existing files','Замяніць існыя файлы','Allow replacement at the exact export paths. Disabled protects existing files.','Дазволіць замену файлаў па дакладных шляхах экспарту. Выключэнне абараняе існыя файлы.'),
 'guide_transparent':('Transparent PNG','Празрысты PNG','Save background alpha. An enabled background plane remains visible.','Захаваць празрыстасць фону. Уключаная плоскасць фону застаецца бачнай.'),
 'guide_export_selection':('Export colors','Колеры экспарту','Palette mode renders selected combinations. Current appearance renders each model without changing its colors.','Палітры рэндэраць выбраныя спалучэнні. Бягучы выгляд рэндэрыць кожную мадэль без змены колераў.'),
 'combination_mode':('Color combinations','Спалучэнні колераў','Render every Main × Accent pair, or pair colors by order.','Рэндэрыць усе пары Main × Accent або пары паводле парадку.'),
 'rig_auto_frame':('Fit before each render','Падганяць перад рэндэрам','Fit each model to the camera using the same frame margin and image aspect ratio. Does not scale mesh geometry.','Падганяць кожную мадэль да камеры з аднолькавымі палямі і прапорцыямі выявы. Геаметрыя не маштабуецца.'),
 'rig_margin':('Frame margin','Палі кадра','Empty space around the model: 0 to 45 percent on each side.','Вольнае месца вакол мадэлі: ад 0 да 45 працэнтаў з кожнага боку.'),
 'rig_intensity':('Light intensity','Інтэнсіўнасць святла','Light power multiplier; 1 uses the preset power. Apply studio settings to update.','Множнік магутнасці святла; 1 выкарыстоўвае прэсэт. Ужыві налады студыі для абнаўлення.'),
 'rig_softness':('Light softness','Мяккасць святла','Increase light size for softer shadows. Apply studio settings to update.','Павялічыць памер святла для мякчэйшых ценяў. Ужыві налады студыі для абнаўлення.'),
 'rig_backdrop':('Background plane','Плоскасць фону','Show a plane behind the model, oriented to the camera. Independent of PNG transparency.','Паказаць плоскасць за мадэллю, арыентаваную да камеры. Незалежна ад празрыстасці PNG.'),
 'guide_language':('Interface language','Мова інтэрфейсу','Change addon labels and tooltips. User material and model names are preserved.','Змяніць подпісы і падказкі адона. Назвы карыстальніцкіх матэрыялаў і мадэляў захоўваюцца.'),
 'appearance_mode':('Color source','Крыніца колеру','Use a saved palette or copy colors and surfaces from another scene model.','Выкарыстоўваць захаваную палітру або скапіяваць колеры і паверхні іншай мадэлі сцэны.'),
}
NAMES={
 'fast_route':('Fast Track route','Маршрут Fast Track'),
 'rig_preset':('Camera view','Ракурс камеры'),'rig_lighting':('Lighting preset','Прэсэт святла'),
 'rig_background_color':('Background color','Колер фону'),'rig_background_material':('Background material','Матэрыял фону'),
 'rig_background_mode':('Background source','Крыніца фону'),'rig_yaw':('Camera yaw','Паварот камеры'),'rig_elevation':('Camera elevation','Узвышэнне камеры'),
 'rig_key_color':('Key light color','Колер асноўнага святла'),'rig_fill_color':('Fill light color','Колер запаўнення'),'rig_rim_color':('Rim light color','Колер контравага святла'),
 'appearance_details':('Palette details','Падрабязнасці палітры'),'appearance_camera':('Camera and lights','Камера і святло'),
 'appearance_export':('Export settings','Налады экспарту'),'appearance_advanced':('Selective recoloring','Выбарачная перафарбоўка'),
 'appearance_palette_export':('Export workspace palettes','Экспартаваць палітры працоўнага экрана'),
 'variants_open':('Material library','Бібліятэка матэрыялаў'),'variants_details':('Tint and shader','Адценне і шэйдар'),
 'guide_export_size':('Base image size','Базавы памер выявы'),'guide_export_width':('Image width','Шырыня выявы'),
 'guide_export_height':('Image height','Вышыня выявы'),'width':('Width','Шырыня'),'height':('Height','Вышыня'),
 'scale_factor':('Export scale','Маштаб экспарту'),'factor':('Export scale','Маштаб экспарту'),
 'mode':('Mode','Рэжым'),'shader':('Shader type','Тып шэйдара'),'node_name':('Shader or group node','Нод шэйдара або групы'),
 'socket_name':('Color input','Уваход колеру'),'workspace_page':('Workspace page','Старонка інтэрфейсу'),
}
_definitions={};_language=None

def refresh(language='EN'):
    global _language
    if _language==language:return
    import color_prime
    be=language=='BE'
    for cls in color_prime._REGISTERED_CLASSES:
        if not issubclass(cls,(bpy.types.PropertyGroup,bpy.types.Operator)):continue
        for name,decl in getattr(cls,'__annotations__',{}).items():
            if name=='name':continue # Blender's inherited ID name metadata is not addon-owned.
            if name not in FIELDS and name not in NAMES:continue
            if not hasattr(decl,'keywords'):continue
            key=(cls,name);_definitions.setdefault(key,decl)
            original=_definitions[key];kw=dict(original.keywords)
            if name!='guide_language' and isinstance(kw.get('items'),(tuple,list)):
                from .workspace_locale import enum_choices
                choices=kw['items'];default=kw.get('default')
                if isinstance(default,str):kw['default']=next(i for i,item in enumerate(choices) if item[0]==default)
                kw['items']=enum_choices(choices)
            if name in FIELDS:
                row=FIELDS[name];kw['name']=row[bool(be)];kw['description']=row[2+bool(be)]
            else:
                label=NAMES[name][bool(be)];kw['name']=label
                kw['description']=('Змяніць наладу: '+label+'.') if be else ('Change '+label.lower()+'.')
            # Registering metadata keeps the same storage key, type and callbacks.
            setattr(cls,name,original.function(**kw))
    _language=language

def language_changed(self,context):
    language=self.guide_language
    def apply():
        refresh(language)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:area.tag_redraw()
    bpy.app.timers.register(apply,first_interval=0.01)

def sync_language():
    s=getattr(getattr(bpy.context,'scene',None),'color_prime',None)
    if s and not s.render_running:refresh(s.studio.guide_language)
    return .5

def clear():
    global _language
    _language=None;_definitions.clear()

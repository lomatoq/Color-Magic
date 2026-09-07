from .workspace_locale import enum_choices
from .tooltip_locale import language_changed
"""Persistent Studio state. No access to scene data at module import/register time."""
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
    FloatVectorProperty, IntProperty, PointerProperty, StringProperty)
from .studio_presets import VIEW_ITEMS,LIGHT_ITEMS


class CPBackupSlot(bpy.types.PropertyGroup):
    material: PointerProperty(type=bpy.types.Material)
    link: StringProperty(default='DATA')


class CPBackupObject(bpy.types.PropertyGroup):
    object: PointerProperty(type=bpy.types.Object)
    original_mesh: PointerProperty(type=bpy.types.Mesh)
    staged_mesh: PointerProperty(type=bpy.types.Mesh)
    slots: CollectionProperty(type=CPBackupSlot)
    custom_json: StringProperty(default='{}')
    active_material_index: IntProperty(default=0)


class CPBackupMaterial(bpy.types.PropertyGroup):
    original: PointerProperty(type=bpy.types.Material)
    staged: PointerProperty(type=bpy.types.Material)


class CPPreviewValue(bpy.types.PropertyGroup):
    material: PointerProperty(type=bpy.types.Material)
    kind: StringProperty(default='NONE')
    node_name: StringProperty(default='')
    socket_name: StringProperty(default='')
    socket_index: IntProperty(default=-1)
    group_node_name: StringProperty(default='')
    inner_node_name: StringProperty(default='')
    path_json: StringProperty(default='[]')
    color: FloatVectorProperty(size=4, default=(0,0,0,1))


def _look_changed(self, context):
    self.score = -1.0
    self.rendered_signature = ''
    try:
        scene = getattr(self, 'id_data', None)
        settings = getattr(scene, 'color_prime', None)
        if settings is not None:
            st = settings.studio
            if not settings.suppress_callbacks and not settings.render_running:
                self.user_edited = True
            if 0 <= st.look_index < len(st.looks) and st.looks[st.look_index] == self:
                from .guide_runtime import request
                request(scene)
    except (AttributeError, ReferenceError, RuntimeError):
        pass


def _look_index_changed(self, context):
    try:
        from .guide_runtime import request
        request(getattr(self, 'id_data', None))
    except (AttributeError, ReferenceError, RuntimeError):
        pass



class CPLook(bpy.types.PropertyGroup):
    user_edited: BoolProperty(default=False)
    name: StringProperty(default='Look', update=_look_changed)
    uid: StringProperty(default='')
    main_color: FloatVectorProperty(name='Main', subtype='COLOR', size=4, min=0, max=1, default=(.15, .4, .3, 1), update=_look_changed)
    accent_color: FloatVectorProperty(name='Accent', subtype='COLOR', size=4, min=0, max=1, default=(.8, .2, .08, 1), update=_look_changed)
    variant: StringProperty(default='CUSTOM')
    reason: StringProperty(default='')
    enabled: BoolProperty(name='Export', default=True)
    image: PointerProperty(type=bpy.types.Image)
    score: FloatProperty(default=-1.0)
    measurements_json: StringProperty(default='{}')
    rendered_signature: StringProperty(default='')


class CPRigLightBackup(bpy.types.PropertyGroup):
    object: PointerProperty(type=bpy.types.Object)
    hide_render: BoolProperty(default=False)


class CPModelCollectionLink(bpy.types.PropertyGroup):
    collection: PointerProperty(type=bpy.types.Collection)
    scene_root: BoolProperty(default=False)


class CPModelObjectBackup(bpy.types.PropertyGroup):
    original_name: StringProperty(default='')
    object: PointerProperty(type=bpy.types.Object)
    parent: PointerProperty(type=bpy.types.Object)
    parent_type: StringProperty(default='OBJECT')
    parent_bone: StringProperty(default='')
    matrix_basis: FloatVectorProperty(size=16)
    parent_inverse: FloatVectorProperty(size=16)
    collections: CollectionProperty(type=CPModelCollectionLink)


class CPModelCreated(bpy.types.PropertyGroup):
    object: PointerProperty(type=bpy.types.Object)
    collection: PointerProperty(type=bpy.types.Collection)
    new_collection: BoolProperty(default=False)
    original_name: StringProperty(default='')
    original_tags: StringProperty(default='{}')


class CPStudioSettings(bpy.types.PropertyGroup):
    model_backups: CollectionProperty(type=CPModelObjectBackup)
    model_created: CollectionProperty(type=CPModelCreated)
    zone_policy: EnumProperty(name='Разметка',items=enum_choices((('PRESERVE','Использовать существующую','Сохранить материалы и назначения; найти только недостающие зоны'),('REPLACE','Разметить заново','Явно заменить разметку на рабочих копиях'))),default=0)
    replace_zones: BoolProperty(name='Пересоздать зоны',default=True)
    replace_materials: BoolProperty(name='Заменить материалы',default=True)
    adoption_pending: BoolProperty(default=False)
    adoption_note: StringProperty(default='')
    model_journal: StringProperty(default='')
    visibility_journal: StringProperty(default='')
    create_studio_on_prepare: BoolProperty(name='Create Studio Camera + Lights', default=False,
        description='Opt-in: add an orthographic camera, three softboxes and neutral world. Existing scene setup can be restored.')
    rig_preset: EnumProperty(name='View', items=enum_choices(VIEW_ITEMS), default=6)
    rig_margin: FloatProperty(name='Frame Margin', description='Empty space around the model, 0–45%. / Вольнае месца вакол мадэлі, 0–45%.', default=.12, min=0., max=.45, subtype='FACTOR')
    rig_yaw: FloatProperty(name='Поворот, °',default=-45.,min=-360.,max=360.)
    rig_elevation: FloatProperty(name='Высота, °',default=28.,min=-90.,max=90.)
    rig_lighting: EnumProperty(name='Свет',items=enum_choices(LIGHT_ITEMS),default=0)
    rig_intensity: FloatProperty(name='Интенсивность',description='Studio light power multiplier; 1 is the preset value. / Множнік магутнасці святла; 1 — значэнне прэсэта.',default=1.,min=0.,max=20.)
    rig_softness: FloatProperty(name='Мягкость',description='Larger light sources give softer shadows. / Большыя крыніцы святла даюць мякчэйшыя цені.',default=1.,min=.05,max=10.)
    rig_key_color: FloatVectorProperty(name='Основной свет',subtype='COLOR',size=3,min=0,max=1,default=(1,1,1))
    rig_fill_color: FloatVectorProperty(name='Заполняющий свет',subtype='COLOR',size=3,min=0,max=1,default=(1,1,1))
    rig_rim_color: FloatVectorProperty(name='Контровой свет',subtype='COLOR',size=3,min=0,max=1,default=(1,1,1))
    rig_backdrop: BoolProperty(name='Плоскость фона за моделью',default=False)
    rig_background_mode: EnumProperty(name='Фон',items=enum_choices((('COLOR','Свой цвет',''),('MATERIAL','Материал',''),('MAIN','Main',''),('ACCENT','Accent',''))),default=0)
    rig_background_color: FloatVectorProperty(name='Цвет фона',subtype='COLOR',size=4,min=0,max=1,default=(.035,.045,.065,1))
    rig_background_material: PointerProperty(name='Материал фона',type=bpy.types.Material)
    rig_background_owned: PointerProperty(type=bpy.types.Material)
    rig_isolate_lights: BoolProperty(name='Mute Existing Local Lights', default=True)
    rig_auto_frame: BoolProperty(name='Fit Studio per Icon', default=True)
    rig_id: StringProperty(default='')
    rig_setup_owner: StringProperty(default='')
    rig_collection: PointerProperty(type=bpy.types.Collection)
    rig_model_collection: PointerProperty(type=bpy.types.Collection)
    rig_model_root: PointerProperty(type=bpy.types.Object)
    rig_camera: PointerProperty(type=bpy.types.Object)
    rig_world: PointerProperty(type=bpy.types.World)
    rig_previous_camera: PointerProperty(type=bpy.types.Object)
    rig_previous_world: PointerProperty(type=bpy.types.World)
    rig_previous_json: StringProperty(default='')
    rig_stage_pose_json: StringProperty(default='')
    rig_muted_lights: CollectionProperty(type=CPRigLightBackup)
    rig_note: StringProperty(default='')
    surface_regions: EnumProperty(name='Solid Mesh Regions', items=(('AUTO','Аўта / Auto','Concave creases, sharp edges, seams and strong narrow necks; no arbitrary cuts'),('DETAIL','Больш зон / More','Also use strong convex changes; review the result'),('OFF','Не дзяліць / Off','Do not infer new material zones')), default='AUTO')
    region_max: IntProperty(name='Maximum Regions', default=32, min=2, max=64)
    region_note: StringProperty(default='')
    region_index: IntProperty(name='Region', default=1, min=1, max=64)
    match_similar_parts: BoolProperty(name='Keep similar parts together',default=True,
        description='Assign the same child to strongly matching rotated, mirrored or uniformly scaled parts within one family')
    assignment_scope: EnumProperty(name='Apply to',items=(('MODEL','Prepared model','All meshes in enabled prepared icon sets'),
        ('SELECTED','Selected meshes','Only currently selected prepared meshes')),default='MODEL')
    assignment_summary: StringProperty(default='')
    assignment_details: StringProperty(default='{}')
    workflow_block: EnumProperty(items=(('MODEL','1 · Мадэль',''),('ZONES','2 · Зоны',''),('COLORS','3 · Колеры і матэрыялы',''),
        ('ASSIGN','4 · Прызначыць',''),('SAVE','5 · Захаваць','')),default='MODEL')
    workflow_options: BoolProperty(default=False)
    workflow_variants: BoolProperty(default=False)
    color_tools: EnumProperty(items=(('PALETTES','Palettes','Main and Accent color libraries'),('MATERIALS','Materials','Child materials and assignment')),default='PALETTES')
    workflow_camera: BoolProperty(default=False)
    workflow_tools: BoolProperty(default=False)
    workflow_analyzed: BoolProperty(default=False)

    ui_mode: EnumProperty(name='Interface', items=(('GUIDED', 'Simple', 'Step-by-step workflow'), ('EXPERT', 'Advanced', 'Full original controls')), default='GUIDED')
    guide_language: EnumProperty(name='Language', items=(('BE', 'Бел', 'Беларуская'), ('EN', 'EN', 'English')), default='BE',update=language_changed)
    guide_step: EnumProperty(name='Step', items=(('MODEL', '1', 'Model'), ('COLORS', '2', 'Colors'), ('EXPORT', '3', 'Export')), default='MODEL')
    guide_help: BoolProperty(name='Interactive help', default=True)
    guide_scope: EnumProperty(name='Model source', items=(('SELECTED', 'Selection', 'Only selected meshes and descendants of selected Empties'), ('AUTO', 'Find icons', 'Detect scene icon roots'), ('SAVED', 'Existing icon list', 'Use enabled icons already configured')), default='SELECTED')
    guide_selection_collection: PointerProperty(type=bpy.types.Collection)
    guide_notice: StringProperty(default='', options={'SKIP_SAVE'})
    live_preview: BoolProperty(name='Live color preview', default=True, description='Update the active look on the model after a short main-thread debounce; does not trigger a render')
    guide_parts_open: BoolProperty(name='Change which parts are colored', default=False)
    guide_export_selection: EnumProperty(name='Colors to export', items=enum_choices((('PALETTES','Main / Accent palettes','Every checked palette combination'),('CURRENT', 'Current model colors', 'Current Prime Main and Accent colors'), ('ENABLED', 'Enabled looks', 'All checked looks'))), default=0)
    guide_export_size: EnumProperty(name='Base size (1x)', items=enum_choices((('SCENE', 'Scene size', 'Scene resolution and percentage define 1x'), ('256', '256 x 256', ''), ('512', '512 x 512', ''), ('1024', '1024 x 1024', ''), ('2048', '2048 x 2048', ''),('CUSTOM','Custom base size','Set width and height for 1x'))), default=0)
    guide_export_width: IntProperty(name='Base width',default=1024,min=1,max=65536)
    guide_export_height: IntProperty(name='Base height',default=1024,min=1,max=65536)
    guide_transparent: BoolProperty(name='Transparent PNG', default=True)
    guide_export_success: BoolProperty(default=False, options={'SKIP_SAVE'})
    guide_export_folder: StringProperty(default='', subtype='DIR_PATH', options={'SKIP_SAVE'})
    guide_export_count: IntProperty(default=0, options={'SKIP_SAVE'})
    comparison_return_space: StringProperty(default='VIEW_3D', options={'SKIP_SAVE'})
    tab: EnumProperty(name='Workspace', items=(('LOOKS','Looks','Palette, reference and rendered comparisons'),
        ('PARTS','Parts','Icon scope and Main/Accent assignments'),('EXPORT','Export','Batch rendering and validation')), default='LOOKS')
    preview_values: CollectionProperty(type=CPPreviewValue)
    overwrite_outputs: BoolProperty(name='Replace Existing Files', default=False, description='Explicitly permit atomic replacement of existing output files')
    stage_status: EnumProperty(items=(('NONE','Original','No pending structural changes'),
        ('STAGED','Preview setup','Working mesh/material copies, originals retained'),
        ('RECOVERY','Recovery required','An interrupted setup needs rollback')), default='NONE')
    stage_id: StringProperty(default='')
    reuse_family_library: BoolProperty(default=False)
    zones_assign_library: BoolProperty(name='Assign existing materials after finding zones',default=False)
    stage_settings_json: StringProperty(default='')
    backup_objects: CollectionProperty(type=CPBackupObject)
    backup_materials: CollectionProperty(type=CPBackupMaterial)
    stage_note: StringProperty(default='')
    looks: CollectionProperty(type=CPLook)
    look_index: IntProperty(default=0, update=_look_index_changed)
    sheet: PointerProperty(type=bpy.types.Image)
    preview_size: IntProperty(name='Preview Long Edge', default=384, min=96, max=1024)
    preview_samples: IntProperty(name='Cycles Preview Samples', default=12, min=1, max=128)
    background: FloatVectorProperty(name='Target UI Background', subtype='COLOR', size=4, min=0, max=1, default=(.025, .029, .04, 1))
    generate_inverted: BoolProperty(name='Include Inverted Palette', default=True,
        description='An alternative palette; never rewrites manual part assignments')
    refine_best: BoolProperty(name='Try Render-guided Correction', default=True,
        description='Render one bounded correction and keep it only when measured fit improves')
    ray_grid: IntProperty(name='Visibility Grid', default=48, min=16, max=96)
    preview_running: BoolProperty(default=False, options={'SKIP_SAVE'})
    preview_progress: StringProperty(default='', options={'SKIP_SAVE'})
    selftest_status: StringProperty(default='Not run in this Blender')
    selftest_report: StringProperty(default='')
    reference_roi: FloatVectorProperty(name='Reference Crop (left, bottom, right, top)', size=4, min=0, max=1, default=(0,0,1,1))
    reference_spatial_background: BoolProperty(name='Connected Border Background', default=True)
    reference_background_tolerance: FloatProperty(name='Background Tolerance', default=.045, min=.005, max=.15)
    batch_from_looks: BoolProperty(name='Export Enabled Looks', default=False,
        description='Render named pairs rather than the Cartesian product of the two palettes')
    gate_passed: BoolProperty(default=False)
    gate_details: StringProperty(default='')
    export_progress: StringProperty(default='', options={'SKIP_SAVE'})
    last_manifest: StringProperty(default='', subtype='FILE_PATH')
    show_expert: BoolProperty(name='Expert Controls', default=False)


CLASSES = (CPBackupSlot, CPBackupObject, CPBackupMaterial, CPPreviewValue, CPLook, CPRigLightBackup, CPModelCollectionLink, CPModelObjectBackup, CPModelCreated, CPStudioSettings)

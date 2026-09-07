from .workspace_locale import enum_choices
import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from .constants import *
from .studio_properties import CPStudioSettings, CLASSES as STUDIO_CLASSES


def _family_update(self, context):
    owner = getattr(self, 'id_data', None)
    scene = owner if hasattr(owner, 'color_prime') else getattr(context, 'scene', None)
    settings = getattr(scene, 'color_prime', None) if scene else None
    if settings is not None and settings.suppress_callbacks:
        return
    if settings is not None and settings.preview_active:
        from .runtime import restore_preview
        restore_preview(scene)
    self.manual_family = True
    mat = self.material
    if settings is not None and mat is not None:
        try:
            from .targets import read_binding_color
            current = read_binding_color(self)
            if current is not None:
                self.captured_material_color = current
                self.captured_family_color = settings.main_reference_color if self.family == 'MAIN' else settings.accent_reference_color if self.family == 'ACCENT' else current
        except Exception:
            pass
    if mat:
        try:
            mat['color_prime_family'] = self.family
            mat['color_prime_family_source'] = 'MANUAL'
        except Exception:
            pass
    if mat and settings:
        from .family_links import ready,connect,disconnect
        from .targets import find_material_target,apply_spec_to_binding
        if ready(settings):
            if mat.get('cp_family_master',''):
                # Master roles are identities. Reassign a part, not its source.
                settings.suppress_callbacks=True
                try:self.family=mat['cp_family_master']
                finally:settings.suppress_callbacks=False
                mat['color_prime_family']=self.family
            elif self.family in {'MAIN','ACCENT'} and self.enabled:
                connect(mat,settings,self.family)
                for child in settings.children:
                    if child.material==mat:child.family=self.family
            else:disconnect(mat)
            apply_spec_to_binding(self,find_material_target(mat,False))


def _lock_update(self, context):
    owner = getattr(self, 'id_data', None)
    scene = owner if hasattr(owner, 'color_prime') else getattr(context, 'scene', None)
    settings = getattr(scene, 'color_prime', None)
    if settings is not None and settings.suppress_callbacks:
        return
    if self.material:
        try:
            self.material['color_prime_locked'] = bool(self.locked)
        except Exception:
            pass


def _binding_enabled(self,context):
    s=getattr(self.id_data,'color_prime',None)
    if not s or s.suppress_callbacks or not self.material:return
    from .family_links import shared_socket,disconnect,connect,ready
    from .targets import find_material_target,apply_spec_to_binding
    if not self.enabled and shared_socket(self.material) and not self.material.get('cp_family_master',''):
        from .runtime import restore_preview
        restore_preview(self.id_data);disconnect(self.material)
    elif self.enabled and ready(s) and self.family in {'MAIN','ACCENT'}:
        connect(self.material,s,self.family)
    apply_spec_to_binding(self,find_material_target(self.material,False))


def _palette_changed(self,context):
    scene=getattr(self,'id_data',None)
    s=getattr(scene,'color_prime',None)
    if s is not None and not s.suppress_callbacks:
        from .guide_runtime import request
        request(scene,source='PALETTE')


class CPColorItem(bpy.types.PropertyGroup):
    name: StringProperty(name='Name', default='Color')
    enabled: BoolProperty(name='Enabled', default=True)
    color: FloatVectorProperty(name='Color', subtype='COLOR', size=4, min=0.0, max=1.0, default=(0.18, 0.8, 0.28, 1.0),update=_palette_changed)


class CPPaletteSet(bpy.types.PropertyGroup):
    uid: StringProperty(default='')
    name: StringProperty(default='Palette')
    family: EnumProperty(items=(('MAIN','Main',''),('ACCENT','Accent','')))
    enabled: BoolProperty(default=True)
    colors: CollectionProperty(type=CPColorItem)


class CPReferencePairItem(bpy.types.PropertyGroup):
    name: StringProperty(name='Name', default='Clean Pick')
    variant: StringProperty(default='CLEAN')
    main_color: FloatVectorProperty(name='Main', subtype='COLOR', size=4, min=0.0, max=1.0, default=(0.18, 0.8, 0.28, 1.0))
    accent_color: FloatVectorProperty(name='Accent', subtype='COLOR', size=4, min=0.0, max=1.0, default=(0.8, 0.18, 0.55, 1.0))
    score: FloatProperty(name='Score', default=0.0, min=0.0, max=1.0, subtype='FACTOR')
    confidence: FloatProperty(name='Evidence Score', description='Heuristic ranking signal, not a calibrated probability of correctness', default=0.0, min=0.0, max=1.0)
    main_coverage: FloatProperty(name='Main Coverage', default=0.0, min=0.0, max=1.0, subtype='FACTOR')
    accent_coverage: FloatProperty(name='Accent Coverage', default=0.0, min=0.0, max=1.0, subtype='FACTOR')
    reason: StringProperty(default='')


def _appearance_color_update(self,context):
    from .workspace_actions import queue_preview
    queue_preview(self,context)


class CPAppearanceColor(bpy.types.PropertyGroup):
    name: StringProperty(default='Color')
    color: FloatVectorProperty(subtype='COLOR',size=4,min=0,max=1,default=(.5,.5,.5,1),update=_appearance_color_update)
    enabled: BoolProperty(default=True)


class CPAppearanceMaterial(bpy.types.PropertyGroup):
    material: PointerProperty(type=bpy.types.Material)
    family: StringProperty(default='MAIN')


class CPAppearance(bpy.types.PropertyGroup):
    uid: StringProperty(default='')
    name: StringProperty(default='Новая палитра')
    enabled: BoolProperty(name='Включить в экспорт',default=True)
    main_colors: CollectionProperty(type=CPAppearanceColor)
    accent_colors: CollectionProperty(type=CPAppearanceColor)
    main_index: IntProperty(default=0,min=0,update=_appearance_color_update)
    accent_index: IntProperty(default=0,min=0,update=_appearance_color_update)
    materials: CollectionProperty(type=CPAppearanceMaterial)
    main_base: FloatVectorProperty(size=4,default=(.5,.5,.5,1))
    accent_base: FloatVectorProperty(size=4,default=(.5,.5,.5,1))
    source_key: StringProperty(default='')
    source_signature: StringProperty(default='')


class CPResolutionItem(bpy.types.PropertyGroup):
    name: StringProperty(name='Name', default='1x')
    enabled: BoolProperty(name='Enabled', default=True)
    mode: EnumProperty(name='Mode', items=RESOLUTION_MODE_ITEMS, default='SCALE')
    scale_percent: IntProperty(name='Scale', default=100, min=1, max=1600, subtype='PERCENTAGE')
    scale_factor: FloatProperty(name='Scale x',min=.01,max=16.,precision=2,
        get=lambda self:self.scale_percent/100.,set=lambda self,value:setattr(self,'scale_percent',round(value*100)))
    width: IntProperty(name='Width', default=1024, min=1, max=65536)
    height: IntProperty(name='Height', default=1024, min=1, max=65536)


class CPIconSetItem(bpy.types.PropertyGroup):
    name: StringProperty(name='Name', default='Icon')
    enabled: BoolProperty(name='Render', default=True)
    selective_colors: BoolProperty(default=False)
    root_kind: EnumProperty(name='Root Type', items=ROOT_KIND_ITEMS, default='OBJECT')
    object_root: PointerProperty(name='Object Root', type=bpy.types.Object)
    collection_root: PointerProperty(name='Collection Root', type=bpy.types.Collection)
    auto_detected: BoolProperty(name='Auto-detected', default=False)
    status: StringProperty(name='Status', default='')
    applied_appearance: StringProperty(default='')
    applied_appearance_uid: StringProperty(default='')
    applied_materials: BoolProperty(default=False)
    applied_signature: StringProperty(default='')
    zones_origin: StringProperty(default='')
    workspace_auto_zones: BoolProperty(name='Автозоны при применении',default=False,description='Найти недостающие зоны перед применением; существующие семейства и ручная разметка сохраняются')
    workspace_zones_checked: BoolProperty(default=False)


class CPMaterialBindingItem(bpy.types.PropertyGroup):
    material: PointerProperty(name='Material', type=bpy.types.Material)
    material_identity: StringProperty(default='')
    enabled: BoolProperty(name='Enabled', default=True,update=_binding_enabled)
    family: EnumProperty(name='Family', items=FAMILY_ITEMS, default='IGNORE', update=_family_update)
    locked: BoolProperty(name='Lock Assignment', default=False, update=_lock_update)
    manual_family: BoolProperty(default=False)
    inheritance_mode: EnumProperty(name='Inheritance', items=INHERITANCE_ITEMS, default='OKLCH')
    confidence: FloatProperty(name='Evidence Score', description='Heuristic ranking signal, not a calibrated probability of correctness', default=0.0, min=0.0, max=1.0)
    usage_weight: FloatProperty(default=0.0, min=0.0)
    used_by_icon_count: IntProperty(default=0, min=0)
    auto_reason: StringProperty(default='')
    status: StringProperty(default='')
    target_kind: EnumProperty(name='Target', items=TARGET_KIND_ITEMS, default='NONE')
    target_node_name: StringProperty(default='')
    target_socket_name: StringProperty(default='')
    target_socket_index: IntProperty(default=-1, min=-1)
    target_group_node_name: StringProperty(default='')
    target_inner_node_name: StringProperty(default='')
    target_path_json: StringProperty(default='[]')
    legacy_source_group_name: StringProperty(default='')
    source_group_name: StringProperty(default='')
    source_group_identity: StringProperty(default='')
    source_group_stem: StringProperty(default='')
    captured_family_color: FloatVectorProperty(name='Family Reference', subtype='COLOR', size=4, min=0.0, max=1.0, default=(0.18, 0.8, 0.28, 1.0))
    captured_material_color: FloatVectorProperty(name='Material Reference', subtype='COLOR', size=4, min=0.0, max=1.0, default=(0.18, 0.8, 0.28, 1.0))


class CPDiagnosticItem(bpy.types.PropertyGroup):
    level: EnumProperty(items=DIAGNOSTIC_LEVEL_ITEMS, default='INFO')
    message: StringProperty(default='')
    data_name: StringProperty(default='')
    code: StringProperty(default='')


class CPChildMaterialItem(bpy.types.PropertyGroup):
    material: PointerProperty(name='Child material', type=bpy.types.Material)
    family: EnumProperty(name='Parent family', items=(('MAIN','Main',''),('ACCENT','Accent','')), default='MAIN')
    profile: StringProperty(default='SAME')
    auto_assign: BoolProperty(name='Include in automatic assignment', default=True)
    follow_surface: BoolProperty(name='Inherit parent surface on Sync', default=True,
        description='Sync copies unlinked Principled values from the parent, keeping the selected variant overrides')


class ColorPrimeSettings(bpy.types.PropertyGroup):
    appearances: CollectionProperty(type=CPAppearance)
    appearance_index: IntProperty(default=0,min=0)
    appearance_source: StringProperty(default='')
    appearance_mode: EnumProperty(items=enum_choices((('PALETTE','Палитра',''),('MODEL','Из модели',''))),default=0)
    workspace_page: EnumProperty(items=enum_choices((('WORKSPACE','Workspace',''),('FAST','Fast Track',''))),default=0)
    fast_route: EnumProperty(items=enum_choices((('BATCH','Many icons → consistent framing',''),('TRANSFER','Model → another model',''),('IMPORT','Imported model → zones',''))),default=0)
    appearance_materials: BoolProperty(name='Перенести также материалы',default=False,description='Перенести поверхности при первом применении палитры к другой модели. Повторная смена цветов сохраняет назначения; модель-источник не переразмечается')
    appearance_details: BoolProperty(default=False)
    appearance_advanced: BoolProperty(default=False)
    appearance_export: BoolProperty(default=False)
    appearance_camera: BoolProperty(default=False)
    appearance_note: StringProperty(default='')
    variants_open: BoolProperty(default=True)
    variants_family: EnumProperty(items=(('MAIN','Main',''),('ACCENT','Accent','')),default='MAIN')
    variants_count: IntProperty(name='Количество',default=3,min=1,max=64)
    variants_style: EnumProperty(name='Набор',items=enum_choices((('VARIED','Разные поверхности','Матовый, глянцевый, металлический, светлый и тёмный варианты'),('SAME','Одинаковая основа','Нейтральные экземпляры для ручной настройки'))),default=0)
    variants_details: BoolProperty(default=False)
    workspace_preview: BoolProperty(default=False)
    appearance_palette_export: BoolProperty(name='Экспортировать палитры рабочего экрана',default=True)
    palette_sets: CollectionProperty(type=CPPaletteSet)
    main_palette_uid: StringProperty(default='')
    accent_palette_uid: StringProperty(default='')
    studio: PointerProperty(type=CPStudioSettings)
    children: CollectionProperty(type=CPChildMaterialItem)
    child_index: IntProperty(default=0, min=0)
    schema_version: IntProperty(default=SCHEMA_VERSION, options={'HIDDEN'})
    addon_version: StringProperty(default=VERSION_STRING, options={'HIDDEN'})
    suppress_callbacks: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    setup_complete: BoolProperty(default=False)

    detection_mode: EnumProperty(name='Icon Detection', items=DETECTION_MODE_ITEMS, default='SMART')
    auto_localize_legacy: BoolProperty(name='Localize Shared Color Groups', default=True)
    auto_protect_textures: BoolProperty(name='Protect Texture-driven Materials', default=True)
    fixed_name_suffix: StringProperty(name='Fixed Marker', default=DEFAULT_FIXED_NAME_SUFFIX, description="Semantic material names ending in this marker are never recolored; Gold!.001 is also Fixed")

    auto_prepare_materials: BoolProperty(name='Create/Split Materials Automatically', default=True, description='Create missing Main/Accent materials and split a material shared across inferred roles')
    auto_split_shared_materials: BoolProperty(name='Split Shared Main/Accent Material', default=True)
    localize_per_icon: BoolProperty(name='Unique Copies per Icon', default=False, description='When splitting a cross-role material, make separate copies for every icon object')
    geometry_analysis_mode: EnumProperty(name='Geometry Evidence', items=GEOMETRY_ANALYSIS_ITEMS, default='AUTO')
    loose_part_mode: EnumProperty(name='Disconnected Mesh Islands', items=LOOSE_PART_MODE_ITEMS, default='TWO_ONLY')
    autopilot_last_summary: StringProperty(default='')

    main_parent_material: PointerProperty(name='Main Parent', type=bpy.types.Material)
    accent_parent_material: PointerProperty(name='Accent Parent', type=bpy.types.Material)
    main_reference_color: FloatVectorProperty(name='Main Reference', subtype='COLOR', size=4, min=0, max=1, default=(0.18, 0.8, 0.28, 1))
    accent_reference_color: FloatVectorProperty(name='Accent Reference', subtype='COLOR', size=4, min=0, max=1, default=(0.8, 0.18, 0.55, 1))
    palette_initialized_from_scene: BoolProperty(default=False, options={'HIDDEN'})
    main_colors: CollectionProperty(type=CPColorItem)
    main_color_index: IntProperty(default=0,update=_palette_changed)
    accent_colors: CollectionProperty(type=CPColorItem)
    accent_color_index: IntProperty(default=0,update=_palette_changed)
    combination_mode: EnumProperty(name='Combinations', items=enum_choices(COMBINATION_ITEMS), default=0)

    reference_image: PointerProperty(name='Reference Image', type=bpy.types.Image)
    reference_pairs: CollectionProperty(type=CPReferencePairItem)
    reference_pair_index: IntProperty(default=0)
    reference_max_samples: IntProperty(name='Sample Budget', default=10000, min=512, max=50000)
    reference_cluster_count: IntProperty(name='Color Clusters', default=8, min=3, max=12)
    reference_alpha_threshold: FloatProperty(name='Ignore Alpha Below', default=0.04, min=0.0, max=1.0, subtype='FACTOR')
    reference_clean_strength: FloatProperty(name='UI Cleanup', default=0.68, min=0.0, max=1.0, subtype='FACTOR')
    reference_input_encoding: EnumProperty(name='Reference Buffer', items=(
        ('AUTO', 'Auto: sRGB / Linear Rec.709', 'Decode a standard byte sRGB buffer, preserve a native linear float buffer; reject unknown profiles'),
        ('SRGB', 'sRGB Encoded', 'Interpret raw RGB samples as sRGB, not as linear light'),
        ('LINEAR', 'Linear sRGB / Rec.709', 'Interpret raw samples as linear sRGB; does not convert other primaries')), default='AUTO')
    reference_buffer_alpha: EnumProperty(name='Reference Buffer Alpha', items=(
        ('AUTO', 'Auto: Native Buffer', 'Byte buffer straight; float buffer premultiplied, except packed/no-alpha data'),
        ('STRAIGHT', 'Straight', 'Raw RGB samples are not multiplied by alpha'),
        ('PREMUL', 'Premultiplied', 'Unpremultiply raw RGB samples before color analysis')), default='AUTO')
    reference_background_mode: EnumProperty(name='Background', items=REFERENCE_BACKGROUND_ITEMS, default='AUTO')
    reference_auto_use: BoolProperty(name='Use Best Pair during Auto Setup', default=True)
    auto_preview_after_setup: BoolProperty(name='Color Model after Auto Setup', default=True, description='Apply the selected Main/Accent pair as a reversible preview after automatic setup')
    reference_last_summary: StringProperty(default='No reference analyzed')

    icon_sets: CollectionProperty(type=CPIconSetItem)
    icon_set_index: IntProperty(default=0)
    bindings: CollectionProperty(type=CPMaterialBindingItem)
    binding_index: IntProperty(default=0)
    resolutions: CollectionProperty(type=CPResolutionItem)
    resolution_index: IntProperty(default=0)
    diagnostics: CollectionProperty(type=CPDiagnosticItem)
    diagnostic_index: IntProperty(default=0)

    output_folder: StringProperty(name='Output Folder', subtype='DIR_PATH', default='//ColorPrime/')
    filename_template: StringProperty(name='Filename', default='{icon} (Main {main} - Accent {accent})')
    transparency: EnumProperty(name='Film', items=TRANSPARENCY_ITEMS, default='KEEP')
    force_show_target: BoolProperty(name='Force Show Target Objects', default=True)
    skip_existing: BoolProperty(name='Skip Existing', default=False)
    write_manifest: BoolProperty(name='Write JSONL Manifest', default=True)
    error_policy: EnumProperty(name='Errors', items=ERROR_POLICY_ITEMS, default='STOP')

    show_advanced: BoolProperty(name='Advanced', default=False)
    preview_active: BoolProperty(default=False, options={'SKIP_SAVE'})
    render_running: BoolProperty(default=False, options={'SKIP_SAVE'})
    render_paused: BoolProperty(default=False, options={'SKIP_SAVE'})
    render_stop_requested: BoolProperty(default=False, options={'SKIP_SAVE'})
    render_current: IntProperty(default=0, min=0, options={'SKIP_SAVE'})
    render_total: IntProperty(default=0, min=0, options={'SKIP_SAVE'})
    last_job_signature: StringProperty(default='', options={'HIDDEN'})
    last_success_index: IntProperty(default=-1, options={'HIDDEN'})
    last_error: StringProperty(default='', options={'HIDDEN'})
    last_setup_summary: StringProperty(default='Not scanned')


CLASSES = STUDIO_CLASSES + (
    CPColorItem,
    CPPaletteSet,
    CPReferencePairItem,
    CPAppearanceColor,
    CPAppearanceMaterial,
    CPAppearance,
    CPResolutionItem,
    CPIconSetItem,
    CPMaterialBindingItem,
    CPDiagnosticItem,
    CPChildMaterialItem,
    ColorPrimeSettings,
)

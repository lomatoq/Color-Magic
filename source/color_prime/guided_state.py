"""Read-only workflow rules. Importable without Blender for contract tests."""


def tr(settings, english, belarusian):
    return belarusian if getattr(settings.studio, 'guide_language', 'EN') == 'BE' else english


def pointer(value):
    try:
        return value.as_pointer()
    except (AttributeError, ReferenceError):
        return id(value)


def mesh_descendants(root):
    out = []
    stack = [root]
    seen = set()
    while stack:
        obj = stack.pop()
        key = pointer(obj)
        if key in seen:
            continue
        seen.add(key)
        if getattr(obj, 'type', '') == 'MESH':
            out.append(obj)
        stack.extend(getattr(obj, 'children', ()))
    return out


def selection_roots(objects):
    """Do not climb to unselected ancestors and accidentally include a scene.

    Descendants of explicitly selected model roots are intentional. Image
    reference Empties and non-geometry helpers are not icon roots.
    """
    valid = []
    for obj in objects:
        kind = getattr(obj, 'type', '')
        if kind not in {'MESH', 'EMPTY'}:
            continue
        if kind == 'EMPTY' and getattr(obj, 'data', None) is not None:
            continue
        if mesh_descendants(obj):
            valid.append(obj)
    selected = {pointer(o) for o in valid}
    result = []
    for obj in valid:
        parent = getattr(obj, 'parent', None)
        nested = False
        seen = set()
        while parent is not None and pointer(parent) not in seen:
            seen.add(pointer(parent))
            if pointer(parent) in selected:
                nested = True
                break
            parent = getattr(parent, 'parent', None)
        if not nested:
            result.append(obj)
    return sorted(result, key=lambda o: getattr(o, 'name', ''))


def selection_counts(roots):
    # Multiple selected standalone meshes form ONE icon, not one export per cube.
    empties = [o for o in roots if getattr(o, 'type', '') == 'EMPTY']
    meshes = [o for o in roots if getattr(o, 'type', '') == 'MESH']
    members = {pointer(m) for o in roots for m in mesh_descendants(o)}
    return len(empties) + bool(meshes), len(members)


def material_counts(settings):
    counts = {'MAIN': 0, 'ACCENT': 0, 'FIXED': 0, 'IGNORE': 0, 'REVIEW': 0}
    for binding in settings.bindings:
        if not binding.material or not binding.enabled:
            continue
        role = binding.family
        counts[role] = counts.get(role, 0) + 1
        if role in {'MAIN', 'ACCENT'} and not binding.manual_family and binding.confidence < .65:
            counts['REVIEW'] += 1
    return counts


def prepared(settings):
    if settings.studio.stage_status == 'RECOVERY':
        return False
    icons = any(i.enabled and (i.object_root if i.root_kind == 'OBJECT' else i.collection_root)
                for i in settings.icon_sets)
    colors = any(b.enabled and b.material and b.family in {'MAIN', 'ACCENT'}
                 and b.target_kind != 'NONE' for b in settings.bindings)
    return bool(icons and (colors or settings.studio.stage_status=='STAGED'))


def valid_step(settings):
    """Deleting model/bindings sends the guide to a useful start, not a dead end."""
    requested = getattr(settings.studio, 'guide_step', 'MODEL')
    if requested not in {'MODEL', 'COLORS', 'EXPORT'}:
        return 'MODEL'
    if requested != 'MODEL' and not prepared(settings):
        return 'MODEL'
    if requested == 'EXPORT' and not settings.studio.looks:
        return 'COLORS'
    return requested


def guided_pairs(settings):
    st = settings.studio
    if st.guide_export_selection=='PALETTES':
        from .palette_sets import pairs
        return pairs(settings)
    if st.guide_export_selection == 'CURRENT':
        # A current-appearance render also works on imported, unprepared models.
        return [('Current Main',(.5,.5,.5,1),'Current Accent',(.5,.5,.5,1),'current-model')]
    else:
        items = [look for look in st.looks if look.enabled]
    return [(look.name, tuple(look.main_color), look.name, tuple(look.accent_color), look.uid)
            for look in items]


def export_dimensions(scene, size, studio=None):
    if size=='CUSTOM' and studio is not None:
        return studio.guide_export_width,studio.guide_export_height
    if size == 'SCENE':
        r = scene.render
        return (max(1, int(r.resolution_x * r.resolution_percentage / 100)),
                max(1, int(r.resolution_y * r.resolution_percentage / 100)))
    if size not in {'256', '512', '1024', '2048'}:
        raise ValueError('Choose Scene size or an available square PNG size')
    return int(size), int(size)


def guided_resolutions(scene,settings,include_disabled=False):
    from types import SimpleNamespace
    from .export_kernel import effective_resolution
    st=settings.studio
    base=export_dimensions(scene,st.guide_export_size,st)
    presets=list(settings.resolutions)
    if not presets:
        presets=[SimpleNamespace(name='1x',enabled=True,mode='SCALE',scale_percent=100,width=base[0],height=base[1])]
    output=[]
    for res in presets:
        if not res.enabled and not include_disabled:continue
        width,height=effective_resolution(*base,100,res.mode,res.width,res.height,res.scale_percent)
        name='{:g}x'.format(res.scale_percent/100.) if res.mode=='SCALE' else '{}x{}px'.format(width,height)
        output.append(SimpleNamespace(name=res.name,enabled=res.enabled,mode='ABSOLUTE',width=width,height=height,
            scale_percent=res.scale_percent,folder_name=name))
    return output


def export_count(settings):
    candidates=[i for i in settings.icon_sets if i.enabled and (i.object_root if i.root_kind=='OBJECT' else i.collection_root)]
    if getattr(settings,'appearance_mode','PALETTE')=='MODEL':candidates=[i for i in candidates if i.name!=settings.appearance_source]
    scene=getattr(settings,'id_data',None)
    if scene is not None:
        from .utils import objects_from_icon_item
        candidates=[i for i in candidates if any(o.type in {'MESH','CURVE','SURFACE','FONT','META'} for o in objects_from_icon_item(i,scene))]
    icons=len(candidates)
    sizes=sum(bool(r.enabled) for r in settings.resolutions) if settings.resolutions else 1
    return icons * len(guided_pairs(settings)) * sizes

"""Reusable material names independent of the model that first used them."""
import bpy

STYLES={'SAME':'Base','BASE':'Base','LIGHT':'Light','DARK':'Dark','MATTE':'Matte',
        'GLOSS':'Gloss','METAL':'Metallic'}


def name_material(material,family,style='BASE',prefix=''):
    if style=='BASE':
        from .family_links import principled
        node=principled(material)
        if node:
            metal=node.inputs.get('Metallic');rough=node.inputs.get('Roughness')
            if metal and not metal.is_linked and metal.default_value>=.5:style='METAL'
            elif rough and not rough.is_linked and rough.default_value>=.6:style='MATTE'
            elif rough and not rough.is_linked and rough.default_value<=.3:style='GLOSS'
    label=STYLES.get(style,style.title())
    stem='{} · {}'.format(prefix.strip() or family.title(),label)
    used={m.name for m in bpy.data.materials if m!=material}
    index=1
    while '{} {:02d}'.format(stem,index) in used:index+=1
    material.name='{} {:02d}'.format(stem,index)
    material['color_prime_generated_name']=material.name
    material['color_prime_name_style']=style
    material['color_prime_name_prefix']=prefix.strip()
    return material.name


def legacy_style(mat,settings):
    from .transaction import OWNER_KEY,protected
    if settings.studio.stage_status!='STAGED':return None
    if mat.get(OWNER_KEY,'')!=settings.studio.stage_id or protected(mat,settings):return None
    family=mat.get('color_prime_family','')
    if family not in {'MAIN','ACCENT','FIXED'} or mat.get('color_prime_generated_name',''):return None
    if mat.get('color_prime_child',False):
        return mat.get('color_prime_child_profile','SAME') if mat.name.startswith('CP '+family.title()+' ') else None
    return 'BASE' if 'cp_material_region' in mat or mat.get('color_prime_family_source','')=='AUTO_GEOMETRY' else None


def normalize_generated_names(settings):
    """Migrate RC3/RC4 working materials after restoring temporary colors.

    Original material IDs and explicit custom child prefixes stay untouched.
    An author's rename after generation is also kept.
    """
    from .utils import data_block_identity
    if settings.preview_active:raise ValueError('Restore preview before renaming generated materials.')
    changed=0
    for mat in sorted(bpy.data.materials,key=lambda m:m.name):
        style=legacy_style(mat,settings)
        if style is None:continue
        name_material(mat,mat.get('color_prime_family'),style);changed+=1
    # Renaming must retain disabled inheritance and captured baselines when the
    # next scan indexes bindings by their material's current datablock identity.
    for binding in settings.bindings:
        if binding.material:binding.material_identity=data_block_identity(binding.material)
    return changed


def update_surface_names(settings):
    """Describe actual child surfaces; retain deliberate names and color shades."""
    from .family_links import principled
    from .utils import data_block_identity
    if settings.preview_active or settings.suppress_callbacks or settings.render_running:return 0
    changed=0
    for child in settings.children:
        mat=child.material
        if mat is None or mat.name!=mat.get('color_prime_generated_name',''):continue
        from .family_links import color_input
        target=color_input(mat)
        if target is None:continue
        shader=target.node
        if shader.type=='BSDF_PRINCIPLED':
            metal=shader.inputs.get('Metallic');rough=shader.inputs.get('Roughness')
            if metal.is_linked or rough.is_linked:continue
            style='METAL' if metal.default_value>=.5 else 'MATTE' if rough.default_value>=.6 else 'GLOSS' if rough.default_value<=.3 else 'BASE'
        else:style={'BSDF_DIFFUSE':'Diffuse','EMISSION':'Emission'}.get(shader.type,'Custom')
        from .material_controls import tint_node
        tint=tint_node(mat)
        if tint and tint.inputs[0].default_value>0:
            color=tuple(tint.inputs[2].default_value[:3])
            shade='Light' if min(color)>.999 else 'Dark' if max(color)<.001 else 'Tint'
            style=shade+' '+STYLES.get(style,style)
        if mat.get('color_prime_name_style','')!=style or mat.get('cp_name_family',child.family)!=child.family:
            name_material(mat,child.family,style,mat.get('color_prime_name_prefix',''));changed+=1
        mat['cp_name_family']=child.family
    if changed:
        for binding in settings.bindings:
            if binding.material:binding.material_identity=data_block_identity(binding.material)
    return changed


def _flush_names():
    for scene in bpy.data.scenes:
        settings=getattr(scene,'color_prime',None)
        if settings:update_surface_names(settings)
    return None


def queue_surface_names():
    if not bpy.app.timers.is_registered(_flush_names):bpy.app.timers.register(_flush_names,first_interval=.25)


def cancel_surface_names():
    if bpy.app.timers.is_registered(_flush_names):bpy.app.timers.unregister(_flush_names)

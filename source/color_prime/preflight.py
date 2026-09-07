"""Fail-closed checks shared by preview and final export."""
from pathlib import Path
import os
import bpy
from .targets import binding_target_is_valid
from .utils import icon_item_is_valid,objects_from_icon_item
from .transaction import protected
from .compat import compositor_tree, compositor_file_outputs


SUPPORTED_FORMATS={'PNG':'.png','JPEG':'.jpg','TIFF':'.tif','OPEN_EXR':'.exr','WEBP':'.webp'}


def check_scene(scene,settings,preview=False,output_format=None,require_colors=True):
    errors=[];warnings=[]
    if getattr(bpy.context,'mode','OBJECT')!='OBJECT':errors.append('Switch to Object Mode before rendering')
    if settings.studio.stage_status=='RECOVERY':errors.append('An incomplete setup must be reverted before rendering')
    if scene.camera is None:errors.append('Set the active scene camera')
    if scene.render.engine=='BLENDER_WORKBENCH':errors.append('Workbench does not render the managed shader colors. Use Eevee or Cycles.')
    enabled=[i for i in settings.icon_sets if i.enabled]
    if not enabled:errors.append('Enable at least one icon set')
    claimed={}
    for icon in enabled:
        if not icon_item_is_valid(icon,scene):
            errors.append('Missing icon root: '+icon.name);continue
        for obj in objects_from_icon_item(icon,scene):
            if obj.type!='MESH':continue
            if obj.as_pointer() in claimed and claimed[obj.as_pointer()]!=icon.name:
                errors.append('Overlapping icon sets share mesh '+obj.name)
            claimed[obj.as_pointer()]=icon.name
    active=[b for b in settings.bindings if b.enabled and b.material and b.family in {'MAIN','ACCENT'}
            and not protected(b.material,settings)]
    if require_colors and not active:errors.append('No editable model colors. Apply a palette to the selected models first.')
    for binding in active:
        if require_colors and not binding_target_is_valid(binding):errors.append('Color target is missing: '+binding.material.name)
        if binding.confidence<.65 and not binding.manual_family:
            warnings.append('Review low-evidence role: '+binding.material.name)
    for layer in scene.view_layers:
        if layer.use and layer.material_override:errors.append('Remove material override from view layer '+layer.name)
    tree=compositor_tree(scene)
    file_outputs=list(compositor_file_outputs(tree))
    if preview and any(path for path, node in file_outputs):
        errors.append('Mute nested compositor File Output nodes before comparison renders')
    if not preview:
        if (output_format or scene.render.image_settings.file_format) not in SUPPORTED_FORMATS:
            errors.append('Choose PNG, JPEG, TIFF, WebP or single-layer OpenEXR for atomic still export')
        if scene.render.use_border:errors.append('Disable Render Region for dimension-verified batch export')
        if getattr(scene.render,'use_multiview',False):errors.append('Multiview export is not supported')
        if file_outputs:
            errors.append('Mute compositor File Output nodes (including nested groups): they bypass the atomic output contract')
        sequencer=getattr(scene,'sequence_editor',None)
        strips=getattr(sequencer,'strips',getattr(sequencer,'sequences',())) if sequencer else ()
        if strips and scene.render.use_sequencer:errors.append('Disable Sequencer output for icon batch rendering')
        if not str(settings.output_folder or '').strip():errors.append('Choose an output folder')
        if str(settings.output_folder or '').startswith('//') and not bpy.data.filepath:
            errors.append('Save the .blend first or choose an absolute output folder')
    if os.environ.get('OCIO'):warnings.append('Custom OCIO: numeric palette matching is not calibrated for this config')
    if scene.display_settings.display_device!='sRGB':warnings.append('Non-sRGB display: readability scores are advisory only')
    if settings.studio.selftest_status=='Not run in this Blender':warnings.append('Built-in Blender self-test has not been run on this installation')
    return list(dict.fromkeys(errors)),list(dict.fromkeys(warnings))


def report(scene,settings):
    errors,warnings=check_scene(scene,settings,False)
    try:
        from .batch_export import make_tasks
        tasks=make_tasks(scene,settings)
    except Exception as exc:
        errors.append(str(exc));tasks=[]
    text=['Color Prime Studio — preflight', 'Blender: '+bpy.app.version_string,
          'Scene: '+scene.name, 'Tasks: '+str(len(tasks)), '']
    text+=['ERROR: '+x for x in errors]+['WARNING: '+x for x in warnings]
    if not errors:text.append('Preflight passed. This is not a render-runtime certification.')
    settings.studio.gate_passed=not bool(errors)
    settings.studio.gate_details='\n'.join(text)
    return errors,warnings,text

"""Frozen render tasks, atomic files, exact-hash resume, exclusive output lock."""
from pathlib import Path
import os
import struct
import uuid
import bpy
from .export_kernel import (safe_component,digest,file_digest,append_record,read_manifest,
    reusable,assert_unique_paths,OutputLock,effective_resolution,paired_indices)
from .preflight import SUPPORTED_FORMATS,check_scene
from .render_session import RenderJob
from .runtime import apply_family_colors,material_pointers_for_objects
from .utils import objects_from_icon_item,all_icon_objects,icon_item_is_valid

MANIFEST='color_prime_studio_manifest.jsonl'


def color_label(settings,family,name,hex_code):
    if name and name not in {'Current Main','Current Accent'}:return name.strip()
    from .palette_sets import export_colors
    from .color_math import color_to_hex
    return next((label for _,label,color in export_colors(settings,family)
                 if color_to_hex(color).lstrip('#')==hex_code),'#'+hex_code)


def color_pairs(settings):
    if settings.studio.batch_from_looks:
        return [(l.name,tuple(l.main_color),l.name,tuple(l.accent_color),l.uid)
                for l in settings.studio.looks if l.enabled]
    from .palette_sets import pairs
    return pairs(settings)


def make_tasks(scene,settings,pairs_override=None,resolutions_override=None,file_format=None,current_by_model=False):
    icons=[i for i in settings.icon_sets if i.enabled and icon_item_is_valid(i,scene)]
    icons=[i for i in icons if any(o.type in {'MESH','CURVE','SURFACE','FONT','META'} for o in objects_from_icon_item(i,scene))]
    if getattr(settings,'appearance_mode','PALETTE')=='MODEL':
        icons=[i for i in icons if i.name!=settings.appearance_source]
    resolutions=[r for r in settings.resolutions if r.enabled] if resolutions_override is None else resolutions_override
    pairs=color_pairs(settings) if pairs_override is None else pairs_override
    if not icons or not resolutions or not pairs:
        raise ValueError('The queue needs enabled icons, color pairs/looks, and resolutions')
    extension=SUPPORTED_FORMATS.get(file_format or scene.render.image_settings.file_format)
    if extension is None:raise ValueError('Unsupported output image format')
    root=Path(bpy.path.abspath(settings.output_folder)).resolve()
    tasks=[]
    for icon in icons:
        if current_by_model:
            from .appearance_workspace import model_colors,meshes
            colors,_=model_colors(meshes(icon,scene),settings)
            pairs=[('Current Main',colors['MAIN'],'Current Accent',colors['ACCENT'],'current-model')]
        for main_name,main,accent_name,accent,lookid in pairs:
            from .color_math import color_to_hex
            main_hex=color_to_hex(main).lstrip('#');accent_hex=color_to_hex(accent).lstrip('#')
            main_label=color_label(settings,'MAIN',main_name,main_hex)
            accent_label=color_label(settings,'ACCENT',accent_name,accent_hex)
            for res in resolutions:
                width,height=effective_resolution(scene.render.resolution_x,scene.render.resolution_y,
                    scene.render.resolution_percentage,res.mode,res.width,res.height,res.scale_percent)
                idx=len(tasks)
                resolution_name=getattr(res,'folder_name',None) or ('{:g}x'.format(res.scale_percent/100.) if res.mode=='SCALE' else '{}x{}px'.format(width,height))
                values={'icon':safe_component(icon.name),'main':safe_component(main_label),
                    'accent':safe_component(accent_label),'main_hex':main_hex,'accent_hex':accent_hex,
                    'resolution':safe_component(resolution_name),'index':'{:05d}'.format(idx)}
                from .export_kernel import DEFAULT_FILENAME,LEGACY_FILENAME,readable_path
                template=settings.filename_template
                if template==LEGACY_FILENAME:template=DEFAULT_FILENAME
                try:name=safe_component(template.format_map(values))
                except (KeyError,ValueError,IndexError) as exc:raise ValueError('Invalid filename template: '+str(exc))
                target=root/readable_path(icon.name,main_label,accent_label,resolution_name,name,extension,len(resolutions)>1)
                # Relative traversal is impossible after sanitization; assert it anyway.
                target.relative_to(root)
                spec={'index':idx,'icon':icon.name,'root':icon.object_root.name if icon.root_kind=='OBJECT' else icon.collection_root.name,
                      'kind':icon.root_kind,'main_name':main_name,'main':main,'accent_name':accent_name,'accent':accent,
                      'look_id':lookid,'width':width,'height':height,'path':str(target)}
                spec.update(main_hex=main_hex,accent_hex=accent_hex,resolution=resolution_name)
                task_id=digest(spec)
                tasks.append({'spec':spec,'id':task_id,'objects':tuple(objects_from_icon_item(icon,scene)),
                              'target':target,'temp':None})
    assert_unique_paths([t['target'] for t in tasks])
    return tasks


def make_guided_tasks(scene, settings):
    """Freeze simple-mode options without destroying expert palettes/presets."""
    from .guided_state import guided_pairs, guided_resolutions
    return make_tasks(scene, settings, pairs_override=guided_pairs(settings),
                      resolutions_override=guided_resolutions(scene,settings), file_format='PNG',
                      current_by_model=settings.studio.guide_export_selection=='CURRENT')


def saved_source_fingerprint(scene):
    # Never reuse files based on filenames or an incomplete geometry fingerprint.
    # Dirty/unsaved scenes get a fresh job identity; safe resume requires an exact,
    # clean saved .blend snapshot plus the full frozen task specification.
    path=bpy.data.filepath
    if not path or bpy.data.is_dirty or not Path(path).is_file():return None
    # Enumerate and hash external dependencies as well. Missing, sequence/UDIM
    # patterns, dynamic caches and an unavailable enumerator disable reuse.
    enumerate_paths=getattr(bpy.utils, 'blend_paths', None)
    if enumerate_paths is None: return None
    try:
        external=sorted(set(enumerate_paths(absolute=True, packed=True, local=False)))
        dependencies=[]
        for name in external:
            dependency=Path(name)
            if not dependency.is_file(): return None
            dependencies.append((str(dependency.resolve()), file_digest(dependency)))
        for image in bpy.data.images:
            if getattr(image, 'source', '') in {'SEQUENCE','MOVIE','TILED'}:
                return None
        return {'blend':str(Path(path).resolve()),'sha256':file_digest(path),
                'scene':scene.name, 'dependencies':dependencies,
                'blender':bpy.app.version_string, 'ocio':os.environ.get('OCIO','')}
    except (OSError, ValueError, RuntimeError, TypeError):
        return None


def job_signature(scene,settings,tasks):
    source=saved_source_fingerprint(scene)
    if source is None:return None
    from .constants import VERSION_STRING
    return digest({'source':source,'tasks':[t['spec'] for t in tasks],
                   'version':VERSION_STRING,'film':settings.transparency,'force':settings.force_show_target})


def _valid_file(path,width,height,file_format):
    if not path.is_file() or path.stat().st_size<16:raise RuntimeError('Render output is missing or empty')
    if file_format=='PNG':
        from .png_validation import validate_png
        validate_png(path,width,height)
    else:
        # Let Blender decode non-PNG formats rather than inventing partial parsers.
        image=bpy.data.images.load(str(path),check_existing=False)
        try:
            if tuple(image.size[:2])!=(width,height):raise RuntimeError('Output dimensions mismatch')
        finally:bpy.data.images.remove(image)


class ExportJob(RenderJob):
    def __init__(self,scene,settings,resume=False,guided=False):
        super().__init__(scene,settings)
        self.guided=guided
        self.guided_transparent=bool(settings.studio.guide_transparent) if guided else None
        from .export_prepare import bindings
        bindings(scene,settings)
        self.keep_current=guided and settings.studio.guide_export_selection=='CURRENT'
        errors,warnings=check_scene(scene,settings,False,output_format="PNG" if guided else None,require_colors=not self.keep_current)
        if errors:raise ValueError('\n'.join(errors))
        self.tasks=make_guided_tasks(scene,settings) if guided else make_tasks(scene,settings)
        self.root=Path(bpy.path.abspath(settings.output_folder)).resolve()
        self.manifest=self.root/MANIFEST;self.lock=OutputLock(self.root)
        self.signature=job_signature(scene,settings,self.tasks)
        if guided and self.signature is not None:
            self.signature=digest({"source":self.signature,"guided_png_alpha":self.guided_transparent})
        self.reuse_enabled=bool(resume or settings.skip_existing) and self.signature is not None
        self.overwrite=bool(settings.studio.overwrite_outputs)
        self.records={r.get('task_id'):r for r in read_manifest(self.manifest)}
        self.run_id=uuid.uuid4().hex
        self.all_objects=all_icon_objects(settings,scene,False)
        self.format="PNG" if guided else scene.render.image_settings.file_format
        self.failures=0;self.completed_count=0;self.skipped_count=0
        existing=[t for t in self.tasks if t['target'].exists() and not (self.reuse_enabled and
                  reusable(self.records.get(t['id'],{}),self.signature,t['target']))]
        if existing and not self.overwrite:
            raise ValueError('{} output file(s) already exist without a matching verified checkpoint. Choose a new folder or explicitly enable Replace Existing Files.'.format(len(existing)))

    def start(self):
        self.lock.acquire()
        try:
            super().start()
            self.settings.studio.last_manifest=str(self.manifest)
            append_record(self.manifest,{'status':'JOB_START','run_id':self.run_id,'signature':self.signature,
                'tasks':len(self.tasks),'blender':bpy.app.version_string,'verified_reuse':self.reuse_enabled})
        except Exception:
            self.lock.release();raise

    def skip(self,task):
        if self.reuse_enabled and reusable(self.records.get(task['id'],{}),self.signature,task['target']):
            self.skipped_count+=1
            self.settings.studio.export_progress='Verified '+str(task['target'])
            return True
        return False

    def prepare(self,task):
        self.guard.reset_materials()
        self.guard.visibility(task['objects'],self.all_objects,self.settings.force_show_target)
        spec=task['spec'];allowed=material_pointers_for_objects(task['objects'])
        changed,failed=(1,[]) if self.keep_current else apply_family_colors(self.settings,spec['main'],spec['accent'],allowed)
        if failed:raise RuntimeError('Invalid color target(s): '+', '.join(failed))
        if not changed:raise RuntimeError('No managed target changed for '+spec['icon'])
        render=self.scene.render
        if self.guided:
            render.image_settings.file_format='PNG'
            render.image_settings.color_mode='RGBA' if self.guided_transparent else 'RGB'
            render.image_settings.color_depth='8'
        render.resolution_x=spec['width'];render.resolution_y=spec['height'];render.resolution_percentage=100
        if getattr(self.settings.studio,'rig_auto_frame',False):
            from .studio_rig import frame
            frame(self.scene,self.settings,task['objects'])
        render.use_file_extension=True
        render.film_transparent=True if self.settings.transparency=='ON' else False if self.settings.transparency=='OFF' else self.guard.render['film_transparent']
        if self.guided:render.film_transparent=self.guided_transparent
        target=task['target'];target.parent.mkdir(parents=True,exist_ok=True)
        task['temp']=target.with_name('.cp_'+self.run_id+'_'+str(spec['index'])+target.suffix)
        render.filepath=str(task['temp'])
        self.settings.studio.export_progress='Rendering {}/{}: {}'.format(self.cursor+1,len(self.tasks),target.name)

    def completed(self,task):
        spec=task['spec'];temp=task['temp'];target=task['target']
        _valid_file(temp,spec['width'],spec['height'],self.format)
        with open(temp, 'rb+') as written:
            os.fsync(written.fileno())
        size=temp.stat().st_size;sha=file_digest(temp)
        if self.overwrite:
            os.replace(temp,target)
        else:
            # Atomic no-clobber publication on the same filesystem.
            os.link(temp,target);temp.unlink()
        row={'status':'DONE','run_id':self.run_id,'signature':self.signature,'task_id':task['id'],
             'bytes':size,'sha256':sha,**spec}
        append_record(self.manifest,row)
        self.completed_count+=1
        self.settings.last_success_index=spec['index']

    def failed(self,task,error):
        self.failures+=1
        append_record(self.manifest,{'status':'FAILED','run_id':self.run_id,'signature':self.signature,
            'task_id':task['id'],'index':task['spec']['index'],'error':str(error)})

    def finish(self,success):
        was_closed=self.closed
        errors=super().finish(success)
        if was_closed or not self.closed:return errors
        try:
            for task in self.tasks:
                temp=task.get('temp')
                if temp and temp.exists():temp.unlink()
            if self.started:
                append_record(self.manifest,{'status':'JOB_COMPLETE' if success and not errors and not self.failures else 'JOB_STOPPED',
                    'run_id':self.run_id,'rendered':self.completed_count,'verified':self.skipped_count,
                    'failed':self.failures,'restoration_errors':errors})
        finally:self.lock.release()
        self.settings.studio.export_progress='{} rendered, {} verified, {} failed; scene restored'.format(
            self.completed_count,self.skipped_count,self.failures)
        if self.guided:
            st=self.settings.studio
            st.guide_export_success=bool(success and not errors and not self.failures)
            st.guide_export_count=self.completed_count+self.skipped_count
            st.guide_export_folder=str(self.root)
        return errors

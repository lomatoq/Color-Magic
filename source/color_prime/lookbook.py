"""Named look proposals, rendered comparison, bounded render-guided correction."""
import json
import math
from pathlib import Path
import tempfile
import uuid
import bpy
from .color_math import linear_rgb_to_oklab,oklab_to_oklch,gamut_map_oklch
from .export_kernel import digest
from .render_session import RenderJob
from .runtime import apply_family_colors,material_pointers_for_objects,apply_preview
from .utils import all_icon_objects,objects_from_icon_item,active_item
from .look_metrics import evaluate,correct_color,refinement_is_better


def _variant(color,lightness,chroma):
    L,C,h=oklab_to_oklch(linear_rgb_to_oklab(color[:3]))
    return (*gamut_map_oklch((max(.05,min(.95,L+lightness)),C*chroma,h)),1.)


def _generate(settings):
    studio=settings.studio
    pairs=[]
    if settings.reference_pairs:
        for p in settings.reference_pairs:
            pairs.append((p.name,tuple(p.main_color),tuple(p.accent_color),p.variant,p.reason))
    else:
        main=active_item(settings.main_colors,settings.main_color_index)
        accent=active_item(settings.accent_colors,settings.accent_color_index)
        if main is None or accent is None:raise ValueError('Run Auto Setup or add Main and Accent colors first')
        m,a=tuple(main.color),tuple(accent.color)
        pairs=[('Your Palette',m,a,'CUSTOM','Exact current palette'),
               ('Soft',_variant(m,.06,.85),_variant(a,.04,.9),'DESIGNED','Designed softer variant; not extracted from a reference'),
               ('Deep',_variant(m,-.08,1.08),_variant(a,.02,1.12),'DESIGNED','Designed value contrast; original hue families retained')]
    if studio.generate_inverted and pairs:
        p=pairs[0]
        pairs.append(('Inverted Palette',p[2],p[1],'INVERTED','Swapped palette colors; part assignments and locks unchanged'))
    old_images=[look.image for look in studio.looks if look.image]
    if studio.sheet: old_images.append(studio.sheet)
    studio.sheet=None
    studio.looks.clear()
    seen=set()
    for name,main,accent,variant,reason in pairs[:6]:
        key=tuple(round(v,5) for v in main+accent)
        if key in seen:continue
        seen.add(key)
        item=studio.looks.add();item.uid=uuid.uuid4().hex;item.name=name
        item.main_color=main;item.accent_color=accent;item.variant=variant;item.reason=reason
    studio.look_index=0;studio.batch_from_looks=True
    # Only clean images created by Studio and with no remaining user.
    for image in old_images:
        if image.users==0 and image.get('cp_studio_internal',False):bpy.data.images.remove(image)
    return len(studio.looks)


def generate(settings):
    previous = settings.suppress_callbacks
    kept = [(x.uid, x.name, tuple(x.main_color), tuple(x.accent_color), x.reason, x.enabled)
            for x in settings.studio.looks if getattr(x, 'user_edited', False)]
    settings.suppress_callbacks = True
    try:
        _generate(settings)
        for uid, name, main, accent, reason, enabled in kept:
            item = settings.studio.looks.add()
            item.uid = uid
            item.name = name
            item.main_color, item.accent_color = main, accent
            item.reason = reason or 'Your edited color pair, preserved during regeneration'
            item.variant = 'CUSTOM'
            item.enabled = enabled
            item.user_edited = True
        return len(settings.studio.looks)
    finally:
        settings.suppress_callbacks = previous


def selected(settings):
    return active_item(settings.studio.looks,settings.studio.look_index)


def preview(scene,settings,index=None):
    if index is not None:settings.studio.look_index=index
    look=selected(settings)
    if look is None:raise ValueError('Generate Looks first')
    return apply_preview(scene,settings,look.main_color,look.accent_color)


class LookJob(RenderJob):
    def __init__(self,scene,settings):
        super().__init__(scene,settings)
        from .preflight import check_scene
        errors,warnings=check_scene(scene,settings,preview=True)
        if errors:raise ValueError('\n'.join(errors))
        if not settings.studio.looks:generate(settings)
        icon=active_item(settings.icon_sets,settings.icon_set_index)
        if icon is None:raise ValueError('Select an icon set to compare')
        self.objects=objects_from_icon_item(icon,scene)
        self.all_objects=all_icon_objects(settings,scene,False)
        self.allowed=material_pointers_for_objects(self.objects)
        self.folder=Path(tempfile.mkdtemp(prefix='color_prime_lookbook_'))
        self.mask=None;self.mask_size=None;self.mask_warnings=[]
        import os
        if os.environ.get('OCIO') or scene.display_settings.display_device != 'sRGB':
            self.mask_warnings.append('Non-standard color management: numeric calibration disabled')
        self.measured=[];self.refinement_added=False
        self.tasks=[{'uid':l.uid,'main':tuple(l.main_color),'accent':tuple(l.accent_color),
                     'desired_main':tuple(l.main_color),'desired_accent':tuple(l.accent_color),
                     'name':l.name,'path':str(self.folder/(l.uid+'.png')),'refinement':False}
                    for l in settings.studio.looks]

    def start(self):
        super().start();self.settings.studio.preview_running=True

    def prepare(self,task):
        self.guard.reset_materials()
        self.guard.visibility(self.objects,self.all_objects,self.settings.force_show_target)
        self.guard.configure_preview(self.settings.studio.preview_size,self.settings.studio.preview_samples)
        if getattr(self.settings.studio,'rig_auto_frame',False):
            from .studio_rig import frame
            frame(self.scene,self.settings,self.objects)
        changed,failed=apply_family_colors(self.settings,task['main'],task['accent'],self.allowed)
        if failed:raise RuntimeError('Unwritable color targets: '+', '.join(failed))
        if not changed:raise RuntimeError('No writable Main/Accent targets in this icon')
        self.scene.render.filepath=task['path']
        self.settings.studio.preview_progress='Rendering {} ({}/{})'.format(task['name'],self.cursor+1,len(self.tasks))
        if self.mask is None:
            w,h=self.scene.render.resolution_x,self.scene.render.resolution_y
            grid=self.settings.studio.ray_grid;factor=grid/max(w,h)
            self.mask_size=(max(8,round(w*factor)),max(8,round(h*factor)))
            try:
                from .camera_evidence import role_mask
                self.mask,more_warnings=role_mask(self.scene,self.settings,*self.mask_size)
                self.mask_warnings.extend(more_warnings)
            except Exception as exc:
                # A gallery still works when a camera/mesh cannot be scored.
                self.mask=[0]*(self.mask_size[0]*self.mask_size[1]);self.mask_warnings=[str(exc)]

    def _lookup(self,uid):
        return next((l for l in self.settings.studio.looks if l.uid==uid),None)

    def completed(self,task):
        if not Path(task['path']).is_file():raise RuntimeError('Blender did not produce the expected preview PNG')
        image=bpy.data.images.load(task['path'],check_existing=False)
        image.name='CP Look — '+task['name'];image['cp_studio_internal']=True
        image.pack()
        raw=image.pixels[:];w,h=map(int,image.size[:2])
        metric=evaluate(w,h,raw,*self.mask_size,self.mask,self.settings.studio.background,
                        task['desired_main'],task['desired_accent'],encoded=not image.is_float)
        metric['warnings'].extend(self.mask_warnings)
        if task['refinement']:
            parent=self._lookup(task['parent_uid'])
            if parent is None or not refinement_is_better(task['parent_metric'],metric):
                bpy.data.images.remove(image);return
            look=self.settings.studio.looks.add();look.uid=task['uid'];look.name=task['name']
            look.main_color=task['main'];look.accent_color=task['accent'];look.variant='REFINED'
            look.reason='One bounded correction improved measured reference fit without reducing readability'
        else:
            look=self._lookup(task['uid'])
            if look is None:
                bpy.data.images.remove(image);raise RuntimeError('Look list changed during rendering')
            if tuple(look.main_color)!=task['main'] or tuple(look.accent_color)!=task['accent']:
                bpy.data.images.remove(image);raise RuntimeError('Look colors changed during rendering; snapshot was discarded')
        old=look.image;look.image=image
        if old and old.users==0 and old.get('cp_studio_internal',False):bpy.data.images.remove(old)
        look.score=metric['score'] if not metric['warnings'] else -1.
        look.measurements_json=json.dumps(metric,ensure_ascii=False)
        look.rendered_signature=digest({'main':task['main'],'accent':task['accent'],'frame':self.scene.frame_current,'camera':self.scene.camera.name})
        self.measured.append((task,metric))
        # Schedule at most ONE extra render, only when both roles were measurable.
        if (not self.refinement_added and not task['refinement'] and self.cursor==len(self.tasks)-1
            and self.settings.studio.refine_best):
            self.refinement_added=True
            valid=[(t,m) for t,m in self.measured if not m['warnings']]
            if valid:
                best,measurement=max(valid,key=lambda tm:tm[1]['score'])
                if measurement['reference_error']>.025:
                    uid=uuid.uuid4().hex
                    self.tasks.append({'uid':uid,'name':best['name']+' Refined',
                        'main':correct_color(best['main'],best['desired_main'],measurement['main_lab']),
                        'accent':correct_color(best['accent'],best['desired_accent'],measurement['accent_lab']),
                        'desired_main':best['desired_main'],'desired_accent':best['desired_accent'],
                        'path':str(self.folder/(uid+'.png')),'refinement':True,
                        'parent_uid':best['uid'],'parent_metric':measurement})
                    self.settings.render_total=len(self.tasks)

    def finish(self,success):
        was_closed=self.closed
        errors=super().finish(success)
        if was_closed or not self.closed:return errors
        if success and not errors:
            try:
                from .contact_sheet import compose
                looks=[l for l in self.settings.studio.looks if l.image]
                if looks:
                    w,h,pixels=compose([l.image for l in looks],looks,self.settings.studio.background)
                    sheet=bpy.data.images.new('CP Lookbook — '+self.scene.name,w,h,alpha=True,float_buffer=False)
                    sheet['cp_studio_internal']=True
                    try:sheet.colorspace_settings.name='sRGB'
                    except TypeError:pass
                    sheet.pixels.foreach_set(pixels);sheet.update();sheet.pack()
                    previous=self.settings.studio.sheet;self.settings.studio.sheet=sheet
                    if previous and previous.users==0 and previous.get('cp_studio_internal',False):bpy.data.images.remove(previous)
                    ranked=[(i,l.score) for i,l in enumerate(self.settings.studio.looks) if l.score>=0]
                    if ranked:self.settings.studio.look_index=max(ranked,key=lambda pair:pair[1])[0]
                    preview(self.scene,self.settings)
                self.settings.studio.preview_progress='Comparison ready; scores are advisory snapshots'
            except Exception as exc:
                errors.append('Comparison assembly: '+str(exc))
        import shutil
        shutil.rmtree(self.folder,ignore_errors=True)
        return errors

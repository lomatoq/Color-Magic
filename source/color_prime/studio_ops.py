"""Studio workflow operators; all scene mutations are synchronous on the main thread."""
import json
import bpy
from bpy.props import BoolProperty,EnumProperty,IntProperty
from . import transaction
from .utils import active_item,report_exception,all_icon_objects
from .runtime import restore_preview
from .render_session import active_job,blender_is_rendering


def idle(context):
    scene=getattr(context,'scene',None);s=getattr(scene,'color_prime',None)
    return bool(s is not None and not s.render_running and active_job() is None)


def run_auto(operator,context,redetect=False,before_settings=None):
    from .operators import ensure_resolution_default,seed_palettes_from_detected_scene,reference_image_from_context
    from .discovery import detect_icon_sets,scan_material_bindings,add_icon_root
    from .scene_intelligence import auto_prepare_materials,_set_principled_color
    from .reference_workflow import analyze_reference_image,apply_pair_to_palettes
    from . import lookbook
    scene=context.scene;s=scene.color_prime
    if not idle(context):
        operator.report({'ERROR'},'Finish the current render first');return {'CANCELLED'}
    if s.studio.stage_status!='NONE':
        operator.report({'WARNING'},'A setup is already staged. Accept Parts or Revert Setup first.');return {'CANCELLED'}
    restore_preview(scene)
    before=None
    mats_before=set();groups_before=set()
    try:
        before=transaction.snapshot_settings(s) if before_settings is None else before_settings
        mats_before={m.as_pointer() for m in bpy.data.materials}
        groups_before={g.as_pointer() for g in bpy.data.node_groups}
        if redetect or not s.icon_sets:
            if not s.icon_sets and not redetect:
                roots=set()
                for obj in context.selected_objects:
                    if obj.type not in {'MESH','EMPTY'} or (obj.type=='EMPTY' and getattr(obj,'data',None) is not None):continue
                    root=obj
                    while root.parent is not None:root=root.parent
                    if root.type in {'MESH','EMPTY'}:roots.add(root)
                for root in sorted(roots,key=lambda o:o.name):
                    if root.type=='MESH' or any(o.type=='MESH' for o in root.children_recursive):add_icon_root(s,obj=root)
            if redetect or not s.icon_sets:detect_icon_sets(scene,s,True)
        ensure_resolution_default(s)
        transaction.start(scene,s,before)
        from .model_library import organize
        organize(scene,s)
        from .authored_setup import mark_originals,analyze
        objects=[o for o in all_icon_objects(s,scene,True) if o.type=='MESH']
        mark_originals(s,objects)
        if getattr(s.studio,'create_studio_on_prepare',False):
            from . import studio_rig
            if studio_rig.is_active(s):s.studio.rig_stage_pose_json=json.dumps(studio_rig.snapshot_pose(s))
            created=studio_rig.create(scene,s)
            if created:s.studio.rig_setup_owner=s.studio.stage_id
        # Convert only OUR working copies of legacy non-node flat materials.
        for rec in s.studio.backup_materials:
            mat=rec.staged
            if mat and not mat.use_nodes:_set_principled_color(mat,tuple(mat.diffuse_color))
        if s.auto_prepare_materials:
            bootstrap=auto_prepare_materials(scene,s,objects=[o for o in objects if not o.get('color_prime_authored_slots',False)])
            s.autopilot_last_summary=bootstrap.summary()
        from .material_names import normalize_generated_names
        normalize_generated_names(s)
        result=analyze(scene,s,objects)
        from .family_links import upgrade
        if not s.studio.adoption_pending:upgrade(scene,s)
        seed_palettes_from_detected_scene(s)
        image=reference_image_from_context(context)
        if image is not None:s.reference_image=image
        if s.reference_image:
            analyze_reference_image(s.reference_image,s)
            if s.reference_auto_use and s.reference_pairs:apply_pair_to_palettes(s,s.reference_pairs[0],False)
        lookbook.generate(s)
        if s.auto_preview_after_setup and not any(o.get('color_prime_authored_slots',False) for o in objects):
            from .family_links import set_colors
            item=lookbook.selected(s);set_colors(scene,s,item.main_color,item.accent_color)
        transaction.tag_created(s,mats_before,groups_before)
        s.studio.stage_note=result['summary']+'; originals retained'
        s.last_error=''
        operator.report({'INFO'},'Safe setup ready. Review Parts, render Looks, then Keep Look in Scene.')
        return {'FINISHED'}
    except Exception as exc:
        if s.studio.stage_status!='NONE':
            try:
                transaction.tag_created(s,mats_before,groups_before)
                transaction.rollback(scene,s)
            except Exception as rollback_error:
                s.studio.stage_status='RECOVERY'
                s.studio.stage_note='Setup failed: {}; rollback needs attention: {}'.format(exc,rollback_error)
        elif before is not None:
            s.suppress_callbacks=True
            try:transaction.restore_settings(s,before)
            finally:s.suppress_callbacks=False
        s.last_error=str(exc)
        report_exception(operator,'Setup stopped and rollback attempted',exc)
        return {'CANCELLED'}


class COLORPRIME_OT_stage_action(bpy.types.Operator):
    bl_idname='color_prime.stage_action';bl_label='Stage Action';bl_options={'REGISTER','UNDO'}
    action:EnumProperty(items=(('ACCEPT','Accept Parts','Keep prepared materials'),('REVERT','Revert Setup','Restore original meshes, material slots and settings')),default='ACCEPT')
    @classmethod
    def poll(cls,context):return idle(context) and context.scene.color_prime.studio.stage_status!='NONE'
    def execute(self,context):
        s=context.scene.color_prime
        try:
            if self.action=='REVERT':transaction.rollback(context.scene,s)
            else:
                transaction.accept(context.scene,s)
                from .lookbook import preview
                if s.studio.looks:preview(context.scene,s)
            self.report({'INFO'},s.studio.stage_note);return {'FINISHED'}
        except Exception as exc:report_exception(self,'Stage action failed',exc);return {'CANCELLED'}
    def invoke(self,context,event):
        if self.action=='REVERT':return context.window_manager.invoke_confirm(self,event)
        return self.execute(context)


class COLORPRIME_OT_generate_looks(bpy.types.Operator):
    bl_idname='color_prime.generate_looks';bl_label='Generate Looks';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):return idle(context)
    def execute(self,context):
        try:
            from .lookbook import generate
            count=generate(context.scene.color_prime)
            self.report({'INFO'},'{} named looks generated; render to compare on the model'.format(count));return {'FINISHED'}
        except Exception as exc:report_exception(self,'Look generation failed',exc);return {'CANCELLED'}


class COLORPRIME_OT_select_look(bpy.types.Operator):
    bl_idname='color_prime.select_look';bl_label='Preview Look';bl_options={'REGISTER'}
    index:IntProperty(default=-1)
    @classmethod
    def poll(cls,context):return idle(context) and bool(context.scene.color_prime.studio.looks)
    def execute(self,context):
        try:
            from .lookbook import preview
            preview(context.scene,context.scene.color_prime,self.index if self.index>=0 else None)
            return {'FINISHED'}
        except Exception as exc:report_exception(self,'Look preview failed',exc);return {'CANCELLED'}


class COLORPRIME_OT_bake_look(bpy.types.Operator):
    bl_idname='color_prime.bake_look';bl_label='Keep Look in Scene';bl_options={'REGISTER','UNDO'}
    bl_description='Accept prepared parts and make the chosen colors the saved scene baseline (Undo supported)'
    @classmethod
    def poll(cls,context):return idle(context) and bool(context.scene.color_prime.studio.looks)
    def execute(self,context):
        from .lookbook import selected,preview
        from . import runtime
        from .discovery import recapture_binding
        s=context.scene.color_prime
        try:
            look=selected(s)
            if s.studio.stage_status=='RECOVERY':
                raise RuntimeError('Revert the incomplete setup before keeping a look')
            # Validate/apply while structural backup and preview snapshots still exist.
            from .family_links import ready,parent,shared_socket
            shared=ready(s)
            if not shared:preview(context.scene,s)
            before=transaction.snapshot_settings(s)
            s.suppress_callbacks=True
            try:
                s.main_reference_color=shared_socket(parent(s,'MAIN')).default_value if shared else look.main_color
                s.accent_reference_color=shared_socket(parent(s,'ACCENT')).default_value if shared else look.accent_color
                for b in s.bindings:
                    if b.family in {'MAIN','ACCENT'}:recapture_binding(b,s)
            except Exception:
                transaction.restore_settings(s,before)
                raise
            finally:
                s.suppress_callbacks=False
            runtime._PREVIEWS.pop(context.scene.as_pointer(),None)
            s.studio.preview_values.clear();s.preview_active=False
            if s.studio.stage_status=='STAGED':transaction.accept(context.scene,s)
            self.report({'INFO'},'Look kept in scene. Save the .blend to retain it.');return {'FINISHED'}
        except Exception as exc:report_exception(self,'Keep Look failed',exc);return {'CANCELLED'}
    def invoke(self,context,event):return context.window_manager.invoke_confirm(self,event)


class COLORPRIME_OT_assign_selected(bpy.types.Operator):
    bl_idname='color_prime.assign_selected';bl_label='Assign Selected Parts';bl_options={'REGISTER','UNDO'}
    family:EnumProperty(items=(('MAIN','Main',''),('ACCENT','Accent',''),('FIXED','Fixed','')),default='MAIN')
    @classmethod
    def poll(cls,context):return idle(context) and getattr(context,'mode','OBJECT')=='OBJECT' and bool(context.selected_objects)
    def execute(self,context):
        from .discovery import scan_material_bindings
        from .scene_intelligence import _new_family_material
        from .targets import find_material_target
        s=context.scene.color_prime
        if s.studio.stage_status!='STAGED':
            self.report({'ERROR'},'Run Auto Setup first; corrections are made on its safe working copies.');return {'CANCELLED'}
        restore_preview(context.scene)
        scope={o.as_pointer() for o in all_icon_objects(s,context.scene,True)};count=0
        try:
            for obj in context.selected_objects:
                if obj.type!='MESH' or obj.as_pointer() not in scope:continue
                assigned=False
                for slot in obj.material_slots:
                    mat=slot.material
                    if mat is None or transaction.hard_protected(mat,s):continue
                    if self.family!='FIXED' and not find_material_target(mat,False).writable:continue
                    copy=mat.copy();copy[transaction.OWNER_KEY]=s.studio.stage_id
                    copy['color_prime_family']=self.family;copy['color_prime_family_source']='MANUAL';copy['color_prime_locked']=True
                    from .family_links import ready,connect,disconnect
                    if ready(s):
                        if self.family in {'MAIN','ACCENT'}:connect(copy,s,self.family)
                        else:disconnect(copy)
                    slot.material=copy;assigned=True;count+=1
                if assigned:
                    obj['color_prime_family']=self.family;obj['color_prime_family_source']='MANUAL'
            scan_material_bindings(context.scene,s,False)
            from .child_materials import _refresh
            _refresh(context)
            self.report({'INFO'},'{} material slot(s) assigned and locked; ! materials untouched'.format(count))
            return {'FINISHED'}
        except Exception as exc:report_exception(self,'Part correction failed; Revert Setup is available',exc);return {'CANCELLED'}


class COLORPRIME_OT_open_sheet(bpy.types.Operator):
    bl_idname='color_prime.open_sheet';bl_label='Open Comparison'
    @classmethod
    def poll(cls,context):return bool(context.scene.color_prime.studio.sheet) and bool(context.area)
    def execute(self,context):
        image=context.scene.color_prime.studio.sheet
        context.scene.color_prime.studio.comparison_return_space=context.area.type
        context.area.type='IMAGE_EDITOR';context.area.spaces.active.image=image
        context.area.spaces.active.show_region_ui=True
        self.report({'INFO'},'Real render comparison. Shift+F5 returns this area to the 3D View.');return {'FINISHED'}


class COLORPRIME_OT_preflight(bpy.types.Operator):
    bl_idname='color_prime.studio_preflight';bl_label='Check Export'
    @classmethod
    def poll(cls,context):return idle(context)
    def execute(self,context):
        from .preflight import report
        errors,warnings,lines=report(context.scene,context.scene.color_prime)
        text=bpy.data.texts.get('Color Prime Preflight') or bpy.data.texts.new('Color Prime Preflight')
        text.clear();text.write('\n'.join(lines))
        self.report({'ERROR'} if errors else {'INFO'},'{} errors, {} warnings. Details in Color Prime Preflight text.'.format(len(errors),len(warnings)))
        return {'FINISHED'}


class COLORPRIME_OT_selftest(bpy.types.Operator):
    bl_idname='color_prime.selftest';bl_label='Run Blender Self-test'
    render_test:BoolProperty(name='Include real scratch renders',default=False)
    bl_description='Create a scratch scene, test material protection, staging and rollback, then delete only test data'
    @classmethod
    def poll(cls,context):return idle(context)
    def execute(self,context):
        from .selftest import run
        s=context.scene.color_prime
        result=run(render=self.render_test)
        scope='RENDER + CORE' if self.render_test else 'CORE ONLY'
        s.studio.selftest_status=(scope+' PASS in ' if result['ok'] else scope+' FAIL in ')+bpy.app.version_string
        s.studio.selftest_report=json.dumps(result,ensure_ascii=False,indent=2)
        text=bpy.data.texts.get('Color Prime Self-test') or bpy.data.texts.new('Color Prime Self-test')
        text.clear();text.write(s.studio.selftest_report)
        self.report({'INFO'} if result['ok'] else {'ERROR'},s.studio.selftest_status+' — details in Color Prime Self-test')
        return {'FINISHED'}


class COLORPRIME_OT_studio_render(bpy.types.Operator):
    bl_idname='color_prime.studio_render';bl_label='Render Color Prime'
    mode:EnumProperty(items=(('LOOKS','Render Comparison',''),('EXPORT','Render All','')),default='LOOKS')
    resume:BoolProperty(default=False)
    guided:BoolProperty(default=False, options={'SKIP_SAVE'})
    _timer=None;_job=None;_wm=None
    @classmethod
    def poll(cls,context):return idle(context) and not blender_is_rendering()
    def execute(self,context):
        try:
            if self.mode=='LOOKS':
                from .lookbook import LookJob
                self._job=LookJob(context.scene,context.scene.color_prime)
            else:
                from .batch_export import ExportJob
                self._job=ExportJob(context.scene,context.scene.color_prime,self.resume,guided=self.guided)
            if bpy.app.background:
                from .render_session import run_sync
                run_sync(self._job);return {'FINISHED'}
            self._job.start();self._wm=context.window_manager
            self._timer=self._wm.event_timer_add(.15,window=context.window)
            self._wm.modal_handler_add(self)
            self._wm.progress_begin(0,len(self._job.tasks))
            return {'RUNNING_MODAL'}
        except Exception as exc:
            if self._job:self._job.finish(False)
            context.scene.color_prime.last_error=str(exc)
            report_exception(self,'Render could not start',exc);return {'CANCELLED'}
    def invoke(self,context,event):
        if self.mode=='EXPORT' and self.guided:
            from .preflight import check_scene
            from .batch_export import make_guided_tasks
            s=context.scene.color_prime
            try:
                from .export_prepare import bindings
                bindings(context.scene,s)
                errors,_=check_scene(context.scene,s,False,output_format='PNG',require_colors=s.studio.guide_export_selection!='CURRENT')
                if errors:raise ValueError('\n'.join(errors))
                make_guided_tasks(context.scene,s)
            except Exception as exc:
                s.last_error=str(exc)
                self.report({'ERROR'},str(exc));return {'CANCELLED'}
            return context.window_manager.invoke_props_dialog(self,width=420)
        return self.execute(context)
    def draw(self,context):
        if not self.guided:return
        from .guided_state import export_count,export_dimensions,tr
        from .studio_ui import message
        s=context.scene.color_prime
        w,h=export_dimensions(context.scene,s.studio.guide_export_size)
        self.layout.label(text=tr(s,'Export {} PNG image(s)'.format(export_count(s)),
            'Экспарт: {} PNG'.format(export_count(s))),icon='RENDER_STILL')
        self.layout.label(text='{} x {} px'.format(w,h))
        message(self.layout,bpy.path.abspath(s.output_folder),width=54)
        message(self.layout,tr(s,'Existing files are protected unless replacement is enabled in Advanced.',
            'Існыя файлы абароненыя, калі перазапіс не ўключаны ў пашыраных наладах.'),width=54)
        message(self.layout,tr(s,'OK starts rendering. Cancel makes no scene changes.',
            'OK запускае рэндэр. Cancel нічога не мяняе.'),width=54)

    def _finish(self,success):
        errors=self._job.finish(success)
        if self._timer and self._wm:
            self._wm.event_timer_remove(self._timer);self._timer=None
            self._wm.progress_end()
        if errors:self.report({'ERROR'},'Restoration needs attention: '+'; '.join(errors[:3]));return {'CANCELLED'}
        failed=getattr(self._job,'failures',0)
        if failed:
            self.report({'WARNING'},'Queue ended with {} failed task(s); see manifest. Scene restored.'.format(failed))
            return {'CANCELLED'}
        self.report({'INFO'},'Render completed; scene restored' if success else 'Render stopped; scene restored')
        return {'FINISHED'} if success else {'CANCELLED'}
    def modal(self,context,event):
        job=self._job
        if event.type=='ESC':job.stop_requested=True
        if event.type!='TIMER':return {'PASS_THROUGH'}
        if context.scene!=job.scene:job.stop_requested=True
        try:
            job.stop_requested=job.stop_requested or bool(job.settings.render_stop_requested)
            job.paused=bool(job.settings.render_paused)
            finished=job.step()
            if self._wm:self._wm.progress_update(job.cursor)
            if context.area:context.area.tag_redraw()
            if finished:return self._finish(not job.stop_requested)
        except Exception as exc:
            job.settings.last_error=str(exc)
            if self.mode=='EXPORT' and job.cursor<len(job.tasks):
                try:job.failed(job.tasks[job.cursor],exc)
                except Exception:pass
                if job.settings.error_policy=='SKIP' and not job.waiting:
                    job.cursor+=1;self.report({'WARNING'},str(exc));return {'RUNNING_MODAL'}
            self.report({'ERROR'},str(exc));return self._finish(False)
        return {'RUNNING_MODAL'}
    def cancel(self,context):
        if self._job:
            self._job.stop_requested=True
            if not blender_is_rendering():self._finish(False)



class COLORPRIME_OT_demo(bpy.types.Operator):
    bl_idname='color_prime.create_demo';bl_label='Create Demo Scene';bl_options={'REGISTER','UNDO'}
    bl_description='Add an isolated chest icon, synthetic reference, lights and camera. Existing scenes are not cleared.'
    @classmethod
    def poll(cls,context):return idle(context) and context.window is not None
    def execute(self,context):
        try:
            from .demo import create
            create(context,self)
            context.scene.color_prime.studio.guide_step='COLORS'
            self.report({'INFO'},'Demo scene ready. Render Comparison to compare actual looks.');return {'FINISHED'}
        except Exception as exc:
            report_exception(self,'Demo setup failed; original scene was not cleared',exc);return {'CANCELLED'}

CLASSES=(COLORPRIME_OT_demo,COLORPRIME_OT_stage_action,COLORPRIME_OT_generate_looks,COLORPRIME_OT_select_look,
         COLORPRIME_OT_bake_look,COLORPRIME_OT_assign_selected,COLORPRIME_OT_open_sheet,
         COLORPRIME_OT_preflight,COLORPRIME_OT_selftest,COLORPRIME_OT_studio_render)

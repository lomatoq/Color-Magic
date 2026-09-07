"""One render owner, explicit source scene, asynchronous UI and sync CI entry.

Completion handlers only set flags. Image reads, atomic publication and cleanup
happen on a later main-thread timer tick, after Blender's render job has ended.
"""
import time
import bpy
from .scene_guard import SceneGuard
from .runtime import restore_preview
from .utils import log

_ACTIVE=None


def active_job():
    return _ACTIVE


def blender_is_rendering():
    fn=getattr(bpy.app,'is_job_running',None)
    return bool(fn('RENDER')) if fn else False


class RenderJob:
    def __init__(self,scene,settings):
        self.scene=scene;self.settings=settings;self.guard=None
        self.tasks=[];self.cursor=0;self.waiting=False;self.done_event=False;self.cancel_event=False
        self.stop_requested=False;self.paused=False;self.closed=False;self.started=False;self.errors=[]
        self.settle_ticks=0;self.started_at=time.monotonic();self.paths_to_clean=[]

    def start(self):
        from .guide_runtime import cancel_pending
        cancel_pending()
        global _ACTIVE
        if _ACTIVE is not None or blender_is_rendering():
            raise RuntimeError('Another render is already running. Finish it before starting Color Prime.')
        restore_preview(self.scene)
        if self.settings.preview_active:
            raise RuntimeError('Restore or revert the incomplete palette preview before rendering')
        self.guard=SceneGuard(self.scene,self.settings)
        if hasattr(self.scene.render, 'use_lock_interface'):
            self.scene.render.use_lock_interface=True
        _ACTIVE=self;self.started=True
        self.settings.render_running=True
        self.settings.render_total=len(self.tasks);self.settings.render_current=0
        for collection,handler in ((bpy.app.handlers.render_complete,self._complete),
                                    (bpy.app.handlers.render_cancel,self._cancel)):
            if handler not in collection:collection.append(handler)

    def _same(self,scene):
        try:return scene==self.scene
        except ReferenceError:return False

    def _complete(self,scene,*args):
        if self._same(scene):self.done_event=True

    def _cancel(self,scene,*args):
        if self._same(scene):self.cancel_event=True

    def prepare(self,task):
        raise NotImplementedError

    def completed(self,task):
        raise NotImplementedError

    def skip(self,task):
        return False

    def progress(self):
        return '{}/{}'.format(self.cursor,len(self.tasks))

    def step(self):
        """Return True when all resources can be released."""
        if self.waiting:
            if not (self.done_event or self.cancel_event):return False
            if blender_is_rendering():return False
            self.settle_ticks+=1
            if self.settle_ticks<2:return False
            self.waiting=False
            if self.cancel_event:
                self.stop_requested=True;return True
            self.completed(self.tasks[self.cursor]);self.cursor+=1
            self.settings.render_current=self.cursor
        if self.stop_requested:return True
        if self.paused:return False
        while self.cursor<len(self.tasks) and self.skip(self.tasks[self.cursor]):
            self.cursor+=1;self.settings.render_current=self.cursor
        if self.cursor>=len(self.tasks):return True
        self.prepare(self.tasks[self.cursor])
        self.done_event=False;self.cancel_event=False;self.settle_ticks=0;self.waiting=True
        result=bpy.ops.render.render('INVOKE_DEFAULT',write_still=True,scene=self.scene.name)
        if 'RUNNING_MODAL' in result:return False
        if 'FINISHED' in result:
            self.done_event=True;return False
        self.waiting=False
        raise RuntimeError('Blender refused or cancelled the render invocation')

    def finish(self,success):
        global _ACTIVE
        if self.closed:return self.errors
        if self.waiting and blender_is_rendering():
            self.stop_requested=True
            return ['Render still active; cleanup deferred']
        self.closed=True
        for collection,handler in ((bpy.app.handlers.render_complete,self._complete),
                                    (bpy.app.handlers.render_cancel,self._cancel)):
            if handler in collection:collection.remove(handler)
        if self.guard:self.errors.extend(self.guard.close())
        try:
            self.settings.render_running=False;self.settings.render_paused=False;self.settings.render_stop_requested=False
            self.settings.studio.preview_running=False
        except (ReferenceError,AttributeError):pass
        if _ACTIVE is self:_ACTIVE=None
        return self.errors


def run_sync(job):
    """Use in Blender --background. Never fake bpy for this integration gate."""
    ok=False
    try:
        job.start()
        while job.cursor<len(job.tasks):
            task=job.tasks[job.cursor]
            if not job.skip(task):
                job.prepare(task)
                result=bpy.ops.render.render('EXEC_DEFAULT',write_still=True,scene=job.scene.name)
                if 'FINISHED' not in result:raise RuntimeError('Headless render cancelled')
                job.completed(task)
            job.cursor+=1
            job.settings.render_current=job.cursor
        ok=True
        return job
    except Exception as exc:
        if job.started and job.cursor < len(job.tasks) and hasattr(job, 'failed'):
            try: job.failed(job.tasks[job.cursor], exc)
            except Exception: pass
        raise
    finally:
        errors=job.finish(ok)
        if errors:raise RuntimeError('Render restoration failed: '+'; '.join(errors))


def shutdown():
    """Unregister is safe even while an asynchronous render is winding down."""
    job=_ACTIVE
    if job is None:return
    job.stop_requested=True
    def after_render():
        if blender_is_rendering():return .25
        job.waiting=False
        job.finish(False)
        return None
    if blender_is_rendering():
        timers=getattr(bpy.app,'timers',None)
        if timers:timers.register(after_render,first_interval=.25)
    else:
        after_render()

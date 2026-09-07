"""Opt-in, non-blocking release checks. Running Python is never hot-reloaded."""
import concurrent.futures
import bpy
from . import update_core
from .constants import VERSION
from .guided_state import tr

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_future = None
_release = None
_status = 'IDLE'
_detail = ''
_timer_owner = None


def shutdown():
    global _future, _timer_owner, _status
    if _timer_owner:
        manager, timer = _timer_owner
        try: manager.event_timer_remove(timer)
        except (ReferenceError, RuntimeError): pass
        _timer_owner = None
    if _future:
        _future.cancel()
        _future = None
    if _status in {'CHECK', 'DOWNLOAD'}: _status = 'IDLE'


def draw(layout, context, s):
    box = layout.box()
    row = box.row(align=True)
    row.operator('color_prime.update_check', text=tr(s, 'Check updates', 'Праверыць абнаўленні'), icon='FILE_REFRESH')
    row.operator('wm.url_open', text=tr(s, 'Guide', 'Інструкцыя'), icon='HELP').url = 'https://github.com/lomatoq/Color-Magic#readme'
    messages = {
        'CHECK': ('Checking GitHub…', 'Праверка GitHub…'),
        'DOWNLOAD': ('Downloading and checking…', 'Спампоўванне і праверка…'),
        'CURRENT': ('You have the latest stable version.', 'Усталявана апошняя стабільная версія.'),
        'READY': ('Update available: ', 'Даступнае абнаўленне: '),
        'RESTART': ('Installed. Save your work and restart Blender.', 'Усталявана. Захавайце працу і перазапусціце Blender.'),
        'ERROR': ('Update failed. Retry or install the release ZIP manually.', 'Не ўдалося абнавіць. Паўтарыце або ўсталюйце ZIP уручную.'),
    }
    if _status in messages:
        from .guided_ui import lines
        message = tr(s, *messages[_status])
        if _status == 'READY': message += _release['tag']
        lines(box, message, context)
    if _status == 'READY':
        box.operator('color_prime.update_install', text=tr(s, 'Install update · restart required', 'Усталяваць · патрэбны перазапуск'), icon='IMPORT')


def allowed(context):
    from .render_session import active_job, blender_is_rendering
    return _future is None and _status != 'RESTART' and active_job() is None and not blender_is_rendering() and not getattr(getattr(context.scene, 'color_prime', None), 'render_running', False)


class UpdateTask:
    @classmethod
    def poll(cls, context):
        ok = allowed(context)
        if not ok:
            s = getattr(context.scene, 'color_prime', None)
            cls.poll_message_set(tr(s, 'Wait for rendering/updating to finish, or restart after installation.', 'Дачакайцеся завяршэння рэндэру/абнаўлення або перазапусціце пасля ўсталявання.') if s else 'Update unavailable')
        return ok

    def start(self, context, task, status):
        global _future, _status, _detail, _timer_owner
        _detail = ''; _status = status
        _future = _pool.submit(task)
        self._timer = context.window_manager.event_timer_add(.2, window=context.window)
        _timer_owner = (context.window_manager, self._timer)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        global _future, _status, _release, _detail, _timer_owner
        if _future is None: return {'CANCELLED'}
        if event.type != 'TIMER' or not _future.done(): return {'PASS_THROUGH'}
        try:
            value = _future.result()
            if _status == 'CHECK':
                _release = value
                _status = 'READY' if tuple(value['version']) > VERSION else 'CURRENT'
            else:
                from .render_session import active_job, blender_is_rendering
                if active_job() is not None or blender_is_rendering():
                    raise RuntimeError('Render started during download; retry after rendering')
                _detail = update_core.install(value, __import__('pathlib').Path(__file__).parent, _release['version'])
                _status = 'RESTART'
                print('Color Prime update backup:', _detail)
        except Exception as exc:
            _status = 'ERROR'; _detail = str(exc)
            print('Color Prime update:', _detail)
            self.report({'ERROR'}, tr(context.scene.color_prime, 'Update failed; existing installation kept. See system console for details.', 'Абнаўленне не ўдалося; існая ўстаноўка захаваная. Падрабязнасці ў сістэмнай кансолі.'))
        finally:
            _future = None
            context.window_manager.event_timer_remove(self._timer)
            _timer_owner = None
            for window in context.window_manager.windows:
                for area in window.screen.areas: area.tag_redraw()
        return {'FINISHED'}


class COLORPRIME_OT_update_check(UpdateTask, bpy.types.Operator):
    bl_idname = 'color_prime.update_check'
    bl_label = 'Check updates'
    def execute(self, context):
        return self.start(context, update_core.latest, 'CHECK')


class COLORPRIME_OT_update_install(UpdateTask, bpy.types.Operator):
    bl_idname = 'color_prime.update_install'
    bl_label = 'Install update'
    @classmethod
    def poll(cls, context):
        return _status == 'READY' and super().poll(context)
    def execute(self, context):
        return self.start(context, lambda: update_core.fetch_package(_release), 'DOWNLOAD')


CLASSES = (COLORPRIME_OT_update_check, COLORPRIME_OT_update_install)

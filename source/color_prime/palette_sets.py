"""Multiple named palettes per family; the current editable palette stays live."""
import uuid
import bpy
from bpy.props import EnumProperty,StringProperty,IntProperty
from .studio_ops import idle


def _colors(settings,family):return settings.main_colors if family=='MAIN' else settings.accent_colors
def _active_field(family):return 'main_palette_uid' if family=='MAIN' else 'accent_palette_uid'


def _copy(source,target):
    rows=[(c.name,tuple(c.color),c.enabled) for c in source]
    target.clear()
    for name,color,enabled in rows:
        c=target.add();c.name=name;c.color=color;c.enabled=enabled


def save_active(settings,family):
    uid=getattr(settings,_active_field(family))
    entry=next((p for p in settings.palette_sets if p.uid==uid),None)
    if entry:_copy(_colors(settings,family),entry.colors)


def export_colors(settings,family):
    appearances=getattr(settings,'appearances',())
    if appearances and getattr(settings,'appearance_palette_export',True):
        chosen=[p for p in appearances if p.enabled]
        return [(p.uid+str(i),(p.name+' · ' if len(chosen)>1 else '')+c.name,tuple(c.color))
                for p in chosen for i,c in enumerate(p.main_colors if family=='MAIN' else p.accent_colors) if c.enabled]
    palettes=[p for p in settings.palette_sets if p.family==family]
    current=_colors(settings,family)
    if not palettes:return [(str(i),c.name,tuple(c.color)) for i,c in enumerate(current) if c.enabled]
    uid=getattr(settings,_active_field(family));result=[]
    for p in palettes:
        if not p.enabled:continue
        colors=current if p.uid==uid else p.colors
        for i,c in enumerate(colors):
            if c.enabled:result.append((p.uid+str(i),(p.name+' · ' if len(palettes)>1 else '')+c.name,tuple(c.color)))
    return result


def pairs(settings):
    from .export_kernel import paired_indices
    main=export_colors(settings,'MAIN');accent=export_colors(settings,'ACCENT')
    return [(main[i][1],main[i][2],accent[j][1],accent[j][2],main[i][0]+'-'+accent[j][0])
            for i,j in paired_indices(list(range(len(main))),list(range(len(accent))),settings.combination_mode=='MATRIX')]


class COLORPRIME_OT_palette_set(bpy.types.Operator):
    bl_idname='color_prime.palette_set';bl_label='Палитра';bl_options={'REGISTER','UNDO'}
    family:EnumProperty(items=(('MAIN','Main',''),('ACCENT','Accent','')))
    action:EnumProperty(items=(('SAVE','Сохранить в список',''),('NEW','Новая палитра',''),('USE','Редактировать',''),('REMOVE','Удалить из списка','')))
    name:StringProperty(name='Имя палитры',default='Palette')
    index:IntProperty(default=-1)
    @classmethod
    def poll(cls,context):return idle(context)
    def invoke(self,context,event):
        if self.action in {'SAVE','NEW'}:return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)
    def draw(self,context):self.layout.prop(self,'name')
    def execute(self,context):
        s=context.scene.color_prime;save_active(s,self.family)
        old=s.suppress_callbacks;s.suppress_callbacks=True
        try:
            if self.action in {'SAVE','NEW'}:
                if self.action=='NEW' and not any(p.family==self.family for p in s.palette_sets):
                    entry=s.palette_sets.add();entry.uid=uuid.uuid4().hex;entry.family=self.family;entry.name='Original'
                    _copy(_colors(s,self.family),entry.colors)
                entry=s.palette_sets.add();entry.uid=uuid.uuid4().hex;entry.family=self.family;entry.name=self.name
                if self.action=='NEW':
                    target=_colors(s,self.family);target.clear();c=target.add();c.name='Color 1';c.color=s.main_reference_color if self.family=='MAIN' else s.accent_reference_color
                _copy(_colors(s,self.family),entry.colors);setattr(s,_active_field(self.family),entry.uid)
            elif 0<=self.index<len(s.palette_sets):
                entry=s.palette_sets[self.index]
                if self.action=='USE':
                    _copy(entry.colors,_colors(s,entry.family));setattr(s,_active_field(entry.family),entry.uid)
                else:
                    active=getattr(s,_active_field(entry.family))==entry.uid;family=entry.family
                    s.palette_sets.remove(self.index)
                    if active:
                        remaining=next((p for p in s.palette_sets if p.family==family),None)
                        setattr(s,_active_field(family),remaining.uid if remaining else '')
                        if remaining:_copy(remaining.colors,_colors(s,family))
            return {'FINISHED'}
        finally:s.suppress_callbacks=old


def draw(layout,settings,family):
    for i,p in enumerate(settings.palette_sets):
        if p.family!=family:continue
        row=layout.row(align=True);row.prop(p,'enabled',text='');row.prop(p,'name',text='')
        op=row.operator('color_prime.palette_set',text='',icon='CHECKMARK' if p.uid==getattr(settings,_active_field(family)) else 'GREASEPENCIL')
        op.action='USE';op.index=i;op.family=family
        op=row.operator('color_prime.palette_set',text='',icon='X');op.action='REMOVE';op.index=i;op.family=family
    row=layout.row(align=True)
    for action,label in (('SAVE','Сохранить в список…'),('NEW','Новая палитра…')):
        op=row.operator('color_prime.palette_set',text=label);op.action=action;op.family=family


CLASSES=(COLORPRIME_OT_palette_set,)

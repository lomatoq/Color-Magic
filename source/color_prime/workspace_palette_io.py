"""Portable Main/Accent palette colors."""
import json,math
from pathlib import Path
import bpy
from bpy.props import StringProperty,EnumProperty

class COLORPRIME_OT_workspace_palette_file(bpy.types.Operator):
    bl_idname='color_prime.workspace_palette_file';bl_label='Palette JSON';bl_options={'REGISTER','UNDO'}
    action:EnumProperty(items=(('LOAD','Load',''),('SAVE','Save','')))
    filepath:StringProperty(subtype='FILE_PATH')
    filter_glob:StringProperty(default='*.json',options={'HIDDEN'})
    def invoke(self,context,event):
        self.filepath='palette.json';context.window_manager.fileselect_add(self);return {'RUNNING_MODAL'}
    def execute(self,context):
        from .appearance_workspace import active_palette,new_palette
        s=context.scene.color_prime
        try:
            if self.action=='SAVE':
                p=active_palette(s)
                if p is None:raise ValueError('Choose a palette first.')
                data={'format':'ColorPrimePalette','version':1,'name':p.name}
                for key in ('main_colors','accent_colors'):
                    data[key]=[{'name':c.name,'color':list(c.color),'enabled':c.enabled} for c in getattr(p,key)]
                with Path(self.filepath).with_suffix('.json').open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
            else:
                data=json.loads(Path(self.filepath).read_text(encoding='utf-8-sig'))
                if data.get('format')!='ColorPrimePalette':raise ValueError('Choose a Color Prime palette JSON.')
                for key in ('main_colors','accent_colors'):
                    rows=data.get(key)
                    if not isinstance(rows,list) or not 1<=len(rows)<=1024:raise ValueError('Palette must contain 1–1024 colors per family.')
                    for r in rows:
                        color=r.get('color',[])
                        if len(color)!=4 or not all(isinstance(x,(int,float)) and math.isfinite(x) and 0<=x<=1 for x in color):raise ValueError('Invalid RGBA color.')
                p=new_palette(s,str(data.get('name','Palette')))
                for key in ('main_colors','accent_colors'):
                    colors=getattr(p,key);colors.clear()
                    for row in data[key]:
                        c=colors.add();c.name=str(row.get('name','Color'));c.color=row['color'];c.enabled=bool(row.get('enabled',True))
            s.last_error='';return {'FINISHED'}
        except Exception as exc:s.last_error=str(exc);self.report({'ERROR'},str(exc));return {'CANCELLED'}

CLASSES=(COLORPRIME_OT_workspace_palette_file,)

"""Run with blender --background --factory-startup --python this_file."""
import sys
from pathlib import Path
import bpy

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'source'))
import color_prime
color_prime.register()
from color_prime.default_palette import initialize,ensure,HUES
from color_prime.appearance_workspace import new_palette,meshes,apply
from color_prime.workspace_actions import find_zones
initialize()
s=bpy.context.scene.color_prime
assert len(s.appearances)==1
p=s.appearances[0]
assert len(p.main_colors)==len(p.accent_colors)==16
assert p.main_colors[0].color[:]==(1.,0.,0.,1.)
assert p.main_colors[-1].name=='F Fuchsia'
assert len(set(HUES))==16
p.main_colors[0].name='User edit'
ensure(bpy.context.scene)
assert len(s.appearances)==1 and p.main_colors[0].name=='User edit'
custom=new_palette(s,'Custom',{'MAIN':(.2,.3,.4,1),'ACCENT':(.1,.2,.3,1)})
assert len(custom.main_colors)==1
assert bpy.ops.color_prime.sample_scooter()=={'FINISHED'}
assert len(s.icon_sets)==1
obj=meshes(s.icon_sets[0],bpy.context.scene)[0]
assert len(obj.data.polygons)>1000
assert find_zones(bpy.context,s.icon_sets[0])==23
gold=[m for m in obj.data.materials if m and m.get('showreel_gold')]
assert len(gold)==1 and gold[0].get('color_prime_family')=='FIXED'
before=tuple(gold[0].node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value)
apply(bpy.context,custom,list(s.icon_sets),False)
assert tuple(gold[0].node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value)==before
assert bpy.ops.color_prime.sample_scooter()=={'FINISHED'}
assert len(s.icon_sets)==2
print('PASS: 16-color defaults, preserved edits, custom capture, two independent sample imports, 23 zones, fixed gold')
color_prime.unregister()

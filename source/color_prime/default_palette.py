"""The supplied 0–F hue wheel in scene-linear RGB."""
import colorsys

HUES = (0,23,45,65,85,113,138,158,186,202,222,252,270,287,315,338)
NAMES = ('Red','Orange','Amber','Yellow','Lime','Green','Teal','Turquois','Cyan','Aqua','Blue','Indigo','Purple','Magenta','Pink','Fuchsia')
NAME = 'Spectrum 16 · Main × Accent'

def colors():
    def linear(v):return v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4
    return [(format(i,'X')+' '+name,tuple(linear(v) for v in colorsys.hsv_to_rgb(h/360,1,1))+(1,)) for i,(h,name) in enumerate(zip(HUES,NAMES))]

def populate(p):
    for rows in (p.main_colors,p.accent_colors):
        rows.clear()
        for name,color in colors():
            c=rows.add();c.name=name;c.color=color;c.enabled=True
    p.main_index=10;p.accent_index=8
    p.main_base=p.main_colors[10].color;p.accent_base=p.accent_colors[8].color

def ensure(scene):
    s=getattr(scene,'color_prime',None)
    if s is None or s.appearances or scene.get('cp_default_palette_seeded'):return
    from .appearance_workspace import new_palette
    new_palette(s,NAME)
    scene['cp_default_palette_seeded']=True

def initialize():
    import bpy
    for scene in bpy.data.scenes:ensure(scene)
    return None

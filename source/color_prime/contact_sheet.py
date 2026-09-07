"""Assemble REAL render outputs, never synthetic/mock render thumbnails."""
from array import array
import math
from .color_math import linear_to_srgb_channel

# Small built-in bitmap glyphs; no external fonts or imaging dependencies.
_FONT={
'0':['111','101','101','101','111'],'1':['010','110','010','010','111'],
'2':['111','001','111','100','111'],'3':['111','001','111','001','111'],
'4':['101','101','111','001','001'],'5':['111','100','111','001','111'],
'6':['111','100','111','101','111'],'7':['111','001','010','010','010'],
'8':['111','101','111','101','111'],'9':['111','101','111','001','111'],
'-':['000','000','111','000','000'],'.':['000','000','000','000','010'],
'L':['100','100','100','100','111'],'O':['111','101','101','101','111'],
'K':['101','101','110','101','101'],' ':['000']*5}


def compose(images,looks,background):
    """Return width,height,sRGB RGBA floats, bottom-up like Blender Image.pixels."""
    if not images:raise ValueError('There are no rendered looks to compare')
    tile_w=max(int(im.size[0]) for im in images);tile_h=max(int(im.size[1]) for im in images)
    gap,bar=12,36;cols=min(3,len(images));rows=math.ceil(len(images)/cols)
    w=cols*(tile_w+gap)+gap;h=rows*(tile_h+bar+gap)+gap
    bg=tuple(linear_to_srgb_channel(float(v)) for v in background[:3])+(1.,)
    out=array('f',bg)*(w*h)
    def rect(x0,y0,x1,y1,color):
        row=array('f',color)*max(0,x1-x0)
        for y in range(max(0,y0),min(h,y1)):
            out[(y*w+x0)*4:(y*w+x1)*4]=row
    def text(x,y,value):
        for char in value:
            glyph=_FONT.get(char,_FONT[' '])
            for gy,line in enumerate(reversed(glyph)):
                for gx,bit in enumerate(line):
                    if bit=='1':rect(x+gx*3,y+gy*3,x+gx*3+3,y+gy*3+3,(.9,.9,.92,1))
            x+=12
    for idx,(image,look) in enumerate(zip(images,looks)):
        col,row=idx%cols,idx//cols
        x0=gap+col*(tile_w+gap);y0=h-gap-(row+1)*(tile_h+bar)-row*gap
        iw,ih=map(int,image.size[:2]);raw=image.pixels[:]
        ox=x0+(tile_w-iw)//2;oy=y0+bar+(tile_h-ih)//2
        # The sheet is a visual UI comparison; composite sRGB PNG samples using
        # linear light, then encode back to sRGB.
        from .color_math import srgb_to_linear_channel
        for y in range(ih):
            for x in range(iw):
                i=(y*iw+x)*4;j=((oy+y)*w+ox+x)*4;a=max(0,min(1,raw[i+3]))
                for c in range(3):
                    v=srgb_to_linear_channel(raw[i+c])*a+background[c]*(1-a)
                    out[j+c]=linear_to_srgb_channel(v)
                out[j+3]=1.
        text(x0+8,y0+10,'LOOK '+str(idx+1))
        for offset,color in ((tile_w-74,look.main_color),(tile_w-38,look.accent_color)):
            sw=tuple(linear_to_srgb_channel(float(v)) for v in color[:3])+(1.,)
            rect(x0+offset,y0+6,x0+offset+28,y0+28,sw)
    return w,h,out

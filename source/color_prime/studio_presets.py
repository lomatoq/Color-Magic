"""Deterministic camera and lighting presets, expressed in camera coordinates."""
VIEWS={
    'FRONT':('Front',-90.,0.),'BACK':('Back',90.,0.),'LEFT':('Left',180.,0.),'RIGHT':('Right',0.,0.),
    'TOP':('Top',-90.,90.),'BOTTOM':('Bottom',-90.,-90.),
    'THREE_QUARTER':('Three-quarter front right',-45.,28.),'QUARTER_FL':('Three-quarter front left',-135.,28.),
    'QUARTER_BR':('Three-quarter back right',45.,28.),'QUARTER_BL':('Three-quarter back left',135.,28.),
    'ISOMETRIC':('Isometric front right',-45.,35.26438968),'ISO_FL':('Isometric front left',-135.,35.26438968),
    'ISO_BR':('Isometric back right',45.,35.26438968),'ISO_BL':('Isometric back left',135.,35.26438968),
}
VIEW_ITEMS=tuple((key,value[0],'') for key,value in VIEWS.items())+(('CUSTOM','Manual angles',''),)
LIGHT_ITEMS=(('SOFT','Мягкое',''),('EVEN','Равномерное',''),('CONTRAST','Контрастное',''),('SIDE','Боковое',''),('RIM','Контровое',''),('COLOR','Цветное',''))


def lights(frame,settings):
    from .studio_math import softboxes
    base=softboxes(frame);preset=settings.rig_lighting
    factors={'SOFT':(1,1,1),'EVEN':(.8,2.2,.5),'CONTRAST':(1.3,.15,.7),'SIDE':(1.1,.12,.3),'RIM':(.25,.2,1.8),'COLOR':(1,1.5,1)}[preset]
    colors=(settings.rig_key_color,settings.rig_fill_color,settings.rig_rim_color)
    colored=((1.,.24,.08),(.08,.3,1.),(.7,.15,1.))
    rows=[]
    for i,(role,location,size,power,tone) in enumerate(base):
        if preset=='SIDE' and i==0:location=frame.offset(-frame.span*2,frame.span*.4,frame.span*.2)
        tint=colored[i] if preset=='COLOR' else tone
        rows.append((role,location,size*settings.rig_softness,power*factors[i]*settings.rig_intensity,tuple(a*b for a,b in zip(tint,colors[i]))))
    return rows

"""Small, dependency-free camera/softbox geometry. Distances are scene-relative."""
import math
from dataclasses import dataclass


def dot(a, b): return sum(x*y for x,y in zip(a,b))
def add(a, b): return tuple(x+y for x,y in zip(a,b))
def mul(a, k): return tuple(x*k for x in a)


@dataclass(frozen=True)
class StudioFrame:
    target: tuple
    right: tuple
    up: tuple
    back: tuple
    width: float
    height: float
    depth: float
    span: float
    distance: float
    vertical_scale: float

    def offset(self, x, y, z):
        return add(self.target, add(mul(self.right,x),add(mul(self.up,y),mul(self.back,z))))


def fit_frame(points, yaw=-45.0, elevation=28.0, aspect=1.0, margin=.12):
    """Fit all supplied world-space points; does not rotate/scale the object.

    vertical_scale is the *vertical* camera extent. The Blender adapter also
    measures view_frame() so HORIZONTAL/AUTO sensor conventions cannot clip it.
    """
    pts=[tuple(map(float,p)) for p in points]
    if not pts or any(len(p)!=3 or not all(math.isfinite(v) for v in p) for p in pts):
        raise ValueError('The model has no finite 3D bounds.')
    if not math.isfinite(aspect) or aspect<=0:
        raise ValueError('The render aspect ratio must be positive.')
    yaw,elevation=map(math.radians,(yaw,elevation))
    back=(math.cos(yaw)*math.cos(elevation),math.sin(yaw)*math.cos(elevation),math.sin(elevation))
    right=(-math.sin(yaw),math.cos(yaw),0.)
    up=(-math.cos(yaw)*math.sin(elevation),-math.sin(yaw)*math.sin(elevation),math.cos(elevation))
    axes=(right,up,back)
    ranges=[(min(dot(p,a) for p in pts),max(dot(p,a) for p in pts)) for a in axes]
    width,height,depth=(hi-lo for lo,hi in ranges)
    span=max(width,height,depth)
    if span<1e-8: raise ValueError('The selected model has zero-size bounds.')
    target=(0.,0.,0.)
    for axis,(lo,hi) in zip(axes,ranges): target=add(target,mul(axis,(lo+hi)*.5))
    occupancy=1.-2.*max(0.,min(.45,float(margin)))
    scale=max(height,width/aspect,span*.01)/occupancy
    return StudioFrame(target,right,up,back,width,height,depth,span,
                       max(span*3.,depth+span),scale)


def softboxes(frame):
    """Power proportional to length² preserves irradiance when model size changes."""
    r=frame.span*.5
    return (
        ('Key',frame.offset(-2.8*r,3.1*r,3.5*r),1.9*r,850*r*r,(1.,.97,.94)),
        ('Fill',frame.offset(3.1*r,1.0*r,2.3*r),2.6*r,300*r*r,(.94,.97,1.)),
        ('Rim',frame.offset(1.0*r,2.7*r,-2.5*r),1.8*r,1050*r*r,(1.,1.,1.)),
    )

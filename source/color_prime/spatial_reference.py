"""Bounded, deterministic spatial reference preprocessor (pure Python).

A border-colored *connected component* may be excluded. A same-colored enclosed
object is kept. No claim of semantic foreground segmentation. Crop is explicit.
"""
from collections import Counter, deque
import math
from .color_math import linear_rgb_to_oklab


def distance(a,b):
    return math.sqrt(sum((a[i]-b[i])**2 for i in range(3)))


def sample_reference(width,height,pixels,budget=10000,roi=(0,0,1,1),alpha_threshold=.04,
                     remove_border=True,tolerance=.045,force=False):
    if width <= 0 or height <= 0 or len(pixels) != width*height*4:
        raise ValueError('Invalid reference pixel dimensions')
    if len(roi) != 4 or not all(math.isfinite(float(x)) for x in roi):
        raise ValueError('Reference crop must contain four finite values')
    x0,y0,x1,y1 = [max(0.,min(1.,float(x))) for x in roi]
    if x1-x0 < .001 or y1-y0 < .001:
        raise ValueError('Reference crop must have positive width and height')
    cw,ch = max(1,int(width*(x1-x0))), max(1,int(height*(y1-y0)))
    scale = min(1.,math.sqrt(max(16,int(budget))/float(cw*ch)))
    limit = max(16, int(budget))
    w,h = max(1,int(cw*scale)),max(1,int(ch*scale))
    if w*h > limit:
        if w >= h: w = max(1, limit//h)
        else: h = max(1, limit//w)
    data, labs, alpha = [],[],[]
    for y in range(h):
        sy = min(height-1,int((y0+(y+.5)/h*(y1-y0))*height))
        for x in range(w):
            sx = min(width-1,int((x0+(x+.5)/w*(x1-x0))*width))
            offset = (sy*width+sx)*4
            rgba = [float(pixels[offset+c]) for c in range(4)]
            if not all(math.isfinite(v) for v in rgba):
                raise ValueError('Reference contains non-finite pixels')
            data.extend(rgba)
            labs.append(linear_rgb_to_oklab(rgba[:3]))
            alpha.append(rgba[3])
    result = {'width':w,'height':h,'pixels':data,'removed':0,'reason':'alpha / explicit crop only',
              'background_lab':None,'original_visible':sum(a>=alpha_threshold for a in alpha)}
    if not remove_border:
        return result
    border = [i for i in range(w*h) if (i%w in (0,w-1) or i//w in (0,h-1)) and alpha[i]>=alpha_threshold]
    if len(border)<8:
        return result
    # Quantized border voting; require a majority before excluding anything.
    quant = lambda lab:tuple(round(v/.045) for v in lab)
    counts = Counter(quant(labs[i]) for i in border)
    key,count = sorted(counts.items(),key=lambda item:(-item[1],item[0]))[0]
    if count/len(border) < (.2 if force else .4):
        result['reason'] = 'complex border retained; use crop for an intentional subject'
        return result
    anchors = [i for i in border if quant(labs[i])==key]
    target = tuple(sorted(labs[i][c] for i in anchors)[len(anchors)//2] for c in range(3))
    seeds = [i for i in border if distance(labs[i],target)<=tolerance]
    q,seen = deque(seeds),set(seeds)
    while q:
        i=q.popleft(); x,y=i%w,i//w
        for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
            if not (0<=nx<w and 0<=ny<h):
                continue
            j=ny*w+nx
            if j in seen or alpha[j]<alpha_threshold:
                continue
            if distance(labs[j],target)<=tolerance:
                seen.add(j); q.append(j)
    visible = result['original_visible']
    # A solid patch/reference has no reliably distinguishable foreground.
    if visible-len(seen)<max(8,visible*.035):
        result['reason']='flat/full-frame reference retained (no separable background)'
        return result
    for i in seen:
        data[i*4+3]=0.
    result.update(removed=len(seen),background_lab=target,
                  reason='{} connected border samples removed; enclosed matching colors retained'.format(len(seen)))
    return result

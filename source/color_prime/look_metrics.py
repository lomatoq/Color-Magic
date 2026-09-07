"""Explainable DISPLAY-referred heuristics, not a learned aesthetic oracle.

Inputs are scene-rendered PNG samples and a camera-space surface-role map.
Metrics are valid only for the explicitly selected UI background and view.
"""
import math
from .color_math import (linear_rgb_to_oklab, oklab_to_linear_rgb, oklab_to_oklch,
                        gamut_map_oklch, srgb_to_linear_channel, clamp)


def _dist(a,b):
    return math.sqrt(sum((a[i]-b[i])**2 for i in range(3)))


def _median(values):
    values=sorted(values)
    n=len(values)
    return values[n//2] if n%2 else (values[n//2-1]+values[n//2])*.5


def _robust_lab(samples):
    if len(samples)<3:
        return None
    # Trim both extreme shadows and highlights within each measured role.
    ordered=sorted(samples,key=lambda lab:lab[0])
    trim=int(len(ordered)*.15)
    core=ordered[trim:len(ordered)-trim] if trim else ordered
    return tuple(_median([lab[c] for lab in core]) for c in range(3))


def evaluate(width,height,rgba,mask_width,mask_height,roles,background,desired_main,desired_accent,encoded=True):
    if len(roles)!=mask_width*mask_height:
        raise ValueError('Role mask dimensions mismatch')
    if len(rgba)!=width*height*4:
        raise ValueError('Rendered image dimensions mismatch')
    buckets={1:[],2:[],3:[]}; edge=[]
    bg=linear_rgb_to_oklab(background[:3])
    for y in range(mask_height):
        py=min(height-1,int((y+.5)*height/mask_height))
        for x in range(mask_width):
            role=int(roles[y*mask_width+x])
            if role not in buckets:
                continue
            px=min(width-1,int((x+.5)*width/mask_width)); i=(py*width+px)*4
            alpha=clamp(float(rgba[i+3])); rgb=[float(v) for v in rgba[i:i+3]]
            if encoded:
                rgb=[srgb_to_linear_channel(v) for v in rgb]
            if alpha<.08:
                continue
            # Composite in linear light on the intended UI surface.
            rgb=[v*alpha+background[c]*(1-alpha) for c,v in enumerate(rgb)]
            lab=linear_rgb_to_oklab(rgb);buckets[role].append(lab)
            neighbors=[roles[ny*mask_width+nx] for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1))
                       if 0<=nx<mask_width and 0<=ny<mask_height]
            if 0 in neighbors:
                edge.append(_dist(lab,bg))
    main=_robust_lab(buckets[1]);accent=_robust_lab(buckets[2])
    visible=sum(len(v) for v in buckets.values())
    coverage=len(buckets[2])/max(1,visible)
    desired=(linear_rgb_to_oklab(desired_main[:3]),linear_rgb_to_oklab(desired_accent[:3]))
    warning=[]
    if main is None: warning.append('Main has too few visible samples')
    if accent is None: warning.append('Accent has too few visible samples')
    separation=_dist(main,accent) if main and accent else 0.
    fit=(_dist(main,desired[0])+_dist(accent,desired[1]))*.5 if main and accent else 1.
    edge_value=_median(edge) if edge else 0.
    coverage_score=clamp(coverage/.04)*clamp((.80-coverage)/.30)
    score=(.36*clamp(separation/.20)+.23*clamp(edge_value/.25)+
           .16*coverage_score+.25*clamp(1-fit/.30)) if main and accent else 0.
    return {'score':score,'main_lab':main,'accent_lab':accent,'accent_fraction':coverage,
            'pair_distance':separation,'edge_background_distance':edge_value,'reference_error':fit,
            'main_samples':len(buckets[1]),'accent_samples':len(buckets[2]),
            'warnings':warning,'kind':'camera-grid readability heuristic, not aesthetic truth'}


def correct_color(base,desired,measured):
    if measured is None:
        return tuple(base)
    current=linear_rgb_to_oklab(base[:3]);goal=linear_rgb_to_oklab(desired[:3])
    delta=[(goal[i]-measured[i])*.45 for i in range(3)]
    delta[0]=max(-.05,min(.05,delta[0]))
    length=math.hypot(delta[1],delta[2])
    if length>.035:
        delta[1]*=.035/length;delta[2]*=.035/length
    target=(max(.05,min(.96,current[0]+delta[0])),current[1]+delta[1],current[2]+delta[2])
    rgb=gamut_map_oklch(oklab_to_oklch(target))
    return (*rgb,float(base[3]) if len(base)>3 else 1.)


def refinement_is_better(old,new):
    return (not new.get('warnings') and new['reference_error'] < old['reference_error']-.004 and
            new['score'] >= old['score']-.005 and new['pair_distance'] >= old['pair_distance']-.012)

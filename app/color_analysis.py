"""sRGB/D65 CIE Lab (Delta E 76) over bounded, alpha-weighted color samples."""
import math
import re
from PIL import Image

REGIONS=('whole','person','background','torso')
COLOR_VERSION='lab-d65-palette64-sample128-v1'


def parse_hex(value):
    if not isinstance(value,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',value):raise ValueError('색상은 #RRGGBB 형식으로 입력하세요.')
    return tuple(int(value[i:i+2],16) for i in (1,3,5))


def rgb_lab(rgb):
    linear=[]
    for value in rgb:
        s=value/255.;linear.append(s/12.92 if s<=.04045 else ((s+.055)/1.055)**2.4)
    r,g,b=linear
    xyz=((.4124564*r+.3575761*g+.1804375*b)/.95047,(.2126729*r+.7151522*g+.072175*b),(.0193339*r+.119192*g+.9503041*b)/1.08883)
    f=lambda t:t**(1/3) if t>(6/29)**3 else t/(3*(6/29)**2)+4/29
    x,y,z=map(f,xyz)
    return min(100.,max(0.,116*y-16)),500*(x-y),200*(y-z)


def delta_e(a,b):return math.sqrt(sum((x-y)**2 for x,y in zip(a,b)))


def palette(image,mask=None):
    sample=image.convert('RGBA');sample.thumbnail((128,128),Image.Resampling.LANCZOS)
    if mask is not None:
        mask=mask.convert('L').resize(sample.size,Image.Resampling.NEAREST)
        membership=list(mask.getdata())
    else:membership=None
    colors=[];weights=[]
    for i,(r,g,b,a) in enumerate(sample.getdata()):
        if a>=16 and (membership is None or membership[i]>=192):colors.append((r,g,b));weights.append(a)
    if not colors:return []
    # Invisible pixels never consume palette slots; alpha supplies integer weights.
    strip=Image.new('RGB',(len(colors),1));strip.putdata(colors)
    quantized=strip.quantize(colors=min(64,len(colors)),method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
    histogram={}
    for index,weight in zip(quantized.getdata(),weights):histogram[index]=histogram.get(index,0)+weight
    data=quantized.getpalette();total=sum(weights);result=[]
    for index,weight in sorted(histogram.items(),key=lambda pair:-pair[1]):
        rgb=tuple(data[index*3:index*3+3]);lab=rgb_lab(rgb)
        result.append(dict(hex='#'+''.join(f'{v:02x}' for v in rgb),l=lab[0],a=lab[1],b=lab[2],weight=weight,total=total))
    return result


def occupancy(bins,color,tolerance):
    target=rgb_lab(parse_hex(color));total=bins[0]['total'] if bins else 0
    return 100*sum(p['weight'] for p in bins if delta_e((p['l'],p['a'],p['b']),target)<=tolerance)/total if total else None

# convert_to_unity.py
# -*- coding: utf-8 -*-
"""
Unity용 SVG 평탄화 + 컷아웃(구멍) 유지(import-friendly)
- 백분율(%)과 단위(px, mm, cm, in, pt, pc) 처리
- 중첩 <svg>의 로컬 뷰포트(width/height %) 해석
- path/rect/circle/ellipse/line/polyline/polygon 변환
- mask/clipPath/filter 무시(안전)
- evenodd로 컷아웃 보존 옵션
"""

import math
from pathlib import Path
from typing import Dict, Tuple, List, Optional
from lxml import etree
from svgpathtools import parse_path, Path as SVGPath, Line, CubicBezier, QuadraticBezier, Arc

SVG_NS = "http://www.w3.org/2000/svg"
STYLE_PROPS = [
    "fill","fill-opacity","stroke","stroke-width","stroke-opacity",
    "stroke-linecap","stroke-linejoin","stroke-miterlimit","stroke-dasharray","stroke-dashoffset",
    "opacity","fill-rule"
]

# ===================== 단위/퍼센트 파싱 =====================

DPI = 96.0
UNIT_TO_PX = {
    "px": 1.0,
    "in": DPI,
    "cm": DPI / 2.54,
    "mm": DPI / 25.4,
    "pt": DPI / 72.0,
    "pc": DPI / 6.0,
}

def _s(s: Optional[str]) -> str:
    return (s or "").strip()

def parse_viewbox(vb: str) -> Optional[Tuple[float,float,float,float]]:
    parts = [p for p in vb.replace(",", " ").split() if p]
    if len(parts) != 4: return None
    try:
        return tuple(float(x) for x in parts)  # minx, miny, w, h
    except Exception:
        return None

def parse_percentage(s: str) -> Optional[float]:
    s = _s(s)
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except Exception:
            return None
    return None

def parse_numeric_length(s: str) -> Optional[Tuple[float,str]]:
    if not s: return None
    s = s.strip()
    if s.endswith("%"):  # 퍼센트는 여기서 처리 안 함
        return None
    i = 0
    while i < len(s) and (s[i].isdigit() or s[i] in "+-.eE"):
        i += 1
    num = s[:i]
    unit = s[i:].strip().lower() or "px"
    try:
        val = float(num)
    except Exception:
        return None
    return (val, unit)

def to_px(val_unit: Tuple[float,str]) -> float:
    val, unit = val_unit
    return val * UNIT_TO_PX.get(unit, 1.0)

def parse_length(s: Optional[str], axis: str, viewport_wh: Tuple[float,float]) -> Optional[float]:
    """ axis: 'x' or 'y' (퍼센트 기준 축 선택) """
    s = _s(s)
    if not s:
        return 0.0
    pct = parse_percentage(s)
    if pct is not None:
        base = viewport_wh[0] if axis == "x" else viewport_wh[1]
        return pct * base
    nu = parse_numeric_length(s)
    if nu is not None:
        return to_px(nu)
    try:
        return float(s)
    except Exception:
        return None

def parse_points(s: Optional[str], viewport_wh: Tuple[float,float]) -> List[Tuple[float,float]]:
    s = _s(s)
    if not s: return []
    import re
    toks = [t for t in re.split(r"[,\s]+", s) if t]
    pts = []
    it = iter(toks)
    for xs in it:
        ys = next(it, None)
        if ys is None: break
        x = parse_length(xs, "x", viewport_wh)
        y = parse_length(ys, "y", viewport_wh)
        if x is None or y is None: continue
        pts.append((x,y))
    return pts

# ===================== 변환 행렬/적용 =====================

def mat_mult(a,b):
    return (
        a[0]*b[0] + a[2]*b[1],
        a[1]*b[0] + a[3]*b[1],
        a[0]*b[2] + a[2]*b[3],
        a[1]*b[2] + a[3]*b[3],
        a[0]*b[4] + a[2]*b[5] + a[4],
        a[1]*b[4] + a[3]*b[5] + a[5],
    )

def parse_transform(tr: Optional[str]):
    mat = (1,0,0,1,0,0)
    if not tr: return mat
    import re
    for kind, args in re.findall(r'(\w+)\s*\(([^)]+)\)', tr):
        nums = [float(x) for x in re.split(r'[,\s]+', args.strip()) if x]
        if kind == "matrix" and len(nums) >= 6:
            m = tuple(nums[:6])
        elif kind == "translate":
            tx = nums[0]; ty = nums[1] if len(nums) > 1 else 0.0
            m = (1,0,0,1,tx,ty)
        elif kind == "scale":
            sx = nums[0]; sy = nums[1] if len(nums) > 1 else sx
            m = (sx,0,0,sy,0,0)
        elif kind == "rotate":
            ang = math.radians(nums[0]); c = math.cos(ang); s = math.sin(ang)
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                m = mat_mult((1,0,0,1,cx,cy),(c,s,-s,c,0,0))
                m = mat_mult(m,(1,0,0,1,-cx,-cy))
            else:
                m = (c,s,-s,c,0,0)
        elif kind == "skewX":
            ax = math.radians(nums[0]); m = (1,0,math.tan(ax),1,0,0)
        elif kind == "skewY":
            ay = math.radians(nums[0]); m = (1,math.tan(ay),0,1,0,0)
        else:
            m = (1,0,0,1,0,0)
        mat = mat_mult(mat, m)
    return mat

def apply_matrix_to_complex(m, z: complex) -> complex:
    a,b,c,d,e,f = m
    x,y = z.real, z.imag
    return complex(a*x + c*y + e, b*x + d*y + f)

def apply_matrix_to_path(m, p: SVGPath) -> SVGPath:
    segs = []
    for seg in p:
        if isinstance(seg, Line):
            segs.append(Line(apply_matrix_to_complex(m,seg.start),
                             apply_matrix_to_complex(m,seg.end)))
        elif isinstance(seg, (CubicBezier, QuadraticBezier)):
            pts = [apply_matrix_to_complex(m, seg.start)]
            if isinstance(seg, CubicBezier):
                pts += [apply_matrix_to_complex(m, seg.control1),
                        apply_matrix_to_complex(m, seg.control2)]
            else:
                pts += [apply_matrix_to_complex(m, seg.control)]
            pts += [apply_matrix_to_complex(m, seg.end)]
            segs.append(type(seg)(*pts))
        elif isinstance(seg, Arc):
            # 근사 변환
            segs.append(Arc(apply_matrix_to_complex(m, seg.start),
                            seg.radius, seg.rotation, seg.large_arc, seg.sweep,
                            apply_matrix_to_complex(m, seg.end)))
        else:
            segs.append(seg)
    return SVGPath(*segs)

# ===================== 도형 → path =====================

def rect_to_path(x,y,w,h):
    return parse_path(f"M{x},{y} h{w} v{h} h{-w} z")

def circle_to_path(cx,cy,r):
    return parse_path(f"M {cx-r},{cy} a {r},{r} 0 1,0 {2*r},0 a {r},{r} 0 1,0 {-2*r},0 z")

def ellipse_to_path(cx,cy,rx,ry):
    return parse_path(f"M {cx-rx},{cy} a {rx},{ry} 0 1,0 {2*rx},0 a {rx},{ry} 0 1,0 {-2*rx},0 z")

def line_to_path(x1,y1,x2,y2):
    return parse_path(f"M{x1},{y1} L{x2},{y2}")

def points_to_path(points: List[Tuple[float,float]], close=False):
    if not points: return SVGPath()
    d = f"M{points[0][0]},{points[0][1]} " + " ".join(f"L{x},{y}" for x,y in points[1:])
    if close: d += " Z"
    return parse_path(d)

# ===================== 스타일 상속 =====================

def parse_style_attr(style_str: str) -> Dict[str,str]:
    out={}
    if not style_str: return out
    for kv in style_str.split(";"):
        if ":" in kv:
            k,v = kv.split(":",1); out[k.strip()] = v.strip()
    return out

def merged_style(parent: Dict[str,str], elem: etree._Element) -> Dict[str,str]:
    st = dict(parent)
    st.update(parse_style_attr(elem.get("style","")))
    for p in STYLE_PROPS:
        if p in elem.attrib:
            st[p] = elem.attrib[p]
    return st

# ===================== 루트 뷰포트 계산 =====================

def compute_root_viewport(root: etree._Element) -> Tuple[float,float]:
    vb = parse_viewbox(root.get("viewBox",""))
    w = parse_length(root.get("width"), "x", (0,0))
    h = parse_length(root.get("height"), "y", (0,0))
    if w and h and w>0 and h>0:
        return (w,h)
    if vb:
        return (vb[2], vb[3])
    return (100.0, 100.0)

# ===================== 핵심 변환: element → paths =====================

def element_to_paths(elem: etree._Element,
                     inherited_mtx,
                     inherited_style,
                     viewport_wh: Tuple[float,float]) -> List[Tuple[SVGPath, Dict[str,str]]]:
    res=[]
    if not isinstance(elem.tag, str):
        return res
    tag = etree.QName(elem.tag).localname

    m = parse_transform(elem.get("transform"))
    mtx = mat_mult(inherited_mtx, m)
    st = merged_style(inherited_style, elem)

    def emit(p: SVGPath):
        if len(p)==0: return
        res.append((apply_matrix_to_path(mtx, p), st))

    if tag == "svg" or tag == "g":
        # 중첩 svg는 로컬 뷰포트 계산(%, 단위 처리)
        if tag == "svg":
            w = parse_length(elem.get("width"), "x", viewport_wh) or viewport_wh[0]
            h = parse_length(elem.get("height"), "y", viewport_wh) or viewport_wh[1]
            child_vp = (w, h)
        else:
            child_vp = viewport_wh
        for ch in elem:
            if isinstance(ch.tag, str):
                res += element_to_paths(ch, mtx, st, child_vp)

    elif tag == "path":
        d = _s(elem.get("d"))
        if d:
            try:
                emit(parse_path(d))
            except Exception:
                pass

    elif tag == "rect":
        x = parse_length(elem.get("x"), "x", viewport_wh) or 0.0
        y = parse_length(elem.get("y"), "y", viewport_wh) or 0.0
        w = parse_length(elem.get("width"), "x", viewport_wh)
        h = parse_length(elem.get("height"), "y", viewport_wh)
        if w and h and w>0 and h>0:
            emit(rect_to_path(x,y,w,h))

    elif tag == "circle":
        cx = parse_length(elem.get("cx"), "x", viewport_wh) or 0.0
        cy = parse_length(elem.get("cy"), "y", viewport_wh) or 0.0
        r  = parse_length(elem.get("r"),  "x", viewport_wh) or 0.0
        if r>0:
            emit(circle_to_path(cx,cy,r))

    elif tag == "ellipse":
        cx = parse_length(elem.get("cx"), "x", viewport_wh) or 0.0
        cy = parse_length(elem.get("cy"), "y", viewport_wh) or 0.0
        rx = parse_length(elem.get("rx"), "x", viewport_wh) or 0.0
        ry = parse_length(elem.get("ry"), "y", viewport_wh) or 0.0
        if rx>0 and ry>0:
            emit(ellipse_to_path(cx,cy,rx,ry))

    elif tag == "line":
        x1 = parse_length(elem.get("x1"), "x", viewport_wh) or 0.0
        y1 = parse_length(elem.get("y1"), "y", viewport_wh) or 0.0
        x2 = parse_length(elem.get("x2"), "x", viewport_wh) or 0.0
        y2 = parse_length(elem.get("y2"), "y", viewport_wh) or 0.0
        emit(line_to_path(x1,y1,x2,y2))

    elif tag == "polyline":
        pts = parse_points(elem.get("points"), viewport_wh)
        emit(points_to_path(pts, close=False))

    elif tag == "polygon":
        pts = parse_points(elem.get("points"), viewport_wh)
        emit(points_to_path(pts, close=True))

    else:
        # clipPath, mask, filter 등은 스킵
        pass

    return res

# ===================== 출력 빌드 =====================

def build_minimal_svg_preserve(root_in: etree._Element,
                               paths_with_style,
                               use_evenodd=False,
                               add_stroke=False):
    viewBox = root_in.get("viewBox") or "0 0 100 100"
    width   = root_in.get("width")
    height  = root_in.get("height")

    out = etree.Element("svg", nsmap=None)
    out.set("xmlns", SVG_NS)
    out.set("version", "1.1")
    out.set("viewBox", viewBox)
    if width:  out.set("width",  width)
    if height: out.set("height", height)

    if use_evenodd:
        # 모든 서브패스를 하나로 합쳐 evenodd 적용 → 구멍 보존
        parts = []
        fill_val = None
        for p, st in paths_with_style:
            try:
                parts.append(p.d())
            except Exception:
                continue
            if fill_val is None and st.get("fill"):
                fill_val = st["fill"]
        if parts:
            path_el = etree.SubElement(out, "path")
            path_el.set("d", " ".join(parts))
            path_el.set("fill-rule", "evenodd")
            path_el.set("fill", fill_val or "#FFFFFF")
            if add_stroke:
                path_el.set("stroke", "#000000")
                path_el.set("stroke-width", "0.5")
    else:
        for p, st in paths_with_style:
            try:
                d = p.d()
            except Exception:
                continue
            path_el = etree.SubElement(out, "path")
            path_el.set("d", d)
            path_el.set("fill", st.get("fill", "#FFFFFF"))
            if st.get("stroke"):
                path_el.set("stroke", st["stroke"])
            elif add_stroke:
                path_el.set("stroke", "#000000")
                path_el.set("stroke-width", "0.5")
            for k in ("fill-opacity","stroke-width","opacity","stroke-linecap","stroke-linejoin"):
                if st.get(k):
                    path_el.set(k, st[k])

    return out

# ===================== 외부 호출용 진입점 =====================

def flatten_preserve_holes(src: Path, dst: Path, use_evenodd=True, add_stroke=True):
    """
    src: 입력 SVG 경로
    dst: 출력 SVG 경로
    use_evenodd: True면 subpath 합치고 fill-rule="evenodd"로 컷아웃 보존
    add_stroke: True면 stroke 없을 때 얇은 검정 윤곽선 추가
    """
    parser = etree.XMLParser(remove_comments=True, recover=True)
    tree = etree.parse(str(src), parser)
    root = tree.getroot()
    if etree.QName(root.tag).localname != "svg":
        raise ValueError("Root element is not <svg>.")

    root_viewport = compute_root_viewport(root)
    paths = element_to_paths(root, (1,0,0,1,0,0), {}, root_viewport)
    out = build_minimal_svg_preserve(root, paths, use_evenodd=use_evenodd, add_stroke=add_stroke)
    etree.ElementTree(out).write(str(dst), encoding="UTF-8", xml_declaration=True, pretty_print=True)

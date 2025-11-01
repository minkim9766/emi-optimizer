#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import json
import math
from copy import deepcopy
from typing import List, Dict, Any, Optional, Tuple, Set
from lxml import etree as ET
from svgpathtools import parse_path, Line

DPI = 96.0
DEFAULT_STROKE_PX = 1.0

UNIT_TO_PX = {
    "": 1.0,
    "px": 1.0,
    "in": DPI,
    "cm": DPI / 2.54,
    "mm": DPI / 25.4,
    "pt": DPI / 72.0,
    "pc": DPI / 6.0,
}

NS = {
    "svg": "http://www.w3.org/2000/svg",
    "xlink": "http://www.w3.org/1999/xlink",
}

BASE_TAGS = {"rect", "circle", "ellipse", "image", "text"}
LINE_TAGS = {"line", "polyline", "polygon", "path"}
SKIP_WHOLE_SUBTREE_TAGS = {"defs", "symbol"}
USE_TAG = "use"

STYLE_KEYS = {"stroke-width", "vector-effect", "stroke"}
Matrix = Tuple[float, float, float, float, float, float]

ROUND_NDIGITS = 4
MIN_RECT_LENGTH_PX = 1.0
MIN_CIRCLE_R_DROP = 0.1     # 여기서 0.001 미만 원 제거
FLATTEN_MAX_ERR_PX = 0.25
FLATTEN_MAX_DEPTH = 10

def _strip_ns(tag: Optional[str]) -> Optional[str]:
    if not tag:
        return tag
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag

def _parse_length_to_px(val: Optional[str]) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if s.endswith("%"):
        return None
    num = ""
    unit = ""
    for ch in s:
        if ch.isdigit() or ch in ".+-eE":
            num += ch
        else:
            unit += ch
    unit = unit.strip().lower()
    try:
        f = float(num)
    except Exception:
        return None
    scale = UNIT_TO_PX.get(unit)
    if scale is None:
        return None
    return f * scale

def _parse_percent_or_float(val: Optional[str], ref: float) -> float:
    if val is None:
        return 0.0
    s = str(val).strip()
    if not s:
        return 0.0
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0 * ref
        except Exception:
            return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0

def _parse_viewbox(vb: Optional[str]) -> Optional[Tuple[float, float, float, float]]:
    if not vb:
        return None
    try:
        a = [float(x) for x in vb.replace(",", " ").split()]
        if len(a) != 4:
            return None
        return a[0], a[1], a[2], a[3]
    except Exception:
        return None

def _get_svg_size_info(root) -> Dict[str, Any]:
    raw_w = root.get("width")
    raw_h = root.get("height")
    vb = _parse_viewbox(root.get("viewBox"))
    wpx = _parse_length_to_px(raw_w)
    hpx = _parse_length_to_px(raw_h)
    if (wpx is None or hpx is None) and vb is not None:
        _, _, vw, vh = vb
        if wpx is None:
            wpx = vw
        if hpx is None:
            hpx = vh
    return {
        "width_px": wpx,
        "height_px": hpx,
        "width_raw": raw_w,
        "height_raw": raw_h,
        "viewBox": vb if vb is not None else None
    }

def _identity() -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

def _mat_mul(m1: Matrix, m2: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )

def _apply_mat(m: Matrix, x: float, y: float) -> Tuple[float, float]:
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f

def _parse_style_attr(style_str: Optional[str]) -> Dict[str, str]:
    out = {}
    if not style_str:
        return out
    for kv in style_str.split(";"):
        kv = kv.strip()
        if not kv or ":" not in kv:
            continue
        k, v = kv.split(":", 1)
        out[k.strip()] = v.strip()
    return out

def _effective_style(elem, inherited: Dict[str, str]) -> Dict[str, str]:
    eff = dict(inherited)
    inline = _parse_style_attr(elem.get("style"))
    for k in STYLE_KEYS:
        if k in inline:
            eff[k] = inline[k]
    for k in STYLE_KEYS:
        v = elem.get(k)
        if v is not None:
            eff[k] = v
    return eff

def _stroke_present(eff_style: Dict[str, str]) -> bool:
    v = eff_style.get("stroke")
    if v is None:
        return False
    return v.lower() != "none"

def _stroke_width_px_from_style(eff_style: Dict[str, str]) -> Optional[float]:
    v = eff_style.get("stroke-width")
    if v is not None:
        px = _parse_length_to_px(v)
        if px is not None:
            return px
    if _stroke_present(eff_style):
        return DEFAULT_STROKE_PX
    return None

def _non_scaling_stroke(eff_style: Dict[str, str]) -> bool:
    return eff_style.get("vector-effect", "") == "non-scaling-stroke"

def _uniform_scale_from_matrix(m: Matrix) -> Optional[float]:
    a, b, c, d, _, _ = m
    sx = math.hypot(a, b)
    sy = math.hypot(c, d)
    if abs(sx - sy) < 1e-9:
        return sx
    return None

def _viewport_matrix(size_info: Dict[str, Any]) -> Matrix:
    vb = size_info.get("viewBox")
    wpx = size_info.get("width_px")
    hpx = size_info.get("height_px")
    if vb and wpx and hpx:
        minx, miny, vw, vh = vb
        sx = wpx / vw if vw else 1.0
        sy = hpx / vh if vh else 1.0
        return (sx, 0, 0, sy, -minx * sx, -miny * sy)
    return _identity()

def _read_points_list(s: str) -> List[Tuple[float, float]]:
    pts = []
    s = s.replace(",", " ")
    toks = [t for t in s.split() if t]
    it = iter(toks)
    for x in it:
        try:
            y = next(it)
        except StopIteration:
            break
        try:
            pts.append((float(x), float(y)))
        except:
            pass
    return pts

def _flatten_segment(seg, m: Matrix, t0: float, t1: float, out: List[Tuple[Tuple[float,float], Tuple[float,float]]], depth: int):
    if isinstance(seg, Line):
        p0 = seg.point(t0); p1 = seg.point(t1)
        out.append((_apply_mat(m, p0.real, p0.imag), _apply_mat(m, p1.real, p1.imag)))
        return
    p0 = seg.point(t0); p1 = seg.point(t1); pm = seg.point((t0 + t1) * 0.5)
    dx = p1.real - p0.real; dy = p1.imag - p0.imag
    den = math.hypot(dx, dy) or 1.0
    dev = abs(dy * pm.real - dx * pm.imag + p1.real * p0.imag - p1.imag * p0.real) / den
    if dev <= FLATTEN_MAX_ERR_PX or depth >= FLATTEN_MAX_DEPTH:
        out.append((_apply_mat(m, p0.real, p0.imag), _apply_mat(m, p1.real, p1.imag)))
        return
    tm = (t0 + t1) * 0.5
    _flatten_segment(seg, m, t0, tm, out, depth + 1)
    _flatten_segment(seg, m, tm, t1, out, depth + 1)

def _path_to_segments(d: str, m: Matrix) -> List[Tuple[Tuple[float,float], Tuple[float,float]]]:
    try:
        path = parse_path(d)
    except Exception:
        return []
    out: List[Tuple[Tuple[float,float], Tuple[float,float]]] = []
    for seg in path:
        _flatten_segment(seg, m, 0.0, 1.0, out, 0)
    return out

def _segment_to_rect(p1: Tuple[float, float], p2: Tuple[float, float], stroke_px: Optional[float]) -> Optional[Tuple[Dict[str, float], Dict[str, float]]]:
    if stroke_px is None:
        return None
    x1, y1 = p1; x2, y2 = p2
    dx = x2 - x1; dy = y2 - y1
    L = math.hypot(dx, dy)
    if L < MIN_RECT_LENGTH_PX:
        return None
    cx = (x1 + x2) * 0.5; cy = (y1 + y2) * 0.5
    angle = math.degrees(math.atan2(dy, dx))
    attrs = {
        "x": cx - L * 0.5,
        "y": cy - stroke_px * 0.5,
        "width": L,
        "height": stroke_px
    }
    rot = {"angle_deg": angle, "cx": cx, "cy": cy}
    return attrs, rot

def _round(v: float) -> float:
    return round(float(v), ROUND_NDIGITS)

def _rect_record(attrs: Dict[str, float], rot: Dict[str, float], ops_base: List[Dict[str, Any]], source_id: Optional[str]) -> Dict[str, Any]:
    ops = list(ops_base) + [{"type": "rotate", "angle_deg": _round(rot["angle_deg"]), "cx": _round(rot["cx"]), "cy": _round(rot["cy"])}]
    return {
        "kind": "base_shape",
        "type": "rect",
        "source_id": source_id,
        "attrs": {k: _round(v) for k, v in attrs.items()},
        "transform_ops": ops
    }

def _circle_record(cx: float, cy: float, r: float, ops_base: List[Dict[str, Any]], source_id: Optional[str]) -> Dict[str, Any]:
    return {
        "kind": "base_shape",
        "type": "circle",
        "source_id": source_id,
        "attrs": {"cx": _round(cx), "cy": _round(cy), "r": _round(r)},
        "transform_ops": list(ops_base)
    }

def _serialize_transform_ops(ops: List[Dict[str, Any]]) -> Matrix:
    m = _identity()
    for op in ops:
        t = op.get("type")
        if t == "matrix":
            mm = (op.get("a",1.0), op.get("b",0.0), op.get("c",0.0), op.get("d",1.0), op.get("e",0.0), op.get("f",0.0))
        elif t == "translate":
            mm = (1,0,0,1, op.get("tx",0.0), op.get("ty",0.0))
        elif t == "scale":
            sx = op.get("sx",1.0); sy = op.get("sy",sx)
            mm = (sx,0,0,sy,0,0)
        elif t == "rotate":
            ang = math.radians(op.get("angle_deg",0.0))
            cosv = math.cos(ang); sinv = math.sin(ang)
            if "cx" in op and "cy" in op:
                cx = op["cx"]; cy = op["cy"]
                mm = _mat_mul(_mat_mul((1,0,0,1,cx,cy), (cosv, sinv, -sinv, cosv, 0,0)), (1,0,0,1,-cx,-cy))
            else:
                mm = (cosv, sinv, -sinv, cosv, 0, 0)
        elif t == "skewX":
            ang = math.radians(op.get("angle_deg",0.0)); mm = (1,0, math.tan(ang), 1, 0, 0)
        elif t == "skewY":
            ang = math.radians(op.get("angle_deg",0.0)); mm = (1, math.tan(ang), 0, 1, 0, 0)
        else:
            mm = _identity()
        m = _mat_mul(m, mm)
    return m

def _parse_transform_ops(s: Optional[str]) -> List[Dict[str, Any]]:
    if not s:
        return []
    i = 0
    ops = []
    while i < len(s):
        while i < len(s) and s[i].isspace():
            i += 1
        if i >= len(s): break
        def _read_vals(idx: int) -> Tuple[List[float], int]:
            j = s.find("(", idx) + 1
            buf = ""; depth = 1
            while j < len(s) and depth > 0:
                ch = s[j]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        j += 1; break
                buf += ch; j += 1
            toks = []
            cur = ""
            for ch in buf.replace(",", " "):
                if ch in " \t\r\n":
                    if cur:
                        toks.append(cur); cur = ""
                else:
                    cur += ch
            if cur: toks.append(cur)
            vals = []
            for t in toks:
                try: vals.append(float(t))
                except: pass
            return vals, j
        if s.startswith("matrix", i):
            vals, i = _read_vals(i)
            if len(vals) >= 6: ops.append({"type":"matrix","a":vals[0],"b":vals[1],"c":vals[2],"d":vals[3],"e":vals[4],"f":vals[5]})
        elif s.startswith("translate", i):
            vals, i = _read_vals(i)
            ops.append({"type":"translate","tx": vals[0] if len(vals)>=1 else 0.0,"ty": vals[1] if len(vals)>=2 else 0.0})
        elif s.startswith("scale", i):
            vals, i = _read_vals(i)
            ops.append({"type":"scale","sx": vals[0] if len(vals)>=1 else 1.0,"sy": vals[1] if len(vals)>=2 else vals[0] if len(vals)>=1 else 1.0})
        elif s.startswith("rotate", i):
            vals, i = _read_vals(i)
            if len(vals)>=3: ops.append({"type":"rotate","angle_deg":vals[0],"cx":vals[1],"cy":vals[2]})
            else: ops.append({"type":"rotate","angle_deg":vals[0] if vals else 0.0})
        elif s.startswith("skewX", i):
            vals, i = _read_vals(i); ops.append({"type":"skewX","angle_deg": vals[0] if vals else 0.0})
        elif s.startswith("skewY", i):
            vals, i = _read_vals(i); ops.append({"type":"skewY","angle_deg": vals[0] if vals else 0.0})
        else:
            i += 1
    return ops

def _visible(elem) -> bool:
    if elem.get("display") == "none":
        return False
    if elem.get("visibility") == "hidden":
        return False
    return True

def list_svg_flat(svg_path: str, expand_use: bool = True) -> Dict[str, Any]:
    parser = ET.XMLParser(remove_comments=True)
    root = ET.parse(svg_path, parser).getroot()

    size_info = _get_svg_size_info(root)
    vp_mat = _viewport_matrix(size_info)

    defs_by_id = {}
    for e in root.iter():
        if e.get("id"):
            defs_by_id[e.get("id")] = e

    objects: List[Dict[str, Any]] = []

    seen_rect: Set[Tuple[float, float, float, float, float, float, float, float]] = set()
    seen_circle: Set[Tuple[float, float, float]] = set()

    svg_w_ref = size_info.get("width_px") or 1000.0
    svg_h_ref = size_info.get("height_px") or 1000.0

    nodes = [root]
    while nodes:
        elem = nodes.pop(0)

        tag = _strip_ns(getattr(elem, "tag", None))
        if tag is None:
            continue

        if tag in SKIP_WHOLE_SUBTREE_TAGS:
            continue

        if not _visible(elem):
            nodes[0:0] = list(elem)
            continue

        if tag == USE_TAG and expand_use:
            href = elem.get("{%s}href" % NS["xlink"]) or elem.get("href")
            if href and href.startswith("#"):
                ref_id = href[1:]
                target = defs_by_id.get(ref_id)
                if target is not None:
                    cloned = deepcopy(target)
                    use_tx = _parse_percent_or_float(elem.get("x"), svg_w_ref)
                    use_ty = _parse_percent_or_float(elem.get("y"), svg_h_ref)
                    t_ops = _parse_transform_ops(elem.get("transform"))
                    if use_tx or use_ty:
                        t_ops = [{"type":"translate","tx":use_tx,"ty":use_ty}] + t_ops
                    old = cloned.get("transform")
                    if old:
                        t_ops = t_ops + _parse_transform_ops(old)
                    if t_ops:
                        parts = []
                        for op in t_ops:
                            if op["type"]=="translate":
                                parts.append(f"translate({op.get('tx',0)},{op.get('ty',0)})")
                            elif op["type"]=="rotate":
                                if "cx" in op and "cy" in op:
                                    parts.append(f"rotate({op['angle_deg']},{op['cx']},{op['cy']})")
                                else:
                                    parts.append(f"rotate({op['angle_deg']})")
                            elif op["type"]=="scale":
                                parts.append(f"scale({op.get('sx',1)},{op.get('sy',op.get('sx',1))})")
                            elif op["type"]=="matrix":
                                a=op["a"];b=op["b"];c=op["c"];d=op["d"];e=op["e"];f=op["f"]
                                parts.append(f"matrix({a},{b},{c},{d},{e},{f})")
                            elif op["type"]=="skewX":
                                parts.append(f"skewX({op['angle_deg']})")
                            elif op["type"]=="skewY":
                                parts.append(f"skewY({op['angle_deg']})")
                        cloned.set("transform", " ".join(parts))
                    nodes.insert(0, cloned)
            continue

        chain = []
        e = elem
        while e is not None:
            chain.append(e)
            e = e.getparent() if hasattr(e, "getparent") else None
        chain.reverse()

        ops_all: List[Dict[str, Any]] = []
        eff_style: Dict[str, str] = {}
        mat_all: Matrix = vp_mat
        for node in chain:
            ops = _parse_transform_ops(node.get("transform"))
            if ops:
                ops_all.extend(ops)
                mat_all = _mat_mul(mat_all, _serialize_transform_ops(ops))
            eff_style = _effective_style(node, eff_style)

        sw = _stroke_width_px_from_style(eff_style)
        uni = _uniform_scale_from_matrix(mat_all)
        non_scale = _non_scaling_stroke(eff_style)
        stroke_for_rect = sw
        if sw is not None and uni is not None and not non_scale:
            stroke_for_rect = sw * uni

        if tag in LINE_TAGS:
            segs: List[Tuple[Tuple[float,float], Tuple[float,float]]] = []
            if tag == "line":
                x1 = _parse_percent_or_float(elem.get("x1"), svg_w_ref)
                y1 = _parse_percent_or_float(elem.get("y1"), svg_h_ref)
                x2 = _parse_percent_or_float(elem.get("x2"), svg_w_ref)
                y2 = _parse_percent_or_float(elem.get("y2"), svg_h_ref)
                segs.append((_apply_mat(mat_all, x1, y1), _apply_mat(mat_all, x2, y2)))
            elif tag == "polyline":
                pts = _read_points_list(elem.get("points", "") or "")
                for i in range(len(pts)-1):
                    p1 = _apply_mat(mat_all, *pts[i]); p2 = _apply_mat(mat_all, *pts[i+1])
                    segs.append((p1, p2))
            elif tag == "polygon":
                pts = _read_points_list(elem.get("points", "") or "")
                for i in range(len(pts)):
                    p1 = _apply_mat(mat_all, *pts[i]); p2 = _apply_mat(mat_all, *pts[(i+1)%len(pts)])
                    segs.append((p1, p2))
            elif tag == "path":
                d = elem.get("d")
                if d:
                    segs = _path_to_segments(d, mat_all)

            for p1, p2 in segs:
                sr = _segment_to_rect(p1, p2, stroke_for_rect)
                if not sr:
                    continue
                attrs, rot = sr
                rec = _rect_record(attrs, rot, [], elem.get("id"))
                k = (
                    rec["attrs"]["x"], rec["attrs"]["y"],
                    rec["attrs"]["width"], rec["attrs"]["height"],
                    rec["transform_ops"][-1]["angle_deg"],
                    rec["transform_ops"][-1]["cx"], rec["transform_ops"][-1]["cy"],
                    0.0
                )
                if k in seen_rect:
                    continue
                seen_rect.add(k)
                objects.append(rec)

            nodes[0:0] = list(elem)
            continue

        if tag in BASE_TAGS:
            if tag == "circle":
                cx = _parse_percent_or_float(elem.get("cx"), svg_w_ref)
                cy = _parse_percent_or_float(elem.get("cy"), svg_h_ref)
                r = _parse_percent_or_float(elem.get("r"), min(svg_w_ref, svg_h_ref))
                cx2, cy2 = _apply_mat(mat_all, cx, cy)
                rr = r
                if not _non_scaling_stroke(eff_style):
                    if uni is not None:
                        rr = r * uni
                if rr < MIN_CIRCLE_R_DROP:
                    nodes[0:0] = list(elem)
                    continue
                key = (_round(cx2), _round(cy2), _round(rr))
                if key in seen_circle:
                    nodes[0:0] = list(elem); continue
                seen_circle.add(key)
                objects.append(_circle_record(cx2, cy2, rr, [], elem.get("id")))

            elif tag == "rect":
                x = _parse_percent_or_float(elem.get("x"), svg_w_ref)
                y = _parse_percent_or_float(elem.get("y"), svg_h_ref)
                w = _parse_percent_or_float(elem.get("width"), svg_w_ref)
                h = _parse_percent_or_float(elem.get("height"), svg_h_ref)
                pts = [
                    _apply_mat(mat_all, x, y),
                    _apply_mat(mat_all, x+w, y),
                    _apply_mat(mat_all, x, y+h),
                    _apply_mat(mat_all, x+w, y+h),
                ]
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                xx = min(xs); yy = min(ys); ww = max(xs)-xx; hh = max(ys)-yy
                objects.append({
                    "kind": "base_shape",
                    "type": "rect",
                    "source_id": elem.get("id"),
                    "attrs": {"x": _round(xx), "y": _round(yy), "width": _round(ww), "height": _round(hh)},
                    "transform_ops": []
                })

            elif tag == "ellipse":
                cx = _parse_percent_or_float(elem.get("cx"), svg_w_ref)
                cy = _parse_percent_or_float(elem.get("cy"), svg_h_ref)
                rx = _parse_percent_or_float(elem.get("rx"), svg_w_ref)
                ry = _parse_percent_or_float(elem.get("ry"), svg_h_ref)
                cx2, cy2 = _apply_mat(mat_all, cx, cy)
                sx = math.hypot(mat_all[0], mat_all[1]); sy = math.hypot(mat_all[2], mat_all[3])
                objects.append({
                    "kind": "base_shape",
                    "type": "ellipse",
                    "source_id": elem.get("id"),
                    "attrs": {"cx": _round(cx2), "cy": _round(cy2), "rx": _round(rx*sx), "ry": _round(ry*sy)},
                    "transform_ops": []
                })

            elif tag == "text":
                x = _parse_percent_or_float(elem.get("x"), svg_w_ref)
                y = _parse_percent_or_float(elem.get("y"), svg_h_ref)
                x2, y2 = _apply_mat(mat_all, x, y)
                rec = {
                    "kind": "base_shape",
                    "type": "text",
                    "source_id": elem.get("id"),
                    "attrs": {"x": _round(x2), "y": _round(y2)},
                    "transform_ops": [],
                }
                if elem.text and elem.text.strip():
                    rec["text"] = elem.text.strip()
                objects.append(rec)

            nodes[0:0] = list(elem)
            continue

        nodes[0:0] = list(elem)

    return {
        "svg_size": size_info,
        "objects": objects
    }

def save_svg_json(obj: Dict[str, Any], out_path: str, pretty: bool = True) -> None:
    with open(out_path, "w", encoding="utf-8") as f:
        if pretty:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        else:
            json.dump(obj, f, ensure_ascii=False)

def _main():
    if len(sys.argv) < 3:
        print("사용 예시")
        print("python svg_to_json_fixed.py input.svg output.json")
        sys.exit(1)
    svg_path = sys.argv[1]; out_path = sys.argv[2]
    data = list_svg_flat(svg_path)
    save_svg_json(data, out_path)
    size = data["svg_size"]
    print(f"SVG width_px={size['width_px']} height_px={size['height_px']}")
    print(f"총 객체 수 {len(data['objects'])}")

if __name__ == "__main__":
    _main()

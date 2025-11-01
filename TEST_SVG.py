#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import math
import argparse
from xml.etree.ElementTree import Element, SubElement, ElementTree
from typing import Any, Dict, List, Optional, Union

Number = Union[int, float]

def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None

def _fmt(v: Any, ndigits: int = 6) -> str:
    if isinstance(v, (int, float)):
        s = f"{v:.{ndigits}f}"
        s = s.rstrip("0").rstrip(".")
        return s if s != "-0" else "0"
    return str(v)

def _serialize_transform_ops(ops: List[Dict[str, Any]], ndigits: int = 6) -> Optional[str]:
    if not ops:
        return None
    parts = []
    for op in ops:
        t = op.get("type")
        if t == "matrix":
            a = _fmt(op.get("a", 1), ndigits)
            b = _fmt(op.get("b", 0), ndigits)
            c = _fmt(op.get("c", 0), ndigits)
            d = _fmt(op.get("d", 1), ndigits)
            e = _fmt(op.get("e", 0), ndigits)
            f = _fmt(op.get("f", 0), ndigits)
            parts.append(f"matrix({a},{b},{c},{d},{e},{f})")
        elif t == "translate":
            tx = _fmt(op.get("tx", 0), ndigits)
            ty = _fmt(op.get("ty", 0), ndigits)
            parts.append(f"translate({tx},{ty})")
        elif t == "scale":
            sx = _fmt(op.get("sx", 1), ndigits)
            sy = _fmt(op.get("sy", op.get("sx", 1)), ndigits)
            parts.append(f"scale({sx},{sy})")
        elif t == "rotate":
            ang = _fmt(op.get("angle_deg", 0), ndigits)
            if "cx" in op and "cy" in op:
                cx = _fmt(op.get("cx", 0), ndigits)
                cy = _fmt(op.get("cy", 0), ndigits)
                parts.append(f"rotate({ang},{cx},{cy})")
            else:
                parts.append(f"rotate({ang})")
        elif t == "skewX":
            ang = _fmt(op.get("angle_deg", 0), ndigits)
            parts.append(f"skewX({ang})")
        elif t == "skewY":
            ang = _fmt(op.get("angle_deg", 0), ndigits)
            parts.append(f"skewY({ang})")
    return " ".join(parts) if parts else None

def _ensure_viewbox(svg_root: Element, width_px: Optional[float], height_px: Optional[float], viewbox_from_json: Optional[List[float]]):
    if width_px and height_px:
        svg_root.set("width", _fmt(width_px))
        svg_root.set("height", _fmt(height_px))
        svg_root.set("viewBox", f"0 0 { _fmt(width_px) } { _fmt(height_px) }")
    elif viewbox_from_json and len(viewbox_from_json) == 4:
        minx, miny, w, h = viewbox_from_json
        svg_root.set("viewBox", f"{_fmt(minx)} {_fmt(miny)} {_fmt(w)} {_fmt(h)}")

def _set_common_style(elem: Element, stroke: str, fill: str, stroke_width: float):
    elem.set("stroke", stroke)
    elem.set("fill", fill)
    elem.set("stroke-width", _fmt(stroke_width))

def _add_rect(parent: Element, attrs: Dict[str, Any], transform: Optional[str], style: Dict[str, Any]):
    r = SubElement(parent, "rect")
    for k in ("x", "y", "width", "height", "rx", "ry"):
        if k in attrs:
            r.set(k, _fmt(attrs[k]))
    if transform:
        r.set("transform", transform)
    _set_common_style(r, style.get("stroke_rect", "#ff0000"), style.get("fill_rect", "none"), style.get("stroke_width_rect", 0.5))

def _add_circle(parent: Element, attrs: Dict[str, Any], transform: Optional[str], style: Dict[str, Any], small: bool = False):
    c = SubElement(parent, "circle")
    for k in ("cx", "cy", "r"):
        if k in attrs:
            c.set(k, _fmt(attrs[k]))
    if transform:
        c.set("transform", transform)
    if small:
        _set_common_style(c, style.get("stroke_circle_small", "#00aa00"), style.get("fill_circle_small", "none"), style.get("stroke_width_circle_small", 0.4))
    else:
        _set_common_style(c, style.get("stroke_circle", "#0066cc"), style.get("fill_circle", "none"), style.get("stroke_width_circle", 0.4))

def _add_ellipse(parent: Element, attrs: Dict[str, Any], transform: Optional[str], style: Dict[str, Any]):
    e = SubElement(parent, "ellipse")
    for k in ("cx", "cy", "rx", "ry"):
        if k in attrs:
            e.set(k, _fmt(attrs[k]))
    if transform:
        e.set("transform", transform)
    _set_common_style(e, style.get("stroke_ellipse", "#cc6600"), style.get("fill_ellipse", "none"), style.get("stroke_width_ellipse", 0.4))

def _add_text(parent: Element, attrs: Dict[str, Any], text_val: Optional[str], transform: Optional[str], style: Dict[str, Any]):
    t = SubElement(parent, "text")
    t.set("x", _fmt(attrs.get("x", 0)))
    t.set("y", _fmt(attrs.get("y", 0)))
    if "font-size" in attrs:
        t.set("font-size", str(attrs["font-size"]))
    if transform:
        t.set("transform", transform)
    _set_common_style(t, style.get("stroke_text", "none"), style.get("fill_text", "#000000"), style.get("stroke_width_text", 0.0))
    if text_val:
        t.text = text_val

def json_to_svg(doc: Dict[str, Any], min_circle_r_px: float = 0.0, round_ndigits: int = 6) -> Element:
    svg = Element("svg", xmlns="http://www.w3.org/2000/svg")
    size = doc.get("svg_size", {})
    width_px = _num(size.get("width_px"))
    height_px = _num(size.get("height_px"))
    vb = size.get("viewBox")
    _ensure_viewbox(svg, width_px, height_px, vb)

    style = {
        "stroke_rect": "#ff0000",
        "fill_rect": "none",
        "stroke_width_rect": 0.5,
        "stroke_circle": "#0066cc",
        "fill_circle": "none",
        "stroke_width_circle": 0.4,
        "stroke_circle_small": "#00aa00",
        "fill_circle_small": "none",
        "stroke_width_circle_small": 0.4,
        "stroke_ellipse": "#cc6600",
        "fill_ellipse": "none",
        "stroke_width_ellipse": 0.4,
        "stroke_text": "none",
        "fill_text": "#000000",
        "stroke_width_text": 0.0
    }

    layer_all = SubElement(svg, "g", id="all")
    layer_small_cir = SubElement(svg, "g", id="small_circles")

    for obj in doc.get("objects", []):
        if obj.get("kind") != "base_shape":
            continue
        t = obj.get("type")
        attrs = obj.get("attrs", {})
        text_val = obj.get("text")
        ops = obj.get("transform_ops", [])
        transform = _serialize_transform_ops(ops, ndigits=round_ndigits)

        if t == "rect":
            _add_rect(layer_all, attrs, transform, style)
        elif t == "circle":
            r = _num(attrs.get("r"))
            if r is not None and r < min_circle_r_px:
                _add_circle(layer_small_cir, attrs, transform, style, small=True)
            else:
                _add_circle(layer_all, attrs, transform, style, small=False)
        elif t == "ellipse":
            _add_ellipse(layer_all, attrs, transform, style)
        elif t == "text":
            _add_text(layer_all, attrs, text_val, transform, style)
        else:
            pass

    return svg

def save_svg(svg_root: Element, out_path: str):
    tree = ElementTree(svg_root)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)

def main():
    ap = argparse.ArgumentParser(description="SVG JSON을 시각 검토용 SVG로 복원")
    ap.add_argument("input_json", help="입력 JSON 경로")
    ap.add_argument("output_svg", help="출력 SVG 경로")
    ap.add_argument("--min-circle-r", type=float, default=0.0, help="이 값보다 작은 원은 small_circles 레이어로 분리")
    ap.add_argument("--round", type=int, default=6, help="좌표 반올림 자릿수")
    args = ap.parse_args()

    with open(args.input_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    svg = json_to_svg(data, min_circle_r_px=args.min_circle_r, round_ndigits=args.round)
    save_svg(svg, args.output_svg)
    print("완료")
    print("입력:", args.input_json)
    print("출력:", args.output_svg)

if __name__ == "__main__":
    main()

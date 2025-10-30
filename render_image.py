from __future__ import annotations

import json
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Union

from pygerber.gerberx3.api.v2 import FileTypeEnum, GerberFile, Project
import Front_Botton_Divider

# PIL 후처리(옵션)
from PIL import Image
import re as _re


# 디버그 토글
DEBUG_SORT = False   # 정렬/매칭/폴백 과정을 자세히 출력


# =========================================================
# 1) 색상 매핑 기본값
# =========================================================
COLOR_BY_CATEGORY: Dict[str, str] = {
    "GLUE": "#FFFFFF",             # Adhes → 기본 흰색(배경 레이어)
    "ASSEMBLYDRAWING": "#FF0000",  # Fab   → 장애물(빨강)
    "SOLDERMASK": "#FF0000",       # Mask  → 장애물(빨강)
    "SOLDERPASTE": "#0000FF",      # Paste → 파랑(맨 위)
    # 보조/예외
    "PROFILE": "#000000",          # Edge.Cuts(윤곽선)
    "LEGEND": "#000000",           # Silkscreen
    "COPPER": "#FFFFFF",           # 필요 시 흰색(대비용)
    "UNKNOWN": "#808080",
}

# “단색(uniform_color)” 적용 시에도 원색을 유지할 카테고리(검정 보존용)
PRESERVE_BLACK_CATEGORIES = {"PROFILE", "LEGEND"}

# =========================================================
# 2) 정렬 우선순위 (아래→위)
# =========================================================
CATEGORY_ORDER: List[str] = [
    "GLUE",             # 맨 아래 (배경 레이어)
    "ASSEMBLYDRAWING",
    "SOLDERMASK",
    "SOLDERPASTE",      # 맨 위
    # 기타는 뒤로
    "PROFILE",
    "LEGEND",
    "COPPER",
    "UNKNOWN",
]

# =========================================================
# 2-1) 합성 버킷(고정 매핑) — type_composite=True에서 사용
# =========================================================
COMPOSITE_BUCKET = {
    "ASSEMBLYDRAWING": "SOLDERMASK",
    "SOLDERMASK":      "SOLDERMASK",
    "SOLDERPASTE":     "SOLDERPASTE",
    "PROFILE":         "PROFILE",
    "LEGEND":          "LEGEND",
    "COPPER":          "COPPER",
    "GLUE":            "GLUE",
    "UNKNOWN":         "UNKNOWN",
}

# =========================================================
# (보조) PNG 후처리 유틸 — 배경색 선택 가능
# =========================================================
BytesPathStr = Union[BytesIO, Path, str]

def _hex_to_rgb(color: str) -> Tuple[int, int, int]:
    s = color.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6 or not _re.fullmatch(r"[0-9A-Fa-f]{6}", s):
        raise ValueError(f"유효하지 않은 HEX 색상: {color}")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))

def _ensure_rgba(img: Image.Image) -> Image.Image:
    return img if img.mode == "RGBA" else img.convert("RGBA")

def _recolor_uniform_preserve_black(img: Image.Image, target_hex: str) -> Image.Image:
    """
    알파>0 픽셀 중 '검정(0,0,0)'은 그대로 두고, 나머지만 target_hex로 치환.
    → 윤곽/실크의 검정은 유지.
    """
    target_rgb = _hex_to_rgb(target_hex)
    img = _ensure_rgba(img)
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            if (r, g, b) == (0, 0, 0):  # 검정 유지(윤곽/실크)
                continue
            px[x, y] = (*target_rgb, a)
    return img

def _fill_transparent_with_color(img: Image.Image, color_hex: str) -> Image.Image:
    """
    투명(알파=0) 영역을 지정한 색으로 메움. (PNG 배경 채움)
    """
    img = _ensure_rgba(img)
    r, g, b = _hex_to_rgb(color_hex)
    bg = Image.new("RGBA", img.size, (r, g, b, 255))
    bg.alpha_composite(img)
    return bg

def _save_image(img: Image.Image, destination: BytesPathStr, quality: Optional[int] = None) -> None:
    if isinstance(destination, BytesIO):
        destination.seek(0)
        img.save(destination, format="PNG", quality=quality if quality is not None else 85)
        destination.seek(0)
    else:
        save_kwargs = {}
        if quality is not None:
            save_kwargs["quality"] = quality
        p = str(destination)
        if not p.lower().endswith(".png"):
            p = p + ".png"
        img.save(p, **save_kwargs)


# =========================================================
# 3) gbrjob 로드
# =========================================================
def load_gbrjob_attributes(job_file_path: str) -> Dict[str, Dict[str, str]]:
    with open(job_file_path, "r", encoding="utf-8") as f:
        job = json.load(f)

    attrs: Dict[str, Dict[str, str]] = {}
    for item in (job.get("FilesAttributes") or []):
        p = item.get("Path")
        if not p:
            continue
        attrs[p] = {
            "FileFunction": item.get("FileFunction", ""),
            "FilePolarity": item.get("FilePolarity", ""),
        }
    return attrs


# =========================================================
# 4) 파일명 매칭 보강
# =========================================================
_PREFIX_PAT = re.compile(r"^(edit__|copy_of_|tmp_|temp_|test_)", re.IGNORECASE)

def normalize_name(name: str) -> str:
    return _PREFIX_PAT.sub("", Path(name).name.lower())

def core_suffix(name: str) -> str:
    base = Path(name).name
    m = re.search(r"[0-9a-zA-Z].*", base)
    return m.group(0).lower() if m else base.lower()

def find_attr_for_path(rel_path: str, job_attrs: Dict[str, Dict[str, str]]) -> Optional[Dict[str, str]]:
    if rel_path in job_attrs:
        if DEBUG_SORT:
            print(f"[MATCH] exact: {rel_path}")
        return job_attrs[rel_path]

    rel_name = Path(rel_path).name
    rel_norm = normalize_name(rel_name)
    rel_core = core_suffix(rel_name)
    rel_name_l = rel_name.lower()

    norm_index: Dict[str, Dict[str, str]] = {}
    core_index: Dict[str, Dict[str, str]] = {}
    name_index: Dict[str, Dict[str, str]] = {}

    for k, v in job_attrs.items():
        kn = normalize_name(k)
        kc = core_suffix(k)
        kl = Path(k).name.lower()
        norm_index.setdefault(kn, v)
        core_index.setdefault(kc, v)
        name_index.setdefault(kl, v)

    if rel_norm in norm_index:
        if DEBUG_SORT:
            print(f"[MATCH] norm: {rel_path} -> {rel_norm}")
        return norm_index[rel_norm]
    if rel_core in core_index:
        if DEBUG_SORT:
            print(f"[MATCH] core: {rel_path} -> {rel_core}")
        return core_index[rel_core]
    if rel_name_l in name_index:
        if DEBUG_SORT:
            print(f"[MATCH] name: {rel_path} -> {rel_name_l}")
        return name_index[rel_name_l]

    if DEBUG_SORT:
        print(f"[MISS ] no gbrjob match: {rel_path}")
    return None


# =========================================================
# 5) FileFunction → 카테고리/사이드 판정 + 파일명 폴백
# =========================================================
def parse_filefunction(file_function: str) -> Tuple[str, Optional[str]]:
    if not file_function:
        return ("UNKNOWN", None)
    parts = [p.strip() for p in file_function.split(",") if p.strip()]
    head = parts[0].upper()
    if head == "ASSEMBLY":
        head = "ASSEMBLYDRAWING"
    side = None
    if len(parts) >= 2:
        s = parts[1].upper()
        if s in ("TOP", "BOT", "BOTTOM"):
            side = "TOP" if s == "TOP" else "BOT"
    if head not in COLOR_BY_CATEGORY:
        head = "UNKNOWN"
    return (head, side)

def guess_from_name(name: str) -> Tuple[str, Optional[str]]:
    nm = Path(name).name.upper()
    side = "TOP" if ("F_" in nm or "-F_" in nm or "TOP" in nm) else ("BOT" if ("B_" in nm or "-B_" in nm or "BOT" in nm or "BOTTOM" in nm) else None)
    if "ADHES" in nm or "GLUE" in nm:
        cat = "GLUE"
    elif "FAB" in nm or "ASSEMBLY" in nm:
        cat = "ASSEMBLYDRAWING"
    elif "MASK" in nm:
        cat = "SOLDERMASK"
    elif "PASTE" in nm:
        cat = "SOLDERPASTE"
    elif "EDGE" in nm or "PROFILE" in nm or "OUTLINE" in nm or "GKO" in nm:
        cat = "PROFILE"
    elif "SILK" in nm or "LEGEND" in nm:
        cat = "LEGEND"
    elif "CU" in nm or "COPPER" in nm or nm.endswith(".GTL") or nm.endswith(".GBL"):
        cat = "COPPER"
    else:
        cat = "UNKNOWN"
    if DEBUG_SORT:
        print(f"[FALLB] name→cat/side: {name} → ({cat},{side or '-'})")
    return (cat, side)


# =========================================================
# 6) 정렬 및 팔레트 생성 (사이드 누수 차단)
# =========================================================
def order_and_color_by_gbrjob(
    layer_paths: List[str],
    job_attrs: Dict[str, Dict[str, str]],
    *,
    side_hint: Optional[str] = None,
) -> Tuple[List[str], List[str], List[Tuple[str, Optional[str]]]]:
    records: List[Tuple[int, str, str, Optional[str], str, str, str]] = []

    for i, rel in enumerate(layer_paths):
        attr = find_attr_for_path(rel, job_attrs)
        file_function = (attr or {}).get("FileFunction", "")
        cat, side = parse_filefunction(file_function)
        source = "job" if attr else "name"
        if not attr:
            cat, side_guess = guess_from_name(rel)
            if side is None:
                side = side_guess
        color = COLOR_BY_CATEGORY.get(cat, COLOR_BY_CATEGORY["UNKNOWN"])

        if side_hint is not None:
            if side and side != side_hint:
                if DEBUG_SORT:
                    print(f"[FILTER] side mismatch: {rel} ({cat},{side}) != {side_hint}")
                continue
            if side is None:
                nm = Path(rel).name.upper()
                is_top_like = ("F_" in nm or "-F_" in nm or "TOP" in nm)
                is_bot_like = ("B_" in nm or "-B_" in nm or "BOT" in nm or "BOTTOM" in nm)
                if side_hint == "TOP" and not is_top_like:
                    if DEBUG_SORT:
                        print(f"[FILTER] side unknown(name says NOT TOP): {rel}")
                    continue
                if side_hint == "BOT" and not is_bot_like:
                    if DEBUG_SORT:
                        print(f"[FILTER] side unknown(name says NOT BOT): {rel}")
                    continue

        records.append((i, rel, cat, side, color, source, file_function))

    rank = {cat: idx for idx, cat in enumerate(CATEGORY_ORDER)}
    records_sorted = sorted(records, key=lambda r: (rank.get(r[2], 999), r[0]))

    sorted_paths = [p for _, p, _, _, _, _, _ in records_sorted]
    ordered_colors = [c for _, _, _, _, c, _, _ in records_sorted]
    ordered_cat_side = [(cat, side) for _, _, cat, side, _, _, _ in records_sorted]

    if DEBUG_SORT:
        print("---- 정렬 결과 (아래→위) ----")
        for j, rec in enumerate(records_sorted, 1):
            i, rel, cat, side, col, src, func = rec
            print(f"{j:02d}. {rel:<30} {cat:<16} {col:<8} ({func or src})")

    return sorted_paths, ordered_colors, ordered_cat_side


# =========================================================
# 7) 타입 추정(보조)
# =========================================================
def detect_file_type_from_kicad(file_path: str) -> FileTypeEnum:
    try:
        with open(file_path, "r", errors="ignore") as f:
            for line in f:
                if "TF.FileFunction" in line:
                    parts = line.split(",")
                    if len(parts) >= 3:
                        func = parts[1].strip().lower()
                        layer = parts[2].strip().lower()
                        if func in ("mask", "soldermask"):
                            return FileTypeEnum.TOP_MASK if layer == "top" else FileTypeEnum.BOTTOM_MASK
                        if func in ("paste", "solderpaste"):
                            return FileTypeEnum.TOP_PASTE if layer == "top" else FileTypeEnum.BOTTOM_PASTE
                        if func in ("outline", "profile"):
                            return FileTypeEnum.OUTLINE
                        if func == "legend":
                            return FileTypeEnum.TOP_SILK if layer == "top" else FileTypeEnum.BOTTOM_SILK
                        if func == "copper":
                            return FileTypeEnum.TOP_COPPER if layer == "top" else FileTypeEnum.BOTTOM_COPPER
    except Exception:
        pass
    return FileTypeEnum.UNDEFINED


# =========================================================
# 7-1) 공통 유틸 — GLUE 모드 적용
# =========================================================
def _apply_glue_mode(
    cat_side: List[Tuple[str, Optional[str]]],
    palette: List[str],
    paths_or_gfiles: List[Union[str, GerberFile]],
    *,
    glue_mode: str,  # "keep" | "drop" | "bg"
) -> Tuple[List[Tuple[str, Optional[str]]], List[str], List[Union[str, GerberFile]]]:
    """
    glue_mode:
      - keep:  변화 없음
      - drop:  cat == "GLUE" 인 항목 전부 제거
      - bg:    drop과 동일(그리지는 않음). 배경은 호출부에서 fill_background로 처리
    """
    if glue_mode not in ("keep", "drop", "bg"):
        raise ValueError("glue_mode must be 'keep' | 'drop' | 'bg'")

    if glue_mode == "keep":
        return cat_side, palette, paths_or_gfiles

    flt_cat_side: List[Tuple[str, Optional[str]]] = []
    flt_palette: List[str] = []
    flt_paths: List[Union[str, GerberFile]] = []

    for (cs, col, p) in zip(cat_side, palette, paths_or_gfiles):
        cat, side = cs
        if cat == "GLUE":
            continue
        flt_cat_side.append(cs)
        flt_palette.append(col)
        flt_paths.append(p)

    return flt_cat_side, flt_palette, flt_paths


# =========================================================
# 8) 팔레트 오버라이드(카테고리 강제 색)
# =========================================================
def _apply_category_overrides(
    cat_side_list: List[Tuple[str, Optional[str]]],
    palette_in: List[str],
    *,
    category_override_colors: Optional[Dict[str, str]] = None,
) -> List[str]:
    """카테고리별 강제 색상(예: {'GLUE': '#FFFFFF'})을 최우선 반영."""
    if not category_override_colors:
        return palette_in
    out: List[str] = []
    for (cat, _), col in zip(cat_side_list, palette_in):
        out.append(category_override_colors.get(cat, col))
    return out


# =========================================================
# 9) SVG 보조 — 배경/렌더 속성/HEX 정규화
# =========================================================
def _inject_svg_background(svg_path: str, bg_hex: str) -> None:
    """
    <svg ...> 바로 다음에 full-size 배경 rect 삽입(배경 채움).
    """
    try:
        with open(svg_path, "r", encoding="utf-8") as f:
            svg = f.read()
        i = svg.find(">")
        if i == -1:
            return
        bg_rect = f'\n  <rect width="100%" height="100%" fill="{bg_hex}"/>\n'
        svg_new = svg[:i+1] + bg_rect + svg[i+1:]
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg_new)
    except Exception as e:
        print(f"[WARN] SVG 배경 주입 실패: {e}")

def _inject_svg_root_attrs(svg_path: str, extra_style: str = None) -> None:
    """
    <svg ...> 루트에 렌더 속성(style) 주입 → 경계 또렷/스트로크 비스케일.
    """
    if extra_style is None:
        extra_style = "shape-rendering:crispEdges;vector-effect:non-scaling-stroke"
    try:
        import re as _re2
        with open(svg_path, "r", encoding="utf-8") as f:
            svg = f.read()
        m = _re2.search(r"<svg\b([^>]*)>", svg, _re2.IGNORECASE)
        if not m:
            return
        attrs = m.group(1)
        if 'style="' in attrs:
            svg_new = _re2.sub(
                r'style="([^"]*)"',
                lambda mm: f'style="{mm.group(1)};{extra_style}"',
                svg,
                count=1,
            )
        else:
            svg_new = svg.replace(m.group(0), f'<svg{attrs} style="{extra_style}">', 1)
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg_new)
    except Exception as e:
        print(f"[WARN] SVG 루트 속성 주입 실패: {e}")

def _normalize_hex(hex_str: Optional[str]) -> Optional[str]:
    if not hex_str:
        return None
    s = hex_str.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) in (3, 6):
        if len(s) == 3:
            s = "".join(ch*2 for ch in s)
        if re.fullmatch(r"[0-9A-Fa-f]{6}", s):
            return "#" + s.upper()
    raise ValueError(f"유효하지 않은 HEX 색상: {hex_str!r}")


# =========================================================
# 10) SVG 생성 — PNG와 시각 규칙 동일화 + GLUE 제어 + Assembly 제외 옵션
# =========================================================
def create_svg(
    folder_path: str,
    gbrjob: str,
    output_images_path: str,
    *,
    by_layers: bool = False,
    uniform_color: Optional[str] = None,   # 보존 카테고리=검정, 나머지 단색
    per_layer_uniform: bool = True,
    background_color: str = "#000000",
    fill_background: bool = True,
    type_composite: bool = False,
    glue_mode: str = "keep",               # "keep" | "drop" | "bg"
    include_assembly_in_main: bool = False,# 메인(top/bot)_output.svg에서 Assembly 제외(겹침 두께 방지)
    category_override_colors: Optional[Dict[str, str]] = None,  # 예: {"GLUE":"#FFFFFF"}
):
    job_path = os.path.join(folder_path, gbrjob)
    os.makedirs(output_images_path, exist_ok=True)

    print(f"확인된 파일 개수:{len(os.listdir(folder_path))}개")

    # HEX 정규화
    uniform_color = _normalize_hex(uniform_color)
    background_color = _normalize_hex(background_color) or "#000000"
    # 오버라이드 색도 정규화
    if category_override_colors:
        category_override_colors = {k: (_normalize_hex(v) or v) for k, v in category_override_colors.items()}

    job_attrs = load_gbrjob_attributes(job_path)
    front_layers, bottom_layers = Front_Botton_Divider.divide(job_path, folder_path, True)

    def _palette_for_whole(cat_side_list: List[Tuple[str, Optional[str]]],
                           orig_palette: List[str]) -> List[str]:
        if uniform_color:
            pal = ["#000000" if (cat in PRESERVE_BLACK_CATEGORIES) else uniform_color
                   for (cat, _), _orig in zip(cat_side_list, orig_palette)]
        else:
            pal = list(orig_palette)
        # 카테고리 오버라이드 최우선
        pal = _apply_category_overrides(cat_side_list, pal, category_override_colors=category_override_colors)
        return pal

    def _color_for_single_layer(cat: str, orig_color: str) -> str:
        # 카테고리 강제색 우선
        if category_override_colors and cat in category_override_colors:
            return category_override_colors[cat]
        if uniform_color and per_layer_uniform:
            return "#000000" if (cat in PRESERVE_BLACK_CATEGORIES) else uniform_color
        return orig_color

    # ---------------- TOP ----------------
    print("top 레이어 처리...")
    front_sorted, front_palette, front_cat_side = order_and_color_by_gbrjob(
        front_layers, job_attrs, side_hint="TOP"
    )

    f_cat_side, f_palette, f_paths = _apply_glue_mode(front_cat_side, front_palette, front_sorted, glue_mode=glue_mode)

    top_gfiles: List[GerberFile] = []
    for idx, rel in enumerate(f_paths, 1):
        print(f"{idx}/{len(f_paths)}, 처리중:", rel)
        fpath = os.path.join(folder_path, rel)
        gtype = GerberFile.from_file(fpath).parse().get_file_type()
        if gtype == FileTypeEnum.UNDEFINED:
            gtype = detect_file_type_from_kicad(fpath)
        top_gfiles.append(GerberFile.from_file(fpath, gtype))

    # 메인 출력용: Assembly 제외 옵션 적용
    _main_cat_side, _main_palette, _main_gfiles = f_cat_side, f_palette, top_gfiles
    if not include_assembly_in_main:
        _main_trip = [(cs, col, gf) for (cs, col, gf) in zip(f_cat_side, f_palette, top_gfiles) if cs[0] != "ASSEMBLYDRAWING"]
        if _main_trip:
            _main_cat_side = [t[0] for t in _main_trip]
            _main_palette  = [t[1] for t in _main_trip]
            _main_gfiles   = [t[2] for t in _main_trip]

    if not by_layers:
        palette = _palette_for_whole(_main_cat_side, _main_palette)
        top_svg = os.path.join(output_images_path, "top_output.svg")
        Project(_main_gfiles).parse().render_svg(
            top_svg,
            ordered_colors=palette,
            ordered_cycle=False,
            recolor_mode="both",     # SVG에서는 'both'가 팔레트 강제 적용에 안전
            inline_apertures=True,
        )
        if fill_background:
            _inject_svg_background(top_svg, background_color)
        _inject_svg_root_attrs(top_svg)  # 경계 또렷/스트로크 비스케일
        print("렌더링 완료: top_output.svg")
    else:
        for idx, (gfile, (cat, _side), color) in enumerate(zip(top_gfiles, f_cat_side, f_palette)):
            out_svg = os.path.join(output_images_path, f"f_layer_{idx}.svg")
            pal_color = _color_for_single_layer(cat, color)
            Project([gfile]).parse().render_svg(
                out_svg,
                ordered_colors=[pal_color],
                ordered_cycle=False,
                recolor_mode="both",
                inline_apertures=True,
            )
            if fill_background:
                _inject_svg_background(out_svg, background_color)
            _inject_svg_root_attrs(out_svg)

    # 카테고리별 합성(top): ASSEMBLYDRAWING을 SOLDERMASK에 병합(명시 매핑)
    if type_composite:
        cats_top: Dict[str, Dict[str, List[Union[GerberFile, str]]]] = {}
        for ((cat, _side), col, gf) in zip(f_cat_side, f_palette, top_gfiles):
            bucket = COMPOSITE_BUCKET.get(cat, "UNKNOWN")
            entry = cats_top.setdefault(bucket, {"gfiles": [], "colors": []})
            pal_color = _color_for_single_layer(bucket, col) if uniform_color or category_override_colors else col
            entry["gfiles"].append(gf)
            entry["colors"].append(pal_color)

        for bucket, bundle in cats_top.items():
            if not bundle["gfiles"]:
                continue
            out_svg_cat = os.path.join(output_images_path, f"top_{bucket.lower()}.svg")
            Project(bundle["gfiles"]).parse().render_svg(
                out_svg_cat,
                ordered_colors=bundle["colors"],
                ordered_cycle=False,
                recolor_mode="both",
                inline_apertures=True,
            )
            if fill_background:
                _inject_svg_background(out_svg_cat, background_color)
            _inject_svg_root_attrs(out_svg_cat)
            print(f"렌더링 완료: {Path(out_svg_cat).name}")

    # ---------------- BOT ----------------
    print("bottom 레이어 처리...")
    bottom_sorted, bottom_palette, bottom_cat_side = order_and_color_by_gbrjob(
        bottom_layers, job_attrs, side_hint="BOT"
    )

    b_cat_side, b_palette, b_paths = _apply_glue_mode(bottom_cat_side, bottom_palette, bottom_sorted, glue_mode=glue_mode)

    bot_gfiles: List[GerberFile] = []
    for idx, rel in enumerate(b_paths, 1):
        print(f"{idx}/{len(b_paths)}, 처리중:", rel)
        fpath = os.path.join(folder_path, rel)
        gtype = GerberFile.from_file(fpath).parse().get_file_type()
        if gtype == FileTypeEnum.UNDEFINED:
            gtype = detect_file_type_from_kicad(fpath)
        bot_gfiles.append(GerberFile.from_file(fpath, gtype))

    # 메인 출력용: Assembly 제외 옵션 적용
    _b_main_cat_side, _b_main_palette, _b_main_gfiles = b_cat_side, b_palette, bot_gfiles
    if not include_assembly_in_main:
        _b_main_trip = [(cs, col, gf) for (cs, col, gf) in zip(b_cat_side, b_palette, bot_gfiles) if cs[0] != "ASSEMBLYDRAWING"]
        if _b_main_trip:
            _b_main_cat_side = [t[0] for t in _b_main_trip]
            _b_main_palette  = [t[1] for t in _b_main_trip]
            _b_main_gfiles   = [t[2] for t in _b_main_trip]

    if not by_layers:
        palette = _palette_for_whole(_b_main_cat_side, _b_main_palette)
        bot_svg = os.path.join(output_images_path, "bot_output.svg")
        Project(_b_main_gfiles).parse().render_svg(
            bot_svg,
            ordered_colors=palette,
            ordered_cycle=False,
            recolor_mode="both",
            inline_apertures=True,
        )
        if fill_background:
            _inject_svg_background(bot_svg, background_color)
        _inject_svg_root_attrs(bot_svg)
        print("렌더링 완료: bot_output.svg")
    else:
        for idx, (gfile, (cat, _side), color) in enumerate(zip(bot_gfiles, b_cat_side, b_palette)):
            out_svg = os.path.join(output_images_path, f"b_layer_{idx}.svg")
            pal_color = _color_for_single_layer(cat, color)
            Project([gfile]).parse().render_svg(
                out_svg,
                ordered_colors=[pal_color],
                ordered_cycle=False,
                recolor_mode="both",
                inline_apertures=True,
            )
            if fill_background:
                _inject_svg_background(out_svg, background_color)
            _inject_svg_root_attrs(out_svg)

    # 카테고리별 합성(bot)
    if type_composite:
        cats_bot: Dict[str, Dict[str, List[Union[GerberFile, str]]]] = {}
        for ((cat, _side), col, gf) in zip(b_cat_side, b_palette, bot_gfiles):
            bucket = COMPOSITE_BUCKET.get(cat, "UNKNOWN")
            entry = cats_bot.setdefault(bucket, {"gfiles": [], "colors": []})
            pal_color = _color_for_single_layer(bucket, col) if uniform_color or category_override_colors else col
            entry["gfiles"].append(gf)
            entry["colors"].append(pal_color)

        for bucket, bundle in cats_bot.items():
            if not bundle["gfiles"]:
                continue
            out_svg_cat = os.path.join(output_images_path, f"bot_{bucket.lower()}.svg")
            Project(bundle["gfiles"]).parse().render_svg(
                out_svg_cat,
                ordered_colors=bundle["colors"],
                ordered_cycle=False,
                recolor_mode="both",
                inline_apertures=True,
            )
            if fill_background:
                _inject_svg_background(out_svg_cat, background_color)
            _inject_svg_root_attrs(out_svg_cat)
            print(f"렌더링 완료: {Path(out_svg_cat).name}")


# =========================================================
# 11) PNG 생성 — GLUE 제어/팔레트 오버라이드 동일 반영
# =========================================================
def create_png(
    folder_path: str,
    gbrjob: str,
    output_images_path: str,
    by_layers: bool = False,
    dpmm: int = 30,
    *,
    uniform_color: Optional[str] = None,   # 전체 단색(윤곽/실크 검정 보존)
    per_layer_uniform: bool = True,        # 레이어별 PNG에도 단색 적용
    background_color: str = "#000000",     # 투명 영역을 채울 배경색
    fill_background: bool = True,          # 투명영역 채우기 여부
    quality: int = 90,
    type_composite: bool = False,          # 카테고리별 합성 PNG 생성
    create_full: bool = False,             # (호환용)
    glue_mode: str = "keep",               # "keep" | "drop" | "bg"
    category_override_colors: Optional[Dict[str, str]] = None,  # 예: {"GLUE":"#FFFFFF"}
):
    job_path = os.path.join(folder_path, gbrjob)
    os.makedirs(output_images_path, exist_ok=True)

    print(f"확인된 파일 개수:{len(os.listdir(folder_path))}개")

    # 색 정규화
    def _norm_hex(s: Optional[str], default: Optional[str] = None) -> Optional[str]:
        if s is None:
            return default
        t = s.strip()
        if t.startswith("#"):
            t = t[1:]
        if len(t) == 3:
            t = "".join(ch*2 for ch in t)
        if re.fullmatch(r"[0-9A-Fa-f]{6}", t):
            return "#" + t.upper()
        raise ValueError(f"유효하지 않은 HEX 색상: {s!r}")

    uniform_color = _norm_hex(uniform_color)
    background_color = _norm_hex(background_color, "#000000") or "#000000"
    if category_override_colors:
        category_override_colors = {k: (_norm_hex(v) or v) for k, v in category_override_colors.items()}

    job_attrs = load_gbrjob_attributes(job_path)
    front_layers, bottom_layers = Front_Botton_Divider.divide(job_path, folder_path, True)

    def _palette_for_whole(cat_side_list: List[Tuple[str, Optional[str]]],
                           orig_palette: List[str]) -> List[str]:
        if uniform_color:
            pal = ["#000000" if (cat in PRESERVE_BLACK_CATEGORIES) else uniform_color
                   for (cat, _), _orig in zip(cat_side_list, orig_palette)]
        else:
            pal = list(orig_palette)
        # 카테고리 오버라이드 최우선
        pal = _apply_category_overrides(cat_side_list, pal, category_override_colors=category_override_colors)
        return pal

    def _color_for_single_layer(cat: str, orig_color: str) -> str:
        if category_override_colors and cat in category_override_colors:
            return category_override_colors[cat]
        if uniform_color and per_layer_uniform:
            return "#000000" if (cat in PRESERVE_BLACK_CATEGORIES) else uniform_color
        return orig_color

    # ---- FRONT (TOP) ----
    print("top 레이어 처리...")
    front_sorted, front_palette, front_cat_side = order_and_color_by_gbrjob(front_layers, job_attrs, side_hint="TOP")
    f_cat_side, f_palette, f_paths = _apply_glue_mode(front_cat_side, front_palette, front_sorted, glue_mode=glue_mode)
    top_gfiles: List[GerberFile] = []
    for idx, rel in enumerate(f_paths, 1):
        print(f"{idx}/{len(f_paths)}, 처리중:", rel)
        fpath = os.path.join(folder_path, rel)
        gtype = GerberFile.from_file(fpath).parse().get_file_type()
        if gtype == FileTypeEnum.UNDEFINED:
            gtype = detect_file_type_from_kicad(fpath)
        top_gfiles.append(GerberFile.from_file(fpath, gtype))

    # 전체 합성(top)
    if not by_layers:
        palette = _palette_for_whole(f_cat_side, f_palette)
        top_out = os.path.join(output_images_path, "top_output.png")
        Project(top_gfiles).parse().render_raster(
            top_out,
            ordered_colors=palette,
            ordered_cycle=False,
            recolor_mode="tint",
            dpmm=dpmm,
        )
        img = Image.open(top_out)
        if fill_background:
            img = _fill_transparent_with_color(img, background_color)
        if uniform_color:
            img = _recolor_uniform_preserve_black(img, uniform_color)
        _save_image(img, top_out, quality=quality)
        print("렌더링 완료: top_output.png")
    else:
        for idx, (gfile, (cat, _side), color) in enumerate(zip(top_gfiles, f_cat_side, f_palette)):
            out_path = os.path.join(output_images_path, f'f_layer_{idx}.png')
            pal_color = _color_for_single_layer(cat, color)
            Project([gfile]).parse().render_raster(
                out_path,
                ordered_colors=[pal_color],
                ordered_cycle=False,
                recolor_mode="tint",
                dpmm=dpmm,
            )
            img = Image.open(out_path)
            if fill_background:
                img = _fill_transparent_with_color(img, background_color)
            if uniform_color and per_layer_uniform:
                img = _recolor_uniform_preserve_black(img, uniform_color)
            _save_image(img, out_path, quality=quality)

    # 카테고리별 합성(top)
    if type_composite:
        cats_top: Dict[str, Dict[str, List[Union[GerberFile, str]]]] = {}
        for ((cat, _side), col, gf) in zip(f_cat_side, f_palette, top_gfiles):
            composite_key = "SOLDERMASK" if cat in ("SOLDERMASK", "ASSEMBLYDRAWING") else cat
            entry = cats_top.setdefault(composite_key, {"gfiles": [], "colors": []})
            pal_color = _color_for_single_layer(composite_key, col)
            entry["gfiles"].append(gf)
            entry["colors"].append(pal_color)

        for cat_key, bundle in cats_top.items():
            if not bundle["gfiles"]:
                continue
            out_path_cat = os.path.join(output_images_path, f"top_{cat_key.lower()}.png")
            Project(bundle["gfiles"]).parse().render_raster(
                out_path_cat,
                ordered_colors=bundle["colors"],
                ordered_cycle=False,
                recolor_mode="tint",
                dpmm=dpmm,
            )
            img = Image.open(out_path_cat)
            if fill_background:
                img = _fill_transparent_with_color(img, background_color)
            if uniform_color:
                img = _recolor_uniform_preserve_black(img, uniform_color)
            _save_image(img, out_path_cat, quality=quality)

    # ---- BOTTOM (BOT) ----
    print("bottom 레이어 처리...")
    bottom_sorted, bottom_palette, bottom_cat_side = order_and_color_by_gbrjob(bottom_layers, job_attrs, side_hint="BOT")
    b_cat_side, b_palette, b_paths = _apply_glue_mode(bottom_cat_side, bottom_palette, bottom_sorted, glue_mode=glue_mode)
    bot_gfiles: List[GerberFile] = []
    for idx, rel in enumerate(b_paths, 1):
        print(f"{idx}/{len(b_paths)}, 처리중:", rel)
        fpath = os.path.join(folder_path, rel)
        gtype = GerberFile.from_file(fpath).parse().get_file_type()
        if gtype == FileTypeEnum.UNDEFINED:
            gtype = detect_file_type_from_kicad(fpath)
        bot_gfiles.append(GerberFile.from_file(fpath, gtype))

    if not by_layers:
        palette = _palette_for_whole(b_cat_side, b_palette)
        bot_out = os.path.join(output_images_path, "bot_output.png")
        Project(bot_gfiles).parse().render_raster(
            bot_out,
            ordered_colors=palette,
            ordered_cycle=False,
            recolor_mode="tint",
            dpmm=dpmm,
        )
        img = Image.open(bot_out)
        if fill_background:
            img = _fill_transparent_with_color(img, background_color)
        if uniform_color:
            img = _recolor_uniform_preserve_black(img, uniform_color)
        _save_image(img, bot_out, quality=quality)
        print("렌더링 완료: bot_output.png")
    else:
        for idx, (gfile, (cat, _side), color) in enumerate(zip(bot_gfiles, b_cat_side, b_palette)):
            out_path = os.path.join(output_images_path, f'b_layer_{idx}.png')
            pal_color = _color_for_single_layer(cat, color)
            Project([gfile]).parse().render_raster(
                out_path,
                ordered_colors=[pal_color],
                ordered_cycle=False,
                recolor_mode="tint",
                dpmm=dpmm,
            )
            img = Image.open(out_path)
            if fill_background:
                img = _fill_transparent_with_color(img, background_color)
            if uniform_color and per_layer_uniform:
                img = _recolor_uniform_preserve_black(img, uniform_color)
            _save_image(img, out_path, quality=quality)

    # 카테고리별 합성(bot)
    if type_composite:
        cats_bot: Dict[str, Dict[str, List[Union[GerberFile, str]]]] = {}
        for ((cat, _side), col, gf) in zip(b_cat_side, b_palette, bot_gfiles):
            composite_key = "SOLDERMASK" if cat in ("SOLDERMASK", "ASSEMBLYDRAWING") else cat
            entry = cats_bot.setdefault(composite_key, {"gfiles": [], "colors": []})
            pal_color = _color_for_single_layer(composite_key, col)
            entry["gfiles"].append(gf)
            entry["colors"].append(pal_color)

        for cat_key, bundle in cats_bot.items():
            if not bundle["gfiles"]:
                continue
            out_path_cat = os.path.join(output_images_path, f"bot_{cat_key.lower()}.png")
            Project(bundle["gfiles"]).parse().render_raster(
                out_path_cat,
                ordered_colors=bundle["colors"],
                ordered_cycle=False,
                recolor_mode="tint",
                dpmm=dpmm,
            )
            img = Image.open(out_path_cat)
            if fill_background:
                img = _fill_transparent_with_color(img, background_color)
            if uniform_color:
                img = _recolor_uniform_preserve_black(img, uniform_color)
            _save_image(img, out_path_cat, quality=quality)

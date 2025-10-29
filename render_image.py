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
#    - 필요 시 호출부에서 GLUE를 흰색으로 바꾸면 그대로 반영됨
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
# GLUE는 더 이상 무조건 보존하지 않습니다(사용자가 지정한 색이 반영되도록).
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
    → 윤곽/실크의 검정은 유지, GLUE는 팔레트/호출 설정에 따름.
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
    투명(알파=0) 영역을 지정한 색으로 메움. (기본: 사용자 지정 배경색)
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
# 6) 정렬 및 팔레트 생성
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
                if cat == 'SOLDERMASK':
                    print(f"[FILTER] side exception skipped for SOLDERMASK: ({rel}, {cat})")
                else:
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

    if DEBUG_SORT:
        print("---- [DEBUG] CANDIDATES BEFORE SORT ----")
        for rec in records:
            i, rel, cat, side, col, src, func = rec
            print(f"  idx={i:02d} src={src:<4} cat={cat:<16} side={str(side or '-'):>3} col={col:<8} func='{func}'  -> {rel}")

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
# 8) SVG 생성 — 팔레트 기반(배경은 GLUE 색상에 따름)
# =========================================================
def create_svg(
    folder_path: str,
    gbrjob: str,
    output_images_path: str,
    *,
    uniform_color: Optional[str] = None,   # 전체 단색(윤곽/실크는 검정 보존)
):
    job_path = os.path.join(folder_path, gbrjob)
    os.makedirs(output_images_path, exist_ok=True)

    print(f"확인된 파일 개수:{len(os.listdir(folder_path))}개")

    job_attrs = load_gbrjob_attributes(job_path)
    front_layers, bottom_layers = Front_Botton_Divider.divide(job_path, folder_path, True)

    # ---- FRONT (TOP) ----
    print("top 레이어 처리...")
    front_sorted, front_palette, front_cat_side = order_and_color_by_gbrjob(front_layers, job_attrs, side_hint="TOP")
    top_gfiles: List[GerberFile] = []
    for idx, rel in enumerate(front_sorted, 1):
        print(f"{idx}/{len(front_sorted)}, 처리중:", rel)
        fpath = os.path.join(folder_path, rel)
        gtype = GerberFile.from_file(fpath).parse().get_file_type()
        if gtype == FileTypeEnum.UNDEFINED:
            gtype = detect_file_type_from_kicad(fpath)
        top_gfiles.append(GerberFile.from_file(fpath, gtype))

    if uniform_color:
        svg_palette = []
        for (cat, _), orig in zip(front_cat_side, front_palette):
            if cat in PRESERVE_BLACK_CATEGORIES:
                svg_palette.append("#000000")
            else:
                svg_palette.append(uniform_color)
    else:
        svg_palette = front_palette

    Project(top_gfiles).parse().render_svg(
        os.path.join(output_images_path, "top_output.svg"),
        ordered_colors=svg_palette,
        inline_apertures=True,
        recolor_mode="both",
    )
    print("렌더링 완료:top_output.svg")

    # ---- BOTTOM (BOT) ----
    print("bottom 레이어 처리...")
    bottom_sorted, bottom_palette, bottom_cat_side = order_and_color_by_gbrjob(bottom_layers, job_attrs, side_hint="BOT")
    bot_gfiles: List[GerberFile] = []
    for idx, rel in enumerate(bottom_sorted, 1):
        print(f"{idx}/{len(bottom_sorted)}, 처리중:", rel)
        fpath = os.path.join(folder_path, rel)
        gtype = GerberFile.from_file(fpath).parse().get_file_type()
        if gtype == FileTypeEnum.UNDEFINED:
            gtype = detect_file_type_from_kicad(fpath)
        bot_gfiles.append(GerberFile.from_file(fpath, gtype))

    if uniform_color:
        svg_palette_bot = []
        for (cat, _), orig in zip(bottom_cat_side, bottom_palette):
            if cat in PRESERVE_BLACK_CATEGORIES:
                svg_palette_bot.append("#000000")
            else:
                svg_palette_bot.append(uniform_color)
    else:
        svg_palette_bot = bottom_palette

    Project(bot_gfiles).parse().render_svg(
        os.path.join(output_images_path, "bot_output.svg"),
        ordered_colors=svg_palette_bot,
        inline_apertures=True,
        recolor_mode="both",
    )
    print("렌더링 완료:bot_output.svg")


# =========================================================
# 9) PNG 생성 — 배경색 선택 가능
#     ※ by_layers=True에서는 GerberFile.render_raster()를 직접 쓰지 않고
#        Project([gfile]).render_raster()로 우회(버전 차이 안전)
#     ※ 신규 기능: type_composite=True 시 카테고리별 합성 PNG 추가 생성
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
    create_full: bool = False,             # (유지용 파라미터; 아래 구현에서는 항상 전체 PNG 생성)
):
    job_path = os.path.join(folder_path, gbrjob)
    os.makedirs(output_images_path, exist_ok=True)

    print(f"확인된 파일 개수:{len(os.listdir(folder_path))}개")

    job_attrs = load_gbrjob_attributes(job_path)
    front_layers, bottom_layers = Front_Botton_Divider.divide(job_path, folder_path, True)

    # ---- FRONT (TOP) ----
    print("top 레이어 처리...")
    front_sorted, front_palette, front_cat_side = order_and_color_by_gbrjob(front_layers, job_attrs, side_hint="TOP")
    top_gfiles: List[GerberFile] = []
    for idx, rel in enumerate(front_sorted, 1):
        print(f"{idx}/{len(front_sorted)}, 처리중:", rel)
        fpath = os.path.join(folder_path, rel)
        gtype = GerberFile.from_file(fpath).parse().get_file_type()
        if gtype == FileTypeEnum.UNDEFINED:
            gtype = detect_file_type_from_kicad(fpath)
        top_gfiles.append(GerberFile.from_file(fpath, gtype))

    # 전체 합성(top) — by_layers=False면 항상 생성
    if not by_layers:
        # 팔레트 구성
        if uniform_color:
            palette = ["#000000" if (cat in PRESERVE_BLACK_CATEGORIES) else uniform_color
                       for (cat, _), _orig in zip(front_cat_side, front_palette)]
        else:
            palette = front_palette

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
        # 레이어별 개별 출력(top)
        for idx, (gfile, (cat, _side), color) in enumerate(zip(top_gfiles, front_cat_side, front_palette)):
            out_path = os.path.join(output_images_path, f'f_layer_{idx}.png')
            pal_color = "#000000" if (uniform_color and cat in PRESERVE_BLACK_CATEGORIES) else (uniform_color or color)
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

    # 카테고리별 합성(top): ASSEMBLYDRAWING을 SOLDERMASK와 합침
    if type_composite:
        cats_top: Dict[str, Dict[str, List[Union[GerberFile, str]]]] = {}
        for ((cat, _side), col, gf) in zip(front_cat_side, front_palette, top_gfiles):
            composite_key = "SOLDERMASK" if cat in ("SOLDERMASK", "ASSEMBLYDRAWING") else cat
            entry = cats_top.setdefault(composite_key, {"gfiles": [], "colors": []})
            entry["gfiles"].append(gf)
            pal_color = ("#000000" if (uniform_color and composite_key in PRESERVE_BLACK_CATEGORIES)
                         else (uniform_color or col))
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
    bot_gfiles: List[GerberFile] = []
    for idx, rel in enumerate(bottom_sorted, 1):
        print(f"{idx}/{len(bottom_sorted)}, 처리중:", rel)
        fpath = os.path.join(folder_path, rel)
        gtype = GerberFile.from_file(fpath).parse().get_file_type()
        if gtype == FileTypeEnum.UNDEFINED:
            gtype = detect_file_type_from_kicad(fpath)
        bot_gfiles.append(GerberFile.from_file(fpath, gtype))

    # 전체 합성(bot) — by_layers=False면 항상 생성
    if not by_layers:
        if uniform_color:
            palette = ["#000000" if (cat in PRESERVE_BLACK_CATEGORIES) else uniform_color
                       for (cat, _), _orig in zip(bottom_cat_side, bottom_palette)]
        else:
            palette = bottom_palette

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
        # 레이어별 개별 출력(bot)
        for idx, (gfile, (cat, _side), color) in enumerate(zip(bot_gfiles, bottom_cat_side, bottom_palette)):
            out_path = os.path.join(output_images_path, f'b_layer_{idx}.png')
            pal_color = "#000000" if (uniform_color and cat in PRESERVE_BLACK_CATEGORIES) else (uniform_color or color)
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

    # 카테고리별 합성(bot): ASSEMBLYDRAWING을 SOLDERMASK와 합침
    if type_composite:
        cats_bot: Dict[str, Dict[str, List[Union[GerberFile, str]]]] = {}
        for ((cat, _side), col, gf) in zip(bottom_cat_side, bottom_palette, bot_gfiles):
            composite_key = "SOLDERMASK" if cat in ("SOLDERMASK", "ASSEMBLYDRAWING") else cat
            entry = cats_bot.setdefault(composite_key, {"gfiles": [], "colors": []})
            entry["gfiles"].append(gf)
            pal_color = ("#000000" if (uniform_color and composite_key in PRESERVE_BLACK_CATEGORIES)
                         else (uniform_color or col))
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

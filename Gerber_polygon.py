#!/usr/bin/env python3
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
import math
import os
import re
import json

def fill_gerber_outline_to_region(
    input_path: str,
    output_path: str,
    *,
    snap_tol_mm: float = 0.02,
    max_seg_len_mm: float = 0.2,
    max_angle_deg: float = 5.0,
) -> Dict[str, object]:
    """
    RS-274X 기반의 '외곽선(선분/원호)' Gerber를 자동 보정/정렬하여
    G36/G37 Region(채움)으로 내보내는 단일 함수 진입점.

    - 임포트 사용 예:
        from gerber_fill_region_fn import fill_gerber_outline_to_region
        info = fill_gerber_outline_to_region("in.gbr", "out_filled.gbr", snap_tol_mm=0.05)

    매개변수
    ----------
    input_path : str
        입력 Gerber 파일 경로(외곽선/글루 레이어 등).
    output_path : str
        출력(Region G36/G37) Gerber 파일 경로.
    snap_tol_mm : float, default 0.02
        끝점 스냅/병합 허용 오차(mm). 미세한 오프셋을 정리하여 루프를 닫음.
    max_seg_len_mm : float, default 0.2
        원호(G02/G03) 폴리라인 근사 시 최대 현 길이(mm).
    max_angle_deg : float, default 5.0
        원호 근사 시 분할 최대 각도(도).

    반환
    ----
    Dict[str, object]
        처리 요약 정보(입출력 경로, 파싱된 경로 수, 닫힌 폴리곤 수, FS/단위 등).

    제한/가정
    --------
    - RS-274X 서브셋 지원:
      %MO(IN/MM)%, %FS..%, %ADD..% 헤더, G01/G02/G03, D02(move)/D01(draw), I/J 원호 중심(시작점 기준 상대) 등.
    - 원호 θ 모드, 단일 사분면(G74) 등 특수 보간 모드는 미지원(일반 대다수 Glue/Outline에는 G75+I/J가 보편적).
    - 내부 단위는 mm로 정규화 후, 출력 시 입력 단위(MM/INCH) 그대로 유지.
    - Region 내부에서는 아퍼처 폭은 무시되나, RS-274X 문법상 활성 아퍼처 D코드는 선택해 둠(없으면 기본 ADD10 추가).
    """

    # -----------------------------
    # 내부 헬퍼/데이터 구조 정의
    # -----------------------------
    @dataclass
    class FSFormat:
        absolute: bool = True               # A vs I
        zero_suppression: str = "L"         # 'L' or 'T'
        x_int: int = 2
        x_dec: int = 5
        y_int: int = 2
        y_dec: int = 5
        @property
        def x_total(self) -> int: return self.x_int + self.x_dec
        @property
        def y_total(self) -> int: return self.y_int + self.y_dec

    @dataclass
    class GState:
        fs: FSFormat = field(default_factory=FSFormat)
        unit_mm: bool = True               # True for mm, False for inch
        cur_x: Optional[float] = None      # mm
        cur_y: Optional[float] = None      # mm
        interp_mode: str = "G01"           # "G01", "G02", "G03"
        current_aperture: Optional[str] = None  # e.g., "D10"
        quadrant_mode: str = "G75"         # default multi-quadrant

    @dataclass
    class Path:
        points: List[Tuple[float, float]] = field(default_factory=list)  # (x_mm, y_mm)
        def add_point(self, x: float, y: float):
            if not self.points or (self.points[-1][0] != x or self.points[-1][1] != y):
                self.points.append((x, y))

    FS_RE   = re.compile(r"^%FS")
    MOIN_RE = re.compile(r"^%MO(IN|MM)\*%")
    APERT_RE= re.compile(r"^%ADD")
    USE_APERT_RE = re.compile(r"^D(\d+)\*")
    QUAD_RE = re.compile(r"^G7([45])\*")
    MOVE_RE = re.compile(
        r"^(?:G0?([1234-7]))?\s*"
        r"(?:X([+\-]?\d+))?"
        r"(?:Y([+\-]?\d+))?"
        r"(?:I([+\-]?\d+))?"
        r"(?:J([+\-]?\d+))?"
        r"(?:D0?([123]))?\*"
    )
    END_RE  = re.compile(r"^M02\*$")

    def parse_fs(line: str) -> FSFormat:
        body = line.strip().strip("%").strip("*")
        assert body.startswith("FS"), f"Invalid FS line: {line}"
        body = body[2:]
        zero = "L" if "L" in body else ("T" if "T" in body else "L")
        absolute = "A" in body
        xfmt = re.search(r"X(\d)(\d)", body)
        yfmt = re.search(r"Y(\d)(\d)", body)
        x_int = int(xfmt.group(1)) if xfmt else 2
        x_dec = int(xfmt.group(2)) if xfmt else 5
        y_int = int(yfmt.group(1)) if yfmt else 2
        y_dec = int(yfmt.group(2)) if yfmt else 5
        return FSFormat(absolute, zero, x_int, x_dec, y_int, y_dec)

    def parse_coord_field(token: Optional[str], total: int, dec: int, zero_supp: str,
                          prev_val: Optional[float], unit_mm_flag: bool) -> Optional[float]:
        if token is None:
            return prev_val
        s = token
        sign = 1
        if s.startswith("+"):
            s = s[1:]
        elif s.startswith("-"):
            sign = -1
            s = s[1:]
        if zero_supp == "L":
            s = s.zfill(total)
        else:
            s = s.ljust(total, "0")
        int_part = s[: total - dec] if dec > 0 else s
        frac_part = s[total - dec :] if dec > 0 else ""
        ival = int(int_part) if int_part else 0
        fval = int(frac_part) / (10 ** dec) if frac_part else 0.0
        v = sign * (ival + fval)  # mm 또는 inch
        if unit_mm_flag:
            return v
        return v * 25.4  # inch → mm

    def format_coord_from_mm(v_mm: float, total: int, dec: int, zero_supp: str, unit_mm_flag: bool) -> str:
        v = v_mm if unit_mm_flag else (v_mm / 25.4)
        sign = "-" if v < 0 else ""
        v = abs(v)
        ival = int(math.floor(v))
        fval = round((v - ival) * (10 ** dec))
        if fval >= 10 ** dec:
            ival += 1
            fval = 0
        raw = f"{ival:0{total - dec}d}{fval:0{dec}d}" if dec > 0 else f"{ival:0{total}d}"
        if zero_supp == "L":
            raw = raw.lstrip("0") or ("0" if dec == 0 else "")
        else:
            raw = raw.rstrip("0") or "0"
        return f"{sign}{raw}"

    def arc_points(start: Tuple[float,float], end: Tuple[float,float], center: Tuple[float,float],
                   cw: bool, seg_len_mm: float, angle_deg: float) -> List[Tuple[float,float]]:
        sx, sy = start; ex, ey = end; cx, cy = center
        r0 = math.hypot(sx - cx, sy - cy)
        r1 = math.hypot(ex - cx, ey - cy)
        r = (r0 + r1) / 2.0
        a0 = math.atan2(sy - cy, sx - cx)
        a1 = math.atan2(ey - cy, ex - cx)
        sweep = a1 - a0
        if cw:
            while sweep >= 0: sweep -= 2 * math.pi
        else:
            while sweep <= 0: sweep += 2 * math.pi
        max_angle = math.radians(angle_deg)
        n_by_angle = max(1, int(abs(sweep) / max_angle + 0.5))
        n_by_len   = max(1, int((abs(sweep) * r) / seg_len_mm + 0.5))
        n = max(n_by_angle, n_by_len)
        pts: List[Tuple[float,float]] = []
        for k in range(1, n + 1):
            t = a0 + sweep * (k / n)
            x = cx + r * math.cos(t)
            y = cy + r * math.sin(t)
            pts.append((x, y))
        return pts

    def signed_area(poly: List[Tuple[float,float]]) -> float:
        a = 0.0
        n = len(poly)
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i+1) % n]
            a += x1*y2 - x2*y1
        return 0.5 * a

    # -----------------------------
    # 1) 파싱: 선/원호를 폴리라인 Path들로 수집
    # -----------------------------
    state = GState()
    header: Dict[str, str] = {}
    paths: List[Path] = []
    cur_path: Optional[Path] = None
    last_x_tok: Optional[str] = None
    last_y_tok: Optional[str] = None

    with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # Header 유지
        if FS_RE.match(line):
            state.fs = parse_fs(line)
            header["FS"] = line
            continue
        m = MOIN_RE.match(line)
        if m:
            state.unit_mm = (m.group(1) == "MM")
            header["MO"] = line
            continue
        if APERT_RE.match(line):
            header.setdefault("APERTURES", "")
            header["APERTURES"] += line + "\n"
            continue
        m = QUAD_RE.match(line)
        if m:
            state.quadrant_mode = "G7" + m.group(1)
            continue
        if END_RE.match(line):
            break

        # G01/G02/G03 단독 지정
        g_only = re.match(r"^G0?([123])\*$", line)
        if g_only:
            state.interp_mode = "G0" + g_only.group(1)
            continue

        # 이동/그리기 명령
        m = MOVE_RE.match(line)
        if not m:
            continue

        g = m.group(1)      # 1,2,3,...
        x_tok = m.group(2)
        y_tok = m.group(3)
        i_tok = m.group(4)
        j_tok = m.group(5)
        d     = m.group(6)  # 1/2/3

        if g in ("1", "2", "3"):
            state.interp_mode = "G0" + g

        # 모달 좌표 처리
        x_use = x_tok if x_tok is not None else last_x_tok
        y_use = y_tok if y_tok is not None else last_y_tok
        if x_tok is not None: last_x_tok = x_tok
        if y_tok is not None: last_y_tok = y_tok

        new_x = parse_coord_field(x_use, state.fs.x_total, state.fs.x_dec, state.fs.zero_suppression, state.cur_x, state.unit_mm)
        new_y = parse_coord_field(y_use, state.fs.y_total, state.fs.y_dec, state.fs.zero_suppression, state.cur_y, state.unit_mm)

        if d == "2":  # D02: move
            state.cur_x, state.cur_y = new_x, new_y
            cur_path = Path()
            if state.cur_x is not None and state.cur_y is not None:
                cur_path.add_point(state.cur_x, state.cur_y)
                paths.append(cur_path)
            continue

        # draw (D01 or modal draw)
        if d == "1" or (d is None and state.interp_mode in ("G01", "G02", "G03")):
            if cur_path is None:
                cur_path = Path()
                if state.cur_x is not None and state.cur_y is not None:
                    cur_path.add_point(state.cur_x, state.cur_y)
                elif new_x is not None and new_y is not None:
                    cur_path.add_point(new_x, new_y)
                paths.append(cur_path)

            if state.interp_mode == "G01":
                if new_x is not None and new_y is not None:
                    cur_path.add_point(new_x, new_y)
                    state.cur_x, state.cur_y = new_x, new_y
            elif state.interp_mode in ("G02", "G03"):
                # I/J 없으면 직선 대체
                if i_tok is None or j_tok is None or new_x is None or new_y is None or state.cur_x is None or state.cur_y is None:
                    if new_x is not None and new_y is not None:
                        cur_path.add_point(new_x, new_y)
                        state.cur_x, state.cur_y = new_x, new_y
                else:
                    i_val = parse_coord_field(i_tok, state.fs.x_total, state.fs.x_dec, state.fs.zero_suppression, 0.0, state.unit_mm) or 0.0
                    j_val = parse_coord_field(j_tok, state.fs.y_total, state.fs.y_dec, state.fs.zero_suppression, 0.0, state.unit_mm) or 0.0
                    cx, cy = state.cur_x + i_val, state.cur_y + j_val
                    cw = (state.interp_mode == "G02")
                    pts = arc_points((state.cur_x, state.cur_y), (new_x, new_y), (cx, cy), cw, max_seg_len_mm, max_angle_deg)
                    for px, py in pts:
                        cur_path.add_point(px, py)
                    state.cur_x, state.cur_y = new_x, new_y

    # -----------------------------
    # 2) 스냅/병합으로 루프 닫기
    # -----------------------------
    def snap_close_paths(paths: List[Path], tol: float) -> List[Path]:
        # 개별 경로: 시작/끝 스냅
        for p in paths:
            if len(p.points) >= 2:
                sx, sy = p.points[0]
                ex, ey = p.points[-1]
                if math.hypot(sx - ex, sy - ey) <= tol:
                    p.points[-1] = (sx, sy)

        closed: List[Path] = []
        open_paths: List[Path] = []
        for p in paths:
            if len(p.points) < 2:
                continue
            if p.points[0] == p.points[-1]:
                closed.append(p)
            else:
                open_paths.append(p)

        def endpoints(p: Path): return p.points[0], p.points[-1]

        # 최근접 끝점끼리 그리디 병합
        changed = True
        while changed and len(open_paths) > 1:
            changed = False
            best = None  # (i,j,ri,rj,dist)
            for i in range(len(open_paths)):
                pi = open_paths[i]; si, ei = endpoints(pi)
                for j in range(i+1, len(open_paths)):
                    pj = open_paths[j]; sj, ej = endpoints(pj)
                    candidates = [
                        (i,j,False,False, math.hypot(ei[0]-sj[0], ei[1]-sj[1])),
                        (i,j,False,True,  math.hypot(ei[0]-ej[0], ei[1]-ej[1])),
                        (i,j,True, False, math.hypot(si[0]-sj[0], si[1]-sj[1])),
                        (i,j,True, True,  math.hypot(si[0]-ej[0], si[1]-ej[1])),
                    ]
                    for cand in candidates:
                        if cand[4] <= tol * 1.5 and (best is None or cand[4] < best[4]):
                            best = cand
            if best:
                i,j,ri,rj,dist = best
                pi = open_paths[i]; pj = open_paths[j]
                if ri: pi.points.reverse()
                if rj: pj.points.reverse()
                if math.hypot(pi.points[-1][0]-pj.points[0][0], pi.points[-1][1]-pj.points[0][1]) <= tol:
                    merged = pi.points + pj.points[1:]
                else:
                    merged = pi.points + pj.points
                newp = Path(points=merged)
                for idx in sorted([i,j], reverse=True):
                    open_paths.pop(idx)
                open_paths.append(newp)
                changed = True

        # 최종 닫힘
        for p in open_paths:
            if len(p.points) >= 2:
                sx, sy = p.points[0]; ex, ey = p.points[-1]
                if math.hypot(sx - ex, sy - ey) <= tol * 1.5:
                    p.points[-1] = (sx, sy)
                else:
                    p.points.append((sx, sy))
                closed.append(p)
        return closed

    closed = snap_close_paths(paths, snap_tol_mm)

    # -----------------------------
    # 3) 시계방향 정렬
    # -----------------------------
    polys: List[Path] = []
    for p in closed:
        if len(p.points) < 3:
            continue
        pts = p.points[:]
        core = pts[:-1] if pts[0] == pts[-1] else pts
        a = signed_area(core)
        if a > 0:     # CCW -> reverse to CW
            pts = pts[::-1]
        polys.append(Path(points=pts))

    # -----------------------------
    # 4) Region(G36/G37)로 출력
    # -----------------------------
    out_lines: List[str] = []
    if "FS" in header: out_lines.append(header["FS"])
    else:              out_lines.append("%FSLAX35Y35*%")
    if "MO" in header: out_lines.append(header["MO"])
    else:              out_lines.append("%MOMM*%")

    # 아퍼처 정의 보존(or 기본 추가)
    apert_raw = header.get("APERTURES", "").rstrip("\n")
    if apert_raw:
        out_lines.append(aperts_raw := apert_raw)
        m = re.search(r"D(\d+)\*", apert_raw)
        if m:
            out_lines.append(f"D{m.group(1)}*")
        else:
            out_lines.append("%ADD10C,0.100*%"); out_lines.append("D10*")
    else:
        out_lines.append("%ADD10C,0.100*%"); out_lines.append("D10*")

    out_lines.append("G36*")
    for p in polys:
        if len(p.points) < 3: continue
        pts = p.points[:]
        if pts[0] != pts[-1]:
            pts.append(pts[0])

        x0 = format_coord_from_mm(pts[0][0], state.fs.x_total, state.fs.x_dec, state.fs.zero_suppression, state.unit_mm)
        y0 = format_coord_from_mm(pts[0][1], state.fs.y_total, state.fs.y_dec, state.fs.zero_suppression, state.unit_mm)
        out_lines.append(f"X{x0}Y{y0}D02*")
        for x, y in pts[1:]:
            xs = format_coord_from_mm(x, state.fs.x_total, state.fs.x_dec, state.fs.zero_suppression, state.unit_mm)
            ys = format_coord_from_mm(y, state.fs.y_total, state.fs.y_dec, state.fs.zero_suppression, state.unit_mm)
            out_lines.append(f"X{xs}Y{ys}D01*")
    out_lines.append("G37*")
    out_lines.append("M02*")

    # 파일 저장
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fw:
        fw.write("\n".join(out_lines))

    return {
        "input": input_path,
        "output": output_path,
        "num_input_paths": len(paths),
        "num_closed_polys": len(polys),
        "fs": {
            "absolute": state.fs.absolute,
            "zero_suppression": state.fs.zero_suppression,
            "x_int": state.fs.x_int,
            "x_dec": state.fs.x_dec,
            "y_int": state.fs.y_int,
            "y_dec": state.fs.y_dec,
        },
        "unit_mm": state.unit_mm,
        "params": {
            "snap_tol_mm": snap_tol_mm,
            "max_seg_len_mm": max_seg_len_mm,
            "max_angle_deg": max_angle_deg,
        },
    }

# 단독 실행 지원 (옵션)
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Gerber outline(선/원호) → Region(G36/G37) 자동 채움 함수")
    ap.add_argument("input", help="입력 Gerber(.gbr) 경로")
    ap.add_argument("output", help="출력 Gerber(.gbr) 경로")
    ap.add_argument("--snap-tol-mm", type=float, default=0.02, help="끝점 스냅/병합 허용 오차(mm)")
    ap.add_argument("--max-seg-len-mm", type=float, default=0.2, help="원호 근사 최대 세그먼트 길이(mm)")
    ap.add_argument("--max-angle-deg", type=float, default=5.0, help="원호 근사 최대 각도(도)")
    args = ap.parse_args()

    info = fill_gerber_outline_to_region(
        args.input,
        args.output,
        snap_tol_mm=args.snap_tol_mm,
        max_seg_len_mm=args.max_seg_len_mm,
        max_angle_deg=args.max_angle_deg,
    )
    print(json.dumps(info, ensure_ascii=False, indent=2))

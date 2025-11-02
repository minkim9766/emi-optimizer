# outline_cascade_keep_red_circles.py
# 중심에서 가장 먼 k번째 도형을 시작으로 접촉 연쇄 선택
# keep_only=True이면 선택 집합만 남기고 나머지 제거
# 단 원은 제거에서 제외  단 반지름이 아주 작은 원은 항상 제거
# circle과 ellipse를 지원  ellipse는 rx와 ry가 거의 같으면 원으로 간주

from lxml import etree
import math, re

# 기본 설정
SMALL_CIRCLE_R = 1
ELLIPSE_AS_CIRCLE_TOL = 0.05   # |rx-ry| <= tol * max(rx,ry) 이면 원으로 간주

def I():
    return 1.0, 0.0, 0.0, 1.0, 0.0, 0.0

def mul(M, N):
    a, b, c, d, e, f = M
    A, B, C, D, E, F = N
    return (
        a*A + c*B,
        b*A + d*B,
        a*C + c*D,
        b*C + d*D,
        a*E + c*F + e,
        b*E + d*F + f
    )

def parse_transform(tr):
    if not tr:
        return I()
    mats = []
    for m in re.finditer(r'(matrix|translate|scale|rotate)\s*\(([^)]*)\)', tr):
        kind = m.group(1)
        nums = [float(x) for x in re.split(r'[ ,]+', m.group(2).strip()) if x]
        if kind == "matrix" and len(nums) == 6:
            mats.append(tuple(nums))
        elif kind == "translate":
            tx = nums[0] if nums else 0.0
            ty = nums[1] if len(nums) > 1 else 0.0
            mats.append((1, 0, 0, 1, tx, ty))
        elif kind == "scale":
            sx = nums[0] if nums else 1.0
            sy = nums[1] if len(nums) > 1 else sx
            mats.append((sx, 0, 0, sy, 0, 0))
        elif kind == "rotate":
            ang = math.radians(nums[0] if nums else 0.0)
            ca, sa = math.cos(ang), math.sin(ang)
            mats.append((ca, sa, -sa, ca, 0, 0))
    M = I()
    for mtx in mats:
        M = mul(M, mtx)
    return M

def cumulative_transform(elem):
    M = I()
    chain = []
    cur = elem
    while cur is not None:
        chain.append(cur)
        cur = cur.getparent()
    for node in reversed(chain):
        M = mul(M, parse_transform(node.get("transform")))
    return M

def parse_len(v):
    if v is None:
        return None
    try:
        return float(str(v).lower().replace("px", "").strip())
    except Exception:
        return None

def bbox_path(e):
    d = e.get("d")
    if not d:
        return None
    try:
        toks = []
        num = ""
        for ch in d.replace(",", " "):
            if ch.isalpha():
                if num.strip():
                    toks.append(float(num))
                    num = ""
                toks.append(ch)
            elif ch in " -0123456789.eE+":
                num += ch
            else:
                if num.strip():
                    toks.append(float(num))
                    num = ""
        if num.strip():
            toks.append(float(num))
        x = y = 0.0
        mnx = mny = 1e300
        mxx = mxy = -1e300
        i = 0
        cmd = None
        while i < len(toks):
            t = toks[i]
            if isinstance(t, str):
                cmd = t
                i += 1
                continue
            if cmd in ("M", "L"):
                x = float(toks[i]); y = float(toks[i+1]); i += 2
            elif cmd in ("m", "l"):
                x += float(toks[i]); y += float(toks[i+1]); i += 2
            elif cmd == "H":
                x = float(toks[i]); i += 1
            elif cmd == "h":
                x += float(toks[i]); i += 1
            elif cmd == "V":
                y = float(toks[i]); i += 1
            elif cmd == "v":
                y += float(toks[i]); i += 1
            elif cmd in ("C","c","Q","q","S","s","T","t","A","a"):
                step = {"C":6,"c":6,"Q":4,"q":4,"S":4,"s":4,"T":2,"t":2,"A":7,"a":7}[cmd]
                ex = float(toks[i+step-2]); ey = float(toks[i+step-1])
                if cmd.islower():
                    x += ex; y += ey
                else:
                    x = ex; y = ey
                i += step
            else:
                i += 1
            mnx = min(mnx, x); mny = min(mny, y)
            mxx = max(mxx, x); mxy = max(mxy, y)
        return mnx, mny, mxx, mxy
    except Exception:
        return None

def bbox_elem(e):
    tag = etree.QName(e).localname
    if tag == "path":
        return bbox_path(e)
    if tag == "rect":
        try:
            x = parse_len(e.get("x")) or 0.0
            y = parse_len(e.get("y")) or 0.0
            w = parse_len(e.get("width")) or 0.0
            h = parse_len(e.get("height")) or 0.0
            return x, y, x + w, y + h
        except Exception:
            return None
    if tag == "line":
        try:
            x1 = parse_len(e.get("x1")) or 0.0
            y1 = parse_len(e.get("y1")) or 0.0
            x2 = parse_len(e.get("x2")) or 0.0
            y2 = parse_len(e.get("y2")) or 0.0
            return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
        except Exception:
            return None
    if tag in ("polygon", "polyline"):
        pts = (e.get("points") or "").replace(",", " ").split()
        xs, ys = [], []
        try:
            it = iter(pts)
            for x, y in zip(it, it):
                xs.append(float(x)); ys.append(float(y))
            if xs and ys:
                return min(xs), min(ys), max(xs), max(ys)
        except Exception:
            return None
    if tag == "circle":
        cx = parse_len(e.get("cx")) or 0.0
        cy = parse_len(e.get("cy")) or 0.0
        r  = parse_len(e.get("r"))  or 0.0
        return cx - r, cy - r, cx + r, cy + r
    if tag == "ellipse":
        cx = parse_len(e.get("cx")) or 0.0
        cy = parse_len(e.get("cy")) or 0.0
        rx = parse_len(e.get("rx")) or 0.0
        ry = parse_len(e.get("ry")) or 0.0
        return cx - rx, cy - ry, cx + rx, cy + ry
    return None

def apply_bbox(bb, M):
    if not bb:
        return None
    a, b, c, d, e, f = M
    xl, yl, xr, yr = bb
    pts = [(xl, yl), (xl, yr), (xr, yl), (xr, yr)]
    X = [a*x + c*y + e for x, y in pts]
    Y = [b*x + d*y + f for x, y in pts]
    return min(X), min(Y), max(X), max(Y)

def get_viewbox(svg):
    vb = svg.get("viewBox")
    if vb:
        xs = [float(t) for t in vb.replace(",", " ").split() if t]
        if len(xs) == 4:
            return xs
    w = parse_len(svg.get("width"))
    h = parse_len(svg.get("height"))
    if w is not None and h is not None:
        return [0.0, 0.0, w, h]
    inner = svg.find(".//{http://www.w3.org/2000/svg}svg")
    if inner is not None:
        vb = inner.get("viewBox")
        if vb:
            xs = [float(t) for t in vb.replace(",", " ").split() if t]
            if len(xs) == 4:
                return xs
    return None

def iter_drawables(svg):
    for e in svg.iter():
        if not isinstance(e.tag, str):
            continue
        if etree.QName(e).localname in ("path", "rect", "polygon", "polyline", "line", "circle", "ellipse"):
            yield e

def center_of(bb):
    xl, yl, xr, yr = bb
    return (xl + xr) * 0.5, (yl + yr) * 0.5

def dist2(p, q):
    dx = p[0] - q[0]
    dy = p[1] - q[1]
    return dx*dx + dy*dy

def bbox_gap(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    gx = 0.0
    gy = 0.0
    if ax2 < bx1:
        gx = bx1 - ax2
    elif bx2 < ax1:
        gx = ax1 - bx2
    if ay2 < by1:
        gy = by1 - ay2
    elif by2 < ay1:
        gy = ay1 - by2
    return math.hypot(gx, gy)

def touches(a, b, thresh):
    overlap = not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])
    if overlap:
        return True
    return bbox_gap(a, b) <= thresh

def is_circle_like(e):
    tag = etree.QName(e).localname
    if tag == "circle":
        r = parse_len(e.get("r")) or 0.0
        return True, r
    if tag == "ellipse":
        rx = parse_len(e.get("rx")) or 0.0
        ry = parse_len(e.get("ry")) or 0.0
        mx = max(rx, ry)
        if mx <= 0:
            return False, 0.0
        if abs(rx - ry) <= ELLIPSE_AS_CIRCLE_TOL * mx:
            return True, min(rx, ry)
    return False, 0.0

def keep_only_red(input_svg_path,
                  output_svg_path,
                  debug_svg_path=None,
                  gap_thresh=2.0,
                  require_thin=False,
                  thin_ratio=0.1,
                  start_rank=1):
    tree = etree.parse(input_svg_path)
    svg = tree.getroot()
    vb = get_viewbox(svg)
    if not vb:
        tree.write(output_svg_path, encoding="utf-8", xml_declaration=True)
        return dict(kept=0, removed=0)
    vx, vy, vw, vh = vb
    center = (vx + vw * 0.5, vy + vh * 0.5)
    V = max(vw, vh)

    items = []
    meta  = []   # 각 요소의 메타 정보  circle 여부와 반지름
    for e in iter_drawables(svg):
        bb = bbox_elem(e)
        if not bb:
            continue
        M = cumulative_transform(e)
        tbb = apply_bbox(bb, M)
        if not tbb:
            continue
        w = max(tbb[2] - tbb[0], 0.0)
        h = max(tbb[3] - tbb[1], 0.0)
        circ, r = is_circle_like(e)
        if require_thin and min(w, h) > thin_ratio * V and not circ:
            pass
        d2 = dist2(center_of(tbb), center)
        items.append(dict(elem=e, tbb=tbb, d2=d2))
        meta.append((circ, r))

    if not items:
        tree.write(output_svg_path, encoding="utf-8", xml_declaration=True)
        return dict(kept=0, removed=0)

    items_idx = list(range(len(items)))
    items_idx.sort(key=lambda i: items[i]["d2"], reverse=True)
    k = max(1, int(start_rank))
    if k > len(items_idx):
        k = len(items_idx)
    start_idx = items_idx[k - 1]

    n = len(items)
    adj = [set() for _ in range(n)]
    for i in range(n):
        ai = items[i]["tbb"]
        for j in range(i+1, n):
            bj = items[j]["tbb"]
            if touches(ai, bj, gap_thresh):
                adj[i].add(j)
                adj[j].add(i)

    visited = {start_idx}
    stack = [start_idx]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in visited:
                visited.add(v)
                stack.append(v)

    # 디버그 오버레이
    if debug_svg_path:
        t2 = etree.parse(input_svg_path)
        root2 = t2.getroot()
        overlay = etree.Element("g")
        overlay.set("id", "debug_overlay")
        overlay.set("style", "fill:none;stroke:#ff0000;stroke-width:0.6;stroke-opacity:1")
        for i in sorted(visited):
            x1, y1, x2, y2 = items[i]["tbb"]
            r = etree.Element("rect")
            r.set("x", str(x1)); r.set("y", str(y1))
            r.set("width", str(x2 - x1)); r.set("height", str(y2 - y1))
            overlay.append(r)
        root2.append(overlay)
        t2.write(debug_svg_path, encoding="utf-8", xml_declaration=True)

    # 선택 집합만 남기기
    keep_ids = {id(items[i]["elem"]) for i in visited}

    removed = 0
    kept    = 0

    # 작은 원은 무조건 제거  큰 원은 항상 보존
    for e in list(iter_drawables(svg)):
        circ, r = is_circle_like(e)
        if circ and r is not None:
            if r <= SMALL_CIRCLE_R:
                p = e.getparent()
                if p is not None:
                    p.remove(e)
                    removed += 1
                continue
            else:
                kept += 1
                continue

        # 원이 아니면 keep_only_red 규칙 적용
        if id(e) in keep_ids:
            kept += 1
        else:
            p = e.getparent()
            if p is not None:
                p.remove(e)
                removed += 1

    # 빈 그룹 간단 정리
    def prune_empty_groups(node):
        for ch in list(node):
            prune_empty_groups(ch)
        if len(node) == 0 and not node.text and etree.QName(node).localname == "g":
            par = node.getparent()
            if par is not None:
                par.remove(node)
    prune_empty_groups(svg)

    tree.write(output_svg_path, encoding="utf-8", xml_declaration=True)
    return dict(kept=kept, removed=removed, start_rank=k, small_circle_r=SMALL_CIRCLE_R)


res = keep_only_red(
    "./output_images/bot_solderpaste.svg",
    "./output_images/bot_solderpaste_edited.svg",
    debug_svg_path="debug.svg",
    gap_thresh=1.5,
    require_thin=False,
    start_rank=2
)
print(res)
#!/usr/bin/env python3
"""
VISION MODULE - structural screen understanding WITHOUT OCR.

Why this exists: OCR reads *text* and costs 150-300ms per pass on this CPU.
But most of what's on a screen is *structure* - buttons, panels, icons,
borders, highlights, gridlines, scrollbars. All of that can be detected
geometrically with OpenCV in 1-5ms, which means it can run on the fast
millisecond loop instead of the slow OCR thread.

So: OCR answers "what does that text say?", this module answers "where are
the clickable things, what shape are they, what changed, and what does the
layout look like?" - which is usually what automation actually needs.

Every function takes a BGR/BGRA numpy frame and returns plain data
(dicts/lists of ints) that serializes straight to JSON.
"""
import cv2
import numpy as np

# ---------------------------------------------------------------- helpers

def _bgr(img):
    """Normalize a BGRA (mss) or BGR frame to plain 3-channel BGR uint8."""
    return np.ascontiguousarray(img[:, :, :3]).astype(np.uint8)

def _gray(img):
    return cv2.cvtColor(_bgr(img), cv2.COLOR_BGR2GRAY)

# Structural detection doesn't need full resolution - a button is still a
# rectangle at half size. Downscaling is the single biggest speed lever here:
# benchmarked at 1920x1080 most detectors cost 20-70ms, which blows the
# millisecond-loop budget; at 0.4 scale they land in the 3-12ms range.
# Coordinates are always scaled back to true screen space before returning.
DEFAULT_SCALE = 0.4

def _scaled(img, scale=DEFAULT_SCALE):
    """Returns (small_bgr, inverse_factor) - multiply any coordinate found in
    the small image by inverse_factor to get true full-resolution coordinates."""
    b = _bgr(img)
    if scale >= 0.999:
        return b, 1.0
    h, w = b.shape[:2]
    small = cv2.resize(b, (max(1, int(w * scale)), max(1, int(h * scale))),
                       interpolation=cv2.INTER_AREA)
    return small, 1.0 / scale

def _scale_rect(r, f):
    """Scale a rect dict's coordinates back up to full resolution."""
    if f == 1.0:
        return r
    out = dict(r)
    for k in ("x", "y", "w", "h", "cx", "cy", "x1", "y1", "x2", "y2", "r", "length"):
        if k in out:
            out[k] = int(out[k] * f)
    if "area" in out:
        out["area"] = int(out["area"] * f * f)
    return out


# ---------------------------------------------------------------- structure

def edge_profile(img, low=50, high=150, scale=DEFAULT_SCALE):
    """Canny edge density - how much structure/detail is on screen, and where.
    High density = busy UI (menus, text, panels). Near-zero = blank/solid area."""
    small, _f = _scaled(img, scale)
    edges = cv2.Canny(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), low, high)
    h, w = edges.shape
    # density per 4x4 region so we know WHERE the structure is, not just how much
    gh, gw = h // 4, w // 4
    regions = []
    for r in range(4):
        row = []
        for c in range(4):
            cell = edges[r * gh:(r + 1) * gh, c * gw:(c + 1) * gw]
            row.append(round(100.0 * np.count_nonzero(cell) / max(cell.size, 1), 2))
        regions.append(row)
    return {
        "total_edge_density_pct": round(100.0 * np.count_nonzero(edges) / edges.size, 2),
        "region_density_4x4": regions,
    }

def detect_rectangles(img, min_area=600, max_results=40, scale=DEFAULT_SCALE):
    """Find rectangular UI elements - buttons, panels, input fields, toolbars.
    Most GUI widgets are rectangles, so this finds 'clickable things' without
    knowing anything about the specific app."""
    small, f = _scaled(img, scale)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    scaled_min_area = min_area / (f * f)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    rects = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < scaled_min_area:
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            x, y, w, h = cv2.boundingRect(approx)
            aspect = w / max(h, 1)
            rects.append(_scale_rect({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                                      "area": int(area), "cx": int(x + w / 2),
                                      "cy": int(y + h / 2)}, f) | {"aspect": round(aspect, 2)})
    rects.sort(key=lambda r: -r["area"])
    return rects[:max_results]

def detect_lines(img, min_length=80, max_results=30, scale=DEFAULT_SCALE):
    """Hough lines - finds separators, table gridlines, panel borders, rulers.
    Tells you how the screen is divided up into regions."""
    small, f = _scaled(img, scale)
    edges = cv2.Canny(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=70,
                            minLineLength=int(min_length / f), maxLineGap=8)
    out = []
    if lines is not None:
        for l in lines[:max_results * 3]:
            x1, y1, x2, y2 = l[0]
            dx, dy = abs(x2 - x1), abs(y2 - y1)
            orient = "horizontal" if dx > dy * 3 else ("vertical" if dy > dx * 3 else "diagonal")
            out.append(_scale_rect({"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2),
                                    "length": int(np.hypot(dx, dy))}, f) | {"orientation": orient})
        out.sort(key=lambda l: -l["length"])
    return out[:max_results]

def detect_circles(img, max_results=15, scale=DEFAULT_SCALE):
    """Hough circles - radio buttons, round icons, status dots, avatars."""
    small, f = _scaled(img, scale)
    gray = cv2.medianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 5)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=int(25 / f),
                               param1=100, param2=40,
                               minRadius=max(2, int(5 / f)), maxRadius=int(60 / f))
    out = []
    if circles is not None:
        for c in np.uint16(np.around(circles))[0][:max_results]:
            out.append(_scale_rect({"cx": int(c[0]), "cy": int(c[1]), "r": int(c[2])}, f))
    return out

def detect_corners(img, max_results=40, scale=DEFAULT_SCALE):
    """Shi-Tomasi corners - interactive element boundaries, grid intersections.
    Useful for snapping clicks to precise UI landmarks."""
    small, f = _scaled(img, scale)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    pts = cv2.goodFeaturesToTrack(gray, maxCorners=max_results, qualityLevel=0.05,
                                  minDistance=max(4, int(20 / f)))
    if pts is None:
        return []
    return [{"x": int(p[0][0] * f), "y": int(p[0][1] * f)} for p in pts]


# ---------------------------------------------------------------- text WITHOUT reading it

def detect_text_regions(img, max_results=40, scale=DEFAULT_SCALE):
    """Find WHERE text is without reading it - roughly 100x cheaper than OCR.
    Uses morphological gradient + closing to find dense horizontal stroke
    clusters, which is what text looks like structurally. Use this to decide
    WHICH small region is worth spending a real OCR pass on."""
    small, f = _scaled(img, scale)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    connected = cv2.morphologyEx(bw, cv2.MORPH_CLOSE,
                                 cv2.getStructuringElement(cv2.MORPH_RECT, (9, 1)))
    contours, _ = cv2.findContours(connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    min_w, min_h, max_h = 18 / f, 7 / f, 60 / f
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < min_w or h < min_h or h > max_h:
            continue
        if w / max(h, 1) < 1.4:          # text lines are wider than tall
            continue
        fill = cv2.countNonZero(connected[y:y + h, x:x + w]) / max(w * h, 1)
        if fill < 0.35:
            continue
        out.append(_scale_rect({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                                "cx": int(x + w / 2), "cy": int(y + h / 2)}, f))
    out.sort(key=lambda r: (r["y"], r["x"]))
    return out[:max_results]


# ---------------------------------------------------------------- color

def color_regions(img, max_results=12, min_area=1500):
    """Segment the frame into dominant flat-color regions - panels, sidebars,
    content areas, highlighted selections. Tells you the layout blocks."""
    small = cv2.resize(_bgr(img), (160, 90), interpolation=cv2.INTER_AREA)
    Z = small.reshape((-1, 3)).astype(np.float32)
    K = 6
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 8, 1.0)
    _, labels, centers = cv2.kmeans(Z, K, None, criteria, 2, cv2.KMEANS_PP_CENTERS)
    labels = labels.reshape((90, 160))
    sx, sy = img.shape[1] / 160.0, img.shape[0] / 90.0
    out = []
    for k in range(K):
        mask = (labels == k).astype(np.uint8)
        if cv2.countNonZero(mask) < 12:
            continue
        cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cs:
            x, y, w, h = cv2.boundingRect(c)
            area = int(w * sx * h * sy)
            if area < min_area:
                continue
            b, g, r = centers[k].astype(int)
            out.append({"x": int(x * sx), "y": int(y * sy),
                        "w": int(w * sx), "h": int(h * sy), "area": area,
                        "color_rgb": [int(r), int(g), int(b)]})
    out.sort(key=lambda r: -r["area"])
    return out[:max_results]

def find_color(img, rgb, tolerance=30, max_results=15, min_area=40, scale=DEFAULT_SCALE):
    """Find every region matching a specific color - generalizes the
    orange-selection detector to ANY color, for ANY app's highlight,
    error red, success green, link blue, etc."""
    small, f = _scaled(img, scale)
    target = np.array([rgb[2], rgb[1], rgb[0]], dtype=np.int16)  # RGB -> BGR
    diff = np.abs(small.astype(np.int16) - target).sum(axis=2)
    mask = (diff < tolerance * 3).astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    scaled_min_area = min_area / (f * f)
    for c in contours:
        area = cv2.contourArea(c)
        if area < scaled_min_area:
            continue
        x, y, w, h = cv2.boundingRect(c)
        out.append(_scale_rect({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                                "cx": int(x + w / 2), "cy": int(y + h / 2),
                                "area": int(area)}, f))
    out.sort(key=lambda r: -r["area"])
    return out[:max_results]

def dominant_palette(img, k=5):
    """The actual color scheme in use - identifies app theme, detects when a
    dialog/overlay with a different palette appears on top."""
    small = cv2.resize(_bgr(img), (80, 45), interpolation=cv2.INTER_AREA)
    Z = small.reshape((-1, 3)).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 8, 1.0)
    _, labels, centers = cv2.kmeans(Z, k, None, criteria, 2, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.flatten(), minlength=k)
    total = counts.sum()
    palette = []
    for i in np.argsort(-counts):
        b, g, r = centers[i].astype(int)
        palette.append({"rgb": [int(r), int(g), int(b)],
                        "share_pct": round(100.0 * counts[i] / total, 1)})
    return palette


# ---------------------------------------------------------------- matching & identity

def match_template(img, template, threshold=0.8, max_results=10):
    """Find a known icon/button image anywhere on screen. This is the key to
    app-agnostic automation: 'click the thing that looks like THIS' works in
    any application without knowing its UI framework or reading any text.
    `template` is a BGR numpy array (e.g. cv2.imread of a cropped button)."""
    base = _gray(img)
    tpl = cv2.cvtColor(template[:, :, :3].astype(np.uint8), cv2.COLOR_BGR2GRAY) \
        if template.ndim == 3 else template.astype(np.uint8)
    if tpl.shape[0] > base.shape[0] or tpl.shape[1] > base.shape[1]:
        return []
    res = cv2.matchTemplate(base, tpl, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res >= threshold)
    h, w = tpl.shape
    hits = []
    for x, y in zip(xs, ys):
        hits.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                     "cx": int(x + w / 2), "cy": int(y + h / 2),
                     "score": round(float(res[y, x]), 3)})
    hits.sort(key=lambda r: -r["score"])
    # suppress overlapping duplicates of the same match
    kept = []
    for hcand in hits:
        if all(abs(hcand["cx"] - k["cx"]) > w / 2 or abs(hcand["cy"] - k["cy"]) > h / 2 for k in kept):
            kept.append(hcand)
        if len(kept) >= max_results:
            break
    return kept

def perceptual_hash(img, size=16):
    """Cheap frame fingerprint. Identical hash = screen genuinely unchanged,
    so expensive work (OCR, analysis) can be skipped entirely. Also detects
    'did the screen return to a state we've seen before?'"""
    small = cv2.resize(_gray(img), (size, size), interpolation=cv2.INTER_AREA)
    avg = small.mean()
    bits = (small > avg).flatten()
    return "".join("1" if b else "0" for b in bits)

def hamming_distance(hash_a, hash_b):
    """How different two perceptual hashes are: 0 = identical screen,
    higher = bigger visual change. Cheap way to grade 'how much changed'."""
    if not hash_a or not hash_b or len(hash_a) != len(hash_b):
        return -1
    return sum(1 for a, b in zip(hash_a, hash_b) if a != b)


# ---------------------------------------------------------------- motion & change

def motion_vectors(prev_gray, curr_gray, step=40, scale=0.25):
    """Optical flow - detects direction/magnitude of movement: scrolling,
    dragging, window sliding, animations. Tells you not just THAT something
    moved but WHICH WAY and HOW FAST.
    Farneback is by far the most expensive analysis here (411ms at full
    1920x1080), so it downscales hard by default - direction/speed survive
    aggressive downscaling fine, only fine spatial detail is lost."""
    if prev_gray is None or prev_gray.shape != curr_gray.shape:
        return {"moving": False, "vectors": [], "dominant": None}
    f = 1.0
    if scale < 0.999:
        nw, nh = max(1, int(curr_gray.shape[1] * scale)), max(1, int(curr_gray.shape[0] * scale))
        prev_gray = cv2.resize(prev_gray, (nw, nh), interpolation=cv2.INTER_AREA)
        curr_gray = cv2.resize(curr_gray, (nw, nh), interpolation=cv2.INTER_AREA)
        f = 1.0 / scale
        step = max(4, int(step * scale))
    flow = cv2.calcOpticalFlowFarneback(prev_gray, curr_gray, None,
                                        0.5, 2, 15, 2, 5, 1.2, 0)
    h, w = curr_gray.shape
    vectors, dxs, dys = [], [], []
    for y in range(step, max(step + 1, h - step), max(1, step * 3)):
        for x in range(step, max(step + 1, w - step), max(1, step * 3)):
            dx, dy = flow[y, x]
            mag = float(np.hypot(dx, dy))
            if mag > 1.0:
                vectors.append({"x": int(x * f), "y": int(y * f),
                                "dx": round(float(dx) * f, 1), "dy": round(float(dy) * f, 1),
                                "mag": round(mag * f, 1)})
                dxs.append(dx); dys.append(dy)
    dominant = None
    if dxs:
        mdx, mdy = float(np.mean(dxs)), float(np.mean(dys))
        if abs(mdx) > abs(mdy):
            direction = "right" if mdx > 0 else "left"
        else:
            direction = "down" if mdy > 0 else "up"
        dominant = {"direction": direction, "dx": round(mdx, 2), "dy": round(mdy, 2),
                    "speed": round(float(np.hypot(mdx, mdy)), 2)}
    return {"moving": bool(vectors), "vectors": vectors[:20], "dominant": dominant}

def region_change_grid(prev_gray, curr_gray, cols=8, rows=5, threshold=25):
    """Per-cell change map - tells you exactly WHICH parts of the screen
    changed, as a grid. Far more useful than a single change percentage:
    'the left sidebar changed but the main canvas didn't'."""
    if prev_gray is None or prev_gray.shape != curr_gray.shape:
        return {"grid": [], "changed_cells": []}
    diff = cv2.absdiff(prev_gray, curr_gray)
    h, w = diff.shape
    ch, cw = h // rows, w // cols
    grid, changed = [], []
    for r in range(rows):
        row = []
        for c in range(cols):
            cell = diff[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw]
            pct = round(100.0 * np.count_nonzero(cell > threshold) / max(cell.size, 1), 1)
            row.append(pct)
            if pct > 2.0:
                changed.append({"col": c, "row": r, "pct": pct,
                                "cx": int(c * cw + cw / 2), "cy": int(r * ch + ch / 2)})
        grid.append(row)
    return {"grid": grid, "changed_cells": changed}

def detect_blinking(frame_history, threshold=18):
    """Find elements that alternate between frames - text cursors/carets,
    blinking indicators, loading spinners. `frame_history` is a list of
    recent grayscale frames (oldest first, needs >= 3)."""
    if len(frame_history) < 3:
        return {"blinking_regions": []}
    a, b, c = frame_history[-3], frame_history[-2], frame_history[-1]
    if a.shape != b.shape or b.shape != c.shape:
        return {"blinking_regions": []}
    # something that changed, changed back, i.e. differs from neighbour but matches 2-frames-ago
    d1 = cv2.absdiff(a, b)
    d2 = cv2.absdiff(b, c)
    d3 = cv2.absdiff(a, c)
    blink = ((d1 > threshold) & (d2 > threshold) & (d3 < threshold)).astype(np.uint8) * 255
    contours, _ = cv2.findContours(blink, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for cont in contours:
        if cv2.contourArea(cont) < 8:
            continue
        x, y, w, h = cv2.boundingRect(cont)
        out.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                    "cx": int(x + w / 2), "cy": int(y + h / 2)})
    return {"blinking_regions": out[:10]}


# ---------------------------------------------------------------- layout

def layout_analysis(img, scale=DEFAULT_SCALE):
    """High-level read of how the screen is organised: where the major
    horizontal/vertical dividers are, implying toolbars, sidebars, panels."""
    small, f = _scaled(img, scale)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    col_var = gray.std(axis=0)
    row_var = gray.std(axis=1)
    # a divider is a column/row that's unusually uniform (flat border line)
    v_dividers = [int(x) for x in np.where(col_var < np.percentile(col_var, 3))[0]]
    h_dividers = [int(y) for y in np.where(row_var < np.percentile(row_var, 3))[0]]

    def cluster(vals, gap=12):
        if not vals:
            return []
        groups, cur = [], [vals[0]]
        for v in vals[1:]:
            if v - cur[-1] <= gap:
                cur.append(v)
            else:
                groups.append(int(sum(cur) / len(cur)))
                cur = [v]
        groups.append(int(sum(cur) / len(cur)))
        return groups

    return {
        "width": int(w * f), "height": int(h * f),
        "vertical_dividers_x": [int(v * f) for v in cluster(v_dividers)[:10]],
        "horizontal_dividers_y": [int(v * f) for v in cluster(h_dividers)[:10]],
    }

def scan_all(img, prev_gray=None, frame_history=None, heavy=False, scale=DEFAULT_SCALE):
    """One call that runs the fast structural scan.

    Critical optimization: downscales the frame ONCE here and passes the
    already-small frame to every detector (with scale=1.0 so they don't
    resize again), then scales all returned coordinates back up in one pass.
    Benchmarking showed each detector resizing independently cost more than
    it saved for the cheaper analyses - this pays the resize once.

    `heavy=True` adds the slower analyses (colour k-means, circles, optical
    flow); keep it False on the millisecond loop, True on an occasional pass.
    """
    small, f = _scaled(img, scale)
    small_gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    prev_small_gray = None
    if prev_gray is not None:
        if f != 1.0:
            prev_small_gray = cv2.resize(prev_gray, (small_gray.shape[1], small_gray.shape[0]),
                                         interpolation=cv2.INTER_AREA)
        else:
            prev_small_gray = prev_gray

    def up(items):
        return [_scale_rect(i, f) for i in items]

    result = {
        "edges": edge_profile(small, scale=1.0),
        "rectangles": up(detect_rectangles(small, scale=1.0)),
        "text_regions": up(detect_text_regions(small, scale=1.0)),
        "lines": up(detect_lines(small, scale=1.0)),
        "corners": [{"x": int(c["x"] * f), "y": int(c["y"] * f)}
                    for c in detect_corners(small, scale=1.0)],
        "layout": layout_analysis(small, scale=1.0),
        "phash": perceptual_hash(small),
        "region_change": region_change_grid(prev_small_gray, small_gray),
        "scan_scale": scale,
    }
    if heavy:
        result["color_regions"] = up(color_regions(small))
        result["palette"] = dominant_palette(small)
        result["circles"] = up(detect_circles(small, scale=1.0))
        result["motion"] = motion_vectors(prev_small_gray, small_gray, scale=1.0)
        if frame_history:
            result["blinking"] = detect_blinking(frame_history)
    return result


if __name__ == "__main__":
    # Self-benchmark so the cost of each analysis is measured, not guessed.
    # Averages several runs - single-run timings on a busy machine are noise.
    import time as _t
    import mss as _mss
    RUNS = 5
    with _mss.mss() as sct:
        frame = np.array(sct.grab(sct.monitors[1]))
    g = _gray(frame)
    print(f"frame: {frame.shape[1]}x{frame.shape[0]}   (avg of {RUNS} runs)\n")

    def bench(name, fn):
        times = []
        out = None
        for _ in range(RUNS):
            t0 = _t.perf_counter()
            out = fn()
            times.append((_t.perf_counter() - t0) * 1000)
        n = len(out) if isinstance(out, (list, dict)) else 1
        print(f"{name:24s} {sum(times)/len(times):7.1f} ms  (min {min(times):6.1f})  -> {n} items")

    print("--- individual (each resizes independently) ---")
    bench("edge_profile", lambda: edge_profile(frame))
    bench("detect_rectangles", lambda: detect_rectangles(frame))
    bench("detect_text_regions", lambda: detect_text_regions(frame))
    bench("detect_lines", lambda: detect_lines(frame))
    bench("detect_corners", lambda: detect_corners(frame))
    bench("layout_analysis", lambda: layout_analysis(frame))
    bench("perceptual_hash", lambda: perceptual_hash(frame))
    bench("region_change_grid", lambda: region_change_grid(g, g))
    bench("color_regions", lambda: color_regions(frame))
    bench("dominant_palette", lambda: dominant_palette(frame))
    bench("detect_circles", lambda: detect_circles(frame))
    bench("motion_vectors", lambda: motion_vectors(g, g))
    bench("find_color(orange)", lambda: find_color(frame, (255, 140, 0)))

    print("\n--- combined scan_all (resizes ONCE, shared) ---")
    bench("scan_all(fast)", lambda: scan_all(frame, prev_gray=g))
    bench("scan_all(heavy)", lambda: scan_all(frame, prev_gray=g, heavy=True))
    bench("scan_all(fast, 0.25)", lambda: scan_all(frame, prev_gray=g, scale=0.25))

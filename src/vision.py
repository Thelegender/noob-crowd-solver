"""Find the puzzle in a screenshot and work out which tile sits in each slot.

Pure Pillow (no numpy) so the packaged exe stays small.
Corners are always ordered clockwise from the top-left as seen in the screenshot: TL, TR, BR, BL.
"Canonical" positions are the reference picture's orientation (blank ends bottom-right);
"view" positions are how the board appears in the screenshot. rot = number of 90° counter-clockwise
turns that take the view image to the canonical image.
"""
import math
from collections import deque

from PIL import Image, ImageChops, ImageFilter, ImageStat

N = 8
BLANK = 63
CELL_FEATURE = 14  # tiles are compared as CELL_FEATURE x CELL_FEATURE thumbnails
CELL_MARGIN = 0.12  # fraction of a cell trimmed on each side to skip the black gaps


# ---------------------------------------------------------------- geometry

def _solve(a, b):
    """Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        if abs(m[col][col]) < 1e-12:
            raise ValueError("degenerate corners")
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def homography(src, dst):
    """3x3 matrix (row lists) mapping each src point onto the matching dst point."""
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    h = _solve(a, b)
    return [h[0:3], h[3:6], [h[6], h[7], 1.0]]


def apply_h(h, x, y):
    w = h[2][0] * x + h[2][1] * y + h[2][2]
    return ((h[0][0] * x + h[0][1] * y + h[0][2]) / w, (h[1][0] * x + h[1][1] * y + h[1][2]) / w)


def warp(img, corners, size, resample=Image.BICUBIC, fill=0):
    """Straighten the quad `corners` (TL, TR, BR, BL) into a size x size square."""
    h = homography([(0, 0), (size, 0), (size, size), (0, size)], corners)
    coeffs = (h[0][0], h[0][1], h[0][2], h[1][0], h[1][1], h[1][2], h[2][0], h[2][1])
    return img.transform((size, size), Image.PERSPECTIVE, coeffs, resample, fillcolor=fill)


def order_corners(pts):
    """Sort four points clockwise (screen coordinates), starting at the top-left-most one."""
    cx = sum(p[0] for p in pts) / 4
    cy = sum(p[1] for p in pts) / 4
    pts = sorted(pts, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    start = min(range(4), key=lambda i: pts[i][0] + pts[i][1])
    return [tuple(pts[(start + i) % 4]) for i in range(4)]


# ---------------------------------------------------------------- finding the grid

def dark_mask(img, threshold=60):
    """L image: 255 where the pixel is near-black (the gaps between tiles)."""
    r, g, b = img.convert("RGB").split()
    brightest = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return brightest.point(lambda v: 255 if v < threshold else 0)


def _convex_hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _quad_area(q):
    s = 0.0
    for i in range(4):
        x1, y1 = q[i]; x2, y2 = q[(i + 1) % 4]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def _largest_quad(hull):
    """Four hull vertices enclosing the most area (coordinate ascent from the extreme points)."""
    q = [min(hull, key=lambda p: p[0] + p[1]), max(hull, key=lambda p: p[0] - p[1]),
         max(hull, key=lambda p: p[0] + p[1]), min(hull, key=lambda p: p[0] - p[1])]
    best = _quad_area(q)
    improved = True
    while improved:
        improved = False
        for i in range(4):
            for p in hull:
                cand = q[:i] + [p] + q[i + 1:]
                a = _quad_area(order_corners(cand)) if len(set(cand)) == 4 else 0
                if a > best + 1e-6:
                    best, q, improved = a, order_corners(cand), True
    return q


def detect_corners(img):
    """Best guess at the tile grid's outer corners, or None if nothing grid-like was found."""
    scale = min(1.0, 900 / max(img.size))
    small = img.convert("RGB").resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                                      Image.BILINEAR)
    mask = dark_mask(small)
    blobs = mask.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(9))  # big dark areas, not lines
    lines = ImageChops.subtract(mask, blobs).filter(ImageFilter.MaxFilter(3))  # bridge dashed thin lines
    w, h = lines.size
    data = lines.tobytes()
    seen = bytearray(w * h)
    best = []
    for start in range(w * h):
        if data[start] < 128 or seen[start]:
            continue
        comp, dq = [], deque([start])
        seen[start] = 1
        while dq:
            i = dq.popleft()
            comp.append(i)
            x, y = i % w, i // w
            for dy in (-1, 0, 1):
                yy = y + dy
                if yy < 0 or yy >= h:
                    continue
                for dx in (-1, 0, 1):
                    xx = x + dx
                    if 0 <= xx < w:
                        j = yy * w + xx
                        if not seen[j] and data[j] >= 128:
                            seen[j] = 1
                            dq.append(j)
        if len(comp) > len(best):
            best = comp
    if len(best) < 200:
        return None
    pts = [(i % w, i // w) for i in best]
    hull = _convex_hull(pts)
    if len(hull) < 4:
        return None
    quad = _largest_quad(hull)
    if _quad_area(quad) < 0.02 * w * h:
        return None
    quad = _fit_sides(pts, quad, w, h) or quad
    return [((x + 0.5) / scale, (y + 0.5) / scale) for x, y in quad]


def _fit_line(points):
    """Total-least-squares line through points: (point on line, unit direction)."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    syy = sum((p[1] - my) ** 2 for p in points)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    return (mx, my), (math.cos(ang), math.sin(ang))


def _intersect(l1, l2):
    (x1, y1), (dx1, dy1) = l1
    (x2, y2), (dx2, dy2) = l2
    den = dx1 * dy2 - dy1 * dx2
    if abs(den) < 1e-9:
        return None
    t = ((x2 - x1) * dy2 - (y2 - y1) * dx2) / den
    return (x1 + t * dx1, y1 + t * dy1)


def _fit_sides(pts, quad, w, h):
    """Refit each side of the quad as a straight line through the grid's outer edge pixels.

    Pixels on the screenshot border are ignored, so a board that runs off the edge of the screenshot
    still gets its true (off-screen) corner by extending the sides.
    """
    rows, cols = {}, {}
    for x, y in pts:
        lo, hi = rows.get(y, (x, x)); rows[y] = (min(lo, x), max(hi, x))
        lo, hi = cols.get(x, (y, y)); cols[x] = (min(lo, y), max(hi, y))
    edge = set()
    for y, (lo, hi) in rows.items():
        edge.add((lo, y)); edge.add((hi, y))
    for x, (lo, hi) in cols.items():
        edge.add((x, lo)); edge.add((x, hi))
    edge = [p for p in edge if 1 < p[0] < w - 2 and 1 < p[1] < h - 2]
    for _ in range(2):
        size = math.sqrt(_quad_area(quad))
        lines = []
        for i in range(4):
            (ax, ay), (bx, by) = quad[i], quad[(i + 1) % 4]
            L = math.hypot(bx - ax, by - ay)
            ux, uy = (bx - ax) / L, (by - ay) / L
            near = []
            for px, py in edge:
                t = (px - ax) * ux + (py - ay) * uy
                if 0.1 * L < t < 0.9 * L and abs((px - ax) * uy - (py - ay) * ux) < 0.03 * size:
                    near.append((px, py))
            if len(near) < 20:
                return None
            lines.append(_fit_line(near))
        new = [_intersect(lines[(i + 3) % 4], lines[i]) for i in range(4)]
        if any(p is None for p in new):
            return None
        quad = new
    return quad


class GridFitter:
    """Nudges the corners so the 9 + 9 grid lines sit on the dark gaps between tiles."""

    SIZE = 400

    def __init__(self, img):
        self.scale = min(1.0, 1400 / max(img.size))
        small = img.convert("RGB").resize((round(img.width * self.scale), round(img.height * self.scale)),
                                          Image.BILINEAR)
        self.mask = dark_mask(small).filter(ImageFilter.GaussianBlur(1.3))

    def score(self, corners):
        s = self.SIZE
        try:
            w = warp(self.mask, [(x * self.scale, y * self.scale) for x, y in corners], s, Image.BILINEAR)
        except ValueError:
            return -1
        total = 0.0
        for k in range(N + 1):
            p = min(max(round(k * s / N), 1), s - 2)
            total += ImageStat.Stat(w.crop((0, p, s, p + 1))).mean[0]
            total += ImageStat.Stat(w.crop((p, 0, p + 1, s))).mean[0]
        return total

    def fit(self, corners, start_step=3.0):
        corners = [list(c) for c in corners]
        best = self.score(corners)
        step = start_step / self.scale
        while step >= 0.4 / self.scale:
            improved = True
            while improved:
                improved = False
                for i in range(4):
                    for dx, dy in ((step, 0), (-step, 0), (0, step), (0, -step)):
                        corners[i][0] += dx; corners[i][1] += dy
                        s = self.score(corners)
                        if s > best + 1e-6:
                            best, improved = s, True
                        else:
                            corners[i][0] -= dx; corners[i][1] -= dy
            step /= 2
        return [tuple(c) for c in corners], best


# ---------------------------------------------------------------- reading tiles

def view_to_canon(pos, rot):
    r, c = divmod(pos, N)
    for _ in range(rot % 4):
        r, c = N - 1 - c, r
    return r * N + c


def canon_to_view(pos, rot):
    r, c = divmod(pos, N)
    for _ in range(rot % 4):
        r, c = c, N - 1 - r
    return r * N + c


def rotate_to_canon(img, rot):
    return img.rotate(90 * (rot % 4), expand=True) if rot % 4 else img


def rotate_to_view(img, rot):
    return img.rotate(-90 * (rot % 4), expand=True) if rot % 4 else img


def cell_features(board_img):
    """Thumbnails of the 64 cells of a square board image, in reading order."""
    cell = board_img.width / N
    m = cell * CELL_MARGIN
    out = []
    for i in range(N * N):
        r, c = divmod(i, N)
        box = (round(c * cell + m), round(r * cell + m), round((c + 1) * cell - m), round((r + 1) * cell - m))
        out.append(board_img.crop(box).resize((CELL_FEATURE, CELL_FEATURE), Image.BOX))
    return out


def _cost_matrix(cells, refs):
    return [[sum(ImageStat.Stat(ImageChops.difference(c, t)).sum) for t in refs] for c in cells]


def hungarian(cost):
    """Minimum-cost assignment for an n x m matrix (n <= m). Returns the column chosen for each row."""
    n, m = len(cost), len(cost[0])
    inf = float("inf")
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            row = cost[i0 - 1]
            for j in range(1, m + 1):
                if not used[j]:
                    cur = row[j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    ans = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            ans[p[j] - 1] = j - 1
    return ans


def is_solvable(board):
    """board[pos] = tile id (0..62) or BLANK; goal is tiles in order with the blank bottom-right."""
    inv = 0
    for i in range(64):
        for j in range(i + 1, 64):
            if board[i] > board[j]:
                inv += 1
    b = board.index(BLANK)
    dist = (N - 1 - b // N) + (N - 1 - b % N)
    return inv % 2 == dist % 2


def _assign(cost):
    """64 cells x (63 tiles + blank). The blank column costs 0, so it lands on the worst-fitting cell."""
    padded = [row + [0.0] for row in cost]
    cols = hungarian(padded)
    board = [BLANK if c == 63 else c for c in cols]
    total = sum(cost[i][c] for i, c in enumerate(board) if c != BLANK)
    return board, total


def _apply_gain(cells, gains):
    out = []
    for c in cells:
        bands = [b.point(lambda v, g=g: min(255, round(v * g))) for b, g in zip(c.split(), gains)]
        out.append(Image.merge("RGB", bands))
    return out


class Reader:
    def __init__(self, reference):
        self.reference = reference.convert("RGB")
        self.refs = cell_features(self.reference)[:63]

    def read(self, img, corners):
        """Returns dict: board (canonical), rot, view (straightened screenshot), flags (canonical positions), note."""
        view = warp(img.convert("RGB"), corners, 768, Image.BICUBIC).resize((N * 48, N * 48), Image.BOX)
        best = None
        for rot in range(4):
            cells = cell_features(rotate_to_canon(view, rot))
            cost = _cost_matrix(cells, self.refs)
            board, total = _assign(cost)
            if best is None or total < best[0]:
                best = (total, rot, cells, cost, board)
        _, rot, cells, cost, board = best

        # correct overall brightness/colour cast, then read again
        sums_cell, sums_ref = [0.0] * 3, [0.0] * 3
        for pos, t in enumerate(board):
            if t != BLANK:
                for k, v in enumerate(ImageStat.Stat(cells[pos]).sum):
                    sums_cell[k] += v
                for k, v in enumerate(ImageStat.Stat(self.refs[t]).sum):
                    sums_ref[k] += v
        gains = [max(0.5, min(2.0, r / c)) if c else 1.0 for r, c in zip(sums_ref, sums_cell)]
        if any(abs(g - 1) > 0.03 for g in gains):
            cells = _apply_gain(cells, gains)
            cost = _cost_matrix(cells, self.refs)
            board, _ = _assign(cost)

        flags, note = set(), ""
        if not is_solvable(board):
            best_swap = None
            for a in range(64):
                for b in range(a + 1, 64):
                    ta, tb = board[a], board[b]
                    if BLANK in (ta, tb):
                        continue
                    d = cost[a][tb] + cost[b][ta] - cost[a][ta] - cost[b][tb]
                    if best_swap is None or d < best_swap[0]:
                        best_swap = (d, a, b)
            _, a, b = best_swap
            board[a], board[b] = board[b], board[a]
            flags |= {a, b}
            note = "Two look-alike tiles were swapped so the puzzle can be solved. Check the orange ones."

        assigned = sorted(cost[p][t] for p, t in enumerate(board) if t != BLANK)
        median = assigned[len(assigned) // 2]
        for p, t in enumerate(board):
            if t == BLANK:
                continue
            # unsure when another tile fits this slot about as well as the one chosen
            runner_up = min(v for k, v in enumerate(cost[p]) if k != t)
            if runner_up < 1.12 * cost[p][t]:
                flags.add(p)
        return {"board": board, "rot": rot, "view": view, "flags": flags, "note": note,
                "quality": median / (3 * CELL_FEATURE * CELL_FEATURE)}

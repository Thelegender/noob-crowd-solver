"""End-to-end checks: real screenshot fixture + synthetic screenshots with known answers.

Run:  python tests/test_pipeline.py [number_of_synthetic_cases]
"""
import io, json, math, os, random, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from PIL import Image, ImageDraw, ImageEnhance  # noqa: E402
import solver  # noqa: E402
import vision  # noqa: E402

REF = Image.open(os.path.join(HERE, "..", "assets", "reference.png")).convert("RGB")
READER = vision.Reader(REF)
BLUE, STUD = (18, 92, 196), (10, 58, 140)


def pipeline(img):
    corners = vision.detect_corners(img)
    assert corners, "grid not found"
    corners, _ = vision.GridFitter(img).fit(corners)
    return READER.read(img, corners), corners


def shuffled(rng, steps=3000):
    board, b = list(range(64)), 63
    for _ in range(steps):
        nb = rng.choice(solver.NEIGHBOURS[b])
        board[b], board[nb] = board[nb], board[b]
        b = nb
    return board


def studs(img, box, step=18):
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=BLUE)
    for y in range(y0 + 4, y1 - 8, step):
        for x in range(x0 + 4, x1 - 8, step):
            d.rectangle((x, y, x + 9, y + 9), fill=STUD)


def synth(rng, board, rot, clip=False):
    px, gap, border = 96, 2, 80
    grid = Image.new("RGB", (8 * px, 8 * px), (0, 0, 0))
    for pos, t in enumerate(board):
        r, c = divmod(pos, 8)
        if t == 63:
            studs(grid, (c * px + gap, r * px + gap, (c + 1) * px - gap, (r + 1) * px - gap))
        else:
            tr, tc = divmod(t, 8)
            grid.paste(REF.crop((tc * px + gap, tr * px + gap, (tc + 1) * px - gap, (tr + 1) * px - gap)),
                       (c * px + gap, r * px + gap))
    grid = vision.rotate_to_view(grid, rot)
    size = 8 * px + 2 * border
    framed = Image.new("RGB", (size, size))
    studs(framed, (0, 0, size, size))
    framed.paste((0, 0, 0), (border - 3, border - 3, border + 8 * px + 3, border + 8 * px + 3))
    framed.paste(grid, (border, border))

    W, H = 1280, 800
    cx, cy = W / 2 + rng.uniform(-80, 80), H / 2 + rng.uniform(-40, 60)
    s = rng.uniform(300, 420)
    squash = rng.uniform(0.75, 1.0)  # far edge looks narrower
    ang = math.radians(rng.uniform(-20, 20))
    base = [(-s * squash, -s * 0.9), (s * squash, -s * 0.9), (s, s * 0.9), (-s, s * 0.9)]
    if clip:
        cx -= s * 0.35
    quad = []
    for x, y in base:
        x += rng.uniform(-15, 15); y += rng.uniform(-15, 15)
        quad.append((cx + x * math.cos(ang) - y * math.sin(ang), cy + x * math.sin(ang) + y * math.cos(ang)))
    rect = [(0, 0), (size, 0), (size, size), (0, size)]
    h = vision.homography(quad, rect)
    coeffs = (h[0][0], h[0][1], h[0][2], h[1][0], h[1][1], h[1][2], h[2][0], h[2][1])
    shot = framed.transform((W, H), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(6, 8, 22))
    shot = ImageEnhance.Brightness(shot).enhance(rng.uniform(0.8, 1.15))
    shot = ImageEnhance.Color(shot).enhance(rng.uniform(0.9, 1.1))
    buf = io.BytesIO()
    shot.save(buf, "JPEG", quality=rng.randint(55, 85))
    return Image.open(buf).convert("RGB")


def check(name, result, board, rot):
    wrong = [p for p in range(64) if result["board"][p] != board[p]]
    ok = not wrong and result["rot"] == rot
    print(f"{name:<28} rot {result['rot']} (want {rot})  wrong {len(wrong):>2}  flagged {len(result['flags']):>2}"
          f"  quality {result['quality']:.1f}  {'OK' if ok else 'FAIL'}", flush=True)
    return ok


def main():
    cases = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    passed = total = 0

    fx = json.load(open(os.path.join(HERE, "fixtures", "screenshot-1.json")))
    t = time.time()
    res, _ = pipeline(Image.open(os.path.join(HERE, "fixtures", "screenshot-1.png")))
    total += 1
    passed += check(f"real screenshot ({time.time() - t:.1f}s)", res, fx["start"], fx["rot"])

    rng = random.Random(7)
    for i in range(cases):
        board, rot = shuffled(rng), i % 4
        img = synth(rng, board, rot, clip=(i % 3 == 2))
        t = time.time()
        res, _ = pipeline(img)
        total += 1
        passed += check(f"synthetic {i + 1} ({time.time() - t:.1f}s){' clipped' if i % 3 == 2 else ''}",
                        res, board, rot)

    t = time.time()
    moves = solver.solve(fx["start"])
    b = list(fx["start"])
    for m in moves:
        k = b.index(63)
        assert m in solver.NEIGHBOURS[k]
        b[k], b[m] = b[m], 63
    total += 1
    ok = b == list(range(64))
    passed += ok
    print(f"solver: {len(moves)} moves in {time.time() - t:.1f}s  {'OK' if ok else 'FAIL'}")
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()

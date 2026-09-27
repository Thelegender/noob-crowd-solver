"""Noob Crowd Solver: reads a screenshot of the 8x8 noob-crowd slide puzzle and walks you through the solution.

Press E in any other window (e.g. Roblox) to go to the next move.
"""
import ctypes
import json
import math
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog

from PIL import Image, ImageDraw, ImageGrab, ImageTk

import solver
import vision

APP = "Noob Crowd Solver"
N, BLANK = 8, 63
BG, PANEL, LINE = "#111A2E", "#18233B", "#24324F"
FG, MUTED, ACCENT, PRIMARY, WARN = "#E6ECF8", "#95A2BE", "#FFD21F", "#3C74F0", "#FF9F1C"
POOR_READ = 20  # Reader quality above this means the corners are almost certainly off


def resource(*parts):
    base = getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    return os.path.join(base, *parts)


DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "NoobCrowdSolver")
STATE_FILE = os.path.join(DATA_DIR, "state.json")


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        if len(s["start"]) == 64 and isinstance(s["moves"], list):
            return s
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def save_state(state):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


# ---------------------------------------------------------------- global E key

user32 = ctypes.windll.user32
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetAncestor.restype = ctypes.c_void_p
user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
VK_E, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x45, 0x11, 0x12, 0x5B, 0x5C


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# ---------------------------------------------------------------- drawing boards

class BoardArt:
    def __init__(self, reference):
        self.ref = reference
        self.cell = reference.width // N
        self._tiles = {}

    def tiles(self, px):
        if px not in self._tiles:
            c = self.cell
            self._tiles[px] = [self.ref.crop(((i % N) * c, (i // N) * c, (i % N + 1) * c, (i // N + 1) * c))
                               .resize((px, px), Image.LANCZOS) for i in range(63)]
        return self._tiles[px]

    def render(self, board, rot, px, move=None, flags=(), selected=None):
        """board is canonical; the image comes back the way the player sees the board.
        move = (tile position, blank position) in canonical coordinates, drawn as a highlight + arrow."""
        gap, tiles = max(1, px // 24), self.tiles(px)
        img = Image.new("RGB", (N * px, N * px), (5, 10, 22))
        d = ImageDraw.Draw(img)
        for pos, t in enumerate(board):
            x, y = (pos % N) * px, (pos // N) * px
            if t == BLANK:
                d.rectangle([x + gap, y + gap, x + px - gap - 1, y + px - gap - 1], fill=(12, 20, 40),
                            outline=ACCENT, width=2)
            else:
                img.paste(tiles[t].crop((gap, gap, px - gap, px - gap)), (x + gap, y + gap))
        img = vision.rotate_to_view(img, rot)
        d = ImageDraw.Draw(img)

        def box(p, colour, width):
            v = vision.canon_to_view(p, rot)
            x, y = (v % N) * px, (v // N) * px
            d.rectangle([x, y, x + px - 1, y + px - 1], outline=colour, width=width)

        for p in flags:
            box(p, WARN, max(3, px // 12))
        if selected is not None:
            box(selected, "#FFFFFF", max(4, px // 9))
        if move:
            tp, bp = (vision.canon_to_view(p, rot) for p in move)
            box(move[0], ACCENT, max(3, px // 12))
            dr, dc = bp // N - tp // N, bp % N - tp % N
            self.arrow(d, (tp % N + 0.5) * px, (tp // N + 0.5) * px, (dr, dc), px)
        return img

    @staticmethod
    def arrow(d, cx, cy, direction, size):
        pts = [(-0.32, -0.1), (0.04, -0.1), (0.04, -0.27), (0.36, 0), (0.04, 0.27), (0.04, 0.1), (-0.32, 0.1)]
        ang = math.atan2(direction[0], direction[1])
        ca, sa = math.cos(ang), math.sin(ang)
        poly = [(cx + (x * ca - y * sa) * size, cy + (x * sa + y * ca) * size) for x, y in pts]
        d.polygon(poly, fill=(255, 255, 255), outline=(0, 0, 0), width=max(2, size // 22))


def move_direction(start_blank_pos, tile_pos, rot):
    """Direction the tile slides, as the player sees it."""
    tp, bp = vision.canon_to_view(tile_pos, rot), vision.canon_to_view(start_blank_pos, rot)
    return bp // N - tp // N, bp % N - tp % N


WORDS = {(0, 1): "right", (0, -1): "left", (1, 0): "down (toward you)", (-1, 0): "up (away from you)"}
GLYPH = {(0, 1): "→", (0, -1): "←", (1, 0): "↓", (-1, 0): "↑"}


# ---------------------------------------------------------------- app

class App:
    def __init__(self, root):
        self.root = root
        self.s = root.winfo_fpixels("1i") / 96
        root.title(APP)
        root.configure(bg=BG)
        root.resizable(False, False)
        try:
            self.icon = ImageTk.PhotoImage(Image.open(resource("assets", "icon.png")))
            root.iconphoto(True, self.icon)
        except OSError:
            pass
        reference = Image.open(resource("assets", "reference.png")).convert("RGB")
        self.art = BoardArt(reference)
        self.reader = vision.Reader(reference)
        self.frame, self.mode = None, None
        self.hotkey_on = tk.BooleanVar(value=True)
        self.on_top = tk.BooleanVar(value=True)
        self.e_was_down = key_down(VK_E)
        self.img = self.corners = self.fitter = None
        root.bind_all("<Control-v>", self.paste)
        root.bind_all("<Control-V>", self.paste)
        root.bind("<Right>", lambda e: self.arrow_key(1))
        root.bind("<Left>", lambda e: self.arrow_key(-1))
        self.show_start()
        self.poll()

    # ---- helpers
    def px(self, v):
        return int(v * self.s)

    def arrow_key(self, delta):
        if self.mode == "guide" and not isinstance(self.root.focus_get(), tk.Entry):
            self.go(self.state["step"] + delta)

    def switch(self, mode):
        if self.frame:
            self.frame.destroy()
        self.mode = mode
        self.frame = tk.Frame(self.root, bg=BG)
        self.frame.pack(fill="both", expand=True, padx=self.px(16), pady=self.px(14))
        self.root.attributes("-topmost", mode == "guide" and self.on_top.get())
        self.root.geometry("")
        self.root.after_idle(self.place_window)
        return self.frame

    def place_window(self):
        """Centre the setup screens; park the guide at the right edge so it doesn't cover the game."""
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        if self.mode == "guide":
            x, y = sw - w - self.px(24), self.px(60)
        else:
            x, y = (sw - w) // 2, max(0, (sh - h) // 3)
        self.root.geometry(f"+{max(0, x)}+{max(0, y)}")

    def label(self, parent, text="", size=10, colour=FG, weight="", wrap=0, **kw):
        font = ("Segoe UI Semibold" if weight == "bold" else "Segoe UI", size)
        return tk.Label(parent, text=text, font=font, bg=BG, fg=colour, justify="left", anchor="w",
                        wraplength=wrap, **kw)

    def button(self, parent, text, command, primary=False):
        bg, active = (PRIMARY, "#5A8BF5") if primary else (LINE, "#33446A")
        return tk.Button(parent, text=text, command=command, font=("Segoe UI Semibold", 10), relief="flat",
                         bg=bg, fg="white" if primary else FG, activebackground=active, activeforeground="white",
                         padx=self.px(12), pady=self.px(5), cursor="hand2", bd=0)

    def run_bg(self, work, done, busy_label, busy_text):
        busy_label.config(text=busy_text, fg=MUTED)
        box = {}

        def worker():
            try:
                box["result"] = work()
            except Exception as exc:  # shown to the user rather than lost in a thread
                box["error"] = exc

        threading.Thread(target=worker, daemon=True).start()

        def check():
            if "result" in box:
                done(box["result"])
            elif "error" in box:
                busy_label.config(text=f"Something went wrong: {box['error']}", fg=WARN)
            else:
                self.root.after(60, check)

        self.root.after(60, check)

    # ---- screen 1: load a screenshot
    def show_start(self, error=""):
        f = self.switch("start")
        self.label(f, APP, 20, weight="bold").pack(fill="x")
        self.label(f, "Solves the 8×8 noob-crowd slide puzzle from a screenshot.", 11, MUTED).pack(fill="x")
        steps = ("1.  Stand where you can see the whole puzzle and take a screenshot (Win+Shift+S or PrtScn).\n"
                 "2.  Open the screenshot here, or press Ctrl+V to paste it.\n"
                 "3.  Check the tiles were read right, then follow the moves. "
                 "Press E in Roblox to go to the next move.")
        self.label(f, steps, 10, wrap=self.px(460)).pack(fill="x", pady=(self.px(12), self.px(14)))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.button(row, "Open screenshot…", self.open_file, primary=True).pack(side="left")
        self.button(row, "Paste screenshot (Ctrl+V)", self.paste).pack(side="left", padx=self.px(8))
        saved = load_state()
        if saved and saved.get("step", 0) < len(saved["moves"]):
            self.button(f, f"Continue where you left off (move {saved['step'] + 1} of {len(saved['moves'])})",
                        lambda: self.show_guide(saved)).pack(anchor="w", pady=(self.px(10), 0))
        self.status = self.label(f, error, 10, WARN if error else MUTED, wrap=self.px(460))
        self.status.pack(fill="x", pady=(self.px(10), 0))

    def open_file(self):
        path = filedialog.askopenfilename(title="Open a screenshot of the puzzle", filetypes=[
            ("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        if path:
            try:
                self.load_image(Image.open(path))
            except OSError:
                self.show_start("That file isn't an image this app can open. Try a PNG or JPG screenshot.")

    def paste(self, _event=None):
        if self.mode not in ("start", "corners"):
            return
        clip = ImageGrab.grabclipboard()
        if isinstance(clip, list):
            for path in clip:
                try:
                    clip = Image.open(path)
                    break
                except OSError:
                    continue
        if isinstance(clip, Image.Image):
            self.load_image(clip)
        else:
            self.show_start("There's no image on the clipboard. Take a screenshot with Win+Shift+S, then press Ctrl+V.")

    def load_image(self, img):
        self.img = img.convert("RGB")
        if self.mode == "start":
            self.status.config(text="Finding the puzzle…", fg=MUTED)
            self.root.update_idletasks()
        self.fitter = vision.GridFitter(self.img)
        found = vision.detect_corners(self.img)
        if found:
            self.corners, _ = self.fitter.fit(found)
        else:
            w, h = self.img.size
            self.corners = [(w * 0.3, h * 0.2), (w * 0.7, h * 0.2), (w * 0.7, h * 0.8), (w * 0.3, h * 0.8)]
        self.show_corners(found is not None)

    # ---- screen 2: line up the corners
    def show_corners(self, found=True):
        f = self.switch("corners")
        self.label(f, "Line up the grid", 16, weight="bold").pack(fill="x")
        msg = ("Check the yellow lines sit on the gaps between the tiles. If they don't, drag the dots onto the "
               "four outer corners of the tile grid, then press Snap. A dot can go past the edge of the "
               "screenshot if the board is cut off.") if found else (
               "Couldn't find the puzzle automatically. Drag the four dots onto the outer corners of the tile grid, "
               "then press Snap.")
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        avail_w, avail_h = min(int(sw * 0.8), self.px(1150)), min(int(sh * 0.66), self.px(720))
        self.label(f, msg, 10, MUTED if found else WARN, wrap=avail_w).pack(fill="x", pady=(2, self.px(8)))
        pad = self.px(44)
        img = self.img
        ds = min((avail_w - 2 * pad) / img.width, (avail_h - 2 * pad) / img.height)
        disp = img.resize((max(1, round(img.width * ds)), max(1, round(img.height * ds))), Image.LANCZOS)
        self.ds, self.pad = ds, pad
        self.canvas = tk.Canvas(f, width=disp.width + 2 * pad, height=disp.height + 2 * pad, bg="#0A1020",
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self.disp_photo = ImageTk.PhotoImage(disp)
        self.canvas.create_image(pad, pad, anchor="nw", image=self.disp_photo)
        self.drag = None
        self.canvas.bind("<ButtonPress-1>", self.corner_press)
        self.canvas.bind("<B1-Motion>", self.corner_move)
        self.canvas.bind("<ButtonRelease-1>", self.corner_release)
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(self.px(10), 0))
        self.button(row, "← Back", self.show_start).pack(side="left")
        self.button(row, "Snap to grid lines", self.snap).pack(side="left", padx=self.px(8))
        self.button(row, "Read tiles →", self.read_tiles, primary=True).pack(side="right")
        self.status = self.label(row, "", 10, MUTED)
        self.status.pack(side="right", padx=self.px(10))
        self.draw_overlay()

    def to_canvas(self, x, y):
        return self.pad + x * self.ds, self.pad + y * self.ds

    def draw_overlay(self):
        c = self.canvas
        c.delete("ov")
        quad = vision.order_corners(self.corners)
        try:
            h = vision.homography([(0, 0), (1, 0), (1, 1), (0, 1)], quad)
        except ValueError:
            h = None
        if h:
            for k in range(N + 1):
                t = k / N
                w = 2 if k in (0, N) else 1
                for a, b in (((t, 0), (t, 1)), ((0, t), (1, t))):
                    p1 = self.to_canvas(*vision.apply_h(h, *a))
                    p2 = self.to_canvas(*vision.apply_h(h, *b))
                    c.create_line(*p1, *p2, fill=ACCENT, width=w, tags="ov")
        r = self.px(7)
        for x, y in self.corners:
            cx, cy = self.to_canvas(x, y)
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=ACCENT, outline="#000000", width=2, tags="ov")

    def draw_loupe(self, i):
        c = self.canvas
        c.delete("loupe")
        size = self.px(170)
        x, y = self.corners[i]
        mag = max(self.ds * 4, 2)
        half = size / mag / 2
        crop = self.img.crop((round(x - half), round(y - half), round(x + half), round(y + half)))
        self.loupe_photo = ImageTk.PhotoImage(crop.resize((size, size), Image.BILINEAR))
        cx, _ = self.to_canvas(x, y)
        lx = int(c["width"]) - size - self.px(8) if cx < int(c["width"]) / 2 else self.px(8)
        ly = self.px(8)
        c.create_image(lx, ly, anchor="nw", image=self.loupe_photo, tags="loupe")
        m = size / 2
        c.create_line(lx + m, ly, lx + m, ly + size, fill=ACCENT, tags="loupe")
        c.create_line(lx, ly + m, lx + size, ly + m, fill=ACCENT, tags="loupe")
        c.create_rectangle(lx, ly, lx + size, ly + size, outline=ACCENT, width=2, tags="loupe")

    def corner_press(self, e):
        best, best_d = None, self.px(24)
        for i, (x, y) in enumerate(self.corners):
            cx, cy = self.to_canvas(x, y)
            d = math.hypot(cx - e.x, cy - e.y)
            if d < best_d:
                best, best_d = i, d
        self.drag = best
        if best is not None:
            self.draw_loupe(best)

    def corner_move(self, e):
        if self.drag is None:
            return
        self.corners[self.drag] = ((e.x - self.pad) / self.ds, (e.y - self.pad) / self.ds)
        self.draw_overlay()
        self.draw_loupe(self.drag)

    def corner_release(self, _e):
        self.drag = None
        self.canvas.delete("loupe")

    def snap(self):
        self.corners, _ = self.fitter.fit(vision.order_corners(self.corners))
        self.draw_overlay()

    def read_tiles(self):
        corners = vision.order_corners(self.corners)
        self.run_bg(lambda: self.reader.read(self.img, corners), self.show_review, self.status, "Reading tiles…")

    # ---- screen 3: check what was read
    def show_review(self, result):
        self.result = result
        self.board = list(result["board"])
        self.flags = set(result["flags"])
        self.selected = None
        f = self.switch("review")
        self.label(f, "Check the tiles", 16, weight="bold").pack(fill="x")
        wrap = self.px(640)
        self.label(f, "Left is your screenshot, straightened. Right is what the app read. If a tile on the right is "
                      "wrong, click it, then click the slot it belongs in to swap them.", 10, MUTED,
                   wrap=wrap).pack(fill="x")
        notes = []
        if result["quality"] > POOR_READ:
            notes.append("The tiles don't match the picture well, so the corners are probably off. "
                         "Go back and line them up.")
        if result["note"]:
            notes.append(result["note"])
        elif self.flags:
            notes.append("Tiles outlined in orange were hard to tell apart. Check those first.")
        self.note = self.label(f, "\n".join(notes), 10, WARN, wrap=wrap)
        self.note.pack(fill="x", pady=(self.px(4), self.px(8)))
        boards = tk.Frame(f, bg=BG)
        boards.pack()
        self.rpx = self.px(38)
        size = N * self.rpx
        left = tk.Frame(boards, bg=BG)
        left.pack(side="left", padx=(0, self.px(14)))
        self.label(left, "Your screenshot", 10, MUTED).pack(fill="x")
        self.view_photo = ImageTk.PhotoImage(result["view"].resize((size, size), Image.LANCZOS))
        tk.Label(left, image=self.view_photo, bg=BG, bd=0).pack()
        right = tk.Frame(boards, bg=BG)
        right.pack(side="left")
        self.label(right, "What the app read (click two tiles to swap them)", 10, MUTED).pack(fill="x")
        self.read_label = tk.Label(right, bg=BG, bd=0, cursor="hand2")
        self.read_label.pack()
        self.read_label.bind("<Button-1>", self.review_click)
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(self.px(12), 0))
        self.button(row, "← Adjust corners", lambda: self.show_corners(True)).pack(side="left")
        self.solve_btn = self.button(row, "Solve →", self.solve, primary=True)
        self.solve_btn.pack(side="right")
        self.status = self.label(row, "", 10, MUTED)
        self.status.pack(side="right", padx=self.px(10))
        self.draw_review()

    def draw_review(self):
        img = self.art.render(self.board, self.result["rot"], self.rpx, flags=self.flags, selected=self.selected)
        self.read_photo = ImageTk.PhotoImage(img)
        self.read_label.config(image=self.read_photo)
        ok = vision.is_solvable(self.board)
        self.solve_btn.config(state="normal" if ok else "disabled")
        if not ok:
            self.status.config(text="Two tiles are still swapped: this layout can't be solved.", fg=WARN)
        elif self.status.cget("fg") == WARN:
            self.status.config(text="", fg=MUTED)

    def review_click(self, e):
        c, r = e.x // self.rpx, e.y // self.rpx
        if not (0 <= r < N and 0 <= c < N):
            return
        p = vision.view_to_canon(r * N + c, self.result["rot"])
        if self.selected is None:
            self.selected = p
        else:
            q = self.selected
            self.board[p], self.board[q] = self.board[q], self.board[p]
            self.flags -= {p, q}
            self.selected = None
        self.draw_review()

    def solve(self):
        start, rot = list(self.board), self.result["rot"]

        def done(moves):
            state = {"start": start, "moves": moves, "rot": rot, "step": 0}
            save_state(state)
            self.show_guide(state)

        self.solve_btn.config(state="disabled")
        self.run_bg(lambda: solver.solve(start), done, self.status, "Working out the moves…")

    # ---- screen 4: step through the moves
    def show_guide(self, state):
        self.state = state
        self.board = list(state["start"])
        self.blanks = [self.board.index(BLANK)] + state["moves"]
        self.cur = 0
        f = self.switch("guide")
        self.count = self.label(f, "", 16, weight="bold")
        self.count.pack(fill="x")
        self.instr = self.label(f, "", 12, ACCENT)
        self.instr.pack(fill="x")
        self.guide_img = tk.Label(f, bg=BG, bd=0)
        self.guide_img.pack(pady=self.px(8))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.button(row, "← Back", lambda: self.go(self.state["step"] - 1)).pack(side="left")
        self.button(row, "Next →", lambda: self.go(self.state["step"] + 1), primary=True).pack(
            side="left", padx=self.px(6))
        self.label(row, "Go to move", 10, MUTED).pack(side="left", padx=(self.px(10), self.px(4)))
        self.jump = tk.Entry(row, width=5, font=("Consolas", 11), bg=PANEL, fg=FG, insertbackground=FG,
                             relief="flat")
        self.jump.pack(side="left")
        self.jump.bind("<Return>", self.do_jump)
        self.button(row, "Go", self.do_jump).pack(side="left", padx=self.px(4))
        opts = dict(font=("Segoe UI", 10), bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                    activeforeground=FG, anchor="w")
        tk.Checkbutton(f, text="E goes to the next move (untick while typing in chat)", variable=self.hotkey_on,
                       **opts).pack(fill="x", pady=(self.px(8), 0))
        tk.Checkbutton(f, text="Keep this window on top", variable=self.on_top,
                       command=lambda: self.root.attributes("-topmost", self.on_top.get()), **opts).pack(fill="x")
        self.label(f, "E is ignored while this window is focused. Click back into Roblox first.", 9,
                   MUTED).pack(fill="x")
        self.button(f, "New puzzle", self.show_start).pack(anchor="w", pady=(self.px(8), 0))
        self.go(state.get("step", 0))

    def go(self, k):
        moves = self.state["moves"]
        k = max(0, min(len(moves), k))
        while self.cur < k:
            b, p = self.blanks[self.cur], moves[self.cur]
            self.board[b], self.board[p] = self.board[p], BLANK
            self.cur += 1
        while self.cur > k:
            self.cur -= 1
            b, p = self.blanks[self.cur], moves[self.cur]
            self.board[p], self.board[b] = self.board[b], BLANK
        self.state["step"] = k
        save_state(self.state)
        rot, total = self.state["rot"], len(moves)
        if k < total:
            p = moves[k]
            d = move_direction(self.blanks[k], p, rot)
            v = vision.canon_to_view(p, rot)
            self.count.config(text=f"Move {k + 1} of {total}")
            self.instr.config(text=f"{GLYPH[d]}  Slide row {v // N + 1}, col {v % N + 1} {WORDS[d]}")
            move = (p, self.blanks[k])
        else:
            self.count.config(text=f"Solved ({total} moves)")
            self.instr.config(text="✓  Every tile should now be in place.")
            move = None
        self.guide_photo = ImageTk.PhotoImage(self.art.render(self.board, rot, self.px(46), move=move))
        self.guide_img.config(image=self.guide_photo)

    def do_jump(self, _event=None):
        try:
            self.go(int(self.jump.get()) - 1)
        except ValueError:
            pass
        self.jump.delete(0, "end")

    # ---- global hotkey
    def own_window_focused(self):
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        mine = user32.GetAncestor(ctypes.c_void_p(self.root.winfo_id()), 2)
        return user32.GetAncestor(ctypes.c_void_p(fg), 2) == mine

    def poll(self):
        down = key_down(VK_E)
        if (down and not self.e_was_down and self.mode == "guide" and self.hotkey_on.get()
                and not self.own_window_focused()
                and not any(key_down(k) for k in (VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))):
            self.go(self.state["step"] + 1)
        self.e_was_down = down
        self.root.after(12, self.poll)


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

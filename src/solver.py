"""Staged 8x8 sliding-puzzle solver.

Shrinks the unsolved area one row or column at a time. Each group of one or two tiles is placed with an
exact breadth-first search over (tile positions, blank position); the last 3x3 corner is solved exactly.
Moves are returned as the board position of the tile that slides into the blank.
"""
from collections import deque

N = 8
BLANK = 63
NEIGHBOURS = [[p + d for d, ok in ((-N, p >= N), (N, p < N * N - N), (-1, p % N > 0), (1, p % N < N - 1)) if ok]
              for p in range(N * N)]


def _place(board, tiles, free):
    """Shortest blank path (within `free`) that puts each tile id on the position equal to its id."""
    goal = tuple(tiles)
    start = tuple(board.index(t) for t in tiles) + (board.index(BLANK),)
    if start[:-1] == goal:
        return []
    prev = {start: None}
    dq = deque([start])
    while dq:
        s = dq.popleft()
        b = s[-1]
        for nb in NEIGHBOURS[b]:
            if nb not in free:
                continue
            ps = tuple(b if q == nb else q for q in s[:-1])
            ns = ps + (nb,)
            if ns in prev:
                continue
            prev[ns] = s
            if ps == goal:
                path = []
                while prev[ns] is not None:
                    path.append(ns[-1])
                    ns = prev[ns]
                return path[::-1]
            dq.append(ns)
    raise RuntimeError("no path")


def _apply(board, path):
    for nb in path:
        b = board.index(BLANK)
        board[b], board[nb] = board[nb], BLANK


def _groups(cells):
    head, out = cells[:-2], []
    if len(head) % 2:
        out.append([head[0]])
        head = head[1:]
    out += [head[i:i + 2] for i in range(0, len(head), 2)]
    out.append(cells[-2:])
    return out


def _solve_last(board, region):
    idx = {p: i for i, p in enumerate(region)}
    start = tuple(board[p] for p in region)
    goal = tuple(region[:-1]) + (BLANK,)
    if start == goal:
        return []
    prev = {start: None}
    dq = deque([start])
    while dq:
        s = dq.popleft()
        bi = s.index(BLANK)
        for nb in NEIGHBOURS[region[bi]]:
            j = idx.get(nb)
            if j is None:
                continue
            lst = list(s)
            lst[bi], lst[j] = lst[j], lst[bi]
            ns = tuple(lst)
            if ns in prev:
                continue
            prev[ns] = (s, nb)
            if ns == goal:
                path = []
                while prev[ns] is not None:
                    ns, mv = prev[ns]
                    path.append(mv)
                return path[::-1]
            dq.append(ns)
    raise RuntimeError("unsolvable")


def _solve(start, prefer_rows):
    board, moves, locked = list(start), [], set()
    r0 = c0 = 0
    while N - r0 > 3 or N - c0 > 3:
        h, w = N - r0, N - c0
        if h == 3:
            rows = False
        elif w == 3:
            rows = True
        elif h != w:
            rows = h > w
        else:
            rows = prefer_rows
        cells = [r0 * N + c for c in range(c0, N)] if rows else [r * N + c0 for r in range(r0, N)]
        for grp in _groups(cells):
            free = {p for p in range(N * N) if p not in locked}
            path = _place(board, grp, free)
            _apply(board, path)
            moves += path
            locked |= set(grp)
        if rows:
            r0 += 1
        else:
            c0 += 1
    path = _solve_last(board, [r * N + c for r in range(r0, N) for c in range(c0, N)])
    _apply(board, path)
    moves += path
    assert board == list(range(N * N - 1)) + [BLANK]
    return moves


def solve(start):
    """Shortest of a couple of strategies. `start[pos]` = tile id (0..62) or BLANK."""
    return min((_solve(start, prefer) for prefer in (False, True)), key=len)

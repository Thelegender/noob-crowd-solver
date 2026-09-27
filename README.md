# Noob Crowd Solver

Solves the 8×8 noob-crowd slide puzzle in Roblox from a screenshot, then walks you through the moves.

## Use it

1. Stand where you can see the whole puzzle and take a screenshot (Win+Shift+S or PrtScn).
2. Download `NoobCrowdSolver.exe` from the [latest release](https://github.com/Thelegender/noob-crowd-solver/releases/latest) and run it. Open the screenshot or press Ctrl+V to paste it.
3. Check the yellow grid sits on the gaps between tiles. Drag a corner dot if it doesn't, then press **Snap**.
4. Check the tiles it read. Click two tiles to swap them if one is wrong.
5. Press **Solve**, then follow the moves. Press **E** in Roblox to go to the next move.

Progress is saved, so you can close the app and continue later.

Windows may show "Windows protected your PC" because the app isn't code-signed. Click **More info → Run anyway**.
The app watches the E key only to go to the next move. It doesn't record or send anything anywhere.

## Build from source

```bash
python -m venv .venv-build
.venv-build/Scripts/python -m pip install pillow pyinstaller
.venv-build/Scripts/pyinstaller --noconfirm --onefile --windowed --name NoobCrowdSolver --icon assets/icon.ico --add-data "assets/reference.png;assets" --add-data "assets/icon.png;assets" --paths src src/app.py
```

Tests: `python tests/test_pipeline.py`

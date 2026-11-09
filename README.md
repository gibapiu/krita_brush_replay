# Krita Brush Replay

![Krita 5.3+](https://img.shields.io/badge/Krita-5.3%2B-3babff) ![Krita 6.0+](https://img.shields.io/badge/Krita-6.0%2B-3babff)

Krita plugin to record freehand brush strokes and replay the same movement with different brush settings.

## What it does

- Records mouse and tablet strokes on one paint layer.
- Keeps position, pressure, timing, estimated speed, tilt, rotation, and tangential pressure.
- Replays the recorded input with the current brush settings.

## Limits

Recordings stay in memory and are tied to one document and layer. On current stable Krita releases, replay can create several undo steps, reset brush dynamics between short sections, and round positions to whole pixels. Brushes that depend on tilt, speed, distance, timing, or randomness may not match the original exactly.

## Install

Copy `krita_brush_replay.desktop` and the `krita_brush_replay` folder into Krita's `pykrita` resource folder.

Restart Krita, enable **Brush Replay** in **Settings → Configure Krita → Python Plugin Manager**, restart again, then open **Settings → Dockers → Brush Replay**.

## Use

1. Select a writable paint layer and the freehand brush tool.
2. Press **Record new session** and draw one or more strokes.
3. Press **Stop**.
4. Undo the original strokes or prepare the same layer for comparison.
5. Choose new brush settings and press **Replay with current settings**.

Starting a new recording replaces the previous recording. Changing the document or layer stops recording. Do not change tools or brush settings while replay is running.

## Test

```bash
pytest -q
```

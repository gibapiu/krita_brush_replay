# Krita Brush Replay

![Krita 5.3+](https://img.shields.io/badge/Krita-5.3%2B-3babff) ![Krita 6.0+](https://img.shields.io/badge/Krita-6.0%2B-3babff)

Krita plugin to record freehand brush strokes and replay the same movement on the active layer with its original or current brush settings.

## What it does

- Records mouse and tablet strokes on one paint layer.
- Keeps position, pressure, timing, estimated speed, tilt, rotation, and tangential pressure.
- Captures each stroke's saved preset selection, colours, size, opacity, flow, rotation, pattern, and blending mode.
- Replays on the active paint layer with recorded settings (default) or current brush settings.

## Limits

Recordings stay in memory. Each recording is captured from one document and layer, then can be replayed on the active paint layer.

Replay prefers Krita's native continuous-stroke API when available (Krita 5.4+). 
Krita 5.3 uses queued tablet events on the canvas, preserving each stroke as one gesture with its pressure, tilt, rotation, and timing. 
The canvas receives focus before replay and a matching leave event after each stroke.

The synthetic fallback uses the canvas where input was recorded. Switching views or documents requires recording on the new canvas first. 
Changing the document, layer, or canvas transform during replay stops it. 
Brushes that depend on speed, distance, timing, or randomness may not match the original exactly.

Only settings exposed reliably by Krita's scripting API are captured. Unsaved preset-engine tweaks, such as spacing and dynamics curves, and eraser mode are not recorded.
Original-settings replay re-selects the preset that was active before replay, so unsaved tweaks may not survive that restoration.

## Install

No release configured atm, so simply zip plugin from the source:

```bash
zip -r krita_brush_replay_plugin.zip krita_brush_replay.desktop krita_brush_replay/
```

1. Open Krita.
2. Navigate to **Tools → Scripts → Import Python Plugin from File...** and upload your `krita_brush_replay_plugin.zip`.
3. Restart Krita.
4. Enable **Brush Replay** in **Settings → Configure Krita → Python Plugin Manager**
5. Restart Krita again.
6. Open and check **Settings → Dockers → Brush Replay**.
7. ...
8. Profit!

## Use

1. Select a writable paint layer and the freehand brush tool.
2. Press **Record new session** and draw one or more strokes.
3. Press **Stop**.
4. Select the paint layer where the replay should be drawn.
5. Press **Replay original settings** to reproduce the captured settings, or choose
   new settings and press **Compare with current settings**.

Starting a new recording replaces the previous recording. Changing the document or layer stops recording. Replay always targets the active paint layer.
Original-settings replay restores the captured pre-replay settings when it finishes or fails. Comparison mode leaves the current settings unchanged.

⚠️ Do not change tools or brush settings while replay is running.

⚠️ Replay promises the same captured settings, not identical pixels. Random brush engines and event timing can produce different pixels.

## Test

```bash
pytest -q
```

# Krita Brush Replay

![Krita 5.3+](https://img.shields.io/badge/Krita-5.3%2B-3babff) ![Krita 6.0+](https://img.shields.io/badge/Krita-6.0%2B-3babff)

Krita plugin to record freehand brush strokes and replay the same movement on the active layer with its original, edited, or current brush settings.

## What it does

- Records mouse and tablet strokes on one paint layer.
- Keeps position, pressure, timing, estimated speed, tilt, rotation, and tangential pressure.
- Captures each stroke's saved preset selection, colours, size, opacity, flow, rotation, pattern, gradient, and blending mode.
- Lets you change those settings for one, several, or all strokes and reset any changed setting to its recorded value.
- Replays on the active paint layer with each stroke's settings (default) or current brush settings.
- Browses and deletes saved recordings across documents, including documents that are closed.

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

1. In **Record**, select a paint layer, press **New recording**, draw, and press **Stop**.
2. In **Replay**, choose a recording from any document. Search by document or date.
3. Select the target paint layer, choose **Recorded settings** or **Current settings**, and press **Replay**.

To alter selected storkes open **Edit strokes** to change selected strokes.
Mixed settings show —; ↺ resets a setting.
Changed strokes are marked with ✎.

⚠️ Do not change tools or brush settings while replay is running.

⚠️ Replay promises the same captured settings, not identical pixels. Random brush engines and event timing can produce different pixels.


## Current Limitations

Each recording is captured from one document and layer, then can be replayed on the active paint layer.
Recordings are saved in Krita's data folder as you draw, and move with the document when it is saved for the first time or under a new name. Choose a recording in Replay to load it, including unfinished recordings recovered after a Krita crash. If saving fails, its strokes remain available in memory when you switch documents. A recording on a document that was never saved is lost when you switch documents.
Each document keeps its 10 latest recordings. Recording stops at 1,000,000 samples, or when 50,000 samples are held only in memory because the recording is not saved or saving has fallen behind.

Replay prefers Krita's native continuous-stroke API when available (Krita 5.4+).
Both replay modes use Krita's current global tablet pressure curve and **Use Pen Pressure** setting.
Recordings retain raw pressure only; input calibration is not saved with strokes.
Krita 5.3 uses queued tablet events on the canvas, preserving each stroke as one gesture with its pressure, tilt, rotation, and timing.
The canvas receives focus before replay and a matching leave event after each stroke.

The synthetic fallback sends input to the active view's canvas.
Changing the document, layer, or canvas transform during replay stops it.
Brushes that depend on speed, distance, timing, or randomness may not match the original exactly.
=
Only settings exposed reliably by Krita's scripting API are captured. Unsaved preset-engine tweaks, such as spacing and dynamics curves, and eraser mode are not recorded.
Replay re-selects the preset that was active before replay, so unsaved tweaks may not survive that restoration.
The blending-mode editor offers common modes only because Krita's scripting API cannot list them all.
Colours picked in the editor are stored as 8-bit sRGB.
Some brush engines round, clamp, or ignore size and rotation; replay paints with the values the engine accepts.


## Test

```bash
pytest -q
```

The suite checks user workflows through the runtime's UI and host ports, real file persistence and process restarts.
It uses simulated Krita mocks and PyQ6 for widget's interactions.

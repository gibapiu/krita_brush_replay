# Krita Brush Replay

![Krita 5.3+](https://img.shields.io/badge/Krita-5.3%2B-3babff) ![Krita 6.0+](https://img.shields.io/badge/Krita-6.0%2B-3babff)

Krita plugin to record freehand brush strokes and replay the same movement on the active layer with its original, edited, or current brush settings.

## What it does

- Records mouse and tablet strokes on one paint layer.
- Keeps position, pressure, timing, estimated speed, tilt, rotation, and tangential pressure.
- Captures each stroke's saved preset selection, colours, size, opacity, flow, rotation, pattern, gradient, and blending mode.
- Lets you change those settings for one, several, or all strokes and reset any changed setting to its recorded value.
- Replays on the active paint layer with each stroke's settings (default) or current brush settings.

## Limits

Recordings stay in memory. Each recording is captured from one document and layer, then can be replayed on the active paint layer.

Replay prefers Krita's native continuous-stroke API when available (Krita 5.4+). 
Krita 5.3 uses queued tablet events on the canvas, preserving each stroke as one gesture with its pressure, tilt, rotation, and timing. 
The canvas receives focus before replay and a matching leave event after each stroke.

The synthetic fallback uses the canvas where input was recorded. Switching views or documents requires recording on the new canvas first. 
Changing the document, layer, or canvas transform during replay stops it. 
Brushes that depend on speed, distance, timing, or randomness may not match the original exactly.

Only settings exposed reliably by Krita's scripting API are captured. Unsaved preset-engine tweaks, such as spacing and dynamics curves, and eraser mode are not recorded.
Replay re-selects the preset that was active before replay, so unsaved tweaks may not survive that restoration.
The blending-mode editor offers common modes only because Krita's scripting API cannot list them all.
Colours picked in the editor are stored as 8-bit sRGB.
Some brush engines round, clamp, or ignore size and rotation; replay paints with the values the engine accepts.

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
4. Optionally select strokes in the list (or **Select all strokes**) and change their settings.
   Settings that differ between selected strokes show —. Press ↺ next to a setting to return the selected strokes
   to their recorded value. Changed strokes are marked with ✎.
5. Select the paint layer where the replay should be drawn.
6. Press **Replay** to paint with each stroke's settings, or choose
   new settings in Krita and press **Compare with current settings**.

Starting a new recording replaces the previous recording. Changing the document or layer stops recording. Replay always targets the active paint layer.
Changing a stroke's preset keeps its own size, opacity, flow, rotation, and blending mode unless you change those too.
Changes never alter the recording and are discarded when a new recording starts. Comparison mode ignores them.
Replay restores the captured pre-replay settings, and the settings of every preset it used, when it finishes or fails. Comparison mode leaves the current settings unchanged.

⚠️ Do not change tools or brush settings while replay is running.

⚠️ Replay promises the same captured settings, not identical pixels. Random brush engines and event timing can produce different pixels.

## Reporting pressure problems

1. Record a few short strokes with light, heavy, and varying pen pressure, then press **Stop**.
2. Run **Replay**, and optionally **Compare with current settings**. Note which one differs.
3. Press **Export diagnostic report…** in the Brush Replay docker and save the text file.
4. Fill in the tester notes at the top of the file, including the tablet model, driver,
   steps to reproduce, and expected versus actual pressure. Attach the file to your bug report.

Diagnostics are collected automatically while the docker is open and also appear in Krita's
output with a `[Brush Replay]` prefix. Export is available while recording and replay are stopped.
The report includes a build marker and report ID, OS and Krita/Qt versions, relevant input
settings, brush preset names, capture and replay pressure summaries, ignored mouse event
counts, and plugin errors. It contains no canvas image or document contents.
Only the most recent 2,000 log records are retained in memory; discarded history is marked
in the report. Export soon after reproducing the problem. Closing Krita clears this history.

## Test

```bash
pytest -q
```

# Color Prime Studio

**Prepare 3D icons, reuse colors and surfaces, export PNG collections.**

[Download](https://github.com/lomatoq/Color-Magic/releases/latest) · [Русский](docs/README_RU.md) · [Беларуская](docs/README_BE.md)

## Install

Download `Color_Prime_Studio_3.5.2_Universal.zip` from release **Assets**, not GitHub's Source code archive. Keep it zipped. In Blender, use **Edit → Preferences → Add-ons → Install** (3.6) or **Install from Disk** (newer versions), then enable Color Prime Studio. Disable duplicate copies first. Save your work and restart Blender. Open **3D View → N → Color Prime**.

Tested on Windows with Blender **3.6.5 and 5.2.1 LTS**. Choose **EN / Бел** in the panel. Native Blender dialogs and tooltips follow Blender's language setting.

## Choose your task

| Task | Route |
| --- | --- |
| Render one model as it is | Fast Track → Many icons → consistent framing; check one model; Prepare batch; Current model colors; folder/size; render |
| Render many icons | Same route, check all models; camera fits each separately without scaling geometry |
| Copy a safe's appearance to a scooter | Fast Track → Model → another model; select source and checked recipients; transfer |
| Find missing regions on an import | Fast Track → Imported model → zones; check recipients; prepare |
| Detailed editing | Workspace |

Fast Track prepares the task; it **does not start rendering**. Source models are excluded from transfer recipients. Transfer copies both colors and surfaces. For colors alone, use **Workspace → From Model**, disable material transfer, then Apply. Prepare zones first when the recipient needs them.

## Models, regions and protected materials

Find models or add selected objects in Workspace. A checkbox controls processing/export; the eye controls visibility. Prepare creates/reuses a collection and `Anchor_<model>` root, preserving transforms and hierarchy. Removing a list row does not delete geometry.

Use existing regions by default. Missing-region detection preserves authored materials; explicit rebuilding may replace boundaries. Automatic segmentation is a heuristic, so check unfamiliar/dense meshes. To control exactly what changes, select objects or faces and assign **Main**, **Accent**, or **Fixed**. Keep gold/coins Fixed. Unsupported texture/shader graphs need an explicit safe color input or current-appearance export.

## Palettes and material variants

Create a named palette or capture one from a model. Apply establishes scoped native color bindings on checked models. Afterwards **Preview colors** updates shared colors without finding zones again. Restore cancels preview; Apply keeps it.

In material instances, choose Main/Accent, count and surface style. **Create** adds library materials. **Autoassign** places them on zones; manual assignment is also available. Surface edits may require Autoassign again; follow the state shown in the panel. Shader/tint settings are available on variants. Colors inherit through normal Blender nodes.

Palette JSON stores colors, names, order and inclusion flags. Save material graphs, assignments and model organization in the **`.blend`**. Ctrl+Z undoes operations; Ctrl+S saves the file.

## Export

For unchanged materials choose **Current model colors**. For recoloring choose workspace palettes and **All combinations**. Export visits Main × Accent × models × sizes: **10 × 10 × 1 × 3 = 300 PNGs**. Paired export is available too.

Enable 1x/2x/3x, custom fractional scales or absolute dimensions. Check final pixels, file count and path preview before rendering. Folders start with the model, then Main/Accent choices; filenames include model, colors and size. Existing paths are checked before rendering; overwriting must be enabled explicitly.

Models render sequentially, with other models hidden per frame. Temporary colors, visibility and render settings are restored. Camera and light contains views, framing, lights and background controls. A visible background plane is independent of PNG film transparency.

## Update and roll back

Click **Check updates** at the panel top. If a newer stable GitHub release exists, click **Install update · restart required**. The download runs in the background; the ZIP, SHA256 and internal file hashes are checked before installation. Previous addon files are backed up. Scene files are not changed. **Save and restart Blender** to load the new code.

Updates need GitHub access and write access to the addon directory; no update is installed during rendering. Core modeling/rendering works offline. Hashes detect corruption; they are not an independent publisher signature.

Backup ZIPs live in `.color_prime_backups` beside the addon folder; the exact path is printed in Blender's system console. Install a backup ZIP, or a previous GitHub release ZIP, through Preferences to roll back. Restart and keep only one copy enabled.

## Troubleshooting

| Problem | Next step |
| --- | --- |
| No panel | Enable the addon, open 3D View, press N; restart after installation |
| Mixed installation / duplicate | Disable other copies, restart, install the complete release ZIP |
| No model in list | Select its root/meshes and add selected; inspect Empty/collection hierarchy |
| Disabled action | Read the nearby state and hover help; check model checkboxes, Object Mode and active jobs |
| Auto Setup / no writable colors | Current model colors needs no recolor binding; palette export requires editable Main/Accent inputs |
| Gold changes | Assign Fixed before applying; undo the previous change if needed |
| Zones look wrong | Preserve existing boundaries and correct selected parts manually |
| Variants do not appear | Create only builds the library; Autoassign or assign manually |
| Slow color changes | Apply once, then Preview; do not rerun zone discovery for each palette |
| Deleted camera/lights | Create/repair the studio in Camera and light |
| Empty render | Check camera, lights and render visibility; prepare the studio |
| Export stops | Check error, disk space, output permissions and filename collisions; let restoration finish |
| Update fails | Check network and directory permissions; manually install release ZIP; see console for technical error |
| Version unchanged | Save and fully restart Blender |

Report Blender/addon versions, reproduction steps, expected result and traceback in [Issues](https://github.com/lomatoq/Color-Magic/issues). Share only models you have permission to distribute; remove private paths.

## Build and tests

Run `python tools/build.py` to generate ZIP and SHA256 in `dist`. Rebuild after Python changes to refresh the integrity manifest. Tests are in `tests`; historical scene-specific tests require external fixtures. Windows Blender 3.6.5/5.2.1 tests cover native setup, render/restoration, language switching, Fast Track and update validation. Arbitrary third-party graphs and meshes are not guaranteed. License: [GNU GPL v3](source/color_prime/LICENSE), retained from the original addon package.

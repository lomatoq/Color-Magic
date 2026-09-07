# RC7 validation — 2026-09-05

- Blender 3.6.5: 73 RC7 acceptance checks passed; Blender 5.2.1 LTS: 73 passed. See tests/native_rc7.py and evidence/rc7/{native36,native52}.log.
- Both versions also pass the 38-check built-in release gate, including actual PNG renders, crash recovery, save/reopen and durable rollback (evidence/rc7/release36.log and release52.log).
- Four independent shape-matching tests pass, including rotated/mirrored/scaled shapes (tests/test_shape_matching.py).
- Both versions exercise flat authored materials, two shared RGB branches inside nested shader groups, same-stem distinct group IDs, appearance-preserving adoption, all child library entries, Emission/Diffuse, 100% tint, independent visibility, physical collections, rollback, all 14 views at 0% and 45% margins, six light presets and Main-linked backdrop.
- Both versions generate a unique 300-task queue (10×10×3), combine multiple palettes, render validated 32×24 and 48×36 PNGs into Main/Accent/scale folders, reject unapproved overwrite and restore sources/pose. New models reuse exact existing source/material IDs after commit; partly assigned meshes preserve painted faces.
- Production vault (5.2.1): 12 checks passed. Its 48 original meshes were restored from RC5 backup pointers; six one-face cuts removed. Purple maps to Main, Green to Accent and Gold stays Fixed. All original face material indices are retained. Repeated zone discovery adds no materials.
- Real vault before/after PNG comparison: mean RGBA error 0.00002101; 99.9% of components differ by less than .01 and alpha is identical. A few path-tracing edge samples differ (maximum 0.141176); the comparison does not claim byte-identical renders. Parent recoloring changes the rendered vault while Gold remains unchanged.
- Vault_RC7_Review.blend retains native sources, one physical model collection and the full original-mesh/hierarchy journal. Reopening and then rollback passed. Vault_original.png, Vault_inherited.png and Vault_recolored.png are in evidence/rc7.
- Native sources remain in the .blend after disabling the add-on. Geometric inference remains bounded; unsupported/ambiguous graphs require an explicit color input rather than automatic flattening.

Interactive UI verification and installed-bundle verification are recorded in evidence/rc7/ui_review.md and installation.json when completed.

## RC8 deletion recovery
22 focused checks passed on Blender 3.6.5 and 5.2.1: delete each rig component, delete/unlink its collection, recreate, skip deleted model rows, fit a second model without duplicates, restore legacy Eevee and unavailable renderer settings. Evidence: evidence/rc8/.

## RC9 selected-part recoloring
20 focused checks and 73 workflow regression checks pass in each of Blender 3.6.5 and 5.2.1 LTS. Includes shared-material isolation on partial meshes, five-part child reuse, repeat clicks, rescan protection, parent recoloring, freeze/reassign, rollback, linked-mesh multi-object Edit Mode, native links without addon and actual 32px texture renders unchanged on attachment. Evidence: evidence/rc9/.

## RC11 workspace
In Blender 3.6.5 and 5.2.1: 24 two-model workflow checks, 73 main regression checks, 20 selected-part checks and 22 studio-recovery checks pass. Tests cover draft/application separation, both/subset application, unchanged shared originals, source-to-target metallic transfer including evaluated shader colors, Fixed gold, repeat source capture, missing recipients, deleted models, source exclusion from export and retained advanced palette export. Evidence: evidence/rc11/.

The native 5.2.1 UI was inspected in the user's empty scene and a separate synthetic two-model fixture. The fixture was removed afterwards; the original scene was restored without saving or restarting Blender. Visual inspection is not a claim of complete accessibility testing.

## RC12 surface preservation / speed
42 focused checks pass on Blender 3.6.5 and 5.2.1, plus the 73-check native suite. The source fixture contains distinct metallic and plastic materials of the same family; recoloring retains exact material and mesh IDs. Preview keeps durable restoration data, refreshes after draft edits and restores exact originals. A queue with 20 models isolates one model at a time and restores visibility. The native suite includes small actual PNG renders and a 300-job queue.

On a saved copy of the current vault, recovery preserves original Metallic values on all 205,372 faces across 48 meshes and leaves the unchecked scooter's mesh ID unchanged. Measured 5.2.1 runs: recovery 5–13s, repeat recolor 25–51ms, preview 24–50ms; scooter geometric search (97,324 faces) finds 32 zones in 1.5–2.4s. Interactive preview on both open models took 37ms and its debounced update and exact cancellation were verified. These measure Python/data operations, not viewport redraw or final render time. Evidence: evidence/rc12/.

## RC13 targeted regression review
45 focused workspace checks and 73 native regression checks pass on Blender 3.6.5 and 5.2.1. On the saved current scene, 26 unused slots were removed with exact per-face material identities retained on the vault. The scooter partition is byte-for-byte unchanged; its selected Accent zones are 2/3/4, excluding aggregate region 1. Accent surface area changes from .822554 to .195182. Before/after 384px native Cycles renders use the same camera and 4 samples (comparison previews, not final-quality output). Repeat Apply preserves mesh and material IDs. Original datablocks remain for rollback; automatic regions remain geometric suggestions. Evidence: evidence/rc13/.

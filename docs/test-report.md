# Alpha 1 implementation and validation report

Date: 2026-10-28 (Asia/Seoul). Status: **Linux-validated alpha source; Windows completion gate remains open**.

## Implemented and exercised

- Artist/group creation and rename, aliases, mixed groups, solo artists, multiple memberships, target flag and unlink, name history.
- Local file/folder import, metadata/EXIF orientation, SHA256 duplicates, confirmed person/group links, external references and managed copies.
- Original integrity, missing/corrupt image handling, SHA256 relinking, thumbnail persistence/recovery/source-signature invalidation.
- Dark main window, navigation, model/delegate gallery, bounded thumbnail jobs/LRU, incremental pages, search and four sort modes.
- Double-click viewer, fit/zoom, full-resolution pixel mode, previous/next beyond the first gallery page, Esc, fullscreen state, preserved selection.
- Favorites and settings across restart, atomic settings failure preservation, malformed-settings defaults, DB foreign keys/migration and future-schema protection, locked-write failure without losing records.

## Test environment and results

Linux 6.18.55 x86_64, Python 3.12.14, Qt/PySide6 6.8.3, Pillow 11.2.1, SQLite 3.53.1, pytest 8.3.5, pytest-qt 4.4.0. All dependencies are pinned in `requirements.lock`.

`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --junitxml=docs/test-results.xml`: **19 passed, 0 failed, 0 skipped**, 4.95 seconds in the recorded run. Native entry point `python -m app.main` also remained running in an isolated data directory until deliberately terminated by the smoke harness. It did not exit on startup. Fontconfig reports a nonfatal cache-directory warning in this managed environment.

UI evidence: `gallery-ui.png` (1440×920) and `portrait-ui.png` (900×1200). All displayed artwork and artist names are synthetic UI-review fixtures, not real collection results. Portrait layout automatically collapses the metadata panel. This is offscreen rendering, not a Windows monitor or human gameplay test.

## Performance observations

`benchmark.json`: 10 samples per query at 100, 1,000 and 10,000 synthetic metadata records. At 10,000 records: first-page median 5.760 ms; last-page median 5.845 ms; filename-search median 12.631 ms; resolution-sort median 5.637 ms. These measurements include tracemalloc overhead and warm local filesystem caches. Peak tracked Python allocation was 3.25 MiB for the benchmark process, excluding Qt/native memory. This does not measure real photo decoding.

`image-profile.json`: 100 generated 2400×3600 JPEGs imported successfully, zero failures; 2,808.76 ms total import, first thumbnail 72.61 ms. During offscreen loading and scripted scrolling, 10ms timer gaps had p95 12.31 ms / maximum 16.73 ms. Peak process RSS 157.81 MiB includes fixture generation and Qt; 22 decoded thumbnails were retained at the observation point. These are event-loop measurements, **not measured display FPS**, and not a guarantee on Windows, all image formats or arbitrary hardware.

## Problems found and corrected

- SQL escaping for literal `%`/`_` in search; a dedicated escape character now preserves literal input.
- Group searches must include indirect membership links, not only directly assigned group photos.
- Viewer pagination previously stopped at the gallery's loaded page; it now loads the next filtered page asynchronously.
- Cancelled queued jobs previously still decoded images; pre-execution cancellation and finished cleanup were added.
- Thumbnail cache now validates source signatures and recovers corrupted cached JPEGs.
- DB writes were moved off the GUI thread, and narrow windows automatically collapse the detail panel.
- The initial double-click test lacked the first click expected by Qt's input sequence; it now exercises an actual click plus double-click and modal close.

## Unverified / remaining limitations

- **Windows executable build, Windows launch and Windows ZIP are not performed or generated.** This Linux machine has no attached Windows runner. `build_windows.bat` and packaging scripts are supplied, not claimed as verified Windows output.
- Actual display frame pacing, Windows HiDPI/multi-monitor behavior, native drag ergonomics, prolonged usage, 8K/100MP stress and disk-exhaustion UI behavior need Windows/native testing. A simulated atomic-write failure is tested; actual disk-full behavior is not.
- Complete color-managed/HDR display, animated formats, TIFF pages beyond the first, region/tiled decode and neighbor-image prefetch are not implemented.
- Main-thread artist/association reads are small synchronous queries. Very large artist trees and loading all metadata pages remain optimization targets; only the tested sizes are supported by evidence.
- Source modification updates thumbnail signatures but asset metadata needs reimport; no file watcher. Manual photo associations can be added, but photo-person unlink/history UI is future work.
- Online collection, automatic discovery, analysis/color filters, related-photo engine and both AI upscalers are unimplemented. No provider/network/AI functionality is represented as passed or enabled.
- Provider terms, model usage rights and Windows-specific dependency licenses must be checked when those components are actually adopted/bundled. Installed dependency notices were collected in `dependency-licenses/`.

## Deliverables

- Actual source ZIP: `dist/K-IDOL-ARCHIVE_v0.1.0_Source.zip` with SHA256 sidecar and CRC verification.
- Windows build recipe: `build_windows.bat`; actual Windows ZIP: **not generated**.
- README, CHANGELOG, architecture/follow-up notes, test XML, measurement JSON and synthetic UI captures included.

Full stage-one completion cannot be claimed until Windows execution and distribution are verified. Current source is ready for Windows build/validation, with success there still unverified.

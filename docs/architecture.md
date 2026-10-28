# Alpha 2 architecture update

The existing isolated checkout is `/workspace/K-IDOL-ARCHIVE`. Do not create worktrees unless explicitly requested. No dependency, data-directory or persistent schema change was required for v0.2.0; the v1 library, IDs, associations and favorites remain compatible.

- `GalleryModel` reports all result positions but holds only eight 256-row metadata pages. Missing positions request bounded asynchronous pages; failed pages wait for refresh. The view owns no widget per image. Qt still maintains internal geometry per position, so total process memory is not independent of result size.
- Thumbnail scheduling is throttled rather than trailing-debounced, cancels obsolete visible-range jobs and decodes cache files off the GUI thread. Job tokens prevent stale completion from removing newer requests.
- `ImageCache` enforces a 128MiB byte budget and 12-entry cap. Default 4096px neighboring previews are prefetched separately from foreground work. Oversized prefetch candidates are skipped. Current displayed images and decoder buffers are outside the cache budget.
- The viewer uses owned native QImages and a custom graphics item that draws the exposed source rectangle, avoiding a second full-resolution QPixmap conversion. This is **viewport-limited painting, not file-region decoding**. Qt may fully decode PNG internally. Active native decodes cannot be interrupted; queued jobs can be cancelled.
- Viewer metadata is a sliding window of at most 768 records with forward/backward query pagination. Source-pixel zoom accounts for preview scale and device pixel ratio.
- Refresh bookmarks resolve stable asset IDs with a background SQL window query, restoring selection and vertical anchor even when newly registered images change positions. In a uniform grid an inserted image may naturally change columns.
- Provider/AI contracts remain inactive. Live viewer pagination uses the current DB, not a persistent transactional snapshot; simultaneous edits affecting a filter/order can change later page membership.

The following Alpha 1 notes are retained as historical context; their unbounded-metadata and pixmap notes are superseded above.

# Alpha 1 architecture

The official stage-one codebase is a new isolated project at `/workspace/K-IDOL-ARCHIVE`. The pre-existing game release repository is unchanged. No Git worktrees were created. Future cloud tasks should use this existing checkout rather than create worktrees unless explicitly requested.

## Modules

- `core.py`: application paths, atomic JSON settings and rotating logs.
- `database.py`: SQLite connection-per-operation repositories, foreign keys, transactional migration v1, query indexes. Uses standard-library SQLite rather than an ORM to keep the local app dependency surface small.
- `images.py`: metadata/EXIF, source hashing, managed copy, folder enumeration, thumbnail cache signatures, worker-safe QImage previews.
- `workers.py`: bounded thread pools, cancellation flags, queued results/errors and lifecycle tracking.
- `gallery.py`: model/view/delegate drawing (no widget per photo), 256-row asynchronous pages, viewport-prioritized thumbnails, bounded decoded LRU.
- `viewer.py`: asynchronous decode, stale-response rejection, fit/full-resolution modes, pan/zoom, filtered-result pagination and fullscreen.
- `dialogs.py`, `main_window.py`, `theme.py`: UI orchestration, management forms, navigation and centralized theme.
- `extensions.py`: capability-based Provider/Analyzer/Upscaler contracts and registry. No third-party providers or models enabled.

For this alpha, cohesive modules replace the proposed many empty package folders. Import/thumbnail services are isolated from widgets, and future providers cannot directly drive UI. As features grow these modules can split into subpackages without changing persistent IDs or rewriting user data.

## Storage and identity

A person has a UUID, aliases, separate solo state and many-to-many memberships. Groups preserve complete membership and a separate `gallery_target` flag. Images are SHA256-deduplicated assets with many-to-many person and group links. Person links default to manual user confirmation with provenance fields. Same-name candidates are rejected for review, not merged automatically. Multiple source IDs/advanced lifecycle history will need richer migrations in stage two.

Captured and published dates are independent nullable fields. Registered time is never presented as capture time. Name changes are retained. Extensible versioned records reserve analysis/job/cluster/provider payloads; full typed online/AI schemas are not claimed to be implemented. External IDs have provider namespaces.

## Performance boundaries

UI does not decode images or run imports on the main thread. Writes, gallery queries and image work use separate pools. Small entity/association reads remain synchronous; very large artist catalogs will require async trees in later stages. Search is debounced, LIKE values are escaped and sort SQL uses fixed identifiers. All loaded metadata stays in the list model; this alpha does not promise constant metadata memory over hundreds of thousands of items. Viewer pagination preserves filter/order, with results read from the current DB rather than a transactional long-lived snapshot.

At most eight thumbnail jobs are outstanding, two execute concurrently, and at most 96 decoded 512px thumbnails are retained (about 96 MiB worst case). Old queued viewer jobs exit before decoding when cancelled. In-progress PIL decode cannot be forcibly interrupted; shutdown waits for the current decode/file operation. A maximum 100-million-pixel safety limit is enforced. Native decoder allocation is measured separately from Python allocations.

Thumbnail version and source path/size/mtime signature invalidate persistent cache. If external source content changes, reimport it to refresh asset metadata; automatic file watching and source-content reconciliation are not implemented. Existing managed originals are hash-verified before reuse. No originals are removed by the app.

## Next-stage work

1. Windows-native build, actual launch/exit, display/DPI, drag panning and install/update validation are the first release gate.
2. Profile physical scrolling, larger real image sets and 8K/100MP decoding; use tiled/region decode and bounded prefetch where measured necessary.
3. Add cancellable async artist tree reads, stable snapshot/keyset pagination, richer membership/name/solo histories and typed provider/source/license/sync migrations before online collection.
4. Validate each provider's current terms, API/rate limits and separate metadata/image/AI usage rights. No external platform has been approved in this alpha.
5. Preserve existing photo links/favorites/corrections through every migration. Background collection/analysis must yield to foreground image viewing.

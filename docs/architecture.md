# Alpha 6 architecture update

`workers.Tasks` owns a pending deque and hands Qt only the configured number of active runnables. Cancelled queued jobs are pruned before dispatch; optional keys retain the latest replaceable request. Explicit unkeyed writes are preserved. Signal connections are disposed for both completed and never-started jobs to break closure cycles. Native active decodes still require cooperative completion.

Schema v5 adds per-source scheduling and claim tokens to `sync_jobs`, persistent source cooldown/turn records, and `perceptual_features` with five covering band indexes. Source turns are logical monotonically increasing sequence numbers, not wall-clock timestamps. Atomic claims exclude an already-running network job of the same source; local analysis is independent. Production `OnlineService` finishes with the claim token, preventing a recovered old invocation from overwriting a new claim. At startup the entry point acquires a data-directory `QLockFile` before DB migrations or recovery. Queue consumers outside the app entry point must coordinate recovery themselves.

`OnlineRunner` has at most two network tasks and one low-priority analysis task. Settings can reduce network work to one; running tasks complete before the new capacity is enforced. Reference-counted foreground scopes and a short scrolling deferral suppress new automatic launches while preserving explicit manual requests. Source fairness applies to automatic claims; targeted requests retain their explicit request queue.

`request_pacing.HostPacer` shares per-host spacing and server cooldown across HTTP clients and redirects, without holding a global lock while waiting. A cooldown longer than two seconds returns a retry condition so a worker does not wait for minutes. Durable source backoff survives restart; runtime host pacing is rebuilt per process. Inherited proxy/TLS trust and host guards remain active.

`perceptual` splits 64-bit dHash into 13/13/13/13/12-bit bands. At Hamming radius four at least one band is equal. Bounded sorted prefixes from five indexes are unioned, paged by item ID and then filtered by exact distance/current revision/analysis permissions. Batch size is at most 512; source jobs checkpoint the cursor and input SHA. Continuations reuse verified stored features. Per-invocation suggestions stop at 200 to bound ambiguous low-information hashes. Schema migration backfills only existing stored hashes in chunks, without new pixel analysis or permission grants.

`Database.register` begins an immediate transaction before duplicate lookup so concurrent source completions share the same asset and preserve both associations. This is logical SHA256 deduplication; cross-source storage copies are not globally compacted or deleted. See stage-6-report.md for evidence and remaining release gates. Historical version notes follow.

# Alpha 5 architecture update

`related_schema.py` adds schema v4 transactionally, backing up existing v1/v2/v3 databases. Canonically ordered `related_overrides` store symmetric user-confirmed event/related/excluded decisions and evidence without modifying assets or identity links. `related_searches` stores one latest audit snapshot per seed, including input hashes, algorithm version and ranked reasons; it is never reused as an authorization token. Date and reverse relationship indexes bound candidate lookups.

`related.py` samples at most 256 entries per reason into a pool of at most 1,024 assets and returns at most 120 results. It joins only confirmed person links, distinguishes capture/publication dates, and never substitutes registration dates. Existing permitted dHash features are compared only for current saved revisions and currently permitted analysis on both images. Visual proximity and shared pages are suggestions, never event/identity proof. User exclusions are rechecked directly for each candidate.

Reference mode requires a selected confirmed Person ID and separate local-analysis/AI-input rights on seed and candidate. Remote origins need a current reviewed source and resource revision; an additional local import marker cannot bypass those checks. Thumbnail and original-view rights are separate. No restoration model runs.

`related_dialog.py` performs searches, original-list preparation and mutations off the GUI thread. A close request during an explicit mutation waits for its result. `related_strip.py` is opt-in with 12 list items, a single low-priority worker and serial thumbnail delivery. Seed changes/hiding invalidate generations; closing stops workers and releases icons. Existing virtual gallery, bounded thumbnail and viewer caches remain in place. The reusable gallery accepts an optional worker-side thumbnail authorization callback.

External provider policy/API and Windows validation gates remain open. See stage-5-report.md for tested scope and remaining fifth-stage work. Older architecture notes below describe their original release versions.

# Alpha 3 architecture update

`online_schema.py` adds schema v2 transactionally. A version-1 SQLite backup is retained before migration. Local originals remain in `assets`; remote candidates live in `remote_images` and only verified user-requested originals are attached to local assets. Provider IDs and file-revision hashes identify remote revisions independently of local SHA256 deduplication.

`providers/http.py` uses inherited proxy/CA trust, HTTPS hostname validation, guarded redirects, descriptive User-Agent, response bounds, request pacing and bounded read timeouts. `wikimedia.py` implements bounded discovery/entity/category API adapters and per-file metadata parsing. Provider policy is unreviewed until explicit current evidence is recorded; no live permission is implied by the existence of an adapter.

`online_repository.py` owns external-ID bindings, source proposals, watched targets, remote indexes and a bounded-key persistent job queue. `online_service.py` handles one page/job, verified atomic downloads and source provenance. `online_runner.py` runs in a separate worker pool and only signals a main-library refresh for actual local changes. Source metadata updates therefore do not reset the foreground gallery every job.

The online dialog pages at 200 remote rows, displays at most 300 review candidates, omits large raw source JSON from periodic snapshots, and decodes selected previews in workers. Raw metadata is retained in SQLite for audit. Original hashes, local paths and source license data are distinct. Categories do not assert depicted identity; person linking requires explicit confirmation.

Conservative automatic registration requires referenced structured claims, an operational date window and a Commons category. Existing bindings and name conflicts never trigger name-only merges or overwrite local corrections. This narrow rule is fixture-tested, not live-validated. Complete automatic coverage, mixed-group roster reconciliation and reliable depicted-person filtering remain unverified/incomplete.

Current external blocker: the enforced runtime permits no custom Wikimedia hosts. Seven required hosts are persisted in an editable environment draft but not active/published. Live API contracts, vocabulary and current policy review remain open release gates. Windows validation also remains open.

Historical notes follow; schema-v1 statements below are superseded by this update.

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

## v0.4 multi-source extension

`source_specs` validates data-only manifests; `source_schema` adds schema v3. `source_repository` stores independent permissions, pages, immutable revision evidence and provenance. `page_discovery` extracts declared image candidates; `web_service` mediates permission/robots/host checks, measured probes, explicit preservation and separately permitted similarity analysis. Optional `rendered_page` uses sandboxed Playwright with intercepted resources, not a screenshot source. `source_pane` keeps the source workflow inside the online dialog. Network and local analysis have separate bounded runner queues. Source IDs/categories flow through the virtual gallery and viewer queries. See stage-4-report.md for limits and actual validation scope.

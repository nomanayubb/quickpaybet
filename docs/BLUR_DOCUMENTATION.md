# Casino Content Blur — Documentation

Compliance requirement from the client: suggestive/immodest imagery of women (thumbnails and, where a live dealer is involved, the actual in-play video) must be blurred for players. This document tracks everything related to that feature specifically, separate from the main project master documentation.

---

## 1. Thumbnail blur (static images)

**Shipped 2026-09-10.**

Some game cover thumbnails show suggestive artwork (a woman's face/clothing). The client needed a precise, per-image blur — not the whole thumbnail, and not a fixed position, since every game's artwork layout differs.

- `CasinoGame.blur_region` — a `JSONField` storing `{top, left, width, height}` as **percentages of the image**, so it's resolution/display-size independent. Never touched by `sync_catalog` — same "admin curation survives a re-sync" pattern as `is_active`.
- Admin tool: `admin_casino_game_blur_view` (`/panel/casino/games/<id>/blur/`) — click-drag directly on the real thumbnail, live blurred preview, then Save.
- `admin_casino_games.html` — Thumbnail column + a per-row "Set/Edit blur" link.
- Rendering: `_casino_game_card.html` (the shared partial used everywhere a game is shown to players — lobby, provider pages, search) renders the blur as an absolutely-positioned `backdrop-filter: blur()` div at the saved percentages. Only that exact region blurs; cards, logos, and background stay untouched.
- The drag tool uses Pointer Events (not mouse-only), so it works on touch devices too — this was a real bug fix after the client reported the tool "not working," traced to touch input never firing `mousedown`/`mousemove`.
- 5 tests. Live-verified end to end.

---

## 2. Live in-play blur (fixed box, admin-positioned)

**Shipped 2026-09-10.**

The client's real ask: blur a live dealer's face/upper costume **during actual play**, not just the static thumbnail.

### Why real-time tracking isn't possible from the page itself

The live video is rendered inside a cross-origin iframe (Waija's/the studio's own domain). The browser's Same-Origin Policy makes that iframe's pixels **completely unreadable** to any script running on our own page — this is a hard security boundary, not a missing feature or a permissions issue. No detection algorithm (manual or AI) has anything to analyze, because nothing on our page can see a single pixel of that video. This was explained to the client in detail; they confirmed a fixed, admin-positioned box was acceptable as the interim solution while the AI-tracking system (Section 3) is built.

### What was built

- `CasinoGame.live_blur_region` — same `{top, left, width, height}` percentage shape as the thumbnail's `blur_region`, but sized against the **same 80vh container** the real play page (`casino_play.html`) uses, so saved positions line up exactly regardless of viewport.
- Admin tool: `admin_casino_game_live_blur_view` / `admin_casino_game_live_blur.html` — a drag-to-select tool positioned against the free demo stream, where available.
- **Discovered live**: most live-dealer games have no demo mode at all ("Demo mode is not available for this game" — you can't demo a real live broadcast). The editor falls back to the game's own promotional thumbnail (usually a real photo of the actual dealer/table) inside the same letterboxed container, keeping the percentage math consistent either way.
- Rendering: a persistent `backdrop-filter: blur()` overlay on `casino_play.html`, visible for the whole session. Pure CSS compositing — zero effect on the video's own loading or buffering.
- 7 tests. Live-verified on a real live-dealer game (Amazing Baccarat).

### Real bugs caught and fixed along the way

1. **z-index stacking bug** (2026-09-10): the blur overlay had an explicit `z-index:3` while the branding loading screen (`#casino-loading-overlay`, showing "Games provided by quickpaybet.com") had none. An element with an explicit z-index renders above one with none, regardless of DOM order — so the blur was rendering *on top of* the loading screen instead of underneath it, blurring our own branding text during the load. Fixed by giving the loading overlay an explicit, higher z-index (4) than the blur overlay (2).
2. **Thumbnail-vs-real-layout mismatch** (2026-09-10): boxes were enlarged (per client request, to cover more of the upper body) while positioned against the *promotional thumbnail* — a marketing "hero shot" with a completely different composition than the real in-play screen (which also shows the wheel, betting controls, chip stacks, etc.). The enlarged boxes ended up covering real, clickable betting elements (e.g. the wheel in Lightning Storm), which would have blocked players from actually betting. **All fixed-box regions applied that session were reverted** (cleared back to `None`) once this was caught. Correct process going forward: verify placement against the **actual live game screen** (a real, zero-cost launch — Waija's seamless wallet only charges on an actual bet, not on launch/viewing), never the thumbnail alone, whenever a box needs to extend beyond a tight face-only crop.

### Games covered so far

*(Update this table as games are added/removed. `None` means no fixed box has survived review — either never set, or reverted after the thumbnail-mismatch incident above.)*

| Game | Brand | live_blur_region | Notes |
|---|---|---|---|
| Amazing Baccarat | pragmaticplaylive | None (reverted) | Originally set against thumbnail; cleared per client instruction pending AI tracking / re-verification against the real screen. |
| Funky Time | Evolution Live | None (reverted) | Same. |
| Monopoly Live | Evolution Live | None (reverted) | Same. |
| Mega Ball | Evolution Live | None (reverted) | Same. |
| Lightning Storm | Evolution Live | None (reverted) | Client directly confirmed this one covered the wheel — flagship bug report for the thumbnail-mismatch issue. |
| Marble Race | Evolution Live | None (reverted) | Same. |
| Crazy Pachinko | Evolution Live | Never saved | Box was drawn but the session moved to "remove all blur" before saving. |

### Evolution Live game-show survey (2026-09-10)

Reviewed all 14 Evolution "game show" titles' thumbnails to identify which need blur at all (as opposed to the ~400 standard table games, which are overwhelmingly modestly-dressed professional dealers):

- **Male host — no blur needed**: Dream Catcher, Football Studio, Imperial Quest, Balloon Race, Stock Market.
- **Modest costume — no blur needed**: Crazy Time (formal jacket, high collar), Crazy Coin Flip (turtleneck, covered arms), Ice Fishing (winter coat).
- **Real exposure — needs blur**: Funky Time (sleeveless, bare shoulders), Monopoly Live (strapless), Mega Ball (V-neck), Lightning Storm (off-shoulder corset dress), Marble Race (sleeveless), Crazy Pachinko (one-shoulder, bare arm).

The ~400 remaining standard table games (Blackjack Classic N, Baccarat A/B, various Roulette variants, etc.) were not individually reviewed — spot checks suggest these are overwhelmingly professional, fully-covered dealer attire (the "vulgar costume" concern concentrates in the themed game-show titles, not the bulk catalog), but this has not been exhaustively confirmed game-by-game.

---

## 3. AI-based live tracking (in progress)

**Started 2026-09-10.** This is the system that replaces the fixed-box approach with one that actually follows the dealer as she moves, rather than sitting at one static position for the whole session.

### Why this is possible (correcting an earlier statement)

Section 2 explains why **JavaScript running on our own webpage** can never read the video's pixels — that remains true and unbypassable. But a **screenshot** — capturing the final rendered image the browser displays — is not blocked by that rule, because it happens at the browser's compositor level, not via JavaScript DOM access. A **separate, external, always-independent service** (not code running inside the player's page) that periodically screenshots the actual game and runs face-detection on that image can genuinely see her position. This is the same principle behind any browser-automation screenshot tool.

### Design

- **Not per-player, per-game.** Since a live dealer's video is the same broadcast for every viewer, one watcher process per *actively-played game* serves every player currently in that game — not one per user. Confirmed with the client using a concrete example: 40 users spread across 5 or 15 different tables means 5 or 15 watchers, not 40.
- **On-demand, not always-on.** A game's watcher starts only when the first player opens it, and stops when the last player leaves that specific game — never runs for games nobody is currently playing. Confirmed with the client explicitly ("if any of them close [game] 5, then program stop for 5").
- **Detection target: faces/people specifically, not generic motion.** A naive frame-differencing "what moved" detector was considered and rejected — it can't distinguish a person from the wheel spinning, balls bouncing, or lights flashing, and would end up blurring game elements exactly like the thumbnail-mismatch incident in Section 2, but unpredictably. The detector must be a proper face/person model.
- **No video storage.** Each screenshot is analyzed and discarded immediately; only the tiny resulting `{top, left, width, height}` region is kept. No cloud video storage requirement.
- **Real, ongoing infrastructure cost.** This needs an always-available compute process (a headless browser + a face-detection model) running for the duration any game is actively being watched. This is a genuinely new subsystem for this project - not a template edit like Sections 1–2 - with real hosting cost and a new failure mode to guard against (see Safety below).
- **Safety/fallback**: if the detection service is unavailable, errors, or hasn't reported a fresh position recently, the player-facing overlay must fall back to the game's fixed `live_blur_region` (Section 2) rather than showing no blur at all. Never fail open.

- **Gender-aware, not just face-aware.** A live table can rotate between male and female presenters — confirmed live against this exact proof-of-concept game (Lightning Storm switched to a male host partway through testing). Face detection alone has no gender concept, so a confirmed face only actually triggers a blur once *also* classified female by a second, independent model. Client explicitly confirmed this requirement: "it must scan whether female is sitting or male, and it only has to blur female not male."

### Setup (per environment)

```bash
pip install playwright mediapipe
playwright install chromium
# gender_net.caffemodel is gitignored (45MB) - download once:
curl -L -o apps/casino/ai_models/gender_net.caffemodel "https://www.dropbox.com/s/iyv483wz7ztr9gh/gender_net.caffemodel?dl=1"
```

`opencv-contrib-python` gets installed as mediapipe's own dependency, but **must be pinned to `4.10.0.84`**, not whatever mediapipe pulls in by default (5.x at time of writing) - see the Caffe-removal note below. If a newer version is already installed:

```bash
pip install "opencv-contrib-python==4.10.0.84"
```

`blaze_face_full_range.tflite` and `gender_deploy.prototxt` are small enough to be committed directly (`apps/casino/ai_models/`) - only the 45MB caffemodel needs the manual download step above.

### Implementation (shipped as a runnable daemon, 2026-09-10)

**Not a Celery task** — a persistent, long-lived process (`python manage.py run_ai_blur_tracker`), the same pattern as running `celery worker`/`celery beat` as their own processes. It is not started automatically by anything yet (not `runserver`, not Celery) — a deployment needs to run it as its own service.

**Stack**:
- `playwright` (async API) — headless Chromium, one browser-context tab per actively-watched game, screenshotting on a loop.
- `mediapipe` Tasks API, `blaze_face_full_range.tflite` model (`apps/casino/ai_models/`) — face detection. Chosen over the `short_range` model after `short_range` missed a real profile-angle shot of the dealer entirely in testing (she's rarely dead-on frontal on a live table); `full_range` catches more real faces but also more false positives on game artwork.
- OpenCV `dnn` (Levi & Hassner gender CNN, Caffe format, `apps/casino/ai_models/gender_*`) — gender classification, run only on an already-confirmed face (a second real inference cost, not applied every tick).
- **Real compatibility issue hit and fixed**: OpenCV 5.x (installed as mediapipe's own dependency) has *removed Caffe support entirely* ("Caffe importer has been removed. Please use ONNX-converted models or use an older OpenCV version" — its own literal error text). Pinned `opencv-contrib-python==4.10.0.84` instead (mediapipe declares no version constraint on it, so this is a safe downgrade) — verified both mediapipe's face detector and the Caffe gender model work correctly together on this version.

**Django-side contract** (`apps/casino/models.py`, `apps/casino/services.py`):
- `CasinoGame.ai_tracking_enabled` — per-game opt-in.
- `CasinoLiveViewer` (user, game, `last_heartbeat`) — one row per (user, game), refreshed by `casino_wallet_status_view`'s existing poll (piggybacked, not a new endpoint), throttled to one DB write per 2s per viewer. A DB row rather than a cache key specifically because this project's local dev cache is process-local (`locmem`) — a cache-based heartbeat would silently never be visible to the separately-running tracker process at all.
- `record_live_viewer_heartbeat(user, game)` / `get_effective_live_blur_region(game)` (`apps/casino/services.py`) — the heartbeat write and the safety-fallback read (fresh AI detection → else fixed `live_blur_region` → else `None`). `AI_TRACKING_VIEWER_STALE_AFTER_SECONDS=10`, `AI_TRACKING_DETECTION_STALE_AFTER_SECONDS=5`.
- `casino_wallet_status_view`'s JSON response gained a `blur_region` field; `casino_play.html`'s poll handler (`updateBlurOverlay`) moves the overlay div to match every poll, independent of the balance/exposure hold logic, with a CSS transition so the move is smooth rather than a jump-cut.

**Detection pipeline per tick** (`GameWatcher._tick`, `run_ai_blur_tracker.py`):
1. Screenshot the page.
2. Raw face detection (`_detect_raw_face_box`) — returns the single largest detected face as percentages, or `None`.
3. **Confidence floor** (`MIN_DETECTION_CONFIDENCE = 0.80`) — set via the detector's own `min_detection_confidence` option. Caught live: the detector matched cartoon icon artwork on the wheel as a "face" with real confidence (0.73) when no dealer was even in frame. 0.80 sits strictly between that false positive and a real true positive (0.88) from the same test run.
4. **Temporal confirmation** (`MIN_CONSECUTIVE_TICKS = 2`, `POSITION_MATCH_TOLERANCE`) — a detection is only trusted once it recurs in roughly the same spot on 2 consecutive ticks. Documented as *not* sufficient alone during an idle/static camera shot (a false positive on a stationary wheel icon repeats every tick too, since the icon isn't moving) — it only helps alongside the confidence floor, not instead of it.
5. **Gender classification**, only on an already-confirmed box — crops the face (padded 30% each side for context), runs it through the Caffe gender net, and only proceeds if classified `Female`.
6. Padding (`_pad_region`) — expands the tight face box outward/downward (35% each side, 25% above, 160% below - covers down to upper chest, per the client's "chest part must be covered" instruction) before writing to `detected_blur_region`.

### Live verification (2026-09-10, real Lightning Storm sessions, zero cost - seamless-wallet launches never touch the wallet until an actual bet)

- **Detection + smooth tracking confirmed**: a 60-tick real run caught a presenter's face for 4 consecutive ticks with small, consistent position deltas (`cx≈66.9→68.7`, `cy≈62.9→64.6`) — real motion tracking, not noise — then correctly cleared once the camera cut away (0 detections for the following 26 ticks).
- **False-positive rejection confirmed**: the same false positive (wheel icon, score 0.73) that appeared in early testing was correctly excluded once `min_detection_confidence=0.80` was set on the detector itself, while the same run's true positive (0.88) still passed.
- **Gender classification confirmed 100% correct** on the two known-gender frames available: the male host frame from the live tracking run above → classified `Male` (M=1.000, F=0.000); the game's own official promotional photo (a real photo of the actual female host) → classified `Female` (M=0.000, F=1.000).
- **The male-host discovery itself is a real, load-bearing finding, not a test artifact**: it's the reason gender classification exists as a second stage at all — without it, this exact proof-of-concept game would have blurred a man who needed no blur, contradicting the client's explicit "only girl" requirement.
- Not yet captured live in a single unbroken run: a *female* detection flowing all the way through to a saved `detected_blur_region` in one continuous daemon session (both stages were validated, independently, against real footage from this same game — but the live sampling windows tested so far happened to catch either "no one clearly in frame" or "the male host" during the presenter's on-screen moments, not the female host's). A follow-up 3-minute run (2026-09-11) caught nobody at all, male or female. Live dealer studios typically run multi-hour presenter shifts, not frequent rotation - the most likely explanation is the male host was simply on shift for this entire testing window, not a detection problem. Re-verify this specific end-to-end path (female detected → confirmed → classified → saved → visible in `casino_wallet_status_view`'s response → overlay moves in the browser) at a different time of day, when the female host is more likely on shift.

### Known limitations / next steps

- Detection is inherently intermittent, matching how this game's camera actually cycles (wide shots with the presenter visible vs. close-ups on just the wheel where nobody is on screen at all) — expect long stretches of "no face," which is correct behavior (nothing to blur), not a bug. The fixed `live_blur_region` fallback carries real weight in practice for exactly this reason.
- Not yet wired into any process supervisor / deployment config (Docker, systemd, etc.) — currently a manually-run command only.
- Only validated against one game (Lightning Storm) and one real false positive / one real male-host case. Constants (confidence floor, padding fractions, consecutive-tick count) are a first-pass heuristic, not a guarantee, and may need revisiting per game as more are enabled.
- No automated test coverage of the daemon's own async loop, browser lifecycle, or ML inference (out of scope for the unit test suite - these were validated via real, manual, live runs against the real game instead, documented above). `apps/casino/tests.py`'s `AiBlurTrackerGeometryTests` covers the pure padding/matching math; `LiveAiBlurTests` covers the Django-side heartbeat/fallback contract.

"""
Persistent daemon (not a Celery task - see docs/BLUR_DOCUMENTATION.md
Section 3 for why): watches every game currently flagged
ai_tracking_enabled=True that has at least one fresh CasinoLiveViewer,
running one headless-browser tab per such game, taking a screenshot every
tick, running face detection on it, and writing the resulting
{top, left, width, height} percentages to CasinoGame.detected_blur_region.

One watcher per actively-played GAME, not per player - every viewer of the
same live table sees the same broadcast, so one tab serves all of them.
A game's watcher starts the first time a fresh viewer row appears for it
and stops the moment none remain fresh - it never runs for a game nobody
currently has open.

Screenshots are never saved anywhere - each one is decoded, detected
against, and discarded; only the tiny derived region (four numbers) is
persisted. No video/image storage of any kind.

Run this as its own long-lived process (same idea as running `celery
worker` and `celery beat` as separate processes) - e.g.
`python manage.py run_ai_blur_tracker`. It is NOT started automatically by
runserver or by Celery; nothing in this project launches it for you yet.
"""
import asyncio
import io
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.casino.models import CasinoGame, CasinoLiveViewer
from apps.casino.services import AI_TRACKING_VIEWER_STALE_AFTER_SECONDS

POLL_INTERVAL_SECONDS = 2  # how often to check which games need a watcher
DETECT_INTERVAL_SECONDS = 1  # how often each active watcher screenshots+detects
# The tight face box from the detector is deliberately padded downward/
# outward to also cover upper-body/costume, per the client's own
# "chest part must be covered" instruction - but modestly, and NEVER by
# guessing at body proportions from the face box the way the thumbnail-
# based fixed boxes did (that's exactly what put a fixed box on top of a
# real wheel once - see docs/BLUR_DOCUMENTATION.md Section 2). This stays
# a fixed, conservative multiple of the detected face box itself.
PAD_LEFT_RIGHT_FRACTION = 0.35   # of face width, each side
PAD_TOP_FRACTION = 0.25          # of face height, above
PAD_BOTTOM_FRACTION = 1.6        # of face height, below (covers down to upper chest)

# Both caught live against a real game (Lightning Storm, 2026-09-10): the
# detector can match cartoon icon artwork on the wheel as a "face" with
# real confidence (0.73, not a marginal score) when no dealer is even in
# frame. Two independent defenses, since neither alone was reliable on its
# own in testing:
#  1. MIN_DETECTION_CONFIDENCE, set strictly between the observed false
#     positive (0.73) and true positive (0.88) from that same test run.
#  2. Temporal confirmation - a detection is only trusted once it recurs
#     in roughly the same spot on MIN_CONSECUTIVE_TICKS consecutive ticks.
#     This is NOT sufficient by itself during an idle/static camera shot
#     (a false positive on a stationary wheel icon repeats every tick too,
#     since the icon doesn't move) - it only helps alongside the
#     confidence floor, not instead of it.
# This is a heuristic tuned against one real game and one real false
# positive, not a guarantee - expect to revisit these constants as more
# games are added. See docs/BLUR_DOCUMENTATION.md Section 3.
MIN_DETECTION_CONFIDENCE = 0.80
MIN_CONSECUTIVE_TICKS = 2
# How close (as a fraction of frame width/height) two detections' centers
# must be to count as "the same" position for temporal confirmation.
POSITION_MATCH_TOLERANCE = 0.08

# The client's requirement is specifically "only girl" - a live table can
# rotate between male and female presenters (confirmed live, 2026-09-10:
# Lightning Storm's tracker correctly found and tracked a face that turned
# out to be a male host). Face detection alone has no gender concept, so a
# confirmed face only actually triggers a blur once ALSO classified female
# by this second, independent model (Levi & Hassner gender CNN, Caffe
# format - apps/casino/ai_models/gender_net.caffemodel). Runs only on
# already-confirmed detections, never on every tick, since it's a second
# real inference cost on top of face detection.
GENDER_MODEL_PROTOTXT = 'apps/casino/ai_models/gender_deploy.prototxt'
GENDER_MODEL_WEIGHTS = 'apps/casino/ai_models/gender_net.caffemodel'
GENDER_MODEL_MEAN_VALUES = (78.4263377603, 87.7689143744, 114.895847746)
GENDER_CROP_PADDING_FRACTION = 0.3  # of face width/height, each side - gives the classifier context beyond a tight face box

WATCHER_USERNAME_PREFIX = 'ai-blur-watcher'


class Command(BaseCommand):
    help = 'Persistent daemon that live-tracks a dealer\'s face on every actively-watched, AI-tracking-enabled game.'

    def handle(self, *args, **options):
        try:
            asyncio.run(self._run())
        except KeyboardInterrupt:
            self.stdout.write('Stopped.')

    async def _run(self):
        from asgiref.sync import sync_to_async

        detector = await sync_to_async(_build_detector)()
        gender_net = await sync_to_async(_build_gender_classifier)()
        watchers = {}

        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            self.stdout.write(self.style.SUCCESS('AI blur tracker started.'))
            try:
                while True:
                    active_game_ids = await self._get_active_game_ids()

                    for game_id in active_game_ids:
                        if game_id not in watchers:
                            game = await CasinoGame.objects.aget(pk=game_id)
                            watcher = GameWatcher(browser, game, detector, gender_net, self.stdout, self.style)
                            watchers[game_id] = watcher
                            asyncio.create_task(watcher.run())

                    for game_id in list(watchers):
                        if game_id not in active_game_ids:
                            watchers[game_id].stop_requested = True
                            del watchers[game_id]

                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
            finally:
                for watcher in watchers.values():
                    watcher.stop_requested = True
                await asyncio.sleep(0.5)
                await browser.close()

    async def _get_active_game_ids(self):
        cutoff = timezone.now() - timedelta(seconds=AI_TRACKING_VIEWER_STALE_AFTER_SECONDS)
        game_ids = set()
        async for game_id in CasinoLiveViewer.objects.filter(
            last_heartbeat__gte=cutoff, game__ai_tracking_enabled=True,
        ).values_list('game_id', flat=True).distinct():
            game_ids.add(game_id)
        return game_ids


def _build_detector():
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    # full_range, not short_range: short_range missed a real profile-angle
    # shot of the dealer entirely in testing (she's rarely dead-on frontal
    # to camera on a live table). full_range catches more real faces but
    # also more false positives on game artwork - that tradeoff is why
    # MIN_DETECTION_CONFIDENCE and temporal confirmation exist above.
    base_options = python.BaseOptions(
        model_asset_path='apps/casino/ai_models/blaze_face_full_range.tflite',
    )
    options = vision.FaceDetectorOptions(base_options=base_options, min_detection_confidence=MIN_DETECTION_CONFIDENCE)
    return vision.FaceDetector.create_from_options(options)


def _build_gender_classifier():
    """
    Levi & Hassner's gender CNN (Caffe format) via OpenCV's dnn module.
    Requires opencv-contrib-python pinned to a version that still supports
    Caffe (4.10.0.84 - OpenCV 5.x removed the Caffe importer entirely,
    confirmed live 2026-09-10: "Caffe importer has been removed. Please
    use ONNX-converted models or use an older OpenCV version."). No
    version constraint from mediapipe's own requirements, so this pin is
    safe - verified mediapipe's face detector still works correctly on
    this older OpenCV build too.
    """
    import cv2
    return cv2.dnn.readNetFromCaffe(GENDER_MODEL_PROTOTXT, GENDER_MODEL_WEIGHTS)


class GameWatcher:
    """One headless-browser tab + detection loop for one actively-watched game."""

    def __init__(self, browser, game: CasinoGame, detector, gender_net, stdout, style):
        self.browser = browser
        self.game = game
        self.detector = detector
        self.gender_net = gender_net
        self.stdout = stdout
        self.style = style
        self.stop_requested = False
        self.page = None
        self._pending_center = None  # (cx_pct, cy_pct) from the last tick's detection, awaiting confirmation
        self._pending_streak = 0

    async def run(self):
        from asgiref.sync import sync_to_async

        try:
            launch_url = await sync_to_async(_get_watcher_launch_url)(self.game)
        except Exception as exc:  # noqa: BLE001 - a launch failure just means this game never gets tracked this tick
            self.stdout.write(self.style.ERROR(f'[{self.game.name}] could not open a watch session: {exc}'))
            return

        context = await self.browser.new_context(viewport={'width': 1280, 'height': 900})
        self.page = await context.new_page()
        try:
            await self.page.goto(launch_url, timeout=30000)
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f'[{self.game.name}] failed to load: {exc}'))
            await context.close()
            return

        self.stdout.write(self.style.SUCCESS(f'[{self.game.name}] watcher started.'))
        try:
            while not self.stop_requested:
                await self._tick()
                await asyncio.sleep(DETECT_INTERVAL_SECONDS)
        finally:
            await context.close()
            await sync_to_async(_clear_detected_region)(self.game.id)
            self.stdout.write(f'[{self.game.name}] watcher stopped.')

    async def _tick(self):
        from asgiref.sync import sync_to_async

        try:
            png_bytes = await self.page.screenshot()
        except Exception as exc:  # noqa: BLE001 - page may have navigated/closed mid-shot
            self.stdout.write(self.style.WARNING(f'[{self.game.name}] screenshot failed: {exc}'))
            return

        box = await sync_to_async(_detect_raw_face_box)(self.detector, png_bytes)
        if box is None:
            self._pending_center = None
            self._pending_streak = 0
            return

        center = (box['cx'], box['cy'])
        if self._pending_center is not None and _centers_close(center, self._pending_center):
            self._pending_streak += 1
        else:
            self._pending_streak = 1
        self._pending_center = center

        if self._pending_streak >= MIN_CONSECUTIVE_TICKS:
            # Gender check only runs on an already-confirmed detection, not
            # every tick - it's a second real inference cost, and "only
            # blur female" is the whole point (a live table can rotate
            # between male and female presenters - confirmed live,
            # 2026-09-10: this exact game correctly tracked a face that
            # turned out to be a male host, and got skipped here).
            gender = await sync_to_async(_classify_gender)(self.gender_net, png_bytes, box)
            if gender == 'Female':
                region = _pad_region(box)
                await sync_to_async(_save_detected_region)(self.game.id, region)
                self.stdout.write(f'[{self.game.name}] blurring (female host confirmed).')
            else:
                self.stdout.write(f'[{self.game.name}] face confirmed but male - not blurred.')


def _get_watcher_launch_url(game: CasinoGame) -> str:
    """
    A real launch, same as any player would get - zero financial cost for
    a seamless-wallet provider (Waija never touches the wallet at launch,
    only on an actual bet - see apps.casino.services.launch_game). Uses a
    dedicated internal watcher account, never a real player's.
    """
    from apps.accounts.models import User
    from apps.casino.services import launch_game

    watcher_user, _ = User.objects.get_or_create(
        email=f'{WATCHER_USERNAME_PREFIX}@internal.quickpaybet.com',
        defaults={'is_active': True},
    )
    _, launch_url = launch_game(
        watcher_user, game, None,
        return_url='https://quickpaybet.com/', callback_url='https://quickpaybet.com/',
    )
    return launch_url


def _detect_raw_face_box(detector, png_bytes: bytes) -> dict | None:
    """
    Returns the single best raw detection as percentages of the frame
    (center + box, all 0-100), or None. Does NOT apply padding and does
    NOT decide whether to trust it - see GameWatcher._tick for the
    temporal-confirmation gate this feeds into.
    """
    import mediapipe as mp
    from PIL import Image

    image = Image.open(io.BytesIO(png_bytes)).convert('RGB')
    width, height = image.size
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=_pil_to_numpy(image))
    result = detector.detect(mp_image)
    if not result.detections:
        return None

    # The largest detected face - a live dealer table sometimes shows
    # player avatars/chat icons too; the dealer herself is reliably the
    # largest face in frame.
    best = max(result.detections, key=lambda d: d.bounding_box.width * d.bounding_box.height)
    bb = best.bounding_box

    return {
        'cx': (bb.origin_x + bb.width / 2) / width * 100,
        'cy': (bb.origin_y + bb.height / 2) / height * 100,
        'left': bb.origin_x / width * 100,
        'top': bb.origin_y / height * 100,
        'width': bb.width / width * 100,
        'height': bb.height / height * 100,
    }


def _classify_gender(gender_net, png_bytes: bytes, box_pct: dict) -> str:
    """
    Crops the confirmed face box (box_pct - percentages, same shape as
    _detect_raw_face_box's return) out of the same frame the detector saw,
    pads it a bit for context, and runs it through the gender CNN.
    Verified live, 2026-09-10, against real Lightning Storm frames: 100%
    correct with high confidence on both a known male host (M=1.00) and
    the known female host from the game's own promotional photo (F=1.00).
    """
    import cv2
    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(png_bytes)).convert('RGB')
    width, height = image.size
    np_rgb = np.array(image)

    x = int(box_pct['left'] / 100 * width)
    y = int(box_pct['top'] / 100 * height)
    w = int(box_pct['width'] / 100 * width)
    h = int(box_pct['height'] / 100 * height)
    pad_x, pad_y = int(w * GENDER_CROP_PADDING_FRACTION), int(h * GENDER_CROP_PADDING_FRACTION)
    x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
    x1, y1 = min(width, x + w + pad_x), min(height, y + h + pad_y)

    face_bgr = cv2.cvtColor(np_rgb[y0:y1, x0:x1], cv2.COLOR_RGB2BGR)
    blob = cv2.dnn.blobFromImage(face_bgr, 1.0, (227, 227), GENDER_MODEL_MEAN_VALUES, swapRB=False)
    gender_net.setInput(blob)
    preds = gender_net.forward()
    return ('Male', 'Female')[preds[0].argmax()]


def _centers_close(a: tuple, b: tuple) -> bool:
    return abs(a[0] - b[0]) <= POSITION_MATCH_TOLERANCE * 100 and abs(a[1] - b[1]) <= POSITION_MATCH_TOLERANCE * 100


def _pad_region(box: dict) -> dict:
    """Applies the upper-body padding (see PAD_* constants) to a confirmed raw detection, in percentage space."""
    pad_x = box['width'] * PAD_LEFT_RIGHT_FRACTION
    pad_top = box['height'] * PAD_TOP_FRACTION
    pad_bottom = box['height'] * PAD_BOTTOM_FRACTION

    left = max(0.0, box['left'] - pad_x)
    top = max(0.0, box['top'] - pad_top)
    right = min(100.0, box['left'] + box['width'] + pad_x)
    bottom = min(100.0, box['top'] + box['height'] + pad_bottom)

    return {
        'top': round(top, 2),
        'left': round(left, 2),
        'width': round(right - left, 2),
        'height': round(bottom - top, 2),
    }


def _pil_to_numpy(image):
    import numpy as np
    return np.array(image)


def _save_detected_region(game_id: int, region: dict) -> None:
    CasinoGame.objects.filter(pk=game_id).update(
        detected_blur_region=region, detected_blur_region_updated_at=timezone.now(),
    )


def _clear_detected_region(game_id: int) -> None:
    CasinoGame.objects.filter(pk=game_id).update(detected_blur_region=None, detected_blur_region_updated_at=None)

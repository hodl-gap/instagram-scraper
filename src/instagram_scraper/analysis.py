"""Non-VLM video-content analysis for scraped reels (the optional `--analyze` flag).

Runs entirely WITHOUT any vision-language model. Given a downloaded reel mp4, it
produces four best-effort blocks:

  * shots          -> PySceneDetect : shot cuts + count + mean brightness
  * audio          -> faster-whisper: spoken-word transcript + detected language
  * people         -> Ultralytics YOLO: person count + per-track screen time
  * on_screen_text -> RapidOCR (classical OCR, NOT a VLM): burned-in text

What this CANNOT do (needs a VLM, out of scope): what people are *doing*,
clothing/appearance, mood/narrative. See README.

These deps are heavy and live in the optional `analysis` extra
(`pip install -e ".[analysis]"`). Each stage lazy-imports its dependency and
degrades independently — a missing or failing stage leaves an `{"error": ...}`
note instead of crashing the run.
"""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger("instagram_scraper")

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")


def download_video(url: str, dest: Path) -> Path:
    """Download a reel mp4 from its (signed) CDN URL to `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=90) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


# --- stage: shots + brightness (PySceneDetect + OpenCV) ---------------------

_BRIGHTNESS_LABELS = ((20, "dark"), (40, "dim"), (65, "moderate"), (101, "bright"))


def _brightness(path: Path) -> tuple:
    import cv2  # type: ignore

    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    vals = []
    for i in range(0, max(total, 1), max(total // 8, 1)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        vals.append(float(gray.mean()))
    cap.release()
    if not vals:
        return None, None
    pct = round(sum(vals) / len(vals) / 255 * 100, 1)
    label = next((lbl for thr, lbl in _BRIGHTNESS_LABELS if pct < thr), "bright")
    return pct, label


def _shots(path: Path) -> dict:
    from scenedetect import detect, ContentDetector  # type: ignore

    scenes = detect(str(path), ContentDetector())
    shots = [
        {"index": i, "start_sec": round(s.get_seconds(), 2), "end_sec": round(e.get_seconds(), 2)}
        for i, (s, e) in enumerate(scenes)
    ]
    pct, label = _brightness(path)
    return {"shot_count": len(shots) or 1, "shots": shots,
            "brightness_pct": pct, "brightness_label": label}


# --- stage: audio transcript (faster-whisper) -------------------------------

def _audio(path: Path, model_size: str = "small") -> dict:
    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(path))
    transcript = [
        {"start_sec": round(s.start, 2), "end_sec": round(s.end, 2), "text": s.text.strip()}
        for s in segments
    ]
    return {"language": info.language, "transcript": transcript}


# --- stage: people presence (Ultralytics YOLO) ------------------------------

def _people(path: Path, model_path: str = "yolo11n.pt", min_track_sec: float = 0.3) -> dict:
    from ultralytics import YOLO  # type: ignore
    import cv2  # type: ignore

    fps = cv2.VideoCapture(str(path)).get(cv2.CAP_PROP_FPS) or 30.0
    model = YOLO(model_path)
    seen: dict = {}
    for fi, res in enumerate(model.track(str(path), persist=True, classes=[0], stream=True, verbose=False)):
        ids = getattr(res.boxes, "id", None)
        if ids is None:
            continue
        for tid in ids.int().tolist():
            t = seen.setdefault(tid, {"first": fi, "last": fi, "frames": 0})
            t["last"] = fi
            t["frames"] += 1
    tracks = []
    for tid, t in seen.items():
        secs = round((t["last"] - t["first"] + 1) / fps, 2)
        if secs >= min_track_sec:
            tracks.append({"track_id": int(tid), "screen_time_sec": secs,
                           "first_seen_sec": round(t["first"] / fps, 2)})
    tracks.sort(key=lambda x: -x["screen_time_sec"])
    return {"person_count": len(tracks), "tracks": tracks}


# --- stage: on-screen text (RapidOCR — classical, non-VLM) -------------------

def _ocr(path: Path, max_frames: int = 8) -> dict:
    from rapidocr_onnxruntime import RapidOCR  # type: ignore
    import cv2  # type: ignore

    engine = RapidOCR()
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(total // max_frames, 1) if total else 1
    spans = []
    seen = set()
    for i in range(0, max(total, 1), step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, frame = cap.read()
        if not ok:
            continue
        result, _ = engine(frame)
        for _box, text, _conf in (result or []):
            t = (text or "").strip()
            if t and t not in seen:
                seen.add(t)
                spans.append({"timestamp_sec": round(i / fps, 2), "text": t})
    cap.release()
    return {"on_screen_text": spans}


# --- orchestration ----------------------------------------------------------

_STAGES = {"shots": _shots, "audio": _audio, "people": _people, "on_screen_text": _ocr}


def analyze_video(path: Path, stages: Optional[list] = None, **stage_opts) -> dict:
    """Run the requested non-VLM stages on a local mp4. Each stage is best-effort."""
    out: dict = {}
    for name in (stages or list(_STAGES)):
        fn = _STAGES[name]
        try:
            if name == "audio" and "whisper_model" in stage_opts:
                out[name] = fn(path, stage_opts["whisper_model"])
            elif name == "people" and "yolo_model" in stage_opts:
                out[name] = fn(path, stage_opts["yolo_model"])
            else:
                out[name] = fn(path)
        except ImportError as e:
            out[name] = {"error": f"missing dependency for '{name}': {e}. Install with pip install -e '.[analysis]'"}
            logger.warning("Analysis stage '%s' skipped — %s", name, e)
        except Exception as e:  # noqa: BLE001 - one bad stage shouldn't sink the others
            out[name] = {"error": f"{type(e).__name__}: {e}"}
            logger.warning("Analysis stage '%s' failed — %s", name, type(e).__name__)
    return out


def analyze_post(post: dict, work_dir: Path, stages: Optional[list] = None, **stage_opts) -> dict:
    """Download a post's video and run non-VLM analysis on it. Best-effort."""
    url = (post.get("video_urls") or [None])[0]
    if not url:
        return {"skipped": "no video to analyze (photo post or not hydrated)"}
    dest = Path(work_dir) / f"{post['id']}.mp4"
    try:
        download_video(url, dest)
    except Exception as e:  # noqa: BLE001
        return {"error": f"video download failed: {type(e).__name__}: {e}"}
    return analyze_video(dest, stages=stages, **stage_opts)

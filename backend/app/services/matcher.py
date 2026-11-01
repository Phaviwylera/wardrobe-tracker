"""Decides whether a candidate listing shows *the same garment* as the shopper's photo.

Signals (all computed on the garment region, background stripped):
  hash   – perceptual + difference hash; ~1.0 when it is literally the same product photo
  orb    – ORB keypoints + RANSAC homography inliers; catches the same print / embroidery /
           cut photographed differently (different crop, model, angle)
  colour – hue/saturation histogram correlation; separates colour variants of one design
  clip   – optional CLIP embedding cosine (set ENABLE_CLIP=1 and install open_clip-torch)
plus a prior from where it was found (Lens "exact matches" tab > products > visual).

Colour variants are deliberately capped below "exact": a red and a blue version of the
same kurta are the same design but not the item the shopper photographed.
"""
from __future__ import annotations

import io
import logging
import math
import os
from dataclasses import dataclass

import cv2
import imagehash
import numpy as np
from PIL import Image, ImageOps

log = logging.getLogger(__name__)

CHANNEL_PRIOR = {
    "lens:exact_matches": 0.14,
    "lens:products": 0.07,
    "lens:visual_matches": 0.03,
    "shopping": 0.0,
    "organic": 0.0,
}


@dataclass
class Features:
    phash: imagehash.ImageHash
    dhash: imagehash.ImageHash
    hist: np.ndarray
    kp_desc: np.ndarray | None
    kp_pts: np.ndarray | None
    clip: np.ndarray | None
    fg_ratio: float


def load_image(data: bytes, max_side: int = 640) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_side, max_side))
    return img


def normalise_upload(data: bytes, max_side: int = 1280) -> tuple[bytes, int, int]:
    """Re-encode any upload (HEIC excluded) as a clean JPEG for storage and Lens."""
    img = load_image(data, max_side)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue(), img.width, img.height


def crop_image(data: bytes, box: tuple[float, float, float, float]) -> bytes:
    """box = (x, y, w, h) as fractions of the image."""
    img = load_image(data, 4000)
    x, y, w, h = box
    W, H = img.size
    l, t = max(0, int(x * W)), max(0, int(y * H))
    r, b = min(W, int((x + w) * W)), min(H, int((y + h) * H))
    if r - l < 32 or b - t < 32:
        return data
    out = io.BytesIO()
    img.crop((l, t, r, b)).save(out, "JPEG", quality=92)
    return out.getvalue()


def _foreground(arr: np.ndarray) -> np.ndarray:
    """Mask of pixels that differ from the border colour (studio backgrounds) and aren't near-white."""
    h, w = arr.shape[:2]
    border = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]]).astype(np.float32)
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(arr.astype(np.float32) - bg, axis=2)
    mask = (dist > 28).astype(np.uint8)
    near_white = (arr.min(axis=2) > 235).astype(np.uint8)
    mask = mask & (1 - near_white)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    if mask.mean() < 0.08:  # background removal failed (busy photo) – use the centre region
        mask = np.zeros((h, w), np.uint8)
        mask[h // 8: h - h // 8, w // 6: w - w // 6] = 1
    return mask


_orb = cv2.ORB_create(nfeatures=1500)
_bf = cv2.BFMatcher(cv2.NORM_HAMMING)


class _Clip:
    model = None
    preprocess = None
    tried = False

    @classmethod
    def embed(cls, img: Image.Image) -> np.ndarray | None:
        if os.getenv("ENABLE_CLIP", "0") != "1":
            return None
        if not cls.tried:
            cls.tried = True
            try:
                import open_clip  # type: ignore
                import torch  # type: ignore  # noqa: F401
                cls.model, _, cls.preprocess = open_clip.create_model_and_transforms(
                    os.getenv("CLIP_MODEL", "ViT-B-32"), pretrained=os.getenv("CLIP_WEIGHTS", "laion2b_s34b_b79k"))
                cls.model.eval()
            except Exception as e:  # pragma: no cover - optional dependency
                log.warning("CLIP disabled: %s", e)
                cls.model = None
        if cls.model is None:
            return None
        import torch  # type: ignore
        with torch.no_grad():
            v = cls.model.encode_image(cls.preprocess(img).unsqueeze(0))[0].numpy()
        return v / (np.linalg.norm(v) + 1e-9)


def features(data: bytes) -> Features:
    img = load_image(data)
    arr = np.asarray(img)
    mask = _foreground(arr)
    ys, xs = np.nonzero(mask)
    crop = img.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)) if len(xs) else img

    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1], mask, [30, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist)

    gray = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2GRAY)
    kps, desc = _orb.detectAndCompute(gray, None)
    pts = np.float32([k.pt for k in kps]) if kps else None

    return Features(
        phash=imagehash.phash(crop, hash_size=16),
        dhash=imagehash.dhash(crop, hash_size=16),
        hist=hist,
        kp_desc=desc,
        kp_pts=pts,
        clip=_Clip.embed(crop),
        fg_ratio=float(mask.mean()),
    )


def _orb_inliers(a: Features, b: Features) -> int:
    if a.kp_desc is None or b.kp_desc is None or len(a.kp_desc) < 10 or len(b.kp_desc) < 10:
        return 0
    pairs = _bf.knnMatch(a.kp_desc, b.kp_desc, k=2)
    good = [m for m, *rest in pairs if rest and m.distance < 0.75 * rest[0].distance]
    if len(good) < 8:
        return 0
    src = np.float32([a.kp_pts[m.queryIdx] for m in good]).reshape(-1, 1, 2)
    dst = np.float32([b.kp_pts[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    _, inl = cv2.findHomography(src, dst, cv2.RANSAC, 6.0)
    return int(inl.sum()) if inl is not None else 0


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class MatchScore:
    score: float
    level: str
    signals: dict


def compare(q: Features, c: Features | None, channel: str, exact_t: float = 0.80,
            close_t: float = 0.62) -> MatchScore:
    prior = CHANNEL_PRIOR.get(channel, 0.0)
    if c is None:
        # No candidate image could be fetched – rely on how it was discovered.
        score = 0.55 + prior * 1.5 if channel == "lens:exact_matches" else 0.30 + prior
        return MatchScore(round(score, 3), _level(score, exact_t, close_t),
                          {"verified": False, "prior": prior})

    hash_sim = 1 - float((q.phash - c.phash) + (q.dhash - c.dhash)) / (2 * 256)
    h = _clip01((hash_sim - 0.58) / 0.32)
    colour_corr = float(cv2.compareHist(q.hist, c.hist, cv2.HISTCMP_CORREL))
    col = _clip01((colour_corr - 0.15) / 0.70)
    inliers = _orb_inliers(q, c)
    orb = 1 - math.exp(-(inliers - 12) / 25) if inliers >= 20 else 0.0
    clip = None
    if q.clip is not None and c.clip is not None:
        clip = _clip01((float(q.clip @ c.clip) - 0.70) / 0.25)

    same = max(h, orb)
    if clip is not None:
        visual = 0.35 * clip + 0.32 * same + 0.15 * col + 0.18 * orb
    else:
        visual = 0.55 * same + 0.20 * col + 0.25 * orb
    score = visual + prior
    if h >= 0.85 or orb >= 0.80:          # identical photo, or strong geometric match
        score = max(score, 0.88 if col >= 0.35 else score)
    if col < 0.25 and score >= exact_t:    # same design, different colour way
        score = exact_t - 0.06
    score = _clip01(score)
    return MatchScore(round(score, 3), _level(score, exact_t, close_t), {
        "verified": True, "hash": round(h, 3), "orb_inliers": inliers, "orb": round(orb, 3),
        "colour": round(col, 3), "clip": None if clip is None else round(clip, 3), "prior": prior,
    })


def _level(score: float, exact_t: float, close_t: float) -> str:
    if score >= exact_t:
        return "exact"
    if score >= close_t:
        return "close"
    return "similar"

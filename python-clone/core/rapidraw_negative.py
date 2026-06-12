from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Globals (mirror the consts at the top of src-tauri/src/negative_conversion.rs)
# ---------------------------------------------------------------------------

# Pseudo-log post-gamma (psl mode): applied to the normalized stretch result.
PSL_GAMMA = np.float32(1.6)

# Auto black/white point percentiles, as fractions of the sorted samples.
# 0.05 -> 5th percentile (low end -> black point base);
# 0.995 -> 99.5th percentile (high end -> white point base).
# (Rust ships these; the older 0.001 / 0.999 pair is kept commented there.)
PCT_LOW = np.float32(0.05)
PCT_HIGH = np.float32(0.995)

# Fraction of width/height trimmed from EACH side before percentile analysis.
# 0.12 -> central 76% region. Raise to ignore more border (film edges/sprockets).
CENTER_MARGIN = np.float32(0.12)

# ROI subsample budget (matches Rust's est_pixels / 40_000 column step).
SAMPLE_TARGET = 40_000

# Only every Nth row is sampled during analysis (matches Rust's step_by(3)).
SAMPLE_ROW_STRIDE = 3

# Half-size of the patch averaged when picking a B/W point. 2 -> a 5x5 patch
# ((2*r+1)^2 px); 0 -> a single pixel. Matches Rust's sample_negative_point radius.
SAMPLE_PATCH_RADIUS = 2

# Longest-edge size of the interactive proxy (image "B"). The full-res original is
# downscaled to fit this box once on load; ALL preview conversion and point sampling
# run on the proxy, and the save path analyzes a reference of this size before
# processing full-res. RapidRAW hardcodes 1080; raise for a sharper/bigger proxy at
# the cost of slower preview recomputes. (No lag observed at 1080 on an M-series Mac.)
PREVIEW_MAX_DIM = 1080

# Print-grade sigmoid (exposure/contrast) shape constants.
# k  = SIGMOID_K_SCALE * contrast        (steepness)
# x0 = SIGMOID_X0_BASE - exposure * SIGMOID_EXPOSURE_SCALE  (horizontal shift)
SIGMOID_X0_BASE = np.float32(0.6)
SIGMOID_EXPOSURE_SCALE = np.float32(0.25)
SIGMOID_K_SCALE = np.float32(4.0)

# Highlight desaturation: blend toward luma once the max channel exceeds the
# threshold. reduction = (clip((max - THRESH) * SLOPE, 0, 1))^2.
DESAT_THRESHOLD = np.float32(0.9)
DESAT_SLOPE = np.float32(10.0)

# Rec.709 luma weights (R, G, B), used by the highlight desaturation.
LUMA_R = np.float32(0.2126)
LUMA_G = np.float32(0.7152)
LUMA_B = np.float32(0.0722)

EPS = np.float32(1e-6)

# Conversion modes = which working space the invert+stretch happens in.
# See DOCS_MOD/RapidRaw-negative-conversion-modes.md.
MODE_LIN = "lin"  # linear inversion:  work = 1 - v
MODE_PSL = "psl"  # pseudo-log:        work = 1 - v, then work^PSL_GAMMA after stretch
MODE_LOG = "log"  # optical density:   work = -log10(v)   (default)


@dataclass(frozen=True)
class NegativeConversionParams:
    red_weight: float = 1.0
    green_weight: float = 1.0
    blue_weight: float = 1.0
    exposure: float = 0.0
    contrast: float = 1.0
    gamma: float = 2.2
    mode: str = MODE_LOG
    # Per-channel overrides in the CURRENT mode's working space (None = auto
    # percentile). Tweaks slide each endpoint along the black->white axis.
    bp_override: tuple | None = None
    wp_override: tuple | None = None
    bp_tweak: float = 0.0
    wp_tweak: float = 0.0


@dataclass(frozen=True)
class ChannelBounds:
    min: float
    max: float


DEFAULT_PARAMS = NegativeConversionParams()


# ---------------------------------------------------------------------------
# Working-space transforms (per channel) — mode dependent
# ---------------------------------------------------------------------------

def to_working(v: np.ndarray, mode: str) -> np.ndarray:
    """Scanned-negative pixel value(s) -> working space."""
    v = np.asarray(v, dtype=np.float32)
    if mode == MODE_LOG:
        return -np.log10(np.clip(v, EPS, np.float32(1.0)))
    return np.float32(1.0) - np.clip(v, np.float32(0.0), np.float32(1.0))  # lin + psl


def from_working(d: float, mode: str) -> float:
    """Working-space value -> negative pixel value (for the [0,255] readout)."""
    if mode == MODE_LOG:
        return float(10.0 ** (-d))
    return float(1.0 - d)


def ensure_rgb_float01(img: np.ndarray) -> np.ndarray:
    arr = np.asarray(img)
    if arr.ndim == 2:
        arr = np.repeat(arr[:, :, None], 3, axis=2)
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError("Expected an RGB image with shape (H, W, 3)")
    arr = arr[:, :, :3].astype(np.float32, copy=False)
    return np.clip(arr, np.float32(0.0), np.float32(1.0))


def downscale_rgb(img: np.ndarray, max_width: int = PREVIEW_MAX_DIM, max_height: int = PREVIEW_MAX_DIM) -> np.ndarray:
    """Constrain to 1080x1080 like RapidRAW's preview/reference."""
    rgb = ensure_rgb_float01(img)
    h, w = rgb.shape[:2]
    if w <= max_width and h <= max_height:
        return rgb.copy()
    scale = min(max_width / float(w), max_height / float(h))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA).astype(np.float32)


# ---------------------------------------------------------------------------
# Auto analysis (percentiles in working space)
# ---------------------------------------------------------------------------

def analyze_bounds(img: np.ndarray, mode: str) -> tuple:
    """
    Reproduce analyze_bounds() from negative_conversion.rs: trim CENTER_MARGIN
    from each side, sample every 3rd row (and every Nth column), then take direct
    sorted indices at PCT_LOW / PCT_HIGH per channel.
    """
    work = to_working(ensure_rgb_float01(img), mode)
    height, width = work.shape[:2]

    margin_x = int(width * float(CENTER_MARGIN))
    margin_y = int(height * float(CENTER_MARGIN))

    est_pixels = max(0, width - margin_x * 2) * max(0, height - margin_y * 2)
    step = max(est_pixels // SAMPLE_TARGET, 1)

    roi = work[margin_y:height - margin_y:SAMPLE_ROW_STRIDE, margin_x:width - margin_x:step, :]

    def get_bounds(channel_values: np.ndarray) -> ChannelBounds:
        vals = channel_values[np.isfinite(channel_values)].astype(np.float32, copy=False).reshape(-1)
        if vals.size == 0:
            return ChannelBounds(0.0, 1.0)
        vals.sort()
        length = float(vals.size)
        min_idx = min(int(length * float(PCT_LOW)), vals.size - 1)
        max_idx = min(int(length * float(PCT_HIGH)), vals.size - 1)
        mn = float(vals[min_idx])
        mx = float(vals[max_idx])
        if mx <= mn + 0.0001:
            mx = mn + 1.0
        return ChannelBounds(mn, mx)

    return tuple(get_bounds(roi[:, :, c]) for c in range(3))


# ---------------------------------------------------------------------------
# Black/white point resolution + readout
# ---------------------------------------------------------------------------

def resolve_points(bounds: tuple, params: NegativeConversionParams) -> tuple:
    """
    Effective per-channel (bp, wp) the pipeline uses: start from a click override
    or the auto bounds, then slide each endpoint along the black->white axis by
    tweak * range. Direction of the axis is preserved.
    """
    bp_base = np.array([b.min for b in bounds], dtype=np.float32)
    wp_base = np.array([b.max for b in bounds], dtype=np.float32)
    if params.bp_override is not None:
        bp_base = np.array(params.bp_override, dtype=np.float32)
    if params.wp_override is not None:
        wp_base = np.array(params.wp_override, dtype=np.float32)

    rng = np.maximum(wp_base - bp_base, EPS)
    bp = bp_base + np.float32(params.bp_tweak) * rng
    wp = wp_base + np.float32(params.wp_tweak) * rng
    return bp, wp


def points_to_display_255(bp: np.ndarray, wp: np.ndarray, mode: str) -> tuple:
    """Density-space endpoints -> negative pixel values in [0,255], (black, white)."""
    def to_255(d):
        v = from_working(float(d), mode)
        return int(np.clip(round(v * 255.0), 0, 255))

    black = [to_255(bp[c]) for c in range(3)]  # bp (thin film) -> output BLACK
    white = [to_255(wp[c]) for c in range(3)]  # wp (dense film) -> output WHITE
    return black, white


# ---------------------------------------------------------------------------
# Core pipeline (given bounds) — mirrors run_pipeline() in the Rust module
# ---------------------------------------------------------------------------

def run_pipeline(img: np.ndarray, params: NegativeConversionParams, bounds: tuple) -> np.ndarray:
    rgb = ensure_rgb_float01(img)
    work = to_working(rgb, params.mode)

    bp, wp = resolve_points(bounds, params)
    mins = bp.reshape(1, 1, 3)
    maxs = wp.reshape(1, 1, 3)
    weights = np.array(
        [params.red_weight, params.green_weight, params.blue_weight], dtype=np.float32
    ).reshape(1, 1, 3)

    normalized = (work - mins) / np.maximum(maxs - mins, EPS)
    normalized = np.maximum(normalized, np.float32(0.0))

    if params.mode == MODE_PSL:
        normalized = np.power(normalized, PSL_GAMMA)

    normalized = normalized * weights

    k = SIGMOID_K_SCALE * np.float32(max(float(params.contrast), 0.1))
    x0 = SIGMOID_X0_BASE - np.float32(params.exposure) * SIGMOID_EXPOSURE_SCALE
    y0 = np.float32(1.0) / (np.float32(1.0) + np.exp(k * x0))
    y1 = np.float32(1.0) / (np.float32(1.0) + np.exp(-k * (np.float32(1.0) - x0)))
    scale = np.float32(1.0) / np.maximum(y1 - y0, EPS)

    sigmoid = np.float32(1.0) / (np.float32(1.0) + np.exp(-k * (normalized - x0)))
    out = np.clip((sigmoid - y0) * scale, np.float32(0.0), np.float32(1.0))

    luma = LUMA_R * out[:, :, 0] + LUMA_G * out[:, :, 1] + LUMA_B * out[:, :, 2]
    max_ch = np.max(out, axis=2)
    overflow = np.clip((max_ch - DESAT_THRESHOLD) * DESAT_SLOPE, np.float32(0.0), np.float32(1.0))
    sat_reduction = overflow * overflow
    out = out + (luma[:, :, None] - out) * sat_reduction[:, :, None]

    gamma_inv = np.float32(1.0 / max(float(params.gamma), 0.01))
    return np.power(np.clip(out, np.float32(0.0), np.float32(1.0)), gamma_inv).astype(np.float32)


# ---------------------------------------------------------------------------
# Convenience entry points
# ---------------------------------------------------------------------------

def convert_negative_preview(img: np.ndarray, params: NegativeConversionParams = DEFAULT_PARAMS) -> dict:
    """
    Preview path: compute auto bounds on the (already downscaled) image, run the
    pipeline, and report the effective [0,255] points + the analysis margin.
    Mirrors preview_negative_conversion() in the Rust module.
    """
    bounds = analyze_bounds(img, params.mode)
    result = run_pipeline(img, params, bounds)
    bp, wp = resolve_points(bounds, params)
    black, white = points_to_display_255(bp, wp, params.mode)
    return {
        "image": result,
        "black_point": black,
        "white_point": white,
        "center_margin": float(CENTER_MARGIN),
    }


def convert_negative(img: np.ndarray, params: NegativeConversionParams = DEFAULT_PARAMS,
                     bounds: tuple | None = None) -> np.ndarray:
    if bounds is None:
        bounds = analyze_bounds(img, params.mode)
    return run_pipeline(img, params, bounds)


def convert_negative_like_rapidraw_save(img: np.ndarray, params: NegativeConversionParams = DEFAULT_PARAMS,
                                        reference_max_dim: int = PREVIEW_MAX_DIM) -> np.ndarray:
    """Save path: analyze a 1080px reference, then process the full-res image."""
    reference = downscale_rgb(img, reference_max_dim, reference_max_dim)
    bounds = analyze_bounds(reference, params.mode)
    return run_pipeline(img, params, bounds)


def sample_point(img: np.ndarray, x: float, y: float, mode: str, radius: int = SAMPLE_PATCH_RADIUS) -> tuple:
    """
    Sample a small patch around a normalized (x, y) click and return its
    per-channel value in the working space — ready for bp_override / wp_override.
    Mirrors sample_negative_point() in the Rust module.
    """
    rgb = ensure_rgb_float01(img)
    h, w = rgb.shape[:2]
    if w == 0 or h == 0:
        raise ValueError("Empty image")
    cx = int(round(float(np.clip(x, 0.0, 1.0)) * (w - 1)))
    cy = int(round(float(np.clip(y, 0.0, 1.0)) * (h - 1)))
    x0 = max(0, cx - radius)
    x1 = min(w, cx + radius + 1)
    y0 = max(0, cy - radius)
    y1 = min(h, cy + radius + 1)
    patch = rgb[y0:y1, x0:x1, :]
    avg = patch.reshape(-1, 3).mean(axis=0)
    dens = to_working(avg, mode)
    return tuple(float(d) for d in dens)

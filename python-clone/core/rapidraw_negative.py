from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


DEFAULT_CENTRAL_REGION_PERCENT = 76.0
DEFAULT_LOW_PERCENTILE = 0.1
DEFAULT_HIGH_PERCENTILE = 99.9
EPS = np.float32(1e-6)


@dataclass(frozen=True)
class NegativeConversionParams:
    central_region_percent: float = DEFAULT_CENTRAL_REGION_PERCENT
    low_percentile: float = DEFAULT_LOW_PERCENTILE
    high_percentile: float = DEFAULT_HIGH_PERCENTILE
    red_weight: float = 1.0
    green_weight: float = 1.0
    blue_weight: float = 1.0
    exposure: float = 0.0
    contrast: float = 1.0


@dataclass(frozen=True)
class ChannelBounds:
    min: float
    max: float


DEFAULT_PARAMS = NegativeConversionParams()


def ensure_rgb_float01(img: np.ndarray) -> np.ndarray:
    """Return an RGB float32 image clipped to [0, 1]."""
    arr = np.asarray(img)
    if arr.ndim == 2:
        arr = np.repeat(arr[:, :, None], 3, axis=2)
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError("Expected an RGB image with shape (H, W, 3)")
    arr = arr[:, :, :3].astype(np.float32, copy=False)
    return np.clip(arr, np.float32(0.0), np.float32(1.0))


def downscale_rgb(img: np.ndarray, max_width: int = 1080, max_height: int = 1080) -> np.ndarray:
    """Match RapidRAW's preview/reference behavior: constrain image to 1080x1080."""
    rgb = ensure_rgb_float01(img)
    h, w = rgb.shape[:2]
    if w <= max_width and h <= max_height:
        return rgb.copy()

    scale = min(max_width / float(w), max_height / float(h))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA).astype(np.float32)


def analyze_bounds_from_log_density(
    log_density: np.ndarray,
    central_region_percent: float = DEFAULT_CENTRAL_REGION_PERCENT,
    low_percentile: float = DEFAULT_LOW_PERCENTILE,
    high_percentile: float = DEFAULT_HIGH_PERCENTILE,
) -> tuple[ChannelBounds, ChannelBounds, ChannelBounds]:
    """
    Reproduce RapidRAW's auto analysis.

    RapidRAW's default ignores a 12% border on every side, which equals a 76%
    central analysis region. It samples every 3rd row and picks direct sorted
    indices at 0.1% / 99.9% for each channel.
    """
    log_density = np.asarray(log_density, dtype=np.float32)
    if log_density.ndim != 3 or log_density.shape[2] < 3:
        raise ValueError("Expected log-density data with shape (H, W, 3)")
    log_density = log_density[:, :, :3]
    height, width = log_density.shape[:2]

    central_fraction = float(np.clip(central_region_percent / 100.0, 0.05, 1.0))
    margin_fraction = (1.0 - central_fraction) * 0.5
    margin_x = int(width * margin_fraction)
    margin_y = int(height * margin_fraction)
    x1 = max(margin_x, width - margin_x)
    y1 = max(margin_y, height - margin_y)

    est_pixels = max(0, width - margin_x * 2) * max(0, height - margin_y * 2)
    step = max(est_pixels // 40_000, 1)
    roi = log_density[margin_y:y1:3, margin_x:x1:step, :]
    low_fraction = float(np.clip(low_percentile / 100.0, 0.0, 1.0))
    high_fraction = float(np.clip(high_percentile / 100.0, 0.0, 1.0))
    if high_fraction <= low_fraction:
        high_fraction = min(low_fraction + 0.001, 1.0)
        low_fraction = min(low_fraction, max(high_fraction - 0.001, 0.0))

    def get_bounds(channel_values: np.ndarray) -> ChannelBounds:
        vals = channel_values[np.isfinite(channel_values)].astype(np.float32, copy=False).reshape(-1)
        if vals.size == 0:
            return ChannelBounds(0.0, 1.0)

        vals.sort()
        length = float(vals.size)
        min_idx = min(int(length * low_fraction), vals.size - 1)
        max_idx = min(int(length * high_fraction), vals.size - 1)
        mn = float(vals[min_idx])
        mx = float(vals[max_idx])
        if mx <= mn + 0.0001:
            mx = mn + 1.0
        return ChannelBounds(mn, mx)

    return tuple(get_bounds(roi[:, :, c]) for c in range(3))  # type: ignore[return-value]


def analyze_bounds(
    img: np.ndarray,
    central_region_percent: float = DEFAULT_CENTRAL_REGION_PERCENT,
    low_percentile: float = DEFAULT_LOW_PERCENTILE,
    high_percentile: float = DEFAULT_HIGH_PERCENTILE,
) -> tuple[ChannelBounds, ChannelBounds, ChannelBounds]:
    rgb = ensure_rgb_float01(img)
    log_density = -np.log10(np.clip(rgb, EPS, np.float32(1.0)))
    return analyze_bounds_from_log_density(
        log_density,
        central_region_percent,
        low_percentile,
        high_percentile,
    )


def convert_negative(
    img: np.ndarray,
    params: NegativeConversionParams = DEFAULT_PARAMS,
    bounds: tuple[ChannelBounds, ChannelBounds, ChannelBounds] | None = None,
) -> np.ndarray:
    """Apply the RapidRAW negative conversion pipeline to an RGB float [0, 1] image."""
    rgb = ensure_rgb_float01(img)
    log_density = -np.log10(np.clip(rgb, EPS, np.float32(1.0)))

    if bounds is None:
        bounds = analyze_bounds_from_log_density(
            log_density,
            params.central_region_percent,
            params.low_percentile,
            params.high_percentile,
        )

    mins = np.array([b.min for b in bounds], dtype=np.float32).reshape(1, 1, 3)
    maxs = np.array([b.max for b in bounds], dtype=np.float32).reshape(1, 1, 3)
    weights = np.array(
        [params.red_weight, params.green_weight, params.blue_weight],
        dtype=np.float32,
    ).reshape(1, 1, 3)

    normalized = (log_density - mins) / np.maximum(maxs - mins, EPS)
    normalized = np.maximum(normalized, np.float32(0.0)) * weights

    k = np.float32(4.0 * max(float(params.contrast), 0.1))
    x0 = np.float32(0.6 - float(params.exposure) * 0.25)
    y0 = np.float32(1.0) / (np.float32(1.0) + np.exp(k * x0))
    y1 = np.float32(1.0) / (np.float32(1.0) + np.exp(-k * (np.float32(1.0) - x0)))
    scale = np.float32(1.0) / np.maximum(y1 - y0, EPS)

    sigmoid = np.float32(1.0) / (np.float32(1.0) + np.exp(-k * (normalized - x0)))
    out = np.clip((sigmoid - y0) * scale, np.float32(0.0), np.float32(1.0))

    luma = (
        np.float32(0.2126) * out[:, :, 0]
        + np.float32(0.7152) * out[:, :, 1]
        + np.float32(0.0722) * out[:, :, 2]
    )
    max_ch = np.max(out, axis=2)
    overflow = np.clip((max_ch - np.float32(0.9)) * np.float32(10.0), np.float32(0.0), np.float32(1.0))
    sat_reduction = overflow * overflow
    out = out + (luma[:, :, None] - out) * sat_reduction[:, :, None]

    gamma_inv = np.float32(1.0 / 2.2)
    return np.power(np.clip(out, np.float32(0.0), np.float32(1.0)), gamma_inv).astype(np.float32)


def convert_negative_like_rapidraw_save(
    img: np.ndarray,
    params: NegativeConversionParams = DEFAULT_PARAMS,
    reference_max_dim: int = 1080,
) -> np.ndarray:
    """
    Match RapidRAW's save path: analyze a 1080px reference, then process full-res.
    """
    reference = downscale_rgb(img, reference_max_dim, reference_max_dim)
    bounds = analyze_bounds(
        reference,
        params.central_region_percent,
        params.low_percentile,
        params.high_percentile,
    )
    return convert_negative(img, params, bounds=bounds)

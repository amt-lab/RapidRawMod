import numpy as np
import cv2

_BLUR_SIGMA = 2.0   # shapes local vs. micro response; see docs/local-contrast.md

def _equalized_layer(img, mode, blur_sigma, clip_limit, tile_grid):
    # uint8 roundtrip is intentional — cv2 equalization operates on u8;
    # quantisation banding is invisible after opacity mix
    u8 = np.clip(img * 255.0 + 0.5, 0, 255).astype(np.uint8)

    if mode == 'clahe':
        # equalize luminance only (LAB L-channel); colour channels untouched
        lab = cv2.cvtColor(u8, cv2.COLOR_RGB2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=float(clip_limit),
                                 tileGridSize=(int(tile_grid), int(tile_grid)))
        lab_eq = cv2.merge((clahe.apply(l), a, b))
        eq_u8 = cv2.cvtColor(lab_eq, cv2.COLOR_LAB2RGB)
    else:  # 'histeq'
        # equalize each RGB channel independently
        channels = cv2.split(u8)
        eq_u8 = cv2.merge([cv2.equalizeHist(c) for c in channels])

    eq = eq_u8.astype(np.float32) / 255.0

    if blur_sigma and blur_sigma > 0.0:
        eq = cv2.GaussianBlur(eq, ksize=(0, 0),
                              sigmaX=float(blur_sigma), sigmaY=float(blur_sigma))

    return np.clip(eq, np.float32(0.0), np.float32(1.0))


def _soft_light(base, layer):
    # Photoshop soft-light formula; result depends on both pixels — not a curve
    b = np.clip(base,  np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)
    l = np.clip(layer, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)
    dark  = np.float32(2.0)*b*l + b*b*(np.float32(1.0) - np.float32(2.0)*l)
    light = np.sqrt(b)*(np.float32(2.0)*l - np.float32(1.0)) + np.float32(2.0)*b*(np.float32(1.0) - l)
    result = np.where(l < np.float32(0.5), dark, light)
    return np.clip(result, np.float32(0.0), np.float32(1.0))


def apply_local_contrast(img, strength, *, mode='clahe', blur_sigma=_BLUR_SIGMA,
                          clip_limit=2.0, tile_grid=8):
    """
    Local contrast enhancement via adaptive equalization + soft-light blend.

    strength  : 0.0 – 1.0; how much of the enhanced layer replaces the original
    mode      : 'clahe' (luminance only, colour-neutral) or 'histeq' (per RGB channel)
    blur_sigma: Gaussian blur on reference layer; shapes local vs. micro response (default _BLUR_SIGMA)
    clip_limit: CLAHE amplification cap per tile (default 2.0)
    tile_grid : CLAHE tiles per axis (default 8)
    """
    alpha = float(np.clip(strength, 0.0, 1.0))
    img = np.clip(img, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)

    if alpha < 1e-6:
        return img

    ref = _equalized_layer(img, mode, blur_sigma, clip_limit, tile_grid)
    blended = _soft_light(img, ref)
    out = img + np.float32(alpha) * (blended - img)
    return np.clip(out, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)

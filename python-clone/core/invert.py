
import numpy as np

MAX_DENSITY = 4.0   # log-space normalisation reference for Option-B tweak formula


def compute_stretch_points(img, *, pct_bp=1.0, pct_wp=99.0, frac=0.85, use_log=False, invert=False):
    """
    Compute per-channel black and white points from percentiles of a center crop.

    Returns (bp, wp) as float32 (3,) in the same working space that stretch() uses
    internally at the point where user_bp/user_wp are applied — i.e. after the
    optional inversion step.  Pass invert=True when calling stretch() with invert=True.

    Working space values:
      use_log=False, invert=False :  linear [0,1]
      use_log=True,  invert=False :  log10(pixel)          — pre-inversion
      use_log=False, invert=True  :  1 − pixel
      use_log=True,  invert=True  :  −log10(pixel)         — post-inversion density

    To convert a sampled linear pixel to the same space for manual overrides:
        -log10(clip(px, 1e-6, 1))  if use_log and invert
         log10(clip(px, 1e-6, 1))  if use_log and not invert
         1 - px                    if not use_log and invert
         px                        otherwise
    """
    eps = np.float32(1e-6)
    img = np.clip(img, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)
    if use_log:
        work = np.log10(np.clip(img, eps, np.float32(1.0)))
    else:
        work = img

    if invert:
        work = -work if use_log else (np.float32(1.0) - work)

    frac = float(np.clip(frac, 0.05, 1.0))
    h, w = work.shape[:2]
    ch = max(1, int(round(h * frac)))
    cw = max(1, int(round(w * frac)))
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    roi = work[y0:y0 + ch, x0:x0 + cw]
    bw = np.percentile(roi.reshape(-1, 3), [pct_bp, pct_wp], axis=0).astype(np.float32)
    return bw[0], bw[1]   # bp, wp  — both in post-inversion working space


def stretch(img, bp, wp, r=0.03):
    """
    Stretch per-channel [bp, wp] → [0, 1] with parabolic knee roll-off.

    Parameters
    ----------
    img : (H,W,3) float32 — image already in working space (post log-transform
          and inversion).  Caller is responsible for those steps.
    bp  : (3,) float32 — per-channel black point (post-inversion working space)
    wp  : (3,) float32 — per-channel white point (post-inversion working space),
          must satisfy wp > bp channel-wise.
    r   : float — roll-off radius in output units, [0, 0.5).
          0    = hard clip (standard linear stretch)
          0.03 = subtle, recovers ~3% of clipped range at each end
          0.05 = moderate, visible softness at extremes

    Returns
    -------
    (H,W,3) float32 in [0, 1]
    """
    bp = np.asarray(bp, dtype=np.float32).reshape(1, 1, 3)
    wp = np.asarray(wp, dtype=np.float32).reshape(1, 1, 3)
    v  = img.astype(np.float32)
    v  = (v - bp) / np.maximum(wp - bp, np.float32(1e-6))

    if r <= 0.0 or r >= 0.5:
        return np.clip(v, np.float32(0.0), np.float32(1.0))

    r = np.float32(r)
    out = v.copy()

    # Hard bounds outside roll-off
    out[v < -r]                          = np.float32(0.0)
    out[v > np.float32(1.0) + r]         = np.float32(1.0)

    # Parabolic toe:      v ∈ [−r, +r]     →  (v+r)²  / (4r)
    toe   = (v >= -r) & (v <= r)
    out[toe]   = (v[toe]   + r) ** 2 / (np.float32(4.0) * r)

    # Parabolic shoulder: v ∈ [1−r, 1+r]  →  1 − (1+r−v)² / (4r)
    shldr = (v >= np.float32(1.0) - r) & (v <= np.float32(1.0) + r)
    out[shldr] = np.float32(1.0) - (np.float32(1.0) + r - v[shldr]) ** 2 / (np.float32(4.0) * r)

    return out.astype(np.float32)


def stretch_noRollOff(img, *, base_rgb=None, pct_bp=1.0, pct_wp=99.0, frac=0.85,
                      user_bp=None, user_wp=None, gamma=1.0, use_log=False, invert=True):
    """
    All-in-one stretch: base correction, inversion, percentile or manual
    black/white-point stretch, and gamma in one pass.  Hard clip (no roll-off).

    Parameters
    ----------
    img        : (H,W,3) float32 [0,1] RGB
    base_rgb   : (3,) [0,1] film-base colour.  None = no base correction.
    pct_bp     : percentile used as black point in auto mode (default 1.0)
    pct_wp     : percentile used as white point in auto mode (default 99.0)
    frac       : central fraction of the image used for percentile statistics.
    user_bp    : (3,) black point in post-inversion working space, overrides auto.
    user_wp    : (3,) white point in post-inversion working space, overrides auto.
    gamma      : power applied after stretch; 1.0 = no change.
    use_log    : if True, work in log10 (optical density) space.
    invert     : if True, negate after base correction (film negative → positive).

    Returns
    -------
    float32 (H,W,3) in [0,1]
    """
    eps = np.float32(1e-6)

    img = np.clip(img, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)

    # ---- 1. Base correction + working-space transform ----------------------
    if use_log:
        log_img = np.log10(np.clip(img, eps, np.float32(1.0)))
        if base_rgb is not None:
            base = np.clip(np.asarray(base_rgb, dtype=np.float32), eps, np.float32(1.0)).reshape(1, 1, 3)
            work = log_img - np.log10(base)
        else:
            work = log_img
    else:
        if base_rgb is not None:
            base = np.clip(np.asarray(base_rgb, dtype=np.float32), eps, np.float32(1.0)).reshape(1, 1, 3)
            work = img / base
        else:
            work = img.copy()

    # ---- 2. Inversion ------------------------------------------------------
    if invert:
        work = -work if use_log else (np.float32(1.0) - work)

    # ---- 3. Black / white points -------------------------------------------
    if user_bp is not None and user_wp is not None:
        black = np.asarray(user_bp, dtype=np.float32).reshape(1, 1, 3)
        white = np.asarray(user_wp, dtype=np.float32).reshape(1, 1, 3)
    else:
        frac = float(np.clip(frac, 0.05, 1.0))
        h, w = work.shape[:2]
        ch = max(1, int(round(h * frac)))
        cw = max(1, int(round(w * frac)))
        y0, x0 = (h - ch) // 2, (w - cw) // 2
        roi = work[y0:y0 + ch, x0:x0 + cw]
        bw = np.percentile(roi.reshape(-1, 3), [pct_bp, pct_wp], axis=0).astype(np.float32)
        black = bw[0].reshape(1, 1, 3)
        white = bw[1].reshape(1, 1, 3)

    # ---- 4. Linear stretch -------------------------------------------------
    scale = np.maximum(white - black, eps)
    out = np.clip((work - black) / scale, np.float32(0.0), np.float32(1.0)).astype(np.float32)

    # ---- 5. Gamma ----------------------------------------------------------
    if abs(gamma - 1.0) > 1e-6:
        out = np.power(out, np.float32(max(gamma, 1e-6)))

    return out

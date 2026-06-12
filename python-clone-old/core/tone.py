import numpy as np


def apply_gamma(img, gamma):
    # gamma < 1 brightens; gamma > 1 darkens. GUI range: 0.2 – 3.0, default 1.0
    gamma = max(float(gamma), 1e-6)
    return np.power(np.clip(img, np.float32(0.0), np.float32(1.0)), np.float32(gamma))


def apply_s_curve(img, strength=0.0, steepness=1.2):
    # strength: [-1, 1]; 0 = bypass. Positive = classic S (boost contrast midtones).
    # steepness: controls how abrupt the curve is; 1.2 is mild. GUI range: 0.5 – 3.0.
    # Suggested GUI default: strength = 0.12
    strength = float(np.clip(strength, -1.0, 1.0))
    x = np.clip(img, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)
    if strength == 0.0:
        return x

    k = max(float(steepness), 1e-6)
    norm = np.tanh(k)
    if abs(norm) < 1e-9:
        return x

    u = np.float32(2.0) * x - np.float32(1.0)
    curve = np.float32(0.5) + np.float32(0.5) * (np.tanh(k * u) / norm)
    out = x + ((curve - x) * np.float32(strength))
    return np.clip(out, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)


def apply_brightness(img, brightness=0.0):
    # Parabolic midtone lift: out = x + c·x·(1−x)
    # brightness: [-0.6, 0.6]; 0 = bypass. Preserves exact black (0) and white (1).
    # Effect peaks at midtones (x=0.5), tapers to zero at both endpoints.
    c = float(np.clip(brightness, -0.6, 0.6))
    if abs(c) < 1e-6:
        return img
    x = np.clip(img, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)
    out = x + np.float32(c) * x * (np.float32(1.0) - x)
    return np.clip(out, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)


def apply_temperature_tint(img, temperature=0.0, tint=0.0):
    # Mild white-balance adjustment via per-channel multiplicative scaling.
    # temperature: [-1, 1]; positive = warm (R up, B down), negative = cool. GUI range: -1 – 1.
    # tint:        [-1, 1]; positive = magenta (G down), negative = green (G up). GUI range: -1 – 1.
    # Internally: R/B scaled ±25% at extremes; G scaled ±15% at extremes.
    t = float(np.clip(temperature, -1.0, 1.0))
    n = float(np.clip(tint, -1.0, 1.0))
    if abs(t) < 1e-6 and abs(n) < 1e-6:
        return img
    rgb = np.clip(img, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)
    out = rgb.copy()
    out[..., 0] = np.clip(rgb[..., 0] * np.float32(1.0 + t * 0.25), np.float32(0.0), np.float32(1.0))
    out[..., 1] = np.clip(rgb[..., 1] * np.float32(1.0 - n * 0.15), np.float32(0.0), np.float32(1.0))
    out[..., 2] = np.clip(rgb[..., 2] * np.float32(1.0 - t * 0.25), np.float32(0.0), np.float32(1.0))
    return out


def apply_saturation(img, saturation=1.0):
    # saturation: multiplicative scale on chroma; 1.0 = no change, 0.0 = grayscale.
    # GUI range: 0.0 – 3.0 (or expressed as 0 – 300%), default 100% (= 1.0).
    # Uses Rec.709 luma coefficients; no colorspace conversion needed for mild adjustments.
    s = float(saturation)
    rgb = np.clip(img, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)
    if abs(s - 1.0) < 1e-6:
        return rgb
    luma = (np.float32(0.2126) * rgb[..., 0]
            + np.float32(0.7152) * rgb[..., 1]
            + np.float32(0.0722) * rgb[..., 2])[..., None]
    out = luma + np.float32(s) * (rgb - luma)
    return np.clip(out, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)

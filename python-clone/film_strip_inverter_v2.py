import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
from dataclasses import dataclass, field
from PyQt6.QtCore import Qt, pyqtSignal, QRectF
from PyQt6.QtGui import QImage, QPixmap, QShortcut, QKeySequence, QPainter, QPen, QColor, QCursor
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QVBoxLayout, QWidget,
)

from core.img_io import read_image, read_raw_image, _RAW_EXTS, save_processed_image
from core.invert import stretch
from core.tone import apply_brightness, apply_gamma, apply_s_curve, apply_temperature_tint, apply_saturation
from core.local_contrast import apply_local_contrast

# ── Layout constants ───────────────────────────────────────────────────────────
DISPLAY_MAX     = 1000
PROXY_MAX       = 2000
WINDOW_W        = 1600
WINDOW_H        = 1050
CTRL_MIN_W      = 380
THUMB_SIZE      = 120     # film-strip thumbnail max edge (px)
STRIP_HEIGHT    = 160     # film-strip area height (px)
SWATCH_SIZE     = 26      # B/W/N colour swatch square (px)

# ── Stretch analysis ───────────────────────────────────────────────────────────
PCT_BP          = 1.0     # auto black-point percentile (central crop)
PCT_WP          = 99.0    # auto white-point percentile (central crop)

# ── BP/WP tweak behaviour — switch + tune after playing ─────────────────────────
# 'range' : additive nudge normalised to the stretch span (test_inverter behaviour)
#             bp = bp_base + t * (wp_base - bp_base)
# 'stops' : multiplicative nudge in stops on the working-space point
#             bp = bp_base * 2**t   ,   wp = wp_base * 2**t
BP_WP_TWEAK_MODE = 'range'
BP_TWEAK_RANGE   = 0.15   # slider extent (±) for the BP tweak
WP_TWEAK_RANGE   = 0.15   # slider extent (±) for the WP tweak

# Pseudo-log mode: linear stretch + fixed gamma applied before the tone pipeline
PSL_GAMMA       = 1.6

# ── Defaults ───────────────────────────────────────────────────────────────────
D_BUFFER_PCT    = 8.0     # % border excluded each side; frac = 1 − 2*(pct/100)
D_MODE          = 'lin'   # 'lin' | 'psl' | 'log'
D_BP_TWEAK      = 0.0
D_WP_TWEAK      = 0.0
D_SOFT_CLIP     = 0.03
D_BRIGHTNESS    = 0.0
D_GAMMA         = 1.0
D_S_CURVE       = 0.0
D_LOCAL_CONT    = 0.0
D_TEMPERATURE   = 0.0
D_TINT          = 0.0
D_SATURATION    = 0.0     # offset; passed to apply_saturation as 1.0 + offset

BRIGHT_RANGE    = 0.5     # brightness slider extent (±); core caps at ±0.6
TEMP_RANGE      = 0.33    # temperature and tint slider extent (±)
SAT_RANGE       = 0.5     # saturation offset slider extent (±)


# ── Dataclasses ─────────────────────────────────────────────────────────────────
@dataclass
class Settings:
    buffer_pct:     float  = D_BUFFER_PCT
    bp_base:        object = None   # (3,) post-inv working space, None = auto percentile
    wp_base:        object = None   # (3,) post-inv working space, None = auto percentile
    bp_tweak:       float  = D_BP_TWEAK
    wp_tweak:       float  = D_WP_TWEAK
    soft_clip:      float  = D_SOFT_CLIP
    mode:           str    = D_MODE
    flip:           bool   = False
    rot:            int    = 0
    brightness:     float  = D_BRIGHTNESS
    gamma:          float  = D_GAMMA
    s_curve:        float  = D_S_CURVE
    local_contrast: float  = D_LOCAL_CONT
    temperature:    float  = D_TEMPERATURE
    tint:           float  = D_TINT
    saturation:     float  = D_SATURATION
    gray_pick_rgb:  object = None   # display-only: positive colour of last N pick


@dataclass
class ImageState:
    path:     object  = None
    src_max:  int     = 255
    full_f:   object  = None    # full-res original, float32 RGB  (drives save)
    proxy_f:  object  = None    # downscaled original, float32 RGB (drives preview)
    base_f:   object  = None    # cached stretched + inverted proxy
    bp_used:  object  = None    # (3,) tweaked bp actually applied
    wp_used:  object  = None    # (3,) tweaked wp actually applied
    settings: Settings = field(default_factory=Settings)


# ── Helpers ──────────────────────────────────────────────────────────────────────
def _resize_float(img, max_dim):
    h, w = img.shape[:2]
    if max(h, w) <= max_dim:
        return img.copy()
    s = max_dim / max(h, w)
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    u8 = np.clip(img * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return cv2.resize(u8, (nw, nh), interpolation=cv2.INTER_LANCZOS4).astype(np.float32) / 255.0


def _to_qpixmap(img_f32):
    u8 = np.clip(img_f32 * 255.0 + 0.5, 0, 255).astype(np.uint8)
    u8 = np.ascontiguousarray(u8)
    h, w = u8.shape[:2]
    return QPixmap.fromImage(QImage(u8.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy())


def _write_image(out_path, img_f, src_max):
    # Write float RGB [0,1] to an explicit path (Save As…). Preserves 16-bit for
    # TIFF/PNG when the source was 16-bit, mirroring save_processed_image.
    ext = Path(out_path).suffix.lower()
    use_u16 = (src_max > 255) and (ext in {'.tif', '.tiff', '.png'})
    scale = 65535.0 if use_u16 else 255.0
    dtype = np.uint16 if use_u16 else np.uint8
    out = np.clip(img_f * scale + 0.5, 0.0, scale).astype(dtype)
    if not cv2.imwrite(out_path, cv2.cvtColor(out, cv2.COLOR_RGB2BGR)):
        raise IOError(f"cv2.imwrite failed: {out_path}")
    return out_path


def _apply_flip_rot(arr, flip, rot):
    if flip:
        arr = np.ascontiguousarray(arr[:, ::-1])
    if rot % 4:
        arr = np.ascontiguousarray(np.rot90(arr, k=-rot, axes=(0, 1)))
    return arr


def _working_to_u8(val, use_log):
    # post-inversion working-space value → original scan brightness [0,255]
    if val is None:
        return None
    a = np.asarray(val, dtype=np.float32)
    if use_log:
        a = np.power(np.float32(10.0), -a)   # −log10(px) → px
    else:
        a = np.float32(1.0) - a              # (1−px) → px
    return np.clip(np.round(a * 255), 0, 255).astype(int)


def _tweak_points(bp_base, wp_base, t_bp, t_wp):
    # Apply the BP/WP nudge in the selected mode. Both args are (3,) working space.
    if BP_WP_TWEAK_MODE == 'stops':
        bp = bp_base * np.float32(2.0) ** np.float32(t_bp)
        wp = wp_base * np.float32(2.0) ** np.float32(t_wp)
    else:  # 'range'
        rng = np.maximum(wp_base - bp_base, np.float32(1e-6))
        bp  = bp_base + np.float32(t_bp) * rng
        wp  = wp_base + np.float32(t_wp) * rng
    return bp, wp


def _run_stretch(img, s):
    """Log+invert, compute/tweak bp/wp, stretch. Returns (base_f, bp_used, wp_used).
    Resolution-independent: identical math on proxy and full-res."""
    use_log = s.mode == 'log'
    eps     = np.float32(1e-6)
    src     = np.clip(img, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)

    if use_log:
        work = -np.log10(np.clip(src, eps, np.float32(1.0)))
    else:
        work = np.float32(1.0) - src

    frac = max(0.05, 1.0 - 2.0 * s.buffer_pct / 100.0)
    h, w = work.shape[:2]
    ch   = max(1, int(round(h * frac)))
    cw   = max(1, int(round(w * frac)))
    y0   = (h - ch) // 2
    x0   = (w - cw) // 2
    roi  = work[y0:y0 + ch, x0:x0 + cw]
    bw   = np.percentile(roi.reshape(-1, 3), [PCT_BP, PCT_WP], axis=0).astype(np.float32)
    auto_bp, auto_wp = bw[0], bw[1]

    bp_base = np.asarray(s.bp_base, dtype=np.float32) if s.bp_base is not None else auto_bp
    wp_base = np.asarray(s.wp_base, dtype=np.float32) if s.wp_base is not None else auto_wp

    bp, wp = _tweak_points(bp_base, wp_base, s.bp_tweak, s.wp_tweak)

    base = stretch(work, bp, wp, s.soft_clip)
    if s.mode == 'psl':
        base = np.power(base, np.float32(PSL_GAMMA))
    return base, bp, wp


def _solve_gray(r, g, b):
    """Closed-form temperature/tint that drives the pixel (r,g,b) toward neutral
    under core.tone.apply_temperature_tint's multiplicative model:
        R' = R(1 + 0.25 t),  G' = G(1 − 0.15 n),  B' = B(1 − 0.25 t)
    Solve R'=B' for t, then G'=R' for n. Clamp to slider range."""
    eps = 1e-6
    r, g, b = float(r), float(g), float(b)
    denom = r + b
    t = 4.0 * (b - r) / denom if denom > eps else 0.0
    t = float(np.clip(t, -TEMP_RANGE, TEMP_RANGE))
    rp = r * (1.0 + 0.25 * t)
    n = (1.0 - rp / max(g, eps)) / 0.15
    n = float(np.clip(n, -TEMP_RANGE, TEMP_RANGE))
    return t, n


# ── FloatSlider: horizontal slider + read-only value label in one row ────────────
class FloatSlider(QWidget):
    valueChanged = pyqtSignal(float)

    def __init__(self, label, mn, mx, step, value):
        super().__init__()
        self._mn, self._mx, self._step = float(mn), float(mx), float(step)
        self._dec = max(0, min(4, len(f"{self._step:.4f}".rstrip("0").split(".")[-1])))

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel(label)
        lbl.setMinimumWidth(130)
        row.addWidget(lbl)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, int(round((self._mx - self._mn) / self._step)))
        row.addWidget(self._slider, 1)

        self._value_lbl = QLabel()
        self._value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._value_lbl.setFixedWidth(56)
        row.addWidget(self._value_lbl)

        self._slider.valueChanged.connect(self._from_slider)
        self.set_value(value, emit=False)

    def _to_float(self, i):
        return self._mn + i * self._step

    def _to_int(self, x):
        return int(round((min(max(float(x), self._mn), self._mx) - self._mn) / self._step))

    def _from_slider(self, i):
        v = self._to_float(i)
        self._value_lbl.setText(f"{v:.{self._dec}f}")
        self.valueChanged.emit(v)

    def value(self):
        return self._to_float(self._slider.value())

    def set_value(self, value, emit=True):
        self._slider.blockSignals(True)
        self._slider.setValue(self._to_int(float(value)))
        self._slider.blockSignals(False)
        self._value_lbl.setText(f"{self._to_float(self._slider.value()):.{self._dec}f}")
        if emit:
            self.valueChanged.emit(self.value())


# ── Image label: drag-drop + B/W/N click ─────────────────────────────────────────
class ImageLabel(QLabel):
    fileDropped  = pyqtSignal(str)
    pointClicked = pyqtSignal(int, int, str)   # row, col, 'B'|'W'|'N'

    def __init__(self):
        super().__init__("Drop images here\nor click  Open")
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("border: 1px solid #555; background: #1a1a1a; color: #ccc;")
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._proxy_shape = None
        self._show_overlay = False
        self._margin_frac = D_BUFFER_PCT / 100.0   # inset per side = analysis buffer %

    def set_overlay(self, show):
        self._show_overlay = bool(show)
        self.update()

    def set_margin_frac(self, frac):
        self._margin_frac = float(frac)
        if self._show_overlay:
            self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._show_overlay:
            return
        pix = self.pixmap()
        if pix is None or pix.isNull():
            return
        pw, ph = pix.width(), pix.height()
        ox = (self.width() - pw) // 2
        oy = (self.height() - ph) // 2
        mx, my = pw * self._margin_frac, ph * self._margin_frac
        rect = QRectF(ox + mx, oy + my, pw - 2 * mx, ph - 2 * my)
        p = QPainter(self)
        p.fillRect(rect, QColor(250, 204, 21, 38))          # yellow-400 @ ~15%
        p.setPen(QPen(QColor(250, 204, 21, 200), 2))
        p.drawRect(rect)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            # key-hold gesture OR an armed Set-Black/White/Neutral button
            key = getattr(win, '_held_key', None) or getattr(win, '_pick_mode', None)
            if key in ('B', 'W', 'N'):
                rc = self._pos_to_proxy(e.position().toPoint())
                if rc is not None:
                    self.pointClicked.emit(rc[0], rc[1], key)
        super().mousePressEvent(e)

    def _pos_to_proxy(self, pos):
        pix = self.pixmap()
        if pix is None or pix.isNull() or self._proxy_shape is None:
            return None
        pw, ph = pix.width(), pix.height()
        ox = (self.width() - pw) // 2
        oy = (self.height() - ph) // 2
        px, py = pos.x() - ox, pos.y() - oy
        if not (0 <= px < pw and 0 <= py < ph):
            return None
        proxy_h, proxy_w = self._proxy_shape[:2]
        ix = max(0, min(proxy_w - 1, int(px * proxy_w / pw)))
        iy = max(0, min(proxy_h - 1, int(py * proxy_h / ph)))
        return iy, ix

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = sorted(u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile())
        for p in paths:
            self.fileDropped.emit(p)
        e.acceptProposedAction()


# ── Film-strip thumbnail ──────────────────────────────────────────────────────────
class Thumbnail(QLabel):
    clicked = pyqtSignal(int)

    def __init__(self, index):
        super().__init__()
        self._index = index
        self.setFixedSize(THUMB_SIZE + 8, THUMB_SIZE + 8)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_active(False)

    def set_active(self, active):
        border = "2px solid #4da3ff" if active else "1px solid #444"
        self.setStyleSheet(f"border: {border}; background: #111;")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._index)
        super().mousePressEvent(e)


# ── Main window ────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("neg-invert  |  film-strip inverter  v2")
        self.resize(WINDOW_W, WINDOW_H)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._states       = []      # list[ImageState]
        self._thumbs       = []      # list[Thumbnail]
        self._active       = -1
        self._held_key     = None
        self._pick_mode    = None    # armed explicit pick: 'B'|'W'|'N'|None
        self._current_mode = D_MODE
        self._pre_wb       = None    # cached pre-temp/tint proxy (for N pick)

        # ── Top action buttons ─────────────────────────────────────────────────
        self._btn_open     = QPushButton("Open")
        self._btn_save     = QPushButton("Save")
        self._btn_save_as  = QPushButton("Save As…")
        self._btn_save_all = QPushButton("Save All")
        self._btn_clear    = QPushButton("Clear")
        self._btn_flip     = QPushButton("Flip H")
        self._btn_rot      = QPushButton("Rotate CW")

        self._btn_open.clicked.connect(self._on_open)
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save_as.clicked.connect(self._on_save_as)
        self._btn_save_all.clicked.connect(self._on_save_all)
        self._btn_clear.clicked.connect(self._on_clear)
        self._btn_flip.clicked.connect(self._on_flip)
        self._btn_rot.clicked.connect(self._on_rotate_cw)

        btn_row = QHBoxLayout()
        for b in (self._btn_open, self._btn_save, self._btn_save_as, self._btn_save_all):
            btn_row.addWidget(b)
        btn_row.addSpacing(12)
        for b in (self._btn_clear, self._btn_flip, self._btn_rot):
            btn_row.addWidget(b)
        btn_row.addStretch(1)

        # ── Image area ──────────────────────────────────────────────────────────
        self._img_label = ImageLabel()
        self._img_label.fileDropped.connect(self._load_path)
        self._img_label.pointClicked.connect(self._on_point_clicked)

        # ── Film-strip ──────────────────────────────────────────────────────────
        strip_host = QWidget()
        self._strip_layout = QHBoxLayout(strip_host)
        self._strip_layout.setContentsMargins(4, 4, 4, 4)
        self._strip_layout.setSpacing(6)
        self._strip_layout.addStretch(1)

        self._strip_scroll = QScrollArea()
        self._strip_scroll.setWidgetResizable(True)
        self._strip_scroll.setWidget(strip_host)
        self._strip_scroll.setFixedHeight(STRIP_HEIGHT)
        self._strip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._strip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        img_area = QVBoxLayout()
        img_area.addLayout(btn_row)
        img_area.addWidget(self._img_label, 1)
        img_area.addWidget(self._strip_scroll)
        img_widget = QWidget()
        img_widget.setLayout(img_area)

        # ── Controls panel ──────────────────────────────────────────────────────
        ctrl_host   = QWidget()
        ctrl_layout = QVBoxLayout(ctrl_host)
        ctrl_layout.setContentsMargins(8, 8, 8, 8)
        ctrl_layout.setSpacing(4)

        def section(title):
            lbl = QLabel(title)
            lbl.setStyleSheet("font-weight: bold; margin-top: 8px;")
            ctrl_layout.addWidget(lbl)

        self._btn_reset = QPushButton("Reset")
        self._btn_reset.clicked.connect(self._on_reset)
        ctrl_btn_row = QHBoxLayout()
        ctrl_btn_row.addWidget(self._btn_reset)
        ctrl_btn_row.addStretch(1)
        ctrl_layout.addLayout(ctrl_btn_row)

        # Conversion mode: explicit LIN / LOG / PSL buttons (checkable, exclusive)
        self._mode_buttons = {}
        mode_row = QHBoxLayout()
        for m in ('lin', 'log', 'psl'):
            b = QPushButton(m.upper())
            b.setCheckable(True)
            b.clicked.connect(lambda _checked, mode=m: self._on_set_mode(mode))
            self._mode_buttons[m] = b
            mode_row.addWidget(b)
        self._refresh_mode_buttons(D_MODE)
        ctrl_layout.addLayout(mode_row)

        # Point picking — explicit arm buttons (hybrid with the B/W/N key-hold)
        self._pick_buttons = {}
        pick_row = QHBoxLayout()
        for tag, lbl in (('B', 'Set Black'), ('W', 'Set White'), ('N', 'Set Neutral')):
            b = QPushButton(lbl)
            b.setCheckable(True)
            b.clicked.connect(lambda _c, t=tag: self._on_pick_button(t))
            self._pick_buttons[tag] = b
            pick_row.addWidget(b)
        self._btn_auto = QPushButton("Auto")
        self._btn_auto.clicked.connect(self._on_pick_auto)
        pick_row.addWidget(self._btn_auto)
        ctrl_layout.addLayout(pick_row)

        self._pick_status = QLabel("")
        self._pick_status.setStyleSheet("color: #4da3ff; font-size: 11px;")
        ctrl_layout.addWidget(self._pick_status)

        # Point swatches (B / W / N)
        self._sw_b = self._make_swatch()
        self._sw_w = self._make_swatch()
        self._sw_n = self._make_swatch()
        sw_row = QHBoxLayout()
        for tag, sw in (("B", self._sw_b), ("W", self._sw_w), ("N", self._sw_n)):
            cell = QVBoxLayout()
            cap = QLabel(tag)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(cap)
            cell.addWidget(sw)
            sw_row.addLayout(cell)
        sw_row.addStretch(1)
        ctrl_layout.addLayout(sw_row)

        self._sliders = {}

        def add(key, label, mn, mx, step, default):
            w = FloatSlider(label, mn, mx, step, default)
            w.valueChanged.connect(self._on_slider_changed)
            self._sliders[key] = w
            ctrl_layout.addWidget(w)

        section("Stretch")
        add("buffer_pct", "Analysis Buffer %", 0.0, 30.0, 0.5, D_BUFFER_PCT)
        add("bp_tweak",   "BP Tweak",  -BP_TWEAK_RANGE, BP_TWEAK_RANGE, 0.01, D_BP_TWEAK)
        add("wp_tweak",   "WP Tweak",  -WP_TWEAK_RANGE, WP_TWEAK_RANGE, 0.01, D_WP_TWEAK)
        add("soft_clip",  "Soft Clip",  0.0, 0.1, 0.005, D_SOFT_CLIP)

        self._btn_overlay = QPushButton("Show Analysis Area")
        self._btn_overlay.setCheckable(True)
        self._btn_overlay.toggled.connect(self._on_toggle_overlay)
        ctrl_layout.addWidget(self._btn_overlay)

        section("Tone")
        add("brightness",     "Brightness",    -BRIGHT_RANGE, BRIGHT_RANGE, 0.01, D_BRIGHTNESS)
        add("gamma",          "Gamma",          0.2, 3.0, 0.01, D_GAMMA)
        add("s_curve",        "S-curve",       -1.0, 1.0, 0.01, D_S_CURVE)
        add("local_contrast", "Local Contrast", 0.0, 1.0, 0.01, D_LOCAL_CONT)
        add("temperature",    "Temperature",   -TEMP_RANGE, TEMP_RANGE, 0.01, D_TEMPERATURE)
        add("tint",           "Tint",          -TEMP_RANGE, TEMP_RANGE, 0.01, D_TINT)
        add("saturation",     "Saturation",    -SAT_RANGE,  SAT_RANGE,  0.01, D_SATURATION)

        self._info = QLabel(
            "No image loaded.\n\n"
            "Hover image + press B → black point\n"
            "Hover image + press W → white point\n"
            "Hover image + press N → neutral (gray) point\n"
            "(then left-click while holding the key)"
        )
        self._info.setWordWrap(True)
        self._info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._info.setStyleSheet("font-family: Menlo, monospace; font-size: 11px; color: #ccc; margin-top: 10px;")
        ctrl_layout.addWidget(self._info)
        ctrl_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(ctrl_host)
        scroll.setMinimumWidth(CTRL_MIN_W)
        scroll.setMaximumWidth(500)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(img_widget, 1)
        root_layout.addWidget(scroll)
        self.setCentralWidget(root)

        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._on_save)

    # ── State access ────────────────────────────────────────────────────────────
    def _st(self):
        return self._states[self._active] if 0 <= self._active < len(self._states) else None

    @staticmethod
    def _make_swatch():
        sw = QFrame()
        sw.setFixedSize(SWATCH_SIZE, SWATCH_SIZE)
        sw.setStyleSheet("background: #333; border: 1px solid #888;")
        return sw

    @staticmethod
    def _set_swatch(sw, rgb):
        if rgb is None:
            sw.setStyleSheet("background: #333; border: 1px solid #888;")
        else:
            r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
            sw.setStyleSheet(f"background: rgb({r},{g},{b}); border: 1px solid #888;")

    # ── Key tracking ──────────────────────────────────────────────────────────────
    def keyPressEvent(self, e):
        k = e.key()
        if   k == Qt.Key.Key_B: self._held_key = 'B'
        elif k == Qt.Key.Key_W: self._held_key = 'W'
        elif k == Qt.Key.Key_N: self._held_key = 'N'
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() in (Qt.Key.Key_B, Qt.Key.Key_W, Qt.Key.Key_N):
            self._held_key = None
        super().keyReleaseEvent(e)

    # ── Load ────────────────────────────────────────────────────────────────────
    def _on_open(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open image(s)", "",
            "Images (*.jpg *.jpeg *.tif *.tiff *.png "
            "*.nef *.cr2 *.cr3 *.arw *.dng *.orf *.rw2 *.raf)",
        )
        for p in sorted(paths):
            self._load_path(p)

    def _load_path(self, path):
        try:
            ext = Path(path).suffix.lower()
            if ext in _RAW_EXTS:
                full_f, src_max = read_raw_image(path), 65535
            else:
                full_f, src_max = read_image(path)
        except Exception as e:
            self._info.setText(f"Load error: {e}")
            return

        settings      = Settings(mode=self._current_mode)
        proxy_f       = _resize_float(full_f, PROXY_MAX)
        st            = ImageState(path=path, src_max=src_max,
                                   full_f=full_f, proxy_f=proxy_f, settings=settings)
        idx           = len(self._states)
        self._states.append(st)

        thumb = Thumbnail(idx)
        thumb.clicked.connect(self._set_active)
        self._thumbs.append(thumb)
        self._strip_layout.insertWidget(self._strip_layout.count() - 1, thumb)

        self._set_active(idx)

    # ── Active image switching ────────────────────────────────────────────────────
    def _set_active(self, idx):
        if not (0 <= idx < len(self._states)):
            return
        self._active = idx
        for i, t in enumerate(self._thumbs):
            t.set_active(i == idx)
        st = self._states[idx]
        self._current_mode = st.settings.mode
        self._refresh_mode_buttons(st.settings.mode)
        self._sync_sliders()
        if st.base_f is None:
            self._rebuild_base()
        else:
            self._update_preview()
            self._update_info()

    # ── Rebuild base (stretch + invert) ─────────────────────────────────────────
    def _rebuild_base(self):
        st = self._st()
        if st is None or st.proxy_f is None:
            return
        base, bp, wp = _run_stretch(st.proxy_f, st.settings)
        st.base_f, st.bp_used, st.wp_used = base, bp, wp
        self._update_preview()
        self._update_info()

    # ── Tone pipeline → display + thumbnail ──────────────────────────────────────
    def _compose(self, st):
        # Returns oriented processed proxy. Caches the pre-temp/tint stage for N picks.
        s = st.settings
        out = apply_brightness(st.base_f, s.brightness)
        out = apply_gamma(out, s.gamma)
        out = apply_s_curve(out, s.s_curve)
        out = apply_local_contrast(out, s.local_contrast, mode='clahe')
        self._pre_wb = out                          # pre temp/tint (pre-orientation)
        out = apply_temperature_tint(out, s.temperature, s.tint)
        out = apply_saturation(out, 1.0 + s.saturation)
        return _apply_flip_rot(out, s.flip, s.rot)

    def _update_preview(self):
        st = self._st()
        if st is None or st.base_f is None:
            return
        out = self._compose(st)
        self._img_label._proxy_shape = out.shape    # may change on 90°/270°
        self._img_label.set_margin_frac(st.settings.buffer_pct / 100.0)

        pix = _to_qpixmap(out)
        self._img_label.setPixmap(pix.scaled(
            DISPLAY_MAX, DISPLAY_MAX,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        self._refresh_thumb(self._active, out)

    def _refresh_thumb(self, idx, oriented_out):
        if not (0 <= idx < len(self._thumbs)):
            return
        pix = _to_qpixmap(oriented_out).scaled(
            THUMB_SIZE, THUMB_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._thumbs[idx].setPixmap(pix)

    # ── Info + swatches ───────────────────────────────────────────────────────────
    def _update_info(self):
        st = self._st()
        if st is None:
            return
        s = st.settings
        use_log = s.mode == 'log'
        bp_u8 = _working_to_u8(st.bp_used, use_log)
        wp_u8 = _working_to_u8(st.wp_used, use_log)
        self._set_swatch(self._sw_b, bp_u8)
        self._set_swatch(self._sw_w, wp_u8)
        self._set_swatch(self._sw_n,
                         None if s.gray_pick_rgb is None
                         else np.clip(np.round(np.asarray(s.gray_pick_rgb) * 255), 0, 255).astype(int))

        bp_src = 'click' if s.bp_base is not None else 'auto'
        wp_src = 'click' if s.wp_base is not None else 'auto'
        h_f, w_f = st.full_f.shape[:2]
        h_p, w_p = st.proxy_f.shape[:2]
        lines = [
            f"[{self._active + 1}/{len(self._states)}]  {Path(st.path).name}",
            f"Full: {w_f}×{h_f}   Proxy: {w_p}×{h_p}",
            f"Stretch: {s.mode.upper()}   buf={s.buffer_pct:.1f}%   tweak={BP_WP_TWEAK_MODE}",
            f"BP tweak={s.bp_tweak:+.2f}  WP tweak={s.wp_tweak:+.2f}  softclip={s.soft_clip:.3f}",
            "",
        ]
        if bp_u8 is not None:
            lines.append(f"BP [{bp_src:5s}]  R:{bp_u8[0]:3d}  G:{bp_u8[1]:3d}  B:{bp_u8[2]:3d}")
        if wp_u8 is not None:
            lines.append(f"WP [{wp_src:5s}]  R:{wp_u8[0]:3d}  G:{wp_u8[1]:3d}  B:{wp_u8[2]:3d}")
        lines += ["", "B / W / N + click → black / white / gray point"]
        self._info.setText("\n".join(lines))

    # ── Slider handler ──────────────────────────────────────────────────────────
    def _on_slider_changed(self, _val):
        st = self._st()
        if st is None:
            return
        s = st.settings
        old_stretch = (s.buffer_pct, s.bp_tweak, s.wp_tweak, s.soft_clip)

        s.buffer_pct     = self._sliders['buffer_pct'].value()
        s.bp_tweak       = self._sliders['bp_tweak'].value()
        s.wp_tweak       = self._sliders['wp_tweak'].value()
        s.soft_clip      = self._sliders['soft_clip'].value()
        s.brightness     = self._sliders['brightness'].value()
        s.gamma          = self._sliders['gamma'].value()
        s.s_curve        = self._sliders['s_curve'].value()
        s.local_contrast = self._sliders['local_contrast'].value()
        s.temperature    = self._sliders['temperature'].value()
        s.tint           = self._sliders['tint'].value()
        s.saturation     = self._sliders['saturation'].value()

        if (s.buffer_pct, s.bp_tweak, s.wp_tweak, s.soft_clip) != old_stretch:
            self._rebuild_base()
        else:
            self._update_preview()
            self._update_info()

    # ── Mode select (LIN / LOG / PSL buttons) ─────────────────────────────────────
    def _refresh_mode_buttons(self, mode):
        for m, b in self._mode_buttons.items():
            b.setChecked(m == mode)

    def _on_set_mode(self, mode):
        self._current_mode = mode
        self._refresh_mode_buttons(mode)
        st = self._st()
        if st is None:
            return
        s = st.settings
        if s.mode == mode:
            return
        s.mode    = mode
        s.bp_base = None    # working space changed; click overrides no longer valid
        s.wp_base = None
        self._rebuild_base()

    def _on_toggle_overlay(self, checked):
        self._img_label.set_overlay(checked)
        self._btn_overlay.setText("Hide Analysis Area" if checked else "Show Analysis Area")

    # ── Pick arming (explicit buttons; shares the handler with the key-hold) ──────
    def _on_pick_button(self, tag):
        self._set_pick_mode(None if self._pick_mode == tag else tag)

    def _set_pick_mode(self, mode):
        self._pick_mode = mode
        for tag, b in self._pick_buttons.items():
            b.setChecked(tag == mode)
        self._img_label.setCursor(
            QCursor(Qt.CursorShape.CrossCursor if mode else Qt.CursorShape.ArrowCursor))
        names = {'B': 'BLACK', 'W': 'WHITE', 'N': 'NEUTRAL (gray)'}
        self._pick_status.setText(
            f"▶ Picking {names[mode]} — click the image" if mode else "")

    def _on_pick_auto(self):
        self._set_pick_mode(None)
        self._on_reset_bw()

    # ── Point picking ──────────────────────────────────────────────────────────────
    def _on_point_clicked(self, row, col, key):
        st = self._st()
        if st is None or st.proxy_f is None:
            return
        s = st.settings
        if key == 'N':
            self._set_gray_point(st, row, col)
            self._set_pick_mode(None)
            return
        # Sample original scan pixel in display orientation, convert to working space.
        pixel = _apply_flip_rot(st.proxy_f, s.flip, s.rot)[row, col]
        if s.mode == 'log':
            working = -np.log10(np.clip(pixel, np.float32(1e-6), np.float32(1.0)))
        else:
            working = np.float32(1.0) - pixel
        if key == 'B':
            s.bp_base = working
        else:
            s.wp_base = working
        self._set_pick_mode(None)
        self._rebuild_base()

    def _set_gray_point(self, st, row, col):
        if self._pre_wb is None:
            return
        s = st.settings
        # Sample the pre-temp/tint pixel (post stretch+gamma+s-curve+local-contrast),
        # in display orientation, then solve temp/tint to neutralise it.
        pre = _apply_flip_rot(self._pre_wb, s.flip, s.rot)
        r, g, b = pre[row, col]
        t, n = _solve_gray(r, g, b)
        s.temperature   = t
        s.tint          = n
        s.gray_pick_rgb = np.asarray(pre[row, col], dtype=np.float32)  # positive colour clicked
        self._sliders['temperature'].set_value(t, emit=False)
        self._sliders['tint'].set_value(n, emit=False)
        self._update_preview()
        self._update_info()

    # ── Orientation ─────────────────────────────────────────────────────────────
    def _on_flip(self):
        st = self._st()
        if st is None:
            return
        s = st.settings
        s.rot  = (-s.rot) % 4       # dihedral composition
        s.flip = not s.flip
        self._update_preview()

    def _on_rotate_cw(self):
        st = self._st()
        if st is None:
            return
        st.settings.rot = (st.settings.rot + 1) % 4
        self._update_preview()

    # ── Reset ─────────────────────────────────────────────────────────────────────
    def _on_reset(self):
        st = self._st()
        if st is None:
            return
        old = st.settings
        self._current_mode = D_MODE
        self._refresh_mode_buttons(D_MODE)
        st.settings = Settings(mode=D_MODE, flip=old.flip, rot=old.rot)
        self._sync_sliders()
        self._rebuild_base()

    def _on_reset_bw(self):
        st = self._st()
        if st is None:
            return
        s = st.settings
        s.bp_base  = None
        s.wp_base  = None
        s.bp_tweak = D_BP_TWEAK
        s.wp_tweak = D_WP_TWEAK
        self._sliders['bp_tweak'].set_value(D_BP_TWEAK, emit=False)
        self._sliders['wp_tweak'].set_value(D_WP_TWEAK, emit=False)
        self._rebuild_base()

    def _sync_sliders(self):
        s = self._st().settings
        mapping = {
            'buffer_pct':     s.buffer_pct,
            'bp_tweak':       s.bp_tweak,
            'wp_tweak':       s.wp_tweak,
            'soft_clip':      s.soft_clip,
            'brightness':     s.brightness,
            'gamma':          s.gamma,
            's_curve':        s.s_curve,
            'local_contrast': s.local_contrast,
            'temperature':    s.temperature,
            'tint':           s.tint,
            'saturation':     s.saturation,
        }
        for key, val in mapping.items():
            self._sliders[key].set_value(val, emit=False)

    # ── Clear — reset the whole session to start state ──────────────────────────
    def _on_clear(self):
        for t in self._thumbs:
            self._strip_layout.removeWidget(t)
            t.setParent(None)
            t.deleteLater()
        self._states  = []
        self._thumbs  = []
        self._active  = -1
        self._pre_wb  = None
        self._set_pick_mode(None)

        self._current_mode = D_MODE
        self._refresh_mode_buttons(D_MODE)

        defaults = {
            'buffer_pct': D_BUFFER_PCT, 'bp_tweak': D_BP_TWEAK, 'wp_tweak': D_WP_TWEAK,
            'soft_clip': D_SOFT_CLIP, 'brightness': D_BRIGHTNESS, 'gamma': D_GAMMA,
            's_curve': D_S_CURVE,
            'local_contrast': D_LOCAL_CONT, 'temperature': D_TEMPERATURE,
            'tint': D_TINT, 'saturation': D_SATURATION,
        }
        for key, val in defaults.items():
            self._sliders[key].set_value(val, emit=False)

        for sw in (self._sw_b, self._sw_w, self._sw_n):
            self._set_swatch(sw, None)

        self._img_label.setPixmap(QPixmap())
        self._img_label._proxy_shape = None
        self._img_label.setText("Drop images here\nor click  Open")
        self._info.setText(
            "No image loaded.\n\n"
            "Hover image + press B → black point\n"
            "Hover image + press W → white point\n"
            "Hover image + press N → neutral (gray) point\n"
            "(then left-click while holding the key)"
        )

    # ── Save — full-resolution, identical pipeline ───────────────────────────────
    def _process_full(self, st):
        s = st.settings
        out, _, _ = _run_stretch(st.full_f, s)
        out = apply_brightness(out, s.brightness)
        out = apply_gamma(out, s.gamma)
        out = apply_s_curve(out, s.s_curve)
        out = apply_local_contrast(out, s.local_contrast, mode='clahe')
        out = apply_temperature_tint(out, s.temperature, s.tint)
        out = apply_saturation(out, 1.0 + s.saturation)
        return _apply_flip_rot(out, s.flip, s.rot)

    def _on_save(self):
        # Save button / Ctrl+S: auto-name (_inv) next to source, current image.
        st = self._st()
        if st is None or st.full_f is None:
            self._info.setText("No image loaded.")
            return
        try:
            path = save_processed_image(self._process_full(st), st.path, st.src_max, suffix='_inv')
            self._info.setText(f"Saved:\n{path}")
        except Exception as e:
            self._info.setText(f"Save error: {e}")

    def _on_save_as(self):
        # Save As… : dialog to pick a destination for the current image.
        st = self._st()
        if st is None or st.full_f is None:
            self._info.setText("No image loaded.")
            return
        src = Path(st.path)
        suggested = str(src.with_name(f"{src.stem}_inv{src.suffix or '.tif'}"))
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save inverted image", suggested,
            "TIFF (*.tif *.tiff);;PNG (*.png);;JPEG (*.jpg *.jpeg);;All files (*)")
        if not out_path:
            return
        try:
            _write_image(out_path, self._process_full(st), st.src_max)
            self._info.setText(f"Saved:\n{out_path}")
        except Exception as e:
            self._info.setText(f"Save error: {e}")

    def _on_save_all(self):
        # Save All: auto-name (_inv) every loaded image — the film-strip batch payoff.
        if not self._states:
            self._info.setText("No images loaded.")
            return
        saved, errs = 0, []
        for st in self._states:
            try:
                save_processed_image(self._process_full(st), st.path, st.src_max, suffix='_inv')
                saved += 1
            except Exception as e:
                errs.append(f"{Path(st.path).name}: {e}")
        msg = f"Saved {saved}/{len(self._states)} image(s) (_inv next to source)."
        if errs:
            msg += "\nErrors:\n" + "\n".join(errs)
        self._info.setText(msg)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

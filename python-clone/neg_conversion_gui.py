from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from PyQt6.QtCore import Qt, QTimer, QRect, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.img_io import read_image, read_raw_image
from core.rapidraw_negative import (
    CENTER_MARGIN,
    DEFAULT_PARAMS,
    MODE_LIN,
    MODE_LOG,
    MODE_PSL,
    NegativeConversionParams,
    convert_negative_like_rapidraw_save,
    convert_negative_preview,
    downscale_rgb,
    sample_point,
)


RAW_EXTS = {
    ".nef", ".cr2", ".cr3", ".arw", ".dng", ".orf", ".crw",
    ".rw2", ".raf", ".pef", ".srw", ".kdc", ".mrw", ".3fr", ".erf",
}

MODES = [(MODE_LIN, "Linear"), (MODE_PSL, "Pseudo-Log"), (MODE_LOG, "Log")]


def read_rgb01(path: str) -> np.ndarray:
    ext = Path(path).suffix.lower()
    if ext in RAW_EXTS:
        return read_raw_image(path)
    img, _src_max = read_image(path)
    return img


def rgb01_to_pixmap(img: np.ndarray) -> QPixmap:
    rgb8 = np.clip(img * 255.0 + 0.5, 0, 255).astype(np.uint8)
    rgb8 = np.ascontiguousarray(rgb8)
    h, w = rgb8.shape[:2]
    qimg = QImage(rgb8.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


def write_rgb16_tiff(path: str, img: np.ndarray) -> None:
    out = np.clip(img * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
    out_bgr = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
    if not cv2.imwrite(path, out_bgr):
        raise OSError(f"cv2.imwrite failed: {path}")


class PreviewView(QWidget):
    """Draws the converted preview letterboxed, with an optional analysis-area
    overlay, and emits normalized [0,1] coordinates when clicked in pick mode."""

    point_picked = pyqtSignal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._image_rect = QRect()
        self._show_overlay = False
        self._margin = float(CENTER_MARGIN)
        self._pick = False
        self.setMinimumSize(640, 480)
        self.setStyleSheet("background: #101010;")

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self.update()

    def set_overlay(self, show: bool) -> None:
        self._show_overlay = show
        self.update()

    def set_margin(self, margin: float) -> None:
        self._margin = float(margin)
        self.update()

    def set_pick(self, on: bool) -> None:
        self._pick = on
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor))

    def _compute_image_rect(self) -> QRect:
        if self._pixmap is None or self._pixmap.isNull():
            return QRect()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        aw, ah = self.width(), self.height()
        scale = min(aw / pw, ah / ph)
        dw, dh = int(pw * scale), int(ph * scale)
        ox, oy = (aw - dw) // 2, (ah - dh) // 2
        return QRect(ox, oy, dw, dh)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101010"))
        if self._pixmap is None or self._pixmap.isNull():
            painter.setPen(QColor("#cccccc"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Open an RGB image or RAW scan")
            return

        self._image_rect = self._compute_image_rect()
        painter.drawPixmap(self._image_rect, self._pixmap)

        if self._show_overlay and not self._image_rect.isEmpty():
            r = self._image_rect
            inset_x = int(r.width() * self._margin)
            inset_y = int(r.height() * self._margin)
            region = r.adjusted(inset_x, inset_y, -inset_x, -inset_y)
            painter.fillRect(region, QColor(250, 204, 21, 38))  # yellow-400 @ ~15%
            painter.setPen(QPen(QColor(250, 204, 21, 200), 2))
            painter.drawRect(region)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._pick or self._image_rect.isEmpty():
            return
        pos = event.position().toPoint()
        if not self._image_rect.contains(pos):
            return
        nx = (pos.x() - self._image_rect.x()) / self._image_rect.width()
        ny = (pos.y() - self._image_rect.y()) / self._image_rect.height()
        self.point_picked.emit(float(np.clip(nx, 0.0, 1.0)), float(np.clip(ny, 0.0, 1.0)))


class FloatSlider(QWidget):
    def __init__(self, label, min_value, max_value, default, step,
                 decimals=2, suffix="", parent=None) -> None:
        super().__init__(parent)
        self.min_value = float(min_value)
        self.max_value = float(max_value)
        self.step = float(step)
        self.decimals = int(decimals)
        self.suffix = suffix

        self.title = QLabel(label)
        self.value_label = QLabel()
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, int(round((self.max_value - self.min_value) / self.step)))
        self.slider.setValue(self.float_to_int(default))

        header = QHBoxLayout()
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.value_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addWidget(self.slider)
        self.update_label()
        self.slider.valueChanged.connect(self.update_label)

    def value(self) -> float:
        return self.min_value + self.slider.value() * self.step

    def set_float_value(self, value: float) -> None:
        self.slider.setValue(self.float_to_int(value))

    def float_to_int(self, value: float) -> int:
        return int(round((float(value) - self.min_value) / self.step))

    def update_label(self, _value=None) -> None:
        self.value_label.setText(f"{self.value():.{self.decimals}f}{self.suffix}")


class NegativeConversionWindow(QMainWindow):
    def __init__(self, image_path: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("RapidRAW Negative Conversion Clone")
        self.resize(1280, 860)

        self.image_path: str | None = None
        self.full_image: np.ndarray | None = None
        self.preview_base: np.ndarray | None = None

        # Mutable copy of params (overrides/tweaks/mode are not on sliders).
        self.params = DEFAULT_PARAMS
        self.pick_mode: str | None = None  # 'black' | 'white' | None

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(80)
        self.preview_timer.timeout.connect(self.update_preview)

        self.view = PreviewView()
        self.view.point_picked.connect(self.on_point_picked)

        self.open_button = QPushButton("Open")
        self.save_button = QPushButton("Save TIFF")
        self.reset_button = QPushButton("Reset")
        self.save_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_image)
        self.save_button.clicked.connect(self.save_image)
        self.reset_button.clicked.connect(self.reset_params)

        # --- Conversion mode buttons ---
        self.mode_buttons: dict[str, QPushButton] = {}
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for mode_id, label in MODES:
            b = QPushButton(label)
            b.setCheckable(True)
            b.clicked.connect(lambda _checked, m=mode_id: self.on_mode_change(m))
            self.mode_buttons[mode_id] = b
            self.mode_group.addButton(b)
        self.mode_buttons[self.params.mode].setChecked(True)

        # --- Tone sliders ---
        # Color Timing (RGB weight) ranges are halved around neutral 1.0 so the same
        # drag does half the work (see DOCS_MOD/MOD-CHANGES-slider-sensitivity.md).
        self.red_slider = FloatSlider("Red Weight", 0.75, 1.5, DEFAULT_PARAMS.red_weight, 0.01)
        self.green_slider = FloatSlider("Green Weight", 0.75, 1.5, DEFAULT_PARAMS.green_weight, 0.01)
        self.blue_slider = FloatSlider("Blue Weight", 0.75, 1.5, DEFAULT_PARAMS.blue_weight, 0.01)
        self.exposure_slider = FloatSlider("Exposure", -2.0, 2.0, DEFAULT_PARAMS.exposure, 0.05)
        self.contrast_slider = FloatSlider("Contrast", 0.5, 2.5, DEFAULT_PARAMS.contrast, 0.05)
        self.gamma_slider = FloatSlider("Gamma", 0.5, 3.0, DEFAULT_PARAMS.gamma, 0.05)
        self.bp_tweak_slider = FloatSlider("BP Tweak", -0.1, 0.1, DEFAULT_PARAMS.bp_tweak, 0.01)
        self.wp_tweak_slider = FloatSlider("WP Tweak", -0.1, 0.1, DEFAULT_PARAMS.wp_tweak, 0.01)

        for slider in self.tone_sliders:
            slider.slider.valueChanged.connect(self.on_slider_change)

        # --- Black/White point buttons ---
        self.set_black_button = QPushButton("Set Black")
        self.set_white_button = QPushButton("Set White")
        self.auto_points_button = QPushButton("Auto")
        self.set_black_button.setCheckable(True)
        self.set_white_button.setCheckable(True)
        self.set_black_button.clicked.connect(lambda: self.toggle_pick("black"))
        self.set_white_button.clicked.connect(lambda: self.toggle_pick("white"))
        self.auto_points_button.clicked.connect(self.reset_points)

        self.overlay_button = QPushButton("Show analysis area")
        self.overlay_button.setCheckable(True)
        self.overlay_button.toggled.connect(self.view.set_overlay)

        self.readout_label = QLabel("Points in use: —")
        self.readout_label.setWordWrap(True)
        self.readout_label.setStyleSheet("font-family: monospace; font-size: 11px; color: #bbbbbb;")

        controls = self.build_controls()
        main = QWidget()
        layout = QHBoxLayout(main)
        layout.addWidget(self.view, 1)
        layout.addWidget(controls)
        self.setCentralWidget(main)

        if image_path:
            self.load_image(image_path)

    @property
    def tone_sliders(self):
        return (
            self.red_slider, self.green_slider, self.blue_slider,
            self.exposure_slider, self.contrast_slider, self.gamma_slider,
            self.bp_tweak_slider, self.wp_tweak_slider,
        )

    def build_controls(self) -> QWidget:
        panel = QFrame()
        panel.setFixedWidth(340)
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(panel)

        buttons = QHBoxLayout()
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.reset_button)
        layout.addLayout(buttons)

        layout.addSpacing(14)
        layout.addWidget(QLabel("Conversion Mode"))
        mode_row = QHBoxLayout()
        for mode_id, _ in MODES:
            mode_row.addWidget(self.mode_buttons[mode_id])
        layout.addLayout(mode_row)
        hint = QLabel("Switching mode resets picked B/W points.")
        hint.setStyleSheet("font-size: 10px; color: #888888;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(14)
        layout.addWidget(QLabel("Color Timing"))
        layout.addWidget(self.red_slider)
        layout.addWidget(self.green_slider)
        layout.addWidget(self.blue_slider)

        layout.addSpacing(14)
        layout.addWidget(QLabel("Print Grade"))
        layout.addWidget(self.exposure_slider)
        layout.addWidget(self.contrast_slider)
        layout.addWidget(self.gamma_slider)

        layout.addSpacing(14)
        layout.addWidget(QLabel("Black / White Points"))
        bw_row = QHBoxLayout()
        bw_row.addWidget(self.set_black_button)
        bw_row.addWidget(self.set_white_button)
        bw_row.addWidget(self.auto_points_button)
        layout.addLayout(bw_row)
        layout.addWidget(self.bp_tweak_slider)
        layout.addWidget(self.wp_tweak_slider)
        layout.addWidget(self.overlay_button)

        layout.addSpacing(10)
        layout.addWidget(self.readout_label)
        layout.addStretch(1)

        note = QLabel(
            "Math + UI clone of RapidRAW's modded negative_conversion.rs /\n"
            "NegativeConversionModal.tsx. Percentiles & center margin are globals\n"
            "at the top of core/rapidraw_negative.py."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 10px; color: #888888;")
        layout.addWidget(note)
        return panel

    # --- param plumbing -----------------------------------------------------

    def sync_params_from_sliders(self) -> None:
        self.params = replace(
            self.params,
            red_weight=self.red_slider.value(),
            green_weight=self.green_slider.value(),
            blue_weight=self.blue_slider.value(),
            exposure=self.exposure_slider.value(),
            contrast=self.contrast_slider.value(),
            gamma=self.gamma_slider.value(),
            bp_tweak=self.bp_tweak_slider.value(),
            wp_tweak=self.wp_tweak_slider.value(),
        )

    def on_slider_change(self) -> None:
        self.sync_params_from_sliders()
        self.schedule_preview()

    def on_mode_change(self, mode: str) -> None:
        if mode == self.params.mode:
            return
        # Working space changes -> picked B/W points are no longer valid (reset),
        # but tone controls + tweaks are kept (matches the TSX handleModeChange).
        self.params = replace(self.params, mode=mode, bp_override=None, wp_override=None)
        self.clear_pick()
        self.update_preview()

    def toggle_pick(self, which: str) -> None:
        self.pick_mode = None if self.pick_mode == which else which
        self.set_black_button.setChecked(self.pick_mode == "black")
        self.set_white_button.setChecked(self.pick_mode == "white")
        self.view.set_pick(self.pick_mode is not None)

    def clear_pick(self) -> None:
        self.pick_mode = None
        self.set_black_button.setChecked(False)
        self.set_white_button.setChecked(False)
        self.view.set_pick(False)

    def on_point_picked(self, nx: float, ny: float) -> None:
        if self.pick_mode is None or self.preview_base is None:
            return
        density = sample_point(self.preview_base, nx, ny, self.params.mode)
        if self.pick_mode == "black":
            self.params = replace(self.params, bp_override=density)
        else:
            self.params = replace(self.params, wp_override=density)
        self.clear_pick()
        self.update_preview()

    def reset_points(self) -> None:
        self.params = replace(self.params, bp_override=None, wp_override=None,
                              bp_tweak=0.0, wp_tweak=0.0)
        self.bp_tweak_slider.set_float_value(0.0)
        self.wp_tweak_slider.set_float_value(0.0)
        self.clear_pick()
        self.update_preview()

    # --- preview / IO -------------------------------------------------------

    def open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open negative scan", os.getcwd(),
            "Images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp *.dng *.nef *.cr2 *.cr3 "
            "*.arw *.orf *.rw2 *.raf *.pef);;All files (*)",
        )
        if path:
            self.load_image(path)

    def load_image(self, path: str) -> None:
        try:
            self.full_image = read_rgb01(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self.image_path = path
        self.preview_base = downscale_rgb(self.full_image)  # uses PREVIEW_MAX_DIM
        self.save_button.setEnabled(True)
        self.statusBar().showMessage(path)
        self.update_preview()

    def schedule_preview(self) -> None:
        self.preview_timer.start()

    def update_preview(self) -> None:
        if self.preview_base is None:
            return
        result = convert_negative_preview(self.preview_base, self.params)
        self.view.set_margin(result["center_margin"])
        self.view.set_pixmap(rgb01_to_pixmap(result["image"]))
        b, w = result["black_point"], result["white_point"]
        self.readout_label.setText(
            "Points in use  (negative [0,255])\n"
            f"  Black -> 0   : R {b[0]:3d}  G {b[1]:3d}  B {b[2]:3d}\n"
            f"  White -> 255 : R {w[0]:3d}  G {w[1]:3d}  B {w[2]:3d}"
        )

    def reset_params(self) -> None:
        self.params = DEFAULT_PARAMS
        self.red_slider.set_float_value(DEFAULT_PARAMS.red_weight)
        self.green_slider.set_float_value(DEFAULT_PARAMS.green_weight)
        self.blue_slider.set_float_value(DEFAULT_PARAMS.blue_weight)
        self.exposure_slider.set_float_value(DEFAULT_PARAMS.exposure)
        self.contrast_slider.set_float_value(DEFAULT_PARAMS.contrast)
        self.gamma_slider.set_float_value(DEFAULT_PARAMS.gamma)
        self.bp_tweak_slider.set_float_value(DEFAULT_PARAMS.bp_tweak)
        self.wp_tweak_slider.set_float_value(DEFAULT_PARAMS.wp_tweak)
        self.mode_buttons[DEFAULT_PARAMS.mode].setChecked(True)
        self.clear_pick()
        self.overlay_button.setChecked(False)
        self.update_preview()

    def save_image(self) -> None:
        if self.full_image is None or self.image_path is None:
            return
        source = Path(self.image_path)
        suggested = str(source.with_name(f"{source.stem}_Positive.tiff"))
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save converted TIFF", suggested, "TIFF (*.tif *.tiff)",
        )
        if not out_path:
            return
        try:
            result = convert_negative_like_rapidraw_save(self.full_image, self.params)
            write_rgb16_tiff(out_path, result)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.statusBar().showMessage(f"Saved {out_path}")


def main() -> int:
    app = QApplication(sys.argv)
    image_path = sys.argv[1] if len(sys.argv) > 1 else None
    win = NegativeConversionWindow(image_path)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

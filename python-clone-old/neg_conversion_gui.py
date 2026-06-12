from __future__ import annotations

import os
import sys
from pathlib import Path

import cv2
import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
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
    DEFAULT_PARAMS,
    NegativeConversionParams,
    convert_negative,
    convert_negative_like_rapidraw_save,
    downscale_rgb,
)


RAW_EXTS = {
    ".nef", ".cr2", ".cr3", ".arw", ".dng", ".orf", ".crw",
    ".rw2", ".raf", ".pef", ".srw", ".kdc", ".mrw", ".3fr", ".erf",
}


def read_rgb01(path: str) -> np.ndarray:
    ext = Path(path).suffix.lower()
    if ext in RAW_EXTS:
        return read_raw_image(path)
    img, _src_max = read_image(path)
    return img


def rgb01_to_pixmap(img: np.ndarray) -> QPixmap:
    rgb8 = np.clip(img * 255.0 + 0.5, 0, 255).astype(np.uint8)
    h, w = rgb8.shape[:2]
    qimg = QImage(rgb8.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


def write_rgb16_tiff(path: str, img: np.ndarray) -> None:
    out = np.clip(img * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
    out_bgr = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
    if not cv2.imwrite(path, out_bgr):
        raise OSError(f"cv2.imwrite failed: {path}")


class FloatSlider(QWidget):
    def __init__(
        self,
        label: str,
        min_value: float,
        max_value: float,
        default: float,
        step: float,
        decimals: int = 2,
        suffix: str = "",
        parent: QWidget | None = None,
    ) -> None:
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

    def update_label(self, _value: int | None = None) -> None:
        self.value_label.setText(f"{self.value():.{self.decimals}f}{self.suffix}")


class NegativeConversionWindow(QMainWindow):
    def __init__(self, image_path: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("RapidRAW Negative Conversion Clone")
        self.resize(1200, 800)

        self.image_path: str | None = None
        self.full_image: np.ndarray | None = None
        self.preview_base: np.ndarray | None = None
        self.preview_result: np.ndarray | None = None

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(80)
        self.preview_timer.timeout.connect(self.update_preview)

        self.image_label = QLabel("Open an RGB image or RAW scan")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(640, 480)
        self.image_label.setStyleSheet("background: #101010; color: #cccccc;")

        self.open_button = QPushButton("Open")
        self.save_button = QPushButton("Save TIFF")
        self.reset_button = QPushButton("Reset")
        self.save_button.setEnabled(False)

        self.open_button.clicked.connect(self.open_image)
        self.save_button.clicked.connect(self.save_image)
        self.reset_button.clicked.connect(self.reset_params)

        self.central_region_slider = FloatSlider(
            "Central Region",
            5.0,
            100.0,
            DEFAULT_PARAMS.central_region_percent,
            1.0,
            decimals=0,
            suffix="%",
        )
        self.low_percentile_slider = FloatSlider(
            "Low Percentile",
            0.0,
            5.0,
            DEFAULT_PARAMS.low_percentile,
            0.1,
            decimals=1,
            suffix="%",
        )
        self.high_percentile_slider = FloatSlider(
            "High Percentile",
            95.0,
            100.0,
            DEFAULT_PARAMS.high_percentile,
            0.1,
            decimals=1,
            suffix="%",
        )
        self.red_slider = FloatSlider("Red Weight", 0.5, 2.0, DEFAULT_PARAMS.red_weight, 0.01)
        self.green_slider = FloatSlider("Green Weight", 0.5, 2.0, DEFAULT_PARAMS.green_weight, 0.01)
        self.blue_slider = FloatSlider("Blue Weight", 0.5, 2.0, DEFAULT_PARAMS.blue_weight, 0.01)
        self.exposure_slider = FloatSlider("Exposure", -2.0, 2.0, DEFAULT_PARAMS.exposure, 0.05)
        self.contrast_slider = FloatSlider("Contrast", 0.5, 2.5, DEFAULT_PARAMS.contrast, 0.05)

        for slider in self.sliders:
            slider.slider.valueChanged.connect(self.schedule_preview)

        controls = self.build_controls()

        main = QWidget()
        layout = QHBoxLayout(main)
        layout.addWidget(self.image_label, 1)
        layout.addWidget(controls)
        self.setCentralWidget(main)

        if image_path:
            self.load_image(image_path)

    @property
    def sliders(self) -> tuple[FloatSlider, ...]:
        return (
            self.central_region_slider,
            self.low_percentile_slider,
            self.high_percentile_slider,
            self.red_slider,
            self.green_slider,
            self.blue_slider,
            self.exposure_slider,
            self.contrast_slider,
        )

    def build_controls(self) -> QWidget:
        panel = QFrame()
        panel.setFixedWidth(330)
        panel.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(panel)
        buttons = QHBoxLayout()
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.reset_button)
        layout.addLayout(buttons)

        layout.addSpacing(16)
        layout.addWidget(QLabel("Auto Analysis"))
        layout.addWidget(self.central_region_slider)
        layout.addWidget(self.low_percentile_slider)
        layout.addWidget(self.high_percentile_slider)

        layout.addSpacing(18)
        layout.addWidget(QLabel("Color Timing"))
        layout.addWidget(self.red_slider)
        layout.addWidget(self.green_slider)
        layout.addWidget(self.blue_slider)

        layout.addSpacing(18)
        layout.addWidget(QLabel("Print Grade"))
        layout.addWidget(self.exposure_slider)
        layout.addWidget(self.contrast_slider)
        layout.addStretch(1)

        note = QLabel("Math clone of RapidRAW's negative_conversion.rs.\nInput/output arrays are RGB float [0, 1].")
        note.setWordWrap(True)
        layout.addWidget(note)
        return panel

    def current_params(self) -> NegativeConversionParams:
        return NegativeConversionParams(
            central_region_percent=self.central_region_slider.value(),
            low_percentile=self.low_percentile_slider.value(),
            high_percentile=self.high_percentile_slider.value(),
            red_weight=self.red_slider.value(),
            green_weight=self.green_slider.value(),
            blue_weight=self.blue_slider.value(),
            exposure=self.exposure_slider.value(),
            contrast=self.contrast_slider.value(),
        )

    def open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open negative scan",
            os.getcwd(),
            "Images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp *.dng *.nef *.cr2 *.cr3 *.arw *.orf *.rw2 *.raf *.pef);;All files (*)",
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
        self.preview_base = downscale_rgb(self.full_image, 1080, 1080)
        self.save_button.setEnabled(True)
        self.statusBar().showMessage(path)
        self.update_preview()

    def schedule_preview(self) -> None:
        self.preview_timer.start()

    def update_preview(self) -> None:
        if self.preview_base is None:
            return
        self.preview_result = convert_negative(self.preview_base, self.current_params())
        self.update_pixmap()

    def update_pixmap(self) -> None:
        if self.preview_result is None:
            return
        pixmap = rgb01_to_pixmap(self.preview_result)
        scaled = pixmap.scaled(
            self.image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.update_pixmap()

    def reset_params(self) -> None:
        self.central_region_slider.set_float_value(DEFAULT_PARAMS.central_region_percent)
        self.low_percentile_slider.set_float_value(DEFAULT_PARAMS.low_percentile)
        self.high_percentile_slider.set_float_value(DEFAULT_PARAMS.high_percentile)
        self.red_slider.set_float_value(DEFAULT_PARAMS.red_weight)
        self.green_slider.set_float_value(DEFAULT_PARAMS.green_weight)
        self.blue_slider.set_float_value(DEFAULT_PARAMS.blue_weight)
        self.exposure_slider.set_float_value(DEFAULT_PARAMS.exposure)
        self.contrast_slider.set_float_value(DEFAULT_PARAMS.contrast)
        self.update_preview()

    def save_image(self) -> None:
        if self.full_image is None or self.image_path is None:
            return

        source = Path(self.image_path)
        suggested = str(source.with_name(f"{source.stem}_Positive.tiff"))
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save converted TIFF",
            suggested,
            "TIFF (*.tif *.tiff)",
        )
        if not out_path:
            return

        try:
            result = convert_negative_like_rapidraw_save(self.full_image, self.current_params())
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

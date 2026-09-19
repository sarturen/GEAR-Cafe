"""Camera image/ROI widget. It never opens an input."""

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget
from .config import FULL_ROI


class Preview(QWidget):
    roi_changed = Signal(dict)

    def __init__(self):
        super().__init__()
        self.image = None
        self.roi = dict(FULL_ROI)
        self.editable = True
        self._start = None
        self._drag = None
        self.setMinimumSize(320, 180)
        self.setMouseTracking(True)

    def set_frame(self, image):
        self.image = image
        self.update()

    def set_roi(self, roi):
        self.roi = dict(roi)
        self.update()

    def image_rect(self):
        if self.image is None or self.image.isNull():
            return QRectF()
        scale = min(
            self.width() / self.image.width(), self.height() / self.image.height()
        )
        width, height = self.image.width() * scale, self.image.height() * scale
        return QRectF(
            (self.width() - width) / 2, (self.height() - height) / 2, width, height
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#182230"))
        rect = self.image_rect()
        if rect.isEmpty():
            painter.setPen(QColor("#d5dfe9"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "先选择输入并打开预览"
            )
            return
        painter.drawImage(rect, self.image)
        r = self.roi
        roi_rect = (
            self._drag
            if self._drag is not None
            else QRectF(
                rect.x() + r["x"] * rect.width(),
                rect.y() + r["y"] * rect.height(),
                r["width"] * rect.width(),
                r["height"] * rect.height(),
            )
        )
        painter.setPen(QPen(QColor("#22db95"), 2))
        painter.drawRect(roi_rect)
        painter.end()

    def mousePressEvent(self, event):
        if (
            self.editable
            and event.button() == Qt.MouseButton.LeftButton
            and self.image_rect().contains(event.position())
        ):
            self._start = event.position()
            self._drag = QRectF(self._start, self._start)

    def mouseMoveEvent(self, event):
        if self._start is not None:
            self._drag = (
                QRectF(self._start, event.position())
                .normalized()
                .intersected(self.image_rect())
            )
            self.update()

    def mouseReleaseEvent(self, event):
        if self._start is None:
            return
        rect = self.image_rect()
        selection = QRectF(self._start, event.position()).normalized().intersected(rect)
        self._start, self._drag = None, None
        if self.editable and selection.width() >= 2 and selection.height() >= 2:
            self.roi = {
                "x": (selection.x() - rect.x()) / rect.width(),
                "y": (selection.y() - rect.y()) / rect.height(),
                "width": selection.width() / rect.width(),
                "height": selection.height() / rect.height(),
            }
            self.roi_changed.emit(dict(self.roi))
        self.update()

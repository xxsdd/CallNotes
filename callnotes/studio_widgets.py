"""Native studio views. Wave bars represent measured capture levels only."""
from collections import deque

from PySide6.QtCore import Qt, QRectF, QSize, QPointF
from PySide6.QtGui import QColor, QFont, QPainter, QPolygonF, QPixmap, QIcon
from PySide6.QtWidgets import QWidget, QStyledItemDelegate, QStyle, QComboBox

from .storage import stamp
from . import theme


class StudioComboBox(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        # Draw a readable disclosure arrow independently of Windows' native
        # black arrow assets and font-symbol availability.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        x, y = self.width() - 13, self.height() / 2
        painter.fillRect(QRectF(x - 8, y - 8, 16, 16), QColor(self.property('arrowBackground') or theme.color('#1d2633')))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor((self.property('arrowColor') or theme.MUTED) if self.isEnabled() else theme.color('#718097')))
        painter.drawPolygon(QPolygonF([QPointF(x - 4, y - 2), QPointF(x + 4, y - 2), QPointF(x, y + 3)]))


def playback_icon(playing=False):
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(theme.TEXT))
    if playing:
        painter.drawRoundedRect(QRectF(3, 3, 3, 10), 1, 1)
        painter.drawRoundedRect(QRectF(10, 3, 3, 10), 1, 1)
    else:
        painter.drawPolygon(QPolygonF([QPointF(4, 2), QPointF(13, 8), QPointF(4, 14)]))
    painter.end()
    return QIcon(pixmap)


class DualWaveform(QWidget):
    def __init__(self, parent=None, compact=False):
        super().__init__(parent)
        self.samples = deque(maxlen=240)
        self.elapsed = 0.
        self.remote_label = '对方 · 电脑声音'
        self.compact = compact
        self.setMinimumSize(200 if compact else 250, 72 if compact else 152)
        self.setAccessibleName('我和对方的实时音量波形，最近 30 秒')

    def reset(self):
        self.samples.clear()
        self.elapsed = 0.
        self.update()

    def push(self, elapsed, levels):
        self.elapsed = max(0., float(elapsed))
        self.samples.append((self.elapsed, max(0., min(1., levels.get('mic', 0))),
                             max(0., min(1., levels.get('system', 0)))))
        while self.samples and self.samples[0][0] < self.elapsed - 31:
            self.samples.popleft()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(QFont('Microsoft YaHei UI', 9))
        if self.compact:
            for row, label, color in [(0, '我 · 麦克风', theme.ACCENT),
                                      (1, self.remote_label, theme.color('#8098bb'))]:
                top = row * 34
                painter.setPen(QColor(theme.MUTED))
                short_label = painter.fontMetrics().elidedText(label, Qt.ElideRight, 118)
                painter.drawText(QRectF(0, top, 122, 26), Qt.AlignVCenter, short_label)
                width = max(1, self.width() - 130)
                columns = max(1, int(width / 7))
                bins = {}
                for when, mic, remote in self.samples:
                    if when < self.elapsed - 12:
                        continue
                    column = min(columns - 1, max(0, int((when - max(0., self.elapsed - 12)) / 12 * columns)))
                    bins[column] = max(bins.get(column, 0.), (mic, remote)[row])
                painter.setPen(Qt.NoPen)
                for column in range(columns):
                    level = bins.get(column, 0.)
                    ink = QColor(color)
                    ink.setAlpha(210 if level > .012 else 55)
                    painter.setBrush(ink)
                    height = 2 if level <= .012 else 3 + level * 21
                    painter.drawRoundedRect(QRectF(130 + column * width / columns, top + 13 - height / 2, 3, height), 1.5, 1.5)
            return
        left, width = 4., max(1., self.width() - 12.)
        start = max(0., self.elapsed - 30)
        end = max(30., self.elapsed)
        for row, label, color in [(0, '我 · 麦克风', theme.ACCENT),
                                  (1, self.remote_label, theme.color('#8098bb'))]:
            top, middle = row * 60, row * 60 + 35
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(QRectF(left, top, width, 21), label)
            # Quiet rails make an idle or disconnected source visibly different
            # from actual audio. There is no decorative/random wave animation.
            quiet = QColor(color)
            quiet.setAlpha(55)
            painter.setPen(Qt.NoPen)
            painter.setBrush(quiet)
            columns = max(1, int(width / 9))
            for column in range(columns):
                painter.drawEllipse(QRectF(left + column * width / columns, middle - 1, 2, 2))
            # Aggregate only for display: keep the highest observed level in each
            # 9px column so short peaks survive resizing and the image stays light.
            bins = {}
            for when, mic, remote in self.samples:
                if not start <= when <= end:
                    continue
                index = min(columns - 1, int((when - start) / 30 * columns))
                bins[index] = max(bins.get(index, 0), (mic, remote)[row])
            painter.setBrush(QColor(color))
            for index, level in bins.items():
                if level < .012:
                    continue
                height = 3 + level * 27
                painter.drawRoundedRect(QRectF(left + index * width / columns, middle - height / 2,
                                               3, height), 1.5, 1.5)
        painter.setPen(QColor(theme.MUTED))
        for value, alignment in [(start, Qt.AlignLeft), ((start + end) / 2, Qt.AlignHCenter), (end, Qt.AlignRight)]:
            painter.drawText(QRectF(left, 126, width, 20), alignment | Qt.AlignVCenter, stamp(value))


class HistoryDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        record = index.data(Qt.UserRole + 1)
        if not record:
            return super().paint(painter, option, index)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(0, 3, -1, -3)
        if option.state & QStyle.State_Selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.SELECTION))
            painter.drawRoundedRect(rect, 8, 8)
        x, width = rect.x() + 13, rect.width() - 26
        painter.setPen(QColor(theme.TEXT))
        painter.setFont(QFont('Microsoft YaHei UI', 10, QFont.Medium))
        title = painter.fontMetrics().elidedText(record['title'], Qt.ElideRight, width)
        painter.drawText(QRectF(x, rect.y() + 9, width, 23), title)
        painter.setPen(QColor(theme.MUTED))
        painter.setFont(QFont('Microsoft YaHei UI', 8))
        painter.drawText(QRectF(x, rect.y() + 34, width, 20), record['created'][5:16].replace('T', '  ') + '  ·  ' + stamp(record['duration']))
        painter.setPen(QColor(theme.ACCENT if record['status'] in ('recording', 'transcribing', 'analyzing') else theme.MUTED))
        painter.drawText(QRectF(x, rect.y() + 55, width, 18), record.get('status_label', ''))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(100, 88)

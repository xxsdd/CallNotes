"""Report typography and a local viewer for the transcript behind a citation."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QTextBrowser, QVBoxLayout)

from .storage import stamp
from .text_normalization import simplified
from .markdown_display import readable_markdown
from .reading import reading_options


def reading_colors(mode):
    if mode == 'day':
        return dict(bg='#ffffff', body='#20242a', strong='#000000', muted='#5e6878',
                    border='#d9e0e8', accent='#235da8', highlight='#edf3fb')
    return dict(bg='#273140', body='#cbd5e1', strong='#ffffff', muted='#a6b5c9',
                border='#46566d', accent='#b9d4ff', highlight='#35445b')


class AnalysisBrowser(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.reading_mode = 'night'
        self.reading = getattr(parent.window(), 'reading', reading_options({})) if parent else reading_options({})
        self.setObjectName('analysisText')
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setMinimumWidth(0)
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(16)
        self.document().setDefaultFont(font)
        self.document().setDocumentMargin(18)

    def set_reading_style(self, mode):
        self.reading_mode = mode
        position = self.verticalScrollBar().value()
        previous = self.blockSignals(True)
        try:
            self.typeset()
        finally:
            self.blockSignals(previous)
        self.verticalScrollBar().setValue(position)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_reading_width()

    def update_reading_width(self):
        margin = max(0, (self.width() - self.reading['width'] - 40) // 2)
        if self.viewportMargins().left() != margin:
            self.setViewportMargins(margin, 0, margin, 0)

    def loadResource(self, kind, name):
        # Model output is text, never permission to fetch images or local files.
        return None

    def _set(self, method, text):
        previous = self.blockSignals(True)
        try:
            method(text)
            self.typeset()
        finally:
            self.blockSignals(previous)
        if not previous:
            self.textChanged.emit()

    def setMarkdown(self, text):
        self._set(super().setMarkdown, readable_markdown(simplified(text)))

    def setHtml(self, text):
        self._set(super().setHtml, text)

    def setPlainText(self, text):
        self._set(super().setPlainText, simplified(text))

    def typeset(self):
        colors = reading_colors(self.reading_mode)
        size = self.reading['size'] * .75
        self.update_reading_width()
        block = self.document().begin()
        while block.isValid():
            cursor = QTextCursor(block)
            layout = block.blockFormat()
            heading = layout.headingLevel()
            layout.setLineHeight(float(self.reading['spacing']), QTextBlockFormat.ProportionalHeight.value)
            layout.setTopMargin(25 if heading else 3)
            layout.setBottomMargin(14 if heading else 17)
            cursor.setBlockFormat(layout)
            cursor.select(QTextCursor.BlockUnderCursor)
            text = QTextCharFormat()
            text.setFontPointSize(size + (5 if heading == 1 else 4 if heading == 2 else 2 if heading else 0))
            text.setForeground(QColor(colors['strong'] if heading else colors['body']))
            text.setBackground(QColor('transparent'))
            if heading:
                text.setFontWeight(700)
            cursor.mergeCharFormat(text)
            block = block.next()
        block = self.document().begin()
        while block.isValid():
            it, fragments = block.begin(), []
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    if fmt.anchorHref().startswith('source:'):
                        fragments.append((fragment.position(), fragment.length(), 'source'))
                    elif not block.blockFormat().headingLevel() and fmt.fontWeight() >= 700:
                        fragments.append((fragment.position(), fragment.length(), 'strong'))
                it += 1
            for start, length, kind in fragments:
                cursor = QTextCursor(self.document())
                cursor.setPosition(start)
                cursor.setPosition(start + length, QTextCursor.KeepAnchor)
                citation = QTextCharFormat()
                citation.setForeground(QColor(colors['accent'] if kind == 'source' else colors['strong']))
                citation.setBackground(QColor(colors['highlight'] if kind == 'source' else 'transparent'))
                if kind == 'source':
                    citation.setFontPointSize(max(10, size - 1))
                    citation.setFontUnderline(False)
                    citation.setToolTip('查看对应的说话方、时间和文字稿原文')
                else:
                    citation.setFontWeight(700)
                    citation.setFontPointSize(size)
                cursor.mergeCharFormat(citation)
            block = block.next()


class SourceDialog(QDialog):
    def __init__(self, sources, revision, old, locate, parent=None, missing=0, selected_index=None):
        super().__init__(parent)
        self.sources = sources
        self.setWindowTitle('引用来源')
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(16)
        title = QLabel('这条分析依据了哪些原话')
        title.setObjectName('headline')
        title.setWordWrap(True)
        layout.addWidget(title)
        version = f'文字稿 v{revision}' + (' · 回答引用的是旧版本' if old else ' · 当前版本')
        if missing:
            version += f' · 另有 {missing} 处来源未找到'
        meta = QLabel(version + '\n以下为文字稿原文，可能存在语音识别误差。')
        meta.setObjectName('subtitle')
        meta.setWordWrap(True)
        layout.addWidget(meta)
        content = QHBoxLayout()
        content.setSpacing(18)
        self.choose = QListWidget()
        self.choose.setObjectName('sourceList')
        self.choose.setAccessibleName('本组引用来源，点击切换原话')
        self.choose.setMinimumWidth(155)
        self.choose.setMaximumWidth(215)
        self.choose.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.choose.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.choose.setWordWrap(True)
        selected_row = 0
        for number, (index, segment) in enumerate(sources, 1):
            qualifier = ' · 归属待确认' if segment.get('speaker_uncertain') else ' · 归属参考' if segment.get('speaker_inferred') else ''
            item = QListWidgetItem(f'来源 {number}　{segment["speaker"]}{qualifier}\n{stamp(segment["start"])} — {stamp(segment["end"])}')
            item.setData(Qt.UserRole, index)
            item.setToolTip(simplified(segment['text']))
            self.choose.addItem(item)
            if index == selected_index:
                selected_row = number - 1
        content.addWidget(self.choose, 1)
        self.quote = AnalysisBrowser()
        content.addWidget(self.quote, 3)
        layout.addLayout(content, 1)
        def change_source(row):
            if row < 0:
                return
            self.quote.setPlainText(sources[row][1]['text'])
            self.quote.verticalScrollBar().setValue(0)
            if hasattr(self, 'status'):
                self.status.setText('点击后高亮文字稿并播放，来源窗口保持打开。')
        self.choose.currentRowChanged.connect(change_source)
        self.choose.setCurrentRow(selected_row)
        actions = QHBoxLayout()
        close = QPushButton('关闭')
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        actions.addStretch()

        def go():
            index = self.choose.currentItem().data(Qt.UserRole)
            locate(revision, index, True)

        self.status = QLabel('点击后高亮文字稿并播放，来源窗口保持打开。')
        self.status.setObjectName('subtitle')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        play = QPushButton('定位并回听')
        play.setObjectName('blue')
        play.clicked.connect(go)
        actions.addWidget(play)
        layout.addLayout(actions)

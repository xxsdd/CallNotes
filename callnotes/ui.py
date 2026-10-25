from __future__ import annotations

import html
import os
import re
import time
from functools import reduce
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QThreadPool, QUrl, QSize, QRectF, QEvent
from PySide6.QtGui import QColor, QDesktopServices, QFont, QTextDocument, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QTextEdit, QAbstractSpinBox, QPushButton, QSlider, QSplitter,
    QStackedWidget, QTabWidget, QTextBrowser, QVBoxLayout, QWidget, QStyledItemDelegate, QStyle, QSpinBox,
    QMenu, QToolButton, QCheckBox)

from .audio import Recorder, devices
from .process_audio import enumerate_voice_apps, support_error
from .codex_client import CodexClient, REPORT, is_report, start_login
from .runtime import bundled_worker, default_python
from .jobs import Job, Transcriber, LiveTranscriber
from .storage import DEFAULT_ROOT, Store, atomic_json, read_json, stamp, transcript_text
from .turns import speaker_turns, join_text
from .text_normalization import simplified
from .studio_widgets import DualWaveform, HistoryDelegate, StudioComboBox, playback_icon
from . import theme
from .analysis_view import AnalysisBrowser, SourceDialog
from .citations import cite_markdown, parse_source_target
from .analysis_templates import TEMPLATES, report_prompt, question_prompt, question_label
from .reading import reading_options
from .release import VERSION, changelog_text

STATUS = {'recording': '录制中', 'saved': '待生成文字稿', 'transcribing': '正在转录',
          'ready': '文字稿就绪', 'analyzing': 'Codex 分析中', 'done': '分析完成'}
EFFORT = {'none': '无', 'minimal': '最少', 'low': '低', 'medium': '中', 'high': '高',
          'xhigh': '很高', 'max': '最高', 'ultra': '极高'}
PLAYBACK_RATES = (.5, .75, 1., 1.25, 1.5, 1.75, 2.)


class TurnDelegate(QStyledItemDelegate):
    """Readable, wrapping turn paragraphs on both live and saved timelines."""
    def document(self, index, width):
        turn = index.data(Qt.UserRole + 2)
        doc = QTextDocument()
        reading = getattr(self.parent().window(), 'reading', reading_options({}))
        doc.setDefaultFont(QFont('Microsoft YaHei UI', 11))
        doc.setTextWidth(max(150, min(width - 92, reading['width'])))
        if not turn:
            text = html.escape(index.data(Qt.DisplayRole) or '').replace('\n', '<br>')
            doc.setHtml(f'<p style="font-size:15px;color:{theme.MUTED};line-height:165%">{text}</p>')
            return doc
        name = html.escape(turn['speaker'])
        tint = theme.ACCENT if turn['track'] == 'mic' else theme.TEXT
        state = ' · 正在说 · 临时字幕' if turn.get('partial') else ''
        if turn.get('echo_suspected'):
            state += ' · 疑似串音，请核对'
        elif turn.get('contains_unresolved') and turn.get('speaker_id') != 'unknown':
            state += ' · 划线处归属待核对'
        elif turn.get('speaker_uncertain'):
            state += ' · 重叠或归属不清'
        elif turn.get('review'):
            state += ' · 请核对'
        elif turn.get('speaker_inferred'):
            state += ' · 含归属参考'
        raw = turn['text']
        uncertain = turn.get('uncertain_ranges', [])
        focus = index.data(Qt.UserRole + 3)
        highlight = focus.get('range') if focus else None
        boundaries = sorted({0, len(raw)} | {max(0, min(len(raw), x)) for pair in uncertain + ([highlight] if highlight else []) for x in pair})
        parts = []
        for start, end in zip(boundaries, boundaries[1:]):
            style = ''
            if any(a <= start < b for a, b in uncertain):
                style += f'text-decoration:underline;color:{theme.MUTED};'
            if highlight and highlight[0] <= start < highlight[1]:
                mark = '#ffe29a' if theme.MODE == 'day' else '#695226'
                style += f'background-color:{mark};font-weight:700;'
            parts.append(f'<span style="{style}">{html.escape(raw[start:end])}</span>')
        paragraph = ''.join(parts).replace('\n', '<br>')
        doc.setHtml(f'<div style="font-size:13px;color:{theme.MUTED}"><span style="font-weight:600;color:{tint}">{name}</span>'
                    f'　{stamp(turn["start"])} — {stamp(turn["end"])}{state}</div>'
                    f'<p style="font-size:{reading["size"]}px;line-height:{reading["spacing"]}%;color:{theme.TEXT};margin-top:12px;margin-bottom:12px">{paragraph}</p>')
        return doc

    def paint(self, painter, option, index):
        painter.save()
        selected = bool(option.state & QStyle.State_Selected)
        if selected:
            painter.fillRect(option.rect, QColor(theme.color('#303d50')))
        if index.data(Qt.UserRole + 3):
            painter.fillRect(option.rect, QColor(theme.SELECTION))
            painter.fillRect(option.rect.adjusted(0, 6, -option.rect.width() + 4, -6), QColor(theme.ACCENT))
        turn = index.data(Qt.UserRole + 2)
        if turn:
            if turn.get('partial'):
                painter.fillRect(option.rect.adjusted(4, 13, -option.rect.width() + 6, -13), QColor(theme.ACCENT))
            avatar = QRectF(option.rect.x() + 20, option.rect.y() + 15, 26, 26)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.SELECTION if turn['track'] == 'mic' else theme.color('#2c3747')))
            painter.drawRoundedRect(avatar, 6, 6)
            painter.setPen(QColor(theme.ACCENT if turn['track'] == 'mic' else theme.MUTED))
            painter.setFont(QFont('Microsoft YaHei UI', 9))
            identity = turn.get('speaker_id', '')
            badge = ('?' if identity == 'unknown' else identity.rsplit('-', 1)[-1] if '-speaker-' in identity else turn['speaker'][:1]) if identity or turn['track'] == 'imported' else '我' if turn['track'] == 'mic' else '对'
            painter.drawText(avatar, Qt.AlignCenter, badge or '音')
        doc = self.document(index, option.rect.width())
        painter.translate(option.rect.x() + (61 if turn else 20), option.rect.y() + 14)
        doc.drawContents(painter)
        painter.restore()

    def sizeHint(self, option, index):
        width = option.widget.viewport().width() if option.widget else option.rect.width()
        doc = self.document(index, max(260, width))
        return QSize(200, int(doc.size().height()) + 30)


def button(text, handler, kind=None):
    result = QPushButton(text)
    if kind:
        result.setObjectName(kind)
    result.clicked.connect(handler)
    return result


class MainWindow(QMainWindow):
    def __init__(self, store=None, prewarm=True):
        super().__init__()
        bootstrap = read_json(DEFAULT_ROOT / 'settings.json', {})
        self.store = store or Store(bootstrap.get('data_root', str(DEFAULT_ROOT)))
        self.store.recover()
        self.reading = reading_options(self.store.settings())
        self.codex = CodexClient(self.store.root / 'analysis-workspace')
        self.transcriber = Transcriber()
        self.live = LiveTranscriber()
        self.live_segments = []
        self.live_partials = {}
        self.closing = False
        self.live_failed = False
        self.warming = False
        self.prewarm_enabled = prewarm
        self.source_dialog = None
        self.version_dialog = None
        self.reading_dialog = None
        self.changelog_dialog = None
        self.last_segment_deletion = None
        self.displayed_revision = 0
        self.pool = QThreadPool(self)
        self.jobs = []
        self.recorder = None
        self.recording_id = None
        self.current_id = None
        self.busy_id = None
        self.busy_type = None
        self.trash = False
        self.models = []
        self.segments = []
        self.streamed = ''
        self.players, self.outputs, self.offsets = {}, {}, {}
        self.playing = False
        self.play_anchor = None
        self.play_position = 0.
        saved_rate = self.store.settings().get('playback_rate', 1.)
        self.playback_rate = saved_rate if saved_rate in PLAYBACK_RATES else 1.
        self.setWindowTitle('通话笺 · CallNotes')
        self.resize(1360, 880)
        self.setMinimumSize(1020, 680)
        self.build()
        QApplication.instance().installEventFilter(self)
        self.apply_theme()
        self.refresh_history()
        self.refresh_devices()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(150)
        QTimer.singleShot(400, self.load_models)
        if prewarm:
            QTimer.singleShot(100, self.prepare_captions)

    def build(self):
        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(236)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 26, 18, 20)
        brand = QLabel('通话笺')
        brand.setObjectName('brand')
        side.addWidget(brand)
        sub = QLabel('CallNotes Studio')
        sub.setObjectName('subtitle')
        side.addWidget(sub)
        side.addSpacing(20)
        self.new_button = button('＋  新录音', self.new_record, 'primary')
        side.addWidget(self.new_button)
        self.import_button = button('导入音视频', self.import_media)
        side.addWidget(self.import_button)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索标题或文字稿…')
        self.search.textChanged.connect(self.refresh_history)
        side.addWidget(self.search)
        self.history = QListWidget()
        self.history.setObjectName('history')
        self.history.setItemDelegate(HistoryDelegate(self.history))
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history.currentItemChanged.connect(self.selected_record)
        self.history.itemClicked.connect(self.selected_record)
        self.history.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self.history_context_menu)
        self.history.setToolTip('右键记录：重命名、导出、版本记录、移入回收区')
        side.addWidget(self.history, 1)
        self.changelog_button = button('更新日志', self.show_changelog, 'quiet')
        self.changelog_button.setToolTip('应用版本 ' + VERSION)
        side.addWidget(self.changelog_button)
        self.trash_button = button('回收区', self.toggle_trash)
        side.addWidget(self.trash_button)
        self.theme_box = StudioComboBox()
        self.theme_box.setObjectName('appTheme')
        self.theme_box.addItem('日间模式', 'day')
        self.theme_box.addItem('夜间模式', 'night')
        side.addWidget(self.theme_box)
        self.theme_box.currentIndexChanged.connect(lambda: self.set_theme(self.theme_box.currentData()))
        side.addWidget(button('设置', self.settings_dialog))
        self.location = QLabel('双路音频 · 本地保存')
        self.location.setObjectName('subtitle')
        self.location.setWordWrap(True)
        side.addWidget(self.location)
        layout.addWidget(sidebar)
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(30, 26, 30, 20)
        main_layout.setSpacing(14)
        top = QHBoxLayout()
        self.title = QLabel('新录音')
        self.title.setObjectName('headline')
        self.title.setWordWrap(True)
        top.addWidget(self.title, 1)
        self.ai_button = QPushButton('Codex 分析')
        self.ai_button.setObjectName('primary')
        self.ai_button.setMinimumSize(156, 46)
        self.ai_button.setCheckable(True)
        self.ai_button.toggled.connect(self.toggle_ai_panel)
        top.addWidget(self.ai_button)
        main_layout.addLayout(top)
        self.meta_label = QLabel('本地录音与实时文字稿')
        self.meta_label.setObjectName('subtitle')
        meta_row = QHBoxLayout()
        meta_row.addWidget(self.meta_label, 1)
        main_layout.addLayout(meta_row)
        self.notice = QLabel('准备就绪，点击“开始录制”。')
        self.notice.setObjectName('notice')
        self.notice.setWordWrap(True)
        self.notice.hide()
        main_layout.addWidget(self.notice)
        self.pages = QStackedWidget()
        self.record_page = self.build_recording()
        self.pages.addWidget(self.record_page)
        self.review_page = self.build_review()
        self.pages.addWidget(self.review_page)
        main_layout.addWidget(self.pages, 1)
        layout.addWidget(main, 1)
        self.setCentralWidget(body)
        self.set_record_controls(False)
        self.enable_actions()

    def build_recording(self):
        page = QWidget()
        content = QVBoxLayout(page)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(14)
        # These controls belong to Settings. Keep their state alive between
        # visits so the recorder always uses exactly the saved selections.
        self.audio_settings = QWidget(self)
        self.audio_settings.hide()
        audio_layout = QVBoxLayout(self.audio_settings)
        audio_layout.setContentsMargins(0, 18, 0, 0)
        audio_layout.setSpacing(18)
        intro = QLabel('选择要保存的两路声音。设置会用于下一次录音。')
        intro.setObjectName('subtitle')
        intro.setWordWrap(True)
        audio_layout.addWidget(intro)
        self.capture_mode = StudioComboBox()
        self.capture_mode.setAccessibleName('录音范围')
        self.capture_mode.addItem('麦克风 + 全部电脑声音', 'all')
        self.capture_mode.addItem('麦克风 + 指定通话软件', 'process')
        selected_mode = self.store.settings().get('capture_mode', 'all')
        self.capture_mode.setCurrentIndex(max(0, self.capture_mode.findData(selected_mode)))
        self.device_panel = self.audio_settings
        selectors = self.device_selectors = QFormLayout()
        selectors.setVerticalSpacing(16)
        selectors.addRow('录音范围', self.capture_mode)
        self.mic_box, self.system_box, self.app_box = StudioComboBox(), StudioComboBox(), StudioComboBox()
        for combo in (self.mic_box, self.system_box, self.app_box):
            combo.setMinimumWidth(0)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
            combo.currentTextChanged.connect(self.update_device_summary)
        selectors.addRow('我的麦克风', self.mic_box)
        selectors.addRow('电脑播放声音', self.system_box)
        selectors.addRow('通话软件', self.app_box)
        audio_layout.addLayout(selectors)
        self.refresh_device_button = button('刷新设备与软件', self.refresh_devices)
        audio_layout.addWidget(self.refresh_device_button, 0, Qt.AlignLeft)
        self.capture_hint = QLabel()
        self.capture_hint.setObjectName('subtitle')
        self.capture_hint.setWordWrap(True)
        audio_layout.addWidget(self.capture_hint)
        audio_layout.addStretch()
        self.capture_mode.currentIndexChanged.connect(self.update_capture_mode)
        heading = QHBoxLayout()
        heading_label = QLabel('实时文字稿')
        heading_label.setObjectName('sectionTitle')
        heading.addWidget(heading_label)
        heading.addStretch()
        self.caption_status = QLabel('按发言分段')
        self.caption_status.setObjectName('subtitle')
        heading.addWidget(self.caption_status)
        content.addLayout(heading)
        self.live_canvas = QStackedWidget()
        self.live_empty = QWidget()
        empty = QVBoxLayout(self.live_empty)
        empty.setContentsMargins(24, 24, 24, 24)
        empty.setSpacing(14)
        empty.addStretch(4)
        self.empty_title = QLabel('准备好记录这次对话')
        self.empty_title.setObjectName('emptyTitle')
        self.empty_title.setAlignment(Qt.AlignCenter)
        empty.addWidget(self.empty_title)
        self.empty_note = QLabel('点击下方开始录制，双方字幕会逐步出现在这里。')
        self.empty_note.setObjectName('emptyNote')
        self.empty_note.setWordWrap(True)
        self.empty_note.setAlignment(Qt.AlignCenter)
        empty.addWidget(self.empty_note)
        empty.addStretch(5)
        self.live_canvas.addWidget(self.live_empty)
        self.live_timeline = QListWidget()
        self.live_timeline.setObjectName('timeline')
        self.live_timeline.setItemDelegate(TurnDelegate(self.live_timeline))
        self.live_timeline.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.live_timeline.setResizeMode(QListWidget.Adjust)
        self.live_timeline.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.live_canvas.addWidget(self.live_timeline)
        content.addWidget(self.live_canvas, 1)
        source_row = QHBoxLayout()
        self.device_summary = QLabel('声音来源：我 + 电脑声音')
        self.device_summary.setObjectName('subtitle')
        source_row.addWidget(self.device_summary, 1)
        self.audio_settings_button = button('录音设置', self.settings_dialog, 'quiet')
        source_row.addWidget(self.audio_settings_button)
        content.addLayout(source_row)
        desk = QFrame()
        desk.setObjectName('recordBar')
        desk_layout = QHBoxLayout(desk)
        desk_layout.setContentsMargins(22, 16, 22, 16)
        desk_layout.setSpacing(18)
        controls = QWidget()
        controls.setFixedWidth(155)
        line = QVBoxLayout(controls)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)
        self.pill = QLabel('准备录音')
        self.pill.setObjectName('pill')
        line.addWidget(self.pill)
        self.clock = QLabel('00:00:00')
        self.clock.setObjectName('clock')
        line.addWidget(self.clock)
        desk_layout.addWidget(controls)
        self.waveform = DualWaveform(compact=True)
        desk_layout.addWidget(self.waveform, 1)
        self.start_button = button('●  开始录制', self.start_recording, 'primary')
        self.stop_button = button('■  停止并保存', lambda: self.stop_recording(), 'danger')
        self.pause_record_button = button('暂停录音', self.toggle_record_pause)
        for action in (self.start_button, self.pause_record_button, self.stop_button):
            action.setMinimumSize(100 if action is self.pause_record_button else 146, 48)
            desk_layout.addWidget(action)
        content.addWidget(desk)
        footer = QHBoxLayout()
        hint = QLabel('音频与文字稿保存在本机')
        hint.setObjectName('subtitle')
        footer.addWidget(hint)
        footer.addStretch()
        continued = QLabel('最小化后继续录音')
        continued.setObjectName('subtitle')
        footer.addWidget(continued)
        content.addLayout(footer)
        return page

    def update_capture_mode(self, *args):
        mode = self.capture_mode.currentData()
        process = mode == 'process'
        self.device_selectors.setRowVisible(self.system_box, not process)
        self.device_selectors.setRowVisible(self.app_box, process)
        if not getattr(self, 'editing_audio_settings', False):
            self.store.set_settings(capture_mode=mode)
        self.capture_hint.setText((support_error() or
            '先接通语音再开始录制。仅录所选软件的声音，排除浏览器等其他程序；所选软件内的提示音、视频声音也会录入。')
            if process else '电脑所选播放设备上的声音都会录入，包括通话、浏览器和其他程序。')
        self.update_device_summary()

    def update_device_summary(self, *args):
        process = self.capture_mode.currentData() == 'process'
        target = self.app_box.currentText() if process else self.system_box.currentText()
        if not hasattr(self, 'device_summary'):
            return
        app = self.app_box.currentData() if process else None
        short = app['name'] if app else '通话软件' if process else '电脑声音'
        self.device_summary.setText('声音来源：我 + ' + short)
        self.device_summary.setToolTip(f'我：{self.mic_box.currentText() or "未选择"}\n对方：{target or "未选择"}')
        if hasattr(self, 'waveform'):
            app = self.app_box.currentData() if process else None
            self.waveform.remote_label = '对方 · ' + (app['name'] if app else '通话软件' if process else '电脑声音')
            self.waveform.update()

    def build_review(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        label = QLabel('完整文字稿')
        label.setObjectName('sectionTitle')
        toolbar.addWidget(label)
        self.retry_button = button('生成文字稿', self.transcribe_current, 'blue')
        self.retry_button.setObjectName('quiet')
        toolbar.addWidget(self.retry_button)
        self.edit_button = button('修改选中段落', self.edit_segment)
        toolbar.addWidget(self.edit_button)
        self.region_button = button('重新识别选中段', self.transcribe_region)
        toolbar.addWidget(self.region_button)
        self.speakers_button = button('区分说话人', self.speakers_dialog)
        toolbar.addWidget(self.speakers_button)
        self.segment_actions_button = QToolButton()
        self.segment_actions_button.setText('更多')
        self.segment_actions_button.setPopupMode(QToolButton.InstantPopup)
        segment_menu = QMenu(self.segment_actions_button)
        self.remove_segments_action = segment_menu.addAction('删除选中段落')
        self.remove_segments_action.triggered.connect(self.delete_selected_segments)
        self.segment_actions_button.setMenu(segment_menu)
        self.segment_actions_button.setToolTip('删除不需要的段落；按 Ctrl / Shift 可选多段')
        toolbar.addWidget(self.segment_actions_button)
        self.undo_transcript_button = button('撤销删除', self.undo_segment_deletion, 'quiet')
        self.undo_transcript_button.hide()
        toolbar.addWidget(self.undo_transcript_button)
        toolbar.addStretch()
        self.filter = QLineEdit()
        self.filter.setMaximumWidth(250)
        self.filter.setPlaceholderText('查找本次通话…')
        self.filter.textChanged.connect(self.render_segments)
        toolbar.addWidget(self.filter)
        layout.addLayout(toolbar)
        self.splitter = QSplitter(Qt.Horizontal)
        self.timeline = QListWidget()
        self.timeline.setObjectName('timeline')
        self.timeline.setWordWrap(True)
        self.timeline.setItemDelegate(TurnDelegate(self.timeline))
        self.timeline.setResizeMode(QListWidget.Adjust)
        self.timeline.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.timeline.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.timeline.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.timeline.setContextMenuPolicy(Qt.CustomContextMenu)
        self.timeline.customContextMenuRequested.connect(self.transcript_context_menu)
        self.timeline.itemClicked.connect(self.play_segment)
        self.timeline.itemDoubleClicked.connect(lambda item: self.edit_segment())
        self.splitter.addWidget(self.timeline)
        panel = QFrame()
        self.ai_panel = panel
        panel.setObjectName('analysisPanel')
        panel.setMinimumWidth(340)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 12, 14, 14)
        panel_heading = QHBoxLayout()
        panel_title = QLabel('Codex 分析')
        panel_title.setObjectName('sectionTitle')
        panel_heading.addWidget(panel_title, 1)
        self.expand_reading_button = button('展开阅读', self.expand_reading, 'quiet')
        panel_heading.addWidget(self.expand_reading_button)
        panel_heading.addWidget(button('收起', lambda: self.ai_button.setChecked(False), 'quiet'))
        panel_layout.addLayout(panel_heading)
        self.report_view = AnalysisBrowser()
        self.report_view.anchorClicked.connect(self.open_citation)
        self.report_view.textChanged.connect(self.sync_reading)
        panel_layout.addWidget(self.report_view, 1)
        config = QFormLayout()
        self.template_box = StudioComboBox()
        for key, (label, hint, _) in TEMPLATES.items():
            self.template_box.addItem(label, key)
            self.template_box.setItemData(self.template_box.count() - 1, hint, Qt.ToolTipRole)
        self.template_box.setCurrentIndex(max(0, self.template_box.findData(self.store.settings().get('analysis_template', 'topics'))))
        self.template_box.currentIndexChanged.connect(lambda: self.store.set_settings(analysis_template=self.template_box.currentData()))
        config.addRow('分析方向', self.template_box)
        self.model_box, self.effort_box = StudioComboBox(), StudioComboBox()
        self.model_box.currentIndexChanged.connect(self.model_changed)
        self.effort_box.currentIndexChanged.connect(self.save_model_choice)
        config.addRow('Codex 模型', self.model_box)
        config.addRow('思考档位', self.effort_box)
        panel_layout.addLayout(config)
        self.model_status = QLabel('正在读取 Codex 模型目录…')
        self.model_status.setObjectName('subtitle')
        self.model_status.setWordWrap(True)
        panel_layout.addWidget(self.model_status)
        row = QHBoxLayout()
        row.addWidget(button('刷新模型', self.load_models))
        self.report_button = button('整理话题', self.submit_report, 'blue')
        row.addWidget(self.report_button)
        panel_layout.addLayout(row)
        self.question = QPlainTextEdit()
        self.question.setPlaceholderText('围绕这次通话提问，例如：他对价格有什么顾虑？')
        self.question.setMaximumHeight(95)
        panel_layout.addWidget(self.question)
        actions = QHBoxLayout()
        self.cancel_button = button('取消分析', self.cancel_analysis)
        self.cancel_button.setVisible(False)
        self.ask_button = button('发送问题', self.submit_question, 'blue')
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.ask_button)
        panel_layout.addLayout(actions)
        privacy = QLabel('点击即联网提交通话文字与问题，使用你的 Codex 额度。结果显示并保存在通话笺，不新增 Codex 聊天。原始录音保存在本机。')
        privacy.setObjectName('subtitle')
        privacy.setWordWrap(True)
        panel_layout.addWidget(privacy)
        self.splitter.addWidget(panel)
        panel.hide()
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, True)
        layout.addWidget(self.splitter, 1)
        playback = QHBoxLayout()
        self.play_button = button('播放', self.toggle_play)
        self.play_button.setIcon(playback_icon())
        self.play_button.setToolTip('播放／暂停（空格）')
        playback.addWidget(self.play_button)
        self.back_button = button('−5s', lambda: self.skip_playback(-5))
        self.back_button.setToolTip('后退 5 秒（←）')
        self.back_button.setAccessibleName('后退五秒')
        playback.addWidget(self.back_button)
        self.forward_button = button('+5s', lambda: self.skip_playback(5))
        self.forward_button.setToolTip('前进 5 秒（→）')
        self.forward_button.setAccessibleName('前进五秒')
        playback.addWidget(self.forward_button)
        self.play_label = QLabel('00:00 / 00:00')
        playback.addWidget(self.play_label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.sliderReleased.connect(self.seek_slider)
        playback.addWidget(self.slider, 1)
        self.speed_box = StudioComboBox()
        self.speed_box.setAccessibleName('播放速度')
        self.speed_box.setToolTip('播放速度 · 自动记住上次选择')
        for rate in PLAYBACK_RATES:
            self.speed_box.addItem(f'{rate:g}×', rate)
        self.speed_box.setCurrentIndex(self.speed_box.findData(self.playback_rate))
        self.speed_box.setFixedWidth(92)
        self.speed_box.currentIndexChanged.connect(self.change_playback_rate)
        playback.addWidget(self.speed_box)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(75)
        self.volume.setMaximumWidth(85)
        self.volume.valueChanged.connect(self.set_volume)
        playback.addWidget(QLabel('音量'))
        playback.addWidget(self.volume)
        layout.addLayout(playback)
        return page

    def toggle_ai_panel(self, checked):
        self.ai_panel.setVisible(checked)
        self.ai_button.setText('Codex 分析')
        if checked:
            width = self.splitter.width()
            analysis_width = max(390, min(640, int(width * .60)))
            self.splitter.setSizes([max(280, width - analysis_width), analysis_width])

    def set_theme(self, mode):
        self.store.set_settings(theme_mode=mode)
        self.apply_theme()

    def apply_theme(self):
        settings = self.store.settings()
        mode = settings.get('theme_mode', settings.get('reading_mode', 'day'))
        theme.configure_app(QApplication.instance(), mode)
        self.setStyleSheet(theme.stylesheet())
        self.theme_box.blockSignals(True)
        self.theme_box.setCurrentIndex(self.theme_box.findData(theme.MODE))
        self.theme_box.blockSignals(False)
        for combo in self.findChildren(StudioComboBox):
            combo.setProperty('arrowBackground', theme.color('#1d2633'))
            combo.setProperty('arrowColor', theme.MUTED)
            combo.update()
        for view in self.findChildren(AnalysisBrowser):
            view.reading = self.reading
            view.set_reading_style(theme.MODE)
        self.play_button.setIcon(playback_icon(self.playing))
        for view in (self.timeline, self.live_timeline, self.history):
            view.viewport().update()
        self.waveform.update()

    def apply_reading(self):
        self.reading = reading_options(self.store.settings())
        if self.version_dialog:
            self.version_dialog.reading = self.reading
            for view in self.version_dialog.findChildren(QListWidget):
                view.doItemsLayout()
                view.viewport().update()
        for view in self.findChildren(AnalysisBrowser):
            view.reading = self.reading
            view.set_reading_style(theme.MODE)
        for view in (self.timeline, self.live_timeline):
            view.doItemsLayout()
            view.viewport().update()

    def notify(self, text, error=False):
        self.notice.setText(text)
        self.notice.setProperty('error', error)
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self.notice)
        recording_page = hasattr(self, 'pages') and self.pages.currentWidget() == self.record_page
        self.notice.setVisible(error or not recording_page)

    def run_job(self, function, success, failure=None, delta=None, events=None):
        job = Job(function)
        self.jobs.append(job)
        job.signals.result.connect(success)
        job.signals.error.connect(failure or (lambda message: self.notify(message, True)))
        job.signals.progress.connect(self.notify)
        if delta:
            job.signals.delta.connect(delta)
        if events:
            job.signals.event.connect(events)
        job.signals.finished.connect(lambda: self.jobs.remove(job) if job in self.jobs else None)
        self.pool.start(job)

    def prepare_captions(self):
        if not self.prewarm_enabled or self.closing or self.warming or self.busy_type == 'transcription':
            return
        engine = self.live
        if engine.is_ready:
            return
        self.warming = True
        self.caption_status.setText('字幕引擎准备中…')

        def done(result):
            if self.closing or self.live is not engine:
                return
            self.warming = False
            if not self.recorder:
                self.caption_status.setText('字幕就绪 · 按发言分段')

        def failed(error):
            if self.closing or self.live is not engine:
                return
            self.warming = False
            self.caption_status.setText('字幕引擎尚未就绪')
            self.notify('字幕准备失败：' + error + '。仍可录音并保存。', True)

        self.run_job(lambda signals: engine.preload(self.store.settings(), lambda text: None), done, failed)

    def refresh_devices(self):
        if self.recorder:
            return
        try:
            microphones, outputs = devices()
            config = self.store.settings()
            for combo, values, key in [(self.mic_box, microphones, 'mic_device'), (self.system_box, outputs, 'system_device')]:
                combo.clear()
                selected = 0
                for index, item in enumerate(values):
                    combo.addItem(item['name'], int(item['index']))
                    if item['name'] == config.get(key) or (not config.get(key) and item.get('default')):
                        selected = index
                combo.setCurrentIndex(selected)
            if not microphones or (self.capture_mode.currentData() == 'all' and not outputs):
                self.notify('未找到麦克风或 WASAPI 播放设备，请连接设备后刷新。', True)
        except Exception as exc:
            self.notify('读取音频设备失败：' + str(exc), True)
        self.refresh_voice_apps()
        self.update_capture_mode()

    def refresh_voice_apps(self):
        previous = self.app_box.currentData()
        saved = self.store.settings().get('voice_app', '')
        self.app_box.clear()
        try:
            apps = enumerate_voice_apps()
            selected = 0
            for i, app in enumerate(apps):
                count = sum(other['name'] == app['name'] for other in apps)
                label = app['name'] + (f'（进程 {app["pid"]}）' if count > 1 else '')
                self.app_box.addItem(label, app)
                if (previous and app['pid'] == previous['pid']) or (not previous and app['exe'] == saved):
                    selected = i
            if not apps:
                self.app_box.addItem('请先打开 Discord 或微信，再点刷新', None)
            self.app_box.setCurrentIndex(selected)
        except Exception as exc:
            self.app_box.addItem('未能读取通话软件，请点刷新', None)
            self.notify('读取通话软件失败：' + str(exc), True)

    def set_record_controls(self, active):
        self.start_button.setVisible(not active)
        self.stop_button.setVisible(active)
        self.pause_record_button.setVisible(active)
        self.pause_record_button.setText('暂停录音')
        self.mic_box.setEnabled(not active)
        self.system_box.setEnabled(not active)
        self.app_box.setEnabled(not active)
        self.capture_mode.setEnabled(not active)
        self.refresh_device_button.setEnabled(not active)
        self.audio_settings_button.setEnabled(not active and not self.busy_id)
        if active:
            self.ai_button.setChecked(False)
            self.ai_button.setEnabled(False)
        self.empty_title.setText('正在等待对话' if active else '准备好记录这次对话')
        self.empty_note.setText('录音已开始，识别到语音后会在这里显示字幕。' if active else '点击下方开始录制，双方字幕会逐步出现在这里。')
        self.new_button.setEnabled(not active and not self.busy_id)
        self.import_button.setEnabled(not active and not self.busy_id)
        self.pill.setText('● 正在录音' if active else '准备录音')
        self.pill.setProperty('active', active)
        self.pill.style().unpolish(self.pill)
        self.pill.style().polish(self.pill)

    def toggle_record_pause(self):
        if not self.recorder:
            return
        try:
            self.recorder.set_paused(not self.recorder.paused)
        except Exception as exc:
            self.notify('切换暂停状态失败：' + str(exc), True)
            return
        paused = self.recorder.paused
        self.pause_record_button.setText('继续录音' if paused else '暂停录音')
        self.pill.setText('Ⅱ 已暂停' if paused else '● 正在录音')
        self.caption_status.setText('已暂停 · 暂停期间的声音不保存' if paused else '实时字幕继续更新')
        self.store.update(self.recording_id, status='recording')

    def new_record(self):
        if self.recorder or self.busy_id:
            return
        self.pause()
        self.current_id = None
        self.history.setCurrentItem(None)
        self.history.clearSelection()
        self.title.setText('新录音')
        self.meta_label.setText('本地录音与实时文字稿')
        self.pages.setCurrentWidget(self.record_page)
        self.clock.setText('00:00:00')
        self.waveform.reset()
        self.ai_button.setChecked(False)
        self.ai_button.setEnabled(False)
        self.caption_status.setText('字幕就绪 · 按发言分段' if self.live.is_ready else '字幕引擎准备中…')
        self.live_timeline.clear()
        self.live_canvas.setCurrentWidget(self.live_empty)
        self.notify('准备就绪，点击“开始录制”。')
        self.enable_actions()

    def start_recording(self):
        if self.recorder or self.busy_id:
            return
        process = self.capture_mode.currentData() == 'process'
        source = self.app_box.currentData() if process else self.system_box.currentData()
        if process and support_error():
            self.notify(support_error(), True)
            return
        if self.mic_box.currentData() is None or source is None:
            self.notify('请先打开 Discord 或微信，刷新并选择通话软件及麦克风。' if process else '请先选择两路音频设备。', True)
            return
        self.pause()
        record_id = self.store.create()
        try:
            recorder = Recorder(self.store.folder(record_id), self.mic_box.currentData(), source)
            recorder.start()
        except Exception as exc:
            self.store.update(record_id, status='saved', error=str(exc))
            self.notify('录音未能启动：' + str(exc), True)
            self.refresh_history()
            return
        self.recorder, self.recording_id, self.current_id = recorder, record_id, record_id
        self.live_segments, self.live_partials = [], {}
        self.waveform.reset()
        self.caption_status.setText('正在接入实时字幕…' if self.live.is_ready else '字幕引擎准备中，录音已开始…')
        self.live_failed = False
        self.live_timeline.clear()
        self.live_canvas.setCurrentWidget(self.live_empty)
        self.store.set_settings(mic_device=self.mic_box.currentText(), system_device=self.system_box.currentText())
        if process:
            self.store.set_settings(voice_app=source['exe'])
        self.set_record_controls(True)
        self.history.setEnabled(False)
        self.title.setText(self.store.get(record_id)['title'])
        self.refresh_history(record_id=record_id)
        self.notify('正在录音。字幕在识别到语音后持续更新；音频与字幕保存在本机。')
        def event_received(event):
            if self.closing:
                return
            if event['type'] == 'ready':
                self.caption_status.setText('实时字幕已就绪')
                self.notify('正在录音并持续更新字幕。临时字幕可能修正；停止时只整理末尾。')
                return
            key = event['utterance']
            if event['final']:
                self.live_partials.pop(key, None)
                self.live_segments.extend(event['segments'])
            else:
                self.live_partials[key] = [{**s, 'partial': True} for s in event['segments']]
            self.render_live()
            self.caption_status.setText('已暂停 · 暂停期间的声音不保存' if self.recorder and getattr(self.recorder, 'paused', False)
                else '正在确认最后一句' if self.live_partials else '已保存当前发言')
        def live_done(result):
            if self.closing:
                return
            self.store.save_transcript(record_id, result['segments'], original=True)
            self.store.update(record_id, status='ready')
            self.busy_id, self.busy_type = None, None
            self.refresh_history(record_id=record_id)
            self.show_record(record_id)
            self.enable_actions()
        def live_error(error):
            if self.closing:
                return
            self.live_failed = True
            self.notify('实时字幕异常：' + error + '。原始录音仍保留。', True)
            if not self.recorder:
                self.busy_id, self.busy_type = None, None
                self.store.update(record_id, status='saved', error=error)
                self.refresh_history(record_id=record_id)
                self.show_record(record_id)
                self.enable_actions()
        self.run_job(lambda signals: self.live.run(self.store.folder(record_id), self.store.settings(),
                    signals.progress.emit, signals.event.emit), live_done, live_error, events=event_received)

    def render_live(self):
        scroll = self.live_timeline.verticalScrollBar()
        follow = scroll.maximum() - scroll.value() < 60
        position = scroll.value()
        segments = list(self.live_segments)
        for values in self.live_partials.values():
            segments.extend(values)
        turns = speaker_turns(segments)
        self.live_canvas.setCurrentWidget(self.live_timeline if turns else self.live_empty)
        # Reuse confirmed rows during a long call and preserve the reader's
        # position when they scroll back while new captions continue arriving.
        while self.live_timeline.count() > len(turns):
            self.live_timeline.takeItem(self.live_timeline.count() - 1)
        for row, turn in enumerate(turns):
            item = self.live_timeline.item(row)
            if item is None:
                item = QListWidgetItem()
                self.live_timeline.addItem(item)
            if item.data(Qt.UserRole + 2) != turn:
                item.setText(turn['text'])
                item.setData(Qt.UserRole + 2, turn)
        if follow:
            self.live_timeline.scrollToBottom()
        else:
            scroll.setValue(position)

    def stop_recording(self, auto_transcribe=True):
        if not self.recorder:
            return
        self.stop_button.setEnabled(False)
        self.notify('正在停止并保存两路录音…')
        QApplication.processEvents()
        record_id, recorder = self.recording_id, self.recorder
        try:
            duration = recorder.stop()
            self.store.update(record_id, status='saved', duration=duration, error='；'.join(recorder.errors))
        except Exception as exc:
            self.stop_button.setEnabled(True)
            self.notify(str(exc), True)
            raise
        self.recorder = None
        self.recording_id = None
        self.set_record_controls(False)
        self.stop_button.setEnabled(True)
        self.history.setEnabled(True)
        if not self.live_failed:
            self.live.finish(self.store.folder(record_id))
        self.refresh_history(record_id=record_id)
        self.show_record(record_id)
        if not self.live_failed:
            self.busy_id, self.busy_type = record_id, 'live-finalize'
            self.store.update(record_id, status='transcribing')
            self.enable_actions()
            self.notify('录音已保存，正在确认最后一段字幕。需要区分说话人时，可在文字稿中手动开始。')
        elif auto_transcribe and self.live_failed and not recorder.errors:
            if self.live_segments:
                self.store.save_transcript(record_id, self.live_segments, original=True)
                self.store.update(record_id, status='ready', error='实时字幕中断，结尾可能缺失；原始录音完整保留。')
                self.show_record(record_id)
            self.notify('录音已保存，实时字幕未完成。可点击“生成文字稿”恢复完整转录。', True)
        elif recorder.errors:
            self.notify('录音已保存，但设备异常：' + '；'.join(recorder.errors) + '。请核对后再转录。', True)

    def refresh_history(self, *args, record_id=None):
        selected = record_id or self.current_id
        self.history.blockSignals(True)
        self.history.clear()
        for record in self.store.listing(self.search.text(), self.trash):
            item = QListWidgetItem(f'{record["title"]}\n{record["created"][:16].replace("T", "  ")}  ·  {stamp(record["duration"])}\n{STATUS.get(record["status"], record["status"])}')
            item.setData(Qt.UserRole, record['id'])
            item.setData(Qt.UserRole + 1, {**record, 'status_label': STATUS.get(record['status'], record['status'])})
            item.setToolTip(record['title'])
            self.history.addItem(item)
            if record['id'] == selected:
                self.history.setCurrentItem(item)
        self.history.blockSignals(False)

    def selected_record(self, item, previous=None):
        if item and not self.recorder:
            if (self.current_id == item.data(Qt.UserRole)
                    and self.pages.currentWidget() == self.review_page):
                return
            self.show_record(item.data(Qt.UserRole))

    def show_record(self, record_id):
        self.pause()
        self.current_id = record_id
        record = self.store.get(record_id)
        self.title.setText(record['title'])
        value = self.store.transcript(record_id)
        self.displayed_revision = value['revision']
        self.meta_label.setText(f'{record["created"][:19].replace("T", "  ")}   ·   {stamp(record["duration"])}   ·   {STATUS.get(record["status"], "")}   ·   文字稿 v{value["revision"]}')
        self.segments = value['segments']
        self.render_segments()
        self.render_messages()
        self.pages.setCurrentWidget(self.review_page)
        self.retry_button.setText('重新转录…' if self.segments else '生成文字稿')
        self.enable_actions()
        self.prepare_players(record_id)
        self.notify(record['error'] or ('文字稿已保存在本机。你可以回听、修改，或选择模型后交给 Codex。' if self.segments else '录音已保存，可生成完整文字稿。'), bool(record['error']))

    def enable_actions(self):
        record = self.store.get(self.current_id) if self.current_id else None
        usable = bool(record and not record['deleted'] and not self.busy_id and not self.recorder)
        self.retry_button.setEnabled(usable)
        self.edit_button.setEnabled(usable and bool(self.segments))
        self.region_button.setEnabled(usable and bool(self.segments))
        self.speakers_button.setEnabled(usable and bool(self.segments))
        self.segment_actions_button.setEnabled(usable and bool(self.segments))
        self.remove_segments_action.setEnabled(usable and bool(self.segments))
        undo = self.last_segment_deletion
        can_undo = bool(undo and undo[0] == self.current_id and undo[1] == self.displayed_revision)
        self.undo_transcript_button.setVisible(can_undo)
        self.undo_transcript_button.setEnabled(usable and can_undo)
        self.speakers_button.setVisible(any(s.get('track') in ('imported', 'system') for s in self.segments))
        self.report_button.setEnabled(usable and bool(self.segments) and bool(self.models))
        self.ask_button.setEnabled(self.report_button.isEnabled())
        self.new_button.setEnabled(not self.busy_id and not self.recorder)
        self.import_button.setEnabled(not self.busy_id and not self.recorder)
        self.template_box.setEnabled(not self.busy_id)
        self.ai_button.setEnabled(bool(record and not record['deleted'] and self.segments and not self.recorder))
        self.cancel_button.setVisible(self.busy_type == 'analysis')
        reviewing = self.pages.currentWidget() == self.review_page
        self.ai_button.setVisible(reviewing)
        self.expand_reading_button.setEnabled(bool(record and not self.recorder))
        self.audio_settings_button.setEnabled(not self.recorder and not self.busy_id)

    def record_actions_menu(self, record_id):
        menu = QMenu(self.history)
        menu.setObjectName('recordContextMenu')
        record = self.store.get(record_id)
        if not record:
            return menu
        value = self.store.transcript(record_id)
        editable = not self.recorder and not self.busy_id
        def add(label, handler, enabled, mutation=False):
            action = menu.addAction(label)
            action.setEnabled(bool(enabled))
            def run():
                if self.recorder or mutation and self.busy_id:
                    return
                # Bind to the clicked record, even if a job or keyboard action
                # changed the main selection while the menu was open.
                if not self.store.get(record_id):
                    return
                self.show_record(record_id)
                handler()
            action.triggered.connect(run)
            return action
        add('重命名', self.rename, editable, True)
        add('导出', self.export, value['segments'] and not self.recorder)
        add('版本记录', self.show_versions, value['revision'] > 0 and not self.recorder)
        menu.addSeparator()
        add('恢复记录' if record['deleted'] else '移入回收区', self.delete_record, editable, True)
        return menu

    def history_context_menu(self, position):
        item = self.history.itemAt(position)
        if item is None or self.recorder:
            return
        record_id = item.data(Qt.UserRole)
        self.history.setCurrentItem(item)
        if self.current_id != record_id or self.pages.currentWidget() != self.review_page:
            self.show_record(record_id)
        menu = self.record_actions_menu(record_id)
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(self.history.viewport().mapToGlobal(position))

    def expand_reading(self):
        if not self.current_id or self.recorder:
            return
        record_id = self.current_id
        if self.reading_dialog and self.reading_dialog.property('recordId') == record_id:
            self.reading_dialog.showFullScreen()
            self.reading_dialog.raise_()
            self.reading_dialog.activateWindow()
            return
        if self.reading_dialog:
            self.reading_dialog.close()
        dialog = QDialog(self, Qt.Window)
        self.reading_dialog = dialog
        dialog.setObjectName('analysisReader')
        dialog.setProperty('recordId', record_id)
        dialog.reading = self.reading
        dialog.setWindowTitle('Codex 分析 · 展开阅读')
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(28, 18, 28, 18)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel(self.store.get(record_id)['title'] + ' · Codex 分析')
        title.setTextFormat(Qt.PlainText)
        title.setObjectName('sectionTitle')
        title.setWordWrap(True)
        header.addWidget(title, 1)
        header.addWidget(button('退出阅读 · Esc', dialog.close, 'quiet'))
        layout.addLayout(header)
        dialog.view = AnalysisBrowser(dialog)
        dialog.view.setObjectName('readerText')
        dialog.view.reading = self.reading
        dialog.view.anchorClicked.connect(lambda url: self.open_citation(url, record_id))
        layout.addWidget(dialog.view, 1)
        escape = QShortcut(QKeySequence(Qt.Key_Escape), dialog)
        escape.activated.connect(dialog.close)
        def clear():
            if self.reading_dialog is dialog:
                self.reading_dialog = None
        dialog.finished.connect(clear)
        self.sync_reading(follow=False)
        dialog.showFullScreen()
        dialog.view.setFocus()

    def sync_reading(self, follow=True):
        dialog = self.reading_dialog
        if not dialog or dialog.property('recordId') != self.current_id:
            return
        view = dialog.view
        scroll = view.verticalScrollBar()
        position, at_end = scroll.value(), scroll.maximum() - scroll.value() < 60
        if self.store.messages(self.current_id) or self.busy_type == 'analysis' and self.busy_id == self.current_id:
            view.setHtml(self.report_view.toHtml())
        else:
            view.setMarkdown('## 暂无分析结果\n\n返回分析面板整理话题或提交问题后，即可在这里阅读全文。')
        view.reading = self.reading
        view.set_reading_style(theme.MODE)
        scroll.setValue(scroll.maximum() if follow and at_end else position)

    def show_changelog(self):
        if self.changelog_dialog:
            self.changelog_dialog.show()
            self.changelog_dialog.raise_()
            self.changelog_dialog.activateWindow()
            return
        dialog = QDialog(self)
        self.changelog_dialog = dialog
        dialog.setObjectName('changelogDialog')
        dialog.reading = self.reading
        dialog.setWindowTitle('更新日志 · 通话笺 ' + VERSION)
        dialog.resize(900, 760)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        header = QHBoxLayout()
        title = QLabel('更新日志')
        title.setObjectName('headline')
        header.addWidget(title)
        version = QLabel('应用版本 ' + VERSION)
        version.setObjectName('subtitle')
        header.addWidget(version, 1)
        header.addWidget(button('关闭', dialog.close, 'quiet'))
        layout.addLayout(header)
        view = AnalysisBrowser(dialog)
        view.setObjectName('changelogText')
        view.setMarkdown(changelog_text().removeprefix('# 更新日志\n'))
        view.set_reading_style(theme.MODE)
        layout.addWidget(view, 1)
        def clear():
            if self.changelog_dialog is dialog:
                self.changelog_dialog = None
        dialog.finished.connect(clear)
        dialog.show()

    def show_versions(self):
        if not self.current_id or self.recorder:
            return
        record_id = self.current_id
        if self.version_dialog and self.version_dialog.property('recordId') == record_id:
            self.version_dialog.show()
            self.version_dialog.raise_()
            self.version_dialog.activateWindow()
            return
        if self.version_dialog:
            self.version_dialog.close()
        dialog = QDialog(self)
        self.version_dialog = dialog
        dialog.setProperty('recordId', record_id)
        dialog.reading = self.reading
        dialog.setWindowTitle('文字稿版本 · ' + self.store.get(record_id)['title'])
        dialog.resize(1040, 710)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        title = QLabel('文字稿版本')
        title.setObjectName('headline')
        layout.addWidget(title)
        note = QLabel('每次重新转录或修改都会保留旧稿。选择左侧版本查看；点击原话可按对应时间回听。')
        note.setWordWrap(True)
        note.setObjectName('subtitle')
        layout.addWidget(note)
        deleted_filter = QCheckBox('查看文字稿回收区')
        deleted_filter.setObjectName('deletedVersions')
        layout.addWidget(deleted_filter)
        body = QSplitter(Qt.Horizontal)
        chooser = QListWidget()
        chooser.setObjectName('versionList')
        chooser.setMinimumWidth(170)
        chooser.setMaximumWidth(230)
        chooser.setAccessibleName('文字稿全部版本')
        body.addWidget(chooser)
        preview = QListWidget()
        preview.setObjectName('timeline')
        preview.setAccessibleName('所选版本的文字稿，只读')
        preview.setItemDelegate(TurnDelegate(preview))
        preview.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        preview.setResizeMode(QListWidget.Adjust)
        preview.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.addWidget(preview)
        body.setSizes([190, 800])
        layout.addWidget(body, 1)
        status = QLabel()
        status.setObjectName('subtitle')
        status.setWordWrap(True)
        layout.addWidget(status)
        path_field = QLineEdit()
        path_field.setObjectName('versionPath')
        path_field.setReadOnly(True)
        path_field.setAccessibleName('所选文字稿的本地文件路径')
        layout.addWidget(path_field)
        actions = QHBoxLayout()
        actions.addWidget(button('打开所在文件夹', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.folder(record_id))))))
        actions.addStretch()
        export_button = button('导出此版本', lambda: export_selected())
        restore_button = button('恢复为当前文字稿', lambda: restore_selected())
        restore_button.setObjectName('blue')
        recycle_button = button('删除此版本', lambda: recycle_selected())
        actions.addWidget(recycle_button)
        actions.addWidget(export_button)
        actions.addWidget(restore_button)
        actions.addWidget(button('关闭', dialog.close))
        layout.addLayout(actions)
        selection = {'value': None, 'revision': None}

        def change(row):
            preview.clear()
            selection.update(value=None, revision=None)
            restore_button.setEnabled(False)
            recycle_button.setEnabled(False)
            export_button.setEnabled(False)
            item = chooser.item(row)
            if item is None:
                status.setText('文字稿回收区为空。' if deleted_filter.isChecked() else '暂无文字稿版本。')
                path_field.clear()
                return
            entry = item.data(Qt.UserRole)
            path_field.setText(entry['path'])
            path_field.setCursorPosition(0)
            try:
                value = self.store.transcript_version(record_id, entry['revision'])
                selection.update(value=value, revision=entry['revision'])
                for turn in speaker_turns(value['segments']):
                    line = QListWidgetItem(turn['text'])
                    line.setData(Qt.UserRole + 2, turn)
                    line.setToolTip('点击回听本段原音频；此处只读，不会改写当前文字稿。')
                    preview.addItem(line)
                current = self.store.transcript(record_id)['revision']
                state = '回收区 · 既有分析仍可引用原文' if entry.get('deleted') else '当前版本' if current == value['revision'] else f'历史版本 · 当前使用 v{current}'
                status.setText(f'文字稿 v{value["revision"]} · {state} · {len(value["segments"])} 个原始片段 · 只读查看')
                export_button.setEnabled(True)
                restore_button.setEnabled(current != value['revision'] and not self.busy_id and not self.recorder)
                recycle_button.setEnabled(current != value['revision'] and not self.busy_id and not self.recorder)
            except (ValueError, OSError) as exc:
                status.setText('这个版本无法读取：' + str(exc) + '。文件仍保留，请检查所在文件夹。')

        def populate(selected_revision=None):
            chooser.blockSignals(True)
            chooser.clear()
            chosen = 0
            entries = self.store.transcript_versions(record_id, deleted=deleted_filter.isChecked())
            recycle_button.setText('放回历史列表' if deleted_filter.isChecked() else '删除此版本')
            recycle_button.setToolTip('移入文字稿回收区，可随时找回；当前稿和原始音频保留。')
            for row, entry in enumerate(entries):
                suffix = '已删除' if entry.get('deleted') else '当前' if entry['current'] else '历史'
                item = QListWidgetItem(f'v{entry["revision"]}　{suffix}\n' + ('文件异常' if entry['error'] else f'{entry["count"]} 个原始片段'))
                item.setData(Qt.UserRole, entry)
                chooser.addItem(item)
                if entry['revision'] == selected_revision:
                    chosen = row
            chooser.setCurrentRow(chosen)
            chooser.blockSignals(False)
            change(chosen)

        def play_old(item):
            turn = item.data(Qt.UserRole + 2)
            if not turn or self.recorder:
                return
            if self.current_id != record_id:
                self.refresh_history(record_id=record_id)
                self.show_record(record_id)
            self.seek(turn['start'], True)

        def export_selected():
            value = selection['value']
            if value is None:
                return
            filename, _ = QFileDialog.getSaveFileName(dialog, '导出所选文字稿版本',
                self.store.get(record_id)['title'] + f'-文字稿-v{value["revision"]}.txt', '纯文本 (*.txt);;Markdown (*.md)')
            if filename:
                try:
                    Path(filename).write_text(f'# {self.store.get(record_id)["title"]} · 文字稿 v{value["revision"]}\n\n' + transcript_text(value), encoding='utf-8-sig')
                    status.setText(f'已导出文字稿 v{value["revision"]}：{filename}')
                except OSError as exc:
                    status.setText('导出未完成：' + str(exc))

        def restore_selected():
            revision = selection['revision']
            if revision is None or self.busy_id or self.recorder:
                return
            try:
                value = self.store.restore_transcript(record_id, revision)
                self.store.update(record_id, status='ready', error='')
                if self.current_id == record_id:
                    self.show_record(record_id)
                self.refresh_history()
                deleted_filter.blockSignals(True)
                deleted_filter.setChecked(False)
                deleted_filter.blockSignals(False)
                populate(value['revision'])
                status.setText(f'已将 v{revision} 的内容另存为当前 v{value["revision"]}，恢复前的版本和既有问答均保留。')
            except (ValueError, OSError) as exc:
                status.setText('恢复未完成：' + str(exc))

        def recycle_selected():
            revision = selection['revision']
            if revision is None or self.busy_id or self.recorder:
                return
            deleted = not deleted_filter.isChecked()
            try:
                self.store.set_transcript_deleted(record_id, revision, deleted)
                populate()
                status.setText(f'v{revision} 已移到文字稿回收区，可勾选上方“查看文字稿回收区”找回。' if deleted else
                               f'v{revision} 已放回历史版本列表；当前文字稿保持不变。')
            except (ValueError, OSError) as exc:
                status.setText('操作未完成：' + str(exc))

        chooser.currentRowChanged.connect(change)
        deleted_filter.toggled.connect(lambda: populate())
        preview.itemClicked.connect(play_old)
        populate()
        def clear_reference():
            if self.version_dialog is dialog:
                self.version_dialog = None
        dialog.finished.connect(clear_reference)
        dialog.show()

        # Tasks can finish while this read-only window is open. Refresh only
        # when the local current revision changes, preserving the selected draft.
        last_revision = [self.store.transcript(record_id)['revision']]
        refresh = QTimer(dialog)
        def check_versions():
            current = self.store.transcript(record_id)['revision']
            if current != last_revision[0]:
                selected = selection['revision']
                last_revision[0] = current
                populate(selected)
            restore_button.setEnabled(selection['revision'] is not None and selection['revision'] != current
                                      and not self.busy_id and not self.recorder)
            recycle_button.setEnabled(selection['revision'] is not None and selection['revision'] != current
                                      and not self.busy_id and not self.recorder)
        refresh.timeout.connect(check_versions)
        refresh.start(1000)

    def transcript_context_menu(self, position):
        item = self.timeline.itemAt(position)
        if item is None or item.data(Qt.UserRole) is None:
            return
        if not item.isSelected():
            self.timeline.clearSelection()
            self.timeline.setCurrentItem(item)
        menu = QMenu(self.timeline)
        action = menu.addAction('删除选中段落')
        action.setEnabled(self.remove_segments_action.isEnabled())
        action.triggered.connect(self.delete_selected_segments)
        menu.exec(self.timeline.viewport().mapToGlobal(position))

    def delete_selected_segments(self):
        if not self.current_id or self.busy_id or self.recorder:
            return
        indices = sorted({i for item in self.timeline.selectedItems()
                          for i in (item.data(Qt.UserRole + 1) or [])})
        if not indices:
            self.notify('先选中要删除的段落；按 Ctrl / Shift 可选择多段。')
            return
        rid, before = self.current_id, self.displayed_revision
        try:
            value = self.store.delete_transcript_segments(rid, indices, before)
            self.pause()
            self.last_segment_deletion = (rid, value['revision'], before)
            self.store.update(rid, status='ready', error='')
            self.show_record(rid)
            self.refresh_history()
            self.notify(f'已删除选中内容，保存为文字稿 v{value["revision"]}。原始录音保留；可点击“撤销删除”或从版本记录找回。')
        except (ValueError, OSError) as exc:
            self.notify('删除未完成：' + str(exc), True)

    def undo_segment_deletion(self):
        undo = self.last_segment_deletion
        if not undo or undo[0] != self.current_id or self.busy_id or self.recorder:
            return
        rid, deleted_revision, previous = undo
        if self.store.transcript(rid)['revision'] != deleted_revision:
            self.notify('文字稿已有新的修改，请在版本记录中选择需要恢复的版本。')
            return
        try:
            value = self.store.restore_transcript(rid, previous)
            self.last_segment_deletion = None
            self.show_record(rid)
            self.refresh_history()
            self.notify(f'已撤销删除，保存为文字稿 v{value["revision"]}。')
        except (ValueError, OSError) as exc:
            self.notify('撤销未完成：' + str(exc), True)

    def render_segments(self, *args):
        selected = self.timeline.currentItem().data(Qt.UserRole) if self.timeline.currentItem() else None
        self.timeline.clear()
        needle = self.filter.text().casefold()
        for segment in speaker_turns(self.segments):
            index = segment['indices'][0]
            if needle and needle not in segment['text'].casefold() and needle not in segment['speaker'].casefold():
                continue
            review = '  ·  请核对' if segment.get('review') else ''
            if segment.get('echo_suspected'):
                review = '  ·  疑似串音，请核对说话方'
            partial = '  ·  正在说…（临时字幕）' if segment.get('partial') else ''
            item = QListWidgetItem(f'{segment["speaker"]}    {stamp(segment["start"])} — {stamp(segment["end"])}{partial}{review}\n\n{segment["text"]}')
            item.setData(Qt.UserRole, index)
            item.setData(Qt.UserRole + 1, segment['indices'])
            item.setData(Qt.UserRole + 2, segment)
            if segment.get('speaker_inferred'):
                item.setToolTip('这段含时间边界或声音相似度辅助归属，必要时请回听核对；不代表人工确认。')
            if segment.get('contains_unresolved'):
                item.setToolTip('划线文字含重叠声音或不确定归属。这里只合并阅读排版；原始片段仍保留待确认标记，分析不会当作确定归属。')
            item.setForeground(QColor(theme.TEXT))
            self.timeline.addItem(item)
            if index == selected:
                self.timeline.setCurrentItem(item)
        if not self.segments:
            item = QListWidgetItem('录音已保存在本机\n\n点击“生成文字稿”，完成后这里会显示带时间戳的“我／对方”对话。')
            item.setFlags(Qt.NoItemFlags)
            self.timeline.addItem(item)

    @staticmethod
    def linked(markdown, revision=0):
        return cite_markdown(markdown, revision)

    def analysis_markdown(self, messages, include_report=True):
        reports = [m for m in messages if is_report(m['question'])]
        parts = []
        if include_report and reports:
            report = reports[-1]
            parts.append(self.linked(report['answer'], report['revision']))
        for message in messages:
            if is_report(message['question']):
                continue
            parts.append('## 追问：' + html.escape(question_label(message['question']).replace('\n', ' '))
                         + '\n\n' + self.linked(message['answer'], message['revision']))
        return '\n\n---\n\n'.join(parts)

    def render_messages(self):
        content = self.analysis_markdown(self.store.messages(self.current_id))
        self.report_view.setMarkdown(content or '## 整理与追问\n\n选择模型和思考档位，点击 **整理话题**；也可以在下方输入问题。结果会显示在这里。')

    def transcribe_current(self):
        if not self.current_id or self.busy_id or self.recorder:
            return
        if self.segments and QMessageBox.question(self, '重新转录', '将生成新的识别版本。现有修订版会归档，已有问答保留并标注旧版本。继续？') != QMessageBox.Yes:
            return
        record_id = self.current_id
        folder = self.store.folder(record_id)
        meta = read_json(folder / 'audio.json', {})
        if not meta.get('tracks') or not all((folder / (t['key'] + '.wav')).exists() for t in meta['tracks']):
            self.notify('双方音频文件尚未完整保存，不能开始转录。', True)
            return
        self.busy_id, self.busy_type = record_id, 'transcription'
        # A full re-transcription releases the resident model first so two large
        # CUDA models do not compete for VRAM. It is prepared again afterwards.
        self.live.stop()
        self.live = LiveTranscriber()
        self.warming = False
        self.store.update(record_id, status='transcribing', error='')
        self.enable_actions()
        self.refresh_history()
        def completed(result):
            self.store.save_transcript(record_id, result['segments'], original=True)
            self.store.update(record_id, status='ready', error='')
            self.finish_busy(record_id)
            if result.get('warnings'):
                self.notify('；'.join(result['warnings']), True)
            self.prepare_captions()
        def failed(error):
            self.job_failed(record_id, error)
            self.prepare_captions()
        self.run_job(lambda signals: self.transcriber.run(folder, self.store.settings(), signals.progress.emit),
                     completed, failed)

    def finish_busy(self, record_id):
        self.busy_id, self.busy_type = None, None
        self.refresh_history()
        if self.current_id == record_id:
            self.show_record(record_id)
        self.enable_actions()

    def import_media(self, source=None):
        if self.busy_id or self.recorder:
            return
        if not source:
            source, _ = QFileDialog.getOpenFileName(self, '导入已有音视频', '',
                '音视频 (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma *.mp4 *.mkv *.mov *.webm *.avi);;所有文件 (*)')
        if not source:
            return
        source = Path(source)
        if not source.is_file():
            self.notify('找不到要导入的文件。', True)
            return
        self.pause()
        record_id = self.store.create(source.stem)
        self.store.update(record_id, status='transcribing')
        self.current_id = record_id
        self.busy_id, self.busy_type = record_id, 'transcription'
        self.live.stop()
        self.live = LiveTranscriber()
        self.warming = False
        self.show_record(record_id)
        self.refresh_history(record_id=record_id)
        self.notify('正在本机提取音轨并生成文字稿…')
        settings = self.store.settings()
        folder = self.store.folder(record_id)

        def work(signals):
            metadata = self.transcriber.import_media(source, folder, settings, signals.progress.emit)
            self.store.update(record_id, duration=metadata['duration'])
            return self.transcriber.run(folder, settings, signals.progress.emit)

        def done(result):
            self.store.save_transcript(record_id, result['segments'], original=True)
            self.store.update(record_id, status='ready', error='')
            self.finish_busy(record_id)
            if result.get('warnings'):
                self.notify('已生成文字稿。' + '；'.join(result['warnings']), True)
            else:
                self.notify('文字稿已生成。需要区分谁在说话时，点击“区分说话人”、填写人数后再开始。')
            self.prepare_captions()

        def failed(error):
            self.job_failed(record_id, error)
            self.prepare_captions()

        self.run_job(work, done, failed)

    def transcribe_region(self):
        item = self.timeline.currentItem()
        if not item or item.data(Qt.UserRole) is None:
            self.notify('先点击需要重新识别的段落，再点击“重新识别选中段”。')
            return
        if not self.current_id or self.busy_id or self.recorder:
            return
        record_id = self.current_id
        indices = item.data(Qt.UserRole + 1) or [item.data(Qt.UserRole)]
        original = self.store.transcript(record_id)
        selected = [original['segments'][i] for i in indices]
        region = dict(start=min(s['start'] for s in selected), end=max(s['end'] for s in selected),
                      track=selected[0]['track'])
        folder = self.store.folder(record_id)
        if not (folder / (region['track'] + '.wav')).is_file():
            self.notify('这段文字对应的原始音轨不存在。', True)
            return
        self.pause()
        self.busy_id, self.busy_type = record_id, 'transcription'
        self.store.update(record_id, status='transcribing', error='')
        self.live.stop()
        self.live = LiveTranscriber()
        self.warming = False
        self.enable_actions()
        self.notify(f'正在重新识别 {stamp(region["start"])} — {stamp(region["end"])}，其他段落保留…')

        def done(result):
            candidates = result['segments']
            # Keep human-assigned speaker names; ASR only replaces these words.
            speaker_fields = {k: selected[0][k] for k in ('speaker', 'speaker_id', 'speaker_run', 'speaker_manual', 'speaker_uncertain', 'speaker_inferred') if k in selected[0]}
            identities = {s.get('speaker_id') for s in selected}
            if len(identities) > 1:
                from .speaker_alignment import align_speakers
                data = read_json(folder / 'diarization.json', {})
                aliases = {s['speaker_id']: s['speaker'] for s in original['segments'] if s.get('speaker_id')}
                candidates = align_speakers(candidates, data, aliases)
            else:
                candidates = [{**s, **speaker_fields} for s in candidates]
            if candidates and not self.closing:
                dialog = QDialog(self)
                dialog.setWindowTitle('核对局部重新识别结果')
                dialog.resize(740, 540)
                layout = QVBoxLayout(dialog)
                layout.addWidget(QLabel(f'{stamp(region["start"])} — {stamp(region["end"])} · {selected[0]["speaker"]}'))
                layout.addWidget(QLabel('原文字稿'))
                old = QPlainTextEdit(reduce(join_text, (s['text'] for s in selected), ''))
                old.setReadOnly(True)
                layout.addWidget(old)
                layout.addWidget(QLabel('新的识别结果（可修改后替换）'))
                new_text = reduce(join_text, (s['text'] for s in candidates), '')
                new = QPlainTextEdit(new_text)
                layout.addWidget(new)
                controls = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
                controls.button(QDialogButtonBox.Save).setText('替换选中段')
                controls.button(QDialogButtonBox.Cancel).setText('保留原文')
                controls.accepted.connect(dialog.accept)
                controls.rejected.connect(dialog.reject)
                layout.addWidget(controls)
                if dialog.exec() == QDialog.Accepted and new.toPlainText().strip():
                    if new.toPlainText().strip() != new_text:
                        if len(identities) > 1:
                            self.notify('这段包含不同或待确认的说话人。请用“修改选中段落”分别修改，避免把整段错误归给同一人。', True)
                            self.store.update(record_id, status='ready', error='')
                            self.finish_busy(record_id)
                            self.prepare_captions()
                            dialog.deleteLater()
                            return
                        candidates = [{**candidates[0], 'start': region['start'], 'end': region['end'],
                                       'text': new.toPlainText().strip(), 'review': False}]
                        candidates[0].pop('words', None)
                    removed = set(indices)
                    merged = [s for i, s in enumerate(original['segments']) if i not in removed] + candidates
                    merged.sort(key=lambda s: (s['start'], s['track']))
                    self.store.save_transcript(record_id, merged, original=True)
                dialog.deleteLater()
            self.store.update(record_id, status='ready', error='')
            self.finish_busy(record_id)
            if not candidates:
                self.notify('这段没有识别到可靠语音，已保留原文字稿。', True)
            self.prepare_captions()

        def failed(error):
            self.job_failed(record_id, error)
            self.prepare_captions()

        self.run_job(lambda signals: self.transcriber.run(folder, self.store.settings(), signals.progress.emit, region=region),
                     done, failed)

    def job_failed(self, record_id, error):
        self.store.update(record_id, status='ready' if self.store.transcript(record_id)['segments'] else 'saved', error=error)
        self.finish_busy(record_id)
        self.notify(error, True)

    def load_models(self):
        if self.busy_type == 'analysis':
            return
        self.model_status.setText('正在连接本机 Codex，只读取模型目录…')
        self.run_job(lambda signals: self.codex.models(), self.models_loaded,
                     lambda error: self.model_status.setText('连接失败：' + error))

    def models_loaded(self, models):
        self.models = models
        settings = self.store.settings()
        previous = settings.get('model')
        self.model_box.blockSignals(True)
        self.model_box.clear()
        chosen = 0
        for index, model in enumerate(models):
            self.model_box.addItem(model.get('displayName', model['model']), model['model'])
            if model['model'] == previous or (not previous and model.get('isDefault')):
                chosen = index
        self.model_box.setCurrentIndex(chosen)
        self.model_box.blockSignals(False)
        if previous and not any(m['model'] == previous for m in models):
            self.model_box.setCurrentIndex(-1)
            self.effort_box.clear()
            self.model_status.setText('上次模型已不在目录，请重新选择；未自动替换。')
        else:
            self.model_changed()
            self.model_status.setText('已读取本机模型目录；实际访问以提交结果为准。')
        self.enable_actions()

    def model_changed(self, *args):
        model = next((m for m in self.models if m['model'] == self.model_box.currentData()), None)
        if not model:
            return
        settings = self.store.settings()
        last = settings.get('effort') if settings.get('model') == model['model'] else model.get('defaultReasoningEffort')
        self.effort_box.blockSignals(True)
        self.effort_box.clear()
        for entry in model.get('supportedReasoningEfforts', []):
            effort = entry['reasoningEffort']
            self.effort_box.addItem(f'{EFFORT.get(effort, effort)}  /  {effort}', effort)
            self.effort_box.setItemData(self.effort_box.count() - 1, entry.get('description', ''), Qt.ToolTipRole)
        index = self.effort_box.findData(last)
        self.effort_box.setCurrentIndex(index)
        self.effort_box.blockSignals(False)
        self.save_model_choice()

    def save_model_choice(self, *args):
        model, effort = self.model_box.currentData(), self.effort_box.currentData()
        if model and effort:
            self.store.set_settings(model=model, effort=effort)

    def submit_report(self):
        self.submit(report_prompt(self.template_box.currentData(), REPORT))

    def submit_question(self):
        question = self.question.toPlainText().strip()
        if not question:
            self.notify('请先输入想问的问题。')
            return
        self.submit(question_prompt(self.template_box.currentData(), question))

    def submit(self, question):
        if not self.current_id or self.busy_id or not self.segments:
            return
        model, effort = self.model_box.currentData(), self.effort_box.currentData()
        if not model or not effort:
            self.notify('请先选择模型和该模型支持的思考档位。', True)
            return
        value = self.store.transcript(self.current_id)
        record_id = self.current_id
        messages = self.store.messages(record_id)
        self.pause()
        self.streamed = ''
        self.busy_id, self.busy_type = record_id, 'analysis'
        self.store.update(record_id, status='analyzing', error='')
        self.enable_actions()
        self.ai_button.setChecked(True)
        def stream_content():
            answer = self.linked(self.streamed, value['revision']) or '正在分析…'
            if is_report(question):
                prior = self.analysis_markdown(messages, include_report=False)
                return answer + ('\n\n---\n\n' + prior if prior else '')
            prior = self.analysis_markdown(messages)
            return (prior + '\n\n---\n\n' if prior else '') + '## 追问：' + html.escape(question_label(question).replace('\n', ' ')) + '\n\n' + answer
        self.report_view.setMarkdown(stream_content())
        def progress_chunk(chunk):
            self.streamed += chunk
            if self.current_id == record_id:
                view = self.report_view
                view.setMarkdown(stream_content())
                view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
        def complete(result):
            self.store.update(record_id, status='done', error='')
            self.question.clear()
            self.finish_busy(record_id)
        self.run_job(lambda signals: self.codex.analyze(self.store, record_id, question, model, effort,
                                                       signals.progress.emit, signals.delta.emit),
                     complete, lambda error: self.job_failed(record_id, error), progress_chunk)

    def cancel_analysis(self):
        self.codex.interrupt()
        self.notify('正在取消分析，录音与文字稿保留…')

    def edit_segment(self):
        item = self.timeline.currentItem()
        if not item or item.data(Qt.UserRole) is None or self.busy_id or not self.current_id:
            return
        index = item.data(Qt.UserRole)
        indices = item.data(Qt.UserRole + 1) or [index]
        segment = next((turn for turn in speaker_turns(self.segments) if turn['indices'][0] == index), self.segments[index])
        if segment.get('contains_unresolved') and len(indices) > 1:
            choices = [f'{stamp(self.segments[i]["start"])} — {stamp(self.segments[i]["end"])}  '
                       + self.segments[i]['speaker'] + '：' + self.segments[i]['text'][:40] for i in indices]
            choice, accepted = QInputDialog.getItem(self, '选择要修正的原始片段',
                '这段阅读内容包含待确认的发言，请选择具体片段：', choices, 0, False)
            if not accepted:
                return
            index = indices[choices.index(choice)]
            indices, segment = [index], self.segments[index]
        dialog = QDialog(self)
        dialog.setWindowTitle('修改文字稿（原始录音不变）')
        dialog.resize(630, 350)
        layout = QVBoxLayout(dialog)
        speaker = StudioComboBox()
        speaker.setEditable(True)
        identities = {}
        for value in self.segments:
            identities.setdefault(value['speaker'], value.get('speaker_id'))
        for name, speaker_id in identities.items():
            speaker.addItem(name, speaker_id)
        speaker.setCurrentText(segment['speaker'])
        layout.addWidget(QLabel('说话方'))
        layout.addWidget(speaker)
        text = QPlainTextEdit(segment['text'])
        layout.addWidget(text)
        controls = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        if dialog.exec() == QDialog.Accepted:
            if not text.toPlainText().strip() or not speaker.currentText().strip():
                return
            replacement = {**self.segments[index], 'end': segment['end'],
                           'text': text.toPlainText().strip(), 'speaker': speaker.currentText().strip(),
                           'review': False, 'echo_suspected': False}
            replacement.pop('words', None)
            if segment['track'] in ('imported', 'system') and speaker.currentText() != segment['speaker']:
                replacement['speaker_id'] = identities.get(speaker.currentText()) or 'manual-' + speaker.currentText().strip()
                replacement['speaker_manual'] = True
                replacement['speaker_uncertain'] = False
            self.segments = [replacement if i == index else old for i, old in enumerate(self.segments)
                             if i == index or i not in indices]
            self.store.save_transcript(self.current_id, self.segments)
            self.show_record(self.current_id)
            self.notify('已保存修订版。下次点击分析会提交新版文字稿，原有问答保留。')

    def speakers_dialog(self):
        if not self.current_id or self.busy_id or self.recorder:
            return
        record_id = self.current_id
        value = self.store.transcript(record_id)
        speakers = {}
        for segment in value['segments']:
            if segment['track'] in ('imported', 'system'):
                key = segment.get('speaker_id') or 'legacy:' + segment['speaker']
                speakers.setdefault(key, segment['speaker'])
        if not speakers:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('区分说话人 · 本次录音')
        dialog.resize(610, 500)
        layout = QVBoxLayout(dialog)
        note = QLabel('先填写录音中实际说话的人数，再点击“开始区分说话人”。\n这一步只在你点击后运行，文字内容和原始音频保留。编号代表不同声音，不代表真实身份。')
        if any(s['track'] == 'system' for s in value['segments']):
            note.setText('填写电脑声音中实际说话的人数，再点击“开始区分说话人”；人数不包含你的麦克风。\n你的麦克风仍保留为“我”。若电脑中有比赛解说等节目声，也会参与分组。\n这一步只在你点击后运行，文字内容和原始音频保留。')
        note.setWordWrap(True)
        note.setObjectName('subtitle')
        layout.addWidget(note)
        # Up to twenty participants remain usable on small desktop windows.
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        fields = QWidget()
        form = QFormLayout(fields)
        names = {}
        classified = any(s.get('speaker_id') not in (None, 'unknown') for s in value['segments'] if s['track'] in ('system','imported'))
        for speaker_id, name in speakers.items() if classified else []:
            field = QLineEdit(name)
            field.setObjectName(speaker_id)
            form.addRow(name, field)
            names[speaker_id] = field
        scroll.setWidget(fields)
        if classified:
            layout.addWidget(scroll, 1)
        else:
            scroll.setParent(dialog)
            scroll.hide()
            pending = QLabel('尚未区分说话人。当前文字稿可以直接阅读、导出或提交分析。')
            pending.setObjectName('subtitle')
            pending.setWordWrap(True)
            layout.addWidget(pending)
            dialog.resize(610, 300)
        count = QSpinBox()
        count.setObjectName('speakerCount')
        count.setRange(1, 20)
        count.setSuffix(' 人')
        data = read_json(self.store.folder(record_id) / 'diarization.json', {})
        count.setValue(data.get('requested_speakers') or 2)
        count_form = QFormLayout()
        count_form.addRow('录音中的人数', count)
        layout.addLayout(count_form)
        action = {'identify': False}
        retry = button('开始区分说话人', lambda: (action.update(identify=True), dialog.reject()), 'blue')
        layout.addWidget(retry)
        controls = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        controls.button(QDialogButtonBox.Save).setText('保存姓名')
        controls.button(QDialogButtonBox.Save).setVisible(classified)
        controls.button(QDialogButtonBox.Cancel).setText('取消')
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        accepted = dialog.exec() == QDialog.Accepted
        if accepted:
            mapping = {key: field.text().strip() for key, field in names.items()}
            if not all(mapping.values()):
                self.notify('姓名不能为空，原来的名称保留。', True)
            else:
                updated = [{**s, 'speaker': mapping.get(s.get('speaker_id') or 'legacy:' + s['speaker'], s['speaker'])}
                           if s['track'] in ('imported', 'system') else s for s in value['segments']]
                if updated != value['segments']:
                    self.store.save_transcript(record_id, updated)
                    runs = {s['speaker_run'] for s in value['segments'] if s.get('speaker_run') and not s.get('speaker_manual')}
                    if len(runs) == 1:
                        atomic_json(self.store.folder(record_id) / 'speaker-names.json',
                            dict(signature=next(iter(runs)), names=mapping))
                    self.show_record(record_id)
                    self.notify('姓名已更新。原文字稿版本及分析引用保留，下次分析使用新的姓名。')
        requested = count.value()
        dialog.deleteLater()
        if action['identify']:
            self.reidentify_speakers(requested)

    def reidentify_speakers(self, count):
        if self.busy_id or self.recorder or not self.current_id:
            return
        if type(count) is not int or not 1 <= count <= 20:
            self.notify('请先填写实际说话人数（1–20 人），再开始区分。', True)
            return
        record_id = self.current_id
        self.pause()
        self.busy_id, self.busy_type = record_id, 'diarization'
        self.store.update(record_id, status='transcribing', error='')
        self.enable_actions()
        self.notify(f'正在按 {count} 人区分说话人，文字内容和原音频保留…')
        def done(result):
            if self.closing:
                return
            self.store.save_transcript(record_id, result['segments'])
            atomic_json(self.store.folder(record_id) / 'speaker-settings.json', dict(speakers=count))
            self.store.update(record_id, status='ready', error='')
            self.finish_busy(record_id)
            number = result['diarization']['detected_speakers']
            from .speaker_alignment import count_warning
            warnings = list(result.get('warnings', []))
            mismatch = count_warning(result['diarization'], result['segments'])
            if mismatch and mismatch not in warnings:
                warnings.append(mismatch)
            message = f'当前分为 {number} 个声音组。不确定的归属已标记，可回听或修改。'
            if warnings:
                message = '；'.join(warnings)
            elif any(s['track'] in ('imported', 'system') and not s.get('words') for s in result['segments']):
                message += '旧稿没有词级时间，跨说话人的段落可能仍需核对；重新转录可细化。'
            self.notify(message, bool(warnings))
        self.run_job(lambda signals: self.transcriber.identify_speakers(self.store.folder(record_id), self.store.settings(), count,
                     signals.progress.emit), done, lambda error: self.job_failed(record_id, error))

    def rename(self):
        if not self.current_id:
            return
        title, ok = QInputDialog.getText(self, '重命名通话', '标题', text=self.store.get(self.current_id)['title'])
        if ok and title.strip():
            self.store.update(self.current_id, title=title.strip())
            self.title.setText(title.strip())
            self.refresh_history()

    def export(self):
        if not self.current_id:
            return
        record = self.store.get(self.current_id)
        filename, selected = QFileDialog.getSaveFileName(self, '导出文字稿', record['title'] + '.md', 'Markdown (*.md);;纯文本 (*.txt)')
        if not filename:
            return
        content = f'# {record["title"]}\n\n' + transcript_text(self.store.transcript(self.current_id))
        messages = self.store.messages(self.current_id)
        if messages:
            content += '\n\n# Codex 分析与问答\n\n' + '\n\n---\n\n'.join(
                f'## {"话题整理" if is_report(m["question"]) else m["question"]}\n\n'
                f'{m["requested_model"]} / {m["effort"]} / 文字稿 v{m["revision"]}\n\n{m["answer"]}' for m in messages)
        Path(filename).write_text(content, encoding='utf-8-sig')
        self.notify('已导出：' + filename)

    def toggle_trash(self):
        if self.recorder or self.busy_id:
            return
        self.trash = not self.trash
        self.trash_button.setText('返回通话记录' if self.trash else '回收区')
        self.current_id = None
        self.refresh_history()
        self.new_record()
        if self.trash:
            self.notify('回收区中的记录仍保存在本机，可以恢复；不会自动永久清空。')

    def delete_record(self):
        if not self.current_id or self.busy_id or self.recorder:
            return
        record = self.store.get(self.current_id)
        self.pause()
        self.store.update(self.current_id, deleted=0 if record['deleted'] else 1)
        self.current_id = None
        self.refresh_history()
        self.new_record()
        self.notify('记录已恢复。' if record['deleted'] else '已移入回收区，音频、文字稿与问答保留，可恢复。')

    def settings_dialog(self):
        if self.recorder or self.busy_id:
            self.notify('请先结束录音或处理任务，再修改设置。')
            return
        config = self.store.settings()
        audio_snapshot = [(combo, combo.currentData(), combo.currentText()) for combo in
                          (self.capture_mode, self.mic_box, self.system_box, self.app_box)]
        self.editing_audio_settings = True
        dialog = QDialog(self)
        dialog.setWindowTitle('设置 · 通话笺')
        dialog.resize(760, 540)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(18)
        title = QLabel('设置')
        title.setObjectName('headline')
        layout.addWidget(title)
        tabs = QTabWidget()
        tabs.setObjectName('settingsTabs')
        tabs.addTab(self.audio_settings, '录音与声音')
        self.audio_settings.show()
        recognition = QWidget()
        recognition_layout = QVBoxLayout(recognition)
        recognition_layout.setContentsMargins(0, 18, 0, 0)
        form = QFormLayout()
        form.setVerticalSpacing(16)
        data = QLineEdit(str(self.store.root))
        python = QLineEdit(config.get('asr_python', default_python()))
        model = QLineEdit(config.get('asr_model', ''))
        language = StudioComboBox()
        language.addItem('中文、英文和中英混说', 'zh_en')
        language.addItem('主要中文（保留英文词）', 'zh')
        language.addItem('主要英文', 'en')
        language.setCurrentIndex(max(0, language.findData(config.get('asr_language', 'zh_en'))))
        if bundled_worker().is_file():
            python.setText(default_python())
            form.addRow('识别引擎', QLabel('已内置，无需安装或配置 Python'))
        else:
            form.addRow('转录 Python', python)
        form.addRow('自选模型目录（留空使用内置／已有模型）', model)
        form.addRow('识别语言', language)
        form.addRow('说话人区分', QLabel('默认只生成文字稿；在单条记录中点击“区分说话人”并填写人数后运行。'))
        recognition_layout.addLayout(form)
        recognition_note = QLabel('语音识别在本机完成，不联网下载模型。\nCodex 分析只在你点击提交时运行，使用本机登录。')
        recognition_note.setObjectName('subtitle')
        recognition_note.setWordWrap(True)
        recognition_layout.addWidget(recognition_note)
        recognition_layout.addStretch()
        tabs.addTab(recognition, '文字识别')
        account = QWidget()
        account_layout = QVBoxLayout(account)
        account_layout.setContentsMargins(0, 18, 0, 0)
        account_note = QLabel('录音与字幕在本机完成，无需登录。\n使用 AI 分析时，请登录你自己的 Codex 账号；分析使用该账号的额度。\n点击下方按钮，在浏览器中完成登录，之后返回通话笺即可。')
        account_note.setWordWrap(True)
        account_layout.addWidget(account_note)
        login_status = QLabel('')
        login_status.setWordWrap(True)
        def login():
            try:
                start_login()
                login_status.setText('已打开 Codex 登录。请在浏览器完成操作，然后返回分析面板刷新模型。')
            except Exception as exc:
                login_status.setText('无法打开登录：' + str(exc))
        account_layout.addWidget(button('登录 Codex', login, 'blue'), 0, Qt.AlignLeft)
        account_layout.addWidget(login_status)
        account_layout.addStretch()
        tabs.addTab(account, 'AI 分析')
        local = QWidget()
        local_layout = QVBoxLayout(local)
        local_layout.setContentsMargins(0, 18, 0, 0)
        local_form = QFormLayout()
        local_form.addRow('数据目录', data)
        local_layout.addLayout(local_form)
        note = QLabel('目录变更在重启后生效，不会搬移或删除原有记录。\n音频与文字稿始终保存在本机。')
        note.setObjectName('subtitle')
        note.setWordWrap(True)
        local_layout.addWidget(note)
        local_layout.addWidget(button('打开当前数据文件夹', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root)))), 0, Qt.AlignLeft)
        local_layout.addStretch()
        tabs.addTab(local, '本地存储')
        reading_page = QWidget()
        reading_form = QFormLayout(reading_page)
        reading_form.setContentsMargins(0, 20, 0, 0)
        reading_form.setVerticalSpacing(18)
        font_size, spacing, width = QSpinBox(), QSpinBox(), QSpinBox()
        for control, key, limits, suffix in ((font_size, 'size', (14, 24), ' px'),
                (spacing, 'spacing', (140, 220), ' %'), (width, 'width', (480, 1100), ' px')):
            control.setRange(*limits)
            control.setValue(self.reading[key])
            control.setSuffix(suffix)
        font_size.setObjectName('readingSize')
        spacing.setObjectName('readingSpacing')
        width.setObjectName('readingWidth')
        reading_form.addRow('正文字号', font_size)
        reading_form.addRow('行间距', spacing)
        reading_form.addRow('最大阅读宽度', width)
        reading_note = QLabel('同时应用于实时字幕、完整文字稿、Codex 分析和引用原文。\n日间与夜间保持统一排版，重点使用加粗区分。')
        reading_note.setWordWrap(True)
        reading_note.setObjectName('subtitle')
        reading_form.addRow(reading_note)
        tabs.addTab(reading_page, '阅读排版')
        layout.addWidget(tabs, 1)
        controls = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        controls.button(QDialogButtonBox.Save).setText('保存设置')
        controls.button(QDialogButtonBox.Save).setObjectName('blue')
        controls.button(QDialogButtonBox.Cancel).setText('取消')
        saved = False

        def save():
            nonlocal saved
            target = Path(data.text().strip())
            if not target.is_absolute() or not Path(python.text().strip()).is_file():
                QMessageBox.warning(dialog, '设置未保存', '请填写绝对数据路径及存在的 Python 程序。')
                return
            try:
                if target != self.store.root:
                    target.mkdir(parents=True, exist_ok=True)
                    bootstrap = read_json(DEFAULT_ROOT / 'settings.json', {})
                    bootstrap['data_root'] = str(target)
                    atomic_json(DEFAULT_ROOT / 'settings.json', bootstrap)
                app = self.app_box.currentData()
                self.store.set_settings(asr_python=python.text().strip(), asr_model=model.text().strip(),
                    asr_language=language.currentData(), import_speakers=0, capture_mode=self.capture_mode.currentData(),
                    mic_device=self.mic_box.currentText(), system_device=self.system_box.currentText(),
                    voice_app=app['exe'] if app else config.get('voice_app', ''),
                    reading_size=font_size.value(), reading_spacing=spacing.value(), reading_width=width.value())
                saved = True
                dialog.accept()
            except Exception as exc:
                QMessageBox.warning(dialog, '设置未保存', str(exc))

        controls.accepted.connect(save)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        try:
            dialog.exec()
        finally:
            # The long-lived capture controls survive destruction of the dialog.
            self.audio_settings.setParent(self)
            self.audio_settings.hide()
            self.editing_audio_settings = False
            if not saved:
                for combo, value, text in audio_snapshot:
                    index = combo.findData(value)
                    if index < 0:
                        index = combo.findText(text)
                    was_blocked = combo.blockSignals(True)
                    combo.setCurrentIndex(index)
                    combo.blockSignals(was_blocked)
            self.update_capture_mode()
            dialog.deleteLater()
        if saved:
            self.apply_reading()
            self.notify('设置已保存，下次录音使用新的声音来源。数据目录变更需重启生效。')

    def prepare_players(self, record_id):
        self.pause()
        for player in self.players.values():
            player.stop()
            player.deleteLater()
        for output in self.outputs.values():
            output.deleteLater()
        self.players, self.outputs, self.offsets = {}, {}, {}
        meta = read_json(self.store.folder(record_id) / 'audio.json', {})
        for track in meta.get('tracks', []):
            path = self.store.folder(record_id) / (track['key'] + '.wav')
            if path.exists():
                player = QMediaPlayer(self)
                output = QAudioOutput(self)
                output.setVolume(self.volume.value() / 100)
                player.setAudioOutput(output)
                player.mediaStatusChanged.connect(
                    lambda status, p=player: p.setPlaybackRate(self.playback_rate)
                    if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) else None)
                player.setSource(QUrl.fromLocalFile(str(path)))
                player.setPlaybackRate(self.playback_rate)
                self.players[track['key']] = player
                self.outputs[track['key']] = output
                self.offsets[track['key']] = track.get('offset', 0.)
        self.slider.setRange(0, int(self.store.get(record_id)['duration'] * 1000))
        self.play_position = 0.
        self.slider.setValue(0)
        self.play_label.setText('00:00 / ' + stamp(self.store.get(record_id)['duration']))

    def set_volume(self, value):
        for output in self.outputs.values():
            output.setVolume(value / 100)

    def pause(self):
        self.play_position = self.playback_position()
        self.playing = False
        self.play_anchor = None
        for player in self.players.values():
            player.pause()
        if hasattr(self, 'play_button'):
            self.play_button.setText('播放')
            self.play_button.setIcon(playback_icon())
        self.update_playback_display()

    def playback_duration(self):
        record = self.store.get(self.current_id) if self.current_id else None
        return max(0., record['duration']) if record else 0.

    def playback_position(self):
        position = self.play_position
        if self.playing and self.play_anchor is not None:
            position += (time.perf_counter() - self.play_anchor) * self.playback_rate
        return min(self.playback_duration(), max(0., position))

    def update_playback_display(self):
        if not hasattr(self, 'play_label'):
            return
        position = self.playback_position()
        if not self.slider.isSliderDown():
            self.slider.setValue(round(position * 1000))
        self.play_label.setText(stamp(position) + ' / ' + stamp(self.playback_duration()))

    def change_playback_rate(self):
        rate = self.speed_box.currentData()
        if rate not in PLAYBACK_RATES or rate == self.playback_rate:
            return
        # Rebase the clock at the old rate. Updating an active Windows player
        # directly avoids racing asynchronous pause/seek/play commands.
        self.play_position = self.playback_position()
        self.play_anchor = time.perf_counter() if self.playing else None
        self.playback_rate = rate
        for player in self.players.values():
            player.setPlaybackRate(rate)
        self.store.set_settings(playback_rate=rate)
        self.update_playback_display()

    def skip_playback(self, seconds):
        if self.players and not self.recorder:
            self.seek(self.playback_position() + seconds, start=self.playing)

    def playback_key_allowed(self, target, event):
        if (self.closing or self.recorder or not self.current_id or not self.players
                or not self.review_page.isVisible() or not isinstance(target, QWidget)
                or target.window() is not self
                or QApplication.activeModalWidget() or QApplication.activePopupWidget()
                or event.modifiers() != Qt.NoModifier
                or event.key() not in (Qt.Key_Space, Qt.Key_Left, Qt.Key_Right)):
            return False
        # Ignored keys can bubble from a combo/editor to its parent; check the
        # focus widget too so propagation cannot turn them into playback keys.
        for origin in (target, QApplication.focusWidget()):
            widget = origin
            while widget and widget is not self:
                if isinstance(widget, (QLineEdit, QComboBox, QAbstractSpinBox, QSlider)):
                    return False
                if isinstance(widget, (QPlainTextEdit, QTextEdit)) and not widget.isReadOnly():
                    return False
                widget = widget.parentWidget()
        return True

    def eventFilter(self, target, event):
        if event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress) and self.playback_key_allowed(target, event):
            event.accept()
            if event.type() == QEvent.KeyPress:
                if event.key() == Qt.Key_Space:
                    if not event.isAutoRepeat():
                        self.toggle_play()
                else:
                    self.skip_playback(-5 if event.key() == Qt.Key_Left else 5)
            return True
        return super().eventFilter(target, event)

    def seek(self, seconds, start=True):
        seconds = min(self.playback_duration(), max(0., seconds))
        self.play_position = seconds
        start = bool(start and self.players and seconds < self.playback_duration())
        self.playing = start
        self.play_anchor = time.perf_counter() if start else None
        for key, player in self.players.items():
            offset = self.offsets[key]
            if not start or seconds < offset:
                player.pause()
            player.setPosition(max(0, int((seconds - offset) * 1000)))
            if start and seconds >= offset:
                player.play()
        self.play_button.setText('暂停' if start else '播放')
        self.play_button.setIcon(playback_icon(start))
        self.update_playback_display()

    def toggle_play(self):
        if self.playing:
            self.pause()
        elif self.players:
            duration = self.store.get(self.current_id)['duration']
            self.seek(0 if self.play_position >= duration else self.play_position)

    def seek_slider(self):
        self.seek(self.slider.value() / 1000, self.playing)

    def play_segment(self, item):
        index = item.data(Qt.UserRole)
        if index is not None:
            self.seek(self.segments[index]['start'])

    def citation_value(self, record_id, revision):
        current = self.store.transcript(record_id)
        if revision and revision != current['revision']:
            try:
                return self.store.transcript_version(record_id, revision), True
            except (ValueError, OSError):
                return {'revision': revision, 'segments': []}, True
        return current, False

    def open_citation(self, url, record_id=None):
        record_id = record_id or self.current_id
        if not record_id:
            return
        try:
            if url.scheme() == 'source':
                revision, indices, selected_index = parse_source_target(url.toString())
                value, old = self.citation_value(record_id, revision)
                sources = [(i, value['segments'][i]) for i in indices if 0 <= i < len(value['segments'])]
                if not sources:
                    self.notify('该引用对应的文字稿来源未找到，已有录音和报告仍保留。', True)
                    return
                parent = (self.reading_dialog if self.reading_dialog
                    and self.reading_dialog.isVisible() and self.reading_dialog.property('recordId') == record_id else self)
                if self.source_dialog:
                    self.source_dialog.close()
                dialog = SourceDialog(sources, value['revision'], old,
                    lambda rev, index, play: self.locate_source(rev, index, play, record_id),
                    parent=parent, missing=len(indices) - len(sources), selected_index=selected_index)
                self.source_dialog = dialog
                dialog.quote.reading = self.reading
                dialog.quote.set_reading_style(theme.MODE)
                dialog.setAttribute(Qt.WA_DeleteOnClose)
                def clear_source():
                    if self.source_dialog is dialog:
                        self.source_dialog = None
                dialog.finished.connect(clear_source)
                dialog.destroyed.connect(clear_source)
                dialog.show()
                # Keep the transcript visible to the left of the modeless source
                # window, rather than centering it over the selected paragraph.
                dialog.resize(min(780, max(600, self.width() // 2)), 510)
                corner = self.ai_panel.mapToGlobal(self.ai_panel.rect().topLeft())
                screen = self.screen().availableGeometry()
                dialog.move(min(corner.x(), screen.right() - dialog.width()),
                            max(screen.top(), min(corner.y(), screen.bottom() - dialog.height())))
                dialog.raise_()
            elif url.scheme() == 'segment':
                revision, index = map(int, url.path().split(':'))
                self.locate_source(revision, index, True, record_id)
        except (ValueError, IndexError):
            self.notify('这条来源链接无效，无法定位。', True)

    def locate_source(self, revision, index, play=False, record_id=None):
        if self.recorder:
            self.notify('请先停止本次录音，再定位其他通话的原文。')
            return
        record_id = record_id or self.current_id
        value, old = self.citation_value(record_id, revision)
        if not 0 <= index < len(value['segments']):
            self.notify('引用的文字稿段落不可用。', True)
            return
        if self.current_id != record_id:
            self.refresh_history(record_id=record_id)
            self.show_record(record_id)
        segment = value['segments'][index]
        self.filter.clear()
        for row in range(self.timeline.count()):
            self.timeline.item(row).setData(Qt.UserRole + 3, None)
        if old:
            self.notify(f'引用来自旧版文字稿 v{revision}，按当时的原文时间定位；当前修订版保持不变。')
        for row in range(self.timeline.count()):
            item = self.timeline.item(row)
            turn = item.data(Qt.UserRole + 2)
            matches = (turn['start'] <= segment['start'] <= turn['end'] and turn['track'] == segment['track']) if old else index in turn['indices']
            if not matches:
                continue
            focus = {'revision': revision, 'index': index, 'range': None}
            if not old:
                prefix = ''
                for member in turn['indices']:
                    joined = join_text(prefix, simplified(self.segments[member]['text']))
                    if member == index:
                        focus['range'] = (len(joined) - len(simplified(segment['text']).lstrip()), len(joined))
                        break
                    prefix = joined
            item.setData(Qt.UserRole + 3, focus)
            self.timeline.setCurrentItem(item)
            self.timeline.scrollToItem(item, QAbstractItemView.PositionAtCenter)
            # A turn can be longer than the viewport: reveal the exact highlighted
            # line rather than only scrolling to the middle of the whole turn.
            if focus['range']:
                model_index = self.timeline.indexFromItem(item)
                doc = self.timeline.itemDelegate().document(model_index, self.timeline.viewport().width())
                doc.size()  # Complete layout before taking QTextLine handles.
                body = doc.begin().next()
                block_top = doc.documentLayout().blockBoundingRect(body).top()
                utf16 = len(turn['text'][:focus['range'][0]].encode('utf-16-le')) // 2
                line = body.layout().lineForTextPosition(utf16)
                if line.isValid():
                    y = block_top + line.y() + 14
                    scroll = self.timeline.verticalScrollBar()
                    scroll.setValue(scroll.value() + self.timeline.visualItemRect(item).top() + int(y) - self.timeline.viewport().height() // 3)
            self.timeline.viewport().update()
            break
        if play:
            self.seek(segment['start'])
        else:
            self.seek(segment['start'], start=False)
        if self.source_dialog:
            self.source_dialog.status.setText('已定位并开始回听 · 对应文字稿已高亮' if not old else '正在回听旧版原话 · 文字稿按对应时间标记')

    def tick(self):
        if self.recorder:
            elapsed = getattr(self.recorder, 'elapsed', time.perf_counter() - self.recorder.started)
            self.clock.setText(f'{int(elapsed) // 3600:02}:{int(elapsed) // 60 % 60:02}:{int(elapsed) % 60:02}')
            self.waveform.push(elapsed, self.recorder.levels)
            if self.recorder.errors:
                self.stop_recording(False)
        elif self.playing and self.current_id:
            position = self.playback_position()
            duration = self.store.get(self.current_id)['duration']
            if position >= duration:
                self.pause()
                self.play_position = duration
            else:
                for key, player in self.players.items():
                    expected = max(0, int((position - self.offsets[key]) * 1000))
                    if position >= self.offsets[key]:
                        if player.playbackState() != QMediaPlayer.PlayingState:
                            player.play()
                        if abs(player.position() - expected) > 250:
                            player.setPosition(expected)
            self.update_playback_display()

    def closeEvent(self, event):
        if self.recorder:
            try:
                self.stop_recording(auto_transcribe=False)
            except Exception:
                event.ignore()
                return
        self.pause()
        self.closing = True
        QApplication.instance().removeEventFilter(self)
        for dialog in (self.source_dialog, self.version_dialog, self.reading_dialog, self.changelog_dialog):
            if dialog:
                dialog.close()
        for player in self.players.values():
            player.stop()
            player.setSource(QUrl())
        self.transcriber.stop()
        if self.live.folder:
            self.live.finish(self.live.folder)
        self.live.stop()
        self.codex.close()
        if self.busy_id:
            self.store.update(self.busy_id, status='ready' if self.store.transcript(self.busy_id)['segments'] else 'saved',
                              error='关闭时处理已停止，录音保留，可重新处理。')
        self.pool.waitForDone(4000)
        event.accept()

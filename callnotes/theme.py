"""The user-selected dark recording studio palette and desktop controls."""
import re
from PySide6.QtGui import QColor, QPalette

BACKGROUND = '#222a36'
SIDEBAR = '#1a2230'
PANEL = '#273140'
TEXT = '#e4ebf6'
MUTED = '#a6b5c9'
BORDER = '#384457'
ACCENT = '#a6c3f1'
SELECTION = '#35445b'
RED = '#f1a3ae'
MODE = 'night'
DAY_COLORS = {
    '#222a36': '#ffffff', '#1a2230': '#f4f6f9', '#273140': '#fafbfc',
    '#e4ebf6': '#242b36', '#a6b5c9': '#5c6879', '#384457': '#dce2e9',
    '#a6c3f1': '#285da8', '#35445b': '#e3edf9', '#f1a3ae': '#ad3047',
    '#1d2633': '#ffffff', '#303d50': '#eaf1fa', '#61738c': '#94a6bd',
    '#7897c4': '#346ab6', '#c8dcfa': '#244f89', '#718097': '#7e8794',
    '#313b4b': '#e1e5ea', '#182231': '#ffffff', '#bfd5f7': '#214e8e',
    '#8195b2': '#748397', '#402e39': '#fff0f2', '#526077': '#b9c4d2',
    '#2c3747': '#e9eef4', '#8098bb': '#6683a9',
}
BASE_COLORS = {key: globals()[key] for key in
               ('BACKGROUND', 'SIDEBAR', 'PANEL', 'TEXT', 'MUTED', 'BORDER', 'ACCENT', 'SELECTION', 'RED')}


def color(value):
    return DAY_COLORS.get(value, value) if MODE == 'day' else value


def stylesheet():
    return re.sub(r'#[0-9a-fA-F]{6}', lambda m: color(m[0]), STYLE)


def configure_app(app, mode='night'):
    global MODE
    MODE = mode if mode in ('day', 'night') else 'night'
    for key, value in BASE_COLORS.items():
        globals()[key] = color(value)
    # A consistent palette also covers popup lists, dialogs and disabled text.
    app.setStyle('Fusion')
    palette = QPalette()
    for role, value in {
        QPalette.Window: BACKGROUND, QPalette.WindowText: TEXT,
        QPalette.Base: SIDEBAR, QPalette.AlternateBase: PANEL,
        QPalette.Text: TEXT, QPalette.Button: PANEL, QPalette.ButtonText: TEXT,
        QPalette.ToolTipBase: PANEL, QPalette.ToolTipText: TEXT,
        QPalette.Highlight: SELECTION, QPalette.HighlightedText: TEXT,
        QPalette.Link: ACCENT, QPalette.LinkVisited: ACCENT,
        QPalette.PlaceholderText: MUTED,
    }.items():
        palette.setColor(role, QColor(value))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(color('#718097')))
    app.setPalette(palette)


STYLE = '''
QMainWindow,QDialog,QMessageBox {background:#222a36;color:#e4ebf6;}
QWidget {font-family:"Microsoft YaHei UI";font-size:14px;color:#e4ebf6;}
QFrame#sidebar {background:#1a2230;border:0;}
QLabel#brand {font-size:23px;font-weight:600;}
QLabel#subtitle {font-size:12px;color:#a6b5c9;}
QLabel#headline {font-size:24px;font-weight:600;}
QLabel#sectionTitle {font-size:15px;font-weight:500;}
QLabel#clock {font-size:29px;font-weight:500;color:#e4ebf6;}
QLabel#emptyTitle {font-size:23px;font-weight:600;color:#e4ebf6;}
QLabel#emptyNote {font-size:14px;color:#a6b5c9;}
QFrame#recordBar {background:#273140;border:1px solid #384457;border-radius:12px;}
QLabel#pill {font-size:12px;color:#a6b5c9;}
QLabel#pill[active="true"] {color:#f1a3ae;}
QLabel#notice {color:#a6b5c9;background:#273140;border-radius:7px;padding:9px 12px;font-size:12px;}
QLabel#notice[error="true"] {color:#f1a3ae;background:#402e39;}
QFrame#recordDesk {border-top:1px solid #384457;border-bottom:1px solid #384457;}
QFrame#analysisPanel {background:#273140;border-left:1px solid #384457;}
QWidget#devicePanel {background:#273140;border-radius:8px;}
QPushButton {background:transparent;border:1px solid #384457;border-radius:7px;padding:8px 12px;}
QPushButton:hover {background:#303d50;border-color:#61738c;}
QPushButton:pressed {background:#35445b;}
QPushButton:checked {background:#35445b;border-color:#7897c4;color:#c8dcfa;}
QPushButton:focus {border-color:#a6c3f1;}
QPushButton:disabled {color:#718097;border-color:#313b4b;background:transparent;}
QPushButton#primary {background:#a6c3f1;color:#182231;border:0;font-weight:500;padding:10px 14px;}
QPushButton#primary:hover {background:#bfd5f7;}
QPushButton#danger {background:#a5394a;color:#ffffff;border:0;padding:11px 15px;}
QPushButton#danger:hover {background:#be4658;}
QPushButton#blue {background:#a6c3f1;color:#182231;border:0;}
QPushButton#blue:disabled,QPushButton#primary:disabled {background:#35445b;color:#8195b2;}
QPushButton#quiet {border:0;color:#a6b5c9;}
QPushButton#quiet:checked {background:#35445b;color:#e4ebf6;}
QToolButton {background:transparent;border:1px solid #384457;border-radius:7px;padding:8px 18px 8px 12px;}
QToolButton:hover {background:#303d50;border-color:#61738c;}
QToolButton:disabled {color:#718097;border-color:#313b4b;}
QToolButton::menu-indicator {subcontrol-origin:padding;subcontrol-position:right center;right:5px;}
QMenu {background:#273140;color:#e4ebf6;border:1px solid #384457;border-radius:7px;padding:6px;}
QMenu::item {padding:9px 28px 9px 14px;border-radius:4px;}
QMenu::item:selected {background:#35445b;color:#e4ebf6;}
QMenu::item:disabled {color:#718097;}
QMenu::separator {height:1px;background:#384457;margin:5px 8px;}
QLineEdit,QPlainTextEdit,QComboBox {background:#1d2633;border:1px solid #384457;border-radius:6px;padding:8px;selection-background-color:#35445b;selection-color:#e4ebf6;}
QLineEdit:focus,QPlainTextEdit:focus,QComboBox:focus {border-color:#7897c4;}
QComboBox {padding-right:24px;}
QComboBox::drop-down {border:0;width:23px;}
QComboBox QAbstractItemView {background:#273140;color:#e4ebf6;border:1px solid #61738c;selection-background-color:#35445b;}
QListWidget {border:0;background:transparent;outline:none;}
QListWidget::item {padding:12px 10px;margin:3px 0;border-radius:7px;}
QListWidget::item:selected {background:#35445b;color:#e4ebf6;}
QListWidget#versionList::item {padding:8px 10px;margin:2px 0;}
QListWidget#timeline::item,QListWidget#history::item {border:0;border-radius:0;margin:0;padding:0;}
QListWidget#history::item:selected,QListWidget#timeline::item:selected {background:transparent;}
QTextBrowser {border:0;background:transparent;padding:10px;color:#e4ebf6;font-size:14px;selection-background-color:#35445b;}
QTabWidget::pane {border:0;}
QTabBar::tab {padding:11px 15px;color:#a6b5c9;border-bottom:2px solid transparent;}
QTabBar::tab:selected {color:#c8dcfa;border-bottom:2px solid #a6c3f1;}
QSplitter::handle {background:#384457;width:1px;}
QSlider::groove:horizontal {height:4px;background:#384457;border-radius:2px;}
QSlider::handle:horizontal {background:#a6c3f1;width:12px;margin:-4px 0;border-radius:6px;}
QScrollBar:vertical {background:transparent;width:8px;margin:4px 0;}
QScrollBar::handle:vertical {background:#526077;border-radius:4px;min-height:32px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}
QToolTip {background:#273140;color:#e4ebf6;border:1px solid #61738c;padding:6px;}
'''

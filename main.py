import os
import sys
from pathlib import Path

os.environ.setdefault('QT_MEDIA_BACKEND', 'windows')
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ.setdefault('TEMP', r'D:\Apps\CallNotes-Cache\tmp')
os.environ.setdefault('TMP', os.environ['TEMP'])

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox
from callnotes.ui import MainWindow
from callnotes.theme import configure_app
from callnotes.windows_identity import set_app_identity, set_native_icons
from callnotes.release import VERSION


def main():
    set_app_identity()
    app = QApplication(sys.argv)
    app.setApplicationName('CallNotes')
    app.setApplicationVersion(VERSION)
    app.setOrganizationName('CallNotesLocal')
    icon = Path(__file__).resolve().parent / 'assets' / 'arknights-bighead-v1.ico'
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    configure_app(app)
    font = Path(r'C:\Windows\Fonts\msyh.ttc')
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont('Microsoft YaHei UI', 10))
    Path(r'D:\Apps\CallNotes-Cache\tmp').mkdir(parents=True, exist_ok=True)
    lock = QLockFile(r'D:\Apps\CallNotes-Cache\app.lock')
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, '通话笺', '软件已经打开，请使用现有窗口。')
        return 0
    window = MainWindow()
    if '--record' in sys.argv:
        index = sys.argv.index('--record')
        if index + 1 < len(sys.argv) and window.store.get(sys.argv[index + 1]):
            window.refresh_history(record_id=sys.argv[index + 1])
            window.show_record(sys.argv[index + 1])
    if icon.is_file():
        window.setWindowIcon(QIcon(str(icon)))
    window.show()
    if icon.is_file():
        set_native_icons(window, icon)
    if '--screenshot' in sys.argv:
        index = sys.argv.index('--screenshot')
        QTimer.singleShot(15000, lambda: window.grab().save(sys.argv[index + 1]))
    result = app.exec()
    lock.unlock()
    return result


if __name__ == '__main__':
    sys.exit(main())

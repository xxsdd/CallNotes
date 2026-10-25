import os
import sys
import json
from pathlib import Path

os.environ.setdefault('QT_MEDIA_BACKEND', 'windows')
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
from callnotes.runtime import CACHE_ROOT, prepare_environment
prepare_environment()

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
    lock = QLockFile(str(CACHE_ROOT / 'app.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, '通话笺', '软件已经打开，请使用现有窗口。')
        return 0
    window = MainWindow(prewarm='--self-test' not in sys.argv)
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
    if '--self-test' in sys.argv:
        index = sys.argv.index('--self-test')
        output = Path(sys.argv[index + 1])
        def self_test():
            from callnotes.runtime import bundled_worker, model_directory
            from callnotes.process_audio import support_error
            output.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(output.with_suffix('.png')))
            output.write_text(json.dumps({'version': VERSION, 'frozen': bool(getattr(sys, 'frozen', False)),
                'worker': bundled_worker().is_file(), 'model': str(model_directory({})),
                'capture_error': support_error(), 'data': str(window.store.root)}, indent=2), encoding='utf-8')
            window.close()
        QTimer.singleShot(1200, self_test)
    result = app.exec()
    lock.unlock()
    return result


if __name__ == '__main__':
    sys.exit(main())

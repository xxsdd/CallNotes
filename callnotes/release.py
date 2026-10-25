"""Application release identity; transcript revisions are independent."""
from pathlib import Path

VERSION = '0.1.3'
CHANGELOG = Path(__file__).resolve().parent.parent / 'CHANGELOG.md'


def changelog_text():
    try:
        return CHANGELOG.read_text(encoding='utf-8')
    except OSError:
        return '# 更新日志\n\n更新日志文件暂时无法读取。'

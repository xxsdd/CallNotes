from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
import wave
import re
from datetime import datetime
from pathlib import Path
from .text_normalization import simplified

from .runtime import DATA_ROOT as DEFAULT_ROOT


def atomic_json(path: Path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


class Store:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'records.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS records (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL,
            status TEXT NOT NULL, duration REAL DEFAULT 0, deleted INTEGER DEFAULT 0,
            error TEXT DEFAULT '', thread_id TEXT, thread_revision INTEGER DEFAULT -1)''')
        self.db.commit()

    def folder(self, record_id):
        # IDs originate here or from the local DB; validate even on recovery.
        uuid.UUID(str(record_id))
        path = self.root / 'recordings' / record_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create(self, title=None):
        record_id = str(uuid.uuid4())
        now = datetime.now().astimezone().isoformat(timespec='seconds')
        with self.lock:
            self.db.execute('INSERT INTO records(id,title,created,status) VALUES(?,?,?,?)',
                            (record_id, title or datetime.now().strftime('通话 %m月%d日 %H:%M'), now, 'recording'))
            self.db.commit()
        self.folder(record_id)
        return record_id

    def get(self, record_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM records WHERE id=?', (record_id,)).fetchone()
            return dict(row) if row else None

    def listing(self, query='', deleted=False):
        with self.lock:
            rows = [dict(row) for row in self.db.execute(
                'SELECT * FROM records WHERE deleted=? ORDER BY created DESC', (int(deleted),))]
        if not query.strip():
            return rows
        needle = query.casefold()
        result = []
        for row in rows:
            haystack = row['title'] + '\n' + '\n'.join(s['text'] for s in self.transcript(row['id'])['segments'])
            if needle in haystack.casefold():
                result.append(row)
        return result

    def update(self, record_id, **values):
        allowed = {'title', 'status', 'duration', 'deleted', 'error', 'thread_id', 'thread_revision'}
        if not values or not set(values) <= allowed:
            raise ValueError('不支持的记录字段')
        with self.lock:
            self.db.execute('UPDATE records SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?',
                            (*values.values(), record_id))
            self.db.commit()

    def transcript(self, record_id):
        return read_json(self.folder(record_id) / 'transcript.json', {'revision': 0, 'segments': []})

    def transcript_version(self, record_id, revision):
        if type(revision) is not int or revision < 1:
            raise ValueError('无效的文字稿版本')
        with self.lock:
            current = self.transcript(record_id)
            path = self.folder(record_id) / f'transcript-v{revision}.json'
            if not path.exists():
                path = self.folder(record_id) / 'transcript-trash' / path.name
            value = current if current['revision'] == revision else read_json(path)
            if value is None:
                raise FileNotFoundError(f'文字稿 v{revision} 文件不存在')
            if value.get('revision') != revision or not isinstance(value.get('segments'), list):
                raise ValueError(f'文字稿 v{revision} 文件无效')
            return value

    def transcript_versions(self, record_id, deleted=False):
        with self.lock:
            folder = self.folder(record_id)
            current = self.transcript(record_id)
            paths = {}
            archive_folder = folder / 'transcript-trash' if deleted else folder
            for path in archive_folder.glob('transcript-v*.json'):
                match = re.fullmatch(r'transcript-v([1-9][0-9]*)\.json', path.name)
                if match:
                    paths[int(match[1])] = path
            if current['revision'] > 0 and not deleted:
                paths[current['revision']] = folder / 'transcript.json'
            entries = []
            for revision, path in sorted(paths.items(), reverse=True):
                try:
                    value = self.transcript_version(record_id, revision)
                    entries.append(dict(revision=revision, current=revision == current['revision'],
                        count=len(value['segments']), path=str(path), error='', deleted=deleted))
                except (ValueError, OSError) as exc:
                    entries.append(dict(revision=revision, current=False, count=0, path=str(path), error=str(exc), deleted=deleted))
            return entries

    def set_transcript_deleted(self, record_id, revision, deleted=True):
        """Move an archive into/out of a local recycle folder; keep old AI citations resolvable."""
        if type(revision) is not int or revision < 1:
            raise ValueError('无效的文字稿版本')
        with self.lock:
            if revision == self.transcript(record_id)['revision']:
                raise ValueError('当前版本不能移入回收区；可在文字稿中删除不需要的段落')
            folder = self.folder(record_id).resolve()
            normal = folder / f'transcript-v{revision}.json'
            recycled = folder / 'transcript-trash' / normal.name
            source, target = (normal, recycled) if deleted else (recycled, normal)
            # Fixed filenames + UUID record folder; also reject redirected folders.
            if not source.resolve().is_relative_to(folder) or not target.resolve().is_relative_to(folder):
                raise ValueError('文字稿文件路径无效')
            if not source.is_file():
                raise FileNotFoundError(f'文字稿 v{revision} 不在所选列表中')
            if target.exists():
                raise FileExistsError('目标版本已存在，未覆盖任何文件')
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)

    def delete_transcript_segments(self, record_id, indices, expected_revision):
        with self.lock:
            value = self.transcript(record_id)
            if value['revision'] != expected_revision:
                raise ValueError('文字稿已更新，请重新选择要删除的段落')
            if not indices or any(type(i) is not int or i < 0 or i >= len(value['segments']) for i in indices):
                raise ValueError('没有有效的选中段落')
            removed = set(indices)
            return self.save_transcript(record_id, [s for i, s in enumerate(value['segments']) if i not in removed])

    def restore_transcript(self, record_id, revision):
        with self.lock:
            previous = self.transcript_version(record_id, revision)
            return self.save_transcript(record_id, previous['segments'])

    def save_transcript(self, record_id, segments, original=False):
        with self.lock:
            return self._save_transcript(record_id, segments, original)

    def _save_transcript(self, record_id, segments, original=False):
        previous = self.transcript(record_id)
        value = {'revision': previous['revision'] + 1, 'segments': segments}
        folder = self.folder(record_id)
        if previous['revision'] > 0:
            atomic_json(folder / f'transcript-v{previous["revision"]}.json', previous)
        if original:
            atomic_json(folder / ('recognition-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json'), value)
        atomic_json(folder / 'transcript.json', value)
        return value

    def messages(self, record_id):
        return read_json(self.folder(record_id) / 'messages.json', [])

    def add_message(self, record_id, message):
        with self.lock:
            messages = self.messages(record_id)
            messages.append(message)
            atomic_json(self.folder(record_id) / 'messages.json', messages)

    def settings(self):
        return read_json(self.root / 'settings.json', {})

    def set_settings(self, **values):
        with self.lock:
            config = self.settings()
            config.update(values)
            atomic_json(self.root / 'settings.json', config)

    def recover(self):
        with self.lock:
            rows = [dict(row) for row in self.db.execute(
                "SELECT * FROM records WHERE status IN ('recording','transcribing','analyzing')")]
        for row in rows:
            folder = self.folder(row['id'])
            meta = read_json(folder / 'audio.json', {})
            if row['status'] == 'recording':
                durations = []
                for track in meta.get('tracks', []):
                    info = finalize_pcm(folder, track)
                    durations.append(info.get('duration', 0) + track.get('offset', 0))
                self.update(row['id'], status='saved', duration=max(durations, default=0),
                            error='上次录音异常中断；已恢复落盘音频，请检查完整性。')
            else:
                self.update(row['id'], status='ready' if self.transcript(row['id'])['segments'] else 'saved',
                            error='上次处理被中断，可重新生成。')
        # Live captions are checkpointed independently of the UI and audio writer.
        # Import an interrupted draft only when there is no saved transcript;
        # never overwrite manual corrections or old citation versions.
        with self.lock:
            candidates = [dict(row) for row in self.db.execute('SELECT * FROM records')]
        for row in candidates:
            if self.transcript(row['id'])['revision'] > 0:
                continue
            draft = read_json(self.folder(row['id']) / 'live-transcript.json', {})
            if draft.get('segments'):
                self.save_transcript(row['id'], draft['segments'], original=True)
                self.update(row['id'], status='ready', error='' if draft.get('complete') else
                            '已恢复先前确认的实时字幕；结尾可能不完整，可回听或重新转录。')


def finalize_pcm(folder: Path, track):
    raw = folder / (track['key'] + '.pcm')
    target = folder / (track['key'] + '.wav')
    if not raw.exists():
        return track
    block = track['channels'] * 2
    size = raw.stat().st_size // block * block
    # Write a fresh header to a temporary file. The raw capture remains recoverable.
    tmp = target.with_suffix('.wav.tmp')
    with raw.open('rb') as source, wave.open(str(tmp), 'wb') as out:
        out.setnchannels(track['channels'])
        out.setsampwidth(2)
        out.setframerate(track['rate'])
        remaining = size
        while remaining:
            data = source.read(min(remaining, 1024 * 1024))
            if not data:
                break
            out.writeframesraw(data)
            remaining -= len(data)
    os.replace(tmp, target)
    track['duration'] = size / block / track['rate']
    return track


def stamp(seconds):
    value = max(0, int(seconds))
    h, rest = divmod(value, 3600)
    m, s = divmod(rest, 60)
    return f'{h:02}:{m:02}:{s:02}' if h else f'{m:02}:{s:02}'


def transcript_text(value):
    return '\n'.join(f'[S{i + 1:04d} {stamp(s["start"])}–{stamp(s["end"])}] {s["speaker"]}'
                     + ('（说话人归属待确认）' if s.get('speaker_uncertain') else '')
                     + ('（含声音或时间边界辅助归属，未经人工确认）' if s.get('speaker_inferred') else '')
                     + ('（疑似电脑声音串入麦克风，需核对归属）' if s.get('echo_suspected') else '')
                     + f'：{simplified(s["text"])}'
                     for i, s in enumerate(value['segments']))

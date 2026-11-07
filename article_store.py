"""Durable local article storage; SQLite transactions guard remote writes."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from cover_assets import validate_id, validate_png


class Conflict(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY, data TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, article_id):
        with self.connect() as db:
            row = db.execute('SELECT data FROM articles WHERE id=?', (article_id,)).fetchone()
        if row is None:
            raise KeyError(article_id)
        return json.loads(row[0])

    def catalog_view(self):
        return Store(Path(self.path).with_name('catalog.sqlite3'))

    def oauth_view(self):
        return Store(Path(self.path).with_name('oauth.sqlite3'))

    def create(self, record):
        try:
            with self.connect() as db:
                db.execute('INSERT INTO articles VALUES (?,?)',(record['id'],json.dumps(record,ensure_ascii=False)))
        except sqlite3.IntegrityError:
            raise Conflict('记录已存在，请重新加载。') from None
        return record

    def list(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM articles ORDER BY rowid DESC')]

    def put_cover(self, cover_id, raw, purpose='cover'):
        validate_id(cover_id)
        if validate_png(raw, purpose) != cover_id:
            raise ValueError('Cover hash mismatch.')
        folder = Path(self.path).parent / ('images' if purpose == 'body' else 'covers')
        folder.mkdir(exist_ok=True)
        target = folder / (cover_id + '.png')
        try:
            with target.open('xb') as output:
                output.write(raw)
        except FileExistsError:
            if self.get_cover(cover_id, purpose) != raw:
                raise ValueError('Stored cover mismatch.')

    def get_cover(self, cover_id, purpose='cover'):
        validate_id(cover_id)
        raw = (Path(self.path).parent / ('images' if purpose == 'body' else 'covers') / (cover_id + '.png')).read_bytes()
        if validate_png(raw, purpose) != cover_id:
            raise ValueError('Stored cover integrity error.')
        return raw

    def put_image(self, image_id, raw):
        return self.put_cover(image_id, raw, 'body')

    def get_image(self, image_id):
        return self.get_cover(image_id, 'body')

    def change(self, article_id, mutate):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM articles WHERE id=?', (article_id,)).fetchone()
            if row is None:
                raise KeyError(article_id)
            article = json.loads(row[0])
            mutate(article)
            article['updated_at'] = datetime.now(timezone.utc).isoformat()
            db.execute('UPDATE articles SET data=? WHERE id=?', (json.dumps(article, ensure_ascii=False), article_id))
        return article

    def save(self, data, rendered):
        article_id = data.get('id')
        if article_id:
            def mutate(article):
                if article['status'] != 'saved':
                    raise Conflict('已开始同步的文章不可编辑；请新建文章。')
                if data.get('revision') != article['revision']:
                    raise Conflict('文章已被其他页面修改，请重新加载。')
                article.update(title=rendered['title'], markdown=data['markdown'],
                               html=rendered['html'], content_hash=rendered['content_hash'],
                               cover_id=rendered.get('cover_id', ''), render_version=rendered.get('render_version', 1),
                               image_ids=rendered.get('image_ids', []),
                               revision=article['revision'] + 1)
            return self.change(article_id, mutate)
        article_id = uuid.uuid4().hex
        article = dict(rendered, id=article_id, markdown=data['markdown'],
                       status='saved', revision=1,
                       updated_at=datetime.now(timezone.utc).isoformat())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute('SELECT data FROM articles'):
                existing = json.loads(row[0])
                if existing['content_hash'] == article['content_hash']:
                    return existing
            db.execute('INSERT INTO articles VALUES (?,?)', (article_id, json.dumps(article, ensure_ascii=False)))
        return article

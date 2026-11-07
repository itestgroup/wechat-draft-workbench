"""Local Markdown preview, standard library only. Optional explicitly triggered draft-only synchronization."""
import argparse
import hashlib
import html
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from article_store import Store, Conflict
from draft_service import Drafts
from wechat_client import WeChat
from wechat_draft_probe import hidden_input, ProbeError
from markdown_render import inline, render_markdown, VERSION
from cover_assets import decode_upload, description, validate_id, MAX_COVER_REQUEST
from published_index import PublishedIndex, IndexValidation

ROOT = Path(__file__).resolve().parent
MAX_BODY = 256 * 1024


def preview(data):
    title = data.get('title', '')
    source = data.get('markdown', '')
    if not isinstance(title, str) or not isinstance(source, str):
        raise ValueError()
    if not title.strip() or len(title) > 64 or not source.strip():
        raise ValueError()
    cover_id = data.get('cover_id') or ''
    if cover_id:
        validate_id(cover_id)
    content = render_markdown(source)
    image_ids = list(dict.fromkeys(re.findall(r'data-image-id="([0-9a-f]{64})"', content)))
    if len(image_ids) > 5:
        raise ValueError('At most five body images per article.')
    digest = json.dumps([VERSION, title.strip(), source, cover_id], ensure_ascii=False)
    return {'title': title.strip(), 'html': content,
            'content_hash': hashlib.sha256(digest.encode()).hexdigest(),
            'cover_id': cover_id, 'render_version': VERSION,
            'image_ids': image_ids,
            'status': 'preview_only'}


class Handler(BaseHTTPRequestHandler):
    def valid_host(self):
        return self.headers.get('Host') in {
            '127.0.0.1:%d' % self.server.server_port,
            'localhost:%d' % self.server.server_port}

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # Never log request bodies or headers.

    def reply(self, code, body, content_type):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; connect-src 'self' https://*.tencentscf.com; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def json_reply(self, result):
        self.reply(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, b'Forbidden host', 'text/plain')
        if self.path == '/api/index':
            return self.json_reply({'entries':PublishedIndex(self.server.store).list()})
        if self.path.startswith('/api/images/'):
            try:
                image_id = self.path.rsplit('/',1)[-1]
                validate_id(image_id)
                return self.json_reply(description(image_id,self.server.store.get_image(image_id),'body'))
            except (ValueError,KeyError,FileNotFoundError):
                return self.reply(404,b'Image not found','text/plain')
        if self.path.startswith('/api/covers/'):
            try:
                cover_id = self.path.rsplit('/', 1)[-1]
                validate_id(cover_id)
                return self.json_reply(description(cover_id, self.server.store.get_cover(cover_id)))
            except (ValueError, KeyError, FileNotFoundError):
                return self.reply(404, b'Cover not found', 'text/plain')
        if self.path == '/api/articles':
            return self.json_reply({'articles': self.server.store.list(), 'wechat_enabled': self.server.drafts is not None})
        files = {'/': ('preview/index.html', 'text/html; charset=utf-8'),
                 '/api-client.mjs': ('preview/api-client.mjs', 'text/javascript; charset=utf-8'),
                 '/cover.mjs': ('preview/cover.mjs', 'text/javascript; charset=utf-8'),
                 '/history.mjs': ('preview/history.mjs', 'text/javascript; charset=utf-8'),
                 '/oauth-consent.mjs': ('preview/oauth-consent.mjs', 'text/javascript; charset=utf-8'),
                 '/ai-openapi.json': ('preview/ai-openapi.json', 'application/json; charset=utf-8'),
                 '/ai-instructions.md': ('preview/ai-instructions.md', 'text/plain; charset=utf-8'),
                 '/app.js': ('preview/app.js', 'text/javascript; charset=utf-8')}
        if self.path not in files:
            return self.reply(404, b'Not found', 'text/plain')
        path, kind = files[self.path]
        self.reply(200, (ROOT / path).read_bytes(), kind)

    def do_POST(self):
        if not self.valid_host():
            return self.reply(403, b'Forbidden host', 'text/plain')
        # A custom header prevents cross-origin form submissions. No CORS enabled.
        if self.path not in {'/api/preview', '/api/articles/save', '/api/articles/sync', '/api/articles/reconcile', '/api/covers/upload', '/api/images/upload',
                             '/api/index/save','/api/index/history','/api/index/importpreview','/api/index/importone'}:
            return self.reply(404, b'Not found', 'text/plain')
        if self.headers.get('X-Preview-Request') != '1':
            return self.reply(403, b'Forbidden', 'text/plain')
        try:
            size = int(self.headers.get('Content-Length', '0'))
            limit = MAX_COVER_REQUEST if self.path in {'/api/covers/upload','/api/images/upload'} else MAX_BODY
            if not 0 < size <= limit:
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError()
            if self.path == '/api/index/importpreview':
                result = PublishedIndex(self.server.store).import_preview(data)
            elif self.path == '/api/index/importone':
                result = PublishedIndex(self.server.store).import_one(data)
            elif self.path == '/api/index/save':
                result = PublishedIndex(self.server.store).save(data)
            elif self.path == '/api/index/history':
                result = PublishedIndex(self.server.store).history(data)
            elif self.path == '/api/images/upload':
                image_id,raw = decode_upload(data,'body')
                self.server.store.put_image(image_id,raw)
                result = {'image_id':image_id}
            elif self.path == '/api/covers/upload':
                cover_id, raw = decode_upload(data)
                self.server.store.put_cover(cover_id, raw)
                result = {'cover_id': cover_id, 'width': 900, 'height': 500}
            elif self.path == '/api/preview':
                result = preview(data)
            elif self.path == '/api/articles/save':
                rendered = preview(data)
                if rendered['cover_id']:
                    self.server.store.get_cover(rendered['cover_id'])
                for image_id in rendered['image_ids']:
                    self.server.store.get_image(image_id)
                result = self.server.store.save(data, rendered)
            else:
                if self.server.drafts is None:
                    raise Conflict('微信未启用。请在终端使用 --wechat 启动并隐藏输入凭据。')
                article_id = data.get('id')
                if not isinstance(article_id, str) or not re.fullmatch(r'[0-9a-f]{32}', article_id):
                    raise ValueError()
                if self.path.endswith('/sync'):
                    if data.get('confirm') is not True:
                        raise Conflict('请明确确认上传封面并创建草稿。')
                    result = self.server.drafts.sync(article_id, data.get('content_hash'))
                else:
                    result = self.server.drafts.reconcile(article_id)
        except Conflict as exc:
            return self.reply(409, str(exc).encode(), 'text/plain; charset=utf-8')
        except KeyError:
            return self.reply(404, '文章不存在。'.encode(), 'text/plain; charset=utf-8')
        except ProbeError as exc:
            return self.reply(502, str(exc).encode(), 'text/plain; charset=utf-8')
        except IndexValidation as exc:
            return self.reply(400,json.dumps({'error':str(exc)},ensure_ascii=False).encode(),'application/json; charset=utf-8')
        except (ValueError, UnicodeError):
            return self.reply(400, '请填写标题（不超过64字）和正文；请求不得超过256KB。'.encode(), 'text/plain; charset=utf-8')
        except Exception:
            return self.reply(500, '操作失败。若已触发同步，请只读核对结果，不要重复写入。'.encode(), 'text/plain; charset=utf-8')
        self.json_reply(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18765)
    parser.add_argument('--wechat', action='store_true', help='Enable draft sync; credentials entered in terminal only')
    args = parser.parse_args()
    store = Store(ROOT / '.local' / 'articles.sqlite3')
    drafts = None
    if args.wechat:
        try:
            appid = hidden_input('AppID (hidden, not saved): ')
            secret = hidden_input('AppSecret (hidden, not saved): ')
            if not appid or not secret:
                raise SystemExit('Credentials required.')
            client = WeChat(appid, secret)
            client.call('draft/count')  # Permission check before enabling writes.
            with store.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
                fingerprint = hashlib.sha256(appid.encode()).hexdigest()
                row = db.execute("SELECT value FROM settings WHERE key='account'").fetchone()
                if row and row[0] != fingerprint:
                    raise SystemExit('Account mismatch; local article store is bound to another account.')
                db.execute("INSERT OR IGNORE INTO settings VALUES ('account', ?)", (fingerprint,))
            drafts = Drafts(store, client)
            del appid, secret
        except (ProbeError, KeyboardInterrupt, EOFError) as exc:
            print("微信连接未启用：" + (str(exc) or "已取消"), flush=True)
            print("本地保存和预览仍可使用；修正连接后请重启并使用 --wechat。", flush=True)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.store, server.drafts = store, drafts
    print('Local preview: http://127.0.0.1:%d (Ctrl+C to stop)' % args.port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

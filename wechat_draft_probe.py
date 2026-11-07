"""Python 3.7+; standard library only. Never publishes or sends messages."""
import argparse
import getpass
import hashlib
import json
import struct
import sys
import uuid
import zlib
import warnings
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'https://api.weixin.qq.com/cgi-bin/'
RECORD = []

def hidden_input(prompt):
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt).strip()
        except getpass.GetPassWarning:
            raise ProbeError('Hidden input unavailable; use a local interactive terminal.')

def save_state(path, state):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)



class ProbeError(Exception):
    pass


def request(path, token=None, payload=None, body=None, content_type=None):
    url = BASE + path
    if token:
        url += ('&' if '?' in url else '?') + urlencode({'access_token': token})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        content_type = 'application/json; charset=utf-8'
    req = Request(url, data=body)
    if content_type:
        req.add_header('Content-Type', content_type)
    try:
        with urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        raise ProbeError('HTTP %s; no automatic retry.' % exc.code)
    except (URLError, TimeoutError, OSError, ValueError):
        raise ProbeError('Network/timeout/invalid JSON. No automatic retry. '
                         'For writes, inspect permanent materials or the draft box before rerunning.')
    if not isinstance(data, dict):
        raise ProbeError('Unexpected response shape; no automatic retry.')
    return data


def status(label, data):
    code = data.get('errcode', 0)
    line = '%s: %s' % (label, 'OK' if code == 0 else 'errcode=%s' % code)
    print(line)
    RECORD.append(line)
    hints = {40164: 'Add your current public outbound IP to the WeChat IP whitelist.',
             40013: 'Check AppID.', 40125: 'Check AppSecret.',
             40001: 'Invalid credential/token.', 40014: 'Invalid access token.',
             42001: 'Token expired.', 48001: 'This endpoint is unauthorized.',
             45009: 'API quota exceeded.'}
    if code:
        print(hints.get(code, 'Keep this error code for diagnosis.'))
        # Only show trace IDs, never arbitrary responses which could contain credentials.
        import re
        match = re.search(r'rid:\s*([A-Za-z0-9_-]+)', data.get('errmsg', ''))
        if match:
            print('rid:', match.group(1))
    return code == 0


def cover_png():
    def chunk(kind, value):
        return (struct.pack('!I', len(value)) + kind + value +
                struct.pack('!I', zlib.crc32(kind + value) & 0xffffffff))
    width, height = 900, 500
    pixels = b''.join(b'\x00' + bytes((30, 90, 150)) * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true', help='Only get token and draft count')
    args = parser.parse_args()
    appid = hidden_input('AppID (hidden, not saved): ')
    secret = hidden_input('AppSecret (hidden, not saved): ')
    if not appid or not secret:
        raise ProbeError('AppID and AppSecret are required.')
    data = request('stable_token', payload={
        'grant_type': 'client_credential', 'appid': appid,
        'secret': secret, 'force_refresh': False})
    secret = None
    if not status('stable_token', data):
        return 1
    token = data.get('access_token')
    if not token:
        raise ProbeError('Token missing in response.')
    count = request('draft/count', token)
    if status('draft/count', count):
        print('Draft count:', count.get('total_count'))
    if args.check_only:
        return 0 if count.get('errcode', 0) == 0 else 1

    state_path = Path(__file__).resolve().with_name(
        'wechat_probe_%s.json' % hashlib.sha256(appid.encode()).hexdigest()[:12])
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    if state.get('media_id'):
        print('Existing test draft found locally; only reading it back.')
    else:
        if state.get('add_started'):
            raise ProbeError('Previous draft/add result is uncertain. Inspect the draft box first. '
                             'Do not remove the local state file until checked.')
        if not state.get('thumb_media_id'):
            if state.get('upload_started'):
                raise ProbeError('Previous cover upload result is uncertain. Check permanent image materials in WeChat first. Do not delete or edit the state file before confirmation.')
            boundary = 'probe' + uuid.uuid4().hex
            body = (('--' + boundary + '\r\nContent-Disposition: form-data; '
                     'name="media"; filename="probe.png"\r\nContent-Type: image/png\r\n\r\n').encode()
                    + cover_png() + ('\r\n--' + boundary + '--\r\n').encode())
            # This endpoint needs both access_token and type=image.
            state['upload_started'] = True
            save_state(state_path, state)
            upload = request('material/add_material?type=image', token, body=body,
                             content_type='multipart/form-data; boundary=' + boundary)
            if not status('material/add_material', upload):
                raise ProbeError('Cover upload rejected; stopped. Confirm result before another write.')
            if not upload.get('media_id'):
                raise ProbeError('Cover media_id missing.')
            state['thumb_media_id'] = upload['media_id']
            save_state(state_path, state)
        title = '草稿接入验证 ' + datetime.now().strftime('%Y%m%d-%H%M%S')
        article = {'title': title, 'author': 'LabVIEW伯乐',
                   'digest': '仅用于验证草稿创建和读取，不公开发表。',
                   'content': '<h2>草稿接入验证</h2><p>验证中文、段落与排版。</p>'
                              '<p><strong>此文仅为接口测试，请勿发表。</strong></p>',
                   'thumb_media_id': state['thumb_media_id'],
                   'need_open_comment': 0, 'only_fans_can_comment': 0}
        state.update({'add_started': True, 'title': title})
        save_state(state_path, state)
        result = request('draft/add', token, payload={'articles': [article]})
        if not status('draft/add', result):
            raise ProbeError('Draft creation rejected; stopped. Confirm result before another write.')
        if not result.get('media_id'):
            raise ProbeError('Draft media_id missing; inspect the draft box before rerunning.')
        state['media_id'] = result['media_id']
        save_state(state_path, state)
    result = request('draft/get', token, payload={'media_id': state['media_id']})
    if not status('draft/get', result):
        return 1
    items = result.get('news_item', [])
    if not items or items[0].get('title') != state.get('title'):
        raise ProbeError('Readback title mismatch.')
    print('PASS: test draft created and read back; nothing published or sent.')
    print('Title:', state['title'])
    print('Test draft and cover are retained for inspection; no existing content changed.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ProbeError, KeyboardInterrupt, EOFError) as exc:
        print('STOP:', str(exc) or 'Cancelled')
        RECORD.append('Stopped; check terminal for sanitized diagnostic.')
        sys.exit(1)
    except Exception:
        print('STOP: local/unexpected failure; no automatic retry. Check remote write results before rerunning.')
        RECORD.append('Stopped: unexpected failure; remote write result requires confirmation.')
        sys.exit(1)
    finally:
        record_path = Path(__file__).resolve().with_name('wechat_probe_validation.md')
        try:
            with record_path.open('a', encoding='utf-8') as record:
                record.write('\n## ' + datetime.now().astimezone().isoformat(timespec='seconds') + '\n')
                record.write('Mode: ' + ('check-only' if '--check-only' in sys.argv else 'draft probe') + '\n')
                record.write('\n'.join(RECORD) + '\nNo publish/send/update/delete calls.\n')
        except OSError:
            print('Could not save sanitized validation record.')

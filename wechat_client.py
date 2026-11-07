"""Draft-only WeChat client. Credentials and tokens stay in process memory."""
import threading
import time
import uuid
import re
import ipaddress
from urllib.parse import urlsplit
from wechat_draft_probe import request, cover_png, ProbeError


class WeChat:
    def __init__(self, appid, secret):
        self.appid, self.secret = appid, secret
        self.token, self.expires = None, 0
        self.lock = threading.Lock()

    def call(self, path, **kwargs):
        allowed = {'draft/count', 'draft/add', 'draft/get', 'draft/batchget',
                   'material/add_material?type=image', 'media/uploadimg', 'freepublish/batchget'}
        if path not in allowed:
            raise ProbeError('Endpoint disabled.')
        with self.lock:
            if time.monotonic() >= self.expires:
                data = request('stable_token', payload={'grant_type': 'client_credential',
                               'appid': self.appid, 'secret': self.secret, 'force_refresh': False})
                self.check(data)
                if not data.get('access_token'):
                    raise ProbeError('Token response incomplete.')
                self.token = data['access_token']
                self.expires = time.monotonic() + max(0, int(data.get('expires_in', 7200)) - 120)
            data = request(path, self.token, **kwargs)
        self.check(data)
        return data

    @staticmethod
    def check(data):
        if data.get('errcode', 0):
            if data['errcode'] == 40164:
                # Extract only a validated IP, never echo arbitrary server responses.
                match = re.search(r'invalid ip\s+([^\s,;]+)', str(data.get('errmsg', '')), re.I)
                address = ''
                if match:
                    try:
                        address = ' 微信看到的出口 IP：' + str(ipaddress.ip_address(match.group(1)))
                    except ValueError:
                        pass
                raise ProbeError('WeChat errcode=40164；当前出口 IP 未加入公众号白名单。' + address)
            raise ProbeError('WeChat errcode=%s; no automatic write retry.' % data['errcode'])

    def upload_cover(self, image=None):
        if image is not None:
            from cover_assets import validate_png
            validate_png(image)
        boundary = 'cover' + uuid.uuid4().hex
        body = (('--' + boundary + '\r\nContent-Disposition: form-data; name="media"; '
                 'filename="platform-cover.png"\r\nContent-Type: image/png\r\n\r\n').encode()
                + (image if image is not None else cover_png()) + ('\r\n--' + boundary + '--\r\n').encode())
        result = self.call('material/add_material?type=image', body=body,
                           content_type='multipart/form-data; boundary=' + boundary)
        if not result.get('media_id'):
            raise ProbeError('Cover response incomplete; inspect remote materials.')
        return result['media_id']

    def upload_body_image(self, image):
        from cover_assets import validate_png
        validate_png(image, 'body')
        boundary = 'body' + uuid.uuid4().hex
        body = (('--' + boundary + '\r\nContent-Disposition: form-data; name="media"; '
                 'filename="article-image.png"\r\nContent-Type: image/png\r\n\r\n').encode()
                + image + ('\r\n--' + boundary + '--\r\n').encode())
        result = self.call('media/uploadimg', body=body,
                           content_type='multipart/form-data; boundary=' + boundary)
        url = result.get('url', '')
        target = urlsplit(url)
        if target.scheme not in ('http', 'https') or not target.hostname or not target.hostname.endswith('.qpic.cn') or target.username or target.password:
            raise ProbeError('Body image URL missing or unexpected; result requires confirmation.')
        return url

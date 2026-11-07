"""Authenticated SCF function-URL adapter. Never logs HTTP bodies or headers."""
import base64
import hmac
import json
import os
import re

from article_store import Conflict
from cos_store import StorageError, from_environment
from draft_service import Drafts
from platform_preview import preview, MAX_BODY
from wechat_client import WeChat
from wechat_draft_probe import ProbeError
from cover_assets import decode_upload, description, validate_id as validate_cover_id, MAX_COVER_REQUEST
from published_index import PublishedIndex, IndexValidation


def handle(event, store_factory=from_environment, client_factory=WeChat):
    incoming = event.get('headers') or {}
    if not isinstance(incoming, dict):
        return {'statusCode': 400, 'headers': {},
                'isBase64Encoded': False, 'body': '{"error":"Invalid headers."}'}
    headers = {str(k).lower(): str(v) for k, v in incoming.items()}
    origin = headers.get('origin', '')
    method, path = event.get('httpMethod', ''), event.get('path', '')
    # Only the public MCP transport accepts the exact ChatGPT browser origin.
    # Management routes continue to accept only the owner's workbench origin.
    mcp_origin = path=='/mcp' and origin=='https://chatgpt.com'
    allowed = os.environ.get('PLATFORM_ALLOWED_ORIGIN', '').rstrip('/')
    cors = {'Access-Control-Allow-Origin': allowed, 'Vary': 'Origin'} if origin and origin == allowed else {}
    if mcp_origin:
        cors = {'Access-Control-Allow-Origin':origin,'Vary':'Origin'}

    def response(code, data):
        return {'statusCode': code, 'isBase64Encoded': False,
                # SCF function URLs already emit application/json. An additional
                # Content-Type becomes a second wire header, rejected by MCP SDKs.
                'headers': dict({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}, **cors),
                'body': json.dumps(data, ensure_ascii=False)}

    allowed_scheme = allowed.startswith('https://') or re.fullmatch(r'http://(?:127\.0\.0\.1|localhost):\d{1,5}', allowed)
    if origin and not mcp_origin and (not allowed_scheme or origin != allowed):
        return response(403, {'error': 'Origin not allowed.'})
    if method == 'OPTIONS':
        if not origin or (origin != allowed and not mcp_origin):
            return response(403, {'error': 'Origin not allowed.'})
        result = response(204, {})
        result['headers'].update({'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
                                  'Access-Control-Allow-Headers': 'Authorization,Content-Type,X-Preview-Request,Accept,MCP-Protocol-Version,MCP-Session-Id'})
        result['body'] = ''
        return result
    from mcp_server import PUBLIC_PATHS, dispatch
    if path in PUBLIC_PATHS:
        return dispatch(event,headers,store_factory,response,client_factory)
    key = os.environ.get('PLATFORM_API_KEY', '')
    if len(key) < 32:
        return response(503, {'error': 'Configure PLATFORM_API_KEY (at least 32 characters) in SCF.'})
    ai_paths = {'/api/ai/submit','/api/ai/status','/api/ai/index'}
    ai_key = os.environ.get('AI_SUBMIT_KEY','')
    authorization = headers.get('authorization','').encode()
    ai_authorized = (len(ai_key)>=32 and ai_key!=key
                     and hmac.compare_digest(authorization,('Bearer '+ai_key).encode()))
    if path in ai_paths:
        if len(ai_key)<32 or ai_key==key:
            return response(503, {'error':'Configure a distinct AI_SUBMIT_KEY (at least 32 characters) in SCF.'})
        if not ai_authorized:
            return response(401, {'error':'Unauthorized.'})
    elif ai_authorized:
        return response(403, {'error':'AI credential is limited to AI submission endpoints.'})
    elif not hmac.compare_digest(authorization, ('Bearer ' + key).encode()):
        return response(401, {'error': 'Unauthorized.'})
    cover_read = bool(re.fullmatch(r'/api/covers/[0-9a-f]{64}', path))
    image_read = bool(re.fullmatch(r'/api/images/[0-9a-f]{64}', path))
    if not cover_read and not image_read and path not in ai_paths | {'/api/health', '/api/articles', '/api/preview', '/api/articles/save',
                    '/api/articles/sync', '/api/articles/reconcile', '/api/covers/upload', '/api/images/upload',
                    '/api/index', '/api/index/save', '/api/index/history', '/api/index/importpreview', '/api/index/importone',
                    '/api/oauth/inspect','/api/oauth/approve'}:
        return response(404, {'error': 'Not found.'})
    expected_method = 'GET' if cover_read or image_read or path in {'/api/health', '/api/articles','/api/index','/api/ai/index'} else 'POST'
    if method != expected_method:
        return response(405, {'error': 'Method not allowed.'})
    try:
        if path == '/api/ai/index':
            # Bound response for Actions; excludes draft contents and private storage metadata.
            entries = PublishedIndex(store_factory()).list()
            from mcp_server import query
            offset = int(query(event).get('offset',0))
            if not 0 <= offset <= 500:
                raise ValueError()
            page = [{key:item[key] for key in ('title','url','published_date','collection','tags')}
                    for item in entries[offset:offset+20]]
            return response(200,{'entries':page,'next_offset':offset+20 if offset+20<len(entries) else None})
        if path == '/api/index':
            return response(200,{'entries':PublishedIndex(store_factory()).list()})
        if cover_read:
            cover_id = path.rsplit('/', 1)[-1]
            return response(200, description(cover_id, store_factory().get_cover(cover_id)))
        if image_read:
            image_id = path.rsplit('/',1)[-1]
            return response(200, description(image_id, store_factory().get_image(image_id), 'body'))
        if method == 'GET' and path == '/api/health':
            store_factory()  # Verify role permissions and safe versioning configuration.
            return response(200, {'status': 'ok', 'storage': 'configured',
                                  'wechat_enabled': os.environ.get('ENABLE_DRAFT_SYNC') == 'true'})
        if method == 'GET' and path == '/api/articles':
            return response(200, {'articles': store_factory().list(),
                                  'wechat_enabled': os.environ.get('ENABLE_DRAFT_SYNC') == 'true'})
        if method != 'POST':
            return response(405, {'error': 'Method not allowed.'})
        raw = event.get('body') or ''
        limit = MAX_COVER_REQUEST if path in {'/api/covers/upload','/api/images/upload'} else MAX_BODY
        if not isinstance(raw, str) or len(raw.encode()) > limit * 2:
            raise ValueError()
        body = base64.b64decode(raw, validate=True) if event.get('isBase64Encoded') else raw.encode()
        if not 0 < len(body) <= limit:
            raise ValueError()
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError()
        if path == '/api/preview':
            return response(200, preview(data))
        if path in {'/api/oauth/inspect','/api/oauth/approve'}:
            from mcp_oauth import inspect_request, approval, OAuthError
            try:
                return response(200,inspect_request(data) if path.endswith('/inspect') else
                                approval(store_factory() if data.get('approve') is True else None,data))
            except OAuthError as error:
                return response(error.status,{'error':error.code})
        store = store_factory()
        if path in {'/api/ai/submit','/api/ai/status'}:
            from ai_submission import submit, status
            return response(200,submit(store,data) if path.endswith('/submit') else status(store,data))
        if path == '/api/index/importpreview':
            return response(200,PublishedIndex(store).import_preview(data))
        if path == '/api/index/importone':
            return response(200,PublishedIndex(store).import_one(data))
        if path == '/api/index/save':
            return response(200,PublishedIndex(store).save(data))
        if path == '/api/index/history':
            return response(200,PublishedIndex(store).history(data))
        if path == '/api/covers/upload':
            cover_id, raw = decode_upload(data)
            store.put_cover(cover_id, raw)
            return response(200, {'cover_id':cover_id, 'width':900, 'height':500})
        if path == '/api/images/upload':
            image_id, raw = decode_upload(data, 'body')
            store.put_image(image_id, raw)
            return response(200, {'image_id':image_id})
        if path == '/api/articles/save':
            rendered = preview(data)
            if rendered['cover_id']:
                store.get_cover(rendered['cover_id'])
            for image_id in rendered['image_ids']:
                store.get_image(image_id)
            return response(200, store.save(data, rendered))
        if os.environ.get('ENABLE_DRAFT_SYNC') != 'true':
            raise Conflict('云端草稿写入尚未启用；请先完成存储及鉴权验收。')
        client = client_factory(os.environ['WECHAT_APP_ID'], os.environ['WECHAT_APP_SECRET'])
        drafts = Drafts(store, client)
        article_id = data.get('id')
        if path.endswith('/sync'):
            if data.get('confirm') is not True:
                raise Conflict('Explicit confirmation required.')
            result = drafts.sync(article_id, data.get('content_hash'))
        else:
            result = drafts.reconcile(article_id)
        return response(200, result)
    except KeyError:
        return response(404, {'error': 'Article or required configuration not found.'})
    except Conflict as exc:
        return response(409, {'error': str(exc)})
    except StorageError as exc:
        return response(503, {'error': str(exc)})
    except ProbeError as exc:
        return response(502, {'error': str(exc)})
    except IndexValidation as exc:
        return response(400,{'error':str(exc)})
    except (ValueError, UnicodeError):
        return response(400, {'error': 'Invalid input; title and Markdown required, request <=256KB.'})
    except Exception:
        return response(503, {'error': 'Cloud operation failed; inspect configuration and reload state before retrying writes.'})

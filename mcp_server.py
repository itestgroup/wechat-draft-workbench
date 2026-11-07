"""Stateless Streamable HTTP MCP and OAuth HTTP routes, standard library only."""
import base64
import json
import re
import ipaddress
from urllib.parse import parse_qsl

from article_store import Conflict
from cos_store import StorageError
from published_index import PublishedIndex
from ai_submission import submit, status
from mobile_drafts import create as create_wechat_draft, inspect as inspect_wechat_draft
from wechat_client import WeChat
from wechat_draft_probe import ProbeError
import mcp_oauth as oauth

PUBLIC_PATHS = {'/mcp','/.well-known/oauth-protected-resource','/.well-known/oauth-protected-resource/mcp',
                '/.well-known/oauth-authorization-server','/oauth/authorize','/oauth/token'}
PROTOCOLS = {'2024-11-05','2025-03-26','2025-06-18','2025-11-25'}


def tool(name, description, properties, required, scope, readonly):
    security = [{'type':'oauth2','scopes':[scope]}]
    return {'name':name,'description':description,
            'inputSchema':{'type':'object','properties':properties,'required':required,'additionalProperties':False},
            'securitySchemes':security,'_meta':{'securitySchemes':security},
            'annotations':{'readOnlyHint':readonly,'destructiveHint':False,'idempotentHint':True,'openWorldHint':False}}


ID = {'type':'string','minLength':16,'maxLength':80,'pattern':'^[A-Za-z0-9_-]{16,80}$'}
TOOLS = [
    tool('submitArticleForReview','仅新增平台待审稿，不同步微信。超时先查原编号，禁止自动重投。',
         {'submission_id':ID,'title':{'type':'string','minLength':1,'maxLength':64},
          'markdown':{'type':'string','minLength':1,'maxLength':30000}},
         ['submission_id','title','markdown'],'labview:submit',False),
    tool('getSubmissionStatus','只读核对原投稿编号；not_found也不自动重投。',
         {'submission_id':ID},['submission_id'],'labview:status',True),
    tool('listPublishedArticleIndex','分页读取已登记公开文章索引；不读取微信或平台草稿。',
         {'offset':{'type':'integer','minimum':0,'maximum':500,'default':0}},[],'labview:index',True),
    tool('getWeChatDraftStatus','读取本人原投稿全文、封面说明及版本摘要；只读核对微信草稿结果，不上传、不重建。',
         {'submission_id':ID},['submission_id'],'labview:status',True),
    tool('createWeChatDraft','用户明确确认后，将原投稿当前版本新建到微信草稿箱；有默认封面须先说明。仅新建、不覆盖、不发表、不群发；超时先查询原编号。',
         {'submission_id':ID,'review_hash':{'type':'string','pattern':'^[0-9a-f]{64}$'},
          'confirm':{'type':'boolean','const':True}},
         ['submission_id','review_hash','confirm'],'labview:wechat-draft',False)
]


def raw_body(event, limit):
    raw = event.get('body') or ''
    if not isinstance(raw,str) or len(raw.encode())>limit*2:
        raise ValueError()
    raw = base64.b64decode(raw,validate=True) if event.get('isBase64Encoded') else raw.encode()
    if not 0<len(raw)<=limit:
        raise ValueError()
    return raw


def pairs(raw):
    values = parse_qsl(raw,keep_blank_values=True,max_num_fields=20)
    if len(values)!=len(dict(values)):
        raise ValueError()
    return dict(values)


def query(event):
    if event.get('rawQueryString'):
        return pairs(event['rawQueryString'])
    result = event.get('queryString') if 'queryString' in event else event.get('queryStringParameters',{})
    result = result or {}
    if not isinstance(result,dict) or len(result)>20 or any(not isinstance(v,str) or len(v)>16000 for v in result.values()):
        raise ValueError()
    return result


def rpc_error(response, request_id, code, message, http_status=200):
    return response(http_status,{'jsonrpc':'2.0','id':request_id,'error':{'code':code,'message':message}})


def challenge(response, request_id, error, scope):
    authenticate = ('Bearer resource_metadata="'+oauth.base()+
        '/.well-known/oauth-protected-resource/mcp", error="'+error.code+'", '
        'error_description="Reconnect to grant the required tool scope", scope="'+scope+'"')
    result = response(error.status,{'jsonrpc':'2.0','id':request_id,'result':{
        'content':[{'type':'text','text':'Authorization required for this tool.'}],
        'isError':True,'_meta':{'mcp/www_authenticate':[authenticate]}}})
    result['headers']['WWW-Authenticate'] = authenticate
    return result


def dispatch(event, headers, store_factory, response, client_factory=WeChat):
    path, method = event['path'],event['httpMethod']
    try:
        if path in {'/.well-known/oauth-protected-resource','/.well-known/oauth-protected-resource/mcp',
                    '/.well-known/oauth-authorization-server','/oauth/authorize'}:
            if method!='GET':
                return response(405,{'error':'Method not allowed.'})
            if path.startswith('/.well-known/oauth-protected-resource'):
                return response(200,oauth.protected_metadata())
            if path=='/.well-known/oauth-authorization-server':
                return response(200,oauth.server_metadata())
            redirect = oauth.authorize(query(event))
            result = response(302,{})
            result['headers']['Location']=redirect
            result['headers']['Referrer-Policy']='no-referrer'
            result['body']=''
            return result
        if path=='/oauth/token':
            if method!='POST':
                return response(405,{'error':'Method not allowed.'})
            if headers.get('content-type','').split(';')[0].strip()!='application/x-www-form-urlencoded':
                return response(415,{'error':'invalid_request'})
            parameters = pairs(raw_body(event,20000).decode('utf-8'))
            # Reject malformed tokens and grant type before any storage/network initialization.
            if parameters.get('grant_type')!='authorization_code':
                return response(400,{'error':'unsupported_grant_type'})
            try:
                oauth.verify(parameters.get('code'),'code')
            except oauth.OAuthError as error:
                if error.status==401:
                    raise oauth.OAuthError('invalid_grant') from None
                raise
            result = response(200,oauth.exchange(store_factory(),parameters))
            result['headers']['Pragma']='no-cache'
            return result
        if method!='POST':
            result = response(405,{'error':'This stateless MCP endpoint accepts POST; SSE listening is not enabled.'})
            result['headers']['Allow']='POST'
            return result
        if headers.get('mcp-protocol-version') not in PROTOCOLS | {None}:
            return response(400,{'error':'Unsupported MCP protocol version.'})
        # This server always returns JSON. Discovery clients need not request SSE.
        accept = headers.get('accept','*/*').lower()
        if not any(value.split(';')[0].strip() in {'application/json','application/*','*/*'}
                   and not any(part.strip() in {'q=0','q=0.0','q=0.00','q=0.000'} for part in value.split(';')[1:])
                   for value in accept.split(',')):
            return response(406,{'error':'Accept must allow application/json.'})
        if headers.get('content-type','').split(';')[0].strip()!='application/json':
            return response(415,{'error':'JSON required.'})
        try:
            request = json.loads(raw_body(event,256*1024))
        except (ValueError,UnicodeError):
            return rpc_error(response,None,-32700,'Parse error',400)
        if (not isinstance(request,dict) or request.get('jsonrpc')!='2.0'
                or not isinstance(request.get('method'),str)):
            return rpc_error(response,None,-32600,'Invalid request',400)
        request_id = request.get('id')
        if 'id' not in request:
            if request['method'] not in {'notifications/initialized','notifications/cancelled'}:
                return rpc_error(response,None,-32600,'Unsupported notification',400)
            result = response(202,{})
            result['body']=''
            return result
        if type(request_id) not in (str,int):
            return rpc_error(response,None,-32600,'Invalid request id',400)
        params = request.get('params',{})
        if not isinstance(params,dict):
            return rpc_error(response,request_id,-32602,'Invalid params')
        def result(value):
            return response(200,{'jsonrpc':'2.0','id':request_id,'result':value})
        if request['method']=='initialize':
            version = params.get('protocolVersion')
            if not isinstance(version,str):
                return rpc_error(response,request_id,-32602,'protocolVersion is required')
            return result({'protocolVersion':version if version in PROTOCOLS else '2025-11-25',
                           'capabilities':{'tools':{}},'serverInfo':{'name':'labview-jobs','version':'1.0.0'}})
        if request['method']=='ping':
            return result({})
        if request['method']=='tools/list':
            return result({'tools':TOOLS})
        if request['method']!='tools/call':
            return rpc_error(response,request_id,-32601,'Method not found')
        definition = next((item for item in TOOLS if item['name']==params.get('name')),None)
        if definition is None:
            return rpc_error(response,request_id,-32602,'Unknown tool')
        try:
            oauth.access(headers.get('authorization',''),definition['securitySchemes'][0]['scopes'][0])
        except oauth.OAuthError as error:
            return challenge(response,request_id,error,definition['securitySchemes'][0]['scopes'][0])
        arguments = params.get('arguments',{})
        shape = definition['inputSchema']
        if (not isinstance(arguments,dict) or set(arguments)-set(shape['properties'])
                or not set(shape['required'])<=set(arguments)):
            return rpc_error(response,request_id,-32602,'Invalid tool arguments')
        try:
            store = store_factory()
            if definition['name']=='submitArticleForReview':
                value = submit(store,arguments)
            elif definition['name']=='getSubmissionStatus':
                value = status(store,arguments)
            elif definition['name']=='getWeChatDraftStatus':
                value = inspect_wechat_draft(store,arguments,client_factory)
            elif definition['name']=='createWeChatDraft':
                value = create_wechat_draft(store,arguments,client_factory)
            else:
                offset = arguments.get('offset',0)
                if type(offset) is not int or not 0<=offset<=500:
                    raise ValueError()
                entries = PublishedIndex(store).list()
                value = {'entries':[{key:item[key] for key in ('title','url','published_date','collection','tags')}
                                    for item in entries[offset:offset+20]],
                         'next_offset':offset+20 if offset+20<len(entries) else None}
            return result({'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],
                           'structuredContent':value,'isError':False})
        except ProbeError as error:
            value = {'http_status':502,'error':'WeChat result requires verification; query original submission ID. No write retry.'}
            match = re.search(r'errcode=(\d{1,8})\b',str(error))
            if match:
                value['errcode'] = int(match.group(1))
            if value.get('errcode')==40164 and '微信看到的出口 IP：' in str(error):
                try:
                    value['outbound_ip'] = str(ipaddress.ip_address(str(error).split('微信看到的出口 IP：',1)[1].strip()))
                except ValueError:
                    pass
            return result({'content':[{'type':'text','text':json.dumps(value)}],'isError':True,'structuredContent':value})
        except (Conflict,StorageError,ValueError,KeyError) as error:
            code = 409 if isinstance(error,Conflict) else 503 if isinstance(error,StorageError) else 400
            value = {'http_status':code,'error':'Conflict or pending result; query original ID and review current version. No write retry.' if code==409 else
                     'Result uncertain; query original ID before any write.' if code==503 else 'Invalid tool input.'}
            return result({'content':[{'type':'text','text':json.dumps(value)}],'isError':True,'structuredContent':value})
    except oauth.OAuthError as error:
        return response(error.status,{'error':error.code})
    except (ValueError,UnicodeError):
        return response(400,{'error':'invalid_request'})
    except Exception:
        return response(503,{'error':'Service unavailable; do not repeat writes.'})

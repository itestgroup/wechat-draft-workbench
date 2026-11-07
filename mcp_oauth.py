"""Single-maintainer OAuth authorization code + PKCE. No secret/body logging."""
import base64
import functools
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from article_store import Conflict

BASE = 'https://your-function.ap-guangzhou.tencentscf.com'
SCOPES = ('labview:submit','labview:status','labview:index','labview:wechat-draft')
PUBLIC_CLIENT = 'labview-private-plugin'
# Exact callback copied from the existing ChatGPT connection by the maintainer.
CHATGPT_CIMD_REDIRECT = 'https://chatgpt.com/connector_platform_oauth_redirect'
SNAPSHOT_PATH = Path(__file__).with_name('chatgpt-oauth-public.json')
PUBLIC_DOCUMENT_URLS = ('https://chatgpt.com/oauth/client.json','https://chatgpt.com/oauth/jwks.json')


def validate_public_snapshot(data):
    now = int(time.time())
    if (not isinstance(data,dict) or set(data)!={'fetched_at','expires_at','documents'}
            or type(data['fetched_at']) is not int or type(data['expires_at']) is not int
            or not now-72*3600<=data['fetched_at']<=now+60
            or not now<data['expires_at']<=data['fetched_at']+72*3600
            or not isinstance(data['documents'],dict)
            or set(data['documents'])!=set(PUBLIC_DOCUMENT_URLS)):
        raise ValueError('Invalid or expired public OAuth snapshot')
    client, jwks = (data['documents'][url] for url in PUBLIC_DOCUMENT_URLS)
    if (client.get('client_id')!=PUBLIC_DOCUMENT_URLS[0]
            or client.get('jwks_uri')!=PUBLIC_DOCUMENT_URLS[1]
            or client.get('redirect_uris')!=[CHATGPT_CIMD_REDIRECT]
            or not isinstance(jwks.get('keys'),list) or not 1<=len(jwks['keys'])<=20):
        raise ValueError('Unexpected official OAuth public documents')
    for key in jwks['keys']:
        if (not isinstance(key,dict) or key.get('kty')!='RSA'
                or not all(isinstance(key.get(k),str) and key[k] for k in ('kid','n','e'))
                or set(key)&{'d','p','q','dp','dq','qi','oth','k'}):
            raise ValueError('Expected public RSA keys only')
    return data


def client_public_json(url):
    # CI fetches the exact official HTTPS documents; no proxy, secrets or user data.
    if url in PUBLIC_DOCUMENT_URLS and SNAPSHOT_PATH.is_file():
        try:
            data = validate_public_snapshot(json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8')))
            return data['documents'][url]
        except Exception:
            # Never accept expired keys; try the original HTTPS origin instead.
            pass
    return public_json(url)


class OAuthError(Exception):
    def __init__(self, code='invalid_request', status=400):
        self.code, self.status = code, status


def base():
    value = os.environ.get('MCP_PUBLIC_BASE_URL',BASE).rstrip('/')
    url = urlsplit(value)
    if (url.scheme!='https' or not url.hostname or not url.hostname.endswith('.tencentscf.com')
            or url.netloc!=url.hostname or url.path or url.query or url.fragment):
        raise OAuthError('server_configuration_required',503)
    return value


def resource():
    return base()+'/mcp'


def credentials():
    # SCF can log return bodies. Enable only after disabling response/body capture.
    if os.environ.get('MCP_OAUTH_ENABLED')!='true':
        raise OAuthError('oauth_not_enabled',503)
    admin, secret = os.environ.get('PLATFORM_API_KEY',''),os.environ.get('AI_SUBMIT_KEY','')
    if len(admin)<32 or len(secret)<32 or admin==secret:
        raise OAuthError('server_configuration_required',503)
    # Purpose-separated signing key; neither original credential is sent to a client.
    key = hmac.new(secret.encode(),b'labview-mcp-oauth-signing-v1',hashlib.sha256).digest()
    owner = hashlib.sha256(('labview-maintainer:'+admin).encode()).hexdigest()
    return key, owner


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def sign(claims):
    header = b64(b'{"alg":"HS256","typ":"JWT"}')
    payload = b64(json.dumps(claims,sort_keys=True,separators=(',',':')).encode())
    data = header+'.'+payload
    return data+'.'+b64(hmac.new(credentials()[0],data.encode(),hashlib.sha256).digest())


def verify(token, purpose):
    try:
        if not isinstance(token,str) or len(token)>16000:
            raise ValueError()
        header, payload, signature = token.split('.')
        data = header+'.'+payload
        expected = b64(hmac.new(credentials()[0],data.encode(),hashlib.sha256).digest())
        if not hmac.compare_digest(signature,expected):
            raise ValueError()
        if json.loads(base64.urlsafe_b64decode(header+'='*(-len(header)%4)))!={'alg':'HS256','typ':'JWT'}:
            raise ValueError()
        claims = json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        now = int(time.time())
        if (claims['purpose']!=purpose or claims['iss']!=base() or claims['aud']!=resource()
                or claims['sub']!=credentials()[1] or type(claims['exp']) is not int
                or type(claims['iat']) is not int or not claims['iat']<=now<claims['exp']
                or not isinstance(claims['scope'],str) or not set(claims['scope'].split())<=set(SCOPES)):
            raise ValueError()
        return claims
    except OAuthError:
        raise
    except (ValueError,TypeError,KeyError,UnicodeError):
        raise OAuthError('invalid_token',401) from None


def allowed_redirects():
    values = os.environ.get('MCP_ALLOWED_REDIRECT_URIS','').split()
    if not values or len(values)>10:
        raise OAuthError('server_configuration_required',503)
    for value in values:
        url = urlsplit(value)
        # Only the exact URIs copied from ChatGPT connection management are registered.
        if (url.scheme!='https' or url.netloc!='chatgpt.com' or not url.path.startswith('/')
                or url.query or url.fragment or len(value)>1000):
            raise OAuthError('server_configuration_required',503)
    return list(dict.fromkeys(values+[CHATGPT_CIMD_REDIRECT]))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def public_json(url):
    request = Request(url,headers={'Accept':'application/json','User-Agent':'LabVIEW-Jobs-MCP/1.0'})
    try:
        with build_opener(NoRedirect()).open(request,timeout=8) as response:
            raw = response.read(65537)
    except HTTPError as error:
        raise OAuthError('client_metadata_http_'+str(error.code),503) from None
    except (URLError, TimeoutError, OSError):
        raise OAuthError('client_metadata_unreachable',503) from None
    if len(raw)>65536:
        raise ValueError()
    data = json.loads(raw)
    if not isinstance(data,dict):
        raise ValueError()
    return data


@functools.lru_cache(maxsize=16)
def client_metadata(client_id, time_bucket):
    # Pin CIMD fetches to the documented ChatGPT origin/paths; no arbitrary URL fetching.
    if not re.fullmatch(r'https://chatgpt\.com/oauth/(?:[A-Za-z0-9_-]{1,128}/)?client\.json',client_id):
        raise OAuthError('invalid_client')
    try:
        data = client_public_json(client_id)
        method = data.get('token_endpoint_auth_method','none')
        advertised = data.get('token_endpoint_auth_methods_supported',[method])
        if not isinstance(advertised,list) or not advertised or any(not isinstance(v,str) for v in advertised):
            raise ValueError()
        methods = set(advertised)&{'none','private_key_jwt'}
        if data.get('client_id')!=client_id or not methods:
            raise ValueError()
        redirects = data['redirect_uris']
        if not isinstance(redirects,list) or not all(isinstance(v,str) for v in redirects):
            raise ValueError()
        jwks_uri = data.get('jwks_uri','')
        if 'private_key_jwt' in methods and not re.fullmatch(r'https://chatgpt\.com/oauth/(?:[A-Za-z0-9_-]{1,128}/)?jwks\.json',jwks_uri):
            raise ValueError()
        return {'redirect_uris':tuple(redirects),'method':method,'methods':tuple(sorted(methods)),'jwks_uri':jwks_uri}
    except OAuthError:
        raise
    except Exception:
        raise OAuthError('invalid_client') from None


def validate_client(client_id, redirect):
    if not isinstance(client_id,str) or not isinstance(redirect,str) or redirect not in allowed_redirects():
        raise OAuthError('invalid_request')
    if client_id!=PUBLIC_CLIENT and redirect not in client_metadata(client_id,int(time.time())//300)['redirect_uris']:
        raise OAuthError('invalid_client')


@functools.lru_cache(maxsize=16)
def client_keys(uri,time_bucket):
    if not re.fullmatch(r'https://chatgpt\.com/oauth/(?:[A-Za-z0-9_-]{1,128}/)?jwks\.json',uri):
        raise OAuthError('invalid_client')
    try:
        keys = client_public_json(uri)['keys']
        if not isinstance(keys,list) or not 1<=len(keys)<=20:
            raise ValueError()
        return keys
    except Exception:
        raise OAuthError('invalid_client') from None


def decode_segment(value):
    return base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True)


def verify_rs256(data, signature, jwk):
    # Official COS runtime already bundles PyCryptodome; checked in final ZIP smoke test.
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import RSA
    from Crypto.Signature import pkcs1_15
    modulus = int.from_bytes(decode_segment(jwk['n']),'big')
    exponent = int.from_bytes(decode_segment(jwk['e']),'big')
    if not 2048<=modulus.bit_length()<=8192 or not 3<=exponent<2**32 or exponent%2==0:
        raise ValueError()
    pkcs1_15.new(RSA.construct((modulus,exponent))).verify(SHA256.new(data),signature)


def client_assertion(parameters):
    client_id = parameters['client_id']
    extra = {'client_assertion_type','client_assertion'}
    metadata = {'method':'none','methods':('none',)} if client_id==PUBLIC_CLIENT else client_metadata(client_id,int(time.time())//300)
    methods = metadata.get('methods',(metadata['method'],))
    if not set(parameters)&extra:
        if 'none' not in methods:
            raise OAuthError('invalid_client',401)
        return None
    if 'private_key_jwt' not in methods:
        raise OAuthError('invalid_client',401)
    try:
        assertion = parameters['client_assertion']
        if (parameters['client_assertion_type']!='urn:ietf:params:oauth:client-assertion-type:jwt-bearer'
                or not isinstance(assertion,str) or len(assertion)>16000):
            raise ValueError()
        header, payload, signature = assertion.split('.')
        descriptor = json.loads(decode_segment(header))
        claims = json.loads(decode_segment(payload))
        if descriptor.get('alg')!='RS256' or not isinstance(descriptor.get('kid'),str):
            raise ValueError()
        metadata = client_metadata(client_id,int(time.time())//300)
        keys = client_keys(metadata['jwks_uri'],int(time.time())//300)
        matches = [key for key in keys if isinstance(key,dict) and key.get('kid')==descriptor['kid']
                   and key.get('kty')=='RSA' and key.get('alg','RS256')=='RS256' and key.get('use','sig')=='sig']
        if len(matches)!=1:
            raise ValueError()
        verify_rs256((header+'.'+payload).encode(),decode_segment(signature),matches[0])
        now = int(time.time())
        audiences = claims.get('aud')
        if isinstance(audiences,str):audiences=[audiences]
        if (claims.get('iss')!=client_id or claims.get('sub')!=client_id
                or not isinstance(audiences,list) or not all(isinstance(v,str) for v in audiences)
                or not set(audiences)&{base(),base()+'/oauth/token'}
                or type(claims.get('exp')) is not int or not now<claims['exp']<=now+600
                or type(claims.get('iat')) is not int or not now-600<=claims['iat']<=now+60
                or not isinstance(claims.get('jti'),str) or not 1<=len(claims['jti'])<=256):
            raise ValueError()
        return hashlib.sha256(('assertion:'+client_id+':'+claims['jti']).encode()).hexdigest()[:32]
    except Exception:
        raise OAuthError('invalid_client',401) from None


def authorize(parameters):
    expected = {'response_type','client_id','redirect_uri','scope','state','code_challenge','code_challenge_method','resource'}
    if (not isinstance(parameters,dict) or not expected<=set(parameters)
            or any(not isinstance(value,str) for value in parameters.values())):
        raise OAuthError()
    p = parameters
    credentials()
    validate_client(p['client_id'],p['redirect_uri'])
    scopes = set(p['scope'].split())
    if (p['response_type']!='code' or p['resource']!=resource() or not scopes
            or not scopes<=set(SCOPES) or not 1<=len(p['state'])<=1024
            or p['code_challenge_method']!='S256' or not re.fullmatch(r'[A-Za-z0-9_-]{43}',p['code_challenge'])):
        raise OAuthError()
    origin = os.environ.get('PLATFORM_ALLOWED_ORIGIN','').rstrip('/')
    if not origin.startswith('https://') or urlsplit(origin).path:
        raise OAuthError('server_configuration_required',503)
    now = int(time.time())
    request = dict({key:p[key] for key in expected},purpose='request',iss=base(),aud=resource(),sub=credentials()[1],
                   scope=' '.join(sorted(scopes)),iat=now,exp=now+600,nonce=uuid.uuid4().hex)
    return origin+'/#oauth_request='+sign(request)


def inspect_request(data):
    claims = verify(data.get('request'),'request')
    validate_client(claims['client_id'],claims['redirect_uri'])
    return {'issuer':base(),'client_id':claims['client_id'],'redirect_uri':claims['redirect_uri'],
            'scope':claims['scope'],'expires_at':claims['exp']}


def approval(store, data):
    claims = verify(data.get('request'),'request')
    validate_client(claims['client_id'],claims['redirect_uri'])
    if data.get('approve') is not True:
        values = {'error':'access_denied','state':claims['state'],'iss':base()}
    else:
        code = sign(dict(claims,purpose='code'))
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        record = {'id':hashlib.sha256(('grant:'+claims['nonce']).encode()).hexdigest()[:32],
                  'revision':1,'code_hash':code_hash,'status':'approved'}
        grants = store.oauth_view()
        try:
            current = grants.get(record['id'])
        except KeyError:
            try:
                grants.create(record)
                current = record
            except Conflict:
                current = grants.get(record['id'])
        if current.get('code_hash')!=code_hash or current.get('status')!='approved':
            raise OAuthError('invalid_grant')
        values = {'code':code,'state':claims['state'],'iss':base()}
    return {'redirect_url':claims['redirect_uri']+'?'+urlencode(values)}


def exchange(store, parameters):
    try:
        required = {'grant_type','code','redirect_uri','client_id','code_verifier','resource'}
        if (not required<=set(parameters) or set(parameters)-required-{'client_assertion_type','client_assertion'}
                or parameters['grant_type']!='authorization_code'):
            raise OAuthError('invalid_grant')
        claims = verify(parameters['code'],'code')
        validate_client(parameters['client_id'],parameters['redirect_uri'])
        verifier = parameters['code_verifier']
        if (claims['client_id']!=parameters['client_id'] or claims['redirect_uri']!=parameters['redirect_uri']
                or parameters['resource']!=resource() or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}',verifier)
                or not hmac.compare_digest(b64(hashlib.sha256(verifier.encode()).digest()),claims['code_challenge'])):
            raise OAuthError('invalid_grant')
        code_hash = hashlib.sha256(parameters['code'].encode()).hexdigest()
        grants = store.oauth_view()
        grant_id = hashlib.sha256(('grant:'+claims['nonce']).encode()).hexdigest()[:32]
        record = grants.get(grant_id)
        if record.get('code_hash')!=code_hash or record.get('status')!='approved':
            raise OAuthError('invalid_grant')
        assertion_id = client_assertion(parameters)
        if assertion_id:
            grants.create({'id':assertion_id,'revision':1,'status':'assertion_consumed'})
        # A separate create-only claim makes authorization codes single use across SCF instances.
        grants.create({'id':hashlib.sha256(('consume:'+code_hash).encode()).hexdigest()[:32],
                       'revision':1,'status':'consumed'})
    except (KeyError,Conflict,TypeError):
        raise OAuthError('invalid_grant') from None
    except OAuthError as error:
        if error.status==401:
            raise OAuthError('invalid_grant') from None
        raise
    now = int(time.time())
    token = sign({'purpose':'access','iss':base(),'aud':resource(),'sub':credentials()[1],
                  'client_id':claims['client_id'],'redirect_uri':claims['redirect_uri'],
                  'scope':claims['scope'],'iat':now,'exp':now+3600,'jti':uuid.uuid4().hex})
    return {'access_token':token,'token_type':'Bearer','expires_in':3600,'scope':claims['scope']}


def access(authorization, scope):
    if not authorization.startswith('Bearer '):
        raise OAuthError('invalid_token',401)
    claims = verify(authorization[7:],'access')
    if claims.get('redirect_uri') not in allowed_redirects():
        raise OAuthError('invalid_token',401)
    if scope not in claims['scope'].split():
        raise OAuthError('insufficient_scope',403)
    return claims


def protected_metadata():
    return {'resource':resource(),'authorization_servers':[base()],
            'scopes_supported':list(SCOPES),'bearer_methods_supported':['header']}


def server_metadata():
    return {'issuer':base(),'authorization_endpoint':base()+'/oauth/authorize',
            'token_endpoint':base()+'/oauth/token','response_types_supported':['code'],
            'grant_types_supported':['authorization_code'],'token_endpoint_auth_methods_supported':['none','private_key_jwt'],
            'token_endpoint_auth_signing_alg_values_supported':['RS256'],
            'code_challenge_methods_supported':['S256'],'scopes_supported':list(SCOPES),
            'authorization_response_iss_parameter_supported':True,'client_id_metadata_document_supported':True}

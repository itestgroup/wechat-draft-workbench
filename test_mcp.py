import base64
import hashlib
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit, urlencode

import mcp_oauth as oauth
from mcp_server import TOOLS
from cloud_api import handle
from cos_store import CosStore, StorageError
from article_store import Store
from test_cloud import FakeCOS


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.redirect='https://chatgpt.com/connector/oauth/test-fixture-only'
        self.env={'PLATFORM_API_KEY':'admin-test-'+'a'*40,'AI_SUBMIT_KEY':'submit-test-'+'b'*40,
                  'PLATFORM_ALLOWED_ORIGIN':'https://your-workbench.pages.dev',
                  'MCP_OAUTH_ENABLED':'true',
                  'MCP_ALLOWED_REDIRECT_URIS':self.redirect}
        self.environment=patch.dict(os.environ,self.env,clear=True)
        self.environment.start();self.addCleanup(self.environment.stop)
        self.cos=FakeCOS();self.store=CosStore(self.cos,'example-123','dummy')
        self.verifier='v'*43
        self.parameters={'response_type':'code','client_id':oauth.PUBLIC_CLIENT,'redirect_uri':self.redirect,
                         'scope':' '.join(oauth.SCOPES),'state':'fixture-state',
                         'code_challenge':oauth.b64(hashlib.sha256(self.verifier.encode()).digest()),
                         'code_challenge_method':'S256','resource':oauth.resource()}

    def event(self, path, data=None, method='POST', token=None):
        headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
        if token:headers['Authorization']='Bearer '+token
        return {'httpMethod':method,'path':path,'headers':headers,'body':json.dumps(data or {})}

    def flow(self, parameters=None, store=None):
        url=oauth.authorize(parameters or self.parameters)
        request=parse_qs(urlsplit(url).fragment)['oauth_request'][0]
        reply=oauth.approval(store or self.store,{'request':request,'approve':True})
        callback=parse_qs(urlsplit(reply['redirect_url']).query)
        self.assertEqual(callback['iss'],[oauth.base()])
        return request, {'grant_type':'authorization_code','code':callback['code'][0],
                         'redirect_uri':self.redirect,'client_id':(parameters or self.parameters)['client_id'],
                         'code_verifier':self.verifier,'resource':oauth.resource()}

    def token(self, scope=None):
        parameters=dict(self.parameters,scope=scope) if scope else self.parameters
        _, exchange=self.flow(parameters)
        return oauth.exchange(self.store,exchange)['access_token']

    def call(self, name, arguments, token=None):
        return handle(self.event('/mcp',{'jsonrpc':'2.0','id':1,'method':'tools/call',
                                        'params':{'name':name,'arguments':arguments}},token=token),lambda:self.store,Mock())

    def test_public_discovery_and_transport_do_not_access_storage(self):
        factory=Mock(side_effect=AssertionError('must not initialize storage'))
        for path in ('/.well-known/oauth-protected-resource/mcp','/.well-known/oauth-authorization-server'):
            response=handle(self.event(path,method='GET'),factory)
            self.assertEqual(response['statusCode'],200)
        for method,params in (('initialize',{'protocolVersion':'2025-11-25'}),('tools/list',{})):
            response=handle(self.event('/mcp',{'jsonrpc':'2.0','id':1,'method':method,'params':params}),factory)
            self.assertEqual(response['statusCode'],200)
        self.assertEqual(len(json.loads(response['body'])['result']['tools']),5)
        self.assertNotIn('content-type',{key.lower() for key in response['headers']})
        self.assertEqual([t['annotations']['readOnlyHint'] for t in TOOLS],[False,True,True,True,False])
        self.assertEqual(handle(self.event('/mcp',method='GET'),factory)['statusCode'],405)
        notification=handle(self.event('/mcp',{'jsonrpc':'2.0','method':'notifications/initialized'}),factory)
        self.assertEqual((notification['statusCode'],notification['body']),(202,''))
        invalid=self.event('/mcp',{'jsonrpc':'2.0','id':1,'method':'ping'})
        invalid['headers']['MCP-Protocol-Version']='unknown'
        self.assertEqual(handle(invalid,factory)['statusCode'],400)
        invalid['headers']={'Origin':'https://evil.example'}
        self.assertEqual(handle(invalid,factory)['statusCode'],403)
        factory.assert_not_called()

    def test_only_current_maintainer_can_approve(self):
        request,_=self.flow()
        data={'request':request,'approve':True}
        for key,expected in ((None,401),(self.env['AI_SUBMIT_KEY'],403),('wrong-user',401)):
            event=self.event('/api/oauth/approve',data,token=key)
            self.assertEqual(handle(event,lambda:self.store)['statusCode'],expected)
        event=self.event('/api/oauth/inspect',{'request':request},token=self.env['PLATFORM_API_KEY'])
        self.assertEqual(handle(event,Mock(side_effect=AssertionError()))['statusCode'],200)
        denied=handle(self.event('/api/oauth/approve',{'request':request,'approve':False},token=self.env['PLATFORM_API_KEY']),Mock(side_effect=AssertionError()))
        self.assertIn('error=access_denied',json.loads(denied['body'])['redirect_url'])

    def test_discovery_client_compatibility_preserves_management_auth(self):
        factory=Mock(side_effect=AssertionError('discovery cannot read storage'))
        for accept in ('application/json','*/*','application/json, text/event-stream'):
            event=self.event('/mcp',{'jsonrpc':'2.0','id':1,'method':'tools/list'})
            event['headers'].update({'Accept':accept,'Origin':'https://chatgpt.com','MCP-Protocol-Version':'2024-11-05'})
            response=handle(event,factory)
            self.assertEqual(response['statusCode'],200)
            self.assertEqual(len(json.loads(response['body'])['result']['tools']),5)
        for accept in ('text/event-stream','text/html','application/json;q=0'):
            event['headers']['Accept']=accept
            self.assertEqual(handle(event,factory)['statusCode'],406)
        event['headers']['Accept']='application/json'
        event['body']=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'listPublishedArticleIndex','arguments':{}}})
        self.assertEqual(handle(event,factory)['statusCode'],401)
        event['path']='/api/articles/save'
        self.assertEqual(handle(event,factory)['statusCode'],403)
        factory.assert_not_called()

    def test_code_single_use_and_pkce_audience_client_redirect_binding(self):
        request,parameters=self.flow()
        self.assertEqual(oauth.inspect_request({'request':request})['redirect_uri'],self.redirect)
        for key,bad in (('resource',oauth.base()),('client_id','wrong'),('redirect_uri','https://evil.example/callback'),('code_verifier','z'*43)):
            with self.assertRaises(oauth.OAuthError):oauth.exchange(self.store,dict(parameters,**{key:bad}))
        value=oauth.exchange(self.store,parameters)
        self.assertEqual(value['token_type'],'Bearer')
        self.assertEqual(oauth.access('Bearer '+value['access_token'],'labview:submit')['sub'],oauth.credentials()[1])
        with self.assertRaises(oauth.OAuthError):oauth.exchange(self.store,parameters)
        with self.assertRaises(oauth.OAuthError):oauth.verify(request,'access')

    def test_auth_code_consumption_one_winner_and_fail_closed_on_lost_response(self):
        _,parameters=self.flow()
        def exchange(_):
            try:oauth.exchange(self.store,parameters);return True
            except oauth.OAuthError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(exchange,range(2))),1)
        _,parameters=self.flow()
        self.cos.lose_response=True
        with self.assertRaises(StorageError):oauth.exchange(self.store,parameters)
        self.cos.lose_response=False
        with self.assertRaises(oauth.OAuthError):oauth.exchange(self.store,parameters)

    def test_tokens_reject_tamper_expiration_issuer_owner_scope_and_rotation(self):
        token=self.token('labview:status')
        claims=oauth.verify(token,'access')
        variants=[token+'x']
        for updates in ({'iss':'https://evil.example'},{'aud':'https://evil.example/mcp'},{'sub':'other-maintainer'},
                        {'exp':claims['iat']-1},{'iat':claims['exp']+1}):
            variants.append(oauth.sign(dict(claims,**updates)))
        for value in variants:
            with self.assertRaises(oauth.OAuthError):oauth.access('Bearer '+value,'labview:status')
        self.assertEqual(self.call('submitArticleForReview',{},token)['statusCode'],403)
        with patch.dict(os.environ,{'AI_SUBMIT_KEY':'rotated-'+'c'*40}):
            with self.assertRaises(oauth.OAuthError):oauth.access('Bearer '+token,'labview:status')
        with patch.dict(os.environ,{'PLATFORM_API_KEY':'new-owner-'+'d'*40}):
            with self.assertRaises(oauth.OAuthError):oauth.access('Bearer '+token,'labview:status')

    def test_exact_callback_registration_and_cimd_origin_pin(self):
        for updates in ({'redirect_uri':'https://evil.example/callback'},{'resource':oauth.base()},
                        {'code_challenge_method':'plain'},{'scope':'admin:write'},{'client_id':'https://127.0.0.1/client.json'}):
            with self.assertRaises(oauth.OAuthError):oauth.authorize(dict(self.parameters,**updates))
        with patch.dict(os.environ,{'MCP_ALLOWED_REDIRECT_URIS':''}):
            with self.assertRaises(oauth.OAuthError):oauth.authorize(self.parameters)
        with patch.dict(os.environ,{'MCP_OAUTH_ENABLED':''}):
            with self.assertRaises(oauth.OAuthError) as error:oauth.authorize(self.parameters)
            self.assertEqual(error.exception.code,'oauth_not_enabled')
        with patch('mcp_oauth.client_metadata',return_value={'redirect_uris':(self.redirect,),'method':'none','jwks_uri':''}) as metadata:
            oauth.authorize(dict(self.parameters,client_id='https://chatgpt.com/oauth/client.json'))
            self.assertEqual(metadata.call_args.args[0],'https://chatgpt.com/oauth/client.json')
        for client in ('https://evil.example/client.json','http://chatgpt.com/oauth/client.json','https://chatgpt.com@evil.example/oauth/client.json'):
            with self.assertRaises(oauth.OAuthError):oauth.client_metadata(client,0)

    def test_tencent_query_string_event_authorizes_and_rejects_duplicate_parameters(self):
        event=self.event('/oauth/authorize',method='GET')
        event['queryString']=self.parameters
        reply=handle(event,Mock(side_effect=AssertionError('no storage')))
        self.assertEqual(reply['statusCode'],302)
        self.assertTrue(reply['headers']['Location'].startswith(self.env['PLATFORM_ALLOWED_ORIGIN']+'/#oauth_request='))
        event['rawQueryString']=urlencode(self.parameters)+'&state=duplicate'
        self.assertEqual(handle(event)['statusCode'],400)

    def test_private_key_jwt_claim_binding_and_durable_replay_protection(self):
        client_id='https://chatgpt.com/oauth/client.json'
        metadata={'redirect_uris':(self.redirect,),'method':'private_key_jwt','jwks_uri':'https://chatgpt.com/oauth/jwks.json'}
        key={'kid':'fixture','kty':'RSA','alg':'RS256','use':'sig'}
        now=int(time.time())
        claims={'iss':client_id,'sub':client_id,'aud':oauth.base()+'/oauth/token','iat':now,'exp':now+120,'jti':'fixture-assertion-1'}
        def assertion(values):
            return '.'.join((oauth.b64(json.dumps({'alg':'RS256','kid':'fixture'}).encode()),
                             oauth.b64(json.dumps(values).encode()),oauth.b64(b'fixture-signature')))
        with patch('mcp_oauth.client_metadata',return_value=metadata),patch('mcp_oauth.client_keys',return_value=[key]),patch('mcp_oauth.verify_rs256') as verifier:
            _,parameters=self.flow(dict(self.parameters,client_id=client_id))
            parameters.update(client_assertion_type='urn:ietf:params:oauth:client-assertion-type:jwt-bearer',client_assertion=assertion(claims))
            for change in ({'iss':'other-client'},{'sub':'other-client'},{'aud':'https://evil.example'},
                           {'exp':now-1},{'iat':now+1000},{'jti':''}):
                with self.assertRaises(oauth.OAuthError):oauth.exchange(self.store,dict(parameters,client_assertion=assertion(dict(claims,**change))))
            with patch('mcp_oauth.verify_rs256',side_effect=ValueError('bad signature')):
                with self.assertRaises(oauth.OAuthError):oauth.exchange(self.store,parameters)
            self.assertIn('access_token',oauth.exchange(self.store,parameters))
            verifier.assert_called()
            _,other=self.flow(dict(self.parameters,client_id=client_id))
            other.update(client_assertion_type=parameters['client_assertion_type'],client_assertion=parameters['client_assertion'])
            with self.assertRaises(oauth.OAuthError):oauth.exchange(self.store,other)

    def test_cimd_document_accepts_private_key_jwt_and_pins_jwks(self):
        client='https://chatgpt.com/oauth/client.json'
        document={'client_id':client,'redirect_uris':[self.redirect], 'token_endpoint_auth_method':'private_key_jwt',
                  'jwks_uri':'https://chatgpt.com/oauth/jwks.json'}
        oauth.client_metadata.cache_clear()
        self.addCleanup(oauth.client_metadata.cache_clear)
        with patch('mcp_oauth.public_json',return_value=document):
            self.assertEqual(oauth.client_metadata(client,123)['method'],'private_key_jwt')
        with patch('mcp_oauth.public_json',return_value=dict(document,jwks_uri='https://evil.example/jwks.json')):
            with self.assertRaises(oauth.OAuthError):oauth.client_metadata(client,124)
        with patch('mcp_oauth.public_json',return_value=dict(document,token_endpoint_auth_methods_supported=['none','private_key_jwt'])):
            metadata=oauth.client_metadata(client,125)
            self.assertEqual(set(metadata['methods']),{'none','private_key_jwt'})
        with patch('mcp_oauth.client_metadata',return_value=metadata):
            self.assertIsNone(oauth.client_assertion({'client_id':client}))

    def test_existing_cimd_callback_survives_new_connection_environment(self):
        client='https://chatgpt.com/oauth/client.json'
        document={'client_id':client,'redirect_uris':[oauth.CHATGPT_CIMD_REDIRECT],
                  'token_endpoint_auth_methods_supported':['none']}
        oauth.client_metadata.cache_clear()
        self.addCleanup(oauth.client_metadata.cache_clear)
        with patch('mcp_oauth.public_json',return_value=document):
            url=oauth.authorize(dict(self.parameters,client_id=client,redirect_uri=oauth.CHATGPT_CIMD_REDIRECT))
            self.assertTrue(url.startswith(self.env['PLATFORM_ALLOWED_ORIGIN']+'/#oauth_request='))
            with self.assertRaises(oauth.OAuthError):
                oauth.authorize(dict(self.parameters,client_id=client,redirect_uri='https://chatgpt.com/unregistered'))
        with patch('mcp_oauth.public_json',side_effect=oauth.OAuthError('client_metadata_unreachable',503)):
            with self.assertRaises(oauth.OAuthError) as error: oauth.client_metadata(client,999)
            self.assertEqual((error.exception.code,error.exception.status),('client_metadata_unreachable',503))

    def test_deployed_public_documents_work_without_network_and_expire_closed(self):
        now=int(time.time())
        client,keys=oauth.PUBLIC_DOCUMENT_URLS
        data={'fetched_at':now,'expires_at':now+72*3600,'documents':{
            client:{'client_id':client,'redirect_uris':[oauth.CHATGPT_CIMD_REDIRECT],
                    'jwks_uri':keys,'token_endpoint_auth_methods_supported':['none','private_key_jwt']},
            keys:{'keys':[{'kty':'RSA','kid':'fixture','n':'public-modulus','e':'AQAB'}]}}}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'public.json'
            path.write_text(json.dumps(data),encoding='utf-8')
            with patch.object(oauth,'SNAPSHOT_PATH',path),patch.object(oauth,'public_json',side_effect=oauth.OAuthError('client_metadata_unreachable',503)) as network:
                self.assertEqual(oauth.client_public_json(client)['client_id'],client)
                self.assertEqual(len(oauth.client_public_json(keys)['keys']),1)
                network.assert_not_called()
                data['expires_at']=now-1
                path.write_text(json.dumps(data),encoding='utf-8')
                with self.assertRaises(oauth.OAuthError):oauth.client_public_json(keys)
                data['expires_at']=now+72*3600
                data['documents'][keys]['keys'][0]['d']='private-not-allowed'
                with self.assertRaises(ValueError):oauth.validate_public_snapshot(data)

    def test_mcp_submit_duplicate_conflict_and_unknown_write_readback(self):
        token=self.token()
        data={'submission_id':'mcp-fixture-submission-0001','title':'MCP测试','markdown':'# 待人工审核'}
        for _ in range(2):
            response=self.call('submitArticleForReview',data,token)
            self.assertFalse(json.loads(response['body'])['result']['isError'])
        self.assertEqual(len(self.store.list()),1)
        conflict=self.call('submitArticleForReview',dict(data,markdown='另一版'),token)
        self.assertEqual(json.loads(conflict['body'])['result']['structuredContent']['http_status'],409)
        data=dict(data,submission_id='mcp-fixture-submission-0002')
        self.cos.lose_response=True
        unknown=self.call('submitArticleForReview',data,token)
        self.assertEqual(json.loads(unknown['body'])['result']['structuredContent']['http_status'],503)
        self.cos.lose_response=False
        readback=self.call('getSubmissionStatus',{'submission_id':data['submission_id']},token)
        self.assertEqual(json.loads(readback['body'])['result']['structuredContent']['status'],'saved')
        self.assertEqual(len(self.store.list()),2)

    def test_mcp_unauthorized_and_management_routes_are_not_exposed(self):
        factory=Mock(side_effect=AssertionError())
        event=self.event('/mcp',{'jsonrpc':'2.0','id':1,'method':'tools/call',
                                 'params':{'name':'getSubmissionStatus','arguments':{'submission_id':'unknown-fixture-0001'}}})
        response=handle(event,factory)
        self.assertEqual(response['statusCode'],401)
        self.assertIn('resource_metadata=',response['headers']['WWW-Authenticate'])
        factory.assert_not_called()
        token=self.token()
        for path in ('/api/articles/sync','/api/articles/save','/api/index/save','/api/images/upload','/api/ai/submit','/api/oauth/approve'):
            self.assertEqual(handle(self.event(path,token=token),factory)['statusCode'],401)
        unknown=self.call('syncWeChatDraft',{},token)
        self.assertEqual(json.loads(unknown['body'])['error']['code'],-32602)
        response=self.call('getSubmissionStatus',{'submission_id':'unknown-fixture-0001'},self.env['AI_SUBMIT_KEY'])
        self.assertEqual(response['statusCode'],401)

    def test_old_scopes_cannot_create_wechat_drafts(self):
        token=self.token('labview:submit labview:status labview:index')
        factory=Mock(side_effect=AssertionError('insufficient scope must not read storage'))
        client=Mock(side_effect=AssertionError('insufficient scope must not initialize WeChat'))
        event=self.event('/mcp',{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
            'name':'createWeChatDraft','arguments':{'submission_id':'mobile-old-scope-0001','review_hash':'a'*64,'confirm':True}}},token=token)
        response=handle(event,factory,client)
        self.assertEqual(response['statusCode'],403)
        self.assertIn('labview:wechat-draft',response['headers']['WWW-Authenticate'])
        self.assertTrue(json.loads(response['body'])['result']['isError'])
        factory.assert_not_called();client.assert_not_called()

    def test_mobile_draft_tools_transport_and_safe_wechat_errors(self):
        token=self.token()
        data={'submission_id':'mobile-mcp-fixture-0001','title':'MCP手机稿','markdown':'# 正文'}
        receipt=json.loads(self.call('submitArticleForReview',data,token)['body'])['result']['structuredContent']
        view=json.loads(self.call('getWeChatDraftStatus',{'submission_id':data['submission_id']},token)['body'])['result']['structuredContent']
        self.assertEqual(view['status'],'saved')
        arguments={'submission_id':data['submission_id'],'review_hash':view['review_hash'],'confirm':True}
        event=self.event('/mcp',{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'createWeChatDraft','arguments':arguments}},token=token)
        from wechat_draft_probe import ProbeError
        client=Mock();client.call.side_effect=ProbeError('WeChat errcode=48001 secret-fixture-do-not-return')
        with patch.dict(os.environ,{'ENABLE_DRAFT_SYNC':'true','WECHAT_APP_ID':'fixture','WECHAT_APP_SECRET':'fixture-secret'}):
            reply=handle(event,lambda:self.store,Mock(return_value=client))
        result=json.loads(reply['body'])['result']
        self.assertTrue(result['isError'])
        self.assertEqual(result['structuredContent']['errcode'],48001)
        self.assertNotIn('secret-fixture',reply['body'])
        client.upload_cover.assert_not_called()
        self.assertEqual(self.store.get(receipt['article_id'])['status'],'saved')
        client.call.side_effect=ProbeError('WeChat errcode=40164；当前出口 IP 未加入公众号白名单。 微信看到的出口 IP：203.0.113.8')
        with patch.dict(os.environ,{'ENABLE_DRAFT_SYNC':'true','WECHAT_APP_ID':'fixture','WECHAT_APP_SECRET':'fixture-secret'}):
            reply=handle(event,lambda:self.store,Mock(return_value=client))
        self.assertEqual(json.loads(reply['body'])['result']['structuredContent']['outbound_ip'],'203.0.113.8')

    def test_http_authorize_exchange_and_no_credential_in_grant_records(self):
        start=self.event('/oauth/authorize',method='GET')
        start['queryStringParameters']=self.parameters
        redirect=handle(start,lambda:self.store)
        self.assertEqual(redirect['statusCode'],302)
        request=parse_qs(urlsplit(redirect['headers']['Location']).fragment)['oauth_request'][0]
        approved=handle(self.event('/api/oauth/approve',{'request':request,'approve':True},token=self.env['PLATFORM_API_KEY']),lambda:self.store)
        self.assertEqual(approved['statusCode'],200)
        code=parse_qs(urlsplit(json.loads(approved['body'])['redirect_url']).query)['code'][0]
        parameters={'grant_type':'authorization_code','code':code,'redirect_uri':self.redirect,
                    'client_id':oauth.PUBLIC_CLIENT,'code_verifier':self.verifier,'resource':oauth.resource()}
        event=self.event('/oauth/token');event['headers']['Content-Type']='application/x-www-form-urlencoded'
        event['body']=urlencode(parameters)
        response=handle(event,lambda:self.store)
        self.assertEqual(response['statusCode'],200)
        token=json.loads(response['body'])['access_token']
        stored=b' '.join(self.cos.objects.values()).decode()
        for secret in (token,code,self.env['AI_SUBMIT_KEY'],self.env['PLATFORM_API_KEY']):
            self.assertNotIn(secret,stored)
        bad=handle(event,lambda:self.store)
        self.assertEqual(json.loads(bad['body'])['error'],'invalid_grant')

    def test_local_store_supports_durable_one_time_codes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            store=Store(Path(directory)/'articles.sqlite3')
            _,parameters=self.flow(store=store)
            oauth.exchange(store,parameters)
            with self.assertRaises(oauth.OAuthError):oauth.exchange(store,parameters)
            self.assertEqual(store.list(),[])


if __name__=='__main__':
    unittest.main()

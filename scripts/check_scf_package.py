"""Offline smoke check in an isolated interpreter; no cloud credentials needed."""
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as directory:
        with ZipFile(ROOT / 'dist/scf-probe.zip') as archive:
            for name in archive.namelist():
                if Path(name).is_absolute() or '..' in Path(name).parts:
                    raise ValueError('Unsafe archive path')
            archive.extractall(directory)
        # -I ignores user site-packages/PYTHONPATH. Require SDK to load from this ZIP.
        code = '''import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import qcloud_cos
assert Path(qcloud_cos.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
from qcloud_cos.cos_comm import mapped
assert mapped({'Metadata':{'x-cos-forbid-overwrite':'true'}})['x-cos-forbid-overwrite']=='true'
from qcloud_cos import CosConfig, CosS3Client
from unittest.mock import patch
from types import SimpleNamespace
client=CosS3Client(CosConfig(Region='ap-guangzhou',SecretId='dummy-id',SecretKey='dummy-key',Scheme='https'))
with patch.object(client,'send_request',return_value=SimpleNamespace(headers={})) as send:
    client.put_object(Bucket='dummy-123456',Key='test.json',Body=b'{}',Metadata={'x-cos-forbid-overwrite':'true'})
    assert send.call_args.kwargs['headers']['x-cos-forbid-overwrite']=='true'
from Crypto.Cipher import AES
AES.new(b'0'*16, AES.MODE_ECB).encrypt(b'0'*16)
import scf_probe, cloud_api, cos_store, ai_submission, mcp_oauth, mcp_server
assert scf_probe.main_handler({}, {})['status']=='rejected'
from Crypto.PublicKey import RSA
from Crypto.Hash import SHA256
from Crypto.Signature import pkcs1_15
import json, time, os
snapshot=mcp_oauth.validate_public_snapshot(json.loads(mcp_oauth.SNAPSHOT_PATH.read_text(encoding='utf-8')))
with patch.object(mcp_oauth,'public_json',side_effect=AssertionError('Runtime must use deployed public documents')):
    assert mcp_oauth.client_metadata(mcp_oauth.PUBLIC_DOCUMENT_URLS[0],0)['redirect_uris']==(mcp_oauth.CHATGPT_CIMD_REDIRECT,)
    assert mcp_oauth.client_keys(mcp_oauth.PUBLIC_DOCUMENT_URLS[1],0)
key=RSA.generate(2048)
jwk={'kty':'RSA','kid':'offline-fixture','alg':'RS256','use':'sig',
     'n':mcp_oauth.b64(key.n.to_bytes((key.n.bit_length()+7)//8,'big')),
     'e':mcp_oauth.b64(key.e.to_bytes((key.e.bit_length()+7)//8,'big'))}
client_id='https://chatgpt.com/oauth/client.json'
now=int(time.time())
claims={'iss':client_id,'sub':client_id,'aud':mcp_oauth.BASE+'/oauth/token','iat':now,'exp':now+120,'jti':'offline-fixture'}
data=mcp_oauth.b64(json.dumps({'alg':'RS256','kid':jwk['kid']}).encode())+'.'+mcp_oauth.b64(json.dumps(claims).encode())
signature=pkcs1_15.new(key).sign(SHA256.new(data.encode()))
metadata={'method':'private_key_jwt','jwks_uri':'https://chatgpt.com/oauth/jwks.json'}
params={'client_id':client_id,'client_assertion_type':'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
        'client_assertion':data+'.'+mcp_oauth.b64(signature)}
with patch.object(mcp_oauth,'client_metadata',return_value=metadata), patch.object(mcp_oauth,'client_keys',return_value=[jwk]):
    assert mcp_oauth.client_assertion(params)
    params['client_assertion']=data+'.'+mcp_oauth.b64(bytes([signature[0]^1])+signature[1:])
    try:
        mcp_oauth.client_assertion(params)
    except mcp_oauth.OAuthError:
        pass
    else:
        raise AssertionError('Tampered client assertion accepted')
print('PASS: deployment ZIP COS SDK, native crypto, RS256 assertions and application imports.')
'''
        subprocess.run([sys.executable, '-I', '-c', code, directory], check=True, cwd=directory)


if __name__ == '__main__':
    main()

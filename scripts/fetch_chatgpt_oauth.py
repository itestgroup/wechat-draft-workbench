"""Fetch only official PUBLIC OAuth documents into the tested runtime artifact."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mcp_oauth as oauth


def main():
    now = int(time.time())
    data = {'fetched_at':now,'expires_at':now+72*3600,
            'documents':{url:oauth.public_json(url) for url in oauth.PUBLIC_DOCUMENT_URLS}}
    oauth.validate_public_snapshot(data)
    target = Path(__file__).resolve().parents[1]/'.build/scf-runtime/chatgpt-oauth-public.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,separators=(',',':')),encoding='utf-8')
    print('Verified official ChatGPT public metadata and JWKS; validity 72 hours.')


if __name__=='__main__':
    main()

"""SCF console-invoked read-only probe; never logs event, context or credentials."""
import ipaddress
import os
import re

from wechat_client import WeChat
from wechat_draft_probe import ProbeError


def main_handler(event, context):
    if isinstance(event, dict) and 'httpMethod' in event:
        from cloud_api import handle
        return handle(event)
    if isinstance(event, dict) and event.get('action') == 'labview.storage.check':
        stage = 'initialize_storage'
        try:
            from cos_store import from_environment
            store = from_environment()
            stage = 'list_articles'
            count = len(store.list())
            return {'status': 'ok', 'storage': 'ok', 'article_count': count, 'writes_performed': False}
        except Exception as error:
            from cos_store import safe_diagnostic
            return dict({'status': 'failed', 'stage': stage, 'writes_performed': False},
                        **safe_diagnostic(error))
    # Credentials must be configured through the cloud console, never test events.
    if not isinstance(event, dict) or event.get('action') not in {'labview.check', 'labview.published.check'}:
        return {'status': 'rejected', 'reason': 'Unsupported console action.'}
    appid = os.environ.get('WECHAT_APP_ID', '').strip()
    secret = os.environ.get('WECHAT_APP_SECRET', '').strip()
    if not appid or not secret:
        return {'status': 'configuration_required',
                'reason': 'Configure WECHAT_APP_ID and WECHAT_APP_SECRET in the SCF console.'}
    try:
        client = WeChat(appid, secret)
        if event['action'] == 'labview.published.check':
            result = client.call('freepublish/batchget', payload={'offset': 0, 'count': 1, 'no_content': 1})
            counts = [result.get(key) for key in ('total_count', 'item_count')]
            items = result.get('item', [])
            if (any(type(value) is not int or value < 0 for value in counts)
                    or not isinstance(items, list) or counts[1] != len(items)
                    or counts[1] > 1 or counts[1] > counts[0]):
                return {'status': 'failed', 'reason': 'Unexpected published list response.',
                        'writes_performed': False}
            return {'status': 'ok', 'endpoint': 'freepublish/batchget',
                    'total_count': counts[0], 'item_count': counts[1],
                    'history_completeness': 'not_verified', 'writes_performed': False}
        result = client.call('draft/count')
        count = result.get('total_count')
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return {'status': 'failed', 'reason': 'Unexpected draft count response.'}
        return {'status': 'ok', 'stable_token': 'ok', 'draft_count': count,
                'writes_performed': False}
    except ProbeError as exc:
        # Build a fresh allowlisted result; do not return exception strings.
        diagnostic = str(exc)
        output = {'status': 'failed', 'writes_performed': False}
        code = re.search(r'errcode=(\d+)', diagnostic)
        if code:
            output['errcode'] = int(code.group(1))
        if output.get('errcode') == 40164:
            candidate = diagnostic.split('微信看到的出口 IP：', 1)
            if len(candidate) == 2:
                try:
                    output['outbound_ip'] = str(ipaddress.ip_address(candidate[1].strip()))
                except ValueError:
                    pass
            output['reason'] = 'Add outbound_ip (or the SCF fixed outbound IP) to the WeChat whitelist.'
        else:
            output['reason'] = 'WeChat check failed; no automatic retry.'
        return output
    except Exception:
        return {'status': 'failed', 'reason': 'Unexpected failure; inspect runtime configuration.',
                'writes_performed': False}


def route(event, context, existing_handler):
    """Opt-in integration: keep the existing handler for all unrelated events."""
    if isinstance(event, dict) and event.get('action') == 'labview.check':
        return main_handler(event, context)
    return existing_handler(event, context)


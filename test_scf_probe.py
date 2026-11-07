import io
import json
import os
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch

from scf_probe import main_handler, route
from wechat_draft_probe import ProbeError


class SCFProbeTests(unittest.TestCase):
    def test_published_probe_is_read_only_and_returns_counts_only(self):
        with patch.dict(os.environ, {'WECHAT_APP_ID':'dummy', 'WECHAT_APP_SECRET':'dummy'}), patch('scf_probe.WeChat') as client:
            client.return_value.call.return_value = {'total_count':2,'item_count':1,'item':[{'content':'DO_NOT_RETURN'}]}
            result = main_handler({'action':'labview.published.check'}, {})
            self.assertEqual(result['total_count'],2)
            self.assertFalse(result['writes_performed'])
            self.assertNotIn('DO_NOT_RETURN',json.dumps(result))
            client.return_value.call.assert_called_once_with('freepublish/batchget',payload={'offset':0,'count':1,'no_content':1})
            client.return_value.call.side_effect = ProbeError('WeChat errcode=48001; SECRET')
            result = main_handler({'action':'labview.published.check'}, {})
            self.assertEqual(result['errcode'],48001)
            self.assertNotIn('SECRET',json.dumps(result))

    def test_storage_error_diagnostic_never_returns_exception_text(self):
        for error, expected in [(ImportError('SECRET_DO_NOT_PRINT'), 'SDK_UNAVAILABLE'),
                                (RuntimeError('SECRET_DO_NOT_PRINT'), 'STORAGE_CHECK_FAILED')]:
            with patch('cos_store.from_environment', side_effect=error):
                result = main_handler({'action':'labview.storage.check'}, {})
                self.assertEqual(result['code'], expected)
                self.assertNotIn('SECRET_DO_NOT_PRINT', json.dumps(result))
                self.assertFalse(result['writes_performed'])

    def test_storage_access_denied_diagnostic(self):
        from cos_store import safe_diagnostic
        error = Mock()
        error.get_error_code.return_value = 'AccessDenied'
        self.assertEqual(safe_diagnostic(error)['code'], 'AccessDenied')
        error.get_error_code.return_value = 'SECRET_DO_NOT_PRINT'
        self.assertNotIn('SECRET_DO_NOT_PRINT', json.dumps(safe_diagnostic(error)))

    def test_unrelated_event_is_passed_through(self):
        existing = Mock(return_value={'old_task': 'ok'})
        event = {'Type': 'Timer', 'TriggerName': 'existing-job'}
        with patch('scf_probe.WeChat') as client:
            self.assertEqual(route(event, {}, existing), {'old_task': 'ok'})
            existing.assert_called_once_with(event, {})
            client.assert_not_called()

    def test_missing_action_and_credentials_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True), patch('scf_probe.WeChat') as client:
            self.assertEqual(main_handler({}, {})['status'], 'rejected')
            self.assertEqual(main_handler({'action': 'labview.check'}, {})['status'], 'configuration_required')
            client.assert_not_called()

    def test_success_has_no_secret_and_no_logs(self):
        with patch.dict(os.environ, {'WECHAT_APP_ID': 'dummy-app', 'WECHAT_APP_SECRET': 'dummy-secret'}), patch('scf_probe.WeChat') as client:
            client.return_value.call.return_value = {'total_count': 3}
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                result = main_handler({'action': 'labview.check'}, {})
            self.assertEqual(result['draft_count'], 3)
            self.assertFalse(result['writes_performed'])
            self.assertEqual(output.getvalue(), '')
            self.assertNotIn('dummy-secret', json.dumps(result))
            client.return_value.call.assert_called_once_with('draft/count')

    def test_whitelist_ip_is_returned_without_raw_response(self):
        with patch.dict(os.environ, {'WECHAT_APP_ID': 'dummy-app', 'WECHAT_APP_SECRET': 'dummy-secret'}), patch('scf_probe.WeChat') as client:
            client.return_value.call.side_effect = ProbeError('WeChat errcode=40164；微信看到的出口 IP：203.0.113.8')
            result = main_handler({'action': 'labview.check'}, {})
            self.assertEqual(result['outbound_ip'], '203.0.113.8')
            self.assertEqual(result['errcode'], 40164)


if __name__ == '__main__':
    unittest.main()

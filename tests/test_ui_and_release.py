import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from groupbuy.adapters.ui_flow import can_resume, collect
from groupbuy.common import GateError, digest
from groupbuy.config import task_hash
from tests.support import FakeDriver, config, frame, task


class UITests(unittest.TestCase):
    def setUp(self):
        self.t = task()
        self.t['ui_profile'] = {'steps': [{'action': 'type_search', 'text': '搜索商家'}]}
        self.runtime = config(None)['runtime']

    def test_observed_search_bottom_and_wait(self):
        driver = FakeDriver([frame(), frame(), frame(), frame(True), frame(True)])
        result = collect(self.t, self.runtime, driver)
        self.assertTrue(result['bottom_reached'])
        self.assertEqual(result['stop_reason'], 'explicit_bottom')
        self.assertIn(('type_search', self.t['search_term']), driver.actions)
        self.assertTrue(all(1 <= x[1] <= 2 for x in driver.actions if x[0] == 'wait'))

    def test_budget_resume_same_list_only(self):
        self.runtime['max_scrolls'] = 1
        driver = FakeDriver([frame()] * 6)
        prior = collect(self.t, self.runtime, driver)
        self.assertFalse(prior['bottom_reached'])
        self.assertEqual(prior['stop_reason'], 'scroll_budget')
        can_resume(self.t, prior, frame())
        with self.assertRaisesRegex(GateError, 'resume_window_or_list_mismatch'):
            can_resume(self.t, prior, frame(list_hash=digest('changed')))
        resumed = collect(self.t, self.runtime, FakeDriver([frame(), frame(True), frame(True)]), prior)
        self.assertEqual(resumed['capture_start'], prior['capture_start'])
        self.assertEqual(resumed['filter_frame_hash'], prior['filter_frame_hash'])
        self.assertTrue(resumed['bottom_reached'])

    def test_focus_and_verification_stop(self):
        bad = frame()
        bad['foreground'] = False
        for f in (bad, frame(risk=True)):
            driver = FakeDriver([f])
            with self.assertRaises(GateError):
                collect(self.t, self.runtime, driver)
            self.assertEqual(driver.actions, [])

    def test_wrong_target_not_confirmed(self):
        f = frame()
        f['text'] = '另一个虚构广场'
        with self.assertRaisesRegex(GateError, 'target_or_filter_not_visible'):
            collect(self.t, self.runtime, FakeDriver([frame(), frame(), f]))

    def test_wrong_task_cannot_resume(self):
        self.runtime['max_scrolls'] = 1
        prior = collect(self.t, self.runtime, FakeDriver([frame()] * 6))
        prior['task_hash'] = task_hash(task(2))
        with self.assertRaisesRegex(GateError, 'unsafe_scroll_resume'):
            can_resume(self.t, prior, frame())

    def test_window_interrupt(self):
        with self.assertRaisesRegex(GateError, 'window_changed'):
            collect(self.t, self.runtime, FakeDriver([frame(), frame(), frame(window='another')]))

    def test_terminal_text_in_product_name_is_not_bottom(self):
        self.runtime['max_scrolls'] = 1
        f = frame()
        f['list_text'] = '没有更多套餐特惠'
        result = collect(self.t, self.runtime, FakeDriver([f] * 6))
        self.assertEqual(result['stop_reason'], 'scroll_budget')


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        from tools import release_check
        self.mod = release_check
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mock = patch.object(self.mod, 'ROOT', self.root)
        self.mock.start()
        self.addCleanup(self.mock.stop)
        (self.root / 'publication-files.json').write_text('["publication-files.json", "README.md"]', encoding='utf-8')
        (self.root / 'README.md').write_text('Fictional project', encoding='utf-8')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic Test')
        self.git('config', 'user.email', 'synthetic@example.invalid')

    def git(self, *args):
        subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)

    def test_working_staged_and_history_clean(self):
        self.git('add', 'README.md', 'publication-files.json')
        self.git('commit', '-qm', 'Synthetic initial')
        report = self.mod.scan()
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['counts']['history_commits'], 1)

    def test_ignored_secret_is_found(self):
        (self.root / '.gitignore').write_text('secret.local.json', encoding='utf-8')
        value = 'access_' + 'token=' + 'Z' * 25
        (self.root / 'secret.local.json').write_text(value, encoding='utf-8')
        report = self.mod.scan()
        self.assertEqual(report['status'], 'FAIL')
        self.assertTrue(any('credential_assignment' in x.get('codes', []) for x in report['issues']))
        self.assertNotIn('Z' * 25, json.dumps(report))

    def test_index_outside_allowlist(self):
        (self.root / 'capture.har').write_text('{}', encoding='utf-8')
        self.git('add', 'capture.har')
        report = self.mod.scan()
        self.assertTrue(any(x['surface'] == 'index' and 'outside_publication_allowlist' in x['codes'] for x in report['issues']))

    def test_phone_scan_distinguishes_structured_hash_and_actual_phone(self):
        phone = '138' + '00000000'
        sha = 'a' + phone + 'a' * 52
        def check(value):
            return self.mod.findings_for('fixture.json', json.dumps(value).encode(), False, set())
        self.assertNotIn('phone', check({'source_sha256': sha, 'files': {'shops.csv': sha}}))
        self.assertIn('phone', check({'note': sha}))
        self.assertIn('phone', check({'source_sha256': phone}))
        self.assertIn('phone', check({'source_sha256': sha, 'phone': phone}))

    def test_deleted_historical_secret_detected(self):
        (self.root / 'README.md').write_text('access_' + 'token=' + 'X' * 25, encoding='utf-8')
        self.git('add', 'README.md', 'publication-files.json')
        self.git('commit', '-qm', 'Synthetic test seed')
        (self.root / 'README.md').write_text('Clean now', encoding='utf-8')
        self.git('add', 'README.md')
        self.git('commit', '-qm', 'Remove seed')
        report = self.mod.scan()
        self.assertTrue(any(x['surface'] == 'history' for x in report['issues']))
        self.assertEqual(report['counts']['history_commits'], 2)

    def test_projected_capture_is_local_only_and_still_scanned(self):
        from groupbuy.adapters.dianping import DianpingAdapter
        from tests.test_capture import report
        raw = report()
        safe = DianpingAdapter().capture_entry({'search_term': '示例广场'}, raw)
        doc = {'_groupbuy_projected': True, 'log': {'version': '1.2', 'creator': {}, 'entries': [safe]}}
        path = 'runtime/capture/task/session/input.har'
        data = json.dumps(doc).encode()
        self.assertNotIn('forbidden_file_type', self.mod.findings_for(path, data, False, set()))
        self.assertIn('forbidden_file_type', self.mod.findings_for(path, data, True, {path}))
        safe['request']['headers'] = raw['request']['headers']
        self.assertIn('forbidden_file_type', self.mod.findings_for(path, json.dumps(doc).encode(), False, set()))


if __name__ == '__main__':
    unittest.main()

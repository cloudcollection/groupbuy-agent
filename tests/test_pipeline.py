import base64
import gzip
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from groupbuy.adapters import get_adapter
from groupbuy.adapters.dianping import DianpingAdapter, classify
from groupbuy.common import GateError, atomic_json, load_json
from groupbuy.config import load_config, task_hash
from groupbuy.exporter import csv_cell, merge, verify_export
from groupbuy.local_input import decode, read_responses
from groupbuy.models import PersonalPurchaseOrder
from groupbuy.privacy import clean, safe
from groupbuy.quality import validate_result
from groupbuy.runner import Runner
from groupbuy.scheduler import due_slot, tick
from tests.support import config, entries, entry, evidence, task


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.t, self.e, self.a = task(), entries(), DianpingAdapter()

    def parse(self):
        return self.a.parse(self.t, self.e, evidence(self.t))

    def test_complete_dedup_and_associations(self):
        r = validate_result(self.t, self.parse())
        self.assertEqual((len(r['shops']), len(r['products'])), (2, 2))
        self.assertEqual(r['quality']['duplicate_pages'], 1)
        self.assertIn('示例店', r['shops'][0]['name'])
        self.assertEqual(r['products'][0]['sales_text'], '已售1000+')
        self.assertEqual(r['products'][0]['price_unit'], 'CNY')
        self.assertEqual(r['products'][0]['visible_rules'], '周一至周五可用；需提前预约')
        self.assertEqual(r['shops'][0]['distance_m'], 1200)

    def test_gap(self):
        self.e[-1]['request']['start'] = 2
        with self.assertRaisesRegex(GateError, 'pagination_gap'):
            self.parse()

    def test_no_terminal(self):
        self.e[-1]['response']['data'].update(isEnd=False, nextStartIndex=2)
        with self.assertRaisesRegex(GateError, 'pagination_gap'):
            self.parse()

    def test_numeric_terminal_is_not_explicit_boolean(self):
        self.e[-1]['response']['data']['isEnd'] = 1
        with self.assertRaisesRegex(GateError, 'missing_terminal_flag'):
            self.parse()

    def test_string_confirmation_not_true(self):
        e = evidence(self.t)
        e['filters_confirmed'] = 'false'
        with self.assertRaisesRegex(GateError, 'missing_search_evidence'):
            self.a.parse(self.t, self.e, e)

    def test_conflicting_duplicate(self):
        bad = deepcopy(self.e[-1])
        bad['captured_at'] = '2030-01-01T09:04:00+08:00'
        bad['response']['data']['list'][0]['shopInfo']['score'] = '4.2'
        self.e.append(bad)
        with self.assertRaisesRegex(GateError, 'conflicting_duplicate_page'):
            self.parse()

    def test_incomplete_latest_round_no_fallback(self):
        newer = entry(0, 1, False, 3, 4, 'fictional-round-2')
        self.e.append(newer)
        with self.assertRaisesRegex(GateError, 'pagination_gap'):
            self.parse()

    def test_cursor_reset_is_not_end(self):
        self.e[-1]['response']['data'].update(isEnd=False, nextStartIndex=0)
        with self.assertRaisesRegex(GateError, 'unproven_cursor_reset'):
            self.parse()

    def test_orphan_page(self):
        self.e.append(entry(9, 0, True, 3, 4))
        with self.assertRaisesRegex(GateError, 'orphan_page'):
            self.parse()

    def test_task_query_isolation(self):
        self.e.append({**entry(9, 0, True, 3, 4), 'request': {'keyword': '另一个虚构地点', 'start': 9}})
        self.assertEqual(len(self.parse()['shops']), 2)

    def test_foreign_task_evidence(self):
        with self.assertRaisesRegex(GateError, 'evidence_task_mismatch'):
            self.a.parse(self.t, self.e, evidence(task(2)))

    def test_wrong_filter(self):
        e = evidence(self.t)
        e['selected_filters']['category'] = '购物'
        with self.assertRaisesRegex(GateError, 'search_or_filter_mismatch'):
            self.a.parse(self.t, self.e, e)

    def test_budget_not_bottom(self):
        e = evidence(self.t)
        e.update(stop_reason='scroll_budget', bottom_reached=False)
        with self.assertRaisesRegex(GateError, 'incomplete_ui'):
            self.a.parse(self.t, self.e, e)

    def test_product_shop_mismatch(self):
        self.e[-1]['response']['data']['list'][0]['shopInfo']['shopDealInfos'][0]['shopUuid'] = 'another-shop'
        with self.assertRaisesRegex(GateError, 'product_shop_mismatch'):
            self.parse()

    def test_product_numeric_shop_alias_matches_uuid_parent(self):
        raw = self.e[-1]['response']['data']['list'][0]['shopInfo']
        raw['shopId'] = 'fictional-numeric-shop'
        raw['shopDealInfos'][0]['shopId'] = raw['shopId']
        self.assertEqual(len(self.parse()['products']), 2)

    def test_missing_fields_are_null(self):
        raw = self.e[-1]['response']['data']['list'][0]['shopInfo']
        raw.pop('score')
        raw['shopDealInfos'][0].pop('originalPrice')
        raw['shopDealInfos'][0].pop('dealId')
        r = self.parse()
        self.assertIsNone(r['shops'][1]['rating'])
        self.assertIsNone(r['products'][1]['original_price'])
        self.assertIsNone(r['products'][1]['product_id'])

    def test_no_name_only_identity(self):
        self.e[-1]['response']['data']['list'][0]['shopInfo'].pop('shopUuid')
        with self.assertRaisesRegex(GateError, 'missing_shop_identity'):
            self.parse()

    def test_units_are_not_guessed(self):
        d = self.e[-1]['response']['data']['list'][0]['shopInfo']['shopDealInfos'][0]
        d.update(salePrice=7990, originalPrice=12000, priceUnit='分')
        r = self.parse()
        self.assertEqual((r['products'][1]['sale_price'], r['products'][1]['price_unit']), ('7990', '分'))
        d.pop('priceUnit')
        r = self.parse()
        self.assertIsNone(r['products'][1]['price_unit'])
        self.assertIn('product_price_unit_unknown', r['shops'][1]['quality_flags'])

    def test_ranges_and_plus_preserved(self):
        self.e[-1]['response']['data']['list'][0]['shopInfo']['shopDealInfos'][0]['salePrice'] = '¥79起'
        self.assertEqual(self.parse()['products'][1]['sale_price'], '¥79起')

    def test_unknown_distance_center_no_calculation(self):
        self.e[-1]['response']['data']['list'][0]['shopInfo'].pop('distanceCenter')
        self.assertIsNone(self.parse()['shops'][1]['distance_m'])

    def test_classification_boundaries(self):
        cases = [('川菜', '餐饮'), ('食品零售', '食品零售'), ('超市餐厅', '待复核'),
                 ('熟食熏酱', '待复核'), (None, '待复核'), ('健身', '其他'), ('烤鱼', '餐饮')]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(classify(text)[0], expected)

    def test_sensitive_free_text_and_projection(self):
        mobile = '138' + '00000000'
        raw = self.e[-1]['response']['data']['list'][0]['shopInfo']
        raw.update(openid='fictional-private-id', myLat=1, color='red')
        raw['shopDealInfos'][0]['useRules'] = '联系 ' + mobile
        result = self.parse()
        text = json.dumps(result)
        self.assertNotIn(mobile, text)
        self.assertNotIn('fictional-private-id', text)
        self.assertNotIn('myLat', text)

    def test_quality_rejects_orphan_product(self):
        r = self.parse()
        r['products'][0]['shop_id'] = 'missing'
        with self.assertRaisesRegex(GateError, 'orphan_product'):
            validate_result(self.t, r)

    def test_orders_unsupported(self):
        with self.assertRaises(TypeError):
            PersonalPurchaseOrder('fake-order', 'fake-account')

    def test_unimplemented_platform_rejected(self):
        with self.assertRaisesRegex(GateError, 'unsupported_platform'):
            get_adapter('unimplemented')

    def test_embedded_json_redacted(self):
        r = clean({'payload': json.dumps({'openid': 'private', 'note': 'public'}), 'headers': {'private': 'x'}})
        self.assertEqual(json.loads(r['payload']), {'note': 'public'})
        self.assertNotIn('headers', r)
        self.assertTrue(safe(r))

    def test_formula_injection(self):
        self.assertEqual(csv_cell('=2+2'), "'=2+2")
        self.assertEqual(csv_cell('已售100+'), '已售100+')


class FileAndRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.c = config(self.root)
        for t in self.c['tasks']:
            es = entries()
            for e in es:
                e.pop('source_hash')
                e.pop('entry_index')
            atomic_json(self.root / t['input']['path'], {'schema': 'groupbuy.local-responses.v1', 'synthetic': True, 'entries': es})
            atomic_json(self.root / t['input']['evidence'], evidence(t))
        self.config_path = self.root / 'config.json'
        self.save_config()

    def save_config(self):
        atomic_json(self.config_path, self.c)
        self.loaded = load_config(self.config_path, self.root)
        self.runner = Runner(self.loaded)

    def test_batch_five_resume_remaining(self):
        first = self.runner.run()
        self.assertEqual(len(first['processed']), 5)
        second = self.runner.run()
        self.assertEqual([x['task_id'] for x in second['processed']], ['DEMO-006'])
        self.assertEqual(self.runner.run()['processed'], [])

    def test_merge_keeps_same_shop_across_tasks(self):
        self.runner.run()
        state = self.runner.load_state()
        paths = [self.runner.output / state['tasks'][t['task_id']]['artifact_dir'] for t in self.c['tasks'][:5]]
        result = merge(paths, self.root / 'merged.json')
        self.assertEqual(result, {'task_count': 5, 'shop_count': 10, 'product_count': 10})
        with self.assertRaisesRegex(GateError, 'invalid_merge_inputs'):
            merge([paths[0], paths[0]], self.root / 'again.json')

    def test_failure_stops_batch_then_explicit_retry(self):
        source = self.root / self.c['tasks'][0]['input']['path']
        doc = load_json(source)
        doc['entries'].pop()
        atomic_json(source, doc)
        result = self.runner.run()
        self.assertEqual(result['processed'][0]['code'], 'pagination_gap')
        self.assertEqual(len(result['processed']), 1)
        self.assertEqual(self.runner.run()['status'], 'ATTENTION_REQUIRED')
        doc['entries'].append(entry(1, 0, True, 2, 3))
        atomic_json(source, doc)
        self.runner.retry('DEMO-001')
        self.assertEqual(len(self.runner.run()['processed']), 5)

    def test_task_change_cannot_reuse_completed_result(self):
        self.runner.run()
        self.c['tasks'][0]['search_term'] = '另一个虚构广场'
        self.save_config()
        with self.assertRaisesRegex(GateError, 'task_configuration_changed'):
            self.runner.plan()

    def test_crash_after_export_recovered(self):
        self.runner.run()
        state = self.runner.load_state()
        state['tasks']['DEMO-001']['status'] = 'RUNNING'
        self.runner.save(state)
        self.runner.run()
        self.assertEqual(self.runner.load_state()['tasks']['DEMO-001']['status'], 'COMPLETE')

    def test_artifact_tamper_detected(self):
        self.runner.run()
        record = self.runner.load_state()['tasks']['DEMO-001']
        path = self.runner.output / record['artifact_dir'] / 'shops.csv'
        path.write_text('modified', encoding='utf-8')
        with self.assertRaisesRegex(GateError, 'artifact_integrity_failure'):
            self.runner.run()

    def test_exclusive_lock(self):
        from groupbuy.common import FileLock
        with FileLock(self.runner.progress_path.with_suffix('.lock')):
            with self.assertRaisesRegex(GateError, 'workspace_locked'):
                self.runner.run()

    def test_ui_interruption_cannot_reuse_prior_proof(self):
        from tests.support import FakeDriver, frame
        self.c['tasks'][0]['ui_profile'] = {'steps': [{'action': 'type_search', 'text': '搜索商家'}]}
        self.save_config()
        class InterruptedDriver(FakeDriver):
            def wait(self, seconds):
                raise KeyboardInterrupt
        result = self.runner.ui_segment('DEMO-001', InterruptedDriver([frame()] * 6))
        self.assertEqual(result['code'], 'interrupted')
        record = self.runner.load_state()['tasks']['DEMO-001']
        self.assertEqual(record['status'], 'STOPPED')
        self.assertNotIn('ui_evidence', record)

    def test_schedule_due_and_idempotent(self):
        from datetime import datetime
        self.c['schedule']['enabled'] = True
        self.save_config()
        now = datetime.fromisoformat('2030-01-01T09:00:30+08:00')
        self.assertEqual(due_slot(self.loaded, now), '2030-01-01T09:00+08:00')
        self.assertEqual(len(tick(self.runner, now)['processed']), 5)
        self.assertEqual(tick(self.runner, now)['status'], 'ALREADY_CLAIMED')
        later = datetime.fromisoformat('2030-01-01T12:00:00+08:00')
        self.assertEqual(len(tick(self.runner, later)['processed']), 1)

    def test_schedule_disabled_and_live_gate(self):
        from datetime import datetime
        now = datetime.fromisoformat('2030-01-01T09:00:00+08:00')
        self.assertIsNone(due_slot(self.loaded, now))
        self.loaded['mode'] = 'live'
        with self.assertRaisesRegex(GateError, 'live_schedule_not_validated'):
            tick(self.runner, now)

    def test_duplicate_tasks_and_unsafe_config(self):
        self.c['tasks'][1]['task_id'] = self.c['tasks'][0]['task_id']
        with self.assertRaisesRegex(GateError, 'duplicate_or_empty_tasks'):
            self.save_config()

    def test_field_projection_cannot_drop_unit(self):
        self.c['tasks'][0]['fields']['products'] = ['task_id', 'platform', 'shop_id', 'captured_at', 'provenance_id', 'sale_price']
        with self.assertRaisesRegex(GateError, 'missing_field_semantics'):
            self.save_config()

    def test_completed_manifest_tamper_detected(self):
        self.runner.run()
        record = self.runner.load_state()['tasks']['DEMO-001']
        path = self.runner.output / record['artifact_dir'] / 'manifest.json'
        manifest = load_json(path)
        manifest['files'].pop('shops.csv')
        atomic_json(path, manifest)
        with self.assertRaisesRegex(GateError, 'artifact_integrity_failure'):
            self.runner.run()

    def test_output_on_c_rejected_on_windows(self):
        if os.name != 'nt':
            self.skipTest('Windows path policy')
        self.c['runtime']['output_dir'] = 'C:/forbidden-output'
        with self.assertRaisesRegex(GateError, 'output_requires_d_drive'):
            self.save_config()

    def test_bare_json_has_no_provenance(self):
        source = self.root / 'bare.json'
        atomic_json(source, {'data': {'list': []}})
        with self.assertRaisesRegex(GateError, 'missing_response_metadata'):
            list(read_responses(source, 'json', evidence(task())['capture_start'], evidence(task())['capture_end']))

    def test_har_time_host_and_base64_gzip(self):
        e = entry(0, 0, True)
        body = base64.b64encode(gzip.compress(json.dumps(e['response']).encode())).decode()
        har_entry = {'startedDateTime': e['captured_at'],
                     'request': {'url': 'https://m.dianping.com/searchshop.json?keyword=示例广场&start=0',
                                 'headers': [{'name': 'Cookie', 'value': 'fictional-private'}]},
                     'response': {'status': 200, 'content': {'encoding': 'base64', 'text': body}}}
        other = deepcopy(har_entry)
        other['request']['url'] = 'https://example.invalid/searchshop.json'
        old = deepcopy(har_entry)
        old['startedDateTime'] = '2029-01-01T00:00:00Z'
        source = self.root / 'input.har'
        atomic_json(source, {'log': {'entries': [har_entry, other, old]}})
        result = list(read_responses(source, 'har', evidence(task())['capture_start'], evidence(task())['capture_end']))
        self.assertEqual(len(result), 1)
        self.assertNotIn('headers', result[0]['request'])
        self.assertEqual(result[0]['response'], e['response'])

    def test_decode_nested_and_invalid(self):
        self.assertEqual(decode(gzip.compress(b'{"data":1}')), {'data': 1})
        with self.assertRaises(GateError):
            decode(b'not json')


if __name__ == '__main__':
    unittest.main()

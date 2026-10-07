"""Dianping public search schema and normal observed UI operations."""
import re
from ..common import GateError, digest, moment
from ..models import PublicProduct, Shop, row
from ..privacy import display

DINING = ('餐厅', '火锅', '烧烤', '烤肉', '烤鱼', '川菜', '湘菜', '粤菜', '东北菜',
          '茶餐厅', '咖啡', '奶茶', '饮品', '甜品', '蛋糕', '烘焙', '快餐', '小吃',
          '自助餐', '料理', '豆捞', '香锅', '炸鸡', '炸串', '牛排', '面馆', '粉店')
FOOD_RETAIL = ('食品零售', '生鲜', '超市', '便利店', '滋补品', '酸奶鲜奶', '早市', '集市')
OTHER = ('酒店', '民宿', '景点', '美容', '美发', '按摩', '摄影', '健身', '培训')
DEAL_ROOTS = {'shopDealInfos', 'deals', 'deal', 'dealInfo', 'promotions', 'coupons', 'vouchers'}


def first(obj, keys):
    for k in keys:
        if obj.get(k) not in (None, '', [], {}):
            return obj[k], k
    return None, None


def classify(category):
    text = str(category or '')
    a = any(w in text for w in DINING)
    b = any(w in text for w in FOOD_RETAIL)
    if a and b:
        return '待复核', '类别同时包含餐饮与食品零售'
    if b:
        return '食品零售', display(category)
    if a:
        return '餐饮', display(category)
    if any(w in text for w in OTHER):
        return '其他', display(category)
    return '待复核', display(category)


def product_leaves(value, path):
    if isinstance(value, dict):
        if any(k in value for k in ('dealId', 'dealID', 'productId', 'couponId', 'salePrice', 'dealGroupPrice')):
            yield value, path
        else:
            for key, child in value.items():
                yield from product_leaves(child, path + '.' + key)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from product_leaves(child, f'{path}[{i}]')


def products_in(value, path):
    if isinstance(value, dict):
        for key, child in value.items():
            p = path + '.' + key
            if key in DEAL_ROOTS:
                yield from product_leaves(child, p)
            elif isinstance(child, (dict, list)):
                yield from products_in(child, p)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from products_in(child, f'{path}[{i}]')


def cursor(v):
    if isinstance(v, bool) or v is None or not re.fullmatch(r'\d+', str(v)):
        raise GateError('invalid_page_cursor')
    return int(v)


def norm(text):
    return re.sub(r'\s+', '', str(text or ''))


class DianpingAdapter:
    platform = 'dianping'

    def validate_evidence(self, task, e):
        from ..config import task_hash
        if e.get('schema') != 'groupbuy.ui-evidence.v1' or e.get('task_id') != task['task_id'] or e.get('task_hash') != task_hash(task):
            raise GateError('evidence_task_mismatch')
        if (e.get('search_term') != task['search_term'] or e.get('target_location') != task['target_location']
                or e.get('selected_filters') != task['filters']):
            raise GateError('search_or_filter_mismatch')
        if any(e.get(k) is not True for k in ('search_confirmed', 'target_confirmed', 'filters_confirmed')):
            raise GateError('missing_search_evidence')
        if e.get('risk') or e.get('stop_reason') != 'explicit_bottom' or e.get('bottom_reached') is not True:
            raise GateError('incomplete_ui')
        if not all(re.fullmatch(r'[a-f0-9]{64}', str(e.get(k, ''))) for k in ('filter_frame_hash', 'final_frame_hash', 'list_hash')):
            raise GateError('missing_frame_evidence')
        if e.get('bottom_text') not in {'已经到底', '没有更多', '到底啦', '到底了', '暂无结果'}:
            raise GateError('missing_terminal_evidence')
        if e.get('window_id') is None or len(e.get('window_size', [])) != 2:
            raise GateError('missing_window_evidence')
        if moment(e['capture_start']) > moment(e['capture_end']):
            raise GateError('invalid_capture_window')

    def _page(self, task, entry):
        req, body = entry['request'], entry['response']
        if not isinstance(body, dict):
            return None
        data = body.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('list'), list):
            return None
        keyword = req.get('keyword', req.get('searchkeyword', req.get('query', data.get('keyword'))))
        if norm(keyword) != norm(task['search_term']):
            return None
        if body.get('code', 200) not in (0, 200, '200'):
            raise GateError('invalid_business_response')
        for k, request_k in [('category', 'category'), ('range_text', 'rangetext')]:
            if request_k in req and req[request_k] != task['filters'].get(k):
                raise GateError('response_filter_mismatch')
        if 'cityname' in req and req['cityname'] != task['target_location']['city']:
            raise GateError('response_city_mismatch')
        start = cursor(data.get('start', req.get('start', req.get('startindex'))))
        nxt = cursor(data.get('nextStartIndex'))
        if not isinstance(data.get('isEnd'), bool):
            raise GateError('missing_terminal_flag')
        return {**entry, 'start': start, 'next': nxt, 'end': data['isEnd'], 'items': data['list'],
                'business_hash': digest({'items': data['list'], 'next': nxt, 'end': data['isEnd']})}

    def select_pages(self, task, entries):
        pages = [p for e in sorted(entries, key=lambda x: moment(x['captured_at'])) if (p := self._page(task, e))]
        if not pages:
            raise GateError('no_matching_responses')
        rounds, current = [], []
        for p in pages:
            explicit_changed = current and p.get('round_id') != current[-1].get('round_id')
            new_first = current and p['start'] == 0 and (current[-1]['start'] != 0 or p['business_hash'] != current[-1]['business_hash'])
            if explicit_changed or new_first:
                rounds.append(current)
                current = []
            current.append(p)
        rounds.append(current)
        selected_round = rounds[-1]  # Never fall back to an older complete search.
        indexed = {}
        duplicates = 0
        for p in selected_round:
            if p['start'] in indexed:
                if p['business_hash'] != indexed[p['start']]['business_hash']:
                    raise GateError('conflicting_duplicate_page')
                duplicates += 1
            else:
                indexed[p['start']] = p
        selected, expected, seen = [], 0, set()
        while True:
            if expected in seen:
                raise GateError('pagination_cycle')
            seen.add(expected)
            if expected not in indexed:
                raise GateError('pagination_gap')
            p = indexed[expected]
            selected.append(p)
            if p['end']:
                break
            if p['next'] <= expected:
                raise GateError('unproven_cursor_reset')
            expected = p['next']
        if set(indexed) != seen:
            raise GateError('orphan_page')
        return selected, {'page_count': len(selected), 'duplicate_pages': duplicates,
                          'search_round_count': len(rounds), 'pagination_complete': True}

    def parse(self, task, entries, evidence):
        self.validate_evidence(task, evidence)
        pages, qa = self.select_pages(task, entries)
        shops, products, provenance = {}, {}, {}
        for p in pages:
            for i, item in enumerate(p['items']):
                if not isinstance(item, dict) or not isinstance(item.get('shopInfo'), dict):
                    continue  # Non-shop banners are not business entities.
                raw = item['shopInfo']
                sid, idfield = first(raw, ('shopUuid', 'shopId', 'shopID'))
                if sid in (None, ''):
                    raise GateError('missing_shop_identity')
                sid = str(sid)
                source_path = f'data.list[{i}].shopInfo'
                source = {'source_sha256': p['source_hash'], 'entry_index': p['entry_index'],
                          'page_start': p['start'], 'captured_at': p['captured_at'],
                          'field_path': source_path, 'field_sources': {}}
                pid = task['task_id'] + ':' + digest(source)[:24]
                flags = []
                def value(key, names):
                    v, f = first(raw, names)
                    if f:
                        source['field_sources'][key] = source_path + '.' + f
                    return display(v)
                name = value('name', ('shopName', 'name'))
                branch = value('branch_name', ('branchName',))
                if branch and norm(branch.strip('（）() ')) not in norm(name):
                    name = f'{name or ""}（{branch.strip("（）() ")}）'
                if not name:
                    flags.append('name_missing')
                category = value('category', ('categoryName', 'category', 'shopType'))
                classification, basis = classify(category)
                if classification == '待复核':
                    flags.append('classification_review')
                dist = value('distance_text', ('distanceText', 'distance'))
                meters, center, dist_source = None, None, None
                center = value('distance_center', ('distanceCenter',))
                if center and center == task['target_location'].get('center_name') and dist:
                    m = re.fullmatch(r'(?:距(?:离)?搜索中心\s*)?(\d+(?:\.\d+)?)\s*(m|米|km|公里)', dist, re.I)
                    if m:
                        meters = float(m[1]) * (1000 if m[2].lower() in {'km', '公里'} else 1)
                        dist_source = 'platform_display'
                if dist and meters is None:
                    flags.append('distance_center_unverified')
                avg = value('average_price', ('avgPrice', 'averagePrice', 'price'))
                avg_unit = value('average_price_unit', ('priceUnit', 'currency'))
                if avg and any(s in avg for s in ('¥', '￥', '元')):
                    avg_unit = 'CNY'
                if avg and not avg_unit:
                    flags.append('average_price_unit_unknown')
                shop = row(Shop(task['task_id'], 'dianping', sid, name, category, classification, basis,
                                value('rating', ('score', 'rating', 'star')), avg, avg_unit,
                                value('review_count', ('reviewCount', 'commentCount')),
                                value('address', ('address', 'shopAddress', 'fullAddress')),
                                value('business_area', ('regionName', 'businessArea')),
                                meters, dist, center, dist_source, p['captured_at'], flags, pid))
                provenance[pid] = source
                if sid in shops:
                    comparable = lambda r: {k: v for k, v in r.items() if k not in {'captured_at', 'provenance_id', 'quality_flags'}}
                    if comparable(shop) != comparable(shops[sid]):
                        raise GateError('conflicting_shop_observation')
                else:
                    shops[sid] = shop
                for deal, field_path in products_in(item, f'data.list[{i}]'):
                    ds = {'source_sha256': p['source_hash'], 'entry_index': p['entry_index'],
                          'page_start': p['start'], 'captured_at': p['captured_at'],
                          'field_path': field_path, 'field_sources': {}}
                    dp = task['task_id'] + ':' + digest(ds)[:24]
                    def dv(key, names):
                        v, f = first(deal, names)
                        if f:
                            ds['field_sources'][key] = field_path + '.' + f
                        return display(v)
                    for shop_field in ('shopUuid', 'shopId', 'shopID'):
                        if deal.get(shop_field) is not None and str(deal[shop_field]) not in {
                            str(v) for v in (raw.get('shopUuid'), raw.get('shopId'), raw.get('shopID')) if v is not None}:
                            raise GateError('product_shop_mismatch')
                    product_id = dv('product_id', ('productId', 'mtProductId', 'dealId', 'dealID', 'couponId', 'voucherId'))
                    title = dv('name', ('dealTitle', 'couponTitle', 'couponName', 'title', 'name'))
                    kind = dv('product_type', ('typeName', 'productType'))
                    if not kind and deal.get('dealType') in (1, '1', 2, '2'):
                        kind = '团购套餐' if str(deal['dealType']) == '1' else '优惠券'
                        ds['field_sources']['product_type'] = field_path + '.dealType'
                    sale = dv('sale_price', ('salePrice', 'dealGroupPrice', 'price'))
                    original = dv('original_price', ('originalPrice', 'marketPrice'))
                    unit = dv('price_unit', ('priceUnit', 'currency'))
                    if any(s in (sale or '') + (original or '') for s in ('¥', '￥', '元')):
                        unit = unit or 'CNY'
                    rule = dv('visible_rules', ('rulesText', 'useRules', 'usageRules', 'rules'))
                    if rule is None and isinstance(deal.get('useRules'), list):
                        rule = '；'.join(filter(None, (display(x) for x in deal['useRules']))) or None
                        ds['field_sources']['visible_rules'] = field_path + '.useRules'
                    product = row(PublicProduct(task['task_id'], 'dianping', sid, product_id, title, kind,
                                               sale, original, unit, dv('sales_text', ('salesText', 'soldText', 'sales')),
                                               rule, p['captured_at'], dp))
                    if not product_id:
                        shops[sid]['quality_flags'] = sorted(set(shops[sid]['quality_flags'] + ['product_id_missing']))
                    if sale and not unit:
                        shops[sid]['quality_flags'] = sorted(set(shops[sid]['quality_flags'] + ['product_price_unit_unknown']))
                    key = (sid, product_id or 'observation:' + dp)
                    if key in products:
                        a = {k: v for k, v in products[key].items() if k not in {'captured_at', 'provenance_id'}}
                        b = {k: v for k, v in product.items() if k not in {'captured_at', 'provenance_id'}}
                        if a != b:
                            raise GateError('conflicting_product_observation')
                    else:
                        products[key] = product
                    provenance[dp] = ds
        if not shops and evidence['bottom_text'] != '暂无结果':
            raise GateError('unexpected_empty_result')
        return {'schema': 'groupbuy.result.v1', 'task_id': task['task_id'], 'shops': list(shops.values()),
                'products': list(products.values()), 'provenance': provenance, 'quality': qa}

    def collect_ui(self, task, runtime, driver, prior=None):
        from .ui_flow import collect
        return collect(task, runtime, driver, prior)

    def capture_entry(self, task, entry):
        """Project a normally reported HAR entry before local persistence."""
        import base64
        import json
        from urllib.parse import urlsplit, urlunsplit, urlencode
        from ..local_input import decode, params
        from ..privacy import clean
        request = entry.get('request', {})
        url = urlsplit(request.get('url', ''))
        host = (url.hostname or '').lower()
        if url.scheme != 'https' or not any(host == h or host.endswith('.' + h)
                                           for h in ('dianping.com', 'meituan.com')):
            return None
        if url.username or url.password or 'search' not in url.path.lower():
            return None
        query = params(request)
        if norm(query.get('keyword', query.get('searchkeyword', query.get('query')))) != norm(task['search_term']):
            return None
        response = entry.get('response', {})
        if response.get('status') != 200:
            raise GateError('capture_http_failure')
        content = response.get('content', {})
        body = content.get('text', '')
        if content.get('encoding') == 'base64':
            try:
                body = base64.b64decode(body, validate=True)
            except ValueError:
                raise GateError('invalid_response_encoding') from None
        value = decode(body)
        if not isinstance(value, dict) or not isinstance(value.get('data'), dict):
            return None
        data = value['data']
        if not isinstance(data.get('list'), list):
            return None
        shop_keys = {'shopUuid', 'shopId', 'shopID', 'shopName', 'name', 'branchName',
                     'categoryName', 'category', 'shopType', 'score', 'rating', 'star',
                     'avgPrice', 'averagePrice', 'price', 'priceUnit', 'currency',
                     'reviewCount', 'commentCount', 'address', 'shopAddress', 'fullAddress',
                     'regionName', 'businessArea', 'distanceText', 'distance', 'distanceCenter'}
        product_keys = {'shopUuid', 'shopId', 'shopID', 'productId', 'mtProductId', 'dealId',
                        'dealID', 'couponId', 'voucherId', 'dealTitle', 'couponTitle', 'couponName',
                        'title', 'name', 'typeName', 'productType', 'dealType', 'salePrice',
                        'dealGroupPrice', 'price', 'originalPrice', 'marketPrice', 'priceUnit',
                        'currency', 'salesText', 'soldText', 'sales', 'rulesText', 'useRules',
                        'usageRules', 'rules'}

        def project(node, keys=None):
            if isinstance(node, dict):
                result = {}
                for key, child in node.items():
                    if keys and key in keys:
                        result[key] = clean(child)
                    elif key == 'shopInfo':
                        result[key] = project(child, shop_keys)
                    elif key in DEAL_ROOTS:
                        result[key] = project(child, product_keys)
                    elif isinstance(child, (dict, list)):
                        kept = project(child, keys)
                        if kept:
                            result[key] = kept
                return result
            if isinstance(node, list):
                return [project(child, keys) for child in node]
            return None

        projected = {'code': value.get('code', 200), 'data': {
            **{k: data[k] for k in ('start', 'nextStartIndex', 'isEnd', 'keyword') if k in data},
            'list': project(data['list'])}}
        # Supplied city/filter differences remain so the parser rejects conflicts.
        # No original headers, cookies, request bodies, coordinates or UI decoration.
        safe_url = urlunsplit(('https', host, url.path, urlencode(query), ''))
        return {'startedDateTime': entry['startedDateTime'],
                'request': {'method': request.get('method', 'GET'), 'url': safe_url, 'headers': [], 'cookies': []},
                'response': {'status': 200, 'headers': [], 'cookies': [],
                             'content': {'mimeType': 'application/json',
                                         'text': json.dumps(clean(projected), ensure_ascii=False)}}}

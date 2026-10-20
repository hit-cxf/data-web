"""Offline trace index and paginated waterfall API."""
import hashlib
import json
import math
import conversation

PATHS = ('trace', 'span', 'parent', 'name', 'start', 'duration', 'service', 'host', 'status',
         'kind', 'status_message', 'resource', 'attributes', 'event_time', 'event_name', 'event_attributes')
DEFAULTS = {'trace': ['traceId'], 'span': ['spanId'], 'parent': ['parentSpanID'],
            'name': ['spanName'], 'start': ['start'], 'duration': ['duration'],
            'service': ['serviceName'], 'host': ['resourceAttributes', 'host.name'],
            'status': ['statusCode'], 'kind': ['spanKind'], 'status_message': ['statusMessage'], 'resource': ['resourceAttributes'], 'attributes': ['spanAttributes'],
            'event_time': ['eventsTs'], 'event_name': ['eventsName'], 'event_attributes': ['eventsAttributes']}


def initialize(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS trace_file_cache (
            config_id TEXT NOT NULL,file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            PRIMARY KEY(config_id,file_id));
        CREATE TABLE IF NOT EXISTS trace_spans (
            config_id TEXT NOT NULL,record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            trace_key TEXT NOT NULL,trace_id TEXT,span_id TEXT,parent_id TEXT,name TEXT,service TEXT,host TEXT,
            stamp REAL,duration REAL,error INTEGER NOT NULL,
            PRIMARY KEY(config_id,record_id));
        CREATE INDEX IF NOT EXISTS trace_key_idx ON trace_spans(config_id,trace_key);
        CREATE INDEX IF NOT EXISTS trace_record_idx ON trace_spans(record_id);
    ''')


def config(value, b):
    if not isinstance(value, dict):
        raise b.APIError('字段映射必须为对象。')
    result = {}
    for key in PATHS:
        path = value.get(key, DEFAULTS[key])
        if path is not None and (not isinstance(path, list) or not path or len(path) > 64 or
                                 any(not isinstance(x, str) or not x for x in path)):
            raise b.APIError('字段路径无效：' + key)
        result[key] = path
    for key in ('time_unit', 'duration_unit'):
        unit = value.get(key, 'auto' if key == 'time_unit' else 'ns')
        if unit not in (('auto', 's', 'ms', 'us', 'ns') if key == 'time_unit' else ('s', 'ms', 'us', 'ns')):
            raise b.APIError('无效时间单位。')
        result[key] = unit
    return result


def first(record, path, b):
    return next(b.path_values(record, path), None) if path else None


def string(value):
    return None if value is None else (json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value))


def hrtime(value):
    if not isinstance(value, list) or len(value) != 2:
        return None
    if any(type(part) is not int for part in value) or value[0] < 0 or not 0 <= value[1] < 1000000000:
        return None
    try:
        result = value[0] + value[1] / 1000000000
        return result if math.isfinite(result) else None
    except OverflowError:
        return None


def timestamp(value, unit):
    return hrtime(value) if isinstance(value, list) else conversation.timestamp(value, unit)


def duration(value, unit):
    if isinstance(value, list):
        return hrtime(value)
    try:
        if isinstance(value, bool):
            return None
        number = float(value) / {'s': 1, 'ms': 1000, 'us': 1e6, 'ns': 1e9}[unit]
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def is_error(value):
    if isinstance(value, dict):
        value = value.get('code')
    return int(str(value).strip().casefold() in ('2', 'error', 'status_code_error', 'statuscode.error'))


def ensure_index(scope, mapping, b):
    encoded = b.compact_json({'normalization_version': 2, **{k: mapping[k] for k in ('trace','span','parent','name','start','duration','service','host','status','time_unit','duration_unit')}})
    identity = hashlib.sha256(encoded.encode()).hexdigest()
    with b.SCAN_LOCK:
        conn = b.connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            b.check_scan(conn, scope['scan_id'])
            files = conn.execute('SELECT f.id FROM files f' + scope['file_where'], scope['file_arguments']).fetchall()
            for file in files:
                if conn.execute('SELECT 1 FROM trace_file_cache WHERE config_id=? AND file_id=?', (identity, file['id'])).fetchone():
                    continue
                batch = []
                for row in conn.execute('SELECT id,document FROM records WHERE file_id=?', (file['id'],)):
                    record = json.loads(row['document'])
                    values = {k: first(record, mapping[k], b) for k in ('trace','span','parent','name','start','duration','service','host','status')}
                    trace_id = string(values['trace']) or None
                    key = b.compact_json(['trace', trace_id] if trace_id else ['record', str(row['id'])])
                    batch.append((identity, row['id'], key, trace_id, string(values['span']), string(values['parent']),
                                  string(values['name']), string(values['service']), string(values['host']),
                                  timestamp(values['start'], mapping['time_unit']),
                                  duration(values['duration'], mapping['duration_unit']), is_error(values['status'])))
                    if len(batch) >= 2000:
                        conn.executemany('INSERT INTO trace_spans VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', batch)
                        batch.clear()
                if batch:
                    conn.executemany('INSERT INTO trace_spans VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', batch)
                conn.execute('INSERT INTO trace_file_cache VALUES (?,?)', (identity, file['id']))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return identity


def prepare(payload, b, full=False):
    mapping = config(payload.get('mapping', {}), b)
    if full:
        payload = {**payload, 'field_filters': [], 'filters': {}}
    scope = b.query_scope(payload)
    identity = ensure_index(scope, mapping, b)
    if full:
        scope['where'] += (' AND ' if scope['where'] else ' WHERE ') + 'r.id IN (SELECT record_id FROM trace_spans WHERE config_id=? AND trace_key=?)'
        scope['arguments'] += [identity, payload['trace_key']]
    cte = b.matched_cte(scope) + ''' , entries AS (
        SELECT t.*,q.copies FROM selected q CROSS JOIN trace_spans t INDEXED BY trace_record_idx
        WHERE t.record_id=q.id AND t.config_id=?) '''
    return scope, cte, scope['arguments'] + [identity]


def list_traces(payload, b):
    page, size = b.page_arguments(payload)
    scope, cte, args = prepare(payload, b)
    search = payload.get('search', '')
    if not isinstance(search, str) or len(search) > 1000:
        raise b.APIError('搜索关键词无效。')
    cte += ''', groups AS (SELECT trace_key,trace_id,COUNT(*) AS count,SUM(copies) AS raw_count,
        MIN(stamp) AS first,MAX(stamp+COALESCE(duration,0)) AS last,SUM(error) AS errors,
        COUNT(DISTINCT service) AS services,COUNT(DISTINCT host) AS hosts,
        COALESCE(MIN(CASE WHEN parent_id IS NULL OR parent_id='' OR parent_id='0000000000000000' THEN name END),MIN(name),'未命名操作') AS name,
        SUM(CASE WHEN stamp IS NULL THEN 1 ELSE 0 END) AS missing_time
        FROM entries GROUP BY trace_key), filtered AS (SELECT * FROM groups'''
    if search:
        cte += ''' WHERE trace_key IN (SELECT trace_key FROM entries WHERE
            instr(lower(COALESCE(trace_id,'')),lower(?))>0 OR instr(lower(COALESCE(name,'')),lower(?))>0
            OR instr(lower(COALESCE(service,'')),lower(?))>0 OR instr(lower(COALESCE(host,'')),lower(?))>0)'''
        args += [search] * 4
    cte += ') '
    conn = b.connect()
    try:
        conn.execute('BEGIN')
        b.check_scan(conn, scope['scan_id'])
        stats = dict(conn.execute(cte + '''SELECT COUNT(*) AS traces,COALESCE(SUM(count),0) AS spans,
            COALESCE(SUM(raw_count-count),0) AS duplicates,COALESCE(SUM(errors),0) AS errors,
            MAX(last-first) AS longest FROM filtered''', args).fetchone())
        total = stats['traces']
        pages = math.ceil(total / size)
        page = min(page, max(1, pages))
        rows = conn.execute(cte + 'SELECT * FROM filtered ORDER BY first IS NULL,first DESC,trace_key LIMIT ? OFFSET ?', args + [size, (page-1)*size]).fetchall()
        return {'items': [dict(row) for row in rows], 'stats': stats, 'total': total, 'page': page, 'pages': pages}
    finally:
        conn.close()


def waterfall(payload, b):
    page, size = b.page_arguments(payload, 200)
    key = payload.get('trace_key')
    if not isinstance(key, str) or not key or len(key) > 10000:
        raise b.APIError('请选择链路。')
    scope, cte, args = prepare(payload, b, full=True)
    conn = b.connect()
    try:
        conn.execute('BEGIN')
        b.check_scan(conn, scope['scan_id'])
        rows = [dict(row) for row in conn.execute(cte + 'SELECT * FROM entries WHERE trace_key=? ORDER BY stamp IS NULL,stamp,record_id', args + [key])]
    finally:
        conn.close()
    # Resolve the whole lightweight trace before pagination so children are ordered with their parent.
    by_id, duplicates = {}, set()
    for row in rows:
        sid = row['span_id']
        if sid:
            if sid in by_id:
                duplicates.add(sid)
            else:
                by_id[sid] = row['record_id']
    children, roots, missing_parent = {}, [], 0
    for row in rows:
        parent = row['parent_id']
        pid = by_id.get(parent) if parent and parent not in duplicates else None
        row['parent_record'] = pid
        if pid is None or pid == row['record_id']:
            roots.append(row)
            if parent and parent != '0000000000000000':
                missing_parent += 1
        else:
            children.setdefault(pid, []).append(row)
    ordered, visited = [], set()
    def walk(root):
        stack = [(root, 0)]
        while stack:
            row, depth = stack.pop()
            if row['record_id'] in visited:
                continue
            visited.add(row['record_id'])
            row['depth'] = depth
            row['has_children'] = bool(children.get(row['record_id']))
            ordered.append(row)
            stack.extend((child, depth+1) for child in reversed(children.get(row['record_id'], [])))
    for row in roots:
        walk(row)
    cycles = 0
    for row in rows:
        if row['record_id'] not in visited:
            cycles += 1
            walk(row)
    starts = [r['stamp'] for r in rows if r['stamp'] is not None]
    ends = [r['stamp'] + (r['duration'] or 0) for r in rows if r['stamp'] is not None]
    total = len(rows)
    pages = math.ceil(total / size)
    page = min(page, max(1, pages))
    return {'items': ordered[(page-1)*size:page*size], 'total': total, 'page': page, 'pages': pages,
            'first': min(starts) if starts else None, 'last': max(ends) if ends else None,
            'services': sorted(set(r['service'] for r in rows if r['service'])),
            'errors': sum(r['error'] for r in rows), 'missing_span_id': sum(not r['span_id'] for r in rows),
            'missing_time': sum(r['stamp'] is None for r in rows),
            'missing_duration': sum(r['duration'] is None for r in rows),
            'missing_parent': missing_parent, 'duplicate_span_ids': len(duplicates), 'cycles': cycles}

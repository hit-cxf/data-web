"""User -> thread -> trace -> span browser, with a bounded SQLite membership cache."""
import hashlib
import json
import math
import time
import traceview


def initialize(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS browse_cache (
            id TEXT PRIMARY KEY,scan_id TEXT NOT NULL,config_id TEXT NOT NULL,created REAL NOT NULL,stats TEXT);
        CREATE TABLE IF NOT EXISTS browse_rows (
            cache_id TEXT NOT NULL REFERENCES browse_cache(id) ON DELETE CASCADE,
            record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            user_key TEXT NOT NULL,thread_key TEXT NOT NULL,trace_key TEXT NOT NULL,
            thread_id TEXT,inferred INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(cache_id,record_id));
        CREATE INDEX IF NOT EXISTS browse_user_idx ON browse_rows(cache_id,user_key,thread_key,trace_key);
        CREATE INDEX IF NOT EXISTS browse_trace_idx ON browse_rows(cache_id,user_key,thread_key,trace_key,record_id);
        CREATE TABLE IF NOT EXISTS browse_groups (
            cache_id TEXT NOT NULL REFERENCES browse_cache(id) ON DELETE CASCADE,
            level TEXT NOT NULL,user_key TEXT NOT NULL,thread_key TEXT NOT NULL,key TEXT NOT NULL,
            label TEXT NOT NULL,spans INTEGER,traces INTEGER,sessions INTEGER,errors INTEGER,
            first REAL,last REAL,total_duration REAL,hosts INTEGER,services INTEGER,unassigned INTEGER,
            PRIMARY KEY(cache_id,level,user_key,thread_key,key));
        CREATE INDEX IF NOT EXISTS browse_group_time_idx ON browse_groups(cache_id,level,user_key,thread_key,first DESC,key);
    ''')


def joined():
    return ''' FROM browse_rows r JOIN trace_spans t ON t.record_id=r.record_id AND t.config_id=?
               WHERE r.cache_id=?'''


def prepare(payload, b):
    scope = b.query_scope(payload)
    mapping = traceview.config(payload.get('mapping', {}), b)
    config_id = traceview.ensure_index(scope, mapping, b)
    # Full trace context is kept within the selected directory time range.
    whole = b.query_scope({**payload, 'field_filters': [], 'filters': {}})
    search = payload.get('search', '')
    minimum = payload.get('min_spans', 1)
    if not isinstance(search, str) or len(search) > 1000:
        raise b.APIError('搜索关键词无效。')
    if type(minimum) is not int or not 1 <= minimum <= 10000000:
        raise b.APIError('最少 Span 数无效。')
    key = hashlib.sha256(b.compact_json([3,scope['scan_id'],config_id,scope['where'],scope['arguments'],
                                        whole['where'],whole['arguments'],scope['deduplicate'],search,minimum]).encode()).hexdigest()
    with b.SCAN_LOCK:
        conn = b.connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            b.check_scan(conn,scope['scan_id'])
            if conn.execute('SELECT 1 FROM browse_cache WHERE id=?',(key,)).fetchone():
                return key, True
            conn.execute('DELETE FROM browse_cache WHERE scan_id<>?',(scope['scan_id'],))
            conn.execute('INSERT INTO browse_cache VALUES (?,?,?,?,NULL)',(key,scope['scan_id'],config_id,time.time()))
            # Give the filtered and unfiltered CTEs separate names before combining them.
            full = b.matched_cte(whole).replace('matched','all_matched').replace('selected','all_selected')
            hit = b.matched_cte(scope).replace('WITH matched',', matched').strip()
            cte = full + hit + ''' , base AS (
                SELECT t.*,q.copies FROM all_selected q CROSS JOIN trace_spans t INDEXED BY trace_record_config_idx
                WHERE t.record_id=q.id AND t.config_id=?),
                owners AS (SELECT user_key,trace_key,COUNT(DISTINCT thread_id) AS n,MAX(thread_id) AS thread_id,
                    MAX(CASE WHEN thread_id IS NOT NULL THEN thread_key END) AS thread_key
                    FROM base GROUP BY user_key,trace_key),
                resolved AS (SELECT b.*,CASE WHEN b.thread_id IS NULL AND o.n=1 THEN o.thread_key ELSE b.thread_key END AS resolved_key,
                    CASE WHEN b.thread_id IS NULL AND o.n=1 THEN o.thread_id ELSE b.thread_id END AS resolved_id,
                    CASE WHEN b.thread_id IS NULL AND o.n=1 THEN 1 ELSE 0 END AS inferred
                    FROM base b JOIN owners o ON o.user_key=b.user_key AND o.trace_key=b.trace_key),
                hits AS (SELECT DISTINCT r.user_key,r.resolved_key,r.trace_key FROM resolved r WHERE r.record_id IN (SELECT id FROM selected)'''
            args = whole['arguments'] + scope['arguments'] + [config_id]
            if search:
                cte += ''' AND (instr(lower(COALESCE(r.user_id,'')),lower(?))>0
                    OR instr(lower(COALESCE(r.host,'')),lower(?))>0 OR instr(lower(COALESCE(r.resolved_id,'')),lower(?))>0
                    OR instr(lower(COALESCE(r.trace_id,'')),lower(?))>0 OR instr(lower(COALESCE(r.name,'')),lower(?))>0
                    OR instr(lower(COALESCE(r.service,'')),lower(?))>0)'''
                args += [search]*6
            cte += '''), eligible AS (SELECT r.* FROM resolved r JOIN hits h ON h.user_key=r.user_key
                       AND h.resolved_key=r.resolved_key AND h.trace_key=r.trace_key),
                sizes AS (SELECT user_key,resolved_key,trace_key FROM eligible GROUP BY user_key,resolved_key,trace_key HAVING COUNT(*)>=?)
                INSERT INTO browse_rows SELECT ?,r.record_id,r.user_key,r.resolved_key,r.trace_key,r.resolved_id,r.inferred
                    FROM eligible r JOIN sizes s ON s.user_key=r.user_key AND s.resolved_key=r.resolved_key AND s.trace_key=r.trace_key'''
            conn.execute(cte,args+[minimum,key])
            common = '''COUNT(*),COUNT(DISTINCT r.trace_key),COUNT(DISTINCT CASE WHEN r.thread_id IS NOT NULL THEN r.thread_key END),
                        SUM(t.error),MIN(t.stamp),MAX(t.stamp+COALESCE(t.duration,0)),SUM(t.duration),
                        COUNT(DISTINCT t.host),COUNT(DISTINCT t.service),SUM(CASE WHEN r.thread_id IS NULL THEN 1 ELSE 0 END)'''
            conn.execute('''INSERT INTO browse_groups SELECT ?,'users',r.user_key,'',r.user_key,
                         COALESCE(MAX(t.user_id),MAX(t.host),'未识别用户'),'''+common+joined()+' GROUP BY r.user_key',
                         [key,config_id,key])
            conn.execute('''INSERT INTO browse_groups SELECT ?,'sessions',r.user_key,r.thread_key,r.thread_key,
                         COALESCE(MAX(r.thread_id),'未关联会话'),'''+common+joined()+' GROUP BY r.user_key,r.thread_key',
                         [key,config_id,key])
            conn.execute('''INSERT INTO browse_groups SELECT ?,'traces',r.user_key,r.thread_key,r.trace_key,
                         COALESCE(MIN(CASE WHEN t.parent_id IS NULL OR t.parent_id='' OR t.parent_id='0000000000000000' THEN t.name END),MIN(t.name),'未命名操作'),'''+common+joined()+
                         ' GROUP BY r.user_key,r.thread_key,r.trace_key',[key,config_id,key])
            stats = dict(conn.execute('''SELECT COUNT(DISTINCT r.user_key) AS users,
                COUNT(DISTINCT CASE WHEN r.thread_id IS NOT NULL THEN r.user_key||char(0)||r.thread_key END) AS sessions,
                COUNT(DISTINCT r.user_key||char(0)||r.thread_key||char(0)||r.trace_key) AS traces,
                COUNT(*) AS spans,COALESCE(SUM(t.error),0) AS errors,
                COALESCE(SUM(r.inferred),0) AS inferred,
                COALESCE(SUM(CASE WHEN r.thread_id IS NULL THEN 1 ELSE 0 END),0) AS unassigned'''+joined(),[config_id,key]).fetchone())
            conn.execute('UPDATE browse_cache SET stats=? WHERE id=?',(b.compact_json(stats),key))
            conn.execute('''DELETE FROM browse_cache WHERE id IN
                (SELECT id FROM browse_cache ORDER BY created DESC,id DESC LIMIT -1 OFFSET 3)''')
            conn.commit()
            return key, False
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def query(payload,b):
    started=time.perf_counter()
    level=payload.get('level','users')
    if level not in ('users','sessions','traces','spans','user_stats'):
        raise b.APIError('浏览层级无效。')
    page,size=b.page_arguments(payload,50)
    key=payload.get('cache_id')
    hit=True
    if key is None:
        key,hit=prepare(payload,b)
    if not isinstance(key,str) or len(key)>100:
        raise b.APIError('浏览缓存标识无效。')
    sort=payload.get('sort','recent')
    if sort not in ('recent','spans_desc','duration_desc'):
        raise b.APIError('排序方式无效。')
    conn=b.connect()
    try:
        conn.execute('BEGIN')
        header=conn.execute('SELECT * FROM browse_cache WHERE id=?',(key,)).fetchone()
        if not header:
            raise b.APIError('浏览缓存已更新，请刷新列表。',409)
        b.check_scan(conn,header['scan_id'])
        user,thread,trace=payload.get('user_key'),payload.get('thread_key'),payload.get('trace_key')
        for name,value in [('user_key',user),('thread_key',thread),('trace_key',trace)]:
            if value is not None and (not isinstance(value,str) or len(value)>10000):
                raise b.APIError(name+' 无效。')
        if level!='users' and user is None:
            raise b.APIError('请先选择用户。')
        if level in ('traces','spans') and thread is None:
            raise b.APIError('请先选择会话。')
        if level=='spans' and trace is None:
            raise b.APIError('请先选择链路。')
        response={'cache_id':key,'cache_hit':hit,'stats':json.loads(header['stats'])}
        if level=='user_stats':
            record=conn.execute("SELECT * FROM browse_groups WHERE cache_id=? AND level='users' AND key=?",(key,user)).fetchone()
            if not record:
                raise b.APIError('用户不在当前筛选结果中。',404)
            response['user']=dict(record)
            for output,column in [('hosts','t.host'),('services','t.service'),('operations','t.name')]:
                rows=conn.execute('SELECT '+column+' AS name,COUNT(*) AS spans,SUM(t.error) AS errors'+joined()+
                    ' AND r.user_key=? AND '+column+' IS NOT NULL GROUP BY '+column+' ORDER BY spans DESC,name LIMIT 20',
                    [header['config_id'],key,user]).fetchall()
                response[output]=[dict(row) for row in rows]
        elif level=='spans':
            clause=joined()+' AND r.user_key=? AND r.thread_key=? AND r.trace_key=?'
            args=[header['config_id'],key,user,thread,trace]
            total=conn.execute('SELECT COUNT(*)'+clause,args).fetchone()[0]
            pages=math.ceil(total/size);page=min(page,max(pages,1))
            rows=conn.execute('SELECT t.*,r.inferred'+clause+' ORDER BY t.stamp IS NULL,t.stamp,t.record_id LIMIT ? OFFSET ?',args+[size,(page-1)*size]).fetchall()
            response.update(items=[dict(row) for row in rows],total=total,page=page,pages=pages)
        else:
            clause=' WHERE cache_id=? AND level=?';args=[key,level]
            if user is not None:
                clause+=' AND user_key=?';args.append(user)
            if thread is not None:
                clause+=' AND thread_key=?';args.append(thread)
            total=conn.execute('SELECT COUNT(*) FROM browse_groups'+clause,args).fetchone()[0]
            pages=math.ceil(total/size);page=min(page,max(pages,1))
            order={'recent':'first DESC,key','spans_desc':'spans DESC,first DESC,key','duration_desc':'(last-first) DESC,key'}[sort]
            if level=='sessions':
                order='(sessions=0) ASC,'+order
            rows=conn.execute('SELECT * FROM browse_groups'+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',args+[size,(page-1)*size]).fetchall()
            response.update(items=[dict(row) for row in rows],total=total,page=page,pages=pages)
        response['elapsed_ms']=round((time.perf_counter()-started)*1000,1)
        return response
    finally:
        conn.close()

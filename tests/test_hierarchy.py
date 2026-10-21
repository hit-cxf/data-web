import json
import tempfile
import unittest
from pathlib import Path
import server
import hierarchy


class HierarchyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old_cache=server.CACHE_DIR
        server.CACHE_DIR=Path(self.tmp.name)/'cache';server.initialize_database()
        self.root=Path(self.tmp.name)/'data';self.file=self.root/'20'/'10'/'00'/'a.json';self.file.parent.mkdir(parents=True)
        def record(user,thread,trace,span,time):
            return {'traceId':trace,'spanId':span,'spanName':'operation-'+span,'start':time,'duration':1000000,
                    'resourceAttributes':{'user.id':user,'host.name':'host-'+user},
                    'spanAttributes':{'thread.id':thread} if thread else {}}
        self.records=[record('A','same','t1','a1',1000),record('A',None,'t1','a2',1100),
                      record('A','same','t2','a3',2000),record('A',None,'dns','d1',3000),
                      record('B','same','t1','b1',4000)]
        self.write(self.records*3)
    def tearDown(self):
        server.CACHE_DIR=self.old_cache;self.tmp.cleanup()
    def write(self,records):
        self.file.write_text(json.dumps(records));scan=server.scan_data({'root':str(self.root)})
        self.payload={'scan_id':scan['scan_id'],'mapping':{'time_unit':'ms','duration_unit':'ns'},'deduplicate':True}
    def test_four_levels_user_isolation_and_inheritance(self):
        users=hierarchy.query(self.payload,server)
        self.assertEqual(users['stats']['users'],2);self.assertEqual(users['stats']['sessions'],2)
        self.assertEqual(users['stats']['spans'],5);self.assertEqual(users['stats']['inferred'],1)
        a=next(x for x in users['items'] if x['label']=='A');key=users['cache_id']
        p={'cache_id':key,'user_key':a['key']}
        sessions=hierarchy.query({**p,'level':'sessions'},server)
        self.assertEqual(sessions['total'],2)
        session=next(x for x in sessions['items'] if x['label']=='same')
        traces=hierarchy.query({**p,'level':'traces','thread_key':session['key']},server)
        self.assertEqual(traces['total'],2)
        t1=next(x for x in traces['items'] if json.loads(x['key'])[1]=='t1')
        calls=hierarchy.query({**p,'level':'spans','thread_key':session['key'],'trace_key':t1['key']},server)
        self.assertEqual([x['span_id'] for x in calls['items']],['a1','a2'])
        stats=hierarchy.query({**p,'level':'user_stats'},server)
        self.assertEqual(stats['user']['spans'],4);self.assertEqual(stats['user']['sessions'],1)
        self.assertEqual(stats['user']['traces'],3)
        self.assertTrue(hierarchy.query(self.payload,server)['cache_hit'])
    def test_filter_preserves_trace_context_and_missing_user_fallback(self):
        filtered=hierarchy.query({**self.payload,'field_filters':[{'path':['spanId'],'operator':'equals','value':'a1'}]},server)
        self.assertEqual(filtered['stats']['spans'],2)
        self.assertEqual(filtered['stats']['users'],1)
        self.write([{'traceId':'x','spanId':'y','resourceAttributes':{'host.name':'machine'},'spanAttributes':{'thread.id':'one'}}])
        result=hierarchy.query(self.payload,server)
        self.assertEqual(json.loads(result['items'][0]['key']),['host','machine'])
    def test_ambiguous_thread_does_not_assign_missing_records(self):
        self.records[2]['traceId']='t1';self.records[2]['spanAttributes']['thread.id']='other'
        self.write(self.records)
        result=hierarchy.query(self.payload,server)
        self.assertEqual(result['stats']['inferred'],0)
        self.assertEqual(result['stats']['unassigned'],2)
    def test_pagination_search_and_rescan(self):
        result=hierarchy.query({**self.payload,'search':'a1','page_size':1},server)
        self.assertEqual(result['stats']['spans'],2)
        old=result['cache_id'];self.write(self.records[:1])
        with self.assertRaises(server.APIError):hierarchy.query({'cache_id':old},server)
        self.assertEqual(hierarchy.query(self.payload,server)['stats']['spans'],1)

    def test_large_membership_does_not_scan_selected_rows_for_each_span(self):
        rows=[{'traceId':'t'+str(i//3),'spanId':str(i),'spanName':'call','start':1000+i,
               'resourceAttributes':{'user.id':'u'},'spanAttributes':{'thread.id':'s'+str(i//30)}} for i in range(3000)]
        self.write(rows)
        original=server.connect
        def bounded():
            conn=original();steps=[0]
            def progress():
                steps[0]+=1
                return int(steps[0]>12000)
            conn.set_progress_handler(progress,1000)
            return conn
        server.connect=bounded
        try:
            result=hierarchy.query(self.payload,server)
            self.assertEqual(result['stats']['spans'],3000)
            self.assertEqual(result['stats']['sessions'],100)
        finally:
            server.connect=original

"""Trace aggregation tests with isolated synthetic files and cache."""
import json
import tempfile
import unittest
from pathlib import Path
import server
import traceview


class TraceViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cache = server.CACHE_DIR
        server.CACHE_DIR = Path(self.tmp.name) / 'cache'
        server.initialize_database()
        self.root = Path(self.tmp.name) / 'data'
        self.file = self.root / '20' / '10' / '00' / 'a.json'
        self.file.parent.mkdir(parents=True)
        self.records = [
            {'traceId':'t1','spanId':'root','parentSpanID':'','spanName':'root', 'start':1000,'duration':2000,'serviceName':'a','statusCode':1},
            {'traceId':'t1','spanId':'child','parentSpanID':'root','spanName':'tool','start':1100,'duration':100,'serviceName':'b','statusCode':2,'resourceAttributes':{'host.name':'HOST-A'},'spanAttributes':{'input':'hello'}},
            {'traceId':'t2','spanId':'other','parentSpanID':'missing','spanName':'other','start':2000,'duration':100,'serviceName':'a','statusCode':1},
        ]
        self.write(self.records)

    def tearDown(self):
        server.CACHE_DIR = self.old_cache
        self.tmp.cleanup()

    def write(self, records):
        self.file.write_text(json.dumps(records))
        self.scan = server.scan_data({'root':str(self.root)})
        self.payload = {'scan_id':self.scan['scan_id'],'mapping':{'time_unit':'ms','duration_unit':'ms'},'deduplicate':True}

    def test_grouping_dedup_and_full_context_after_filter(self):
        self.write(self.records * 3)
        p = {**self.payload,'field_filters':[{'path':['resourceAttributes','host.name'],'operator':'equals','value':'HOST-A'}]}
        listed = traceview.list_traces(p,server)
        self.assertEqual(listed['total'],1)
        self.assertEqual(listed['stats']['spans'],1)
        self.assertEqual(listed['stats']['duplicates'],2)
        selected = traceview.waterfall({**p,'trace_key':listed['items'][0]['trace_key']},server)
        self.assertEqual(selected['total'],2)
        self.assertEqual([s['depth'] for s in selected['items']],[0,1])
        self.assertEqual([s['span_id'] for s in selected['items']],['root','child'])
        self.assertEqual(selected['first'],1)
        self.assertEqual(selected['last'],3)
        self.assertEqual(selected['errors'],1)
        self.assertEqual(selected['items'][1]['copies'],3)
        raw = server.record_details({'scan_id':self.scan['scan_id'],'id':str(selected['items'][1]['record_id'])})
        self.assertEqual(raw['record']['spanAttributes']['input'],'hello')
        self.assertEqual(raw['total'],3)
        self.assertEqual(json.loads(raw['raw_json']),raw['record'])

    def test_missing_ids_invalid_time_and_cycles_are_preserved(self):
        self.write([
            {'traceId':'broken','spanId':'a','parentSpanID':'b','start':'bad','duration':-1},
            {'traceId':'broken','spanId':'b','parentSpanID':'a','start':2000,'duration':10},
            {'traceId':'broken','spanName':'no id','start':2100,'duration':20},
            {'spanName':'no trace 1'}, {'spanName':'no trace 2'}])
        result=traceview.list_traces(self.payload,server)
        self.assertEqual(result['total'],3)
        chosen=next(x for x in result['items'] if x['trace_id']=='broken')
        spans=traceview.waterfall({**self.payload,'trace_key':chosen['trace_key'],'page_size':1},server)
        self.assertEqual(spans['total'],3)
        self.assertEqual(spans['pages'],3)
        self.assertEqual(spans['missing_span_id'],1)
        self.assertEqual(spans['missing_time'],1)
        self.assertEqual(spans['missing_duration'],1)
        self.assertEqual(spans['cycles'],1)
        ids=[]
        for page in range(1,4):
            ids += [x['record_id'] for x in traceview.waterfall({**self.payload,'trace_key':chosen['trace_key'],'page_size':1,'page':page},server)['items']]
        self.assertEqual(len(set(ids)),3)

    def test_directory_range_and_custom_nested_mapping(self):
        self.write([{'nested':r} for r in self.records])
        mapping={k:['nested']+v for k,v in traceview.DEFAULTS.items()}
        mapping.update(time_unit='ms',duration_unit='ms')
        p={**self.payload,'mapping':mapping,'time_from':[20,10,0],'time_to':[20,10,0]}
        result=traceview.list_traces(p,server)
        self.assertEqual(result['total'],2)
        self.assertEqual(traceview.list_traces({**p,'search':'HOST-A'},server)['total'],1)
        self.assertEqual(traceview.list_traces({**p,'time_from':[20,10,1],'time_to':None},server)['total'],0)

    def test_rescan_invalidates_trace_index(self):
        first=traceview.list_traces(self.payload,server)
        self.assertEqual(first['total'],2)
        self.write([{'traceId':'new','spanId':'fresh','spanName':'updated','start':9999,'duration':10}])
        result=traceview.list_traces(self.payload,server)
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['trace_id'],'new')

    def test_duplicate_span_id_does_not_fabricate_parent(self):
        self.write([{'traceId':'t','spanId':'x','start':1},{'traceId':'t','spanId':'x','start':2},
                    {'traceId':'t','spanId':'z','parentSpanID':'x','start':3}])
        result=traceview.list_traces(self.payload,server)
        spans=traceview.waterfall({**self.payload,'trace_key':result['items'][0]['trace_key']},server)
        self.assertEqual(spans['duplicate_span_ids'],1)
        self.assertEqual(spans['missing_parent'],1)
        self.assertEqual([s['depth'] for s in spans['items']],[0,0,0])

    def test_sdk_high_resolution_time_and_structured_status(self):
        self.write([{'traceId':'sdk','spanId':'x','start':[1700000000,500000000],
                     'duration':[1,250000000],'statusCode':{'code':2}}])
        result=traceview.list_traces(self.payload,server)
        self.assertEqual(result['stats']['errors'],1)
        spans=traceview.waterfall({**self.payload,'trace_key':result['items'][0]['trace_key']},server)
        self.assertEqual(spans['items'][0]['stamp'],1700000000.5)
        self.assertEqual(spans['items'][0]['duration'],1.25)
        self.assertEqual(traceview.duration([1,1000000000],'ns'),None)

    def test_dns_diagnostics_multi_span_filter_and_cached_paging(self):
        self.write(self.records + [
            {'traceId':'dns1','spanId':'d1','spanName':'dns.lookup','start':3000},
            {'traceId':'dns2','spanId':'d2','spanName':'dns.lookup','start':4000}])
        first=traceview.list_traces(self.payload,server)
        self.assertEqual(first['items'][0]['name'],'dns.lookup')
        self.assertFalse(first['cache_hit'])
        self.assertEqual(first['diagnostics']['dns_single_traces'],2)
        self.assertEqual(first['diagnostics']['single_traces'],3)
        calls=[]
        original_connect=server.connect
        def instrumented():
            conn=original_connect()
            conn.set_trace_callback(calls.append)
            return conn
        server.connect=instrumented
        try:
            multi=traceview.list_traces({**self.payload,'min_spans':2,'sort':'spans_desc'},server)
        finally:
            server.connect=original_connect
        self.assertTrue(multi['cache_hit'])
        self.assertEqual(multi['total'],1)
        self.assertEqual(multi['items'][0]['trace_id'],'t1')
        self.assertFalse(any('FROM selected q CROSS JOIN trace_spans' in sql for sql in calls))
        self.assertEqual(traceview.list_traces({**self.payload,'search':'dns'},server)['total'],2)
        self.assertEqual(traceview.list_traces({**self.payload,'search':'dns','min_spans':2},server)['total'],0)

    def test_mapping_and_request_validation(self):
        for value in [0, True, '2']:
            with self.assertRaises(server.APIError):
                traceview.list_traces({**self.payload,'min_spans':value},server)
        with self.assertRaises(server.APIError):
            traceview.list_traces({**self.payload,'sort':'unknown'},server)
        for mapping in [{'duration_unit':'auto'},{'span':'spanId'},{'time_unit':'foo'}]:
            with self.assertRaises(server.APIError):
                traceview.list_traces({**self.payload,'mapping':mapping},server)
        with self.assertRaises(server.APIError):
            traceview.waterfall(self.payload,server)


if __name__=='__main__':
    unittest.main()

#!/usr/bin/env python3
"""Generate synthetic spans only; no real user or company data."""
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1] / 'trace-demo-data'
BASE = datetime(2026, 10, 20, 1, 42, tzinfo=timezone.utc).timestamp()


def generate(root=ROOT):
    records = []
    for index in range(36):
        trace = '%032x' % (0x6a4e2f901b3c4500 + index)
        start = BASE - index * 137
        host = ['DESKTOP-DESIGN-01', 'WORKSTATION-DEV-02', 'LAPTOP-OFFICE-03'][index % 3]
        root_span = '%016x' % (1000 + index*20)
        total_ms = 2840 + (index % 9) * 170
        definitions = [
            ('officeclaw.agent.invoke', 'officeACE', None, 0, total_ms),
            ('agent.execution', 'agent-runtime', 0, 45, total_ms-80),
            ('trajectory.001.session_started', 'context-service', 1, 80, 190),
            ('trajectory.002.user_message', 'model-gateway', 1, 310, 1080),
            ('trajectory.003.tool_call', 'tool-service', 1, 1450, 370),
            ('trajectory.004.tool_result', 'tool-service', 1, 1465, 520),
            ('POST /v1/messages', 'storage', 4, 1480, 185),
            ('trajectory.005.assistant_message', 'model-gateway', 1, 2050, total_ms-2180),
            ('trajectory.006.done', 'officeACE', 0, total_ms-65, 40),
        ]
        for ordinal, (name, service, parent, offset, length) in enumerate(definitions):
            sid = '%016x' % (1000 + index*20 + ordinal)
            error = index % 7 == 0 and ordinal == 5
            event_start = int((start+offset/1000+.012)*1e9)
            record = {'ts': int((start+total_ms/1000)*1e9), 'start': int((start+offset/1000)*1e9),
                      'traceId': trace, 'spanId': sid, 'traceState': '', 'parentSpanID': '' if parent is None else '%016x' % (1000+index*20+parent),
                      'spanName': name, 'spanKind': 0 if ordinal < 3 else 2,
                      'serviceName': service, 'fullServiceName': 'officeACE.'+service,
                      'resourceAttributes': {'host.name': host, 'user.id': 'demo-user-%02d' % (index%3+1), 'os.type': 'windows', 'service.version': '2.4.0', 'service.name':service, 'service.instance.id':'demo-instance-'+str(index%3+1), 'telemetry.sdk.name':'opentelemetry', 'telemetry.sdk.language':'nodejs', 'telemetry.sdk.version':'2.0.1', 'process.runtime.name':'node', 'process.runtime.version':'22.14.0'},
                      'spanAttributes': {'operation': name, 'thread.id': 'demo-thread-%02d-%02d' % (index%3+1,(index//3)//3+1)},
                      'duration': int(length*1e6), 'statusCode': 2 if error else 1,
                      'statusMessage': 'Search timed out; fallback completed' if error else '',
                      'eventsTs': [event_start, int((start+(offset+length-5)/1000)*1e9)] if ordinal in (3,5,7) else [],
                      'eventsName': ['request.sent', 'response.received'] if ordinal in (3,5,7) else [],
                      'eventsAttributes': [{'request.id':'req-%s-%s'%(index,ordinal)}, {'result': 'timeout' if error else 'ok', 'tokens': 1268}] if ordinal in (3,5,7) else []}
            if error:
                record['eventsName'][-1] = 'exception'
                record['eventsAttributes'][-1] = {'exception.type':'TimeoutError','exception.message':'Workspace search exceeded the configured timeout','exception.stacktrace':'TimeoutError: Workspace search timed out\n    at searchWorkspace (tools/search.js:42:11)\n    at async runAgent (agent/run.js:85:9)'}
            if ordinal == 3:
                record['spanAttributes']['content'] = '请分析这个项目的执行流程，定位可能的性能瓶颈。\n请保留每一步工具调用的结果。'
            if ordinal == 7:
                record['spanAttributes']['content'] = '已完成项目执行流程分析。\n\n主要耗时集中在模型生成与工作区搜索。两个工具操作存在并行执行，不能将它们的耗时简单相加。\n建议先检查搜索范围与模型请求的上下文大小，再对比优化前后的实际耗时。'
            if ordinal in (4, 5):
                record['spanAttributes']['tool.name'] = 'read_file'
            if ordinal == 4:
                record['spanAttributes']['tool.arguments'] = {'path':'src/main.py'}
                record['spanAttributes']['tool.result'] = 'def main():\n    prepare_workspace()\n    run_analysis()\n'
            records.append(record)
    # Repeated file exports reproduce the original full-record triplicate pattern.
    for copy in range(3):
        path = root / '20' / '09' / '42' / ('spans-%02d.json'%copy)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records if copy%2==0 else list(reversed(records)), ensure_ascii=False, indent=2), encoding='utf-8')
    return len(records)*3

if __name__ == '__main__':
    print('Synthetic records:', generate())
    print(ROOT)

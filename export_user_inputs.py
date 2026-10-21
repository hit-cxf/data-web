#!/usr/bin/env python3
"""Export spanAttributes.content from local officeACE files, without starting the web server."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from server import discover_files, fingerprint, read_records


MISSING = object()


def resource_value(resource, key):
    """Prefer literal dotted keys, while allowing ordinary nested dictionaries."""
    if not isinstance(resource, dict):
        return None
    if key in resource:
        return resource[key]
    value = resource
    for token in key.split('.'):
        if not isinstance(value, dict) or token not in value:
            return None
        value = value[token]
    return value


def export_inputs(source, output=None, deduplicate=False):
    source = Path(source).expanduser().resolve(strict=True)
    warnings = []
    if source.is_file():
        files = [source]
        base = source.parent
    elif source.is_dir():
        files = [path for path, _, _ in discover_files(source, warnings)]
        base = source
    else:
        raise ValueError('数据路径必须是目录或 JSON 文件。')
    if not files:
        raise ValueError('未找到 dd/hh/mm/*.json 文件，请检查数据根目录层级。')
    destination = Path(output).expanduser().resolve() if output else base / 'user_inputs.json'
    if destination in {path.resolve() for path in files}:
        raise ValueError('输出路径不能覆盖输入数据文件。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    counts = {'files': len(files), 'records': 0, 'matched': 0, 'exported': 0,
              'duplicates': 0, 'failed_files': 0}
    seen = set()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         prefix='.user_inputs-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write('[\n')
            for path in files:
                file_warnings = []
                try:
                    records = read_records(path, file_warnings)
                except (OSError, ValueError, UnicodeError) as error:
                    counts['failed_files'] += 1
                    warnings.append('%s：%s' % (path.relative_to(base), error))
                    continue
                warnings.extend('%s：%s' % (path.relative_to(base), message) for message in file_warnings)
                for index, record in enumerate(records, 1):
                    counts['records'] += 1
                    if not isinstance(record, dict):
                        continue
                    attributes = record.get('spanAttributes')
                    content = attributes.get('content', MISSING) if isinstance(attributes, dict) else MISSING
                    if content is MISSING:
                        continue
                    counts['matched'] += 1
                    if deduplicate:
                        identity = fingerprint(record)
                        if identity in seen:
                            counts['duplicates'] += 1
                            continue
                        seen.add(identity)
                    resource = record.get('resourceAttributes')
                    item = {
                        'user_id': resource_value(resource, 'user.id'),
                        'host_name': resource_value(resource, 'host.name'),
                        'start': record.get('start'),
                        'trace_id': record.get('traceId'),
                        'span_id': record.get('spanId', record.get('spanID')),
                        'span_name': record.get('spanName'),
                        'content': content,
                        'source': path.relative_to(base).as_posix(),
                        'record_index': index,
                    }
                    if counts['exported']:
                        handle.write(',\n')
                    # Write one exported record at a time; never collect the whole export in memory.
                    handle.write(json.dumps(item, ensure_ascii=False, indent=2, allow_nan=False))
                    counts['exported'] += 1
            handle.write('\n]\n')
        if counts['failed_files'] == counts['files']:
            raise ValueError('所有输入文件均读取失败，未替换原输出文件。' + ('\n' + warnings[0] if warnings else ''))
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {'output': str(destination), **counts, 'warnings': warnings}


def main(argv=None):
    parser = argparse.ArgumentParser(description='导出全部 spanAttributes.content，默认写入数据目录下的 user_inputs.json。')
    parser.add_argument('source', help='包含 dd/hh/mm 子目录的数据根目录，也支持单个 JSON / JSONL 文件')
    parser.add_argument('-o', '--output', help='自定义输出 JSON 路径')
    parser.add_argument('--deduplicate', action='store_true', help='按完整原始记录去重，避免重复导出文件导致内容出现多次')
    args = parser.parse_args(argv)
    try:
        result = export_inputs(args.source, args.output, args.deduplicate)
    except (OSError, ValueError) as error:
        print('导出失败：%s' % error, file=sys.stderr)
        return 1
    print('文件 %d 个，读取 %d 条，匹配 %d 条，导出 %d 条，过滤重复 %d 条。' %
          tuple(result[key] for key in ('files', 'records', 'matched', 'exported', 'duplicates')))
    print('已保存：%s' % result['output'])
    for message in result['warnings'][:20]:
        print('警告：%s' % message, file=sys.stderr)
    if len(result['warnings']) > 20:
        print('另有 %d 条读取警告。' % (len(result['warnings'])-20), file=sys.stderr)
    return 2 if result['failed_files'] else 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Generate deterministic, synthetic officeACE data for the local viewer."""

import argparse
import json
from pathlib import Path
import random


def main():
    parser = argparse.ArgumentParser(description="生成 officeACE 演示数据（不包含真实用户信息）")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "demo-data")
    parser.add_argument("--records-per-file", type=int, default=1000)
    args = parser.parse_args()
    if args.records_per_file < 1:
        parser.error("--records-per-file 必须大于 0")
    randomizer = random.Random(42)
    slots = [(18, 23, 58), (18, 23, 59), (19, 8, 0), (19, 8, 15), (19, 9, 0), (19, 9, 30)]
    apps = ["Word", "Excel", "PowerPoint", "Outlook"]
    hosts = ["office-bj-01", "office-bj-02", "office-sh-01", "office-sz-03"]
    actions = ["document.open", "document.save", "document.edit", "document.export"]
    for file_index, (day, hour, minute) in enumerate(slots):
        records = []
        for offset in range(args.records_per_file):
            sequence = file_index * args.records_per_file + offset
            app = apps[sequence % len(apps)]
            record = {
                "start": "2026-10-{:02d}T{:02d}:{:02d}:{:02d}.{:03d}+08:00".format(day, hour, minute, offset % 60, offset % 1000),
                "host": {
                    "name": hosts[sequence % len(hosts)],
                    "os": {"name": "Windows", "version": "11", "architecture": "x86_64"},
                    "network": {"region": ["北京", "上海", "深圳"][sequence % 3]},
                },
                "user": {"id": "demo-user-{:03d}".format(sequence % 24 + 1), "department": ["研发", "产品", "测试"][sequence % 3]},
                "resourceAttributes": {
                    "host.name": hosts[sequence % len(hosts)],
                    "user.id": "demo-user-{:03d}".format(sequence % 24 + 1),
                    "service": {"name": "officeACE", "environment": "demo"},
                },
                "office": {
                    "application": app,
                    "version": "16.0.17928",
                    "document": {
                        "name": "示例文档-{:04d}".format(sequence % 128 + 1),
                        "size_bytes": randomizer.randint(1024, 8_000_000),
                        "properties": {"pages": randomizer.randint(1, 80), "readonly": sequence % 5 == 0},
                    },
                },
                "event": {
                    "id": "demo-{:06d}".format(sequence + 1),
                    "action": actions[(sequence // 4) % len(actions)],
                    "duration_ms": randomizer.randint(12, 2400),
                    "success": sequence % 17 != 0,
                    "tags": ["synthetic", app.lower()],
                    "steps": [
                        {"name": "load", "timing": {"duration_ms": randomizer.randint(2, 80)}, "ok": True},
                        {"name": "process", "timing": {"duration_ms": randomizer.randint(5, 400)}, "ok": sequence % 17 != 0},
                    ],
                },
                "metadata": {"source": "演示数据", "note": None if sequence % 3 else "仅供界面测试"},
            }
            if sequence % 17 == 0:
                record["event"]["error"] = {"code": "DEMO_TIMEOUT", "message": "演示：操作超时"}
            records.append(record)
        directory = args.output / "{:02d}".format(day) / "{:02d}".format(hour) / "{:02d}".format(minute)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "events-demo.json"
        # Alternate common source formats to exercise ingestion.
        payload = records if file_index % 2 == 0 else {"records": records}
        destination.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("已生成 {} 条合成数据，{} 个文件：{}".format(len(slots) * args.records_per_file, len(slots), args.output.resolve()))


if __name__ == "__main__":
    main()

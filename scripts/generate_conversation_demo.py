#!/usr/bin/env python3
"""Create synthetic dialogues with three identical source copies for QA."""

import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1] / "chat-demo-data"
    records = []
    for host_index, host in enumerate(["office-bj-01", "office-sh-02"]):
        for session_index, session in enumerate(["季度报告整理", "会议纪要与待办"]):
            turns = [
                ("user", "请帮我整理附件里的季度报告，先检查数据，再给出完整的分析和下一步建议。", [{"name": "季度报告.xlsx", "size_bytes": 184320, "type": "spreadsheet", "note": "演示附件：不包含真实文件内容"}], None),
                ("assistant", "我会先读取表格并检查列名、缺失值与汇总范围，然后分三个部分整理结果：数据概览、变化原因和后续行动。", None,
                 [{"id": "call-demo-01", "function": {"name": "read_spreadsheet", "arguments": {"file": "季度报告.xlsx", "sheet": "Sheet1", "include_all_rows": True}}}]),
                ("tool", {"name": "read_spreadsheet", "status": "success", "result": {"columns": ["月份", "项目", "完成量"], "rows": [["一月", "演示项目 A", 120], ["二月", "演示项目 A", 150], ["三月", "演示项目 A", 180]], "warnings": [], "complete": True}}, None, {"name": "read_spreadsheet", "output": "已读取全部 3 行，未截断。"}),
                ("assistant", "已完成检查。\n\n1. 数据概览\n本季度演示项目 A 的完成量合计为 450，三个月分别为 120、150、180。\n\n2. 变化趋势\n每月增加 30，二月较一月增长 25%，三月较二月增长 20%。演示数据没有缺失值。\n\n3. 后续建议\n请核对统计口径、确认项目归属，并在下次复盘中补充计划量和实际完成量的对比。以上为合成示例，不代表真实业务结果。", None, None),
                ("user", "把下一步建议整理成清单，保留具体事项和验证方式。", None, None),
                ("assistant", "待办清单：\n\n□ 核对统计口径：确认月份、项目名称和完成量的定义一致。\n□ 核对数据来源：逐行确认原始记录与汇总表匹配。\n□ 补充计划数据：增加计划量、完成率和差异原因。\n□ 确认负责人：为每项待办填写负责人和完成日期。\n□ 安排复核：由另一位同事独立检查汇总公式。", None, None),
            ]
            for index, (role, content, attachments, tool) in enumerate(turns):
                item = {"start": "2026-10-20T{:02d}:{:02d}:{:02d}+08:00".format(9 + host_index, session_index * 20, index * 8),
                        "resourceAttributes": {"host.name": host, "user.id": "demo-user-{:02d}".format(host_index + 1)},
                        "conversation_id": session, "role": role, "content": content}
                if attachments is not None:
                    item["attachments"] = attachments
                if tool is not None:
                    item["tool_calls"] = tool
                records.append(item)
    # Different object key ordering in one copy must still deduplicate.
    for copy in range(3):
        folder = root / "20" / "09" / "00"
        folder.mkdir(parents=True, exist_ok=True)
        ordered = list(reversed(records)) if copy == 1 else records
        (folder / ("conversation-copy-%d.json" % (copy + 1))).write_text(
            json.dumps(ordered, ensure_ascii=False, sort_keys=copy == 2, indent=2), encoding="utf-8")
    print("对话示例：{} 条唯一记录，三份共 {} 条，{}".format(len(records), len(records) * 3, root))


if __name__ == "__main__":
    main()

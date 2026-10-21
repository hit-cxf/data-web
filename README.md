# officeACE 会话工作台 · 3.0

本地运行的回流数据浏览器，Python 3.9+，只使用标准库，前端无外部依赖。支持内网离线运行，不上传数据。

## 启动

macOS 双击 `start.command`，或在项目目录运行：

```sh
python3 server.py
```

Windows 使用 `python server.py`。浏览器打开 http://127.0.0.1:8766/ 。更新代码后停止旧服务、重新启动并刷新网页。无需删除原有缓存；数据库会自动升级，新字段首次使用时建立索引。

## 数据目录

点击右上角「连接数据目录」，输入运行 Python 服务的电脑上的数据根目录：

```text
数据根目录/
  20/
    09/
      42/
        spans.json
```

支持 JSON 数组、单条记录、常见封装数组和 JSONL/NDJSON。扫描会增量更新新增、变化和删除的文件，解析问题通过提示反馈。目录日 / 时 / 分范围包含起止分钟。

## 四层浏览

已移除中间甘特图。从左到右是：

1. **用户列表**：默认按 `resourceAttributes["user.id"]` 聚合。缺少用户 ID 时按主机区分，并标注「主机标识」；两者都缺失时放入「未识别用户」分组。
2. **会话列表**：同一用户下按 `spanAttributes["thread.id"]` 聚合，一个会话可以包含多个不同的 Trace ID。
3. **链路列表**：显示当前会话中的 Trace。
4. **调用列表**：显示当前 Trace 的 Span，按开始时间从早到晚排序，逐页加载完整记录。

右侧详情：

- 点击用户：会话数、链路数、调用数、异常数、主机数、服务数、观测时间范围、Span 累计耗时、主机与服务分布、常见调用。分布最多显示前 20 项。
- 点击调用：现有 Span 详情，包括 OTel SDK 信息、分组属性、事件、异常堆栈、完整原始 JSON、复制/导出与重复来源。

点击用户后自动展开其第一个会话、链路的调用列表，但右侧继续显示用户统计，直到点击具体 Span。切换上级选择会更新下级列表。四列独立分页。窄屏可横向滚动列表，右侧详情作为浮层展示，也可通过「查看详情」打开。

### 会话关联规则

- `thread.id` 是包含点号的完整键名：`spanAttributes["thread.id"]` 不等于 `spanAttributes.thread.id`。
- 相同 thread ID 在不同用户下相互隔离。
- 某条 Span 缺少 thread ID 时，只在同一用户、同一 Trace 中存在**唯一可确定的 thread ID**时沿用该关联；若存在多个冲突值，则不猜测。
- 不能关联的调用保留在「未关联会话」里，该分组不计入已关联会话数，且在会话列表中排在已关联会话之后。
- 不同 Trace ID 的独立 DNS 记录不会被随意拼接。缺少会话 ID 的 DNS Trace 仍可在未关联分组中查看。

## 筛选与缓存

支持用户/会话/Trace/操作/服务/主机关键词搜索、任意嵌套字段的包含/等于/存在/不存在/范围条件、目录时间范围、完整记录去重、「只看多节点链路」、按时间/Span 数量/耗时排序。

字段条件用于命中记录；四层浏览保留命中链路在选定目录时间内的完整上下文。用户和会话的统计基于这些保留下来的调用。相同 Trace ID 如果确实属于不同用户或不同会话，会在各自范围内分别展示。

去重依据完整 JSON 的 SHA-256，忽略对象键顺序，保留数组顺序；不会只按输入文字、Trace ID 或 Span ID 去重。关闭去重后，重复记录也会计入调用数。

SQLite 缓存在 `.cache/`。分层成员关系与统计聚合后缓存，选择用户、会话、链路或分页不再解析全部原始 JSON。最多保留 3 组浏览查询条件，重新扫描后自动失效。首次建立索引或新的筛选范围仍需要处理数据。当前没有用公司真实数据验证性能。

## 字段映射与 OTel

「字段映射」可指定用户 ID、会话 ID、Trace ID、Span ID、父 Span ID、操作名、开始时间、耗时、服务、主机、状态、资源属性、Span 属性与事件字段。新版本会自动优先匹配已知的 `user.id` 和 `thread.id` 路径，保留其他已有映射设置。

- 开始时间支持 ISO 和 Unix 时间戳，可自动识别秒/毫秒/微秒/纳秒。
- 标量 duration 默认纳秒，必须根据实际格式确认单位。也兼容 JavaScript SDK 的 `[seconds, nanoseconds]` 时间二元组。
- 数字 `spanKind` 的 JavaScript SDK 与 OTLP 编码不同，需手动选择；默认只解读文字枚举，不依据 SDK 语言猜测编码。
- 状态区分 UNSET、OK、ERROR，未知或缺失不标为成功。
- SDK 语言/版本、服务版本、运行时版本分别展示。`telemetry.sdk.language=nodejs` 不足以单独确定 exporter 或完整导出格式。
- 事件支持平铺的 `eventsTs/eventsName/eventsAttributes` 位置配对，也支持记录中的 `events` 对象数组。异常事件单独突出显示，原始字段仍完整保留。
- 当前输入格式为独立 Span 记录，不直接支持完整 OTLP `resourceSpans/scopeSpans` 信封。时间换算使用浮点秒，不承诺纳秒精度。

参考：[OTel 资源语义](https://opentelemetry.io/docs/specs/semconv/resource/)、[JavaScript SpanKind](https://open-telemetry.github.io/opentelemetry-js/enums/_opentelemetry_api._opentelemetry_api.SpanKind.html)。

## 示例

点击「载入示例」体验 3 个用户、12 个会话、36 条链路、324 个不同 Span。每条重复导出三次，共 972 条原始记录。示例仅包含合成数据，可重新生成：

```sh
python3 scripts/generate_trace_demo.py
```

## 导出所有用户输入

```sh
python3 export_user_inputs.py "/实际回流数据根目录" --deduplicate
```

提取每条记录的 `spanAttributes.content`，保存到数据根目录下的 `user_inputs.json`，同时保留用户、主机、start、Trace/Span 标识和来源。完整文本、结构化内容及显式空值均保留；没有该字段的记录跳过。`--deduplicate` 按完整原始记录去重，去掉该参数可保留全部重复记录。

也支持单个 JSON/JSONL 文件，以及 `-o "/其他目录/inputs.json"` 自定义输出路径。无需启动服务。逐文件读取、逐条写入，完成后才替换输出文件；部分文件失败返回退出码 2，全部失败不覆盖已有输出。

## Git 与验证

`.cache/`、示例数据、压缩包、临时截图和默认用户输入导出文件均已忽略。自定义导出文件名时，请自行忽略业务数据。本项目通过 Git 管理，不需要打包后再运行。

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_otel.js tests/test_filters.js tests/test_projection.js
node --check public/trace.js
```

主要源码：`server.py`（扫描/HTTP）、`traceview.py`（Span 索引与兼容链路 API）、`hierarchy.py`（四层浏览与统计）、`public/trace.js` / `trace.css`（界面）、`public/otel.js`（OTel 展示语义）。旧版 API 与辅助源码保留兼容，新首页不再加载旧表格、对话气泡或甘特图。

### Trajectory 业务调用展示

根据严格匹配的 `trajectory.<三位编号>.<类型>` 在调用列表及详情中标注用户输入、Agent 回复、工具调用；`tool_result` 标记为工具结果，`session_started` 和 `done` 分别标注会话开始、执行结束。`POST /v1/messages`、`agent.execution`、`officeclaw.agent.invoke`、`dns.lookup` 分别标注接口请求、Agent 执行、Agent 入口、域名解析，不从名称推断消息正文。

用户输入与 Agent 回复在详情名称下方完整展示 `spanAttributes.content`，以方框区分，不截断文本或结构化内容；工具记录在同一位置展示 `spanAttributes["tool.name"]`。字段缺失时明确提示，不回退成完整 JSON。SDK 信息不再作为顶部卡片，改为基础信息下方的两行小字。原始属性、事件与记录仍可完整查看。

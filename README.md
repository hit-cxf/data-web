# officeACE 链路工作台 · 2.1

本地运行的 Trace / Span 数据分析页面。新版本已替换原有的数据表格与对话页面，按链路结构组织回流数据。Python 3.9+，后端只使用标准库，前端无依赖；无需 npm、pip 或外网。

## 启动

macOS 双击 `start.command`，或在本目录运行：

```sh
python3 server.py
```

Windows 在本目录的终端运行 `python server.py`。浏览器打开 http://127.0.0.1:8766/ 。升级时先停止旧服务，再启动新版本并刷新浏览器。

## 连接数据

点击右上角「连接数据目录」，输入**运行 Python 服务的电脑上**的绝对路径：

```text
数据根目录/
  20/
    09/
      42/
        spans.json
```

目录时间筛选按 `dd/hh/mm` 目录计算，包含起止分钟，跨日范围按时间顺序比较。重新扫描会增量更新新增、修改、删除的文件。支持 JSON 数组、单条记录、常见封装数组以及 JSONL；文件解析问题显示在扫描提示中。浏览器不直接读取文件路径，由本机 Python 服务读取。

可点击「载入示例」查看合成数据：36 条链路、324 个不同 Span，每条重复导出三次，共 972 条原始记录。示例由 `python3 scripts/generate_trace_demo.py` 生成，不含真实业务数据。

## 界面

- **顶部概览**：匹配链路数、命中 Span 数、命中异常 Span 数、匹配记录的最长时间跨度。
- **左侧链路列表**：按开始时间从新到旧分页，支持搜索 Trace ID、操作名、服务名、主机名。
- **中间时间瀑布图**：横条位置和宽度表示开始时间与耗时；按父子关系缩进，支持折叠、展开、缩放。不同服务使用不同颜色，异常 Span 使用橙红色。默认载入 200 个 Span，点击继续加载。
- **右侧详情**：点击任意 Span 查看资源属性、Span 属性、状态、事件、完整原始 JSON、重复记录来源。原始记录支持复制和导出，不截断正文或工具结果。窄屏下详情作为侧边浮层打开。

**筛选语义**：字段条件作用于记录，列表显示包含命中记录的 Trace；打开某条 Trace 后，时间轴显示其在选定目录时间范围内的全部 Span，即使其他 Span 未命中字段条件。这样可以保留调用上下文。跨出目录时间范围的父节点不会被额外读取。

列表中的 Span 数、异常数、耗时基于命中记录；打开后的详情统计基于完整展示的链路，因此两者可能不同。

## 字段映射

扫描后会按实际发现的字段自动匹配，点击「字段映射」可更改。所有映射都可留空；未找到的结构会明确提示。输入和输出保留在属性或原始记录中，不再推断成对话气泡。

| 映射 | 默认候选 | 用途 |
| --- | --- | --- |
| Trace ID | `traceId` | 聚合同一条链路 |
| Span ID | `spanId` / `spanID` | 当前调用的唯一标识 |
| 父 Span ID | `parentSpanID` | 关联父子调用 |
| 操作名称 | `spanName` | 时间轴行名称 |
| 开始时间 | `start` | 时间轴起点 |
| 持续时间 | `duration` | 横条宽度 |
| 服务名称 | `serviceName` / `fullServiceName` | 服务配色与搜索 |
| 主机名称 | `resourceAttributes["host.name"]` | 主机搜索与详情 |
| 状态码 | `statusCode` | 数值 2 或 ERROR 等标准错误标记显示异常 |
| 资源属性 | `resourceAttributes` | 资源详情 |
| Span 属性 | `spanAttributes` | 业务参数、输入输出、工具调用等详情 |
| 事件时间 | `eventsTs` | 事件时间戳 |
| 事件名称 | `eventsName` | 事件名称 |
| 事件属性 | `eventsAttributes` | 事件详情 |

路径按完整键名区分：`resourceAttributes["host.name"]` 与 `resourceAttributes.host.name` 不同。数组字段通过 `[*]` 表示元素层级；链路标识和时间映射应尽量选择单值字段，遇到多个值时使用第一个。

开始时间支持 ISO 格式和 Unix 时间戳，自动识别秒、毫秒、微秒、纳秒，也可指定。**duration 无法仅靠数值推断单位，默认纳秒，请根据实际格式调整。** `ts` 的业务含义未知，不自动替代 `start`。

## 缺失或不完整数据

- 缺少 Trace ID：每条记录单独显示，不把不相关记录合并成一条链路。
- 缺少 Span ID：保留时间分布，但不能完整连接父子关系。
- 父节点缺失：作为顶层节点展示并提示，常见于采样或目录时间范围不完整。
- 同一 Trace 中 Span ID 重复：不随意选择其中一个作为父节点；提示标识不唯一。
- 循环引用：断开遍历避免死循环，保留所有记录并提示。
- 时间无法识别或 duration 负数：保留记录，显示提示，不伪造耗时。开始时间有效且缺少耗时时只显示最小定位条，不代表真实耗时。
- 时间轴使用浮点秒换算相对位置，适合毫秒级分析，不承诺纳秒精度。

事件的三个字段支持标量或数组。数组按位置对应，长度不同时保留已有项，原始内容也可单独展开。点击 Span 后，已选 Span 的事件会标在时间轴上；详情「事件」页展示完整内容。事件时间使用开始时间配置的时间单位。

## 筛选与去重

「筛选条件」支持任意层级字段的包含、等于、字段存在、字段不存在、范围比较。多个条件取 AND。包含比较不区分大小写；等于使用原始文本精确比较。范围支持数字或 ISO 时间。字段存在包含显式 `null`、`false`、`0`。数组按任一元素满足条件计算，同一条记录不会因多个匹配元素重复出现。

默认开启「过滤相同记录」。对完整 JSON 归一化后计算 SHA-256：忽略对象键顺序，保留数组顺序；任何字段内容不同都视为不同记录。去重不依据 Trace ID / Span ID。详情中的副本数及来源统计覆盖当前已扫描的数据，最多列出 200 个来源位置。

## 数据量与缓存

SQLite 本地缓存位于 `.cache/index.sqlite3`，原始数据不会上传。目录扫描、任意字段索引和链路字段索引可复用；文件变动会失效对应缓存。筛选在后端执行，浏览器只接收一页链路、一页 Span 及当前记录详情。

单条超大链路的层级排序会在后端读取该链路的轻量索引来还原完整父子关系，再分页返回。首次索引耗时取决于记录大小、磁盘与设备性能；本版本没有用真实公司数据做性能验证。

不要把 `.cache` 中的实际业务数据放入分发包。发布包仅含代码和合成示例。

## 验证

```sh
python3 -m unittest discover -s tests -v
node --check public/trace.js
```

测试覆盖目录扫描、通用嵌套字段筛选、去重及来源、链路聚合、筛选后保留链路上下文、自定义映射、缺失字段、重复 Span ID、循环关系、分页与重新扫描失效。

## 文件

- `server.py`：本地 HTTP 服务、扫描与通用字段索引。
- `traceview.py`：链路索引、列表聚合、父子关系与瀑布图分页。
- `public/index.html`、`public/trace.css`、`public/trace.js`：新版界面。
- `scripts/generate_trace_demo.py`：合成链路数据生成器。

旧版对话 API 与部分前端辅助源码保留用于兼容和已有回归检查，新首页不再加载旧界面。

## 2.1 · OpenTelemetry 展示优化

- 通过 `telemetry.sdk.name`、`telemetry.sdk.language`、`telemetry.sdk.version` 展示采集 SDK 卡片；`nodejs` 显示为 Node.js。SDK 版本与 `service.version`、`process.runtime.version` 分开显示。这些资源属性不能单独确定 exporter 或实际落盘格式。
- 资源属性按服务实例、主机用户、运行环境、采集 SDK 分组；Span 属性按 HTTP/RPC、数据库、AI/工具、输入输出、异常与其他字段分组。未知字段和复杂值完整保留，可展开原始结构。
- Span 状态区分 `UNSET`、`OK`、`ERROR`，缺失或未知状态不标为成功。异常事件突出显示类型、信息和完整调用堆栈；事件本身不会自动把 Span 的状态改成 ERROR。
- 字段映射增加调用类型和状态说明。**数字 SpanKind 编码必须手动确认**：JavaScript SDK 的 INTERNAL/SERVER/CLIENT 为 0/1/2，OTLP 对应 1/2/3。默认只解释文字枚举，数字显示「待确认编码」，不会因为 SDK 语言是 nodejs 就猜测导出格式。
- 开始时间与耗时兼容 JavaScript SDK `[seconds, nanoseconds]` 二元组；标量耗时仍使用指定单位。
- 详情支持常见 OTLP typed KeyValue 属性的解码和普通 `events` 对象数组；原始 JSON 不变。这不代表支持直接导入完整 OTLP `resourceSpans/scopeSpans` 信封，当前文件仍应是一条条独立 Span 记录。

参考：[OTel 资源语义](https://opentelemetry.io/docs/specs/semconv/resource/)、[JavaScript SDK SpanKind](https://open-telemetry.github.io/opentelemetry-js/enums/_opentelemetry_api._opentelemetry_api.SpanKind.html)、[OTLP Trace 定义](https://github.com/open-telemetry/opentelemetry-proto/blob/main/opentelemetry/proto/trace/v1/trace.proto)。

语义展示测试：`node --test tests/test_otel.js`。

"""Configurable conversation reconstruction over the local record index."""

import hashlib
import json
import math
from collections import OrderedDict
from datetime import datetime, timezone

MISSING = object()
PATH_KEYS = ("host_path", "time_path", "session_path", "message_path", "role_path",
             "type_path", "content_path", "attachment_path", "tool_path")
INDEX_KEYS = ("host_path", "time_path", "session_path", "message_path", "time_unit")


def initialize(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chat_file_cache (
            config_id TEXT NOT NULL,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            PRIMARY KEY(config_id,file_id)
        );
        CREATE TABLE IF NOT EXISTS chat_entries (
            config_id TEXT NOT NULL,
            record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            host TEXT, session TEXT, stamp REAL, time_text TEXT,
            PRIMARY KEY(config_id,record_id,ordinal)
        );
        CREATE INDEX IF NOT EXISTS chat_record_idx ON chat_entries(record_id);
        CREATE INDEX IF NOT EXISTS chat_group_idx ON chat_entries(config_id,host,session,stamp,record_id,ordinal);
    """)


def configuration(value, b):
    if not isinstance(value, dict):
        raise b.APIError("请设置对话字段映射。")
    result = {}
    for key in PATH_KEYS:
        path = value.get(key)
        if path is None or path == []:
            if key in ("host_path", "time_path"):
                raise b.APIError("请选择主机字段和时间字段。")
            result[key] = None
        elif (not isinstance(path, list) or len(path) > 128 or
              any(not isinstance(part, str) or len(part) > 1024 for part in path)):
            raise b.APIError(key + " 必须为完整字段路径的字符串数组。")
        else:
            result[key] = path
    result["time_unit"] = value.get("time_unit", "auto")
    if result["time_unit"] not in ("auto", "s", "ms", "us", "ns"):
        raise b.APIError("时间单位应为 auto / s / ms / us / ns。")
    defaults = {"user_values": ["user", "human"], "agent_values": ["assistant", "agent", "ai"],
                "tool_values": ["tool", "function", "tool_call", "tool_result"]}
    for key, default in defaults.items():
        values = value.get(key, default)
        if not isinstance(values, list) or len(values) > 50 or any(not isinstance(item, str) or len(item) > 200 for item in values):
            raise b.APIError(key + " 必须为角色名称的字符串数组。")
        result[key] = [item.strip().casefold() for item in values if item.strip()]
    rules = value.get("kind_rules", [])
    if not isinstance(rules, list) or len(rules) > 20:
        raise b.APIError("消息类型规则最多支持 20 项。")
    result["kind_rules"] = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("kind") not in ("user", "assistant", "tool"):
            raise b.APIError("消息类型规则应指定 user / assistant / tool。")
        conditions = b.prepare_field_filters(rule.get("conditions", []))
        if not conditions:
            raise b.APIError("消息类型规则至少需要一个有效条件。")
        path = rule.get("content_path")
        if path is not None and (not isinstance(path, list) or not 1 <= len(path) <= 128 or any(not isinstance(part, str) or len(part) > 1024 for part in path)):
            raise b.APIError("类型规则的正文路径无效。")
        result["kind_rules"].append({"kind": rule["kind"], "conditions": conditions, "content_path": path})
    return result


def first(document, path, b):
    return next(b.path_values(document, path), MISSING) if path else MISSING


def text(value, b):
    if value is MISSING or value is None:
        return None
    return b.value_text(value)


def messages(record, config, b):
    if not config["message_path"]:
        yield record
        return
    for value in b.path_values(record, config["message_path"]):
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(reversed(item))
            else:
                yield item


def mapped(record, message, path, config, b):
    if not path:
        return MISSING
    base = config["message_path"]
    if base and path[:len(base)] == base:
        relative = path[len(base):]
        while relative and relative[0] == "[]":
            relative = relative[1:]
        return first(message, relative, b) if relative else message
    return first(record, path, b)


def matches_condition(record, message, rule, config, b):
    path, base = list(rule["path"]), config["message_path"]
    target = record
    if base and path[:len(base)] == base:
        target, path = message, path[len(base):]
        while path and path[0] == "[]":
            path = path[1:]
    values = list(b.path_values(target, path)) if path else [target]
    if rule["operator"] == "exists":
        return bool(values)
    if rule["operator"] == "not_exists":
        return not values
    for value in values:
        rendered = "null" if value is None else b.value_text(value)
        if rule["operator"] == "contains" and rule["value"].casefold() in rendered.casefold():
            return True
        if rule["operator"] == "equals" and rendered == rule["value"]:
            return True
        if rule["operator"] == "between":
            kind, number = b.indexed_start(rendered)
            lower, upper = rule["lower"], rule["upper"]
            if kind == (lower or upper)[0] and (not lower or number >= lower[1]) and (not upper or number <= upper[1]):
                return True
    return False


def timestamp(value, unit="auto"):
    if value is MISSING or value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        divisor = {"s": 1, "ms": 1000, "us": 1000000, "ns": 1000000000}.get(unit)
        if divisor is None:
            absolute = abs(number)
            divisor = 1000000000 if absolute >= 1e17 else 1000000 if absolute >= 1e14 else 1000 if absolute >= 1e11 else 1
        number /= divisor
        return number if abs(number) <= 8640000000000 else None
    except (ValueError, TypeError, OverflowError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            return None


def ensure_index(scope, config, b):
    encoded = b.compact_json({key: config[key] for key in INDEX_KEYS})
    config_id = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    with b.SCAN_LOCK:
        conn = b.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            b.check_scan(conn, scope["scan_id"])
            files = conn.execute("SELECT f.id FROM files f" + scope["file_where"], scope["file_arguments"]).fetchall()
            for file in files:
                if conn.execute("SELECT 1 FROM chat_file_cache WHERE config_id=? AND file_id=?", (config_id, file["id"])).fetchone():
                    continue
                batch = []
                for row in conn.execute("SELECT id,document FROM records WHERE file_id=?", (file["id"],)):
                    record = json.loads(row["document"])
                    host = text(first(record, config["host_path"], b), b)
                    session = text(first(record, config["session_path"], b), b)
                    for ordinal, message in enumerate(messages(record, config, b)):
                        raw_time = mapped(record, message, config["time_path"], config, b)
                        batch.append((config_id, row["id"], ordinal, host, session,
                                      timestamp(raw_time, config["time_unit"]), text(raw_time, b)))
                        if len(batch) >= 2000:
                            conn.executemany("INSERT INTO chat_entries VALUES (?,?,?,?,?,?,?)", batch)
                            batch.clear()
                if batch:
                    conn.executemany("INSERT INTO chat_entries VALUES (?,?,?,?,?,?,?)", batch)
                conn.execute("INSERT INTO chat_file_cache VALUES (?,?)", (config_id, file["id"]))
            conn.commit()
            return config_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def prepare(payload, b):
    config = configuration(payload.get("config"), b)
    scope = b.query_scope(payload)
    config_id = ensure_index(scope, config, b)
    # Keep the grouped representative IDs on the outer side. SQLite otherwise
    # may rescan the entire GROUP BY coroutine once for every chat entry. Force
    # record lookup too: the host-first group index would scan a whole config
    # for every representative ID instead of seeking the requested record.
    cte = b.matched_cte(scope) + ", entries AS (SELECT ce.*,q.copies FROM selected q CROSS JOIN chat_entries ce INDEXED BY chat_record_idx WHERE ce.record_id=q.id AND ce.config_id=?) "
    return config, scope, cte, scope["arguments"] + [config_id]


def list_conversations(payload, b):
    page, size = b.page_arguments(payload, 50)
    config, scope, cte, args = prepare(payload, b)
    search = b.text_argument(payload.get("search", ""), "对话搜索").strip().casefold()
    grouped = "SELECT host,session,COUNT(*) AS count,COUNT(DISTINCT record_id) AS record_count,MIN(stamp) AS first_stamp,MAX(stamp) AS last_stamp,SUM(stamp IS NULL) AS missing_time FROM entries GROUP BY host,session"
    cte = cte.rstrip() + ", groups AS (" + grouped + ") "
    where = " WHERE instr(casefold(COALESCE(host,'')),?)>0 OR instr(casefold(COALESCE(session,'')),?)>0" if search else ""
    if search:
        args += [search, search]
    conn = b.connect()
    conn.create_function("casefold", 1, lambda value: value.casefold())
    try:
        conn.execute("BEGIN")
        b.check_scan(conn, scope["scan_id"])
        total = conn.execute(cte + "SELECT COUNT(*) FROM groups" + where, args).fetchone()[0]
        pages = math.ceil(total / size) if total else 0
        page = min(page, max(pages, 1))
        rows = conn.execute(cte + "SELECT * FROM groups" + where +
                            " ORDER BY (last_stamp IS NULL),last_stamp DESC,host,session LIMIT ? OFFSET ?",
                            args + [size, (page - 1) * size]).fetchall()
        return {"items": [{"id": b.compact_json([row["host"], row["session"]]), **dict(row)} for row in rows],
                "total": total, "page": page, "pages": pages, "page_size": size}
    finally:
        conn.close()


def normalize(record, message, row, config, b):
    role = text(mapped(record, message, config["role_path"], config, b), b)
    event_type = text(mapped(record, message, config["type_path"], config, b), b)
    role_fold, type_fold = (role or "").strip().casefold(), (event_type or "").strip().casefold()
    content = mapped(record, message, config["content_path"], config, b)
    attachment = mapped(record, message, config["attachment_path"], config, b)
    tool = mapped(record, message, config["tool_path"], config, b)
    if role_fold in config["user_values"]:
        kind = "user"
    elif role_fold in config["tool_values"] or type_fold in config["tool_values"]:
        kind = "tool"
    elif role_fold in config["agent_values"]:
        kind = "assistant"
    elif tool is not MISSING and tool is not None and tool != [] and tool != {} and tool != "":
        kind = "tool"
    else:
        kind = "record"
    for rule in config["kind_rules"]:
        if all(matches_condition(record, message, condition, config, b) for condition in rule["conditions"]):
            kind = rule["kind"]
            if rule["content_path"]:
                content = mapped(record, message, rule["content_path"], config, b)
            break
    return {"id": "%s:%s" % (row["record_id"], row["ordinal"]), "record_id": str(row["record_id"]),
            "source": row["path"], "kind": kind, "role": role, "timestamp": row["stamp"],
            "time_raw": row["time_text"], "copies": row["copies"],
            "content": message if content is MISSING else content,
            "attachments": None if attachment is MISSING else attachment,
            "tool": None if tool is MISSING else tool, "raw": message}


def conversation_messages(payload, b):
    if "host" not in payload or "session" not in payload:
        raise b.APIError("请选择一个对话。")
    for key in ("host", "session"):
        if payload[key] is not None:
            b.text_argument(payload[key], key)
    page, size = b.page_arguments(payload, 100)
    config, scope, cte, args = prepare(payload, b)
    where = " WHERE e.host IS ? AND e.session IS ?"
    args += [payload["host"], payload["session"]]
    conn = b.connect()
    try:
        conn.execute("BEGIN")
        b.check_scan(conn, scope["scan_id"])
        total = conn.execute(cte + "SELECT COUNT(*) FROM entries e" + where, args).fetchone()[0]
        pages = math.ceil(total / size) if total else 0
        page = min(page, max(pages, 1))
        rows = conn.execute(cte + "SELECT e.*,f.path FROM entries e JOIN records r ON r.id=e.record_id JOIN files f ON f.id=r.file_id" + where +
                            " ORDER BY (e.stamp IS NULL),e.stamp,e.record_id,e.ordinal LIMIT ? OFFSET ?",
                            args + [size, (page - 1) * size]).fetchall()
        cached, items = OrderedDict(), []
        for row in rows:
            record_id = row["record_id"]
            if record_id not in cached:
                record = json.loads(conn.execute("SELECT document FROM records WHERE id=?", (record_id,)).fetchone()[0])
                cached[record_id] = (record, list(messages(record, config, b)))
                if len(cached) > 4:
                    cached.popitem(last=False)
            record, parts = cached[record_id]
            items.append(normalize(record, parts[row["ordinal"]], row, config, b))
        return {"items": items, "total": total, "page": page, "pages": pages, "page_size": size}
    finally:
        conn.close()

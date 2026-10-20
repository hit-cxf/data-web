#!/usr/bin/env python3
"""Local officeACE explorer. Python 3.9+, standard library only."""

import argparse
import hashlib
import json
import math
import mimetypes
import re
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
CACHE_DIR = BASE_DIR / ".cache"
VERSION = "2.1"
MAX_BODY_BYTES = 1024 * 1024
MAX_PAGE_SIZE = 200
SCAN_LOCK = threading.Lock()
NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
DIRECTORY_NUMBER = re.compile(r"^\d{1,2}$")


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def connect():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CACHE_DIR / "index.sqlite3"), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize_database():
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                signature TEXT NOT NULL,
                day INTEGER NOT NULL, hour INTEGER NOT NULL, minute INTEGER NOT NULL,
                time_key INTEGER NOT NULL,
                record_count INTEGER NOT NULL DEFAULT 0,
                fields_json TEXT NOT NULL DEFAULT '[]',
                warnings_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS file_time_idx ON files(time_key);
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY,
                file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                record_index INTEGER NOT NULL,
                document TEXT NOT NULL,
                start_text TEXT, start_fold TEXT, start_kind TEXT, start_number REAL,
                host_text TEXT, host_fold TEXT, user_text TEXT, user_fold TEXT
            );
            CREATE INDEX IF NOT EXISTS record_file_idx ON records(file_id,record_index);
            CREATE INDEX IF NOT EXISTS record_start_idx ON records(start_kind,start_number);
            CREATE INDEX IF NOT EXISTS record_host_idx ON records(host_text);
            CREATE INDEX IF NOT EXISTS record_user_idx ON records(user_text);
            CREATE TABLE IF NOT EXISTS field_paths (
                id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS field_cache (
                file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                path_id INTEGER NOT NULL REFERENCES field_paths(id),
                PRIMARY KEY(file_id,path_id)
            );
            CREATE TABLE IF NOT EXISTS field_values (
                path_id INTEGER NOT NULL REFERENCES field_paths(id),
                record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                value_text TEXT NOT NULL, value_fold TEXT NOT NULL,
                value_kind TEXT, value_number REAL,
                PRIMARY KEY(path_id,record_id,ordinal)
            );
            CREATE INDEX IF NOT EXISTS field_record_idx ON field_values(record_id);
            CREATE INDEX IF NOT EXISTS field_text_idx ON field_values(path_id,value_text,record_id);
            CREATE INDEX IF NOT EXISTS field_number_idx ON field_values(path_id,value_kind,value_number,record_id);
        """)
        if "fingerprint" not in {row[1] for row in conn.execute("PRAGMA table_info(records)")}:
            conn.execute("ALTER TABLE records ADD COLUMN fingerprint TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS record_fingerprint_idx ON records(fingerprint)")
        import conversation
        conversation.initialize(conn)
        import traceview
        traceview.initialize(conn)


def fingerprint(record):
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ensure_fingerprints(scan_id):
    with SCAN_LOCK:
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            check_scan(conn, scan_id)
            while True:
                rows = conn.execute("SELECT id,document FROM records WHERE fingerprint IS NULL LIMIT 500").fetchall()
                if not rows:
                    break
                conn.executemany("UPDATE records SET fingerprint=? WHERE id=?",
                                 [(fingerprint(json.loads(row["document"])), row["id"]) for row in rows])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def metadata(conn, key):
    row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_metadata(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)", (key, value))


def field_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def collect_fields(record, destination):
    """Count each path once per record, preserving literal dotted key names."""
    seen = {}
    stack = [((), record)]
    while stack:
        path, value = stack.pop()
        if path:
            seen.setdefault(path, set()).add(field_type(value))
        if isinstance(value, dict):
            stack.extend((path + (str(key),), child) for key, child in value.items())
        elif isinstance(value, list):
            stack.extend((path + ("[]",), child) for child in value)
    for path, types in seen.items():
        entry = destination.setdefault(path, {"types": set(), "count": 0})
        entry["types"].update(types)
        entry["count"] += 1


def serialize_fields(fields):
    return [
        {"path": list(path), "types": sorted(entry["types"]), "count": entry["count"]}
        for path, entry in sorted(fields.items())
    ]


def merge_fields(rows):
    merged = {}
    for row in rows:
        for field in json.loads(row["fields_json"]):
            path = tuple(field["path"])
            entry = merged.setdefault(path, {"types": set(), "count": 0})
            entry["types"].update(field["types"])
            entry["count"] += field["count"]
    return serialize_fields(merged)


def lookup(record, path):
    if not isinstance(record, dict):
        return None
    if path in record:
        return record[path]
    value = record
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def value_text(value):
    if value is None:
        return None
    return value if isinstance(value, str) else compact_json(value)


def range_value(value, label):
    if not isinstance(value, str):
        raise APIError(label + " 必须为数字或 ISO 日期字符串。")
    value = value.strip()
    if not value:
        return None
    if NUMBER.fullmatch(value):
        number = float(value)
        if not math.isfinite(number):
            raise APIError(label + " 超出有效数字范围。")
        return "number", number
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return "date", parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        raise APIError(label + " 应为数字或 ISO 日期，例如 2026-10-19T09:30:00+08:00。")


def indexed_start(text):
    if text is None:
        return None, None
    try:
        parsed = range_value(text, "start")
        return parsed if parsed else (None, None)
    except APIError:
        return None, None


def unpack_document(document):
    if isinstance(document, list):
        return document
    if isinstance(document, dict):
        hits = document.get("hits")
        if isinstance(hits, dict) and isinstance(hits.get("hits"), list):
            return [hit.get("_source", hit) if isinstance(hit, dict) else hit for hit in hits["hits"]]
        # An event may legitimately contain an events/data/items array of its own.
        # Identity fields make it a single record instead of a list envelope.
        if any(key in document for key in ("start", "host", "user", "host.name", "user.id")):
            return [document]
        for key in ("records", "events", "data", "items"):
            if isinstance(document.get(key), list):
                return document[key]
        return [document]
    raise ValueError("JSON 顶层应为对象或数组")


def reject_json_constant(value):
    raise ValueError("无效 JSON 数字: " + value)


def read_records(path, warnings):
    with path.open("r", encoding="utf-8-sig") as handle:
        try:
            return unpack_document(json.load(handle, parse_constant=reject_json_constant))
        except json.JSONDecodeError as error:
            # NDJSON is recognized only when its first nonempty line is an object.
            handle.seek(0)
            records = []
            first = True
            bad_lines = 0
            for line_number, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                if first and not line.startswith("{"):
                    raise error
                first = False
                try:
                    record = json.loads(line, parse_constant=reject_json_constant)
                    if not isinstance(record, dict):
                        raise ValueError("NDJSON 行必须为对象")
                    records.append(record)
                except (json.JSONDecodeError, ValueError):
                    bad_lines += 1
                    if bad_lines <= 10:
                        warnings.append("第 %d 行无法解析，已跳过" % line_number)
            if not records:
                raise error
            if bad_lines > 10:
                warnings.append("另有 %d 行无法解析" % (bad_lines - 10))
            return records


def stat_signature(stat):
    return "%d:%d:%d" % (stat.st_size, stat.st_mtime_ns, stat.st_ino)


def discover_files(root, warnings):
    result = []

    def children(directory):
        try:
            return sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError as error:
            warnings.append("无法读取目录 %s：%s" % (directory.relative_to(root), error.strerror))
            return []

    for day in children(root):
        if day.is_symlink() or not day.is_dir() or not DIRECTORY_NUMBER.fullmatch(day.name):
            continue
        if not 1 <= int(day.name) <= 31:
            warnings.append("已忽略日期目录 %s：day 应介于 1–31。" % day.name)
            continue
        for hour in children(day):
            if hour.is_symlink() or not hour.is_dir() or not DIRECTORY_NUMBER.fullmatch(hour.name):
                continue
            if not 0 <= int(hour.name) <= 23:
                warnings.append("已忽略小时目录 %s：hour 应介于 0–23。" % hour.relative_to(root))
                continue
            for minute in children(hour):
                if minute.is_symlink() or not minute.is_dir() or not DIRECTORY_NUMBER.fullmatch(minute.name):
                    continue
                if not 0 <= int(minute.name) <= 59:
                    warnings.append("已忽略分钟目录 %s：minute 应介于 0–59。" % minute.relative_to(root))
                    continue
                for path in children(minute):
                    if path.is_symlink() or path.suffix.lower() not in (".json", ".jsonl", ".ndjson"):
                        continue
                    try:
                        if path.is_file():
                            result.append((path, (int(day.name), int(hour.name), int(minute.name)), stat_signature(path.stat())))
                    except OSError as error:
                        warnings.append("无法读取文件 %s：%s" % (path.relative_to(root), error.strerror))
    return result


def limited_warnings(warnings):
    return warnings[:200] + (["另有 %d 条警告未显示。" % (len(warnings) - 200)] if len(warnings) > 200 else [])


def scan_data(payload):
    root_text = payload.get("root")
    if not isinstance(root_text, str) or not root_text.strip():
        raise APIError("请填写包含 dd/hh/mm 子目录的数据根目录。")
    if "\x00" in root_text:
        raise APIError("数据目录包含无效字符。")
    try:
        root = Path(root_text.strip()).expanduser()
        if not root.is_absolute():
            raise APIError("请使用数据根目录的绝对路径。")
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise APIError("指定路径不是目录。")
        # A failed root listing is an error, never an empty successful replacement.
        next(root.iterdir(), None)
    except (OSError, RuntimeError) as error:
        raise APIError("无法访问数据目录：%s" % error)
    warnings = []
    with SCAN_LOCK:
        files = discover_files(root, warnings)
        if not files:
            warnings.append("未找到 dd/hh/mm/*.json 文件；请确认根目录层级，day 为 1–31、hour 为 0–23、minute 为 0–59。")
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if metadata(conn, "root") != str(root):
                conn.execute("DELETE FROM files")
            old_files = {row["path"]: row for row in conn.execute("SELECT id,path,signature FROM files")}
            current_paths = set()
            for path, slot, signature in files:
                source = path.relative_to(root).as_posix()
                current_paths.add(source)
                old = old_files.get(source)
                if old and old["signature"] == signature:
                    continue
                if old:
                    conn.execute("DELETE FROM files WHERE id=?", (old["id"],))
                day, hour, minute = slot
                cursor = conn.execute(
                    "INSERT INTO files(path,signature,day,hour,minute,time_key) VALUES (?,?,?,?,?,?)",
                    (source, signature, day, hour, minute, day * 1440 + hour * 60 + minute),
                )
                file_id = cursor.lastrowid
                file_warnings = []
                fields = {}
                count = 0
                try:
                    records = read_records(path, file_warnings)
                    if stat_signature(path.stat()) != signature:
                        raise ValueError("文件在读取期间发生变化，请重新扫描")
                    for record_index, record in enumerate(records):
                        document = compact_json(record)
                        start = value_text(lookup(record, "start"))
                        host = value_text(lookup(record, "host.name"))
                        user = value_text(lookup(record, "user.id"))
                        kind, number = indexed_start(start)
                        conn.execute(
                            """INSERT INTO records(file_id,record_index,document,start_text,start_fold,start_kind,start_number,
                            host_text,host_fold,user_text,user_fold,fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (file_id, record_index, document, start, start.casefold() if start else start, kind, number,
                             host, host.casefold() if host else host, user, user.casefold() if user else user, fingerprint(record)),
                        )
                        collect_fields(record, fields)
                        count += 1
                    del records
                except (OSError, ValueError, RecursionError, UnicodeError) as error:
                    conn.execute("DELETE FROM records WHERE file_id=?", (file_id,))
                    fields = {}
                    count = 0
                    file_warnings.append("文件已跳过：%s" % str(error))
                    # Retrying transient failures on the next scan is cheap and avoids stale state.
                    conn.execute("UPDATE files SET signature='' WHERE id=?", (file_id,))
                conn.execute(
                    "UPDATE files SET record_count=?,fields_json=?,warnings_json=? WHERE id=?",
                    (count, compact_json(serialize_fields(fields)), compact_json(file_warnings), file_id),
                )
            for source, old in old_files.items():
                if source not in current_paths:
                    conn.execute("DELETE FROM files WHERE id=?", (old["id"],))
            scan_id = uuid.uuid4().hex
            set_metadata(conn, "root", str(root))
            set_metadata(conn, "scan_id", scan_id)
            set_metadata(conn, "scan_warnings", compact_json(warnings))
            rows = conn.execute("SELECT * FROM files ORDER BY time_key,path").fetchall()
            for row in rows:
                warnings.extend(row["path"] + "：" + warning for warning in json.loads(row["warnings_json"]))
            result = {
                "root": str(root), "scan_id": scan_id, "file_count": len(rows),
                "record_count": sum(row["record_count"] for row in rows),
                "days": sorted({row["day"] for row in rows}),
                "min_time": [rows[0][name] for name in ("day", "hour", "minute")] if rows else None,
                "max_time": [rows[-1][name] for name in ("day", "hour", "minute")] if rows else None,
                "warnings": limited_warnings(warnings), "fields": merge_fields(rows),
            }
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def time_value(value, label):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 3 or any(type(part) is not int for part in value):
        raise APIError(label + " 应为 [day, hour, minute] 整数数组。")
    day, hour, minute = value
    if not (1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59):
        raise APIError(label + " 超出范围：day 1–31、hour 0–23、minute 0–59。")
    return day * 1440 + hour * 60 + minute


def text_argument(value, label):
    if not isinstance(value, str):
        raise APIError(label + " 必须为字符串。")
    if len(value) > 10000:
        raise APIError(label + " 过长。")
    return value


def filter_clause(filters, conditions, arguments):
    if not isinstance(filters, dict):
        raise APIError("filters 必须为对象。")
    for name, column in (("start", "start"), ("host_name", "host"), ("user_id", "user")):
        setting = filters.get(name)
        if setting is None:
            continue
        if not isinstance(setting, dict):
            raise APIError(name + " 筛选设置必须为对象。")
        operator = setting.get("operator", "contains")
        if operator not in ("contains", "equals", "between") or (operator == "between" and name != "start"):
            raise APIError(name + " 筛选方式无效。")
        if operator == "between":
            lower = range_value(setting.get("from", ""), "start 起点")
            upper = range_value(setting.get("to", ""), "start 终点")
            if lower and upper:
                if lower[0] != upper[0]:
                    raise APIError("start 区间的两个端点应同时为数字或同时为 ISO 日期。")
                if lower[1] > upper[1]:
                    raise APIError("start 区间起点不能晚于终点。")
            bound = lower or upper
            if bound:
                conditions.append("r.start_kind=?")
                arguments.append(bound[0])
            if lower:
                conditions.append("r.start_number>=?")
                arguments.append(lower[1])
            if upper:
                conditions.append("r.start_number<=?")
                arguments.append(upper[1])
        else:
            value = text_argument(setting.get("value", ""), name)
            if not value:
                continue
            if operator == "equals":
                conditions.append("r." + column + "_text=?")
                arguments.append(value)
            else:
                conditions.append("instr(r." + column + "_fold,?)>0")
                arguments.append(value.casefold())


def prepare_field_filters(settings):
    """Paths are token arrays: a literal 'host.name' key is never split on dots."""
    if not isinstance(settings, list) or len(settings) > 20:
        raise APIError("field_filters 应为数组，最多支持 20 个筛选条件。")
    result = []
    for index, setting in enumerate(settings, 1):
        label = "第 %d 个字段筛选" % index
        if not isinstance(setting, dict):
            raise APIError(label + " 必须为对象。")
        path = setting.get("path")
        if (not isinstance(path, list) or not 1 <= len(path) <= 128 or
                any(not isinstance(part, str) or len(part) > 1024 for part in path)):
            raise APIError(label + " 的 path 必须为完整字段路径的字符串数组。")
        operator = setting.get("operator", "contains")
        if operator not in ("contains", "equals", "between", "exists", "not_exists"):
            raise APIError(label + " 筛选方式无效。")
        rule = {"path": tuple(path), "operator": operator}
        if operator in ("exists", "not_exists"):
            pass
        elif operator == "between":
            lower = range_value(setting.get("from", ""), label + " 起点")
            upper = range_value(setting.get("to", ""), label + " 终点")
            if lower and upper:
                if lower[0] != upper[0]:
                    raise APIError(label + " 两端应同时为数字或同时为 ISO 日期。")
                if lower[1] > upper[1]:
                    raise APIError(label + " 起点不能晚于终点。")
            if not lower and not upper:
                continue
            rule.update(lower=lower, upper=upper)
        else:
            value = text_argument(setting.get("value", ""), label)
            if value == "":
                continue
            rule["value"] = value
        result.append(rule)
    return result


def path_values(document, path):
    stack = [(document, 0)]
    while stack:
        value, depth = stack.pop()
        if depth == len(path):
            yield value
        elif path[depth] == "[]" and isinstance(value, list):
            stack.extend((item, depth + 1) for item in reversed(value))
        elif isinstance(value, dict) and path[depth] in value:
            stack.append((value[path[depth]], depth + 1))


def ensure_field_indexes(scan_id, paths, file_conditions, file_arguments):
    """Index requested paths once per file; old indexes cascade away on rescan.

    Work stays in bounded batches and uses the existing cached documents, so an
    upgrade does not require re-reading source files or sending records to the UI.
    """
    paths = list(dict.fromkeys(paths))
    if not paths:
        return {}
    with SCAN_LOCK:
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if metadata(conn, "scan_id") != scan_id:
                raise APIError("数据索引已更新，请重新扫描目录。", 409)
            path_ids = {}
            for path in paths:
                encoded = compact_json(list(path))
                conn.execute("INSERT OR IGNORE INTO field_paths(path) VALUES (?)", (encoded,))
                path_ids[path] = conn.execute("SELECT id FROM field_paths WHERE path=?", (encoded,)).fetchone()[0]
            where = " WHERE " + " AND ".join(file_conditions) if file_conditions else ""
            files = conn.execute("SELECT f.id,f.fields_json FROM files f" + where, file_arguments).fetchall()
            for file in files:
                cached = {row[0] for row in conn.execute("SELECT path_id FROM field_cache WHERE file_id=?", (file["id"],))}
                missing = [path for path in paths if path_ids[path] not in cached]
                if not missing:
                    continue
                schema = {tuple(field["path"]) for field in json.loads(file["fields_json"])}
                present = [path for path in missing if path in schema]
                batch = []
                if present:
                    for row in conn.execute("SELECT id,document FROM records WHERE file_id=?", (file["id"],)):
                        document = json.loads(row["document"])
                        for path in present:
                            for ordinal, value in enumerate(path_values(document, path)):
                                text = "null" if value is None else value_text(value)
                                kind, number = indexed_start(text)
                                batch.append((path_ids[path], row["id"], ordinal, text, text.casefold(), kind, number))
                                if len(batch) >= 2000:
                                    conn.executemany("INSERT INTO field_values VALUES (?,?,?,?,?,?,?)", batch)
                                    batch.clear()
                    if batch:
                        conn.executemany("INSERT INTO field_values VALUES (?,?,?,?,?,?,?)", batch)
                conn.executemany("INSERT INTO field_cache(file_id,path_id) VALUES (?,?)",
                                 [(file["id"], path_ids[path]) for path in missing])
            conn.commit()
            return path_ids
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def field_filter_clauses(rules, path_ids, conditions, arguments):
    for rule in rules:
        predicates = ["v.path_id=?"]
        values = [path_ids[rule["path"]]]
        if rule["operator"] in ("exists", "not_exists"):
            pass
        elif rule["operator"] == "between":
            lower, upper = rule["lower"], rule["upper"]
            predicates.append("v.value_kind=?")
            values.append((lower or upper)[0])
            if lower:
                predicates.append("v.value_number>=?")
                values.append(lower[1])
            if upper:
                predicates.append("v.value_number<=?")
                values.append(upper[1])
        elif rule["operator"] == "equals":
            predicates.append("v.value_text=?")
            values.append(rule["value"])
        else:
            predicates.append("instr(v.value_fold,?)>0")
            values.append(rule["value"].casefold())
        comparison = "NOT IN" if rule["operator"] == "not_exists" else "IN"
        conditions.append("r.id " + comparison + " (SELECT v.record_id FROM field_values v WHERE " + " AND ".join(predicates) + ")")
        arguments.extend(values)


def check_scan(conn, scan_id):
    if metadata(conn, "scan_id") != scan_id:
        raise APIError("数据索引已更新或尚未建立，请重新扫描目录。", 409)


def query_scope(payload):
    scan_id = payload.get("scan_id")
    if not isinstance(scan_id, str) or not scan_id:
        raise APIError("请先扫描数据目录。")
    lower = time_value(payload.get("time_from"), "目录时间起点")
    upper = time_value(payload.get("time_to"), "目录时间终点")
    if lower is not None and upper is not None and lower > upper:
        raise APIError("目录时间起点不能晚于终点。")
    file_conditions, file_arguments = [], []
    if lower is not None:
        file_conditions.append("f.time_key>=?")
        file_arguments.append(lower)
    if upper is not None:
        file_conditions.append("f.time_key<=?")
        file_arguments.append(upper)
    conditions, arguments = list(file_conditions), list(file_arguments)
    filter_clause(payload.get("filters", {}), conditions, arguments)
    rules = prepare_field_filters(payload.get("field_filters", []))
    path_ids = ensure_field_indexes(scan_id, [rule["path"] for rule in rules], file_conditions, file_arguments)
    field_filter_clauses(rules, path_ids, conditions, arguments)
    deduplicate = payload.get("deduplicate", False)
    if type(deduplicate) is not bool:
        raise APIError("deduplicate 必须为布尔值。")
    if deduplicate:
        ensure_fingerprints(scan_id)
    return {"scan_id": scan_id, "arguments": arguments, "deduplicate": deduplicate,
            "where": " WHERE " + " AND ".join(conditions) if conditions else "",
            "file_where": " WHERE " + " AND ".join(file_conditions) if file_conditions else "",
            "file_arguments": file_arguments}


def matched_cte(scope):
    base = "WITH matched AS (SELECT r.id,r.fingerprint FROM records r JOIN files f ON f.id=r.file_id" + scope["where"] + ")"
    selected = "SELECT MIN(id) AS id,COUNT(*) AS copies FROM matched GROUP BY fingerprint" if scope["deduplicate"] else "SELECT id,1 AS copies FROM matched"
    return base + ", selected AS (" + selected + ") "


def page_arguments(payload, default_size=50):
    page, size = payload.get("page", 1), payload.get("page_size", default_size)
    if type(page) is not int or not 1 <= page <= 2147483647:
        raise APIError("page 必须为正整数。")
    if type(size) is not int or not 1 <= size <= MAX_PAGE_SIZE:
        raise APIError("page_size 必须介于 1 和 %d。" % MAX_PAGE_SIZE)
    return page, size


def query_data(payload):
    started = time.perf_counter()
    page, page_size = page_arguments(payload)
    sort = payload.get("sort", "time_desc")
    if sort not in ("time_asc", "time_desc"):
        raise APIError("sort 必须为 time_asc 或 time_desc。")
    scope = query_scope(payload)
    cte, args = matched_cte(scope), scope["arguments"]
    conn = connect()
    try:
        conn.execute("BEGIN")
        check_scan(conn, scope["scan_id"])
        counts = conn.execute(cte + "SELECT (SELECT COUNT(*) FROM matched),(SELECT COUNT(*) FROM selected)", args).fetchone()
        raw_total, total = counts
        pages = math.ceil(total / page_size) if total else 0
        page = min(page, max(pages, 1))
        order = "ASC" if sort == "time_asc" else "DESC"
        rows = conn.execute(cte +
            "SELECT r.id,r.record_index,r.document,q.copies,f.path,f.day,f.hour,f.minute FROM selected q JOIN records r ON r.id=q.id JOIN files f ON f.id=r.file_id" +
            " ORDER BY f.time_key " + order + ",f.path ASC,r.record_index ASC LIMIT ? OFFSET ?",
            args + [page_size, (page - 1) * page_size]).fetchall()
        file_rows = conn.execute("SELECT f.* FROM files f" + scope["file_where"] + " ORDER BY f.time_key,f.path", scope["file_arguments"]).fetchall()
        warnings = json.loads(metadata(conn, "scan_warnings") or "[]")
        for row in file_rows:
            warnings.extend(row["path"] + "：" + warning for warning in json.loads(row["warnings_json"]))
        return {
            "items": [{"id": str(row["id"]), "source": row["path"], "record": json.loads(row["document"]),
                       "copies": row["copies"], "time": [row["day"], row["hour"], row["minute"]]} for row in rows],
            "total": total, "raw_total": raw_total, "duplicate_count": raw_total - total,
            "deduplicate": scope["deduplicate"], "page": page, "page_size": page_size, "pages": pages,
            "file_count": len(file_rows), "fields": merge_fields(file_rows),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "warnings": limited_warnings(warnings),
        }
    finally:
        conn.close()


def record_details(payload):
    scan_id = payload.get("scan_id")
    if not isinstance(scan_id, str):
        raise APIError("请先扫描目录。")
    record_id = payload.get("id")
    if not isinstance(record_id, str) or not record_id.isdigit():
        raise APIError("记录 id 无效。")
    ensure_fingerprints(scan_id)
    conn = connect()
    try:
        conn.execute("BEGIN")
        check_scan(conn, scan_id)
        row = conn.execute("SELECT document,fingerprint FROM records WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise APIError("记录不存在。", 404)
        total = conn.execute("SELECT COUNT(*) FROM records WHERE fingerprint=?", (row["fingerprint"],)).fetchone()[0]
        copies = conn.execute("SELECT r.id,r.record_index,f.path FROM records r JOIN files f ON f.id=r.file_id WHERE r.fingerprint=? ORDER BY f.path,r.record_index LIMIT 200", (row["fingerprint"],)).fetchall()
        return {"record": json.loads(row["document"]),
                "raw_json": json.dumps(json.loads(row["document"]), ensure_ascii=False, indent=2), "total": total,
                "copies": [{"id": str(item["id"]), "source": item["path"], "index": item["record_index"] + 1} for item in copies],
                "has_more": total > len(copies)}
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "OfficeACE/" + VERSION

    def send_json(self, status, payload):
        content = compact_json(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def valid_host(self):
        host = self.headers.get("Host", "")
        try:
            parsed = urlsplit("//" + host)
            hostname = parsed.hostname
            port = parsed.port
            if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                return False
            allowed = {"localhost", "127.0.0.1", "::1", self.server.bind_host}
            if hostname not in allowed:
                return False
            return port == self.server.server_port
        except ValueError:
            return False

    def check_origin(self):
        if not self.valid_host():
            raise APIError("请求 Host 不被允许。请使用启动时显示的本地地址。", 403)
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != "http" or parsed.netloc != self.headers.get("Host") or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                raise APIError("不允许跨来源访问本地数据。", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise APIError("不允许跨站请求。", 403)

    def do_POST(self):
        try:
            self.check_origin()
            endpoint = urlsplit(self.path).path
            if endpoint not in ("/api/scan", "/api/query", "/api/record", "/api/conversations", "/api/conversation/messages", "/api/traces", "/api/trace/spans"):
                raise APIError("接口不存在。", 404)
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise APIError("请求 Content-Type 应为 application/json。", 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise APIError("无效 Content-Length。")
            if length < 1 or length > MAX_BODY_BYTES:
                raise APIError("请求为空或超过 1 MiB 限制。", 413)
            try:
                payload = json.loads(self.rfile.read(length), parse_constant=reject_json_constant)
            except (UnicodeError, ValueError):
                raise APIError("请求 JSON 无法解析。")
            if not isinstance(payload, dict):
                raise APIError("请求 JSON 顶层必须为对象。")
            if endpoint == "/api/scan":
                result = scan_data(payload)
            elif endpoint == "/api/query":
                result = query_data(payload)
            elif endpoint == "/api/record":
                result = record_details(payload)
            elif endpoint in ("/api/traces", "/api/trace/spans"):
                import traceview
                backend = sys.modules[__name__]
                result = traceview.list_traces(payload, backend) if endpoint == "/api/traces" else traceview.waterfall(payload, backend)
            else:
                import conversation
                backend = sys.modules[__name__]
                result = conversation.list_conversations(payload, backend) if endpoint == "/api/conversations" else conversation.conversation_messages(payload, backend)
            self.send_json(200, result)
        except APIError as error:
            self.send_json(error.status, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            print("Request failed: %s: %s" % (type(error).__name__, error), file=sys.stderr)
            self.send_json(500, {"error": "服务器处理失败；请查看启动终端的错误信息后重试。"})

    def do_GET(self):
        try:
            if not self.valid_host():
                raise APIError("请求 Host 不被允许。", 403)
            path = unquote(urlsplit(self.path).path)
            if path == "/api/status":
                self.send_json(200, {"default_root": self.server.default_root,
                                     "demo_root": str(BASE_DIR / "demo-data"), "version": VERSION,
                                     "chat_demo_root": str(BASE_DIR / "chat-demo-data"),
                                     "trace_demo_root": str(BASE_DIR / "trace-demo-data"),
                                     "capabilities": ["field_filters", "conversations", "deduplicate", "traces", "otel_semantics"]})
                return
            if path.startswith("/api/"):
                raise APIError("接口不存在。", 404)
            if "\x00" in path or "\\" in path or ".." in Path(path).parts:
                raise APIError("无效资源路径。", 403)
            public_root = PUBLIC_DIR.resolve()
            target = (public_root / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
            try:
                target.relative_to(public_root)
            except ValueError:
                raise APIError("禁止访问网页目录外的资源。", 403)
            if not target.is_file():
                raise APIError("资源不存在。", 404)
            content = target.read_bytes()
            mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or mime in ("application/javascript", "application/json") else ""))
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
            self.end_headers()
            self.wfile.write(content)
        except APIError as error:
            self.send_json(error.status, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            self.send_json(500, {"error": "无法读取网页资源。"})

    def do_OPTIONS(self):
        self.send_json(403, {"error": "不允许跨来源访问本地数据。"})


def main():
    parser = argparse.ArgumentParser(description="officeACE 本地回流数据浏览器")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8766, help="监听端口，默认 8766")
    parser.add_argument("--data-root", help="前端初始数据根目录，也可在网页设置")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port 应介于 0 和 65535")
    initialize_database()
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as error:
        parser.exit(1, "无法启动服务器：%s\n" % error)
    server.bind_host = args.host
    server.default_root = str(Path(args.data_root).expanduser().resolve()) if args.data_root else None
    print("officeACE 数据浏览器：http://%s:%d" % (args.host, server.server_port), flush=True)
    print("在网页中设置包含 dd/hh/mm 子目录的数据根目录。Ctrl+C 停止。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

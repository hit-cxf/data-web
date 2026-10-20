"""HTTP integration tests using only the Python standard library.

Run with: python3 -m unittest discover -s tests -v
"""

import http.client
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DataWebIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server_tmp = tempfile.TemporaryDirectory(prefix="officeace-server-test-")
        cls.server_log = open(Path(cls.server_tmp.name) / "server.log", "w+")
        with socket.socket() as port_probe:
            port_probe.bind(("127.0.0.1", 0))
            cls.port = port_probe.getsockname()[1]
        bootstrap = (
            "import sys\n"
            "from pathlib import Path\n"
            "import server\n"
            "server.CACHE_DIR = Path(sys.argv.pop(1))\n"
            "server.main()\n"
        )
        cls.process = subprocess.Popen(
            [sys.executable, "-c", bootstrap, str(Path(cls.server_tmp.name) / "cache"),
             "--port", str(cls.port)],
            cwd=PROJECT_ROOT,
            stdout=cls.server_log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                break
            try:
                connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=1)
                connection.request("GET", "/api/status")
                response = connection.getresponse()
                response.read()
                connection.close()
                if response.status == 200:
                    return
            except OSError:
                pass
            time.sleep(0.05)
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.server_log.seek(0)
        detail = cls.server_log.read()
        cls.server_log.close()
        cls.server_tmp.cleanup()
        raise RuntimeError("Server did not become ready:\n" + detail)

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait(timeout=5)
        cls.server_log.close()
        cls.server_tmp.cleanup()

    def setUp(self):
        self.dataset = tempfile.TemporaryDirectory(prefix="officeace-data-test-")
        self.addCleanup(self.dataset.cleanup)
        self.root = Path(self.dataset.name)

    def request(self, method, path, payload=None, headers=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        request_headers = {"Content-Type": "application/json"} if body else {}
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            content = response.read()
            if "application/json" in response.getheader("Content-Type", ""):
                content = json.loads(content)
            return response.status, content
        finally:
            connection.close()

    def write_json(self, relative_path, content):
        target = self.root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return target

    def scan(self):
        status, result = self.request("POST", "/api/scan", {"root": str(self.root)})
        self.assertEqual(status, 200, result)
        self.assertIn("scan_id", result)
        return result

    def query(self, scan_id, **overrides):
        payload = {
            "scan_id": scan_id,
            "time_from": None,
            "time_to": None,
            "filters": {},
            "page": 1,
            "page_size": 50,
            "sort": "time_asc",
        }
        payload.update(overrides)
        status, result = self.request("POST", "/api/query", payload)
        self.assertEqual(status, 200, result)
        return result

    @staticmethod
    def record_names(result):
        return [item["record"]["name"] for item in result["items"]]

    def test_status_and_static_app_are_available(self):
        status, result = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertIn("demo_root", result)
        status, content = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", content.lower())

    def test_deduplicate_uses_complete_json_and_preserves_provenance(self):
        record = {"start": 1, "attributes": {"host.name": "pc"}, "content": "hello"}
        for minute in range(3):
            self.write_json("20/09/%02d/copy.json" % minute, [dict(reversed(list(record.items()))) if minute == 1 else record])
        self.write_json("20/09/03/different.json", [{**record, "hidden_metadata": "different"}])
        scan_id = self.scan()["scan_id"]
        self.assertEqual(self.query(scan_id)["total"], 4)
        result = self.query(scan_id, deduplicate=True, page_size=1)
        self.assertEqual((result["raw_total"], result["total"], result["duplicate_count"], result["pages"]), (4, 2, 2, 2))
        self.assertEqual(result["items"][0]["copies"], 3)
        status, origins = self.request("POST", "/api/record", {"scan_id": scan_id, "id": result["items"][0]["id"]})
        self.assertEqual(status, 200, origins)
        self.assertEqual(origins["total"], 3)
        self.assertEqual(len({copy["source"] for copy in origins["copies"]}), 3)
        limited = self.query(scan_id, deduplicate=True, time_from=[20, 9, 1], time_to=[20, 9, 1])
        self.assertEqual((limited["total"], limited["items"][0]["copies"]), (1, 1))
        self.assertEqual(self.query(self.scan()["scan_id"], deduplicate=True)["raw_total"], 4)

    def test_presence_filters_distinguish_null_from_absent(self):
        self.write_json("20/09/00/existence.json", [
            {"name": "null", "nested": {"value": None}}, {"name": "false", "nested": {"value": False}},
            {"name": "zero", "nested": {"value": 0}}, {"name": "missing", "nested": {}},
        ])
        scan_id = self.scan()["scan_id"]
        result = self.query(scan_id, field_filters=[{"path": ["nested", "value"], "operator": "exists"}])
        self.assertEqual(set(self.record_names(result)), {"null", "false", "zero"})
        result = self.query(scan_id, field_filters=[{"path": ["nested", "value"], "operator": "not_exists"}])
        self.assertEqual(self.record_names(result), ["missing"])

    @staticmethod
    def chat_config(**overrides):
        return {"host_path": ["resourceAttributes", "host.name"], "time_path": ["start"],
                "session_path": ["conversation_id"], "role_path": ["role"], "content_path": ["content"],
                "attachment_path": ["attachments"], "tool_path": ["tool_calls"], **overrides}

    def chat_request(self, endpoint, payload):
        status, result = self.request("POST", endpoint, payload)
        self.assertEqual(status, 200, result)
        return result

    def test_conversations_group_by_host_session_sort_instants_and_deduplicate(self):
        records = [
            {"start": "2026-10-20T17:02:00+08:00", "resourceAttributes": {"host.name": "a"}, "conversation_id": "s1", "role": "assistant", "content": "last"},
            {"start": "2026-10-20T09:01:00Z", "resourceAttributes": {"host.name": "a"}, "conversation_id": "s1", "role": "user", "content": "first"},
            {"start": "2026-10-20T09:03:00Z", "resourceAttributes": {"host.name": "a"}, "conversation_id": "s2", "role": "user", "content": "another session"},
            {"start": "2026-10-20T09:04:00Z", "resourceAttributes": {"host.name": "b"}, "conversation_id": "s1", "role": "user", "content": "another host"},
        ]
        for copy in range(3):
            self.write_json("20/09/00/copy-%d.json" % copy, records)
        scan_id = self.scan()["scan_id"]
        payload = {"scan_id": scan_id, "config": self.chat_config(), "deduplicate": True}
        groups = self.chat_request("/api/conversations", payload)
        self.assertEqual(groups["total"], 3)
        self.assertEqual(sum(group["count"] for group in groups["items"]), 4)
        result = self.chat_request("/api/conversation/messages", {**payload, "host": "a", "session": "s1", "page_size": 1})
        self.assertEqual((result["total"], result["pages"]), (2, 2))
        self.assertEqual(result["items"][0]["content"], "first")
        self.assertEqual(result["items"][0]["kind"], "user")
        self.assertEqual(result["items"][0]["copies"], 3)
        result = self.chat_request("/api/conversation/messages", {**payload, "host": "a", "session": "s1", "page": 2, "page_size": 1})
        self.assertEqual(result["items"][0]["content"], "last")
        no_session = self.chat_request("/api/conversations", {**payload, "config": self.chat_config(session_path=None)})
        self.assertEqual(no_session["total"], 2)
        filtered = self.chat_request("/api/conversations", {**payload, "field_filters": [{"path": ["role"], "operator": "equals", "value": "assistant"}]})
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["items"][0]["count"], 1)

    def test_message_arrays_sort_globally_keep_full_content_and_tool_details(self):
        long_text = "完整消息" * 10000
        user = {"time": 1700000000000, "role": "customer", "content": long_text, "attachments": [{"name": "input.pdf", "extra": "keep all"}]}
        agent = {"time": 1700000002000, "role": "assistant", "content": "done", "tool_calls": [{"name": "read", "arguments": {"full": long_text}}]}
        tool = {"time": 1700000001000, "role": "tool", "content": {"result": long_text}}
        for name, parts in [("one", [agent, user]), ("two", [tool])]:
            self.write_json("20/09/00/" + name + ".json", [{"resourceAttributes": {"host.name": "a"}, "body": {"messages": parts}}])
        config = self.chat_config(session_path=None, message_path=["body", "messages"], time_path=["body", "messages", "[]", "time"],
                                  role_path=["body", "messages", "[]", "role"], content_path=["body", "messages", "[]", "content"],
                                  tool_path=["body", "messages", "[]", "tool_calls"], attachment_path=["body", "messages", "[]", "attachments"], user_values=["customer"])
        payload = {"scan_id": self.scan()["scan_id"], "config": config, "host": "a", "session": None}
        result = self.chat_request("/api/conversation/messages", payload)
        self.assertEqual([item["kind"] for item in result["items"]], ["user", "tool", "assistant"])
        self.assertEqual(result["items"][0]["content"], long_text)
        self.assertEqual(result["items"][0]["attachments"], user["attachments"])
        self.assertEqual(result["items"][1]["raw"], tool)
        self.assertEqual(result["items"][2]["tool"], agent["tool_calls"])
        self.assertEqual([item["timestamp"] for item in result["items"]], [1700000000, 1700000001, 1700000002])

    def test_conversation_missing_time_is_last_and_cache_tracks_rescan(self):
        path = self.write_json("20/09/00/messages.json", [
            {"resourceAttributes": {"host.name": "a"}, "role": "unknown", "content": "missing"},
            {"resourceAttributes": {"host.name": "a"}, "start": "invalid", "content": "invalid"},
            {"resourceAttributes": {"host.name": "a"}, "start": 1700000000, "content": "valid"},
        ])
        payload = {"scan_id": self.scan()["scan_id"], "config": self.chat_config(session_path=None), "host": "a", "session": None}
        result = self.chat_request("/api/conversation/messages", payload)
        self.assertEqual([item["content"] for item in result["items"]], ["valid", "missing", "invalid"])
        self.assertEqual(result["items"][1]["kind"], "record")
        path.write_text(json.dumps([{"resourceAttributes": {"host.name": "new-host"}, "start": 1700000001, "content": "replacement"}]))
        payload["scan_id"] = self.scan()["scan_id"]
        self.assertEqual(self.chat_request("/api/conversation/messages", payload)["total"], 0)
        groups = self.chat_request("/api/conversations", payload)
        self.assertEqual([group["host"] for group in groups["items"]], ["new-host"])

    def test_conversation_configuration_validation(self):
        self.write_json("20/09/00/a.json", [{"start": 1}])
        scan_id = self.scan()["scan_id"]
        for config in [None, {}, self.chat_config(host_path="resourceAttributes.host.name"), self.chat_config(time_unit="bad"), self.chat_config(user_values="user")]:
            status, result = self.request("POST", "/api/conversations", {"scan_id": scan_id, "config": config})
            self.assertEqual(status, 400, result)

    def test_message_types_can_be_identified_by_field_presence_and_values(self):
        self.write_json("20/09/00/rules.json", [
            {"start": 1, "resourceAttributes": {"host.name": "a"}, "payload": {"input": "question"}},
            {"start": 2, "resourceAttributes": {"host.name": "a"}, "event_type": "answer", "payload": {"output": "complete answer"}},
            {"start": 3, "resourceAttributes": {"host.name": "a"}, "payload": {"call": {"name": "read", "arguments": {"all": True}}}},
        ])
        config = self.chat_config(session_path=None, role_path=None, content_path=None, kind_rules=[
            {"kind": "user", "conditions": [{"path": ["payload", "input"], "operator": "exists"}], "content_path": ["payload", "input"]},
            {"kind": "assistant", "conditions": [{"path": ["event_type"], "operator": "equals", "value": "answer"}], "content_path": ["payload", "output"]},
            {"kind": "tool", "conditions": [{"path": ["payload", "call"], "operator": "exists"}]},
        ])
        result = self.chat_request("/api/conversation/messages", {"scan_id": self.scan()["scan_id"], "config": config, "host": "a", "session": None})
        self.assertEqual([item["kind"] for item in result["items"]], ["user", "assistant", "tool"])
        self.assertEqual([item["content"] for item in result["items"][:2]], ["question", "complete answer"])
        self.assertEqual(result["items"][2]["raw"]["payload"]["call"]["arguments"], {"all": True})

    def test_arbitrary_field_filters_use_exact_nested_paths(self):
        self.write_json("19/08/00/nested.json", [
            {"name": "match", "start": 10, "resourceAttributes": {
                "host.name": "Company-PC", "user.id": "employee-01",
                "host": {"name": "different-nested-host"}}},
            {"name": "other-user", "start": 20, "resourceAttributes": {
                "host.name": "Company-PC", "user.id": "employee-02"}},
            {"name": "wrong-level", "host.name": "Company-PC", "user.id": "employee-01"},
        ])
        scan_id = self.scan()["scan_id"]
        filters = [
            {"path": ["resourceAttributes", "host.name"], "operator": "contains", "value": "COMPANY"},
            {"path": ["resourceAttributes", "user.id"], "operator": "equals", "value": "employee-01"},
        ]
        result = self.query(scan_id, field_filters=filters)
        self.assertEqual(self.record_names(result), ["match"])
        result = self.query(scan_id, field_filters=[{
            "path": ["resourceAttributes", "host", "name"],
            "operator": "equals", "value": "different-nested-host",
        }])
        self.assertEqual(self.record_names(result), ["match"])

    def test_arbitrary_field_filters_expand_nested_arrays_and_preserve_scalar_types(self):
        self.write_json("19/08/00/arrays.json", [
            {"name": "match", "groups": [[{"attributes": {"user.id": "alice"}},
                                         {"attributes": {"user.id": "bob"}}]],
             "enabled": False, "score": 0, "nullable": None},
            {"name": "other", "groups": [[{"attributes": {"user.id": "carol"}}]],
             "enabled": True, "score": 12},
            {"name": "missing", "groups": []},
        ])
        scan_id = self.scan()["scan_id"]
        result = self.query(scan_id, field_filters=[
            {"path": ["groups", "[]", "[]", "attributes", "user.id"], "value": "bob"},
            {"path": ["enabled"], "operator": "equals", "value": "false"},
            {"path": ["score"], "operator": "equals", "value": "0"},
            {"path": ["nullable"], "operator": "equals", "value": "null"},
        ])
        self.assertEqual(self.record_names(result), ["match"])

    def test_arbitrary_field_ranges_and_cached_rescan(self):
        target = self.write_json("19/08/00/ranges.json", [
            {"name": "low", "metrics": {"duration": 2, "timestamp": "2026-10-19T08:00:00Z"}},
            {"name": "match", "metrics": {"duration": 10, "timestamp": "2026-10-19T17:00:00+08:00"}},
            {"name": "high", "metrics": {"duration": 100, "timestamp": "invalid"}},
        ])
        filters = [
            {"path": ["metrics", "duration"], "operator": "between", "from": "9", "to": "11"},
            {"path": ["metrics", "timestamp"], "operator": "between", "from": "2026-10-19T09:00:00Z"},
        ]
        scan_id = self.scan()["scan_id"]
        for _ in range(2):
            self.assertEqual(self.record_names(self.query(scan_id, field_filters=filters)), ["match"])
        target.write_text(json.dumps([{"name": "replacement", "metrics": {"duration": 200}}]))
        scan_id = self.scan()["scan_id"]
        self.assertEqual(self.query(scan_id, field_filters=filters)["total"], 0)

    def test_arbitrary_field_filters_validate_paths_and_conditions(self):
        self.write_json("19/08/00/data.json", [{"name": "value", "nested": {"key": "data"}}])
        scan_id = self.scan()["scan_id"]
        for invalid in [
            "nested.key", [{"path": "nested.key", "value": "data"}],
            [{"path": [], "value": "data"}], [{"path": ["nested", 1], "value": "data"}],
            [{"path": ["nested"], "operator": "invalid", "value": "data"}],
            [{"path": ["nested"], "value": 12}],
            [{"path": ["nested"], "operator": "between", "from": "11", "to": "2"}],
        ]:
            status, result = self.request("POST", "/api/query", {"scan_id": scan_id, "field_filters": invalid})
            self.assertEqual(status, 400, result)

    def test_arbitrary_field_cache_expands_with_time_range_and_drops_deleted_files(self):
        self.write_json("19/08/00/first.json", [{"name": "first", "resourceAttributes": {"host.name": "pc"}}])
        second = self.write_json("19/09/00/second.json", [{"name": "second", "resourceAttributes": {"host.name": "pc"}}])
        scan_id = self.scan()["scan_id"]
        filters = [{"path": ["resourceAttributes", "host.name"], "value": "pc"}]
        result = self.query(scan_id, time_from=[19, 8, 0], time_to=[19, 8, 0], field_filters=filters)
        self.assertEqual(self.record_names(result), ["first"])
        self.assertEqual(self.query(scan_id, field_filters=filters)["total"], 2)
        # An unchanged scan retains useful indexes but issues a new scan token.
        scan_id = self.scan()["scan_id"]
        self.assertEqual(self.query(scan_id, field_filters=filters)["total"], 2)
        second.unlink()
        scan_id = self.scan()["scan_id"]
        self.assertEqual(self.record_names(self.query(scan_id, field_filters=filters)), ["first"])

    def test_arbitrary_field_keys_and_keywords_are_literal_and_arrays_do_not_duplicate_rows(self):
        self.write_json("19/08/00/literal.json", [
            {"name": "match", "attributes": {'odd"key.[x]': "O'Reilly_100%"}, "tags": ["same", "same"]},
            {"name": "other", "attributes": {'odd"key.[x]': "O'ReillyX1000"}, "tags": ["different"]},
        ])
        scan_id = self.scan()["scan_id"]
        result = self.query(scan_id, field_filters=[
            {"path": ["attributes", 'odd"key.[x]'], "value": "_100%"},
            {"path": ["tags", "[]"], "value": "same"},
        ], page_size=1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(self.record_names(result), ["match"])

    def test_inclusive_time_range_is_chronological_across_days(self):
        for relative_path, name in [
            ("03/22/59/before.json", "before"),
            ("03/23/58/first.json", "first"),
            ("03/23/59/late.json", "late"),
            ("04/00/00/midnight.json", "midnight"),
            ("04/00/02/last.json", "last"),
            ("04/00/03/after.json", "after"),
        ]:
            self.write_json(relative_path, [{"name": name}])
        scanned = self.scan()
        self.assertEqual(scanned["file_count"], 6)
        self.assertEqual(scanned["record_count"], 6)
        self.assertEqual(scanned["days"], [3, 4])
        self.assertEqual(scanned["min_time"], [3, 22, 59])
        self.assertEqual(scanned["max_time"], [4, 0, 3])
        result = self.query(
            scanned["scan_id"], time_from=[3, 23, 58], time_to=[4, 0, 2]
        )
        self.assertEqual(self.record_names(result), ["first", "late", "midnight", "last"])
        self.assertEqual(result["file_count"], 4)
        descending = self.query(
            scanned["scan_id"],
            time_from=[3, 23, 58],
            time_to=[4, 0, 2],
            sort="time_desc",
        )
        self.assertEqual(self.record_names(descending), ["last", "midnight", "late", "first"])

    def test_filters_are_combined_for_nested_and_literal_dotted_fields(self):
        self.write_json("07/09/10/data.json", [
            {"name": "nested", "start": "2026-10-19T09:00:00Z",
             "host": {"name": "Office-Laptop"}, "user": {"id": "alice"}},
            {"name": "literal", "start": "2026-10-19T09:30:00Z",
             "host.name": "Office-Desktop", "user.id": "alice"},
            {"name": "wrong-user", "start": "2026-10-19T09:30:00Z",
             "host": {"name": "Office-Desktop"}, "user": {"id": "bob"}},
            {"name": "wrong-start", "start": "2026-10-18T09:30:00Z",
             "host.name": "Office-Desktop", "user.id": "alice"},
            {"name": "missing-host", "start": "2026-10-19T09:30:00Z", "user.id": "alice"},
        ])
        scanned = self.scan()
        result = self.query(scanned["scan_id"], filters={
            "start": {"operator": "contains", "value": "2026-10-19"},
            "host_name": {"operator": "contains", "value": "OFFICE"},
            "user_id": {"operator": "equals", "value": "alice"},
        })
        self.assertEqual(set(self.record_names(result)), {"nested", "literal"})

    def test_literal_dotted_field_takes_precedence_over_nested_field(self):
        self.write_json("07/09/10/data.json", [{
            "name": "both", "host.name": "literal", "host": {"name": "nested"},
            "user.id": "literal-user", "user": {"id": "nested-user"},
        }])
        scan_id = self.scan()["scan_id"]
        result = self.query(scan_id, filters={
            "host_name": {"operator": "equals", "value": "literal"},
            "user_id": {"operator": "equals", "value": "literal-user"},
        })
        self.assertEqual(result["total"], 1)
        result = self.query(scan_id, filters={
            "host_name": {"operator": "equals", "value": "nested"},
        })
        self.assertEqual(result["total"], 0)

    def test_numeric_start_between_compares_numbers_inclusively(self):
        self.write_json("07/09/10/data.json", [
            {"name": str(value), "start": value} for value in [2, 9, 10, 11, 100]
        ])
        result = self.query(self.scan()["scan_id"], filters={
            "start": {"operator": "between", "from": "9", "to": "11"},
        })
        self.assertEqual(set(self.record_names(result)), {"9", "10", "11"})

    def test_iso_start_between_compares_instants_with_offsets(self):
        self.write_json("07/09/10/data.json", [
            {"name": "before", "start": "2026-10-19T07:59:59Z"},
            {"name": "from", "start": "2026-10-19T16:00:00+08:00"},
            {"name": "middle", "start": "2026-10-19T08:30:00Z"},
            {"name": "to", "start": "2026-10-19T09:00:00Z"},
            {"name": "after", "start": "2026-10-19T09:00:01Z"},
            {"name": "invalid", "start": "unknown"},
        ])
        result = self.query(self.scan()["scan_id"], filters={
            "start": {"operator": "between", "from": "2026-10-19T08:00:00Z",
                      "to": "2026-10-19T17:00:00+08:00"},
        })
        self.assertEqual(set(self.record_names(result)), {"from", "middle", "to"})

    def test_schema_includes_each_object_and_array_level(self):
        self.write_json("07/09/10/data.json", [{
            "name": "schema", "host": {"name": "laptop"}, "host.name": "literal",
            "events": [{"metadata": {"level": "info"}}, {"metadata": {"level": 3}}],
            "empty": [], "nullable": None,
        }])
        scanned = self.scan()
        paths = {tuple(field["path"]) for field in scanned["fields"]}
        self.assertTrue({
            ("host",), ("host", "name"), ("host.name",),
            ("events",), ("events", "[]"), ("events", "[]", "metadata"),
            ("events", "[]", "metadata", "level"), ("empty",), ("nullable",),
        }.issubset(paths), paths)
        for field in scanned["fields"]:
            self.assertIsInstance(field["types"], list)
            self.assertGreater(field["count"], 0)
        result = self.query(scanned["scan_id"])
        self.assertEqual(result["items"][0]["record"]["events"][1]["metadata"]["level"], 3)

    def test_schema_handles_missing_null_and_nested_array_values(self):
        records = [
            {"name": "present", "optional": {"value": None},
             "matrix": [[{"value": 1}, {"value": "two"}], [None]]},
            {"name": "null", "optional": None, "matrix": []},
            {"name": "missing"},
        ]
        self.write_json("07/09/10/data.json", records)
        scanned = self.scan()
        fields = {tuple(field["path"]): field for field in scanned["fields"]}
        self.assertTrue({
            ("optional",), ("optional", "value"),
            ("matrix",), ("matrix", "[]"), ("matrix", "[]", "[]"),
            ("matrix", "[]", "[]", "value"),
        }.issubset(fields), fields)
        self.assertGreaterEqual(len(fields[("optional",)]["types"]), 2)
        self.assertGreaterEqual(len(fields[("matrix", "[]", "[]", "value")]["types"]), 2)
        result = self.query(scanned["scan_id"])
        returned = {item["record"]["name"]: item["record"] for item in result["items"]}
        self.assertEqual(returned, {record["name"]: record for record in records})

    def test_empty_dataset_and_no_matches_return_empty_results(self):
        scanned = self.scan()
        self.assertEqual(scanned["file_count"], 0)
        self.assertEqual(scanned["record_count"], 0)
        self.assertEqual(scanned["days"], [])
        self.assertIsNone(scanned["min_time"])
        self.assertIsNone(scanned["max_time"])
        result = self.query(scanned["scan_id"])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["items"], [])
        self.assertIsInstance(result["pages"], int)
        self.assertGreaterEqual(result["pages"], 0)

        self.write_json("07/09/10/data.json", [{"name": "exists", "user": {"id": "alice"}}])
        result = self.query(self.scan()["scan_id"], filters={
            "user_id": {"operator": "equals", "value": "not-found"},
        })
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["items"], [])

    def test_pagination_preserves_all_records_without_duplicates(self):
        self.write_json("07/09/10/data.json", [
            {"name": f"record-{index}", "start": index} for index in range(7)
        ])
        scan_id = self.scan()["scan_id"]
        results = [self.query(scan_id, page=page, page_size=3) for page in [1, 2, 3]]
        self.assertEqual([len(result["items"]) for result in results], [3, 3, 1])
        self.assertTrue(all(result["total"] == 7 and result["pages"] == 3 for result in results))
        identifiers = [item["id"] for result in results for item in result["items"]]
        self.assertEqual(len(set(identifiers)), 7)
        names = [name for result in results for name in self.record_names(result)]
        self.assertEqual(set(names), {f"record-{index}" for index in range(7)})
        self.assertEqual(results[0]["items"], self.query(scan_id, page=1, page_size=3)["items"])

    def test_wrappers_ndjson_and_malformed_files_keep_valid_records(self):
        self.write_json("07/09/10/array.json", [{"name": "array", "start": 1}])
        self.write_json("07/09/11/wrapped.json", {"records": [{"name": "wrapped", "start": 2}]})
        self.write_json("07/09/12/data-wrapper.json", {"data": [{"name": "data-wrapper", "start": 3}]})
        self.write_json("07/09/13/object.json", {"name": "object", "start": 4})
        ndjson = self.root / "07/09/14/lines.json"
        ndjson.parent.mkdir(parents=True)
        ndjson.write_text('{"name":"line-one","start":5}\ninvalid JSON\n'
                          '{"name":"line-two","start":6}\n', encoding="utf-8")
        (ndjson.parent / "broken.json").write_text('{"broken":', encoding="utf-8")
        scanned = self.scan()
        self.assertEqual(scanned["file_count"], 6)
        self.assertEqual(scanned["record_count"], 6)
        self.assertTrue(scanned["warnings"])
        result = self.query(scanned["scan_id"])
        self.assertEqual(set(self.record_names(result)), {
            "array", "wrapped", "data-wrapper", "object", "line-one", "line-two",
        })

    def test_single_record_identity_preserves_nested_envelope_named_arrays(self):
        expected = {}
        for key in ["events", "data", "items"]:
            record = {
                "name": key, "start": "2026-10-19T09:00:00Z",
                "host": {"name": "laptop"}, "user": {"id": "alice"},
                key: [{"value": "first"}, {"value": "second"}],
            }
            expected[key] = record
            self.write_json(f"07/09/10/{key}.json", record)
        scanned = self.scan()
        self.assertEqual(scanned["file_count"], 3)
        self.assertEqual(scanned["record_count"], 3)
        result = self.query(scanned["scan_id"])
        returned = {item["record"]["name"]: item["record"] for item in result["items"]}
        self.assertEqual(returned, expected)
        paths = {tuple(field["path"]) for field in scanned["fields"]}
        for key in expected:
            with self.subTest(key=key):
                self.assertIn((key, "[]", "value"), paths)

    def test_rescan_reflects_changed_and_deleted_files(self):
        changed = self.write_json("07/09/10/changed.json", [{"name": "old", "start": 1}])
        deleted = self.write_json("07/09/11/deleted.json", [{"name": "deleted", "start": 2}])
        first = self.scan()
        self.assertEqual(first["record_count"], 2)
        changed.write_text(json.dumps([{"name": "replacement-with-new-size", "start": 3}]))
        deleted.unlink()
        self.write_json("07/09/12/added.json", [{"name": "added", "start": 4}])
        second = self.scan()
        self.assertEqual(second["file_count"], 2)
        self.assertEqual(second["record_count"], 2)
        result = self.query(second["scan_id"])
        self.assertEqual(set(self.record_names(result)), {"replacement-with-new-size", "added"})

    def test_invalid_inputs_return_actionable_json_errors(self):
        for payload in [{}, {"root": str(self.root / "does-not-exist")}]:
            with self.subTest(scan=payload):
                status, result = self.request("POST", "/api/scan", payload)
                self.assertGreaterEqual(status, 400)
                self.assertLess(status, 500)
                self.assertIsInstance(result["error"], str)
        self.write_json("07/09/10/data.json", [{"name": "valid", "start": 1}])
        scan_id = self.scan()["scan_id"]
        for overrides in [
            {"scan_id": "nonexistent-scan"},
            {"time_from": [7, 24, 0]},
            {"time_from": [8, 0, 0], "time_to": [7, 23, 59]},
            {"page": 0},
            {"filters": {"start": {"operator": "between", "from": "3", "to": "1"}}},
        ]:
            with self.subTest(query=overrides):
                payload = {"scan_id": scan_id, "page": 1, "page_size": 50}
                payload.update(overrides)
                status, result = self.request("POST", "/api/query", payload)
                self.assertGreaterEqual(status, 400)
                self.assertLess(status, 500)
                self.assertIsInstance(result["error"], str)

    def test_cross_origin_post_and_static_traversal_are_rejected(self):
        self.write_json("07/09/10/data.json", [{"name": "private"}])
        status, result = self.request(
            "POST", "/api/scan", {"root": str(self.root)},
            headers={"Origin": "https://untrusted.example"},
        )
        self.assertEqual(status, 403, result)
        for path in ["/../server.py", "/%2e%2e/server.py", "/..%2fserver.py"]:
            with self.subTest(path=path):
                status, content = self.request("GET", path)
                self.assertIn(status, [400, 403, 404])
                self.assertNotIn(b"class ", content if isinstance(content, bytes) else b"")


if __name__ == "__main__":
    unittest.main()

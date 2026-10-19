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

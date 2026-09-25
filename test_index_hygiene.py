"""Synthetic index/privacy fixtures only; never scan the user's real roots."""

import copy
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

import cockpit


class IndexHygieneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cockpit-hygiene-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.root.mkdir()
        self.index = self.base / "index.json"
        self.cfg = copy.deepcopy(cockpit.DEFAULT_CONFIG)
        self.cfg["roots"] = [str(self.root)]
        for target, value in (("INDEX_FILE", str(self.index)),):
            patcher = patch.object(cockpit, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config_patch = patch.object(cockpit, "load_config", return_value=self.cfg)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.output_patch = patch.object(cockpit, "out")
        self.output_patch.start()
        self.addCleanup(self.output_patch.stop)
        cockpit._text_cache.clear()
        self.addCleanup(cockpit._text_cache.clear)

    def write(self, relative, content="# Normal document\nProject notes"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_scan_never_reads_private_files_and_preserves_docs_and_templates(self):
        private = ["token_cache.json", "config/settings.json", "auth.json", "credentials.txt",
                   "cookies.json", "settings.local.json", "secrets.toml", ".env", ".env.local"]
        public = ["README.md", "配置说明.md", "项目.json", "config/settings.example.json",
                  "config/README.md", "docs/token使用说明.md", "src/config.py"]
        for name in private:
            self.write(name, '{"token": "SYNTHETIC_PRIVATE_VALUE_123456789"}')
        for name in public:
            self.write(name)
        real_read = cockpit.read_head
        reads = []

        def record_read(path, *args, **kwargs):
            reads.append(str(Path(path).relative_to(self.root)).replace("\\", "/"))
            return real_read(path, *args, **kwargs)

        with patch.object(cockpit, "read_head", side_effect=record_read):
            cockpit.scan()
        data = json.loads(self.index.read_text(encoding="utf-8"))
        self.assertEqual(set(reads), set(public))
        self.assertEqual({e["rel"] for e in data["entries"]}, set(public))
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", self.index.read_text(encoding="utf-8"))
        self.assertEqual(data["index_policy"], cockpit.index_policy(self.cfg))

    def test_virtual_environments_are_pruned_by_name_and_markers(self):
        for path in (".env/README.md", ".venv/README.md", "venv/README.md",
                     "custom-python/README.md", "conda-runtime/README.md"):
            self.write(path)
        self.write("custom-python/pyvenv.cfg", "home = synthetic")
        (self.root / "conda-runtime" / "conda-meta").mkdir()
        keep = self.write("project/docs/environment.md")
        self.assertEqual(cockpit.walk_files(str(self.root), self.cfg), [str(keep)])
        self.assertEqual(cockpit.walk_files(str(self.root / "custom-python"), self.cfg), [])

    def test_recent_legacy_index_is_rebuilt_before_any_entries_are_returned(self):
        self.write("README.md")
        legacy = {"last_scan": time.time(), "config_roots": self.cfg["roots"],
                  "entries": [{"path": "token_cache.json", "snippet": "SYNTHETIC_LEGACY_VALUE"}]}
        self.index.write_text(json.dumps(legacy), encoding="utf-8")
        cockpit._text_cache["old"] = ((1, 1), "synthetic cached content")
        data = cockpit.load_index()
        self.assertEqual([entry["rel"] for entry in data["entries"]], ["README.md"])
        self.assertNotIn("SYNTHETIC_LEGACY_VALUE", json.dumps(data))
        self.assertEqual(cockpit._text_cache, {})
        self.assertNotIn("SYNTHETIC_LEGACY_VALUE", self.index.read_text(encoding="utf-8"))

    def test_legacy_index_without_auto_scan_returns_no_old_snippets(self):
        self.index.write_text(json.dumps({"last_scan": time.time(), "entries": [
            {"snippet": "SYNTHETIC_LEGACY_VALUE"}]}), encoding="utf-8")
        with patch.object(cockpit, "scan") as scan:
            data = cockpit.load_index(auto_scan=False)
        scan.assert_not_called()
        self.assertEqual(data["entries"], [])
        self.assertNotIn("SYNTHETIC_LEGACY_VALUE", json.dumps(data))

    def test_changed_exclusions_invalidate_an_otherwise_fresh_index(self):
        self.write("README.md")
        cockpit.scan()
        self.cfg["exclude_dir_names"].append("private-artifacts")
        with patch.object(cockpit, "scan", wraps=cockpit.scan) as scan:
            data = cockpit.load_index()
        scan.assert_called_once_with()
        self.assertEqual(data["index_policy"], cockpit.index_policy(self.cfg))

    def test_security_scan_checks_credentials_only_on_explicit_call_without_index_or_cache(self):
        synthetic_token = "SYNTHETIC_ONLY_TOKEN_0123456789"
        self.write("config/settings.json", json.dumps({"token": synthetic_token}))
        self.write(".env", "API_KEY=" + synthetic_token)
        self.write(".env.local", "API_KEY=" + synthetic_token)
        with patch.object(cockpit, "load_index", side_effect=AssertionError("must not load index")), \
                patch.object(cockpit, "get_text", side_effect=AssertionError("must not cache credentials")):
            findings = cockpit.security()
        self.assertEqual({finding["rel"] for finding in findings},
                         {"config/settings.json", ".env", ".env.local"})
        self.assertNotIn(synthetic_token, json.dumps(findings))
        self.assertFalse(self.index.exists())
        self.assertEqual(cockpit._text_cache, {})

    def test_generated_index_and_its_local_backups_are_not_documents(self):
        local_index = self.root / "cockpit" / "index.json"
        self.write("cockpit/index.json", "synthetic cache")
        self.write("cockpit/.backups/index.json", "synthetic legacy cache")
        self.write("cockpit/.backups/index-20260922.json", "synthetic legacy cache")
        keep = self.write("project/index.json", "normal project data")
        with patch.object(cockpit, "INDEX_FILE", str(local_index)):
            self.assertEqual(cockpit.walk_files(str(self.root), self.cfg), [str(keep)])

    def test_normal_preview_refuses_private_settings_without_reading_contents(self):
        private = self.write("config/settings.json", "SYNTHETIC_PRIVATE_VALUE")
        server = ThreadingHTTPServer(("127.0.0.1", 0), cockpit.Handler)
        worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
        worker.start()
        try:
            with patch.object(cockpit, "get_text") as cached_read, \
                    patch.object(cockpit, "read_full") as full_read:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                try:
                    connection.request("GET", "/api/file?path=" + quote(str(private), safe=""))
                    response = connection.getresponse()
                    body = response.read().decode("utf-8")
                    self.assertEqual(response.status, 403)
                    self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", body)
                finally:
                    connection.close()
                cached_read.assert_not_called()
                full_read.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

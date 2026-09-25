"""Toolbox checks; all desktop opens and process starts are mocked."""

import http.client
import json
import os
import socket
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import cockpit
import toolbox


class ToolboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="驾驶舱测试 ")
        self.root = Path(self.temp.name)
        self.tool = self.root / "中文 工具"
        self.tool.mkdir()
        self.launch = self.tool / "启动 工具.ps1"
        self.launch.write_text("# test fixture only", encoding="utf-8")
        self.registry_path = self.root / "tools.json"
        self.registry = {
            "toolbox_root": str(self.root),
            "categories": [{"id": "create", "name": "创作工具"}],
            "tools": [
                {"id": "test", "name": "中文工具", "category": "create",
                 "path": str(self.tool), "launch": str(self.launch),
                 "url": "http://localhost:8765", "expected_title": "测试工具", "tags": ["测试"]},
                {"id": "missing", "path": str(self.root / "已删除"),
                 "launch": None, "url": None},
            ],
        }
        self.registry_path.write_text(json.dumps(self.registry, ensure_ascii=False), encoding="utf-8-sig")
        self.registry_patch = patch.object(toolbox, "REGISTRY_FILE", self.registry_path)
        self.registry_patch.start()
        self.log_patch = patch.object(toolbox, "LOG_DIR", self.root / "launcher-logs")
        self.log_patch.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.registry_patch.stop)
        self.addCleanup(self.log_patch.stop)

    def test_catalog_preserves_metadata_and_missing_tools(self):
        with patch.object(toolbox, "is_running", return_value=False):
            result = toolbox.catalog()
        self.assertEqual(result["root"], str(self.root))
        self.assertEqual(result["categories"], self.registry["categories"])
        self.assertEqual(result["tools"][0]["tags"], ["测试"])
        self.assertTrue(result["tools"][0]["available"])
        self.assertTrue(result["tools"][0]["launchable"])
        self.assertFalse(result["tools"][1]["available"])
        self.assertFalse(result["tools"][1]["launchable"])
        self.assertEqual(result["tools"][0]["service_state"], "offline")
        self.assertFalse(result["tools"][0]["running"])
        self.assertEqual(result["tools"][1]["service_state"], "not_applicable")

    @contextmanager
    def local_service(self, body=b"<title>Test Tool</title>", *, status=200,
                      content_type="text/html; charset=utf-8", delay=0, headers=None):
        """Run only a disposable fixture on an OS-assigned loopback port."""
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                if delay:
                    time.sleep(delay)
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    for name, value in (headers or {}).items():
                        self.send_header(name, value)
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
        thread.start()
        tool = {**self.registry["tools"][0], "url": f"http://localhost:{server.server_port}",
                "expected_title": "test tool"}
        try:
            yield tool, requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_http_identity_confirms_running_without_starting(self):
        with self.local_service(b"<html><TITLE>My TEST&#32;TOOL workspace</TITLE></html>") as (tool, requests), \
                patch.object(toolbox, "load_registry", return_value={**self.registry, "tools": [tool]}), \
                patch.object(toolbox, "_start") as launch, \
                patch.object(toolbox.os, "startfile") as open_file:
            self.assertEqual(toolbox.service_status(tool), "running")
            result = toolbox.catalog()["tools"][0]
            self.assertTrue(result["running"])
            self.assertEqual(result["service_state"], "running")
            self.assertNotIn("title", result)
            self.assertNotIn("body", result)
            action = toolbox.action({"id": "test", "action": "launch"})
            self.assertTrue(action["online"])
            self.assertFalse(action["started"])
            self.assertEqual(requests, ["/", "/", "/"])
            launch.assert_not_called()
            open_file.assert_not_called()

    def test_other_service_is_conflict_and_cannot_launch(self):
        with self.local_service(b"<title>Different private page</title>") as (tool, requests), \
                patch.object(toolbox, "load_registry", return_value={**self.registry, "tools": [tool]}), \
                patch.object(toolbox, "_start") as launch:
            result = toolbox.catalog()["tools"][0]
            self.assertEqual(result["service_state"], "conflict")
            self.assertFalse(result["running"])
            self.assertNotIn("Different private page", json.dumps(result))
            with self.assertRaises(toolbox.ToolboxError) as error:
                toolbox.action({"id": "test", "action": "launch"})
            self.assertEqual(error.exception.status, 409)
            self.assertIn("其他服务", str(error.exception))
            launch.assert_not_called()

    def test_get_catalog_with_port_conflict_never_launches_tools(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), cockpit.Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
        thread.start()
        try:
            with self.local_service(b"<title>Another local application</title>") as (tool, requests), \
                    patch.object(toolbox, "load_registry", return_value={**self.registry, "tools": [tool]}), \
                    patch.object(toolbox, "_start") as launch, \
                    patch.object(toolbox.os, "startfile") as open_file:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                try:
                    connection.request("GET", "/api/tools")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    result = json.loads(response.read())["tools"][0]
                finally:
                    connection.close()
                self.assertEqual(result["service_state"], "conflict")
                self.assertFalse(result["running"])
                launch.assert_not_called()
                open_file.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_redirect_is_unknown_and_never_followed(self):
        with self.local_service(status=302, headers={"Location": "/target"}) as (tool, requests):
            self.assertEqual(toolbox.service_status(tool), "unknown")
            self.assertEqual(requests, ["/"])

    def test_http_identity_ignores_environment_proxies(self):
        with self.local_service() as (tool, requests), patch.dict(os.environ, {
                "HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
                "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "",
                "http_proxy": "http://127.0.0.1:1", "no_proxy": ""}):
            self.assertEqual(toolbox.service_status(tool), "running")
            self.assertEqual(requests, ["/"])

    def test_unidentified_http_responses_cannot_launch(self):
        cases = [
            {"body": b"<html>no title</html>"},
            {"body": b"<title>test tool"},
            {"body": b"<title>test tool</title>", "content_type": "text/plain"},
            {"status": 503},
            {"body": b" " * toolbox.SERVICE_RESPONSE_LIMIT + b"<title>test tool</title>"},
        ]
        for case in cases:
            with self.subTest(case=case.keys()), self.local_service(**case) as (tool, requests), \
                    patch.object(toolbox, "load_registry", return_value={**self.registry, "tools": [tool]}), \
                    patch.object(toolbox, "_start") as launch:
                self.assertEqual(toolbox.service_status(tool), "unknown")
                with self.assertRaises(toolbox.ToolboxError) as error:
                    toolbox.action({"id": "test", "action": "launch"})
                self.assertEqual(error.exception.status, 409)
                launch.assert_not_called()

    def test_slow_service_is_unknown_and_does_not_launch(self):
        with self.local_service(delay=0.2) as (tool, requests), \
                patch.object(toolbox, "SERVICE_TIMEOUT", 0.03), \
                patch.object(toolbox, "load_registry", return_value={**self.registry, "tools": [tool]}), \
                patch.object(toolbox, "_start") as launch:
            self.assertEqual(toolbox.service_status(tool), "unknown")
            with self.assertRaises(toolbox.ToolboxError) as error:
                toolbox.action({"id": "test", "action": "launch"})
            self.assertEqual(error.exception.status, 409)
            launch.assert_not_called()

    def test_service_without_expected_title_is_unknown(self):
        with self.local_service() as (tool, requests):
            tool.pop("expected_title")
            self.assertEqual(toolbox.service_status(tool), "unknown")
            self.assertEqual(requests, [])

    def test_closed_port_is_offline_and_nonlocal_urls_are_never_probed(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        tool = {"url": f"http://127.0.0.1:{port}", "expected_title": "test tool"}
        self.assertEqual(toolbox.service_status(tool), "offline")
        with patch.object(toolbox, "is_running") as probe:
            self.assertEqual(toolbox.service_status({"url": "https://example.com"}), "unknown")
            self.assertEqual(toolbox.service_status({"url": None}), "not_applicable")
            probe.assert_not_called()

    def test_action_rejects_unknown_id_and_client_path(self):
        with patch.object(toolbox, "_start") as launch:
            for body in ({"id": "../unknown", "action": "launch"},
                         {"id": "test", "action": "launch", "path": str(self.launch)},
                         {"id": "test", "action": "delete"}):
                with self.assertRaises(toolbox.ToolboxError):
                    toolbox.action(body)
            launch.assert_not_called()

    def test_missing_launch_and_folder_are_clear_errors(self):
        for action in ("launch", "folder"):
            with self.assertRaises(toolbox.ToolboxError) as error:
                toolbox.action({"id": "missing", "action": action})
            self.assertEqual(error.exception.status, 404)

    def test_registered_unicode_launch_uses_hidden_process_and_own_directory(self):
        with patch.object(toolbox, "is_running", return_value=False), \
                patch.object(toolbox.subprocess, "Popen") as popen:
            result = toolbox.action({"id": "test", "action": "launch"})
        self.assertTrue(result["started"])
        self.assertEqual(result["status"], "started")
        args, kwargs = popen.call_args
        self.assertEqual(args[0][-1], str(self.launch))
        self.assertEqual(kwargs["cwd"], str(self.tool))
        self.assertEqual(kwargs["creationflags"], toolbox.subprocess.CREATE_NO_WINDOW)
        self.assertEqual(kwargs["startupinfo"].wShowWindow, toolbox.subprocess.SW_HIDE)
        self.assertEqual(kwargs["stderr"], toolbox.subprocess.STDOUT)
        self.assertTrue(Path(result["log_path"]).is_file())

    def test_batch_launch_uses_cmd_and_registered_file_only(self):
        with patch.object(toolbox.subprocess, "Popen") as popen:
            toolbox._start(str(self.tool / "启动.cmd"))
        self.assertEqual(popen.call_args.args[0][1:], ["/d", "/c", str(self.tool / "启动.cmd")])
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.tool))

    def test_interactive_launcher_uses_visible_input_capable_console_only_on_action(self):
        self.registry["tools"][0]["launch_mode"] = "interactive"
        self.registry["tools"][0]["url"] = None
        self.registry_path.write_text(json.dumps(self.registry), encoding="utf-8")
        with patch.object(toolbox.subprocess, "Popen") as popen:
            toolbox.catalog()
            popen.assert_not_called()
            result = toolbox.action({"id": "test", "action": "launch"})
        self.assertTrue(result["interactive"])
        self.assertIsNone(result["url"])
        self.assertEqual(popen.call_args.kwargs["creationflags"], subprocess_flag := toolbox.subprocess.CREATE_NEW_CONSOLE)
        self.assertNotEqual(subprocess_flag, toolbox.subprocess.CREATE_NO_WINDOW)
        self.assertNotIn("stdin", popen.call_args.kwargs)
        self.assertNotIn("-WindowStyle", popen.call_args.args[0])

    def test_running_desktop_tool_reuses_native_launcher_instead_of_browser_url(self):
        self.registry["tools"][0]["open_mode"] = "desktop"
        self.registry_path.write_text(json.dumps(self.registry), encoding="utf-8")
        with patch.object(toolbox, "service_status", return_value="running"), \
                patch.object(toolbox, "_start") as launch:
            result = toolbox.action({"id": "test", "action": "launch"})
        launch.assert_called_once_with(str(self.launch), "test", interactive=False)
        self.assertTrue(result["desktop"])
        self.assertIsNone(result["url"])

    @unittest.skipUnless(sys.platform == "win32", "Windows command launching")
    def test_unicode_batch_fixture_runs_from_its_directory_without_a_console(self):
        # This disposable fixture writes one marker; it never launches a user tool.
        script = self.root / "启动 测试.cmd"
        marker = self.tool / "ready.txt"
        script.write_bytes((f'@echo off\r\nchcp 65001 >nul\r\ncd /d "{self.tool}" || exit /b 1\r\n'
                            '> ready.txt echo ready\r\necho stdout captured\r\n'
                            '>&2 echo stderr captured\r\n').encode("utf-8"))
        real_popen = toolbox.subprocess.Popen
        children = []

        def capture_process(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        with patch.object(toolbox.subprocess, "Popen", side_effect=capture_process):
            log_path = toolbox._start(str(script))
        self.assertEqual(children[0].wait(timeout=5), 0)
        self.assertEqual(marker.read_text(encoding="ascii").strip(), "ready")
        log = Path(log_path).read_text(encoding="ascii")
        self.assertIn("stdout captured", log)
        self.assertIn("stderr captured", log)

    def test_online_tool_returns_url_without_starting_again(self):
        with patch.object(toolbox, "service_status", return_value="running"), \
                patch.object(toolbox, "_start") as launch:
            result = toolbox.action({"id": "test", "action": "launch"})
        launch.assert_not_called()
        self.assertTrue(result["online"])
        self.assertFalse(result["started"])
        self.assertEqual(result["url"], "http://localhost:8765")

    def test_registered_folders_and_html_open_without_shell(self):
        with patch.object(toolbox.os, "startfile") as startfile:
            toolbox.action({"id": "test", "action": "folder"})
            startfile.assert_called_once_with(str(self.tool))
            startfile.reset_mock()
            toolbox.open_root()
            startfile.assert_called_once_with(str(self.root))
            startfile.reset_mock()
            html = str(self.tool / "工具.html")
            toolbox._start(html)
            startfile.assert_called_once_with(html, cwd=str(self.tool))

    def test_status_probes_only_loopback(self):
        with patch.object(toolbox.socket, "create_connection") as connect:
            for url in ("https://example.com", "file:///C:/test", "http://localhost.evil:80",
                        "http://localhost@evil:80", "http://127.0.0.1:bad"):
                self.assertFalse(toolbox.is_running(url))
            connect.assert_not_called()
            self.assertTrue(toolbox.is_running("http://localhost:8765"))
            connect.assert_called_once_with(("127.0.0.1", 8765), timeout=0.15)

    def test_path_boundaries_reject_sibling_prefix_and_parent_traversal(self):
        roots = [str(self.tool)]
        self.assertTrue(cockpit.path_in_roots(str(self.launch), roots))
        self.assertTrue(cockpit.path_in_roots(str(self.tool), roots))
        self.assertFalse(cockpit.path_in_roots(str(self.root / "中文 工具备份" / "secret.md"), roots))
        self.assertFalse(cockpit.path_in_roots(str(self.tool / ".." / "secret.md"), roots))

    @unittest.skipUnless(sys.platform == "win32", "Windows junction semantics")
    def test_junctions_are_not_indexed_twice_and_access_checks_resolve_targets(self):
        import _winapi
        source_root = self.root / "旧工作区"
        source_root.mkdir()
        own_file = source_root / "工作笔记.md"
        own_file.write_text("保留", encoding="utf-8")
        link = source_root / "旧工具路径"
        _winapi.CreateJunction(str(self.tool), str(link))
        try:
            entries = {entry.name: entry for entry in os.scandir(source_root)}
            self.assertTrue(entries[link.name].is_dir(follow_symlinks=False))
            files = cockpit.walk_files(str(source_root), cockpit.DEFAULT_CONFIG)
            self.assertEqual(files, [str(own_file)])
            self.assertIn(str(self.launch), cockpit.walk_files(str(self.tool), cockpit.DEFAULT_CONFIG))
            alias_file = str(link / self.launch.name)
            self.assertFalse(cockpit.path_in_roots(alias_file, [str(source_root)]))
            self.assertTrue(cockpit.path_in_roots(alias_file, [str(source_root), str(self.tool)]))
        finally:
            os.rmdir(link)

    def test_changing_index_roots_refreshes_a_recent_index(self):
        index_path = self.root / "index.json"
        old = {"last_scan": time.time(), "config_roots": ["old"], "entries": []}
        new = {"last_scan": time.time(), "config_roots": [str(self.tool)], "entries": []}
        index_path.write_text(json.dumps(old), encoding="utf-8")

        def rescan():
            index_path.write_text(json.dumps(new), encoding="utf-8")

        with patch.object(cockpit, "INDEX_FILE", str(index_path)), \
                patch.object(cockpit, "load_config", return_value={"roots": new["config_roots"]}), \
                patch.object(cockpit, "out"), \
                patch.object(cockpit, "scan", side_effect=rescan) as scan:
            self.assertEqual(cockpit.load_index(), new)
        scan.assert_called_once_with()

    def test_server_becomes_ready_without_scanning_documents(self):
        with patch.object(cockpit, "load_index") as load_index, \
                patch.object(cockpit, "ThreadingHTTPServer") as server, \
                patch.object(cockpit, "out"):
            server.return_value.serve_forever.side_effect = KeyboardInterrupt
            cockpit.serve(8766)
        load_index.assert_not_called()
        server.assert_called_once_with(("127.0.0.1", 8766), cockpit.Handler)
        server.return_value.serve_forever.assert_called_once_with()

    def test_http_actions_require_same_origin_json_and_ignore_no_paths(self):
        server = cockpit.ThreadingHTTPServer(("127.0.0.1", 0), cockpit.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port

        def request(path, body, *, origin=None, host=None, content_type="application/json", method="POST"):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            headers = {"Content-Type": content_type, "Host": host or f"127.0.0.1:{port}"}
            if origin is not None:
                headers["Origin"] = origin
            conn.request(method, path, json.dumps(body), headers)
            response = conn.getresponse()
            result = response.status, json.loads(response.read())
            conn.close()
            return result

        try:
            with patch.object(toolbox.os, "startfile") as startfile, \
                    patch.object(toolbox, "is_running", return_value=False):
                route = "/api/tools/action"
                body = {"id": "test", "action": "folder"}
                self.assertEqual(request(route, body, origin="https://evil.example")[0], 403)
                self.assertEqual(request(route, body, host=f"evil.example:{port}")[0], 403)
                self.assertEqual(request(route, body, origin="null")[0], 403)
                self.assertEqual(request(route, body, content_type="text/plain")[0], 415)
                self.assertEqual(request(route, {**body, "path": "C:/Windows"})[0], 400)
                self.assertEqual(request("/api/tools/folder", {"path": "C:/Windows"})[0], 400)
                startfile.assert_not_called()
                self.assertEqual(request(route, body, origin=f"http://127.0.0.1:{port}")[0], 200)
                startfile.assert_called_once_with(str(self.tool))
                self.assertEqual(request("/api/tools/folder", {})[0], 200)
                self.assertEqual(request("/api/tools", {}, host=f"evil.example:{port}", method="GET")[0], 403)
                self.assertEqual(request("/api/tools", {}, method="GET")[0], 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

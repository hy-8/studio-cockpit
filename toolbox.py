"""Local tool registry and explicitly registered desktop launch actions."""

import http.client
import json
import os
import re
import socket
import subprocess
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


REGISTRY_FILE = Path(__file__).with_name("tools.json")
LOG_DIR = Path(__file__).with_name("launcher-logs")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
LAUNCH_EXTENSIONS = {".bat", ".cmd", ".ps1", ".lnk", ".html", ".htm", ".exe"}
SERVICE_TIMEOUT = 0.5
SERVICE_RESPONSE_LIMIT = 64 * 1024


class ToolboxError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def load_registry():
    try:
        with REGISTRY_FILE.open(encoding="utf-8-sig") as stream:
            registry = json.load(stream)
        if not isinstance(registry, dict):
            raise ValueError("根节点必须是对象")
        if not isinstance(registry.get("toolbox_root"), str):
            raise ValueError("缺少 toolbox_root")
        if not isinstance(registry.get("categories"), list):
            raise ValueError("categories 必须是数组")
        if not isinstance(registry.get("tools"), list):
            raise ValueError("tools 必须是数组")
        ids = set()
        for tool in registry["tools"]:
            if not isinstance(tool, dict) or not isinstance(tool.get("id"), str):
                raise ValueError("工具必须有字符串 id")
            if not tool["id"] or tool["id"] in ids:
                raise ValueError("工具 id 为空或重复")
            if not isinstance(tool.get("path"), str) or not os.path.isabs(tool["path"]):
                raise ValueError(f"工具 {tool['id']} 的 path 必须是绝对路径")
            launch = tool.get("launch")
            if launch and (not isinstance(launch, str) or not os.path.isabs(launch)):
                raise ValueError(f"工具 {tool['id']} 的 launch 必须是绝对路径")
            ids.add(tool["id"])
        if not os.path.isabs(registry["toolbox_root"]):
            raise ValueError("toolbox_root 必须是绝对路径")
        return registry
    except (OSError, ValueError, TypeError) as exc:
        raise ToolboxError(f"工具清单读取失败：{exc}", 500) from exc


def local_endpoint(url):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https")
                or parsed.hostname not in LOOPBACK_HOSTS
                or parsed.username is not None or parsed.password is not None):
            return None
        return parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def is_running(url):
    """Check only whether the local port is reachable, not the service identity."""
    endpoint = local_endpoint(url)
    if endpoint is None:
        return False
    # Probe numeric loopback addresses only; never resolve a registry URL over DNS.
    hosts = ("127.0.0.1", "::1") if endpoint[0] == "localhost" else (endpoint[0],)
    for host in hosts:
        try:
            with socket.create_connection((host, endpoint[1]), timeout=0.15):
                return True
        except OSError:
            pass
    return False


class _PageTitle(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.in_title = False
        self.complete = False

    def handle_starttag(self, tag, attrs):
        if tag == "title" and not self.complete:
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title" and self.in_title:
            self.in_title = False
            self.complete = True

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)


def service_status(tool):
    """Identify a registered service without starting it or returning page data."""
    url = tool.get("url")
    if not url:
        return "not_applicable"
    endpoint = local_endpoint(url)
    if endpoint is None:
        return "unknown"
    if not is_running(url):
        return "offline"
    expected_title = tool.get("expected_title")
    if not isinstance(expected_title, str) or not expected_title.strip():
        return "unknown"
    expected_title = " ".join(expected_title.split()).casefold()
    parsed = urlsplit(url)
    hosts = ("127.0.0.1", "::1") if endpoint[0] == "localhost" else (endpoint[0],)
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    for host in hosts:
        # http.client connects directly: environment proxies and redirects are not used.
        connection = connection_type(host, endpoint[1], timeout=SERVICE_TIMEOUT)
        try:
            connection.request("GET", target, headers={
                "Host": parsed.netloc, "Accept": "text/html", "Connection": "close",
            })
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                return "unknown"
            if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                return "unknown"
            body = response.read(SERVICE_RESPONSE_LIMIT)
            parser = _PageTitle()
            parser.feed(body.decode(response.headers.get_content_charset() or "utf-8", errors="replace"))
            title = " ".join("".join(parser.parts).split())
            if not parser.complete or not title:
                return "unknown"
            return "running" if expected_title in title.casefold() else "conflict"
        except (OSError, http.client.HTTPException, ValueError, LookupError):
            continue
        finally:
            connection.close()
    return "unknown"


def catalog():
    registry = load_registry()
    tools = []
    for tool in registry["tools"]:
        launch = tool.get("launch")
        state = service_status(tool)
        tools.append({**tool,
                      "available": os.path.isdir(tool["path"]),
                      "launchable": bool(launch and os.path.isfile(launch)),
                      "service_state": state,
                      "running": state == "running"})
    return {"root": registry["toolbox_root"],
            "categories": registry["categories"], "tools": tools}


def check_request(headers, server_port, *, post=False):
    """Reject foreign Host/Origin values before any local filesystem action."""
    host = headers.get("Host", "")
    try:
        parsed = urlsplit("http://" + host)
        valid_host = (parsed.hostname in LOOPBACK_HOSTS
                      and (parsed.port or 80) == server_port
                      and not parsed.username and not parsed.password
                      and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        valid_host = False
    if not valid_host:
        raise ToolboxError("仅允许本机地址访问工具箱", 403)
    origin = headers.get("Origin")
    if origin is not None and origin.lower() != ("http://" + host).lower():
        raise ToolboxError("工具箱操作必须来自当前页面，已拒绝跨站请求", 403)
    if headers.get("Sec-Fetch-Site") == "cross-site":
        raise ToolboxError("已拒绝跨站工具箱请求", 403)
    if post and headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ToolboxError("工具箱操作需要 application/json 请求", 415)


def _open_folder(path):
    if not os.path.isdir(path):
        raise ToolboxError("工具文件夹不存在，请检查工具清单中的路径", 404)
    os.startfile(os.path.abspath(path))
    return {"ok": True, "status": "opened"}


def open_root():
    return _open_folder(load_registry()["toolbox_root"])


def _start(launch, tool_id=None, *, interactive=False):
    path = os.path.abspath(launch)
    directory = os.path.dirname(path)
    extension = Path(path).suffix.lower()
    if extension not in LAUNCH_EXTENSIONS:
        raise ToolboxError("此启动文件类型暂不支持，请打开工具文件夹启动", 422)
    if extension in {".lnk", ".html", ".htm", ".exe"}:
        os.startfile(path, cwd=directory)
        return
    if extension == ".ps1":
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass"]
        if not interactive:
            command += ["-WindowStyle", "Hidden"]
        command += ["-File", path]
    else:
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", path]
    if interactive:
        # Registered terminal tools require user input and an explicit closeable window.
        subprocess.Popen(command, cwd=directory, creationflags=subprocess.CREATE_NEW_CONSOLE)
        return None
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    LOG_DIR.mkdir(exist_ok=True)
    log_name = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_id or Path(path).stem)[:80]
    log_path = LOG_DIR / f"{log_name}-{datetime.now():%Y%m%d-%H%M%S-%f}.log"
    with log_path.open("ab", buffering=0) as log:
        subprocess.Popen(command, cwd=directory, startupinfo=startup,
                         creationflags=subprocess.CREATE_NO_WINDOW,
                         stdin=subprocess.DEVNULL, stdout=log,
                         stderr=subprocess.STDOUT)
    return str(log_path)


def action(body):
    if not isinstance(body, dict) or set(body) != {"id", "action"}:
        raise ToolboxError("请只提供已注册的工具 id 和 action")
    if not isinstance(body["id"], str) or body["action"] not in ("launch", "folder"):
        raise ToolboxError("无效的工具 id 或 action")
    registry = load_registry()
    tool = next((tool for tool in registry["tools"] if tool["id"] == body["id"]), None)
    if tool is None:
        raise ToolboxError("工具未登记，无法执行操作", 404)
    if body["action"] == "folder":
        return _open_folder(tool["path"])
    url = tool.get("url") if local_endpoint(tool.get("url")) else None
    state = service_status(tool)
    desktop = tool.get("open_mode") == "desktop"
    if state == "running" and not desktop:
        return {"ok": True, "status": "online", "started": False, "online": True, "url": url}
    if state == "conflict":
        raise ToolboxError("工具端口正被其他服务占用，未启动工具；请检查端口配置或占用情况", 409)
    if state == "unknown":
        raise ToolboxError("无法确认此端口上的服务身份，未启动工具；请稍后刷新状态或检查服务配置", 409)
    launch = tool.get("launch")
    if not launch or not os.path.isfile(launch):
        raise ToolboxError("启动文件不存在或尚未配置，请打开工具文件夹查看", 404)
    interactive = tool.get("launch_mode") == "interactive"
    log_path = _start(launch, tool["id"], interactive=interactive)
    return {"ok": True, "status": "started", "started": True, "online": False,
            "url": None if desktop or interactive else url, "log_path": log_path,
            "interactive": interactive, "desktop": desktop}

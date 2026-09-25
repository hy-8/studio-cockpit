# -*- coding: utf-8 -*-
"""
Local Project Cockpit
Scan user-configured workspace roots and turn scattered notes and project records into a searchable local cockpit.

零第三方依赖，纯 Python 标准库。Python 3.10+。

命令：
  python cockpit.py scan                 建立索引
  python cockpit.py search 关键词        全文搜索
  python cockpit.py status               项目状态总览
  python cockpit.py recent [天数]        最近 N 天在做什么（默认 14）
  python cockpit.py knowledge            方法/拆解/避坑类知识文档清单
  python cockpit.py security             扫描明文密钥
  python cockpit.py pack 项目前缀 [--out 文件] [--budget 字符数]   生成上下文包
  python cockpit.py serve [--port 8766]  启动网页版
"""

import argparse
import json
import os
import re
import stat
import sys
import threading
import tempfile
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import toolbox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(BASE_DIR, "index.json")
CONFIG_FILE = os.path.join(BASE_DIR, "驾驶舱配置.json")
PACK_DIR = os.path.join(BASE_DIR, "context-packs")
UI_FILE = os.path.join(BASE_DIR, "ui.html")
INDEX_POLICY_VERSION = 1
VIRTUAL_ENV_DIRS = {".env", ".venv", "venv", "virtualenv", ".virtualenv"}
PRIVATE_CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".txt"}
PRIVATE_CONFIG_STEMS = {
    "auth", "credentials", "credential", "cookies", "cookie", "secrets", "secret",
    "token", "tokens", "token_cache", "token-cache", "access_token", "refresh_token",
    "session", "session_cache", "config", "settings", "config.local", "settings.local",
    "config.private", "settings.private", "config.production", "settings.production",
}

DEFAULT_CONFIG = {
    "roots": [],
    "port": 8766,
    "max_file_mb": 2.0,
    "max_entries": 30000,
    "stalled_days": 10,
    "exclude_dir_names": [
        "node_modules", ".git", "__pycache__", "site-packages", ".venv", "venv",
        ".pytest_cache", ".cache", ".obsidian", "dist", "build", "downloads",
        "models", ".tools", ".workbuddy", ".codex", ".claude", ".agents",
        ".ai-bridge", "blender-4.5.11-windows-x64", "launcher-logs", "logs",
        "cookies", "static", "target", ".pytest_cache", "generations",
    ],
    "exclude_path_prefixes": [],
    "text_exts": [".md", ".txt", ".srt", ".vtt", ".csv", ".json", ".py", ".mjs"],
    "doc_exts": [".md", ".txt", ".srt"],
}

TAG_RULES = [
    ("script", ["口播稿", "文稿", "台词", "旁白", "script"]),
    ("plan", ["计划", "分镜", "镜头", "大纲", "prd", "brief", "方案"]),
    ("log", ["制作记录", "制作说明", "交付说明", "开发文档", "使用说明", "记录"]),
    ("method", ["技巧", "模板", "规则", "方法", "指南", "手册", "复用"]),
    ("issues", ["问题", "避坑", "troubleshooting", "修复"]),
    ("qa", ["验收", "检查", "验证", "checklist"]),
    ("prompt", ["提示词", "prompt"]),
    ("analysis", ["拆解", "测评", "评测", "横评", "分析"]),
    ("summary", ["总结", "汇总", "周报", "报告", "收官"]),
]

PENDING_CUES = ["待办", "待确认", "待定", "未完成", "未成片", "未导出", "未确认",
                "进行中", "todo", "wip", "阻塞", "停滞", "下一步", "draft", "待处理"]
DONE_CUES = ["已完成", "完成", "done", "成片", "交付", "final"]

SECURITY_PATTERNS = [
    ("sk- 开头的 API Key", re.compile(r"sk-[A-Za-z0-9_\-]{16,}")),
    ("明文 api_key/secret/token 赋值", re.compile(
        r"(?i)(api[_-]?key|apikey|secret|token|password)[\"']?\s*[=:]\s*[\"']?[A-Za-z0-9_\-\.]{16,}")),
    ("Bearer 令牌", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}")),
]

_text_cache = {}


def out(s):
    print(s)


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                cfg[k] = v
        except Exception as e:
            out(f"[警告] 配置文件读取失败，使用默认配置: {e}")
    return cfg


def read_head(path, head_bytes=8192):
    """读文件头部，返回 (文本或None)。None 表示跳过（二进制/读取失败）。"""
    try:
        with open(path, "rb") as f:
            raw = f.read(head_bytes)
        if not raw or b"\x00" in raw:
            return None
        for enc in ("utf-8", "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


def read_full(path, cap):
    try:
        size = os.path.getsize(path)
        if size > cap:
            return None
        with open(path, "rb") as f:
            raw = f.read()
        if b"\x00" in raw[:8192]:
            return None
        for enc in ("utf-8", "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


def get_text(path, cap):
    """带缓存的全文读取；文件变了会重读。cap 为字节数。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = path
    if key in _text_cache:
        cached_st, text = _text_cache[key]
        if cached_st == (st.st_mtime, st.st_size):
            return text
    text = read_full(path, cap)
    if text is not None:
        _text_cache[key] = ((st.st_mtime, st.st_size), text)
        if len(_text_cache) > 4000:
            _text_cache.pop(next(iter(_text_cache)))
    return text


def extract_title(head, name, ext=""):
    if ext == ".json":
        return name  # json 首行往往是原始数据片段，用文件名更清晰
    for line in head.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            s = s.lstrip("#").strip()
        return s[:80] or name
    return name


def classify(name, rel, head, is_doc):
    low = (name + " " + rel).lower()
    tags = []
    for tag, kws in TAG_RULES:
        if any(kw.lower() in low for kw in kws):
            tags.append(tag)
    status = None
    # 状态线索只看文档与项目.json，代码/转写文件噪音太大
    if is_doc or name in ("项目.json", "项目状态.json"):
        low_head = head[:2048].lower() if head else ""
        if any(c in low_head or c in low for c in PENDING_CUES):
            status = "pending"
        if any(c in low_head for c in DONE_CUES):
            status = status or "done"
    base = os.path.basename(name)
    if base in ("项目.json", "项目状态.json") and head:
        try:
            data = json.loads(head)
            st = str(data.get("status") or data.get("状态") or "")
            if st in ("finalize", "verified", "finalized", "完成"):
                status = "done"
            elif st:
                status = "pending"
        except Exception:
            pass
    return tags, status


def project_of(root_name, rel_parts):
    parts = [p for p in rel_parts if p]
    if len(parts) >= 2:
        return f"{root_name}/{parts[0]} / {parts[1]}"
    if parts:
        return f"{root_name}/{parts[0]}"
    return root_name


def rel_prefix_match(rel_norm, prefixes):
    return any(rel_norm.startswith(p.lower().replace("\\", "/")) for p in prefixes)


def private_document_path(path):
    """Keep known credentials/private settings out of ordinary document surfaces."""
    parts = str(path).replace("\\", "/").lower().split("/")
    name = parts[-1]
    if any(part in VIRTUAL_ENV_DIRS for part in parts[:-1]):
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    stem, ext = os.path.splitext(name)
    return ext in PRIVATE_CONFIG_EXTS and stem in PRIVATE_CONFIG_STEMS


def index_artifact_path(path):
    """Do not index the index itself, including its local backup copies."""
    resolved = os.path.normcase(os.path.abspath(path))
    index_path = os.path.normcase(os.path.abspath(INDEX_FILE))
    if resolved == index_path:
        return True
    parent = os.path.dirname(index_path)
    try:
        inside = os.path.commonpath([resolved, parent]) == parent
    except ValueError:
        return False
    name = os.path.basename(resolved).lower()
    return inside and (name == "index.json" or name.startswith("index.json.") or
                       (name.startswith("index-") and name.endswith(".json")))


def index_policy(cfg):
    return {"version": INDEX_POLICY_VERSION,
            **{key: cfg[key] for key in ("exclude_dir_names", "exclude_path_prefixes",
                                        "text_exts", "doc_exts", "max_file_mb", "max_entries")}}


def walk_files(root, cfg, *, include_private=False):
    """带剪枝的快速遍历，返回绝对路径列表。"""
    excl_dirs = set(d.lower() for d in cfg["exclude_dir_names"]) | VIRTUAL_ENV_DIRS
    excl_prefixes = [p.lower().replace("\\", "/") for p in cfg["exclude_path_prefixes"]]
    root_abs = os.path.abspath(root)
    if (os.path.basename(root_abs).lower() in VIRTUAL_ENV_DIRS or
            os.path.isfile(os.path.join(root_abs, "pyvenv.cfg")) or
            os.path.isdir(os.path.join(root_abs, "conda-meta"))):
        return []
    found = []
    stack = [root_abs]
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for e in entries:
            p = e.path
            rel = os.path.relpath(p, root_abs).replace("\\", "/")
            if rel_prefix_match(rel.lower(), excl_prefixes):
                continue
            try:
                # Windows junctions are reparse points, not necessarily symlinks.
                # Index their real toolbox location once, not the compatibility path.
                attributes = getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0)
                if e.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    continue
            except OSError:
                continue
            if e.is_dir(follow_symlinks=False):
                # Marker checks catch virtual environments with custom directory names.
                environment = (os.path.isfile(os.path.join(p, "pyvenv.cfg")) or
                               os.path.isdir(os.path.join(p, "conda-meta")))
                if e.name.lower() not in excl_dirs and not environment:
                    stack.append(p)
                continue
            if e.is_file(follow_symlinks=False):
                if index_artifact_path(p) or (not include_private and private_document_path(p)):
                    continue
                found.append(p)
                if len(found) > cfg["max_entries"] * 6:
                    return found
    return found


def scan():
    cfg = load_config()
    max_bytes = int(cfg["max_file_mb"] * 1024 * 1024)
    text_exts = set(cfg["text_exts"])
    doc_exts = set(cfg["doc_exts"])
    entries = []
    t0 = time.time()
    for root in cfg["roots"]:
        root = root.replace("\\", "/").rstrip("/")
        root_name = os.path.basename(root) or root
        if not os.path.isdir(root):
            out(f"[跳过] 根目录不存在: {root}")
            continue
        files = walk_files(root, cfg)
        for p in files:
            ext = os.path.splitext(p)[1].lower()
            if ext not in text_exts:
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_size > max_bytes or st.st_size == 0:
                continue
            name = os.path.basename(p)
            # 跳过 agent 工具调用留痕类 json
            if ext == ".json" and (name.startswith("last_") or st.st_size > 512 * 1024):
                continue
            head = read_head(p)
            if head is None:
                continue
            rel = os.path.relpath(p, root).replace("\\", "/")
            rel_norm = rel.lower()
            rel_parts = rel.split("/")[:-1]
            is_doc = ext in doc_exts
            tags, status = classify(name, rel, head, is_doc)
            first1k = head[:1024]
            entries.append({
                "path": p.replace("\\", "/"),
                "root": root_name,
                "rel": rel,
                "project": project_of(root_name, rel_parts),
                "name": name,
                "ext": ext,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "title": extract_title(head, name, ext),
                "snippet": re.sub(r"\s+", " ", first1k)[:200],
                "tags": tags,
                "status": status,
                "is_doc": ext in doc_exts,
            })
            if len(entries) >= cfg["max_entries"]:
                out(f"[警告] 达到条目上限 {cfg['max_entries']}，索引提前结束")
                break
        if len(entries) >= cfg["max_entries"]:
            break
    payload = {
        "last_scan": time.time(),
        "config_roots": cfg["roots"],
        "index_policy": index_policy(cfg),
        "entries": entries,
    }
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(INDEX_FILE),
                                         prefix=".index-", suffix=".tmp", delete=False) as f:
            temporary_path = f.name
            json.dump(payload, f, ensure_ascii=False)
        os.replace(temporary_path, INDEX_FILE)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)
    out(f"[完成] 索引 {len(entries)} 个文本文件，用时 {time.time() - t0:.1f}s → {INDEX_FILE}")


def load_index(auto_scan=True, max_age_hours=48):
    cfg = load_config()
    if os.path.exists(INDEX_FILE):
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            age_h = (time.time() - data.get("last_scan", 0)) / 3600
            roots_changed = data.get("config_roots") != cfg["roots"]
            policy_changed = data.get("index_policy") != index_policy(cfg)
            if (roots_changed or policy_changed) and not auto_scan:
                return {"last_scan": 0, "config_roots": cfg["roots"],
                        "index_policy": index_policy(cfg), "entries": []}
            if auto_scan and (age_h > max_age_hours or roots_changed or policy_changed):
                reason = ("索引规则已更新" if policy_changed else "索引目录已变更" if roots_changed
                          else f"索引已 {age_h:.0f} 小时未更新")
                out(f"[提示] {reason}，自动重新扫描…")
                _text_cache.clear()
                scan()
                with open(INDEX_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            return data
        except Exception as e:
            out(f"[警告] 索引损坏，重新扫描: {e}")
    scan()
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def search(query, limit=30):
    data = load_index()
    terms = [t.lower() for t in query.split() if t.strip()]
    if not terms:
        return []
    results = []
    for e in data["entries"]:
        hay_name = e["name"].lower()
        hay_title = e["title"].lower()
        score = 0
        for t in terms:
            hit = False
            if t in hay_name:
                score += 100
                hit = True
            if t in hay_title and t not in hay_name:
                score += 50
                hit = True
            if any(t in tag for tag in e["tags"]):
                score += 20
                hit = True
            if not hit:
                text = get_text(e["path"], 2 * 1024 * 1024)
                if text and t in text.lower():
                    score += 3 + min(text.lower().count(t), 10)
                else:
                    score = -1
                    break
        if score > 0:
            results.append((score, e))
    results.sort(key=lambda x: (-x[0], -x[1]["mtime"]))
    out_list = []
    for score, e in results[:limit]:
        snippet = e["snippet"]
        text = get_text(e["path"], 2 * 1024 * 1024)
        if text:
            low = text.lower()
            for t in terms:
                i = low.find(t)
                if i >= 0:
                    a = max(0, i - 60)
                    snippet = ("…" if a else "") + re.sub(r"\s+", " ", text[a:i + 120]) + "…"
                    break
        out_list.append({**e, "score": score, "match": snippet})
    return out_list


def project_stats():
    data = load_index()
    cfg = load_config()
    now = time.time()
    stalled_days = cfg["stalled_days"]
    projects = {}
    for e in data["entries"]:
        pj = projects.setdefault(e["project"], {
            "project": e["project"], "root": e["root"], "files": 0, "docs": 0,
            "last_mtime": 0, "pending": [], "done": 0, "tags": {},
        })
        pj["files"] += 1
        if e["is_doc"]:
            pj["docs"] += 1
        pj["last_mtime"] = max(pj["last_mtime"], e["mtime"])
        if e["status"] == "pending":
            pj["pending"].append(e["rel"])
        elif e["status"] == "done":
            pj["done"] += 1
        for t in e["tags"]:
            pj["tags"][t] = pj["tags"].get(t, 0) + 1
    result = []
    for pj in projects.values():
        age_days = (now - pj["last_mtime"]) / 86400
        pj["last_mtime_str"] = datetime.fromtimestamp(pj["last_mtime"]).strftime("%Y-%m-%d %H:%M")
        pj["age_days"] = round(age_days, 1)
        pj["stalled"] = bool(pj["pending"]) and age_days > stalled_days
        pj["pending"] = pj["pending"][:8]
        top_tags = sorted(pj["tags"].items(), key=lambda x: -x[1])[:4]
        pj["tag_str"] = " ".join(f"{t}×{c}" for t, c in top_tags)
        del pj["tags"]
        result.append(pj)
    result.sort(key=lambda x: -x["last_mtime"])
    return result


def recent(days=14):
    data = load_index()
    since = time.time() - days * 86400
    buckets = {}
    items = []
    for e in data["entries"]:
        if e["mtime"] < since:
            continue
        d = datetime.fromtimestamp(e["mtime"]).strftime("%Y-%m-%d")
        b = buckets.setdefault(d, {"date": d, "files": 0, "docs": 0, "roots": {}})
        b["files"] += 1
        if e["is_doc"]:
            b["docs"] += 1
        b["roots"][e["root"]] = b["roots"].get(e["root"], 0) + 1
        items.append(e)
    days_list = [buckets[k] for k in sorted(buckets, reverse=True)]
    items.sort(key=lambda x: -x["mtime"])
    return {"days": days_list, "latest": items[:60], "total": len(items)}


def knowledge():
    data = load_index()
    groups = {}
    want = {"method": "方法/技巧/模板", "analysis": "拆解/测评", "issues": "问题/避坑",
            "qa": "验收/检查", "plan": "计划/分镜", "summary": "总结/周报"}
    for e in data["entries"]:
        for t in e["tags"]:
            if t in want:
                groups.setdefault(t, []).append(e)
    res = {}
    for t, label in want.items():
        lst = sorted(groups.get(t, []), key=lambda x: -x["mtime"])[:200]
        res[t] = {"label": label, "items": lst}
    return res


def security():
    # Explicit security inspection is separate from document indexing. Never cache
    # full credential contents or persist their snippets in index.json.
    cfg = load_config()
    cap = int(cfg["max_file_mb"] * 1024 * 1024)
    findings = []
    seen = set()
    candidates = []
    for root in cfg["roots"]:
        for path in walk_files(root, cfg, include_private=True):
            candidates.append({"path": path.replace("\\", "/"),
                               "rel": os.path.relpath(path, root).replace("\\", "/"),
                               "root": os.path.basename(root.replace("\\", "/").rstrip("/")),
                               "ext": os.path.splitext(path)[1].lower()})
    for e in candidates:
        if e["ext"] not in (".txt", ".env", ".json", ".md", ".py", ".mjs", ".cfg", ".ini",
                            ".toml", ".yaml", ".yml", ".conf", "") and \
                not os.path.basename(e["path"]).lower().startswith(".env."):
            continue
        text = read_full(e["path"], cap)
        if not text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pat in SECURITY_PATTERNS:
                m = pat.search(line)
                if m:
                    val = m.group(0)
                    masked = val[:8] + "***" + val[-4:] if len(val) > 14 else "***"
                    key = (e["path"], lineno, label)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append({
                        "path": e["path"], "rel": e["rel"], "root": e["root"],
                        "line": lineno, "type": label, "masked": masked,
                    })
                    break
            if len(findings) >= 200:
                return findings
    return findings


def pack(prefix, out_file=None, budget=48000):
    data = load_index()
    pl = prefix.lower().replace("\\", "/")
    matches = [e for e in data["entries"]
               if e["is_doc"] and (pl in e["rel"].lower().replace("\\", "/")
                                   or pl in e["project"].lower())]
    if not matches:
        out(f"[未找到] 没有匹配「{prefix}」的文档")
        return None
    prio = {"method": 0, "issues": 1, "plan": 2, "log": 3, "qa": 4, "summary": 5,
            "script": 6, "prompt": 7, "analysis": 8}
    def sort_key(e):
        tag_prios = [prio.get(t, 99) for t in e["tags"]]
        if tag_prios:
            first_tag = min(tag_prios)
        elif e["ext"] in (".srt", ".vtt"):
            first_tag = 50  # 字幕排在文档之后、无标签杂项之前
        else:
            first_tag = 90
        return (first_tag, -e["mtime"])
    matches.sort(key=sort_key)
    project_name = matches[0]["project"].replace("/", "-").replace(" ", "_")
    if not out_file:
        os.makedirs(PACK_DIR, exist_ok=True)
        out_file = os.path.join(
            PACK_DIR, f"{project_name}_{datetime.now().strftime('%Y%m%d_%H%M')}.md")
    used = 0
    parts = [f"# 上下文包：{matches[0]['project']}",
             f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}　"
             f"来源：工作室驾驶舱　字符预算：{budget}",
             ""]
    included = []
    for e in matches:
        text = get_text(e["path"], 1024 * 1024)
        if not text:
            continue
        chunk = f"\n\n---\n\n## {e['rel']}\n\n{text.strip()}"
        if used + len(chunk) > budget:
            chunk = chunk[:max(0, budget - used)] + "\n\n[已截断：超出字符预算]"
            parts.append(chunk)
            included.append((e["rel"], len(chunk)))
            used += len(chunk)
            break
        parts.append(chunk)
        included.append((e["rel"], len(chunk)))
        used += len(chunk)
    header_toc = "\n".join(f"- {r}（{c} 字符）" for r, c in included)
    parts.insert(3, f"\n## 包含文件\n\n{header_toc}\n")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    out(f"[完成] 上下文包：{out_file}")
    out(f"       包含 {len(included)} 个文档，共 {used} 字符")
    return out_file


def doctor():
    cfg = load_config()
    ok = True
    out(f"Python: {sys.version.split()[0]}（需要 3.10+）")
    for r in cfg["roots"]:
        exists = os.path.isdir(r)
        ok = ok and exists
        out(f"根目录 {'✓' if exists else '✗'} {r}")
    if os.path.exists(INDEX_FILE):
        age = (time.time() - os.path.getmtime(INDEX_FILE)) / 3600
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            n = len(json.load(f).get("entries", []))
        out(f"索引: {n} 条，{age:.1f} 小时前建立")
    else:
        out("索引: 尚未建立（运行 python cockpit.py scan）")
    out(f"配置: {CONFIG_FILE}")
    return ok


def project_files(name):
    data = load_index()
    items = [e for e in data["entries"] if e["project"] == name]
    items.sort(key=lambda x: -x["mtime"])
    return items[:500]


def day_files(date_str):
    data = load_index()
    items = [e for e in data["entries"]
             if datetime.fromtimestamp(e["mtime"]).strftime("%Y-%m-%d") == date_str]
    items.sort(key=lambda x: -x["mtime"])
    return {"total": len(items), "items": items[:300]}


# ---------------- Web ----------------

def path_in_roots(path, roots):
    """Compare resolved path components, including toolbox compatibility junctions."""
    candidate = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    for root in roots:
        resolved_root = os.path.normcase(os.path.realpath(os.path.abspath(root)))
        try:
            if os.path.commonpath([candidate, resolved_root]) == resolved_root:
                return True
        except ValueError:  # Different drives cannot share a root.
            continue
    return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if path == "/":
                with open(UI_FILE, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path in ("/toolbox.css", "/toolbox.js"):
                ctype = "text/css" if path.endswith(".css") else "text/javascript"
                with open(os.path.join(BASE_DIR, path[1:]), "rb") as f:
                    return self._send(200, f.read(), ctype + "; charset=utf-8")
            if path == "/api/tools":
                toolbox.check_request(self.headers, self.server.server_port)
                return self._send(200, json.dumps(toolbox.catalog(), ensure_ascii=False).encode())
            if path == "/api/overview":
                projs = project_stats()
                data = load_index()
                per_root = {}
                for e in data["entries"]:
                    per_root[e["root"]] = per_root.get(e["root"], 0) + 1
                return self._send(200, json.dumps({
                    "last_scan": data.get("last_scan"),
                    "total": len(data["entries"]),
                    "per_root": per_root,
                    "projects": projs,
                }, ensure_ascii=False).encode())
            if path == "/api/search":
                return self._send(200, json.dumps(
                    {"results": search(q.get("q", ""), int(q.get("limit", 30)))},
                    ensure_ascii=False).encode())
            if path == "/api/recent":
                return self._send(200, json.dumps(
                    recent(int(q.get("days", 14))), ensure_ascii=False).encode())
            if path == "/api/knowledge":
                return self._send(200, json.dumps(knowledge(), ensure_ascii=False).encode())
            if path == "/api/security":
                return self._send(200, json.dumps(
                    {"findings": security()}, ensure_ascii=False).encode())
            if path == "/api/file":
                p = os.path.abspath(unquote(q.get("path", "")))
                cfg = load_config()
                inside = path_in_roots(p, cfg["roots"])
                if (not inside or not os.path.isfile(p) or private_document_path(p)
                        or index_artifact_path(p)):
                    return self._send(403, json.dumps(
                        {"error": "此路径不提供普通文档预览"}, ensure_ascii=False).encode())
                text = get_text(p, 2 * 1024 * 1024) or read_full(p, 2 * 1024 * 1024) or ""
                return self._send(200, json.dumps(
                    {"path": p, "text": text[:120000]}, ensure_ascii=False).encode())
            if path == "/api/project":
                return self._send(200, json.dumps(
                    {"project": q.get("name", ""), "items": project_files(q.get("name", ""))},
                    ensure_ascii=False).encode())
            if path == "/api/day":
                return self._send(200, json.dumps(
                    day_files(q.get("date", "")), ensure_ascii=False).encode())
            return self._send(404, b'{"error":"not found"}')
        except toolbox.ToolboxError as e:
            return self._send(e.status, json.dumps({"error": str(e)}, ensure_ascii=False).encode())
        except Exception as e:
            return self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False).encode())

    def do_POST(self):
        path = urlparse(self.path).path
        if path in ("/api/tools/action", "/api/tools/folder"):
            return self._toolbox_post(path)
        if path not in ("/api/pack", "/api/rescan", "/api/open"):
            return self._send(404, b'{"error":"not found"}')
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) or b"{}"
            body = {}
            for enc in ("utf-8", "gbk"):
                try:
                    body = json.loads(raw.decode(enc))
                    break
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = {}
            if path == "/api/pack":
                out_path = pack(body.get("project", ""), budget=int(body.get("budget", 48000)))
                payload = {"path": out_path} if out_path else {"error": "未匹配到项目"}
                return self._send(200, json.dumps(payload, ensure_ascii=False).encode())
            if path == "/api/rescan":
                threading.Thread(target=scan, daemon=True).start()
                return self._send(200, b'{"started": true}')
            if path == "/api/open":
                p = os.path.abspath(body.get("path", ""))
                cfg = load_config()
                inside = path_in_roots(p, cfg["roots"])
                if not inside or not os.path.isdir(p):
                    return self._send(403, json.dumps(
                        {"error": "只允许打开索引根目录内的文件夹"}, ensure_ascii=False).encode())
                os.startfile(p)
                return self._send(200, b'{"ok": true}')
        except Exception as e:
            return self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False).encode())

    def _toolbox_post(self, path):
        try:
            toolbox.check_request(self.headers, self.server.server_port, post=True)
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 8192:
                    raise ValueError("invalid length")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise toolbox.ToolboxError("请求必须包含有效 JSON，大小不超过 8 KB") from exc
            if path == "/api/tools/folder":
                if body != {}:
                    raise toolbox.ToolboxError("打开工具箱无需提供路径或其他参数")
                result = toolbox.open_root()
            else:
                result = toolbox.action(body)
            return self._send(200, json.dumps(result, ensure_ascii=False).encode())
        except toolbox.ToolboxError as e:
            return self._send(e.status, json.dumps({"error": str(e)}, ensure_ascii=False).encode())
        except OSError as e:
            return self._send(500, json.dumps(
                {"error": f"工具操作失败：{e}"}, ensure_ascii=False).encode())


def serve(port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    out(f"[启动] 工具箱已就绪 → http://127.0.0.1:{port}  （Ctrl+C 退出）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        out("\n[退出]")


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="工作室驾驶舱")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("scan")
    p_s = sub.add_parser("search"); p_s.add_argument("query"); p_s.add_argument("--limit", type=int, default=30)
    sub.add_parser("status")
    p_r = sub.add_parser("recent"); p_r.add_argument("days", nargs="?", type=int, default=14)
    sub.add_parser("knowledge")
    sub.add_parser("security")
    p_p = sub.add_parser("pack"); p_p.add_argument("project")
    p_p.add_argument("--out", default=None); p_p.add_argument("--budget", type=int, default=48000)
    p_v = sub.add_parser("serve"); p_v.add_argument("--port", type=int, default=None)
    sub.add_parser("doctor")
    args = ap.parse_args()
    if args.cmd == "scan":
        scan()
    elif args.cmd == "search":
        rs = search(args.query, args.limit)
        if not rs:
            out("无结果")
        for e in rs:
            out(f"[{e['score']:>4}] {e['rel']}  ({e['project']})")
            out(f"        {e['title']}  · {datetime.fromtimestamp(e['mtime']).strftime('%Y-%m-%d')}")
            out(f"        {e['match'][:150]}")
    elif args.cmd == "status":
        ps = project_stats()
        for p in ps[:40]:
            flag = " ⚠停滞" if p["stalled"] else ""
            out(f"{p['last_mtime_str']}  {p['project']}  "
                f"文档{p['docs']} 最近活跃{p['age_days']}天前{flag}")
            for r in p["pending"][:3]:
                out(f"    待: {r}")
    elif args.cmd == "recent":
        r = recent(args.days)
        out(f"最近 {args.days} 天共触及 {r['total']} 个文本文件：")
        for d in r["days"][:args.days]:
            roots = " ".join(f"{k}:{v}" for k, v in d["roots"].items())
            out(f"  {d['date']}  文件{d['files']:>4}（文档{d['docs']:>3}）  {roots}")
        out("\n最新改动：")
        for e in r["latest"][:20]:
            out(f"  {datetime.fromtimestamp(e['mtime']).strftime('%m-%d %H:%M')}  {e['rel']}")
    elif args.cmd == "knowledge":
        k = knowledge()
        for t, g in k.items():
            out(f"\n## {g['label']}（{len(g['items'])}）")
            for e in g["items"][:15]:
                out(f"  {datetime.fromtimestamp(e['mtime']).strftime('%Y-%m-%d')}  {e['rel']}")
    elif args.cmd == "security":
        fs = security()
        if not fs:
            out("[通过] 未发现明文密钥")
        else:
            out(f"[警告] 发现 {len(fs)} 处疑似明文密钥：")
            for f_ in fs:
                out(f"  {f_['root']}/{f_['rel']}:{f_['line']}  {f_['type']}  {f_['masked']}")
    elif args.cmd == "pack":
        pack(args.project, args.out, args.budget)
    elif args.cmd == "serve":
        cfg = load_config()
        serve(args.port or cfg["port"])
    elif args.cmd == "doctor":
        doctor()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()

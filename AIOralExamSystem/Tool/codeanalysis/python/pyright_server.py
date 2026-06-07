import ast
import json
import re
import select
import subprocess
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
PYRIGHT_SERVER = Path("/root/.nvm/versions/node/v20.20.2/bin/pyright-langserver")
SYMBOL_KIND_NAMES = {
    5: "class",
    6: "method",
    12: "function",
    13: "variable",
}


def generate_project_maps(
    user_uuid: str,
    timeout: int = 20,
    git_local_address: str | None = None,
) -> dict:
    # 根据当前用户的固定仓库目录生成 Markdown 项目地图，并同时返回地图内容。
    repositories = []
    logs = []
    for repository_name, code_root in _find_user_code_roots(user_uuid, git_local_address):
        result = query_pyright(code_root, timeout)
        if result.get("error"):
            repositories.append(
                {
                    "repository": repository_name,
                    "code_root": str(code_root),
                    "error": result["error"],
                }
            )
            logs.extend(result.get("logs", []))
            continue
        markdown = _render_project_map(code_root, result)
        map_path = code_root / "project_map.md"
        map_path.write_text(markdown, encoding="utf-8")
        repositories.append(
            {
                "repository": repository_name,
                "code_root": str(code_root),
                "map_path": str(map_path),
                "markdown": markdown,
                "file_count": len(result["python_files"]),
            }
        )
        logs.extend(result.get("logs", []))
        logs.append(
            {
                "step": "project_map_write",
                "status": "success",
                "repository": repository_name,
                "map_path": str(map_path),
            }
        )
    return {
        "mode": "lsp",
        "lsp_action": "project_map",
        "language": "python",
        "user_uuid": user_uuid,
        "git_local_address": git_local_address,
        "safe_git_local_address": _safe_path_part(git_local_address) if git_local_address else None,
        "repositories": repositories,
        "logs": logs,
    }


def query_user_symbols(
    user_uuid: str,
    timeout: int = 20,
    query: str | None = None,
    git_local_address: str | None = None,
    limit: int = 20,
) -> dict:
    # 按用户缓存目录遍历全部代码仓库，返回可继续供程序处理的原始符号分析。
    repositories = []
    logs = []
    matches = []
    query_text = str(query or "").strip()
    max_matches = min(100, max(1, int(limit or 20)))

    for repository_name, code_root in _find_user_code_roots(user_uuid, git_local_address):
        result = query_pyright(code_root, timeout)
        repositories.append({"repository": repository_name, **result})
        logs.extend(result.get("logs", []))

        if query_text and not result.get("error"):
            matches.extend(
                _filter_symbol_matches(
                    repository_name=repository_name,
                    result=result,
                    query=query_text,
                    remaining=max_matches - len(matches),
                )
            )
            if len(matches) >= max_matches:
                matches = matches[:max_matches]
                break

    response = {
        "mode": "lsp",
        "lsp_action": "symbols",
        "language": "python",
        "user_uuid": user_uuid,
        "git_local_address": git_local_address,
        "safe_git_local_address": _safe_path_part(git_local_address) if git_local_address else None,
        "repositories": repositories,
        "logs": logs,
    }
    if query_text:
        response.update(
            {
                "query": query_text,
                "limit": max_matches,
                "total_matches": len(matches),
                "matches": matches,
            }
        )
    return response


def query_pyright(project_root: Path, timeout: int) -> dict:
    # 启动固定的 WSL Pyright 服务，读取一个代码目录的符号和诊断结果。
    logs = [{"step": "pyright_start", "status": "start", "command": f"{PYRIGHT_SERVER} --stdio"}]
    process = None
    state = {"diagnostics": {}, "analysis_started": False, "analysis_finished": False}
    next_id = 1
    try:
        if not PYRIGHT_SERVER.is_file():
            raise FileNotFoundError(str(PYRIGHT_SERVER))
        python_files = _collect_python_files(project_root)
        process = subprocess.Popen(
            [str(PYRIGHT_SERVER), "--stdio"],
            cwd=str(project_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
        )
        root_uri = _path_to_uri(project_root)
        initialize_params = {
            "processId": None,
            "rootUri": root_uri,
            "capabilities": {"workspace": {"workspaceFolders": True}},
            "workspaceFolders": [{"uri": root_uri, "name": project_root.name}],
        }
        _request(process, next_id, "initialize", initialize_params, timeout, state)
        next_id += 1
        logs.append({"step": "initialize", "status": "success", "project_root": str(project_root)})
        _notification(process, "initialized", {})
        _open_python_files(process, python_files)
        logs.append({"step": "did_open", "status": "success", "file_count": len(python_files)})

        document_symbols = {}
        for file_path in python_files:
            response = _request(
                process,
                next_id,
                "textDocument/documentSymbol",
                {"textDocument": {"uri": _path_to_uri(file_path)}},
                timeout,
                state,
            )
            next_id += 1
            document_symbols[str(file_path.relative_to(project_root))] = response.get("result") or []
        logs.append({"step": "document_symbols", "status": "success", "file_count": len(document_symbols)})
        workspace_response = _request(process, next_id, "workspace/symbol", {"query": ""}, timeout, state)
        next_id += 1
        _drain_notifications(process, state, timeout)
        logs.append({"step": "workspace_symbols", "status": "success"})
        logs.append({"step": "diagnostics", "status": "success", "file_count": len(state["diagnostics"])})
        _request(process, next_id, "shutdown", None, timeout, state)
        _notification(process, "exit", {})
        return {
            "mode": "lsp",
            "language": "python",
            "server": str(PYRIGHT_SERVER),
            "project_root": str(project_root),
            "python_files": [str(path.relative_to(project_root)) for path in python_files],
            "document_symbols": document_symbols,
            "workspace_symbols": workspace_response.get("result") or [],
            "diagnostics": state["diagnostics"],
            "logs": logs,
        }
    except Exception as exc:
        logs.append({"step": "pyright_error", "status": "error", "message": str(exc)})
        return {
            "mode": "lsp",
            "language": "python",
            "server": str(PYRIGHT_SERVER),
            "project_root": str(project_root),
            "error": str(exc),
            "logs": logs,
        }
    finally:
        if process is not None and process.poll() is None:
            process.kill()


def _find_user_code_roots(
    user_uuid: str,
    git_local_address: str | None = None,
) -> list[tuple[str, Path]]:
    # 用户只能访问项目根目录下属于自身 uuid 的 Gitrepositorys 缓存代码目录。
    user_uuid = str(user_uuid).strip()
    if not user_uuid or not re.fullmatch(r"[A-Za-z0-9._-]+", user_uuid):
        raise ValueError("user_uuid is invalid.")
    user_root = PROJECT_ROOT / "Gitrepositorys" / user_uuid
    if not user_root.is_dir():
        return []
    if git_local_address and str(git_local_address).strip():
        repository_dir = user_root / _safe_repository_path(git_local_address)
        code_root = repository_dir / "code"
        repository_name = str(repository_dir.relative_to(user_root)).replace("\\", "/")
        return [(repository_name, code_root)] if code_root.is_dir() else []
    return [
        (repository_dir.name, repository_dir / "code")
        for repository_dir in sorted(user_root.iterdir())
        if repository_dir.is_dir() and (repository_dir / "code").is_dir()
    ]


def _safe_path_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    return safe.strip("._-") or "repository"


def _safe_repository_path(value: str) -> Path:
    parts = [
        _safe_path_part(part)
        for part in str(value).replace("\\", "/").split("/")
        if part.strip()
    ]
    if not parts:
        return Path("repository")
    return Path(*parts)


def _filter_symbol_matches(
    repository_name: str,
    result: dict,
    query: str,
    remaining: int,
) -> list[dict]:
    query_lower = query.lower()
    matches = []
    for relative_path, symbols in result.get("document_symbols", {}).items():
        for symbol in _flatten_document_symbols(symbols, relative_path):
            haystack = " ".join(
                str(symbol.get(key, ""))
                for key in ("name", "kind", "parent", "relative_path")
            ).lower()
            if query_lower not in haystack:
                continue
            matches.append({"repository": repository_name, **symbol})
            if len(matches) >= remaining:
                return matches
    return matches


def _flatten_document_symbols(
    symbols: list,
    relative_path: str,
    parent: str | None = None,
) -> list[dict]:
    flattened = []
    for symbol in symbols or []:
        range_start = symbol.get("selectionRange", symbol.get("range", {})).get("start", {})
        range_end = symbol.get("range", {}).get("end", {})
        item = {
            "relative_path": relative_path,
            "name": symbol.get("name"),
            "kind": SYMBOL_KIND_NAMES.get(symbol.get("kind"), str(symbol.get("kind"))),
            "line": range_start.get("line", 0) + 1,
            "end_line": range_end.get("line", range_start.get("line", 0)) + 1,
        }
        if parent:
            item["parent"] = parent
        if symbol.get("detail"):
            item["detail"] = symbol["detail"]
        flattened.append(item)
        flattened.extend(
            _flatten_document_symbols(
                symbol.get("children", []),
                relative_path,
                symbol.get("name") or parent,
            )
        )
    return flattened


def _collect_python_files(project_root: Path) -> list[Path]:
    # 收集代码目录中的 Python 文件，并跳过不参与项目分析的缓存和依赖目录。
    ignored_dirs = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", "node_modules"}
    return sorted(
        path
        for path in project_root.rglob("*.py")
        if not any(part in ignored_dirs for part in path.relative_to(project_root).parts)
    )


def _open_python_files(process: subprocess.Popen, python_files: list[Path]) -> None:
    # 将代码文件内容注册到 LSP，保证文档符号和诊断以当前源码为准。
    for file_path in python_files:
        _notification(
            process,
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": _path_to_uri(file_path),
                    "languageId": "python",
                    "version": 1,
                    "text": file_path.read_text(encoding="utf-8"),
                }
            },
        )


def _render_project_map(project_root: Path, result: dict) -> str:
    # 将符号、导入、入口与诊断组织为可直接投喂给 AI 的 Markdown 项目地图。
    files = {}
    entry_points = []
    import_relations = {}
    for relative_path in result["python_files"]:
        file_path = project_root / relative_path
        symbols = [_simplify_symbol(symbol) for symbol in result["document_symbols"].get(relative_path, [])]
        files[relative_path] = [
            symbol
            for symbol in symbols
            if symbol["kind"] in {"class", "method", "function"}
            or (symbol["kind"] == "variable" and "container" not in symbol)
        ]
        imports, entries = _analyze_source_structure(file_path)
        import_relations[relative_path] = imports
        entry_points.extend(entries)

    lines = [
        "# Project Map",
        "",
        f"- Root: `{project_root}`",
        "- Language: `python`",
        f"- Python files: `{len(result['python_files'])}`",
        f"- Analyzer: `{PYRIGHT_SERVER}`",
        "",
        "## Entry Candidates",
        "",
    ]
    lines.extend(f"- `{item}`" for item in entry_points or ["No explicit entry point detected."])
    lines.extend(["", "## File And Symbol Outline", "", "```text"])
    for relative_path, symbols in files.items():
        lines.append(relative_path)
        for symbol in symbols:
            indent = "    " if symbol.get("container") else "  "
            parent = f" [{symbol['container']}]" if symbol.get("container") else ""
            lines.append(f"{indent}{symbol['name']} ({symbol['kind']}, line {symbol['line']}){parent}")
        if not symbols:
            lines.append("  (no symbols)")
        lines.append("")
    lines.extend(["```", "", "## Import Relations", ""])
    for relative_path, imports in import_relations.items():
        lines.append(f"- `{relative_path}`")
        if imports:
            lines.extend(f"  - `{item}`" for item in imports)
        else:
            lines.append("  - No imports.")
    lines.extend(["", "## Pyright Diagnostics", ""])
    diagnostic_lines = []
    for relative_path in result["python_files"]:
        for diagnostic in result["diagnostics"].get(_path_to_uri(project_root / relative_path), []):
            line = diagnostic.get("range", {}).get("start", {}).get("line", 0) + 1
            message = " ".join(str(diagnostic.get("message", "")).split())
            diagnostic_lines.append(f"- `{relative_path}:{line}` {message}")
    lines.extend(diagnostic_lines or ["- No diagnostics reported in this LSP session."])
    return "\n".join(lines) + "\n"


def _simplify_symbol(symbol: dict) -> dict:
    # 压缩 Pyright 符号结构，保留地图中需要展示的名称、类别和所属容器。
    start = symbol.get("location", {}).get("range", {}).get("start", {})
    result = {
        "name": symbol.get("name"),
        "kind": SYMBOL_KIND_NAMES.get(symbol.get("kind"), str(symbol.get("kind"))),
        "line": start.get("line", 0) + 1,
    }
    if symbol.get("containerName"):
        result["container"] = symbol["containerName"]
    return result


def _analyze_source_structure(file_path: Path) -> tuple[list[str], list[str]]:
    # 补充 LSP 符号树未直接返回的导入关系和可执行入口信息。
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    imports = []
    entries = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            names = ", ".join(item.name for item in node.names)
            imports.append(f"{'.' * node.level}{node.module or ''}: {names}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main":
            entries.append(f"{file_path.name}:main (line {node.lineno})")
        elif isinstance(node, ast.If):
            condition = ast.unparse(node.test)
            if "__name__" in condition and "__main__" in condition:
                entries.append(f"{file_path.name} executable guard (line {node.lineno})")
    return imports, entries


def _request(process: subprocess.Popen, request_id: int, method: str, params, timeout: int, state: dict) -> dict:
    # 发送请求并处理其间穿插返回的服务通知，直到取得对应请求响应。
    _send_message(process, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    while True:
        message = _read_message(process, timeout)
        if message.get("method"):
            _capture_notification(message, state)
            if message.get("id") is not None:
                _send_message(process, {"jsonrpc": "2.0", "id": message["id"], "result": None})
            continue
        if message.get("id") == request_id:
            if "error" in message:
                raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))
            return message


def _capture_notification(message: dict, state: dict) -> None:
    # 记录 Pyright 的诊断和分析进度，供地图生成阶段使用。
    method = message.get("method")
    if method == "textDocument/publishDiagnostics":
        params = message.get("params", {})
        state["diagnostics"][params.get("uri", "")] = params.get("diagnostics", [])
    elif method == "pyright/beginProgress":
        state["analysis_started"] = True
    elif method == "pyright/endProgress":
        state["analysis_finished"] = True


def _drain_notifications(process: subprocess.Popen, state: dict, timeout: int) -> None:
    # 等待项目级分析结束，避免在诊断通知抵达前提前写出项目地图。
    deadline = time.monotonic() + min(timeout, 10)
    while time.monotonic() < deadline:
        wait_time = min(1, deadline - time.monotonic())
        ready, _, _ = select.select([process.stdout], [], [], wait_time)
        if not ready:
            if not state["analysis_started"]:
                return
            continue
        message = _read_message(process, max(1, int(wait_time) + 1))
        if message.get("method"):
            _capture_notification(message, state)
        if state["analysis_finished"]:
            return


def _notification(process: subprocess.Popen, method: str, params: dict) -> None:
    # 发送不要求响应的 LSP 通知消息。
    _send_message(process, {"jsonrpc": "2.0", "method": method, "params": params})


def _send_message(process: subprocess.Popen, payload: dict) -> None:
    # 按 LSP 的 Content-Length 帧格式写入 UTF-8 JSON 消息。
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    process.stdin.flush()


def _read_message(process: subprocess.Popen, timeout: int) -> dict:
    # 通过无缓冲二进制管道读取一条完整消息，避免响应预读导致误判超时。
    deadline = time.monotonic() + timeout
    header = bytearray()
    while not header.endswith(b"\r\n\r\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
            raise TimeoutError("Timed out waiting for pyright-langserver response.")
        byte = process.stdout.read(1)
        if not byte:
            stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"pyright-langserver closed stdout: {stderr}")
        header.extend(byte)
    content_length = None
    for line in header.decode("ascii", errors="replace").splitlines():
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())
    if content_length is None:
        raise RuntimeError("Invalid LSP response: missing Content-Length.")
    body = bytearray()
    while len(body) < content_length:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
            raise TimeoutError("Timed out reading pyright-langserver response body.")
        chunk = process.stdout.read(content_length - len(body))
        if not chunk:
            raise RuntimeError("Invalid LSP response: empty body.")
        body.extend(chunk)
    return json.loads(body.decode("utf-8"))


def _path_to_uri(path: Path) -> str:
    # 将 Linux 文件路径转换成 LSP 可识别的 file URI。
    return path.resolve().as_uri()

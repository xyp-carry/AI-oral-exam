import json
import select
import subprocess
from pathlib import Path
from urllib.parse import quote


def query_pyright(project_root: Path, timeout: int) -> dict:
    # 启动 pyright-langserver，把项目内 Python 文件逐个打开并汇总项目级符号结果。
    logs = [{"step": "pyright_start", "status": "start", "command": "pyright-langserver --stdio"}]
    process = None
    next_id = 1
    try:
        python_files = _collect_python_files(project_root)
        process = subprocess.Popen(
            ["pyright-langserver", "--stdio"],
            cwd=str(project_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )

        root_uri = _path_to_uri(project_root)
        initialize_params = {
            "processId": None,
            "rootUri": root_uri,
            "capabilities": {"workspace": {"symbol": {}}},
            "workspaceFolders": [{"uri": root_uri, "name": project_root.name}],
        }
        _request(process, next_id, "initialize", initialize_params, timeout)
        logs.append({"step": "initialize", "status": "success", "project_root": str(project_root)})
        next_id += 1

        _notification(process, "initialized", {})
        opened_files = _open_python_files(process, python_files)
        logs.append({"step": "did_open", "status": "success", "file_count": len(opened_files)})

        document_symbols = {}
        for file_path in python_files:
            file_uri = _path_to_uri(file_path)
            response = _request(
                process,
                next_id,
                "textDocument/documentSymbol",
                {"textDocument": {"uri": file_uri}},
                timeout,
            )
            next_id += 1
            document_symbols[str(file_path.relative_to(project_root))] = response.get("result")
        logs.append({"step": "document_symbols", "status": "success", "file_count": len(document_symbols)})

        workspace_response = _request(process, next_id, "workspace/symbol", {"query": ""}, timeout)
        next_id += 1
        logs.append({"step": "workspace_symbols", "status": "success"})

        _request(process, next_id, "shutdown", None, timeout)
        _notification(process, "exit", {})
        return {
            "mode": "lsp",
            "language": "python",
            "server": "pyright-langserver",
            "project_root": str(project_root),
            "python_files": [str(path.relative_to(project_root)) for path in python_files],
            "document_symbols": document_symbols,
            "workspace_symbols": workspace_response.get("result"),
            "logs": logs,
        }
    except FileNotFoundError as exc:
        logs.append({"step": "pyright_start", "status": "error", "message": "pyright-langserver command not found."})
        return {"mode": "lsp", "language": "python", "server": "pyright-langserver", "error": str(exc), "logs": logs}
    except Exception as exc:
        logs.append({"step": "pyright_error", "status": "error", "message": str(exc)})
        return {"mode": "lsp", "language": "python", "server": "pyright-langserver", "error": str(exc), "logs": logs}
    finally:
        if process is not None and process.poll() is None:
            process.kill()


def _collect_python_files(project_root: Path) -> list[Path]:
    # 收集项目内可交给 pyright 解析的 Python 文件，并跳过常见缓存和虚拟环境目录。
    ignored_dirs = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", "node_modules"}
    files = []
    for path in project_root.rglob("*.py"):
        if any(part in ignored_dirs for part in path.relative_to(project_root).parts):
            continue
        files.append(path)
    return sorted(files)


def _open_python_files(process: subprocess.Popen, python_files: list[Path]) -> list[str]:
    # 通过 didOpen 通知让语言服务器建立每个 Python 文件的文档上下文。
    opened_files = []
    for file_path in python_files:
        source = file_path.read_text(encoding="utf-8")
        file_uri = _path_to_uri(file_path)
        _notification(
            process,
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": file_uri,
                    "languageId": "python",
                    "version": 1,
                    "text": source,
                }
            },
        )
        opened_files.append(file_uri)
    return opened_files


def _request(process: subprocess.Popen, request_id: int, method: str, params, timeout: int) -> dict:
    # 发送 LSP request，并持续读取消息直到拿到对应 id 的响应。
    _send_message(process, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    while True:
        message = _read_message(process, timeout)
        if message.get("id") is not None and message.get("method") and message.get("id") != request_id:
            _send_message(process, {"jsonrpc": "2.0", "id": message["id"], "result": None})
            continue
        if message.get("id") == request_id:
            if "error" in message:
                raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))
            return message


def _notification(process: subprocess.Popen, method: str, params: dict) -> None:
    # 发送不需要响应的 LSP notification。
    _send_message(process, {"jsonrpc": "2.0", "method": method, "params": params})


def _send_message(process: subprocess.Popen, payload: dict) -> None:
    # 按 LSP 的 Content-Length 帧格式写入 JSON-RPC 消息。
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    process.stdin.write(header + body)
    process.stdin.flush()


def _read_message(process: subprocess.Popen, timeout: int) -> dict:
    # 从 stdout 读取一个完整 LSP 消息；如果 pyright 退出则返回 stderr 中的真实错误。
    content_length = None
    while True:
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        if not ready:
            raise TimeoutError("Timed out waiting for pyright-langserver response.")
        line = process.stdout.readline()
        if not line:
            stderr = ""
            if process.poll() is not None and process.stderr:
                stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
            if stderr:
                raise RuntimeError(f"pyright-langserver closed stdout: {stderr}")
            raise RuntimeError("pyright-langserver closed stdout.")
        line_text = line.decode("ascii", errors="replace").strip()
        if not line_text:
            break
        if line_text.lower().startswith("content-length:"):
            content_length = int(line_text.split(":", 1)[1].strip())

    if content_length is None:
        raise RuntimeError("Invalid LSP response: missing Content-Length.")

    body = process.stdout.read(content_length)
    if not body:
        raise RuntimeError("Invalid LSP response: empty body.")
    return json.loads(body.decode("utf-8"))


def _path_to_uri(path: Path) -> str:
    # 将本地路径转换成 file:// URI，供 LSP textDocument 和 workspace 使用。
    resolved = path.resolve()
    return "file://" + quote(str(resolved).replace("\\", "/"), safe="/:")

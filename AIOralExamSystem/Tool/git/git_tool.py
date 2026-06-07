import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool


class GitRepositoryToolInput(BaseModel):
    repo_url: str = Field(description="Git repository URL.")
    user_uuid: str = Field(description="Current user uuid used to scope repository cache.")
    course_id: Optional[str] = Field(default=None, description="Optional course id for exam-scoped repository cache.")
    exam_id: Optional[str] = Field(default=None, description="Optional exam id for exam-scoped repository cache.")
    git_branch: Optional[str] = Field(default=None, description="Optional branch folder name for exam-scoped repository cache.")
    code_path: Optional[str] = Field(default=None, description="Optional code directory or file path in the repository.")
    doc_path: Optional[str] = Field(default=None, description="Optional document directory or file path in the repository.")
    storage_dir: Optional[str] = Field(default=None, description="Optional storage root. Defaults to the project root directory.")
    branch: Optional[str] = Field(default=None, description="Optional branch, tag, or commit-ish to fetch.")
    accelerator_urls: Optional[list[str]] = Field(default=None, description="Optional git or zip accelerator URL prefixes.")
    reload: bool = Field(default=False, description="Force refresh repository cache and fetch the repository again.")


GitRepositoryDescription = (
    "Fetch a git repository for a user. If code_path or doc_path is provided, copy those repository paths into "
    "Gitrepositorys/{user_uuid}/{repo}/code and/or doc, or into "
    "Gitrepositorys/{user_uuid}/{course_id}/{exam_id}/{git_branch}/code and/or doc when exam-scoped fields are "
    "provided, then return readable file contents. If neither path is provided, return repository files with their "
    "directory hierarchy and do not store code/doc files."
)


class GitRepositoryTool(BaseTool):
    """Fetch and cache user-scoped git repository content."""

    def __init__(self, name: str):
        super().__init__(name)
        self.description = GitRepositoryDescription

    async def _run(
        self,
        repo_url: str,
        user_uuid: str,
        code_path: Optional[str] = None,
        doc_path: Optional[str] = None,
        storage_dir: Optional[str] = None,
        branch: Optional[str] = None,
        accelerator_urls: Optional[list[str]] = None,
        course_id: Optional[str] = None,
        exam_id: Optional[str] = None,
        git_branch: Optional[str] = None,
        reload: bool = False,
        target_root: Optional[str] = None,
    ) -> str:
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor,
                self.fetch_repository,
                repo_url,
                user_uuid,
                code_path,
                doc_path,
                storage_dir,
                branch,
                accelerator_urls,
                course_id,
                exam_id,
                git_branch,
                reload,
                target_root,
            )

    def fetch_repository(
        self,
        repo_url: str,
        user_uuid: str,
        code_path: Optional[str] = None,
        doc_path: Optional[str] = None,
        storage_dir: Optional[str] = None,
        branch: Optional[str] = None,
        accelerator_urls: Optional[list[str]] = None,
        course_id: Optional[str] = None,
        exam_id: Optional[str] = None,
        git_branch: Optional[str] = None,
        reload: bool = False,
        target_root: Optional[str] = None,
    ) -> str:
        logs = []
        repo_url = self._require_text(repo_url, "repo_url")
        user_uuid = self._require_text(user_uuid, "user_uuid")
        course_id = self._normalize_optional_path(course_id)
        exam_id = self._normalize_optional_path(exam_id)
        git_branch = self._normalize_optional_path(git_branch)
        code_path = self._normalize_optional_path(code_path)
        doc_path = self._normalize_optional_path(doc_path)
        branch = branch.strip() if branch and branch.strip() else None
        accelerator_urls = self._get_accelerator_prefixes(accelerator_urls)

        self._append_log(
            logs,
            "start",
            "success",
            "Start fetching repository.",
            repo_url=repo_url,
            user_uuid=user_uuid,
            course_id=course_id,
            exam_id=exam_id,
            git_branch=git_branch,
            code_path=code_path,
            doc_path=doc_path,
            branch=branch,
            reload=reload,
            accelerator_count=len(accelerator_urls),
        )

        try:
            if not code_path and not doc_path:
                self._append_log(logs, "mode", "success", "No code_path/doc_path provided; listing files and tree only.")
                return self._list_repository_directories(repo_url, branch, accelerator_urls, logs)

            repo_root = (
                Path(target_root).expanduser().resolve()
                if target_root and str(target_root).strip()
                else self._repo_cache_root(storage_dir, user_uuid, repo_url, course_id, exam_id, git_branch)
            )
            manifest_path = repo_root / "manifest.json"
            self._append_log(logs, "cache_check", "success", "Checking local repository cache.", repository_root=str(repo_root))

            if not reload:
                cached = self._read_cached_result(repo_root, manifest_path, repo_url, code_path, doc_path, branch)
                if cached is not None:
                    self._append_log(logs, "cache_hit", "success", "Matched local cache; reading stored files.")
                    cached["logs"] = logs
                    return json.dumps(cached, ensure_ascii=False)

                self._append_log(logs, "cache_miss", "success", "No matching cache found; preparing repository storage.")
            else:
                self._append_log(logs, "cache_reload", "success", "Reload requested; rebuilding repository storage.")
            if repo_root.exists():
                shutil.rmtree(repo_root)
                self._append_log(logs, "storage_cleanup", "success", "Removed stale repository cache.")
            repo_root.mkdir(parents=True, exist_ok=True)

            with tempfile.TemporaryDirectory(prefix="git_repository_") as temp_dir:
                temp_repo = Path(temp_dir) / "repo"
                self._append_log(logs, "temp_create", "success", "Created temporary repository directory.", temp_dir=temp_dir)
                self._clone_repository(repo_url, temp_repo, branch, accelerator_urls, logs)

                copied = {}
                errors = []
                if code_path:
                    self._append_log(logs, "copy_code", "start", "Copying requested code path.", path=code_path)
                    copied["code"] = self._copy_requested_path(temp_repo, code_path, repo_root / "code")
                    if "error" in copied["code"]:
                        errors.append(copied["code"]["error"])
                        self._append_log(logs, "copy_code", "error", copied["code"]["error"])
                    else:
                        self._append_log(
                            logs,
                            "copy_code",
                            "success",
                            "Copied requested code path.",
                            copied_file_count=copied["code"]["copied_file_count"],
                        )
                if doc_path:
                    self._append_log(logs, "copy_doc", "start", "Copying requested doc path.", path=doc_path)
                    copied["doc"] = self._copy_requested_path(temp_repo, doc_path, repo_root / "doc")
                    if "error" in copied["doc"]:
                        errors.append(copied["doc"]["error"])
                        self._append_log(logs, "copy_doc", "error", copied["doc"]["error"])
                    else:
                        self._append_log(
                            logs,
                            "copy_doc",
                            "success",
                            "Copied requested doc path.",
                            copied_file_count=copied["doc"]["copied_file_count"],
                        )

                manifest = {
                    "repo_url": repo_url,
                    "safe_repo_name": repo_root.name,
                    "user_uuid": user_uuid,
                    "course_id": course_id,
                    "exam_id": exam_id,
                    "git_branch": git_branch,
                    "branch": branch,
                    "code_path": code_path,
                    "doc_path": doc_path,
                    "storage_root": str(repo_root.parent.parent.parent),
                    "repository_root": str(repo_root),
                }
                self._write_json(manifest_path, manifest)
                self._append_log(logs, "manifest_write", "success", "Wrote repository manifest.", manifest_path=str(manifest_path))

            result = self._build_stored_result(repo_root, manifest, cached=False, errors=errors)
            result["copied"] = copied
            result["logs"] = logs
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            self._append_log(logs, "error", "error", str(exc))
            return json.dumps(
                {
                    "repo_url": repo_url,
                    "branch": branch,
                    "mode": "error",
                    "cached": False,
                    "error": str(exc),
                    "logs": logs,
                },
                ensure_ascii=False,
            )

    def _list_repository_directories(
        self,
        repo_url: str,
        branch: Optional[str],
        accelerator_urls: list[str],
        logs: list,
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="git_repository_") as temp_dir:
            temp_repo = Path(temp_dir) / "repo"
            self._append_log(logs, "temp_create", "success", "Created temporary repository directory.", temp_dir=temp_dir)
            self._clone_repository(repo_url, temp_repo, branch, accelerator_urls, logs)
            self._append_log(logs, "collect_directories", "start", "Collecting repository directories.")
            directories = self._collect_directories(temp_repo)
            self._append_log(logs, "collect_directories", "success", "Collected repository directories.", count=len(directories))
            self._append_log(logs, "collect_files", "start", "Collecting repository files.")
            files = self._collect_files(temp_repo)
            self._append_log(logs, "collect_files", "success", "Collected repository files.", count=len(files))
            self._append_log(logs, "collect_tree", "start", "Building repository hierarchy tree.")
            tree = self._collect_repository_tree(temp_repo)
            self._append_log(logs, "collect_tree", "success", "Built repository hierarchy tree.")

        return json.dumps(
            {
                "repo_url": repo_url,
                "branch": branch,
                "mode": "repository_tree",
                "cached": False,
                "directories": directories,
                "directory_count": len(directories),
                "files": files,
                "file_count": len(files),
                "tree": tree,
                "logs": logs,
            },
            ensure_ascii=False,
        )

    def _read_cached_result(
        self,
        repo_root: Path,
        manifest_path: Path,
        repo_url: str,
        code_path: Optional[str],
        doc_path: Optional[str],
        branch: Optional[str],
    ) -> Optional[dict]:
        if not manifest_path.exists():
            return None

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if manifest.get("repo_url") != repo_url:
            return None
        if manifest.get("branch") != branch:
            return None
        if manifest.get("code_path") != code_path:
            return None
        if manifest.get("doc_path") != doc_path:
            return None
        if code_path and not (repo_root / "code").exists():
            return None
        if doc_path and not (repo_root / "doc").exists():
            return None

        return self._build_stored_result(repo_root, manifest, cached=True, errors=[])

    def _build_stored_result(self, repo_root: Path, manifest: dict, cached: bool, errors: list) -> dict:
        result = {
            "repo_url": manifest.get("repo_url"),
            "branch": manifest.get("branch"),
            "mode": "stored_files",
            "cached": cached,
            "repository_root": str(repo_root),
            "code_path": manifest.get("code_path"),
            "doc_path": manifest.get("doc_path"),
            "code": None,
            "doc": None,
            "errors": errors,
        }

        if manifest.get("code_path"):
            result["code"] = self._read_directory_texts(repo_root / "code")
        if manifest.get("doc_path"):
            result["doc"] = self._read_directory_texts(repo_root / "doc")

        return result

    def _clone_repository(
        self,
        repo_url: str,
        target_dir: Path,
        branch: Optional[str],
        accelerator_urls: list[str],
        logs: list,
    ) -> None:
        clone_errors = []
        clone_candidates = self._build_clone_url_candidates(repo_url, accelerator_urls)
        for clone_url in clone_candidates:
            command = ["git", "clone", "--depth", "1"]
            if branch:
                command.extend(["--branch", branch])
            command.extend([clone_url, str(target_dir)])

            self._append_log(
                logs,
                "git_clone",
                "start",
                "Trying git clone.",
                url=clone_url,
                accelerated=clone_url != repo_url,
            )
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode == 0:
                    self._append_log(logs, "git_clone", "success", "Repository cloned successfully.", url=clone_url)
                    return
                clone_error = result.stderr.strip() or result.stdout.strip()
            except FileNotFoundError:
                clone_error = "git command is not available in the current environment."

            clone_errors.append(f"{clone_url}: {clone_error}")
            self._append_log(logs, "git_clone", "error", clone_error, url=clone_url)
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)

        try:
            self._append_log(logs, "zip_fallback", "start", "Git clone failed; trying zip archive fallback.")
            self._download_zip_repository(repo_url, target_dir, branch, accelerator_urls, logs)
        except Exception as exc:
            raise RuntimeError(f"git clone failed: {'; '.join(clone_errors)}; zip download failed: {exc}") from exc

    def _download_zip_repository(
        self,
        repo_url: str,
        target_dir: Path,
        branch: Optional[str],
        accelerator_urls: list[str],
        logs: list,
    ) -> None:
        archive_urls = self._build_archive_url_candidates(repo_url, branch, accelerator_urls)
        if not archive_urls:
            raise RuntimeError("unsupported repository URL for zip fallback.")

        errors = []
        with tempfile.TemporaryDirectory(prefix="git_repository_zip_") as temp_dir:
            zip_path = Path(temp_dir) / "repository.zip"
            extract_dir = Path(temp_dir) / "extract"
            extract_dir.mkdir(parents=True, exist_ok=True)

            for archive_url in archive_urls:
                try:
                    self._append_log(
                        logs,
                        "zip_download",
                        "start",
                        "Trying repository zip archive.",
                        url=archive_url,
                        accelerated=self._is_accelerated_url(archive_url, accelerator_urls),
                    )
                    self._download_file(archive_url, zip_path)
                    self._append_log(logs, "zip_download", "success", "Downloaded repository zip archive.", url=archive_url)
                    self._append_log(logs, "zip_extract", "start", "Extracting repository zip archive.")
                    self._extract_zip_safely(zip_path, extract_dir)
                    self._append_log(logs, "zip_extract", "success", "Extracted repository zip archive.")
                    self._move_extracted_repository(extract_dir, target_dir)
                    self._append_log(logs, "zip_move", "success", "Moved extracted repository to target directory.")
                    return
                except Exception as exc:
                    errors.append(f"{archive_url}: {exc}")
                    self._append_log(logs, "zip_download", "error", str(exc), url=archive_url)
                    if zip_path.exists():
                        zip_path.unlink()
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    extract_dir.mkdir(parents=True, exist_ok=True)

        raise RuntimeError("; ".join(errors))

    def _build_archive_url_candidates(self, repo_url: str, branch: Optional[str], accelerator_urls: list[str]) -> list:
        base_url = self._normalize_repository_page_url(repo_url)
        if not base_url:
            return []

        parsed = urlparse(base_url)
        host = parsed.netloc.lower()
        repo_name = Path(parsed.path.rstrip("/")).name
        refs = [branch] if branch else ["main", "master"]
        candidates = []

        for ref in refs:
            if "github.com" in host:
                candidates.extend(
                    [
                        f"{base_url}/archive/refs/heads/{ref}.zip",
                        f"{base_url}/archive/refs/tags/{ref}.zip",
                    ]
                )
            elif "gitlab" in host:
                candidates.append(f"{base_url}/-/archive/{ref}/{repo_name}-{ref}.zip")
            elif "gitee.com" in host:
                candidates.append(f"{base_url}/repository/archive/{ref}.zip")

            candidates.extend(
                [
                    f"{base_url}/archive/refs/heads/{ref}.zip",
                    f"{base_url}/archive/{ref}.zip",
                    f"{base_url}/-/archive/{ref}/{repo_name}-{ref}.zip",
                ]
            )

        candidates = self._add_accelerated_urls(candidates, accelerator_urls)
        return self._unique_values(candidates)

    def _build_clone_url_candidates(self, repo_url: str, accelerator_urls: list[str]) -> list:
        candidates = [repo_url]
        base_url = self._normalize_repository_page_url(repo_url)
        if base_url:
            https_git_url = f"{base_url}.git"
            candidates.extend(self._add_accelerated_urls([https_git_url], accelerator_urls))
        return self._unique_values(candidates)

    def _add_accelerated_urls(self, urls: list, accelerator_urls: list[str]) -> list:
        candidates = list(urls)
        for url in urls:
            if not self._can_accelerate_url(url):
                continue
            for accelerator in accelerator_urls:
                candidates.append(self._apply_accelerator(url, accelerator))
        return candidates

    def _get_accelerator_prefixes(self, accelerator_urls: Optional[list[str]]) -> list[str]:
        if accelerator_urls:
            prefixes = accelerator_urls
        else:
            env_value = os.getenv("GIT_REPOSITORY_ACCELERATORS", "")
            prefixes = [item.strip() for item in env_value.split(",") if item.strip()]
            if not prefixes:
                prefixes = [
                    "https://gh-proxy.com/",
                    "https://gh.llkk.cc/",
                    "https://hub.gitmirror.com/",
                ]

        return [prefix for prefix in self._unique_values(prefixes) if prefix]

    def _apply_accelerator(self, url: str, accelerator: str) -> str:
        accelerator = accelerator.strip()
        if "{url}" in accelerator:
            return accelerator.format(url=url)
        return f"{accelerator.rstrip('/')}/{url}"

    def _can_accelerate_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and "github.com" in parsed.netloc.lower()

    def _is_accelerated_url(self, url: str, accelerator_urls: list[str]) -> bool:
        return any(url.startswith(prefix.rstrip("/") + "/") for prefix in accelerator_urls if "{url}" not in prefix)

    def _unique_values(self, values: list) -> list:
        seen = set()
        unique_values = []
        for value in values:
            if value not in seen:
                unique_values.append(value)
                seen.add(value)
        return unique_values

    def _append_log(self, logs: list, step: str, status: str, message: str, **metadata) -> None:
        log_item = {
            "step": step,
            "status": status,
            "message": message,
        }
        if metadata:
            log_item.update(metadata)
        logs.append(log_item)

    def _normalize_repository_page_url(self, repo_url: str) -> Optional[str]:
        repo_url = repo_url.strip()
        ssh_match = re.match(r"^git@([^:]+):(.+?)(?:\.git)?/?$", repo_url)
        if ssh_match:
            return f"https://{ssh_match.group(1)}/{ssh_match.group(2)}"

        ssh_url_match = re.match(r"^ssh://git@([^/]+)/(.+?)(?:\.git)?/?$", repo_url)
        if ssh_url_match:
            return f"https://{ssh_url_match.group(1)}/{ssh_url_match.group(2)}"

        parsed = urlparse(repo_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None

        path = parsed.path.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    def _download_file(self, url: str, destination: Path) -> None:
        request = Request(url, headers={"User-Agent": "AIOralExamSystem-GitRepositoryTool"})
        with urlopen(request, timeout=60) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise RuntimeError(f"http status {status}")

            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as file:
                shutil.copyfileobj(response, file)

    def _extract_zip_safely(self, zip_path: Path, extract_dir: Path) -> None:
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_file:
                extract_root = extract_dir.resolve()
                for member in zip_file.infolist():
                    target_path = (extract_dir / member.filename).resolve()
                    if target_path != extract_root and extract_root not in target_path.parents:
                        raise RuntimeError(f"zip member escapes extract directory: {member.filename}")
                zip_file.extractall(extract_dir)
        except zipfile.BadZipFile as exc:
            raise RuntimeError("downloaded file is not a valid zip archive.") from exc

    def _move_extracted_repository(self, extract_dir: Path, target_dir: Path) -> None:
        children = [path for path in extract_dir.iterdir() if path.name != "__MACOSX"]
        if len(children) == 1 and children[0].is_dir():
            source_dir = children[0]
        else:
            source_dir = extract_dir

        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir)

    def _copy_requested_path(self, repo_dir: Path, requested_path: str, destination_dir: Path) -> dict:
        source_path = self._resolve_repo_path(repo_dir, requested_path)
        if not source_path.exists():
            return {
                "requested_path": requested_path,
                "destination": str(destination_dir),
                "error": f"Path does not exist in repository: {requested_path}",
            }

        destination_dir.mkdir(parents=True, exist_ok=True)
        if source_path.is_file():
            shutil.copy2(source_path, destination_dir / source_path.name)
            copied_files = [source_path.name]
        else:
            copied_files = self._copy_directory_files(source_path, destination_dir)

        return {
            "requested_path": requested_path,
            "destination": str(destination_dir),
            "copied_file_count": len(copied_files),
            "copied_files": copied_files,
        }

    def _copy_directory_files(self, source_dir: Path, destination_dir: Path) -> list:
        copied_files = []
        for file_path in source_dir.rglob("*"):
            if not file_path.is_file() or self._should_skip(file_path):
                continue
            relative_path = file_path.relative_to(source_dir)
            target_path = destination_dir / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, target_path)
            copied_files.append(relative_path.as_posix())
        return copied_files

    def _read_directory_texts(self, directory: Path) -> dict:
        files = []
        skipped = []
        if not directory.exists():
            return {"root": str(directory), "files": files, "skipped": skipped, "file_count": 0}

        for file_path in directory.rglob("*"):
            if not file_path.is_file():
                continue

            relative_path = file_path.relative_to(directory).as_posix()
            if self._should_skip(file_path):
                skipped.append({"path": relative_path, "reason": "ignored"})
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    content = file_path.read_text(encoding="gb18030")
                except UnicodeDecodeError:
                    skipped.append({"path": relative_path, "reason": "not_text"})
                    continue
            except OSError as exc:
                skipped.append({"path": relative_path, "reason": str(exc)})
                continue

            files.append({"path": relative_path, "content": content})

        return {
            "root": str(directory),
            "file_count": len(files),
            "skipped_count": len(skipped),
            "files": files,
            "skipped": skipped,
        }

    def _collect_directories(self, repo_dir: Path) -> list:
        directories = []
        for dir_path in repo_dir.rglob("*"):
            if not dir_path.is_dir():
                continue
            if ".git" in dir_path.relative_to(repo_dir).parts:
                continue
            directories.append(dir_path.relative_to(repo_dir).as_posix())
        return sorted(directories)

    def _collect_files(self, repo_dir: Path) -> list:
        files = []
        for file_path in repo_dir.rglob("*"):
            if not file_path.is_file():
                continue
            relative_path = file_path.relative_to(repo_dir)
            if ".git" in relative_path.parts:
                continue
            files.append(
                {
                    "path": relative_path.as_posix(),
                    "name": file_path.name,
                    "parent": relative_path.parent.as_posix() if relative_path.parent.as_posix() != "." else "",
                    "size": self._safe_file_size(file_path),
                }
            )
        return sorted(files, key=lambda item: item["path"])

    def _collect_repository_tree(self, repo_dir: Path) -> dict:
        def build_node(path: Path, relative_path: Path) -> dict:
            node_path = "" if str(relative_path) == "." else relative_path.as_posix()
            if path.is_file():
                return {
                    "type": "file",
                    "name": path.name,
                    "path": node_path,
                    "size": self._safe_file_size(path),
                }

            children = []
            for child in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
                child_relative = child.relative_to(repo_dir)
                if ".git" in child_relative.parts:
                    continue
                children.append(build_node(child, child_relative))

            return {
                "type": "directory",
                "name": path.name if node_path else "",
                "path": node_path,
                "children": children,
            }

        return build_node(repo_dir, Path("."))

    def _safe_file_size(self, file_path: Path) -> Optional[int]:
        try:
            return file_path.stat().st_size
        except OSError:
            return None

    def _resolve_repo_path(self, repo_dir: Path, requested_path: str) -> Path:
        clean_path = requested_path.replace("\\", "/").strip("/")
        candidate = (repo_dir / clean_path).resolve()
        repo_real = repo_dir.resolve()
        if candidate != repo_real and repo_real not in candidate.parents:
            raise ValueError(f"Path escapes repository root: {requested_path}")
        return candidate

    def _repo_cache_root(
        self,
        storage_dir: Optional[str],
        user_uuid: str,
        repo_url: str,
        course_id: Optional[str] = None,
        exam_id: Optional[str] = None,
        git_branch: Optional[str] = None,
    ) -> Path:
        root = Path(storage_dir).expanduser() if storage_dir and storage_dir.strip() else self._default_storage_root()
        if course_id and exam_id and git_branch:
            return (
                root
                / "Gitrepositorys"
                / self._safe_path_part(user_uuid)
                / self._safe_path_part(course_id)
                / self._safe_path_part(exam_id)
                / self._safe_path_part(git_branch)
            )
        return root / "Gitrepositorys" / self._safe_path_part(user_uuid) / self._safe_path_part(repo_url)

    def _default_storage_root(self) -> Path:
        current_file = Path(__file__).resolve()
        for parent in current_file.parents:
            if parent.name == "AIOralExamSystem":
                return parent.parent
        return Path.cwd()

    def _safe_path_part(self, value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
        return safe.strip("._-") or "repository"

    def _normalize_optional_path(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = str(value).strip()
        return value.strip("/") if value else None

    def _require_text(self, value: str, name: str) -> str:
        if value is None or not str(value).strip():
            raise ValueError(f"{name} is required.")
        return str(value).strip()

    def _should_skip(self, file_path: Path) -> bool:
        parts = set(file_path.parts)
        if ".git" in parts or "__pycache__" in parts:
            return True
        if file_path.name in {".DS_Store"}:
            return True
        if file_path.suffix.lower() in {
            ".7z",
            ".bin",
            ".bmp",
            ".class",
            ".dll",
            ".exe",
            ".gif",
            ".ico",
            ".jar",
            ".jpeg",
            ".jpg",
            ".lock",
            ".mp3",
            ".mp4",
            ".png",
            ".pyc",
            ".rar",
            ".so",
            ".ttf",
            ".webp",
            ".xlsx",
            ".zip",
        }:
            return True
        try:
            return file_path.stat().st_size > 1024 * 1024
        except OSError:
            return True

    def _write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_description(self) -> str:
        return self.description

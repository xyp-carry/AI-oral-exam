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
    repo_url: Optional[str] = Field(default=None, description="Git repository URL.")
    user_uuid: str = Field(description="Current user uuid used to scope repository cache.")
    course_id: Optional[str] = Field(default=None, description="Optional course id for exam-scoped repository cache.")
    exam_id: Optional[str] = Field(default=None, description="Optional exam id for exam-scoped repository cache.")
    git_branch: Optional[str] = Field(default=None, description="Optional branch folder name for exam-scoped repository cache.")
    archive_path: Optional[str] = Field(default=None, description="Optional local zip archive path to extract instead of cloning a git repository.")
    archive_name: Optional[str] = Field(default=None, description="Optional original zip archive file name for logs and manifest metadata.")
    storage_dir: Optional[str] = Field(default=None, description="Optional storage root. Defaults to the project root directory.")
    branch: Optional[str] = Field(default=None, description="Optional branch, tag, or commit-ish to fetch.")
    accelerator_urls: Optional[list[str]] = Field(default=None, description="Optional git or zip accelerator URL prefixes.")
    reload: bool = Field(default=False, description="Force refresh repository cache and fetch the repository again.")


GitRepositoryDescription = (
    "Fetch a git repository URL or extract an uploaded local zip archive for a user. "
    "The repository is stored as-is under Gitrepositorys/{user_uuid}/{repo} or "
    "Gitrepositorys/{user_uuid}/{course_id}/{exam_id}/{git_branch}. "
    "This tool only downloads or extracts the repository and returns repository_root metadata. "
    "It does not parse, read, copy, or classify repository files. "
    "Git clones preserve .git metadata for history queries."
)


class GitRepositoryTool(BaseTool):
    """Fetch and cache user-scoped git repository content."""

    def __init__(self, name: str):
        super().__init__(name)
        self.description = GitRepositoryDescription

    async def _run(
        self,
        repo_url: Optional[str] = None,
        user_uuid: str = "",
        archive_path: Optional[str] = None,
        archive_name: Optional[str] = None,
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
                lambda: self.fetch_repository(
                    repo_url=repo_url,
                    user_uuid=user_uuid,
                    archive_path=archive_path,
                    archive_name=archive_name,
                    storage_dir=storage_dir,
                    branch=branch,
                    accelerator_urls=accelerator_urls,
                    course_id=course_id,
                    exam_id=exam_id,
                    git_branch=git_branch,
                    reload=reload,
                    target_root=target_root,
                ),
            )

    def fetch_repository(
        self,
        repo_url: Optional[str] = None,
        user_uuid: str = "",
        archive_path: Optional[str] = None,
        archive_name: Optional[str] = None,
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
        archive_path = self._normalize_optional_text(archive_path)
        archive_name = self._normalize_optional_text(archive_name)
        source_type = "archive" if archive_path else "git"
        repo_url = self._normalize_optional_text(repo_url)
        if archive_path and not repo_url:
            repo_url = archive_name or Path(archive_path).name
        if not archive_path:
            repo_url = self._require_text(repo_url, "repo_url")
        user_uuid = self._require_text(user_uuid, "user_uuid")
        course_id = self._normalize_optional_path(course_id)
        exam_id = self._normalize_optional_path(exam_id)
        git_branch = self._normalize_optional_path(git_branch)
        branch = branch.strip() if branch and branch.strip() else None
        accelerator_urls = self._get_accelerator_prefixes(accelerator_urls)

        self._append_log(
            logs,
            "start",
            "success",
            "Start fetching repository.",
            repo_url=repo_url,
            user_uuid=user_uuid,
            source_type=source_type,
            course_id=course_id,
            exam_id=exam_id,
            git_branch=git_branch,
            archive_path=archive_path,
            archive_name=archive_name,
            branch=branch,
            reload=reload,
            accelerator_count=len(accelerator_urls),
        )

        try:
            repo_root = (
                Path(target_root).expanduser().resolve()
                if target_root and str(target_root).strip()
                else self._repo_cache_root(storage_dir, user_uuid, repo_url, course_id, exam_id, git_branch)
            )
            manifest_path = self._repo_manifest_path(repo_root)
            self._append_log(logs, "cache_check", "success", "Checking local repository cache.", repository_root=str(repo_root))

            if not reload:
                cached = self._read_cached_result(
                    repo_root,
                    manifest_path,
                    repo_url,
                    branch,
                    source_type,
                    archive_name,
                )
                if cached is not None:
                    self._append_log(logs, "cache_hit", "success", "Matched local cache; using stored repository.")
                    cached["logs"] = logs
                    return json.dumps(cached, ensure_ascii=False)

                self._append_log(logs, "cache_miss", "success", "No matching cache found; preparing repository storage.")
            else:
                self._append_log(logs, "cache_reload", "success", "Reload requested; rebuilding repository storage.")
            if repo_root.exists():
                shutil.rmtree(repo_root)
                self._append_log(logs, "storage_cleanup", "success", "Removed stale repository cache.")
            if manifest_path.exists():
                manifest_path.unlink()

            repo_root.parent.mkdir(parents=True, exist_ok=True)
            if archive_path:
                self._append_log(logs, "store_repository", "start", "Extracting uploaded archive directly into repository root.")
                self._extract_local_zip_repository(archive_path, repo_root, logs)
            else:
                self._append_log(logs, "store_repository", "start", "Cloning repository directly into repository root.")
                self._clone_repository(repo_url, repo_root, branch, accelerator_urls, logs, allow_zip_fallback=False)

            errors = []
            manifest = {
                "repo_url": repo_url,
                "source_type": source_type,
                "archive_name": archive_name,
                "archive_path": archive_path,
                "safe_repo_name": repo_root.name,
                "user_uuid": user_uuid,
                "course_id": course_id,
                "exam_id": exam_id,
                "git_branch": git_branch,
                "branch": branch,
                "storage_root": str(repo_root.parent.parent.parent),
                "repository_root": str(repo_root),
                "layout": "repository_root",
            }
            self._write_json(manifest_path, manifest)
            self._append_log(logs, "manifest_write", "success", "Wrote repository sidecar manifest.", manifest_path=str(manifest_path))

            result = self._build_stored_result(repo_root, manifest, cached=False, errors=errors)
            result["logs"] = logs
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            self._append_log(logs, "error", "error", str(exc))
            return json.dumps(
                {
                    "repo_url": repo_url,
                    "source_type": source_type,
                    "branch": branch,
                    "mode": "error",
                    "cached": False,
                    "error": str(exc),
                    "logs": logs,
                },
                ensure_ascii=False,
            )

    def _read_cached_result(
        self,
        repo_root: Path,
        manifest_path: Path,
        repo_url: str,
        branch: Optional[str],
        source_type: str = "git",
        archive_name: Optional[str] = None,
    ) -> Optional[dict]:
        if not manifest_path.exists():
            return None

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        manifest_source_type = manifest.get("source_type") or "git"
        if manifest_source_type != source_type:
            return None
        if manifest.get("repo_url") != repo_url:
            return None
        if source_type == "archive" and manifest.get("archive_name") != archive_name:
            return None
        if manifest.get("branch") != branch:
            return None
        if not repo_root.exists() or not repo_root.is_dir():
            return None
        if source_type == "git" and not (repo_root / ".git").exists():
            return None

        return self._build_stored_result(repo_root, manifest, cached=True, errors=[])

    def _build_stored_result(self, repo_root: Path, manifest: dict, cached: bool, errors: list) -> dict:
        return {
            "repo_url": manifest.get("repo_url"),
            "source_type": manifest.get("source_type") or "git",
            "archive_name": manifest.get("archive_name"),
            "branch": manifest.get("branch"),
            "mode": "stored_repository",
            "cached": cached,
            "repository_root": str(repo_root),
            "history_available": (repo_root / ".git").exists(),
            "layout": manifest.get("layout") or "repository_root",
            "errors": errors,
        }

    def _clone_repository(
        self,
        repo_url: str,
        target_dir: Path,
        branch: Optional[str],
        accelerator_urls: list[str],
        logs: list,
        allow_zip_fallback: bool = True,
    ) -> None:
        clone_errors = []
        clone_candidates = self._build_clone_url_candidates(repo_url, accelerator_urls)
        for clone_url in clone_candidates:
            command = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "clone"]
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

        if not allow_zip_fallback:
            raise RuntimeError(f"git clone failed: {'; '.join(clone_errors)}")

        try:
            self._append_log(logs, "zip_fallback", "start", "Git clone failed; trying zip archive fallback.")
            self._download_zip_repository(repo_url, target_dir, branch, accelerator_urls, logs)
        except Exception as exc:
            raise RuntimeError(f"git clone failed: {'; '.join(clone_errors)}; zip download failed: {exc}") from exc

    def _extract_local_zip_repository(
        self,
        archive_path: str,
        target_dir: Path,
        logs: list,
    ) -> None:
        zip_path = Path(archive_path).expanduser().resolve()
        if not zip_path.exists() or not zip_path.is_file():
            raise RuntimeError(f"archive file does not exist: {archive_path}")
        if zip_path.suffix.lower() != ".zip":
            raise RuntimeError("only .zip archive is supported.")

        with tempfile.TemporaryDirectory(prefix="git_repository_upload_zip_") as temp_dir:
            extract_dir = Path(temp_dir) / "extract"
            extract_dir.mkdir(parents=True, exist_ok=True)
            self._append_log(logs, "local_zip_extract", "start", "Extracting local zip archive.", archive_path=str(zip_path))
            self._extract_zip_safely(zip_path, extract_dir)
            self._append_log(logs, "local_zip_extract", "success", "Extracted local zip archive.")
            self._move_extracted_repository(extract_dir, target_dir)
            self._append_log(logs, "local_zip_move", "success", "Moved extracted archive to temporary repository.")

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

    def _repo_manifest_path(self, repo_root: Path) -> Path:
        return repo_root.parent / f".{repo_root.name}.manifest.json"

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

    def _normalize_optional_text(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _require_text(self, value: str, name: str) -> str:
        if value is None or not str(value).strip():
            raise ValueError(f"{name} is required.")
        return str(value).strip()

    def _write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_description(self) -> str:
        return self.description



class GitHistoryToolInput(BaseModel):
    repo_path: str = Field(description="Local git repository path.")
    mode: str = Field(default="history", description="history or commit_detail.")
    commit_hash: Optional[str] = Field(default=None, description="Commit hash for commit_detail mode.")


GitHistoryDescription = (
    "Read all local git branch/history information and commit details with read-only git commands. "
    "History mode returns all commits; commit_detail mode reads the requested commit. "
    "If repo_path is not a git repository, return NO_GIT_BRANCH_INFO."
)


class GitHistoryTool(BaseTool):
    """Read-only git history and commit detail tool."""

    def __init__(self, name: str):
        super().__init__(name)
        self.description = GitHistoryDescription

    async def _run(
        self,
        repo_path: str,
        mode: str = "history",
        commit_hash: Optional[str] = None,
    ) -> str:
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor,
                self.read_git_history,
                repo_path,
                mode,
                commit_hash,
            )

    def read_git_history(
        self,
        repo_path: str,
        mode: str = "history",
        commit_hash: Optional[str] = None,
    ) -> str:
        repo = self._resolve_git_repo_path(repo_path)
        if repo is None:
            return json.dumps(
                {
                    "ok": False,
                    "flag": "NO_GIT_BRANCH_INFO",
                    "repo_path": str(repo_path or ""),
                    "message": "没有git分支信息。",
                    "history": [],
                    "commit_detail": None,
                },
                ensure_ascii=False,
            )

        mode = str(mode or "history").strip()
        branch = self._run_git(repo, ["git", "branch", "--show-current"])
        status = self._run_git(repo, ["git", "status", "--short"])

        if mode == "commit_detail":
            commit_hash = self._require_commit_hash(commit_hash)
            detail = self._read_commit_detail(repo, commit_hash)
            return json.dumps(
                {
                    "ok": detail["ok"],
                    "flag": "GIT_COMMIT_DETAIL_READY" if detail["ok"] else "GIT_COMMIT_DETAIL_FAILED",
                    "repo_path": str(repo),
                    "branch": branch["stdout"].strip(),
                    "status": status["stdout"],
                    "history": [],
                    "commit_detail": detail,
                },
                ensure_ascii=False,
            )

        history = self._read_history(repo)
        return json.dumps(
            {
                "ok": history["ok"],
                "flag": "GIT_HISTORY_READY" if history["ok"] else "GIT_HISTORY_FAILED",
                "repo_path": str(repo),
                "branch": branch["stdout"].strip(),
                "status": status["stdout"],
                "history": history["commits"],
                "commit_detail": None,
            },
            ensure_ascii=False,
        )

    def _resolve_git_repo_path(self, repo_path: str) -> Optional[Path]:
        if not repo_path or not str(repo_path).strip():
            return None
        repo = Path(str(repo_path)).expanduser().resolve()
        if not repo.exists() or not repo.is_dir():
            return None
        git_check = self._run_git(repo, ["git", "rev-parse", "--is-inside-work-tree"])
        if not git_check["ok"] or git_check["stdout"].strip() != "true":
            return None
        root = self._run_git(repo, ["git", "rev-parse", "--show-toplevel"])
        if not root["ok"] or not root["stdout"].strip():
            return repo
        return Path(root["stdout"].strip()).resolve()

    def _read_history(self, repo: Path) -> dict:
        pretty = "%H%x1f%h%x1f%ad%x1f%an%x1f%s"
        result = self._run_git(
            repo,
            [
                "git",
                "log",
                "--date=iso-strict",
                f"--pretty=format:{pretty}",
            ],
        )
        commits = []
        if result["ok"]:
            for line in result["stdout"].splitlines():
                parts = line.split("\x1f")
                if len(parts) < 5:
                    continue
                commits.append(
                    {
                        "commit_hash": parts[0],
                        "short_hash": parts[1],
                        "date": parts[2],
                        "author": parts[3],
                        "subject": parts[4],
                    }
                )
        result["commits"] = commits
        return result

    def _read_commit_detail(self, repo: Path, commit_hash: str) -> dict:
        stat = self._run_git(repo, ["git", "show", "--stat", "--find-renames", "--format=fuller", commit_hash])
        patch = self._run_git(repo, ["git", "show", "--patch", "--find-renames", "--format=fuller", commit_hash])
        return {
            "ok": stat["ok"] and patch["ok"],
            "commit_hash": commit_hash,
            "stat": stat["stdout"],
            "patch": patch["stdout"],
            "stderr": "\n".join(text for text in [stat["stderr"], patch["stderr"]] if text),
        }

    def _require_commit_hash(self, value: Optional[str]) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("commit_hash is required for commit_detail mode.")
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", text):
            raise ValueError("commit_hash contains unsupported characters.")
        return text

    def _run_git(self, repo: Path, command: list[str]) -> dict:
        try:
            completed = subprocess.run(
                command,
                cwd=str(repo),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                check=False,
                timeout=30,
            )
        except Exception as exc:
            return {
                "ok": False,
                "returncode": -1,
                "stdout": "",
                "stderr": str(exc),
                "command": command,
            }
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
            "command": command,
        }


GithistoryTool = GitHistoryTool
githistoryTool = GitHistoryTool

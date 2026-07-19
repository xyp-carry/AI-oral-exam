import json
import re
import shutil
from pathlib import Path
from typing import Any

from AIOralExamSystem.Agent.FileReader import (
    DEFAULT_REPORT_NAME,
    REPORT_TEMPLATE_NAME,
    TEMPLATE_DIR,
    ReviewerAgent,
    FileReadGraphState,
    FileRunnerAgent,
)
from AIOralExamSystem.Tool.files.folder_tool import FolderStatsTool
from AIOralExamSystem.Tool.git.git_tool import GitHistoryTool


class AIanalysier:
    """LangGraph orchestration layer for reviewer-driven document validation tasks."""

    def __init__(
        self,
        model_settings: dict,
        thinking: bool = False,
        response_format: bool = True,
        temperature: float = 0,
        mineru_api_key: str | None = None,
        chunk_ai_model_settings: dict | None = None,
        extra_tools: list | None = None,
    ):
        self.model_settings = dict(model_settings or {})
        self.thinking = thinking
        self.response_format = response_format
        self.temperature = temperature
        self.mineru_api_key = mineru_api_key
        self.chunk_ai_model_settings = chunk_ai_model_settings or self.model_settings
        self.extra_tools = list(extra_tools or [])
        self.graph = self.build_graph()

    def latest_execution_result(self, state: FileReadGraphState) -> dict | None:
        done_plan = state.get("done_plan") or []
        if not done_plan:
            return None
        latest = done_plan[-1]
        return latest if isinstance(latest, dict) else {"result": latest}

    def plan_output_summary(self, plan: dict) -> dict:
        return {
            "ok": plan.get("ok"),
            "flag": plan.get("flag"),
            "goal": plan.get("goal"),
            "done": plan.get("done"),
            "plan_count": len(plan.get("plan") or plan.get("read_plan") or []),
            "final_answer_ready": bool(plan.get("final_answer")),
        }

    def sanitize_output_state(self, state: dict) -> dict:
        if not isinstance(state, dict):
            return state
        final_answer = state.get("final_answer")
        if isinstance(final_answer, dict):
            return {
                "ok": state.get("status") != "failed" and bool(final_answer.get("ok", True)),
                "flag": str(final_answer.get("flag") or "FILE_READER_GRAPH_DONE"),
                "status": str(state.get("status") or "done"),
                "finish_reason": str(final_answer.get("finish_reason") or state.get("finish_reason") or ""),
                "answer": str(final_answer.get("answer") or ""),
                "report_path": str(final_answer.get("report_path") or state.get("report_path") or ""),
                "merged_report_path": str(
                    final_answer.get("merged_report_path")
                    or state.get("merged_report_path")
                    or ""
                ),
            }
        return {
            "ok": state.get("status") != "failed",
            "flag": "FILE_READER_GRAPH_DONE",
            "status": str(state.get("status") or "done"),
            "finish_reason": str(state.get("finish_reason") or ""),
            "answer": "",
            "report_path": str(state.get("report_path") or ""),
            "merged_report_path": str(state.get("merged_report_path") or ""),
        }

    def build_graph(self):
        from langgraph.graph import END, StateGraph

        graph = StateGraph(FileReadGraphState)
        graph.add_node('prepare_templates', self.prepare_templates_node)
        graph.add_node('load_template', self.load_next_template_node)
        graph.add_node('runner', self.run_with_runner_agent)
        graph.add_node('merge_templates', self.merge_templates_node)
        graph.add_node('finalize', self.finalize_node)
        graph.set_entry_point('prepare_templates')
        graph.add_conditional_edges(
            'prepare_templates',
            self.route_after_prepare_templates,
            {'load_template': 'load_template', 'finalize': 'finalize'},
        )
        graph.add_edge('load_template', 'runner')
        graph.add_conditional_edges(
            'runner',
            self.route_after_runner,
            {
                'load_template': 'load_template',
                'merge_templates': 'merge_templates',
                'finalize': 'finalize',
            },
        )
        graph.add_edge('merge_templates', 'finalize')
        graph.add_edge('finalize', END)
        return graph.compile()

    def prepare_report_template(self, folder_path: str, report_name: str = DEFAULT_REPORT_NAME) -> str:
        template_path = TEMPLATE_DIR / REPORT_TEMPLATE_NAME
        if not template_path.is_file():
            return ""
        root_path = Path("/root/AI-Oral-exam").resolve()
        raw_folder = Path(str(folder_path or root_path)).expanduser()
        if not raw_folder.is_absolute():
            raw_folder = root_path / raw_folder
        try:
            target_folder = raw_folder.resolve()
            target_folder.relative_to(root_path)
        except (OSError, ValueError):
            return ""
        if not target_folder.is_dir():
            return ""
        output_name = Path(str(report_name or DEFAULT_REPORT_NAME)).name or DEFAULT_REPORT_NAME
        output_path = target_folder / output_name
        try:
            shutil.copyfile(template_path, output_path)
        except OSError:
            return ""
        return str(output_path)

    def resolve_project_folder(self, folder_path: str) -> Path:
        root_path = Path("/root/AI-Oral-exam").resolve(strict=False)
        raw_folder = Path(str(folder_path or root_path)).expanduser()
        if not raw_folder.is_absolute():
            raw_folder = root_path / raw_folder
        resolved = raw_folder.resolve(strict=False)
        try:
            resolved.relative_to(root_path)
        except ValueError:
            return root_path
        return resolved

    def report_template_source_dir(self) -> Path:
        return TEMPLATE_DIR / "neihe" / "report"

    def template_sort_key(self, path: Path) -> tuple[int, str]:
        match = re.match(r"^(\d+)", path.name)
        number = int(match.group(1)) if match else 10**9
        return number, path.name

    def resolve_report_output_path(self, folder_path: str, report_name: str = DEFAULT_REPORT_NAME) -> str:
        target_folder = self.resolve_project_folder(folder_path)
        output_name = Path(str(report_name or DEFAULT_REPORT_NAME)).name or DEFAULT_REPORT_NAME
        return str(target_folder / output_name)

    def resolve_template_work_dir(
        self,
        course_id: str | None,
        exam_id: str | None,
    ) -> Path:
        def safe_component(value: str | None, fallback: str) -> str:
            component = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
            component = component.strip(" .")
            return component if component and component not in {".", ".."} else fallback

        return (
            Path("/root/AI-Oral-exam/.report_work")
            / safe_component(course_id, "unknown_course")
            / safe_component(exam_id, "unknown_exam")
        )

    async def prepare_templates_node(self, state: FileReadGraphState) -> FileReadGraphState:
        source_dir = Path(str(state.get("template_source_dir") or self.report_template_source_dir())).expanduser()
        if not source_dir.is_absolute():
            source_dir = Path("/root/AI-Oral-exam") / source_dir
        source_files = [
            path for path in source_dir.glob("*.md")
            if path.is_file() and re.match(r"^\d+", path.name)
        ] if source_dir.is_dir() else []
        source_files = sorted(source_files, key=self.template_sort_key)
        requested_template = str(state.get("template_name") or "").strip()
        if requested_template:
            selector = Path(requested_template).name
            selector_stem = Path(selector).stem
            numeric_selector = selector_stem.lstrip("0") or "0"
            selected_files = []
            for path in source_files:
                path_number_match = re.match(r"^(\d+)", path.stem)
                path_number = (path_number_match.group(1).lstrip("0") or "0") if path_number_match else ""
                if selector in {path.name, path.stem} or selector_stem in {path.name, path.stem}:
                    selected_files.append(path)
                elif numeric_selector == path_number:
                    selected_files.append(path)
            source_files = selected_files
            if not source_files:
                state["status"] = "failed"
                state["error"] = {
                    "flag": "REPORT_TEMPLATE_SELECTION_NOT_FOUND",
                    "error_message": f"Requested template was not found: {requested_template}",
                    "template_source_dir": str(source_dir),
                    "template_name": requested_template,
                }
                return state
        if not source_files:
            state["status"] = "failed"
            state["error"] = {
                "flag": "REPORT_TEMPLATE_FILES_NOT_FOUND",
                "error_message": "No numeric-prefixed markdown templates were found.",
                "template_source_dir": str(source_dir),
            }
            return state

        work_dir = self.resolve_template_work_dir(
            state.get("course_id"),
            state.get("exam_id"),
        )
        work_dir.mkdir(parents=True, exist_ok=True)
        report_name = Path(
            str(state.get("report_path") or DEFAULT_REPORT_NAME)
        ).name or DEFAULT_REPORT_NAME
        state["report_path"] = str(work_dir / report_name)
        copied_files = []
        for source_file in source_files:
            target_file = work_dir / source_file.name
            shutil.copyfile(source_file, target_file)
            copied_files.append(str(target_file))

        state["template_source_dir"] = str(source_dir)
        state["template_work_dir"] = str(work_dir)
        state["source_template_files"] = [str(path) for path in source_files]
        state["template_files"] = copied_files
        state["template_index"] = 0
        state["chapter_history"] = []
        state["current_template_file"] = ""
        state["current_source_template_file"] = ""
        state["current_template_name"] = ""
        state["current_template_content"] = ""
        state["status"] = "templates_prepared"
        return state

    async def load_next_template_node(self, state: FileReadGraphState) -> FileReadGraphState:
        template_files = list(state.get("template_files") or [])
        template_index = int(state.get("template_index") or 0)
        if template_index >= len(template_files):
            state["status"] = "templates_done"
            return state

        current_file = Path(str(template_files[template_index])).expanduser()
        source_template_files = list(state.get("source_template_files") or [])
        current_source_file = ""
        if template_index < len(source_template_files):
            current_source_file = str(source_template_files[template_index] or "")
        try:
            content = current_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = current_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            state["status"] = "failed"
            state["error"] = {
                "flag": "REPORT_TEMPLATE_READ_FAILED",
                "error_message": str(exc),
                "template_file": str(current_file),
            }
            return state

        state["file_path"] = str(current_file)
        state["current_template_file"] = str(current_file)
        state["current_source_template_file"] = current_source_file
        state["current_template_name"] = current_file.name
        state["current_template_content"] = content
        state["chapter_done_plan_start"] = len(state.get("done_plan") or [])
        state["plan"] = []
        state["status"] = "planning"
        return state

    def prepare_runner_step(self, state: FileReadGraphState, step: dict[str, Any]) -> dict[str, Any]:
        current_file = str(state.get("current_template_file") or state.get("file_path") or "")
        current_name = str(state.get("current_template_name") or "")
        prepared = dict(step or {})
        if current_file:
            prepared.setdefault("target_file", current_file)
            prepared.setdefault("file_path", current_file)
            prepared.setdefault("current_template_file", current_file)
        if current_name:
            prepared.setdefault("current_template_name", current_name)
        return prepared

    def summarize_chapter_result(self, state: FileReadGraphState, chapter_results: list[dict[str, Any]], content: str) -> str:
        summary_parts = []
        for item in chapter_results:
            if not isinstance(item, dict):
                continue
            value = str(item.get("summary") or "").strip()
            if value:
                summary_parts.append(value)
        summary = "\n".join(summary_parts).strip()
        if not summary:
            summary = "Chapter completed; updated content length: " + str(len(content))
        return summary[:2000]

    async def save_chapter_history_node(self, state: FileReadGraphState) -> FileReadGraphState:
        current_file = Path(str(state.get("current_template_file") or state.get("file_path") or "")).expanduser()
        content = ""
        if current_file:
            try:
                content = current_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = current_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = str(state.get("current_template_content") or "")

        start_index = int(state.get("chapter_done_plan_start") or 0)
        done_plan = list(state.get("done_plan") or [])
        chapter_results = [item for item in done_plan[start_index:] if isinstance(item, dict)]
        history = list(state.get("chapter_history") or [])
        history.append(
            {
                "index": int(state.get("template_index") or 0) + 1,
                "file": str(current_file),
                "name": str(state.get("current_template_name") or current_file.name),
                "status": "done" if state.get("status") != "failed" else "failed",
                "summary": self.summarize_chapter_result(state, chapter_results, content),
                "content_chars": len(content),
            }
        )
        state["chapter_history"] = history
        state["current_template_content"] = content
        state["template_index"] = int(state.get("template_index") or 0) + 1
        state["plan"] = []
        state["status"] = "chapter_done"
        return state

    def route_after_prepare_templates(self, state: FileReadGraphState) -> str:
        if state.get("status") == "failed":
            return "finalize"
        return "load_template" if state.get("template_files") else "finalize"

    def route_after_runner(self, state: FileReadGraphState) -> str:
        if state.get('status') == 'failed':
            return 'finalize'
        template_index = int(state.get('template_index') or 0)
        template_files = list(state.get('template_files') or [])
        if template_index < len(template_files):
            return 'load_template'
        return 'merge_templates'

    def process_mode_placeholders(self) -> dict[str, str]:
        return {
            "total_files": "[FIELD:total_files]",
            "code_files": "[FIELD:code_files]",
            "doc_files": "[FIELD:doc_files]",
            "other_files": "[FIELD:other_files]",
            "git_time_range": "[FIELD:git_time_range]",
            "git_commit_count": "[FIELD:git_commit_count]",
        }

    def count_files_by_type(
        self,
        folder_tool: FolderStatsTool,
        folder_path: Path,
        file_type: list[str] | None = None,
    ) -> int:
        result_text = folder_tool.get_file_stats(folder_path, file_type=file_type)
        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            return 0
        if not result.get("ok"):
            return 0
        try:
            return int(result.get("file_count") or 0)
        except (TypeError, ValueError):
            return 0

    def collect_project_file_statistics(self, folder_path: Path) -> dict[str, int]:
        folder_tool = FolderStatsTool("process_mode_folder_stats_tool")
        code_file_types = [
            "c", "cc", "cpp", "cxx", "h", "hpp",
            "py", "rs", "go", "java", "js", "ts", "tsx",
            "sh", "bat", "ps1", "cmake", "sql",
            "Makefile", "Kconfig", "CMakeLists.txt", "Dockerfile",
        ]
        doc_file_types = ["md", "markdown", "txt", "rst", "doc", "docx", "pdf"]

        total_files = self.count_files_by_type(folder_tool, folder_path)
        code_files = self.count_files_by_type(folder_tool, folder_path, code_file_types)
        doc_files = self.count_files_by_type(folder_tool, folder_path, doc_file_types)

        return {
            "total_files": total_files,
            "code_files": code_files,
            "doc_files": doc_files,
            "other_files": max(0, total_files - code_files - doc_files),
        }

    def format_git_date_for_report(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return text.split("T", 1)[0] if "T" in text else text[:10]

    def collect_git_history_statistics(self, folder_path: Path) -> dict[str, str]:
        history_tool = GitHistoryTool("process_mode_git_history_tool")
        result_text = history_tool.read_git_history(str(folder_path), mode="history")
        not_found = "未找到"
        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            return {"git_time_range": not_found, "git_commit_count": "0"}
        if not result.get("ok"):
            return {"git_time_range": not_found, "git_commit_count": "0"}

        history = result.get("history") or []
        if not history:
            return {"git_time_range": not_found, "git_commit_count": "0"}

        newest_date = self.format_git_date_for_report(history[0].get("date", ""))
        oldest_date = self.format_git_date_for_report(history[-1].get("date", ""))
        if oldest_date and newest_date:
            time_range = newest_date if oldest_date == newest_date else f"{oldest_date} 至 {newest_date}"
        else:
            time_range = not_found

        return {
            "git_time_range": time_range,
            "git_commit_count": str(len(history)),
        }

    async def run_process_mode_function(self, state: FileReadGraphState) -> None:
        current_file = Path(str(state.get("current_template_file") or state.get("file_path") or "")).expanduser()
        if not current_file.is_file():
            return
        try:
            content = current_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = current_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        placeholders = self.process_mode_placeholders()
        active_keys = [
            key for key, placeholder in placeholders.items()
            if placeholder in content
        ]
        if not active_keys:
            return

        folder_path = self.resolve_project_folder(str(state.get("folder_path") or "/root/AI-Oral-exam"))
        if not folder_path.is_dir():
            return

        stats = {}
        file_stat_keys = {"total_files", "code_files", "doc_files", "other_files"}
        git_stat_keys = {"git_time_range", "git_commit_count"}
        if file_stat_keys.intersection(active_keys):
            stats.update(self.collect_project_file_statistics(folder_path))
        if git_stat_keys.intersection(active_keys):
            stats.update(self.collect_git_history_statistics(folder_path))

        updated_content = content
        for key in active_keys:
            placeholder = placeholders[key]
            updated_content = updated_content.replace(placeholder, str(stats.get(key, "未找到")))

        if updated_content != content:
            current_file.write_text(updated_content, encoding="utf-8")

    async def merge_templates_node(self, state: FileReadGraphState) -> FileReadGraphState:
        template_files = [
            Path(str(path)).expanduser()
            for path in state.get("template_files") or []
        ]
        if not template_files:
            state["status"] = "failed"
            state["error"] = {
                "flag": "REPORT_TEMPLATE_MERGE_FAILED",
                "error_message": "No copied template files are available to merge.",
            }
            return state

        work_dir_raw = str(state.get("template_work_dir") or "").strip()
        if not work_dir_raw:
            state["status"] = "failed"
            state["error"] = {
                "flag": "REPORT_WORK_DIR_NOT_FOUND",
                "error_message": "The template work directory is empty.",
            }
            return state

        work_root = Path("/root/AI-Oral-exam/.report_work").resolve(strict=False)
        work_dir = Path(work_dir_raw).expanduser().resolve(strict=False)
        try:
            work_dir.relative_to(work_root)
        except ValueError:
            state["status"] = "failed"
            state["error"] = {
                "flag": "REPORT_WORK_DIR_INVALID",
                "error_message": "The template work directory is outside .report_work.",
                "template_work_dir": str(work_dir),
            }
            return state

        output_name = Path(
            str(state.get("report_path") or DEFAULT_REPORT_NAME)
        ).name or DEFAULT_REPORT_NAME
        output_path = work_dir / output_name
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            parts = []
            for template_file in sorted(template_files, key=self.template_sort_key):
                try:
                    parts.append(
                        template_file.read_text(encoding="utf-8").rstrip()
                    )
                except UnicodeDecodeError:
                    parts.append(
                        template_file.read_text(
                            encoding="utf-8", errors="replace"
                        ).rstrip()
                    )
            output_path.write_text(
                "\n\n".join(part for part in parts if part) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            state["status"] = "failed"
            state["error"] = {
                "flag": "REPORT_TEMPLATE_MERGE_FAILED",
                "error_message": str(exc),
                "report_path": str(output_path),
            }
            return state

        state["report_path"] = str(output_path)
        state["merged_report_path"] = str(output_path)
        state["finish_reason"] = state.get("finish_reason") or "all_templates_completed"
        state["status"] = "done"
        return state

    async def execute(
        self,
        user_requirement: str,
        file_path: str = "",
        user_name: str = "",
        course_id: str | None = None,
        exam_id: str | None = None,
        folder_path: str = "/root/AI-Oral-exam",
        max_entries: int = 3000,
        target_tokens: int = 6000,
        report_name: str = DEFAULT_REPORT_NAME,
        template_name: str = "",
        max_iterations: int = 10,
    ) -> dict:
        report_path = self.resolve_report_output_path(folder_path, report_name)
        initial_state: FileReadGraphState = {
            "user_requirement": str(user_requirement or "").strip(),
            "file_path": str(file_path or "").strip(),
            "user_name": str(user_name or "").strip(),
            "course_id": course_id,
            "exam_id": exam_id,
            "folder_path": str(folder_path or "/root/AI-Oral-exam").strip(),
            "max_entries": int(max_entries or 3000),
            "target_tokens": int(target_tokens or 6000),
            "report_path": report_path,
            "template_name": str(template_name or "").strip(),
            "max_iterations": max(1, int(max_iterations or 10)),
            "iteration": 0,
            "template_source_dir": str(self.report_template_source_dir()),
            "template_work_dir": "",
            "source_template_files": [],
            "template_files": [],
            "template_index": 0,
            "current_template_file": "",
            "current_source_template_file": "",
            "current_template_name": "",
            "current_template_content": "",
            "chapter_history": [],
            "chapter_done_plan_start": 0,
            "merged_report_path": "",
            "finish_reason": "",
            "plan": [],
            "done_plan": [],
            "status": "planning",
        }
        try:
            final_state = await self.graph.ainvoke(initial_state, config={"recursion_limit": self.
            graph_recursion_limit(initial_state["max_iterations"])})
            return self.sanitize_output_state(final_state)
        except Exception as exc:
            return {
                "ok": False,
                "flag": "FILE_READER_GRAPH_FAILED",
                "error_class": exc.__class__.__name__,
                "error_message": str(exc),
                "done_plan": initial_state.get("done_plan", []),
            }

    def graph_recursion_limit(self, max_iterations: int) -> int:
        return max(80, int(max_iterations or 1) * 10 + 30)

    def read_target_document_content(self, state: FileReadGraphState, limit: int = 30000) -> str:
        file_path = str(state.get("current_template_file") or state.get("file_path") or "").strip()
        if not file_path:
            return ""
        root_path = Path(str(state.get("folder_path") or "/root/AI-Oral-exam")).expanduser()
        if not root_path.is_absolute():
            root_path = Path("/root/AI-Oral-exam") / root_path
        requested_path = Path(file_path).expanduser()
        if not requested_path.is_absolute():
            requested_path = root_path / requested_path
        try:
            resolved = requested_path.resolve(strict=False)
            resolved_root = root_path.resolve(strict=False)
            if resolved != resolved_root and resolved_root not in resolved.parents:
                return ""
            if not resolved.is_file():
                return ""
            try:
                content = resolved.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        max_chars = max(0, int(limit or 0))
        return content[:max_chars] if max_chars else content

    async def plan_node(self, state: FileReadGraphState) -> FileReadGraphState:
        reviewer = self.new_reviewer(state)
        original_path = str(state.get("current_source_template_file") or "")
        generated_path = str(state.get("current_template_file") or state.get("file_path") or "")
        try:
            original = Path(original_path).read_text(encoding="utf-8") if original_path else ""
            generated = Path(generated_path).read_text(encoding="utf-8") if generated_path else ""
        except OSError as exc:
            state["status"] = "failed"
            state["error"] = {
                "flag": "REVIEW_INPUT_READ_FAILED",
                "error_message": str(exc),
            }
            return state
        result = await reviewer.execute(original, generated)
        state["review_result"] = result
        state["plan"] = []
        if result.get("passed"):
            state["status"] = "chapter_done"
        else:
            state["status"] = "needs_runner_rewrite"
            state["error"] = result
        return state

    async def run_with_runner_agent(self, state: FileReadGraphState) -> FileReadGraphState:
        current_file = str(
            state.get("current_template_file") or state.get("file_path") or ""
        ).strip()
        current_name = str(state.get("current_template_name") or "").strip()
        if not current_file:
            state["status"] = "failed"
            state["finish_reason"] = state.get("finish_reason") or "no_current_template_file"
            state["error"] = {
                "flag": "CURRENT_TEMPLATE_FILE_NOT_FOUND",
                "error_message": "当前步骤没有绑定可处理的模板文件",
            }
            return state

        await self.run_process_mode_function(state)

        current_step = {
            "step": 1,
            "target_file": current_file,
            "file_path": current_file,
            "current_template_file": current_file,
            "current_template_name": current_name,
            "write_mode": "rewrite_only",
            "direction": (
                "先读取当前文件，按照当前模板完成内容，使用 rewriteDocument "
                "写回必要修改。不得新增模板中不存在的模块。"
            ),
            "scope": str(state.get("folder_path") or ""),
            "expected_result": "当前文件已完成填写，未修改的有效内容保持不变。",
        }

        runner = FileRunnerAgent(
            self.model_settings,
            thinking=self.thinking,
            response_format=self.response_format,
            temperature=0.2,
            mineru_api_key=self.mineru_api_key,
            chunk_ai_model_settings=self.chunk_ai_model_settings,
            extra_tools=self.extra_tools,
            outerprompt="",
            allowed_scope_root=state.get("folder_path"),
            extra_allowed_roots=[state.get("template_work_dir", "")],
            show_tool_io=True,
        )
        reviewer = self.new_reviewer(state)
        source_file = str(state.get("current_source_template_file") or "").strip()
        try:
            original_content = (
                Path(source_file).read_text(encoding="utf-8") if source_file else ""
            )
        except UnicodeDecodeError:
            original_content = Path(source_file).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError as exc:
            state["status"] = "failed"
            state["error"] = {
                "flag": "REVIEW_TEMPLATE_READ_FAILED",
                "error_message": str(exc),
                "template_file": source_file,
            }
            return state

        runner_summaries = []
        review_result = {}
        max_attempts = 2
        for attempt in range(max_attempts):
            runner_result = await runner.execute(current_step)
            runner_summary = str(runner_result or "").strip()
            if runner_summary:
                runner_summaries.append(runner_summary)

            try:
                completed_content = Path(current_file).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                completed_content = Path(current_file).read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError as exc:
                state["status"] = "failed"
                state["error"] = {
                    "flag": "TEMPLATE_COMPLETION_CHECK_FAILED",
                    "error_message": str(exc),
                    "template_file": current_file,
                }
                return state

            review_result = await reviewer.execute(
                original_template=original_content,
                ai_document=completed_content,
            )
            if review_result.get("passed"):
                break

            review_reason = str(
                review_result.get("reason") or "未提供具体审查原因"
            ).strip()
            if attempt + 1 >= max_attempts:
                state["status"] = "failed"
                state["error"] = {
                    "flag": "REVIEW_NOT_PASSED",
                    "error_message": "文档审查未通过",
                    "review_reason": review_reason,
                    "template_file": current_file,
                }
                return state

            current_step = dict(current_step)
            current_step["direction"] = (
                "上一轮审查未通过，请根据以下原因重新修改当前文档。"
                "只修改模板 A 中已有的模块和字段，不得新增模块。"
                "修改后请重新检查完整文档。\n"
                "审查原因：\n"
                + review_reason
            )
        try:
            final_content = Path(current_file).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            final_content = Path(current_file).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError as exc:
            state["status"] = "failed"
            state["error"] = {
                "flag": "POST_REVIEW_MARKER_READ_FAILED",
                "error_message": str(exc),
                "template_file": current_file,
            }
            return state

        marker_index = final_content.find("--ps--")
        if marker_index >= 0:
            try:
                Path(current_file).write_text(
                    final_content[:marker_index].rstrip() + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                state["status"] = "failed"
                state["error"] = {
                    "flag": "POST_REVIEW_MARKER_CLEANUP_FAILED",
                    "error_message": str(exc),
                    "template_file": current_file,
                }
                return state

        done_plan = list(state.get("done_plan") or [])
        done_plan.append(
            {
                "iteration": int(state.get("iteration") or 0) + 1,
                "chapter_index": int(state.get("template_index") or 0) + 1,
                "chapter_file": current_file,
                "chapter_name": current_name,
                "step": current_step,
                "summary": "\n".join(runner_summaries).strip(),
                "review_summary": str(review_result.get("reason") or "审核通过").strip(),
            }
        )
        state["plan"] = []
        state["done_plan"] = done_plan
        state["iteration"] = int(state.get("iteration") or 0) + 1
        state["status"] = "chapter_done"
        return await self.save_chapter_history_node(state)

    def build_runner_state(self, state: FileReadGraphState, current_step: dict[str, Any]) -> FileReadGraphState:
        """只向 Runner 传递当前计划步骤、用户需求和文件地址。"""
        return {
            "user_requirement": state.get("user_requirement", ""),
            "current_step": current_step,
            "folder_path": state.get("folder_path"),
            "file_path": state.get("file_path"),
        }

    async def finalize_node(self, state: FileReadGraphState) -> FileReadGraphState:
        final_answer = self.build_final_answer(state)
        previous_status = str(state.get("status") or "")
        state["final_answer"] = final_answer
        state["status"] = "failed" if previous_status == "failed" else "done"
        return state

    def route_after_plan(self, state: FileReadGraphState) -> str:
        if state.get("status") == "failed":
            return "finalize"
        return 'runner' if state.get('plan') else 'merge_templates'

    def actionable_plan_steps(self, plan: Any) -> list[dict[str, Any]]:
        if isinstance(plan, list):
            steps = plan
        elif isinstance(plan, dict):
            steps = plan.get("plan") or plan.get("read_plan") or []
        else:
            steps = []
        if not isinstance(steps, list):
            return []
        return [step for step in steps if isinstance(step, dict)]

    def build_final_answer(self, state: FileReadGraphState) -> dict:
        summaries = []
        existing_final = state.get("final_answer") if isinstance(state.get("final_answer"), dict) else {}
        if existing_final.get("answer"):
            summaries.append(str(existing_final.get("answer")))
        for item in state.get("done_plan") or []:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or "").strip()
            if summary:
                summaries.append(summary)
        error = state.get("error") or {}
        if isinstance(error, dict):
            error_text = str(error.get("error_message") or error.get("flag") or "").strip()
            if error_text:
                summaries.append("Reviewer/Runner failed: " + error_text)
        return {
            "ok": state.get("status") != "failed",
            "flag": "FILE_READER_GRAPH_DONE",
            "answer": "\n".join(summaries).strip(),
            "done_plan": state.get("done_plan") or [],
            "chapter_history": state.get("chapter_history") or [],
            "finish_reason": str(state.get("finish_reason") or "").strip(),
            "report_path": str(state.get("report_path") or ""),
            "merged_report_path": str(state.get("merged_report_path") or ""),
        }

    def new_reviewer(self, state: FileReadGraphState | None = None) -> ReviewerAgent:
        return ReviewerAgent(
            self.model_settings,
            thinking=self.thinking,
            response_format=self.response_format,
            temperature=self.temperature
        )


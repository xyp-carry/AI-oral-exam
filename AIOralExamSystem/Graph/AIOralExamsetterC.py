import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable, TypedDict

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from AIOralExamSystem.Agent.General_Agent import GeneralAgent
from AIOralExamSystem.Agent.FileReader import (
    DEFAULT_REPORT_NAME,
    TEMPLATE_DIR,
    ReviewerAgent,
    FileReadGraphState,
    FileRunnerAgent,
)
from AIOralExamSystem.Exam.report_storage import (
    REPORT_WORK_ROOT,
    resolve_report_work_dir,
)
from AIOralExamSystem.Tool.files.folder_tool import FolderStatsTool
from AIOralExamSystem.Tool.git.git_tool import GitHistoryTool
from AIOralExamSystem.Graph.template_content_loader import materialize_template_modules


class CoreModuleDocumentRef(BaseModel):
    file_path: str = Field(..., description="evidence file path")
    quote_or_summary: str = Field(..., description="direct quote or evidence summary")
    reason: str = Field("", description="why this evidence supports the module")


class CoreModuleVariableInput(BaseModel):
    module_name: str = Field(..., description="completed core module name")
    module_function: str = Field(..., description="main function of the module")
    completion_quality: str = Field("", description="completion quality or implementation quality")
    development_process: str = Field(..., description="development process and completion details")
    authenticity: str = Field(..., description="authenticity assessment: real, suspicious, or abnormal")
    document_refs: list[CoreModuleDocumentRef] = Field(
        default_factory=list,
        description="documents, code, or Git evidence related to this module",
    )


class CoreModuleDocumentAppendInput(BaseModel):
    module_name: str = Field(..., description="existing core module name")
    document_refs: list[CoreModuleDocumentRef] = Field(
        default_factory=list,
        description="documents, code, or Git evidence to append to this module",
    )


class CoreModuleQuestionsInput(BaseModel):
    module_name: str = Field(..., description="existing core module name")
    questions: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "exactly 1 oral-exam question for this selected core module. The item should include "
            "aspect, question, Answer, and optional source references."
        ),
    )


class AIOralExamsetterGraphState(FileReadGraphState, total=False):
    """State channels used only by the oral-exam report setter graph."""

    database_template_modules: list[dict[str, Any]] | None
    template_module_metadata: dict[str, dict[str, Any]]
    current_template_prompt: str
    current_template_provides_questions: bool
    current_template_module_key: str
    current_template_module_configured: bool
    needs_core_question_tool: bool
    core_module_records: dict[str, dict[str, Any]]
    core_module_questions: dict[str, dict[str, Any]]


class AIOralExamsetter:
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
        tool_event_callback: Callable[[str], None] | None = None,
    ):
        self.model_settings = dict(model_settings or {})
        self.thinking = thinking
        self.response_format = response_format
        self.temperature = temperature
        self.mineru_api_key = mineru_api_key
        self.chunk_ai_model_settings = chunk_ai_model_settings or self.model_settings
        self.extra_tools = list(extra_tools or [])
        self.tool_event_callback = tool_event_callback
        self.graph = self.build_graph()

    def latest_execution_result(self, state: AIOralExamsetterGraphState) -> dict | None:
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

    def build_core_module_outerprompt(self) -> str:
        return """
## Core module evidence recording rules
- These tools only record core module facts and evidence into the current run state. They do not edit template files, search placeholders, or write markdown tables.
- listCoreModules: show the records already collected in this run, including module count, names, evidence counts, and evidence references.
- fillCoreModule: create or update one core module record with its function, quality, development process, authenticity, and evidence references.
- addCoreModuleDocument: append documents, code, or Git evidence to an existing core module record.
- fillCoreModuleQuestions: record exactly 1 oral-exam question for one of the selected core modules.
- When you find a new core module, call fillCoreModule. When you find additional evidence for an existing record, call addCoreModuleDocument.
- When the template asks for oral-exam questions, first select the 3 most important core modules, then call fillCoreModuleQuestions once for each selected module.
- Do not create duplicate records for the same module name. If evidence is insufficient, continue searching first; if it still cannot be confirmed, mark authenticity as suspicious.
- If there are more than 3 core modules, keep the 3 most important records and generate questions only for those 3.

## Oral exam question rules
- Select the 3 most important core modules overall.
- Prepare exactly 1 oral-exam question for each selected core module, for 3 questions total.
- Across the 3 questions, cover different aspects when possible:
  1. implementation authenticity
  2. technical understanding
  3. quality, boundary handling, or extension
- Each question should ask one clear core point.
- Each question should include a reference answer and useful evidence references when available.
- Keep the question format consistent with the existing question output format.
"""

    def compact_report_text(self, value: Any) -> str:
        text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
        return " ".join(text.split())

    def core_module_record_key(self, module_name: Any) -> str:
        return self.compact_report_text(module_name)

    def normalize_core_module_record_map(self, records: Any) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        if isinstance(records, dict):
            iterable = records.items()
        else:
            iterable = []
            if isinstance(records, list):
                iterable = (("", record) for record in records)

        for raw_key, record in iterable:
            if not isinstance(record, dict):
                continue
            module = record.get("module")
            if not isinstance(module, dict):
                continue
            key = self.core_module_record_key(raw_key) or self.core_module_record_key(module.get("module_name"))
            if not key:
                continue
            normalized[key] = record
        return normalized

    def merge_core_module_record_maps(self, existing_records: Any, new_records: Any) -> dict[str, dict[str, Any]]:
        merged = self.normalize_core_module_record_map(existing_records)
        for key, record in self.normalize_core_module_record_map(new_records).items():
            if key not in merged:
                merged[key] = record
                continue

            existing_module = merged[key].setdefault("module", {})
            module_data = record.get("module") or {}
            for module_key, value in module_data.items():
                if module_key == "document_refs":
                    continue
                if self.compact_report_text(value):
                    existing_module[module_key] = value
            existing_module["document_refs"] = self.merge_core_module_document_refs(
                existing_module.get("document_refs") or [],
                module_data.get("document_refs") or [],
            )
            merged[key]["tool_result"] = record.get("tool_result") or merged[key].get("tool_result") or {}
        return merged

    def dump_core_module_model(self, value: Any) -> dict[str, Any]:
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "dict"):
            return value.dict()
        return dict(value) if isinstance(value, dict) else {}

    def normalize_core_module_document_refs(self, refs: list[Any]) -> list[dict[str, Any]]:
        normalized = []
        for ref in refs or []:
            data = self.dump_core_module_model(ref)
            file_path = self.compact_report_text(data.get("file_path"))
            quote_or_summary = self.compact_report_text(data.get("quote_or_summary"))
            reason = self.compact_report_text(data.get("reason"))
            if not file_path and not quote_or_summary and not reason:
                continue
            normalized.append(
                {
                    "file_path": file_path,
                    "quote_or_summary": quote_or_summary,
                    "reason": reason,
                }
            )
        return normalized

    def merge_core_module_document_refs(self, existing_refs: list[Any], new_refs: list[Any]) -> list[dict[str, Any]]:
        merged = []
        seen = set()
        for ref in self.normalize_core_module_document_refs(existing_refs) + self.normalize_core_module_document_refs(new_refs):
            key = (
                self.compact_report_text(ref.get("file_path")),
                self.compact_report_text(ref.get("quote_or_summary")),
                self.compact_report_text(ref.get("reason")),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(ref)
        return merged

    def core_module_state_snapshot(self, records: Any) -> dict[str, Any]:
        record_map = self.normalize_core_module_record_map(records)
        return {"record_count": len(record_map), "keys": list(record_map.keys())}

    def upsert_current_core_module_record(
        self,
        records: dict[str, dict[str, Any]],
        payload: CoreModuleVariableInput,
        tool_result: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        module_data = self.dump_core_module_model(payload)
        module_data["document_refs"] = self.normalize_core_module_document_refs(
            module_data.get("document_refs") or []
        )
        key = self.core_module_record_key(module_data.get("module_name"))
        if not key:
            raise ValueError("core module record key is empty")

        if key not in records:
            record = {"module": module_data, "tool_result": tool_result}
            records[key] = record
            return record, True

        existing = records[key]
        existing_module = existing.setdefault("module", {})
        for module_key, value in module_data.items():
            if module_key == "document_refs":
                continue
            if self.compact_report_text(value):
                existing_module[module_key] = value
        existing_module["document_refs"] = self.merge_core_module_document_refs(
            existing_module.get("document_refs") or [],
            module_data.get("document_refs") or [],
        )
        existing["tool_result"] = tool_result
        return existing, False

    def append_current_core_module_documents(
        self,
        records: dict[str, dict[str, Any]],
        module_name: str,
        document_refs: list[Any],
    ) -> dict[str, Any] | None:
        key = self.core_module_record_key(module_name)
        if not key or key not in records:
            return None
        record = records[key]
        module = record.setdefault("module", {})
        module["document_refs"] = self.merge_core_module_document_refs(
            module.get("document_refs") or [],
            document_refs or [],
        )
        return record

    def normalize_core_module_question_map(self, questions: Any) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        if isinstance(questions, dict):
            iterable = questions.items()
        elif isinstance(questions, list):
            iterable = (("", item) for item in questions)
        else:
            iterable = []

        for raw_key, value in iterable:
            if len(normalized) >= 3:
                break
            if not isinstance(value, dict):
                continue
            module_name = self.core_module_record_key(value.get("module_name") or raw_key)
            if not module_name:
                continue
            items = value.get("questions")
            if not isinstance(items, list):
                items = []
            question_items = []
            for item in items:
                normalized_item = self.normalize_core_module_question_item(item)
                if normalized_item.get("question"):
                    question_items = [normalized_item]
                    break
            if not question_items:
                continue
            normalized[module_name] = {
                "ok": bool(value.get("ok", True)),
                "flag": str(value.get("flag") or "CORE_MODULE_QUESTIONS_RECORDED"),
                "module_name": module_name,
                "questions": question_items,
            }
        return normalized

    def merge_core_module_question_maps(self, existing_questions: Any, new_questions: Any) -> dict[str, dict[str, Any]]:
        merged = self.normalize_core_module_question_map(existing_questions)
        for key, value in self.normalize_core_module_question_map(new_questions).items():
            merged[key] = value
        return merged

    def normalize_core_module_question_item(self, value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            return {"aspect": "general", "question": value.strip(), "Answer": "", "source": []}
        if not isinstance(value, dict):
            return {"aspect": "", "question": "", "Answer": "", "source": []}

        question = str(value.get("question") or value.get("Question") or "").strip()
        answer = str(
            value.get("Answer")
            or value.get("answer")
            or value.get("standard_answer")
            or value.get("reference_answer")
            or ""
        ).strip()
        aspect = str(value.get("aspect") or value.get("dimension") or "general").strip() or "general"
        return {
            "aspect": aspect,
            "question": question,
            "Answer": answer,
            "source": self.normalize_core_module_question_sources(value.get("source")),
        }

    def normalize_core_module_question_sources(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            return []

        sources = []
        seen = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            file_path = self.compact_report_text(item.get("file_path") or item.get("path"))
            start_line = item.get("start_line") or item.get("line_start") or item.get("line")
            end_line = item.get("end_line") or item.get("line_end") or item.get("line")
            source = {
                "file_path": file_path,
                "start_line": start_line if isinstance(start_line, int) else None,
                "end_line": end_line if isinstance(end_line, int) else None,
            }
            key = (source["file_path"], source["start_line"], source["end_line"])
            if key in seen or (
                not source["file_path"]
                and source["start_line"] is None
                and source["end_line"] is None
            ):
                continue
            seen.add(key)
            sources.append(source)
        return sources

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
                "core_module_records": final_answer.get("core_module_records") or state.get("core_module_records") or {},
                "core_module_questions": final_answer.get("core_module_questions") or state.get("core_module_questions") or {},
            }
        return {
            "ok": state.get("status") != "failed",
            "flag": "FILE_READER_GRAPH_DONE",
            "status": str(state.get("status") or "done"),
            "finish_reason": str(state.get("finish_reason") or ""),
            "answer": "",
            "report_path": str(state.get("report_path") or ""),
            "merged_report_path": str(state.get("merged_report_path") or ""),
            "core_module_records": state.get("core_module_records") or {},
            "core_module_questions": state.get("core_module_questions") or {},
        }

    def build_graph(self):
        from langgraph.graph import END, StateGraph

        graph = StateGraph(AIOralExamsetterGraphState)
        graph.add_node('prepare_templates', self.prepare_templates_node)
        graph.add_node('load_template', self.load_next_template_node)
        graph.add_node('detect_core_question_tool', self.detect_core_question_tool_node)
        graph.add_node('runner', self.run_with_runner_agent)
        graph.add_node('merge_templates', self.merge_templates_node)
        graph.add_node('finalize', self.finalize_node)
        graph.set_entry_point('prepare_templates')
        graph.add_conditional_edges(
            'prepare_templates',
            self.route_after_prepare_templates,
            {'load_template': 'load_template', 'finalize': 'finalize'},
        )
        graph.add_edge('load_template', 'detect_core_question_tool')
        graph.add_edge('detect_core_question_tool', 'runner')
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
        return TEMPLATE_DIR / "neihe copy" / "report"

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
        return resolve_report_work_dir(course_id, exam_id)

    async def prepare_templates_node(self, state: AIOralExamsetterGraphState) -> AIOralExamsetterGraphState:
        database_modules = state.get("database_template_modules")
        if database_modules is not None:
            work_dir = self.resolve_template_work_dir(
                state.get("course_id"),
                state.get("exam_id"),
            )
            work_dir.mkdir(parents=True, exist_ok=True)
            materialized = materialize_template_modules(work_dir, list(database_modules))
            working_files = list(materialized.get("working_files") or [])
            if not working_files:
                state["status"] = "failed"
                state["error"] = {
                    "flag": "REPORT_TEMPLATE_MODULES_NOT_FOUND",
                    "error_message": "No database template modules were provided.",
                }
                return state
            report_name = Path(
                str(state.get("report_path") or DEFAULT_REPORT_NAME)
            ).name or DEFAULT_REPORT_NAME
            state.update(
                {
                    "report_path": str(work_dir / report_name),
                    "template_source_dir": str(materialized.get("source_dir") or ""),
                    "template_work_dir": str(work_dir),
                    "source_template_files": list(materialized.get("source_files") or []),
                    "template_files": working_files,
                    "template_module_metadata": dict(materialized.get("metadata") or {}),
                    "template_index": 0,
                    "chapter_history": [],
                    "current_template_file": "",
                    "current_source_template_file": "",
                    "current_template_name": "",
                    "current_template_content": "",
                    "current_template_prompt": "",
                    "current_template_provides_questions": False,
                    "current_template_module_configured": False,
                    "needs_core_question_tool": False,
                    "status": "templates_prepared",
                }
            )
            return state

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
        state["needs_core_question_tool"] = False
        state["status"] = "templates_prepared"
        return state

    async def detect_core_question_tool_node(self, state: AIOralExamsetterGraphState) -> AIOralExamsetterGraphState:
        content = str(state.get("current_template_content") or "")
        if not content:
            current_file = str(state.get("current_template_file") or state.get("file_path") or "").strip()
            if current_file:
                try:
                    content = Path(current_file).read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    content = Path(current_file).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = ""

        state["needs_core_question_tool"] = False
        if not content.strip():
            return state
        if state.get("current_template_module_configured"):
            state["needs_core_question_tool"] = bool(
                state.get("current_template_provides_questions")
            )
            return state

        agent = GeneralAgent(
            self.model_settings,
            thinking=self.thinking,
            response_format=True,
            temperature=0,
        )
        response = await agent.execute(
            system_prompt=(
                "Decide whether this report template needs core module table collection. "
                "Return only a JSON object."
            ),
            user_prompt=(
                "If the template contains a core task/module/function table that should be filled "
                "with completed module evidence, return "
                "{\"needs_core_question_tool\": true}; otherwise return "
                "{\"needs_core_question_tool\": false}.\n\n"
                f"Template content:\n{content}"
            ),
        )
        text = agent.message_to_text(response).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
            if fence:
                text = fence.group(1).strip()
            match = re.search(r"\{.*\}", text, re.DOTALL)
            try:
                data = json.loads(match.group(0)) if match else {}
            except json.JSONDecodeError:
                data = {}
        detected = bool(data.get("needs_core_question_tool")) if isinstance(data, dict) else False
        state["needs_core_question_tool"] = detected
        return state

    async def load_next_template_node(self, state: AIOralExamsetterGraphState) -> AIOralExamsetterGraphState:
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

        template_metadata = dict(
            (state.get("template_module_metadata") or {}).get(str(current_file)) or {}
        )
        state["file_path"] = str(current_file)
        state["current_template_file"] = str(current_file)
        state["current_source_template_file"] = current_source_file
        state["current_template_name"] = current_file.name
        state["current_template_content"] = content
        state["current_template_prompt"] = str(template_metadata.get("template_prompt") or "")
        state["current_template_provides_questions"] = bool(template_metadata.get("provides_questions"))
        state["current_template_module_key"] = str(template_metadata.get("module_key") or "")
        state["current_template_module_configured"] = bool(template_metadata)
        state["needs_core_question_tool"] = False
        state["chapter_done_plan_start"] = len(state.get("done_plan") or [])
        state["plan"] = []
        state["status"] = "planning"
        return state

    def prepare_runner_step(self, state: AIOralExamsetterGraphState, step: dict[str, Any]) -> dict[str, Any]:
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

    def summarize_chapter_result(self, state: AIOralExamsetterGraphState, chapter_results: list[dict[str, Any]], content: str) -> str:
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

    async def save_chapter_history_node(self, state: AIOralExamsetterGraphState) -> AIOralExamsetterGraphState:
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

    def route_after_prepare_templates(self, state: AIOralExamsetterGraphState) -> str:
        if state.get("status") == "failed":
            return "finalize"
        return "load_template" if state.get("template_files") else "finalize"

    def route_after_runner(self, state: AIOralExamsetterGraphState) -> str:
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
        not_found = "not found"
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
            time_range = newest_date if oldest_date == newest_date else f"{oldest_date} 闂?{newest_date}"
        else:
            time_range = not_found

        return {
            "git_time_range": time_range,
            "git_commit_count": str(len(history)),
        }

    async def run_process_mode_function(self, state: AIOralExamsetterGraphState) -> None:
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
            updated_content = updated_content.replace(placeholder, str(stats.get(key, "not found")))

        if updated_content != content:
            current_file.write_text(updated_content, encoding="utf-8")

    async def merge_templates_node(self, state: AIOralExamsetterGraphState) -> AIOralExamsetterGraphState:
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

        work_root = REPORT_WORK_ROOT
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
        template_modules: list[dict[str, Any]] | None = None,
    ) -> dict:
        report_path = self.resolve_report_output_path(folder_path, report_name)
        initial_state: AIOralExamsetterGraphState = {
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
            "database_template_modules": list(template_modules) if template_modules is not None else None,
            "template_module_metadata": {},
            "current_template_prompt": "",
            "current_template_provides_questions": False,
            "current_template_module_key": "",
            "current_template_module_configured": False,
            "chapter_history": [],
            "chapter_done_plan_start": 0,
            "merged_report_path": "",
            "finish_reason": "",
            "plan": [],
            "done_plan": [],
            "core_module_records": {},
            "core_module_questions": {},
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

    def read_target_document_content(self, state: AIOralExamsetterGraphState, limit: int = 30000) -> str:
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

    async def plan_node(self, state: AIOralExamsetterGraphState) -> AIOralExamsetterGraphState:
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

    async def run_with_runner_agent(self, state: AIOralExamsetterGraphState) -> AIOralExamsetterGraphState:
        current_file = str(
            state.get("current_template_file") or state.get("file_path") or ""
        ).strip()
        current_name = str(state.get("current_template_name") or "").strip()
        if not current_file:
            state["status"] = "failed"
            state["finish_reason"] = state.get("finish_reason") or "no_current_template_file"
            state["error"] = {
                "flag": "CURRENT_TEMPLATE_FILE_NOT_FOUND",
                "error_message": "No current template file is bound for this step.",
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
                "Read the current template, fill only fields or sections that already exist, "
                "and write necessary changes with rewriteDocument. Do not add unrelated sections."
            ),
            "scope": str(state.get("folder_path") or ""),
            "expected_result": "The current template is completed while preserving valid existing content.",
        }

        core_module_records: dict[str, dict[str, Any]] = {}
        core_module_questions: dict[str, dict[str, Any]] = {}

        @tool("listCoreModules", description="Show current in-run core module record count, names, and related evidence.")
        async def listCoreModules() -> str:
            result = {
                "ok": True,
                "flag": "CORE_MODULE_STATE",
                **self.core_module_state_snapshot(core_module_records),
            }
            print(f"listCoreModules: {result}")
            return json.dumps(result, ensure_ascii=False)

        @tool(
            args_schema=CoreModuleVariableInput,
            description=(
                "Record one confirmed core module and its evidence references. "
                "Call this once for each confirmed core module record. "
                "The return value includes the current record count and evidence state."
            ),
        )
        async def fillCoreModule(
            module_name: str = "",
            module_function: str = "",
            completion_quality: str = "",
            development_process: str = "",
            authenticity: str = "suspicious",
            document_refs: list[CoreModuleDocumentRef] | None = None,
        ) -> str:
            print(f"fillCoreModule: {module_name}, {module_function}, {completion_quality}, {development_process}, {authenticity}, {document_refs}")
            authenticity_value = str(authenticity or "").strip()
            if authenticity_value not in {"real", "suspicious", "abnormal"}:
                authenticity_value = "suspicious"
            payload = CoreModuleVariableInput(
                module_name=module_name,
                module_function=module_function,
                completion_quality=completion_quality,
                development_process=development_process,
                authenticity=authenticity_value,
                document_refs=document_refs or [],
            )
            result = {
                "ok": True,
                "flag": "CORE_MODULE_RECORD_READY",
                "module_name": module_name,
                "document_ref_count": len(document_refs or []),
            }
            if not self.core_module_record_key(module_name):
                result = {
                    "ok": False,
                    "flag": "CORE_MODULE_RECORD_KEY_EMPTY",
                    "message": "module_name is required before recording a core module.",
                    **self.core_module_state_snapshot(core_module_records),
                }
                return json.dumps(result, ensure_ascii=False)
            _, created = self.upsert_current_core_module_record(
                core_module_records,
                payload,
                result,
            )
            result = {
                **result,
                "flag": "CORE_MODULE_RECORD_CREATED" if created else "CORE_MODULE_RECORD_UPDATED",
                **self.core_module_state_snapshot(core_module_records),
            }
            return json.dumps(result, ensure_ascii=False)

        @tool(
            args_schema=CoreModuleDocumentAppendInput,
            description=(
                "Append documents, code, or Git evidence to an existing core module record. "
                "Use listCoreModules first if you are unsure which records exist."
            ),
        )
        async def addCoreModuleDocument(
            module_name: str = "",
            document_refs: list[CoreModuleDocumentRef] | None = None,
        ) -> str:
            print(f"addCoreModuleDocument: {module_name}, {document_refs}")
            record = self.append_current_core_module_documents(
                core_module_records,
                module_name,
                document_refs or [],
            )
            if record is None:
                result = {
                    "ok": False,
                    "flag": "CORE_MODULE_RECORD_NOT_FOUND",
                    "module_name": module_name,
                    "message": "Call fillCoreModule before adding documents to this record.",
                    **self.core_module_state_snapshot(core_module_records),
                }
                return json.dumps(result, ensure_ascii=False)
            result = {
                "ok": True,
                "flag": "CORE_MODULE_RECORD_DOCUMENT_ADDED",
                "module_name": module_name,
                **self.core_module_state_snapshot(core_module_records),
            }
            return json.dumps(result, ensure_ascii=False)

        @tool(
            args_schema=CoreModuleQuestionsInput,
            description=(
                "Record exactly 1 oral-exam question for one of the selected core modules. "
                "The question should include aspect, question, Answer, and optional source."
            ),
        )
        async def fillCoreModuleQuestions(
            module_name: str = "",
            questions: list[dict[str, Any]] | None = None,
        ) -> str:
            print(f"fillCoreModuleQuestions: {module_name}, {questions}")
            key = self.core_module_record_key(module_name)
            if not key:
                result = {
                    "ok": False,
                    "flag": "CORE_MODULE_QUESTION_KEY_EMPTY",
                    "message": "module_name is required before recording questions.",
                    "question_count": 0,
                }
                return json.dumps(result, ensure_ascii=False)

            normalized_questions = []
            for item in questions or []:
                normalized_item = self.normalize_core_module_question_item(item)
                if normalized_item.get("question"):
                    normalized_questions = [normalized_item]
                    break
            core_module_questions[key] = {
                "ok": True,
                "flag": "CORE_MODULE_QUESTIONS_RECORDED",
                "module_name": key,
                "questions": normalized_questions,
            }
            result = {
                "ok": True,
                "flag": "CORE_MODULE_QUESTIONS_RECORDED",
                "module_name": key,
                "question_count": len(normalized_questions),
            }
            return json.dumps(result, ensure_ascii=False)

        runner_extra_tools = list(self.extra_tools)
        runner_outerprompt = ""
        if state.get("needs_core_question_tool"):
            runner_extra_tools.extend([listCoreModules, fillCoreModule, addCoreModuleDocument, fillCoreModuleQuestions])
            runner_outerprompt = self.build_core_module_outerprompt()
        runner = FileRunnerAgent(
            self.model_settings,
            thinking=self.thinking,
            response_format=self.response_format,
            temperature=0.2,
            mineru_api_key=self.mineru_api_key,
            chunk_ai_model_settings=self.chunk_ai_model_settings,
            extra_tools=runner_extra_tools,
            outerprompt=runner_outerprompt,
            allowed_scope_root=state.get("folder_path"),
            extra_allowed_roots=[state.get("template_work_dir", "")],
            show_tool_io=False,
            tool_event_callback=self.tool_event_callback,
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
                review_result.get("reason") or "review did not provide a specific reason"
            ).strip()
            if attempt + 1 >= max_attempts:
                state["status"] = "failed"
                state["error"] = {
                    "flag": "REVIEW_NOT_PASSED",
                    "error_message": "document review did not pass",
                    "review_reason": review_reason,
                    "template_file": current_file,
                }
                return state

            current_step = dict(current_step)
            current_step["direction"] = (
                "The previous review failed. Revise the current document according to this reason, "
                "only editing existing template fields or sections. Review reason:\n"
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

        if core_module_records:
            for record in core_module_records.values():
                if not isinstance(record, dict):
                    continue
                record["template_module_key"] = str(state.get("current_template_module_key") or "")
                record["provides_questions"] = bool(state.get("current_template_provides_questions"))
            state["core_module_records"] = self.merge_core_module_record_maps(
                state.get("core_module_records") or {},
                core_module_records,
            )
            runner_summaries.append(
                f"Collected {len(core_module_records)} core module records."
            )
        if core_module_questions:
            for item in core_module_questions.values():
                if not isinstance(item, dict):
                    continue
                item["template_module_key"] = str(state.get("current_template_module_key") or "")
                item["provides_questions"] = bool(state.get("current_template_provides_questions"))
            state["core_module_questions"] = self.merge_core_module_question_maps(
                state.get("core_module_questions") or {},
                core_module_questions,
            )
            runner_summaries.append(
                f"Recorded questions for {len(core_module_questions)} core modules."
            )
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
                "review_summary": str(review_result.get("reason") or "review passed").strip(),
                "needs_core_question_tool": bool(state.get("needs_core_question_tool")),
                "core_module_record_count": len(core_module_records),
                "core_module_question_module_count": len(core_module_questions),
            }
        )
        state["plan"] = []
        state["done_plan"] = done_plan
        state["iteration"] = int(state.get("iteration") or 0) + 1
        state["status"] = "chapter_done"
        return await self.save_chapter_history_node(state)

    def build_runner_state(self, state: AIOralExamsetterGraphState, current_step: dict[str, Any]) -> dict[str, Any]:
        """Build the minimal state passed to FileRunnerAgent."""
        return {
            "user_requirement": state.get("user_requirement", ""),
            "current_step": current_step,
            "folder_path": state.get("folder_path"),
            "file_path": state.get("file_path"),
        }

    async def finalize_node(self, state: AIOralExamsetterGraphState) -> AIOralExamsetterGraphState:
        final_answer = self.build_final_answer(state)
        previous_status = str(state.get("status") or "")
        state["final_answer"] = final_answer
        state["status"] = "failed" if previous_status == "failed" else "done"
        return state

    def route_after_plan(self, state: AIOralExamsetterGraphState) -> str:
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

    def build_final_answer(self, state: AIOralExamsetterGraphState) -> dict:
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
            "core_module_records": state.get("core_module_records") or {},
            "core_module_questions": state.get("core_module_questions") or {},
        }

    def new_reviewer(self, state: AIOralExamsetterGraphState | None = None) -> ReviewerAgent:
        return ReviewerAgent(
            self.model_settings,
            thinking=self.thinking,
            response_format=self.response_format,
            temperature=self.temperature
        )








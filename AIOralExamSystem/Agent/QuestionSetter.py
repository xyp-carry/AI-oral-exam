import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from AIOralExamSystem.Agent.base_Agent import BaseAgent
from AIOralExamSystem.Tool.files.info_search_tool import (
    FileReadTool,
    InfoSearchTool,
)


PROJECT_ROOT = Path("/root/AI-Oral-exam").resolve()


class QuestionSetterSearchInput(BaseModel):
    query: str = Field(..., description="要在绑定文档范围内搜索的关键词或短语。")
    file_globs: list[str] = Field(default_factory=list, description="可选文件匹配规则，例如 *.md、*.txt。")
    case_sensitive: bool = Field(default=False, description="是否区分大小写。")
    regex: bool = Field(default=False, description="是否按正则表达式搜索。")
    context_lines: int = Field(default=1, description="每个匹配项附带的上下文行数，范围 0-5。")
    max_matches: int = Field(default=50, description="最大匹配数。")


class QuestionSetterReadInput(BaseModel):
    file_path: str = Field(..., description="要读取的文件路径，必须位于绑定文档范围内。")
    start_line: Optional[int] = Field(default=None, description="可选起始行号，从 1 开始。")
    end_line: Optional[int] = Field(default=None, description="可选结束行号。")
    max_bytes: int = Field(default=120_000, description="最多返回的 UTF-8 字节数。")


class QuestionSetterAgent(BaseAgent):
    """根据模块内容和文档证据生成用于口试核验的问题。"""

    def __init__(
        self,
        model_settings: dict,
        document_scope: str,
        thinking: bool = False,
        response_format: bool = True,
        temperature: float = 0,
        show_tool_io: bool = False,
        tool_event_callback: Callable[[str], None] | None = None,
    ):
        self.document_scope = self.resolve_document_scope(document_scope)
        super().__init__(
            "QuestionSetterAgent",
            model_settings,
            thinking=thinking,
            response_format=response_format,
            temperature=temperature,
            show_tool_io=show_tool_io,
            tool_event_callback=tool_event_callback,
        )
        self.system_prompt = self.build_system_prompt()

    def resolve_document_scope(self, document_scope: str) -> Path:
        raw_scope = str(document_scope or "").strip()
        if not raw_scope:
            raise ValueError("document_scope is required")
        scope = Path(raw_scope).expanduser()
        if not scope.is_absolute():
            scope = PROJECT_ROOT / scope
        resolved = scope.resolve(strict=False)
        try:
            resolved.relative_to(PROJECT_ROOT)
        except ValueError as exc:
            raise ValueError("document_scope must be inside /root/AI-Oral-exam") from exc
        return resolved

    def build_system_prompt(self) -> str:
        return """
## 角色
你是口试出题者，负责根据项目模块内容和相关文档证据生成问题，用来判断模块是否由学生本人实现，并考察学生对核心实现和关键知识点的理解。

## 可用工具
- docInfoSearch：只能在系统绑定的文档范围内搜索相关材料。
- docReadFile：只能读取系统绑定文档范围内的文件或行区间。

## 工作要求
1. 必须围绕输入模块生成问题，题目贴合模块的具体实现和证据材料。
2. 如果输入的模块内容或文档引用不足，先用 docInfoSearch/docReadFile 补充证据。
3. 为输入模块生成恰好 1 个口试问题，问题应优先围绕该模块最能体现本人实现、技术理解或质量边界的核心点。
4. 每个问题只问一个核心点，题面适合口试中直接提问。
5. 每个问题必须给出参考答案，参考答案用于教师阅卷或口试追问参考。
6. 问题对象使用 {"aspect": "...", "question": "...", "Answer": "...", "source": [...]}。aspect 可从 implementation_authenticity、technical_understanding、quality_and_extension 中选择最适合的一项；source 是证据引用列表，格式为 [{"file_path": "...", "start_line": 1, "end_line": 10}]；没有可用证据时使用空列表。

## 输出
只返回 JSON 对象：
{
  "ok": true,
  "flag": "QUESTION_SET_GENERATED",
  "module_name": "模块名称",
  "questions": [
    {
      "aspect": "implementation_authenticity",
      "question": "围绕该核心模块最关键实现点的口试问题",
      "Answer": "reference answer",
      "source": [
        {"file_path": "path/to/evidence.py", "start_line": 1, "end_line": 10}
      ]
    }
  ],
  "missing_information": []
}
"""

    def get_tools(self):
        @tool(
            args_schema=QuestionSetterSearchInput,
            description="在绑定文档范围内搜索模块相关文档。scope_path 由系统固定，调用者不能修改。",
        )
        async def docInfoSearch(
            query: str,
            file_globs: list[str] | None = None,
            case_sensitive: bool = False,
            regex: bool = False,
            context_lines: int = 1,
            max_matches: int = 50,
        ) -> str:
            search_tool = InfoSearchTool("question_setter_info_search")
            return await search_tool.execute(
                scope_path=str(self.document_scope),
                query=query,
                file_globs=file_globs or [],
                case_sensitive=case_sensitive,
                regex=regex,
                context_lines=context_lines,
                max_matches=max_matches,
                timeout_seconds=10,
            )

        @tool(
            args_schema=QuestionSetterReadInput,
            description="读取绑定文档范围内的文件或行区间。scope_path 由系统固定，调用者不能修改。",
        )
        async def docReadFile(
            file_path: str,
            start_line: Optional[int] = None,
            end_line: Optional[int] = None,
            max_bytes: int = 120_000,
        ) -> str:
            read_tool = FileReadTool("question_setter_file_read")
            return await read_tool.execute(
                scope_path=str(self.document_scope),
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                max_bytes=max_bytes,
            )

        return [docInfoSearch, docReadFile]

    async def execute(
        self,
        module_name: str,
        module_content: dict | str,
        document_refs: list[dict] | None = None,
    ) -> dict:
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()
        try:
            messages = [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": self.build_user_prompt(
                        module_name=module_name,
                        module_content=module_content,
                        document_refs=document_refs or [],
                    ),
                },
            ]
            response = await self.agent.ainvoke({"messages": messages})
            data = self.extract_json_object(self.message_to_text(response))
            return self.normalize_question_set(data, module_name)
        except Exception as exc:
            return {
                "ok": False,
                "flag": "QUESTION_SET_FAILED",
                "module_name": str(module_name or "").strip(),
                "questions": [],
                "evidence": [],
                "missing_information": [],
                "error_class": exc.__class__.__name__,
                "error_message": str(exc),
            }
        finally:
            await self.stop_heartbeat()

    def build_user_prompt(
        self,
        module_name: str,
        module_content: dict | str,
        document_refs: list[dict],
    ) -> str:
        return f"""
绑定文档范围：
{self.document_scope}

模块名称：
{str(module_name or "").strip()}

模块内容：
{self.format_prompt_value(module_content)}

已有相关文档引用：
{self.format_prompt_value(document_refs)}

请基于上述模块和文档证据生成口试问题。如果证据不足，请先使用工具在绑定文档范围内搜索或读取文档。
"""

    def format_prompt_value(self, value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, indent=2)
        return str(value or "").strip()

    def message_to_text(self, response) -> str:
        if isinstance(response, dict):
            messages = response.get("messages")
            if messages:
                return self.message_to_text(messages[-1])
            if response.get("content") is not None:
                return self.message_to_text(response["content"])
        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(
                str(item.get("text", item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content or "")

    def extract_json_object(self, text: str) -> dict:
        cleaned = str(text or "").strip()
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not json_match:
                raise
            data = json.loads(json_match.group(0))
        if not isinstance(data, dict):
            raise ValueError("QuestionSetterAgent response must be a JSON object")
        return data

    def normalize_question_set(self, data: dict, module_name: str) -> dict:
        questions = data.get("questions")
        if not isinstance(questions, list):
            questions = data.get("aspect_questions")
        return {
            "ok": bool(data.get("ok", True)),
            "flag": str(data.get("flag") or "QUESTION_SET_GENERATED"),
            "module_name": str(data.get("module_name") or module_name or "").strip(),
            "questions": self.normalize_aspect_question_list(questions, limit=1),
            "evidence": self.normalize_evidence(data.get("evidence")),
            "missing_information": self.normalize_text_list(data.get("missing_information")),
        }

    def normalize_aspect_question_list(self, value: Any, limit: int) -> list[dict]:
        if not isinstance(value, list):
            return []
        questions = []
        for item in value[:limit]:
            question = self.normalize_aspect_question_item(item)
            if question.get("question"):
                questions.append(question)
        return questions

    def normalize_aspect_question_item(self, value: Any) -> dict:
        item = self.normalize_question_item(value)
        if isinstance(value, dict):
            aspect = str(value.get("aspect") or value.get("dimension") or "").strip()
            source_value = value.get("source")
        else:
            aspect = ""
            source_value = []
        item["aspect"] = aspect
        item["source"] = self.normalize_question_sources(source_value)
        return item

    def normalize_question_item(self, value: Any) -> dict:
        if isinstance(value, str):
            return {
                "question": value.strip(),
                "Answer": "",
            }
        if not isinstance(value, dict):
            return {
                "question": "",
                "Answer": "",
            }

        answer = (
            value.get("Answer")
            or value.get("answer")
            or value.get("standard_answer")
            or value.get("reference_answer")
            or ""
        )
        if not answer:
            expected_points = self.normalize_text_list(value.get("expected_answer_points"))
            answer = "; ".join(expected_points)

        return {
            "question": str(value.get("question") or "").strip(),
            "Answer": str(answer or "").strip(),
        }

    def normalize_implementation_question(self, value: Any) -> dict:
        item = self.normalize_question_item(value)
        source_value = value.get("source") if isinstance(value, dict) else []
        item["source"] = self.normalize_question_sources(source_value)
        return item

    def normalize_question_sources(self, value: Any) -> list[dict]:
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            return []

        sources = []
        seen = set()
        for item in value:
            source = self.normalize_question_source(item)
            if not source:
                continue
            key = (source["file_path"], source["start_line"], source["end_line"])
            if key in seen:
                continue
            seen.add(key)
            sources.append(source)
        return sources

    def normalize_question_source(self, value: Any) -> dict | None:
        if not isinstance(value, dict):
            return None

        file_path = str(value.get("file_path") or value.get("path") or "").strip()
        start_line = self.normalize_line_number(
            value.get("start_line")
            or value.get("line_start")
            or value.get("line_number")
            or value.get("line")
        )
        end_line = self.normalize_line_number(
            value.get("end_line")
            or value.get("line_end")
            or value.get("line_number")
            or value.get("line")
        )
        if start_line is not None and end_line is None:
            end_line = start_line
        if end_line is not None and start_line is None:
            start_line = end_line
        if start_line is not None and end_line is not None and end_line < start_line:
            start_line, end_line = end_line, start_line
        if not file_path and start_line is None and end_line is None:
            return None

        return {"file_path": file_path, "start_line": start_line, "end_line": end_line}

    def normalize_line_number(self, value: Any) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    def normalize_question_list(self, value: Any, limit: int) -> list[dict]:
        if not isinstance(value, list):
            return []
        return [self.normalize_question_item(item) for item in value[:limit]]

    def normalize_knowledge_point(self, value: Any) -> dict:
        if not isinstance(value, dict):
            return {"name": "", "reason": ""}
        return {
            "name": str(value.get("name") or "").strip(),
            "reason": str(value.get("reason") or "").strip(),
        }

    def normalize_evidence(self, value: Any) -> list[dict]:
        if not isinstance(value, list):
            return []
        evidence = []
        for item in value:
            if not isinstance(item, dict):
                continue
            evidence.append(
                {
                    "file_path": str(item.get("file_path") or "").strip(),
                    "line_number": item.get("line_number"),
                    "reason": str(item.get("reason") or "").strip(),
                }
            )
        return evidence

    def normalize_text_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

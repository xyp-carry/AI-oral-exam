import json
import re
from pathlib import Path
from typing import Any, Optional

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
    """根据模块内容和文档证据生成用于口试核验的分级问题。"""

    def __init__(
        self,
        model_settings: dict,
        document_scope: str,
        thinking: bool = False,
        response_format: bool = True,
        temperature: float = 0,
        show_tool_io: bool = False,
    ):
        self.document_scope = self.resolve_document_scope(document_scope)
        super().__init__(
            "QuestionSetterAgent",
            model_settings,
            thinking=thinking,
            response_format=response_format,
            temperature=temperature,
            show_tool_io=show_tool_io,
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
你是口试出题者，负责根据项目模块内容和相关文档证据生成问题，用来判断模块是否由学生本人实现，并围绕最重要知识点进行分级追问。

## 可用工具
- docInfoSearch：只能在系统绑定的文档范围内搜索相关材料。
- docReadFile：只能读取系统绑定文档范围内的文件或行区间。

## 工作要求
1. 必须围绕输入模块生成问题，不要泛泛问概念。
2. 如果输入的模块内容或文档引用不足，先用 docInfoSearch/docReadFile 补充证据。
3. 生成 1 个 implementation_question，用于判断学生是否亲自实现该模块。
4. implementation_question 应关注关键流程、设计取舍、边界处理、调试细节或具体实现路径。
5. 找出该模块中最重要的 1 个知识点，并说明理由。
6. 围绕该知识点分别生成 easy、medium、hard 三个等级的问题，每个等级恰好 2 个。
7. 每个问题只问一个核心点，不要连问，不要把多个问题合并成一句。
8. 问题应能在口试中直接提问，避免在 question 字段泄露标准答案。
9. 每个问题必须给出参考答案，参考答案用于教师阅卷或口试追问参考。
10. 所有问题对象必须统一使用 {"question": "...", "Answer": "..."}，Answer 必须是字符串，不要使用数组。

## 输出
只返回 JSON 对象：
{
  "ok": true,
  "flag": "QUESTION_SET_GENERATED",
  "module_name": "模块名称",
  "implementation_question": {
    "question": "用于判断是否本人实现的问题",
    "Answer": "参考答案"
  },
  "key_knowledge_point": {
    "name": "最重要知识点",
    "reason": "选择理由"
  },
  "leveled_questions": {
    "easy": [
      {"question": "简单问题1", "Answer": "参考答案1"},
      {"question": "简单问题2", "Answer": "参考答案2"}
    ],
    "medium": [
      {"question": "一般问题1", "Answer": "参考答案1"},
      {"question": "一般问题2", "Answer": "参考答案2"}
    ],
    "hard": [
      {"question": "困难问题1", "Answer": "参考答案1"},
      {"question": "困难问题2", "Answer": "参考答案2"}
    ]
  },
  "evidence": [
    {
      "file_path": "证据文件路径",
      "line_number": 1,
      "reason": "该证据支撑的问题或知识点"
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
                "implementation_question": {},
                "key_knowledge_point": {},
                "leveled_questions": {"easy": [], "medium": [], "hard": []},
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
        levels = data.get("leveled_questions") if isinstance(data.get("leveled_questions"), dict) else {}
        return {
            "ok": bool(data.get("ok", True)),
            "flag": str(data.get("flag") or "QUESTION_SET_GENERATED"),
            "module_name": str(data.get("module_name") or module_name or "").strip(),
            "implementation_question": self.normalize_question_item(data.get("implementation_question")),
            "key_knowledge_point": self.normalize_knowledge_point(data.get("key_knowledge_point")),
            "leveled_questions": {
                "easy": self.normalize_question_list(levels.get("easy"), limit=2),
                "medium": self.normalize_question_list(levels.get("medium"), limit=2),
                "hard": self.normalize_question_list(levels.get("hard"), limit=2),
            },
            "evidence": self.normalize_evidence(data.get("evidence")),
            "missing_information": self.normalize_text_list(data.get("missing_information")),
        }

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

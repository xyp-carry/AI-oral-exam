import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from AIOralExamSystem.Agent.General_Agent import GeneralAgent
from AIOralExamSystem.Agent.FileReader import (
    DEFAULT_REPORT_NAME,
    REPORT_TEMPLATE_NAME,
    TEMPLATE_DIR,
    ReviewerAgent,
    FileReadGraphState,
    FileRunnerAgent,
)
from AIOralExamSystem.Agent.QuestionSetter import QuestionSetterAgent
from AIOralExamSystem.Tool.files.folder_tool import FolderStatsTool
from AIOralExamSystem.Tool.git.git_tool import GitHistoryTool


class CoreModuleDocumentRef(BaseModel):
    file_path: str = Field(..., description="evidence file path")
    quote_or_summary: str = Field(..., description="direct quote or evidence summary")
    reason: str = Field("", description="why this evidence supports the module")


class CoreModuleVariableInput(BaseModel):
    target_field: str = Field("module_table", description="target template field, fixed to module_table")
    module_name: str = Field(..., description="completed core module name")
    module_function: str = Field(..., description="main function of the module")
    completion_quality: str = Field(..., description="completion quality assessment")
    development_process: str = Field(..., description="development process and completion details")
    authenticity: str = Field(..., description="authenticity assessment: real, suspicious, or abnormal")
    document_refs: list[CoreModuleDocumentRef] = Field(
        default_factory=list,
        description="documents, code, or Git evidence related to this module",
    )


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

    def build_core_module_outerprompt(self) -> str:
        return """

## 闂傚倷绀侀幖顐ょ矓閸洍鈧箓宕奸姀銏㈠闂佽鍨奸悘娑㈡偄閻撳海浼嬮梺鎯ф禋閸嬪嫭绂掗幘顔解拺闁告稑锕﹂幊鍕煥閺囨ê鐏茬€规洘濞婇幃婊兾熼懖鈺冩毌婵＄偑鍊栭崝鎴﹀垂閸︻厽鏆滈柣妯肩帛閸嬧剝绻涢崱妤冪妞ゅ繆鏅犻弻?
- 闂佽崵鍠愮划搴㈡櫠濡ゅ懎绠板瀣捣缁€濠冧繆椤栨艾鎮戦柛蹇旂矋閵囧嫰寮埀顒勵敄濞嗘挸瑙﹂柛銉戔偓濡插牓鏌熺紒銏犵仩濞存粎鍋撶换娑㈠箣閻愬瓨鍎庢繛瀛樼矤娴滎亝淇婇弶鎴悑濠㈣泛顑呴崜顔碱渻閵堝棙鈷掗柛妯犲洤鍚归柟鐑橆殕閸婄敻鏌ｉ悢鍝勵暭婵犫偓娴煎瓨鐓曢柕濠忕畱閸濇椽鏌熼姘殻鐎规洜鍠栭、妤呭磼濮樺吋顥撻梻鍌氼煬閸嬪嫬煤閵堝鐤い鏍ㄧ箓閺嗙偤姊绘担渚綊闁告洖鐏氶悾宄扳攽閿涘嫬浠滈柛濠傛健瀵偄顓奸崶锔藉媰闂佷紮绲介惉濂告偩鏉堛劋绻嗛柣鎰典簻閳ь剚绋戦悾鐑芥偨閸涘﹤浠у┑鐐村灟閸ㄥ湱娑甸埀顒勬煟鎼粹剝璐″┑顔煎槻閳绘捇宕奸弴鐔哄幗?fillCoreModuleVariable闂?
- 闂備浇宕垫慨鎾敄閸涙潙鐤ù鍏兼綑閺?fillCoreModuleVariable 闂傚倷绀侀幉锟犲箰閸濄儳鐭欓柛鏇ㄥ幗椤洘绻濋棃娑冲姛闁汇倐鍋撻梻浣告啞缁嬫帡鎮鹃鍫濈劦妞ゆ巻鍋撶紒缁樏悾宄拔旈崨顓㈠敹濠电姴锕ら崯鐘诲几韫囨稒鈷?infoSearch闂傚倷绶氬褍螞閺冨倹瀚婚柣鐘垫對dFile 闂?gitHistoryReader 闂傚倷鑳堕幊鎾绘倶濮樿泛纾块柟鎯版閺勩儳鈧厜鍋撻柍褜鍓熼獮蹇涙偐鐠囧弬銊╁嫉椤忓懐鐟归柍褜鍓欓悾椋庣矙鐠囩偓妫冨畷姗€鍩￠崘锕€浠滈梻鍌欒兌椤牏鎹㈤幇鐗堝仾闁搞儺鍓氶崕濠傤熆閼搁潧濮囩紒鐘插级閵囧嫰寮崶銉㈠亾閳ь剟鏌?
- 濠电姵顔栭崳顖滃緤閻ｅ本宕查悗锝庡枟閻撳倹绻濇繝鍌滃闁告劏鍋撶紓鍌欑椤戝牆鈻旈弴鐔剁箚闁搞儯鍔嬬换鍡樸亜閺嶃劊浠滈柛瀣崌閹煎綊顢曢～顓熸▕闂傚倷绀侀幉锟犮€冮崱妞曞搫顭ㄩ崨顏勪壕婵鍘ч獮姗€鏌熸總澶婁喊鐎规洘锕㈤、鏃堝川椤旂瓔鍚傛繝鐢靛仦閸ㄥ爼骞愰幘顔肩；闁规儳顕粻?fillCoreModuleVariable闂傚倷鐒︾€笛呯矙閹寸偟闄勯柡鍐ㄥ€荤粻鏂款熆鐠虹儤婀伴柛鐔锋惈闇夐柨婵嗘处閸も偓闁诡垳鍠愮换婵嬪閿濆懐鍘梺娲诲弾閸ｏ綁寮荤仦绛嬬叆闁稿繐澧介崰鏍垂妤ｅ啯鎯炴い鎰垫線濞ｎ噣姊绘担鍛婂暈闁煎綊绠栭幃褔宕卞☉娆忔闂佺粯姊婚崢褏绮婚敐澶嬬厵闂侇叏缂氱花鑺ャ亜韫囨岸鍝虹紒缁樼洴瀹曠増骞婇柛濠冾殔閳绘捇宕奸弴鐔哄幗濡炪値鍋掗崜娆愪繆閹间焦鐓?
- fillCoreModuleVariable 婵犵數鍋炲娆撳触鐎ｎ偆鈹嶉柧蹇撴贡閻棝鎮楅敐搴″閻庢艾顭烽弻銊モ攽閸℃ê鐝旂紓鍌氱У閻楃娀寮?module_table 闂傚倷绀侀幉锟犳偡閿曞倹鏅濋柕蹇嬪€曢梻顖涚箾瀹割喕绨奸柡鍜佸墯缁绘盯骞嬮悜鍡樼暭缂備礁顑嗛崹鐢稿煡婢舵劕绠荤€规洖娉﹂妷锔轰簻闁冲搫顑囬悾鐢告煙椤栨艾顏柍褜鍓氱粙鎺楁晪婵犮垼娉涚粔褰掑蓟?rewriteDocument 婵犵數濞€濞佳囁囨禒瀣；闁告洦鍨伴悿?module_table闂?
- document_refs 闂傚倸顭崑鍕洪妶澶婄疇婵せ鍋撳┑锛勵棎缁犳盯骞欓崘銊︻吙闂備礁鎼ú銊︽叏閻㈢姹查煫鍥ㄦ惄濞撳鏌曢崼婵囧櫤閻犳劏鍓濈换娑㈢叓椤撶偛绁悗瑙勬礃閿曘垹鐣烽妸鈺婃晬婵炴垶顭囬崝顖炴⒑鐠囨煡顎楁繝鈧柆宥呯；婵炴垯鍨洪崑銈夋煏婵炵偓娅呴柟鐟扮埣閺屾洘绻涜鐎氼噣寮抽悩缁樷拺闁告繂瀚埀顒傤焾鐓ら柨鏇炲€歌繚闂佺鐬奸崑娑滅箽濠电偠鎻徊浠嬪床閺屻儱鏋侀柍鍝勬噺閻撶喐銇勯弮鍥у惞闁告柨绉归弻锛勨偓锝庝簻閺嗙偟绱掗崒娑樻诞闁硅櫕绮撳Λ鍐ㄢ槈濮橆偆鐜?Git 闂備浇宕垫慨鏉懨洪敐澶嬪€块柨鏇楀亾闁伙絽鐏氱粭鐔煎焵椤掑嫬鏋佺€广儱娲ｅ▽顏堟煠濞村娅囬柟鎻掔秺濮婃椽鎮℃惔锝忕礊闂佸搫鎷嬮崑鍡椢ｉ幇閭︽晜闁割偆鍠撻崝鐢告⒑缂佹﹩鐒炬繛鍜冪秮閹垽宕ㄩ妤€浜鹃柛顭戝亝缁舵煡鎮楀鐓庡⒋妤犵偛绻橀幃褔宕奸姀銏″殞婵犵數濞€濞佳兠洪妶鍥╃焾闁挎洖鍊归悡?
- 婵犵數濮烽。浠嬪焵椤掆偓閸熷潡鍩€椤掆偓缂嶅﹪骞冨Ο璇茬窞濠电偑鍨婚崰鏍垂妤ｅ啯鎯炴い鎰垫線濞ｎ噣姊绘担鍛婃儓闁绘妫濊棟妞ゆ洍鍋撶€规洦鍓熼、妤呭礋椤掆偓閸撶儤绻涙潏鍓у埌闁硅姤绮撳鑸电鐎ｎ偆鍘藉┑鐘诧工閻楁粓寮抽幒鏃傜＜闁圭粯甯掗埛鏃傜磼鏉堛劍宕岀€规洦鍋婂畷鐔碱敇閻旂儤袙闂傚倷绀侀幖顐﹀磹缁嬫５娲晝閸屾銉╂煕鐏炲墽銆掗柣鐔活潐缁绘繈妫冨☉娆愭倷缂備椒绶ょ粻鎾诲蓟閵娿儮妲堟俊顖氱仢椤忣參鎮峰鍕凡鐎殿喖澧庣划瀣箳濡も偓鍞梺闈涳紡閸涱亝鏅梻鍌欑劍濡炲灝顭囬崸妤€绀夐悗锝庡枛閼歌銇勯幒鎴濐仾闁稿孩锚闇夐柨婵嗘处濞呮洟鏌ｉ弬鎸庡櫧闁逞屽墮閻忔艾顭垮Ο灏栧亾濮橆剚鎲告俊鍙夊姇閳规垹鈧綆鍓欑粊锕傛煟閻樿崵绱版繛鍜冪到閳?authenticity 闂傚倷绀侀幖顐ょ矓閺夋嚚娲晲閸ャ劌顏搁梺缁樻⒒閸樠囨倶閾忣偆绡€濠电姴鍊搁弳鐔兼煙閻ｅ苯鈻堥柡宀嬬秮婵″爼宕卞Δ鍐ф喚闂備線鈧偛鑻晶顖滅磼閸濆嫭鍋ラ柛鈹惧亾?
- 婵犵數濮烽。浠嬪焵椤掆偓閸熷潡鍩€椤掆偓缂嶅﹪骞冨Ο璇茬窞闁归偊鍓涢惈鍕⒑闂堟盯鐛滅紒杈ㄦ礀椤繑銈ｉ崘鈺冨幐闂佸壊鍋呯换宥呂ｈぐ鎺撶厸閻庯綆浜滈弳娆愩亜閳轰降鍋㈤柡浣瑰姍瀹曟﹢鏁愰崨顒€顥氬┑鐐舵彧缁蹭粙宕查弻銉ユ瀬闁冲搫鎳忛悡鐔搞亜閺冨洤鍚圭紒娑樼箳缁辨帗娼忛妸褏鐣虹紓浣割儏閿曨亪骞冮姀銈呬紶闁靛鑵归幐鍕⒒?Git 闂備浇宕垫慨鏉懨洪敐澶嬪€块柨鏇楀亾闁伙絽鐏氱粭鐔煎焵椤掑嫬鏋佺€广儱娲ｅ▽顏堟煠濞村娅囬柟鎻掔秺閺岋綁鎮㈤崨濠勶紱闂佺粯甯梽鍕┍婵犲洤浼犻柕澶堝灩娴滈箖鏌ｉ悢鍛婄凡闁哄棙鐟х槐鎾愁吋閸滀礁鍓遍梺鐟板槻閹冲酣鈥﹂妸鈺佺闁靛鍎查濂告⒑鐠囧弶鎹ｉ柟铏崌瀵敻顢楅崟顐㈢€┑顔筋焾濞夋盯宕橀埀顒傜磽娴ｅ壊鍎撴繛澶嬫礃娣囧﹪宕堕妸銈囩畾濡炪倖鐗楅妵娑㈠磻閹剧粯鎯炴い鎰垫線濞ｎ噣姊?
"""

    def sanitize_markdown_table_cell(self, value: Any) -> str:
        text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
        text = " ".join(text.split())
        return text.replace("|", "\\|")

    def format_core_module_refs(self, document_refs: list[CoreModuleDocumentRef]) -> str:
        parts = []
        for ref in document_refs or []:
            file_path = self.sanitize_markdown_table_cell(ref.file_path)
            summary = self.sanitize_markdown_table_cell(ref.quote_or_summary)
            reason = self.sanitize_markdown_table_cell(ref.reason)
            detail = f"{file_path}: {summary}" if summary else file_path
            if reason:
                detail += f" ({reason})"
            if detail:
                parts.append(detail)
        return "; ".join(parts)

    def build_core_module_table_row(self, data: CoreModuleVariableInput) -> str:
        authenticity = str(data.authenticity or "").strip()
        if authenticity not in {"real", "suspicious", "abnormal"}:
            authenticity = "suspicious"
        refs_text = self.format_core_module_refs(data.document_refs)
        process = self.sanitize_markdown_table_cell(data.development_process)
        if refs_text:
            process = f"{process}; evidence: {refs_text}" if process else f"evidence: {refs_text}"
        return (
            "| "
            + " | ".join(
                [
                    self.sanitize_markdown_table_cell(data.module_name),
                    self.sanitize_markdown_table_cell(data.module_function),
                    self.sanitize_markdown_table_cell(data.completion_quality),
                    process,
                    self.sanitize_markdown_table_cell(authenticity),
                ]
            )
            + " |"
        )

    def fill_core_module_table_row(self, file_path: str, data: CoreModuleVariableInput) -> dict:
        target_field = str(data.target_field or "module_table").strip()
        if target_field != "module_table":
            return {
                "ok": False,
                "flag": "CORE_MODULE_FIELD_UNSUPPORTED",
                "message": "fillCoreModuleVariable currently supports only module_table.",
            }

        path = Path(file_path)
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="utf-8", errors="replace")

        row = self.build_core_module_table_row(data)
        module_name = self.sanitize_markdown_table_cell(data.module_name)
        if re.search(rf"^\|\s*{re.escape(module_name)}\s*\|", content, re.MULTILINE):
            return {
                "ok": True,
                "flag": "CORE_MODULE_ALREADY_FILLED",
                "module_name": data.module_name,
                "message": "Core module already exists; skipped duplicate row.",
            }

        placeholder_pattern = re.compile(r"^\[FIELD:module_table\b[^\r\n]*\]\r?\n?", re.MULTILINE)
        if placeholder_pattern.search(content):
            new_content = placeholder_pattern.sub(row + "\n", content, count=1)
        else:
            lines = content.splitlines(keepends=True)
            insert_at = None
            in_module_table = False
            for index, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("|") and ("濡€虫健閸氬秶袨" in stripped or "module" in stripped.lower()):
                    in_module_table = True
                    insert_at = index + 1
                    continue
                if in_module_table:
                    if stripped.startswith("|"):
                        insert_at = index + 1
                        continue
                    break
            if insert_at is None:
                return {
                    "ok": False,
                    "flag": "CORE_MODULE_TABLE_NOT_FOUND",
                    "module_name": data.module_name,
                    "message": "module_table placeholder or module table was not found.",
                }
            lines.insert(insert_at, row + "\n")
            new_content = "".join(lines)

        path.write_text(new_content, encoding="utf-8")
        return {
            "ok": True,
            "flag": "CORE_MODULE_FILLED",
            "target_field": "module_table",
            "module_name": data.module_name,
            "document_ref_count": len(data.document_refs or []),
        }

    def compact_report_text(self, value: Any) -> str:
        text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
        return " ".join(text.split())

    def unique_core_module_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique_records: dict[str, dict[str, Any]] = {}
        for record in records or []:
            if not isinstance(record, dict):
                continue
            module = record.get("module")
            if not isinstance(module, dict):
                continue
            module_name = self.compact_report_text(module.get("module_name"))
            key = module_name or json.dumps(module, ensure_ascii=False, sort_keys=True)
            unique_records[key] = record
        return list(unique_records.values())

    def format_expected_answer_points(self, question_item: Any) -> str:
        if not isinstance(question_item, dict):
            return ""
        points = question_item.get("expected_answer_points")
        if not isinstance(points, list):
            return ""
        cleaned_points = [
            self.compact_report_text(point)
            for point in points
            if self.compact_report_text(point)
        ]
        return "; ".join(cleaned_points)

    def format_question_item_line(self, question_item: Any, fallback: str = "not generated") -> str:
        if isinstance(question_item, str):
            question = self.compact_report_text(question_item)
            return question or fallback
        if not isinstance(question_item, dict):
            return fallback
        question = self.compact_report_text(question_item.get("question")) or fallback
        answer = self.compact_report_text(
            question_item.get("Answer") or question_item.get("answer")
        )
        if not answer:
            answer = self.format_expected_answer_points(question_item)
        return f"{question} Answer: {answer}" if answer else question

    def format_core_question_sets_markdown(self, question_sets: list[dict[str, Any]]) -> str:
        sections = ["## Oral Exam Questions"]
        level_titles = {
            "easy": "Easy question",
            "medium": "Medium question",
            "hard": "Hard question",
        }
        for index, item in enumerate(question_sets, start=1):
            module = item.get("module") if isinstance(item, dict) else {}
            question_set = item.get("question_set") if isinstance(item, dict) else {}
            module = module if isinstance(module, dict) else {}
            question_set = question_set if isinstance(question_set, dict) else {}
            module_name = (
                self.compact_report_text(question_set.get("module_name"))
                or self.compact_report_text(module.get("module_name"))
                or f"Core module {index}"
            )
            sections.append(f"### {index}. {module_name}")
            if not question_set.get("ok", True):
                error_message = self.compact_report_text(question_set.get("error_message"))
                sections.append(
                    f"- Generation status: {self.compact_report_text(question_set.get('flag')) or 'QUESTION_SET_FAILED'}"
                    + (f", {error_message}" if error_message else "")
                )
                continue

            implementation_question = question_set.get("implementation_question")
            sections.append(
                f"- Implementation check: {self.format_question_item_line(implementation_question)}"
            )

            knowledge_point = question_set.get("key_knowledge_point")
            if isinstance(knowledge_point, dict):
                knowledge_name = self.compact_report_text(knowledge_point.get("name"))
                knowledge_reason = self.compact_report_text(knowledge_point.get("reason"))
                if knowledge_name or knowledge_reason:
                    sections.append(
                        f"- Key knowledge point: {knowledge_name}"
                        + (f" ({knowledge_reason})" if knowledge_reason else "")
                    )

            leveled_questions = question_set.get("leveled_questions")
            leveled_questions = leveled_questions if isinstance(leveled_questions, dict) else {}
            for level, title in level_titles.items():
                questions = leveled_questions.get(level)
                questions = questions if isinstance(questions, list) else []
                for question_index, question_item in enumerate(questions, start=1):
                    sections.append(
                        f"- {title} {question_index}: {self.format_question_item_line(question_item)}"
                    )

            evidence = question_set.get("evidence")
            evidence = evidence if isinstance(evidence, list) else []
            evidence_lines = []
            for evidence_item in evidence:
                if not isinstance(evidence_item, dict):
                    continue
                file_path = self.compact_report_text(evidence_item.get("file_path"))
                line_number = evidence_item.get("line_number")
                reason = self.compact_report_text(evidence_item.get("reason"))
                if file_path:
                    line_text = f"{file_path}:{line_number}" if line_number else file_path
                    evidence_lines.append(line_text + (f" ({reason})" if reason else ""))
            if evidence_lines:
                sections.append(f"- Evidence: {'; '.join(evidence_lines)}")

        return "\n".join(sections).strip()

    def append_core_question_sets_to_document(
        self,
        file_path: str,
        content: str,
        question_sets: list[dict[str, Any]],
    ) -> str:
        section = self.format_core_question_sets_markdown(question_sets)
        marker_index = content.find("--ps--")
        if marker_index >= 0:
            updated_content = (
                content[:marker_index].rstrip()
                + "\n\n"
                + section
                + "\n\n"
                + content[marker_index:].lstrip()
            )
        else:
            updated_content = content.rstrip() + "\n\n" + section + "\n"
        Path(file_path).write_text(updated_content, encoding="utf-8")
        return updated_content

    async def generate_core_question_sets(
        self,
        state: FileReadGraphState,
        core_module_records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        records = self.unique_core_module_records(core_module_records)
        document_scope = str(state.get("folder_path") or "/root/AI-Oral-exam")

        async def generate_one(record: dict[str, Any]) -> dict[str, Any]:
            module = record.get("module") if isinstance(record, dict) else {}
            module = module if isinstance(module, dict) else {}
            module_name = self.compact_report_text(module.get("module_name"))
            document_refs = module.get("document_refs")

            question_setter = QuestionSetterAgent(
                self.model_settings,
                document_scope=document_scope,
                thinking=self.thinking,
                response_format=True,
                temperature=0,
                show_tool_io=True,
            )
            question_set = await question_setter.execute(
                module_name=module_name,
                module_content=module,
                document_refs=document_refs if isinstance(document_refs, list) else [],
            )
            return {
                "module": module,
                "tool_result": record.get("tool_result") if isinstance(record, dict) else {},
                "question_set": question_set,
            }

        return list(await asyncio.gather(*(generate_one(record) for record in records)))

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
        state["needs_core_question_tool"] = False
        state["status"] = "templates_prepared"
        return state

    async def detect_core_question_tool_node(self, state: FileReadGraphState) -> FileReadGraphState:
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

        agent = GeneralAgent(
            self.model_settings,
            thinking=self.thinking,
            response_format=True,
            temperature=0,
        )
        response = await agent.execute(
            system_prompt=(
                "Decide whether this report template needs core module oral-exam question generation. "
                "Return only a JSON object."
            ),
            user_prompt=(
                "If the template contains core task/module/function content that can be used "
                "to generate follow-up oral-exam questions, return "
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
        state["needs_core_question_tool"] = bool(data.get("needs_core_question_tool")) if isinstance(data, dict) else False
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
        state["needs_core_question_tool"] = False
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
            updated_content = updated_content.replace(placeholder, str(stats.get(key, "not found")))

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

        core_module_records = []

        @tool(
            args_schema=CoreModuleVariableInput,
            description=(
                "Report and fill one completed core module into module_table. "
                "Call this once for each confirmed core module, with evidence references."
            ),
        )
        async def fillCoreModuleVariable(
            target_field: str = "module_table",
            module_name: str = "",
            module_function: str = "",
            completion_quality: str = "",
            development_process: str = "",
            authenticity: str = "suspicious",
            document_refs: list[CoreModuleDocumentRef] | None = None,
        ) -> str:
            payload = CoreModuleVariableInput(
                target_field=target_field,
                module_name=module_name,
                module_function=module_function,
                completion_quality=completion_quality,
                development_process=development_process,
                authenticity=authenticity,
                document_refs=document_refs or [],
            )
            try:
                result = self.fill_core_module_table_row(current_file, payload)
            except OSError as exc:
                result = {
                    "ok": False,
                    "flag": "CORE_MODULE_FILL_FAILED",
                    "module_name": module_name,
                    "error_message": str(exc),
                }
            if result.get("ok"):
                core_module_records.append(
                    {
                        "module": payload.dict(),
                        "tool_result": result,
                    }
                )
            return json.dumps(result, ensure_ascii=False)

        runner_extra_tools = list(self.extra_tools)
        runner_outerprompt = ""
        if state.get("needs_core_question_tool"):
            runner_extra_tools.append(fillCoreModuleVariable)
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
            core_module_records = []
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

        if core_module_records and state.get("needs_core_question_tool"):
            try:
                question_sets = await self.generate_core_question_sets(state, core_module_records)
                if question_sets:
                    final_content = self.append_core_question_sets_to_document(
                        current_file,
                        final_content,
                        question_sets,
                    )
                    state["core_question_sets"] = question_sets
                    runner_summaries.append(
                        f"Generated oral exam questions for {len(question_sets)} core modules."
                    )
            except Exception as exc:
                state["status"] = "failed"
                state["error"] = {
                    "flag": "CORE_QUESTION_GENERATION_FAILED",
                    "error_class": exc.__class__.__name__,
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
                "review_summary": str(review_result.get("reason") or "review passed").strip(),
                "needs_core_question_tool": bool(state.get("needs_core_question_tool")),
            }
        )
        state["plan"] = []
        state["done_plan"] = done_plan
        state["iteration"] = int(state.get("iteration") or 0) + 1
        state["status"] = "chapter_done"
        return await self.save_chapter_history_node(state)

    def build_runner_state(self, state: FileReadGraphState, current_step: dict[str, Any]) -> FileReadGraphState:
        """Build the minimal state passed to FileRunnerAgent."""
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



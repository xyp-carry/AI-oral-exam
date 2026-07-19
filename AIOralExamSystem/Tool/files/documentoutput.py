import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool


PROJECT_ROOT = Path('/root/AI-Oral-exam').resolve()
MAX_PREVIEW_CHARS = 1000


class TextReplacement(BaseModel):
    old_text: str = Field(min_length=1, description='要查找的原文字。')
    new_text: str = Field(default='', description='替换后的新文字。')


class RewriteToolInput(BaseModel):
    file_path: str = Field(min_length=1, description='要修改的文件地址，必须位于 /root/AI-Oral-exam 内。')
    replacements: list[TextReplacement] = Field(
        default_factory=list,
        description='要执行的文字替换列表，每项只包含 old_text 和 new_text。',
    )


RewriteToolDescription = (
    '修改 /root/AI-Oral-exam 内的文件。'
    '传入 file_path 和 replacements；每个替换项只包含 old_text 与 new_text。'
    '一次调用可以修改多处文字，每个 old_text 的所有匹配项都会被替换。'
)


class RewriteTool(BaseTool):
    """只负责项目范围内的精确文字替换。"""

    def __init__(self, name: str):
        super().__init__(name)
        self.description = RewriteToolDescription

    def _run(
        self,
        file_path: str,
        replacements: list[TextReplacement] | None = None,
    ) -> str:
        try:
            path = self._resolve_file_path(file_path)
            if path is None:
                return self._json_error('FILE_PATH_DENIED', '文件地址必须位于 /root/AI-Oral-exam 内。')
            if not path.exists() or not path.is_file():
                return self._json_error('TARGET_NOT_FILE', f'目标文件不存在或不是文件：{path}')

            raw_replacements = list(replacements or [])
            if not raw_replacements:
                return self._json_error('REPLACEMENTS_REQUIRED', 'replacements 至少需要包含一项替换。')

            try:
                original = path.read_text(encoding='utf-8')
            except (OSError, UnicodeError) as exc:
                return self._json_error('READ_FAILED', f'读取文件失败：{exc}')

            operations: list[tuple[int, int, str, int]] = []
            details: list[dict] = []
            for index, raw in enumerate(raw_replacements, start=1):
                try:
                    spec = raw if isinstance(raw, TextReplacement) else TextReplacement(**raw)
                except Exception as exc:
                    return self._json_error('INVALID_REPLACEMENT', f'第 {index} 项替换格式无效：{exc}')

                matches: list[tuple[int, int]] = []
                cursor = 0
                while True:
                    found = original.find(spec.old_text, cursor)
                    if found < 0:
                        break
                    end = found + len(spec.old_text)
                    matches.append((found, end))
                    cursor = end

                if not matches:
                    return self._json_error(
                        'TEXT_NOT_FOUND',
                        f'第 {index} 项 old_text 在文件中不存在：{spec.old_text}',
                    )

                details.append(
                    {
                        'index': index,
                        'old_text': spec.old_text,
                        'new_text': spec.new_text,
                        'replaced_count': len(matches),
                    }
                )
                operations.extend((start, end, spec.new_text, index) for start, end in matches)

            operations.sort(key=lambda item: (item[0], item[1]))
            for previous, current in zip(operations, operations[1:]):
                if current[0] < previous[1]:
                    return self._json_error('OVERLAPPING_REPLACEMENTS', '替换范围重叠，请调整 old_text。')

            pieces: list[str] = []
            cursor = 0
            for start, end, new_text, _index in operations:
                pieces.append(original[cursor:start])
                pieces.append(new_text)
                cursor = end
            pieces.append(original[cursor:])
            new_content = ''.join(pieces)

            self._write_text(path, new_content)
            return json.dumps(
                {
                    'ok': True,
                    'flag': 'TEXT_REPLACEMENTS_APPLIED',
                    'file_path': str(path),
                    'relative_path': self._relative_path(path),
                    'replacement_count': len(details),
                    'replaced_count': len(operations),
                    'details': details,
                    'preview': new_content[:MAX_PREVIEW_CHARS],
                    'preview_truncated': len(new_content) > MAX_PREVIEW_CHARS,
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            return self._json_error('REWRITE_FAILED', f'文件修改失败：{type(exc).__name__}: {exc}')

    def _resolve_file_path(self, file_path: str) -> Path | None:
        raw = str(file_path or '').strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path = path.resolve()
        try:
            path.relative_to(PROJECT_ROOT)
        except ValueError:
            return None
        return path

    def _relative_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(PROJECT_ROOT).as_posix()
        except (OSError, ValueError):
            return str(path)

    def _write_text(self, path: Path, content: str) -> None:
        temporary_path = None
        try:
            fd, temporary_path = tempfile.mkstemp(
                prefix=f'.{path.name}.',
                suffix='.tmp',
                dir=str(path.parent),
            )
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def _json_error(self, error_type: str, message: str) -> str:
        return json.dumps(
            {
                'ok': False,
                'flag': error_type,
                'message': message,
                'file_path': '',
            },
            ensure_ascii=False,
        )


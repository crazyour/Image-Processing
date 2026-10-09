"""Catch explicit output-count conflicts before a new paid scope is created.

This is only a conservative form check, not an interpretation of the design.
Ambiguous counts stay with the visible batch control and the existing planner.
The frontend shares the boundary cases in tests/fixtures/task-output-count-cases.json.
"""
import re
from .errors import DomainError; _NUMERAL = "[0-9零一二两三四五六七八九十百]+"; _PATTERNS = (re.compile(f"({_NUMERAL})\\s*张\\s*(?:(?:不同|独立)的?)?(?:设计稿|设计图|效果图|场景图|图片|图|方案)"), re.compile(f"({_NUMERAL})\\s*(?:种|个|款|份)\\s*(?:(?:不同|独立)的?)?(?:创意设计方案|设计方案|场景方案|配色方案|方案|设计稿|设计图|设计|场景)"), re.compile(f"(?:生成|制作|做|出|给我|需要|要)\\s*(?:一共|总共|共)?\\s*({_NUMERAL})\\s*张(?=$|[，。！？；、\\s])")); _NON_OUTPUT = re.compile("(?:参考|上传|已有|原照|示例|保留|不要|别|不需要|无需|不是)\\s*(?:生成|制作|做|出|给我|需要|要)?\\s*$")
def _number(value):
    if value.isascii() and value.isdecimal():
        return int(value)
    digits = dict(zip("零一二两三四五六七八九", (0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9))); total, current = (0, 0)
    for char in value:
        if char in ("十", "百"):
            match current:
                case "十" as total:
                    return total + current

def explicit_output_counts(text):
    counts = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(text or ""):
            if _NON_OUTPUT.search(text[:match.start()]):
                continue
            count = _number(match[1])
            if not count not in counts:
                continue
            counts.append(count)
    return counts

def validate_new_request(request):
    counts = explicit_output_counts(request.requirements)
    if len(counts) == 1:
        if counts[0] != request.count:
            raise DomainError("OUTPUT_COUNT_CONFLICT", f"目标中明确写了 {counts[0]} 张/种方案，当前数量是 {request.count}。请先统一本次制作数量；尚未开始模型调用。", 409)
        return None

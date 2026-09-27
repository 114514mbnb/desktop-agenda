"""从教务导出的课表 PDF 重建课程列表（容错版）。

PDF 由 iText 生成（Type0 /Encoding UniGB-UCS2-H，UTF-16BE，页面 Rotate 90，4 页横向排布）。
它的 CID→Unicode 映射不完整：`(`、`)`、`单`、`双` 等字符会解成错误的字（`\⠀`、`\⡓`…），
所以解析不能依赖括号，只能依赖**稳定的字段关键词**：

  课程名★
  <A>-<B>周 /校区:…/场地:…      ← 节次与周次挤在同一行、以 "/校区" 为界
  ...场地:A-330...              ← 场地（可能续行）

星期列：每页的列顺序与表头一致，按课程名 x 坐标从左到右映射为 周一…周日。
"""

from __future__ import annotations

import json
import re
import sys
import zlib
from pathlib import Path

TOKEN = re.compile(
    rb"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+Tm"
    rb"|\(((?:\\.|[^\\()])*)\)\s*Tj",
    re.S,
)
WEEKDAY_NAMES = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
#: 第1页实测：课程列 x = 星期列表头 x + 104 + 75（表头 133/237/341 → 课程列 312/416/519）
COLUMN_OFFSET = 104 + 75
COLUMN_PITCH = 104          # 相邻两天的列距
NOISE_WORDS = ("教学班", "考核方式", "周学时", "总学时", "学分", "重修", "具体", "组成",
               "课位置", "课程学时", "实践课程", "其他课程", "注:", "考试", "讲课", "实验")


def page_rows(stream: bytes) -> list[tuple[float, float, str]]:
    x = y = 0.0
    rows: list[tuple[float, float, str]] = []
    for token in TOKEN.finditer(stream):
        if token.group(1) is not None:
            x, y = float(token.group(5)), float(token.group(6))
        elif token.group(7) is not None:
            body = token.group(7).replace(b"\\(", b"(").replace(b"\\)", b")")
            text = body.decode("utf-16-be", errors="replace").replace("\x00", " ").strip()
            if text:
                rows.append((x, y, text))
    return rows


def load_streams(path: Path) -> list[bytes]:
    raw = path.read_bytes()
    streams = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        try:
            streams.append(zlib.decompress(match.group(1)))
        except zlib.error:
            pass
    return streams


def is_course_name(text: str) -> bool:
    stripped = text.strip()
    return len(stripped) <= 40 and bool(re.search(r"[★☆*]\s*$", stripped))


def is_noise(text: str) -> bool:
    return any(word in text for word in NOISE_WORDS)


def extract_numbers(text: str, limit: int = 20) -> list[int]:
    return [int(n) for n in re.findall(r"\d{1,2}", text) if 1 <= int(n) <= limit]


def parse_cell(chunks: list[str]) -> dict | None:
    """从一个单元格的文本行里抽出 节次 / 周次 / 场地。"""
    joined = " / ".join(chunks)
    # ① 节次：单元格开头 "<A>-<B>周" 之前的那段数字（括号可能解错，所以不依赖括号）
    head_match = re.match(r"[^\d]{0,6}(\d{1,2})\s*-\s*(\d{1,2})\s*周", joined)
    if head_match:
        start, end = int(head_match.group(1)), int(head_match.group(2))
    else:
        lead = re.match(r"[^\d]{0,6}(\d{1,2})\s*-\s*(\d{1,2})", joined)
        if lead and "周" in joined[: len(lead.group(0)) + 12]:
            start, end = int(lead.group(1)), int(lead.group(2))
        else:
            first = re.match(r"[^\d]{0,6}(\d{1,2})", joined)
            if not first:
                return None
            start = end = int(first.group(1))
    if not (1 <= start <= 20 and 1 <= end <= 20):
        return None

    # ② 周次："…周" 前的那组数字
    weeks = ""
    week_match = re.search(r"(\d{1,2}\s*-\s*\d{1,2}|\d{1,2})\s*周", joined[head_match.end():] if head_match else joined)
    if week_match:
        weeks = re.sub(r"\s+", "", week_match.group(0))
        if "单" in joined[-40:] and "单" not in weeks:
            weeks = weeks.replace("周", "周(单)")
        elif "双" in joined[-40:] and "双" not in weeks:
            weeks = weeks.replace("周", "周(双)")

    # ③ 场地 / 校区（PDF 里换行会把词切断，所以只接受看起来完整的地点）
    campus = re.search(r"校区[:：]\s*([^\s/]{2,})", joined)
    venue = re.search(r"场地[:：]\s*([^\s/]{1,})", joined)
    parts = []
    for value in (campus.group(1) if campus else "", venue.group(1) if venue else ""):
        text = value.strip()
        if not text or "煨" in text or len(text) < 2:
            continue
        # 去掉被换行截断时粘上的后续字段名
        text = re.sub(r"^(?:区|屜|煨)+", "", text)
        if len(text) >= 2:
            parts.append(text)
    return {
        "period": f"{start}-{end}",
        "weeks": weeks or None,
        "location": " ".join(parts) or None,
    }


def weekday_headers(rows: list[tuple[float, float, str]]) -> dict[int, int]:
    """表头 x → 星期。返回的键是**课程列**坐标（表头 + 偏移）。"""
    mapping: dict[int, int] = {}
    for x, _y, text in rows:
        stripped = text.strip()
        if len(stripped) > 6:
            continue
        for index, name in enumerate(WEEKDAY_NAMES):
            if name in stripped:
                mapping[round(x) + COLUMN_OFFSET] = index
                break
    return mapping


def infer_weekday(course_x: int, headers: dict[int, int]) -> int | None:
    """课程列 x → 星期：找最近的表头推算列（容差取半个列距）。"""
    if not headers:
        return None
    nearest = min(headers, key=lambda key: abs(key - course_x))
    if abs(nearest - course_x) <= COLUMN_PITCH // 2 + 6:
        return headers[nearest]
    # 表头只覆盖部分天（如第1页只有周一~周三）：按列距外推
    for header_x, weekday in headers.items():
        delta = course_x - header_x
        steps = round(delta / COLUMN_PITCH)
        if abs(delta - steps * COLUMN_PITCH) <= 6:
            guess = weekday + steps
            if 0 <= guess <= 6:
                return guess
    return None


def extract(path: Path) -> tuple[list[dict], list[str], dict[int, int]]:
    """返回（课程, 警告, 每页第一列的星期基准）。

    星期列的不确定性写在明面上：
      * 第1页有完整表头，它的列与表头之间存在固定偏移，可以直接标定（可信）；
      * 其余页没有表头，只能按"列序递增"推断（**需用户核对**）。
    与其猜一个可能错的绝对星期号，不如把推断依据暴露出来。
    """
    courses: list[dict] = []
    warnings: list[str] = []
    streams = load_streams(path)
    page_data = [page_rows(stream) for stream in streams]

    # 第1页标定：表头 x 与课程列 x 的关系
    anchors: dict[int, int] = {}
    for rows in page_data:
        for header_x, weekday in weekday_headers_raw(rows).items():
            anchors[header_x] = weekday

    page_base: dict[int, int] = {}
    page_columns: dict[int, list[int]] = {}
    for page_index, rows in enumerate(page_data, start=1):
        columns = sorted({round(x) for x, _y, text in rows if is_course_name(text)})
        if not columns:
            continue
        page_columns[page_index] = columns
        base = infer_page_base(columns, anchors)
        page_base[page_index] = base

    for page_index, rows in enumerate(page_data, start=1):
        columns = page_columns.get(page_index)
        if not columns:
            continue
        base = page_base[page_index]
        for x, y, text in sorted(rows, key=lambda item: (round(item[0]), -item[1])):
            if not is_course_name(text):
                continue
            name = re.sub(r"[★☆*]+\s*$", "", text).strip()
            if len(name) < 2 or is_noise(name):
                continue
            cell_lines = [
                other for ox, oy, other in sorted(rows, key=lambda item: -item[1])
                if abs(ox - x) < 8 and oy < y + 1 and not is_course_name(other)
                and not is_noise(other) and (y - oy) < 130
            ]
            info = parse_cell(cell_lines)
            if info is None:
                continue
            weekday = base + columns.index(round(x))
            # 该列是否被本页表头直接标定过（表头 x = 课程列 x − 179）
            confirmed = any(
                abs(round(x) - (header_x + COLUMN_OFFSET)) <= 6 and weekday == header_weekday
                for header_x, header_weekday in weekday_headers_raw(rows).items()
            )
            course = {"name": name, "weekday": weekday, **info,
                      "_page": page_index, "_column": round(x),
                      "_weekday_confirmed": confirmed}
            courses.append(course)

    seen = set()
    unique = []
    for course in courses:
        key = (course["name"], course["weekday"], course["period"], course.get("weeks"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(course)

    if not anchors:
        warnings.append("整份 PDF 都没有星期表头，星期列完全按列序推断，务必核对")
    return unique, warnings, page_base


def weekday_headers_raw(rows: list[tuple[float, float, str]]) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for x, _y, text in rows:
        stripped = text.strip()
        if len(stripped) > 6:
            continue
        for index, name in enumerate(WEEKDAY_NAMES):
            if name in stripped:
                mapping[round(x)] = index
                break
    return mapping


def infer_page_base(columns: list[int], anchors: dict[int, int]) -> int:
    """推这一页第一列是星期几。

    用第1页标定出的"表头→课程列"偏移，去套本页的列；套不上就退回列序（并会在警告里说明）。
    """
    if anchors and columns:
        # 第1页实测：课程列 = 表头 + 179（133→312, 237→416, 341→519）
        offset = min(
            (course_x - header_x for header_x in anchors for course_x in (312, 416, 519)
             if abs((course_x - header_x) - (312 - 133)) < 20),
            default=179,
        )
        for column in columns:
            guess = None
            for header_x, weekday in anchors.items():
                if abs(column - (header_x + offset)) <= 6:
                    guess = weekday
                    break
            if guess is not None:
                return max(0, guess)
    return 0


def legacy_extract(path: Path) -> list[dict]:
    courses: list[dict] = []
    for stream in load_streams(path):
        rows = page_rows(stream)
        names = sorted({round(x) for x, _y, text in rows if is_course_name(text)})
        if not names:
            continue
        column_map = {x: index for index, x in enumerate(names)}
        for x, _y, text in sorted(rows, key=lambda item: (round(item[0]), -item[1])):
            if not is_course_name(text):
                continue
            name = re.sub(r"[★☆*]+\s*$", "", text).strip()
            if len(name) < 2 or is_noise(name):
                continue
            cell_lines = [
                other for ox, oy, other in sorted(rows, key=lambda item: -item[1])
                if abs(ox - x) < 8 and oy < _y + 1 and not is_course_name(other)
                and not is_noise(other) and (_y - oy) < 130
            ]
            info = parse_cell(cell_lines)
            if info is None:
                continue
            courses.append({"name": name, "weekday": column_map.get(round(x), 0), **info})
    return courses


def main() -> int:
    pdf = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("timetable-from-pdf.json")
    courses, warnings, page_base = extract(pdf)
    payload = {
        "note": "由 tools/parse_schedule_pdf.py 从教务导出 PDF 解析；周次/节次/场地已核对，星期列见 _weekday_confirmed",
        "pageBaseWeekday": {str(k): v for k, v in page_base.items()},
        "courses": [
            {k: v for k, v in course.items() if not k.startswith("_")}
            for course in courses
        ],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    days = "一二三四五六日"
    print(f"解析出 {len(courses)} 条课程记录 → {out}\n")
    for course in courses:
        mark = "✓" if course.get("_weekday_confirmed") else "?"
        print(f"  周{days[max(0, min(6, course['weekday']))]}{mark} 第{course['period']}节  "
              f"{course.get('weeks') or '每周'}  {course['name']}  @ {course.get('location') or '-'}"
              f"   (p{course['_page']} x={course['_column']})")
    print(f"\n每页第一列的星期基准: {page_base}")
    for warning in warnings:
        print("  ! ", warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

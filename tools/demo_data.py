"""截图/演示用的**虚构**数据。

为什么要单独一个模块：截图工具原来是从 `data/timetable.json` 里复制本人的真实课表
（课程、教师姓名、教室号）去渲染面板，于是 README 头图和 `docs/festival-shots/`
里的每一张都带着真实课表 —— 一旦开源就等于把"谁、哪个班、课表长什么样"一起公布。
现在所有截图工具都只允许用这里的数据，**不再读 `data/` 里的任何东西**。

这里的课名、教师、教室、群名全部是编的；假期用的是 2026 年节假日安排（公开信息）。
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
from pathlib import Path

#: 虚构课表：周一至周五各排几门，看起来像真的，但与任何真实院系无关
DEMO_TIMETABLE: dict = {
    "termStart": "2026-09-07",
    "periods": [["08:00", "08:45"], ["08:55", "09:40"], ["10:00", "10:45"],
                ["10:55", "11:40"], ["14:00", "14:45"], ["14:55", "15:40"],
                ["16:00", "16:45"], ["16:55", "17:40"], ["19:00", "19:45"],
                ["19:55", "20:40"], ["20:50", "21:35"], ["21:45", "22:30"]],
    "courses": [
        {"name": "高等数学", "weekday": "周一", "period": "1-2", "weeks": "1-18",
         "teacher": "陈立", "location": "中心校区 A-101"},
        {"name": "大学英语", "weekday": "周一", "period": "3-4", "weeks": "1-16",
         "teacher": "苏晴", "location": "中心校区 A-203"},
        {"name": "程序设计基础", "weekday": "周一", "period": "7-8", "weeks": "1-16",
         "teacher": "周宏", "location": "中心校区 D-107"},
        {"name": "线性代数", "weekday": "周二", "period": "1-2", "weeks": "1-18",
         "teacher": "李文明", "location": "中心校区 A-105"},
        {"name": "程序设计基础", "weekday": "周二", "period": "5-6", "weeks": "1-16",
         "teacher": "周宏", "location": "中心校区 D-107"},
        {"name": "大学物理", "weekday": "周二", "period": "7-8", "weeks": "1-17",
         "teacher": "何芳", "location": "中心校区 B-203"},
        {"name": "高等数学", "weekday": "周三", "period": "1-2", "weeks": "1-18",
         "teacher": "陈立", "location": "中心校区 A-101"},
        {"name": "中国近现代史纲要", "weekday": "周三", "period": "7-8", "weeks": "1-16",
         "teacher": "吴晓敏", "location": "中心校区 E-303"},
        {"name": "线性代数", "weekday": "周四", "period": "1-2", "weeks": "1-18",
         "teacher": "李文明", "location": "中心校区 A-105"},
        {"name": "大学英语", "weekday": "周四", "period": "5-6", "weeks": "1-16",
         "teacher": "苏晴", "location": "中心校区 A-203"},
        {"name": "大学物理实验", "weekday": "周四", "period": "7-8", "weeks": "3-17",
         "teacher": "郑新明", "location": "中心校区 B-101"},
        {"name": "体育（羽毛球）", "weekday": "周五", "period": "3-4", "weeks": "1-16",
         "teacher": "徐鹏", "location": "中心校区 体育馆"},
        {"name": "形势与政策", "weekday": "周五", "period": "7-8", "weeks": "1-8",
         "teacher": "曹文超", "location": "中心校区 E-402"},
    ],
}

#: 虚构群通知（相对"今天"生成，所以任何时候截图都有内容）。
#: 第一条故意放在**当天**：面板头图要有"今天就有事"的样子，不然今日通知永远是 0 条。
DEMO_NOTICE_TEMPLATES = (
    {"offset": 0, "start": "12:00", "end": "13:00",
     "title": "班会：本学期评奖评优材料提交",
     "people": "各班班长", "notes": "把材料交到辅导员办公室，电子版发群文件",
     "group": "2026级本科生年级群"},
    {"offset": 2, "start": "19:00", "end": "20:30",
     "title": "学术讲座：多模态大模型前沿",
     "people": "感兴趣的同学", "notes": "报告厅座位有限，提前十分钟入场",
     "group": "2026级本科生年级群"},
    {"offset": 4, "start": "09:00", "end": "11:00",
     "title": "程序设计基础 上机实验",
     "people": "全体同学", "notes": "机房按学号就座，实验报告当堂提交",
     "group": "程序设计基础课程群"},
)

#: 2026 年节假日安排（公开信息，不是个人数据）
DEMO_CALENDAR: dict = {
    "holidays": [
        {"name": "元旦", "start": "2026-01-01", "end": "2026-01-03"},
        {"name": "春节", "start": "2026-02-16", "end": "2026-02-22"},
        {"name": "清明节", "start": "2026-04-05", "end": "2026-04-06"},
        {"name": "劳动节", "start": "2026-05-01", "end": "2026-05-05"},
        {"name": "端午节", "start": "2026-06-19", "end": "2026-06-21"},
        {"name": "中秋节", "start": "2026-09-25", "end": "2026-09-27"},
        {"name": "国庆节", "start": "2026-10-01", "end": "2026-10-08"},
    ],
    "makeups": [
        {"date": "2026-10-10", "source": "2026-10-07"},
    ],
}


def demo_events(today: dt.date | None = None) -> dict:
    """按"今天"生成几条虚构通知（这样截图里永远有东西可看）。"""
    today = today or dt.date.today()
    events = []
    for index, template in enumerate(DEMO_NOTICE_TEMPLATES, start=1):
        day = today + dt.timedelta(days=int(template["offset"]))
        if day.weekday() >= 5:
            day += dt.timedelta(days=2)          # 挪到工作日，别让通知落在周末
        events.append({
            "id": f"demo-{index}",
            "title": template["title"],
            "date": day.strftime("%Y-%m-%d"),
            "start": template["start"],
            "end": template["end"],
            "people": [template["people"]],
            "notes": template["notes"],
            "group": template["group"],
            "tentative": False,
        })
    return {"schemaVersion": 1, "events": events}


def seed_dir(today: dt.date | None = None, *, holidays: bool = True) -> Path:
    """建一个只装演示数据的临时目录，返回它的路径。

    **故意不接受"源目录"参数**：不给调用方任何机会把真实数据拷进来。

    `holidays=False` 时写一份空日历 —— README 头图要的是一张"平常工作日"的面板，
    带着节日横幅和收心倒计时反而看不清主体（节日效果另有 `docs/festival-shots/`）。
    """
    work = Path(tempfile.mkdtemp(prefix="agenda-demo-"))
    (work / "timetable.json").write_text(
        json.dumps(DEMO_TIMETABLE, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "events.json").write_text(
        json.dumps(demo_events(today), ensure_ascii=False, indent=2), encoding="utf-8")
    calendar = DEMO_CALENDAR if holidays else {"holidays": [], "makeups": []}
    (work / "calendar.json").write_text(
        json.dumps(calendar, ensure_ascii=False, indent=2), encoding="utf-8")
    return work


def cleanup(work: Path) -> None:
    shutil.rmtree(work, ignore_errors=True)

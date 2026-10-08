"""客户端设置：面板开关、关闭行为、窗口位置。

配置文件是纯 JSON，放 data/client.json —— 用户能直接看懂、手改、删掉重来。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_FILENAME = "client.json"


@dataclass
class ClientConfig:
    #: 启动时是否显示桌面面板
    panel_visible: bool = True
    #: 关闭**控制台窗口**时的行为：tray=最小化至托盘（默认，不动面板）/ ask=第一次提醒一句。
    #: 说明：点右上角 X **永远不会**关掉桌面面板 —— 退出应用只有两个入口：
    #: 托盘右键「退出应用」、面板底部右键「退出应用」。
    close_action: str = "tray"
    #: 是否记住过"关闭时不再询问"（旧字段，保留是为了能读老配置）
    remember_close_action: bool = False
    #: 第一次最小化到托盘时提示过一次"去哪儿退出"了吗
    tray_notice_shown: bool = False
    #: 面板宽度（像素）
    panel_width: int = 360
    #: 面板位置（None 表示用默认靠右）
    panel_x: int | None = None
    panel_y: int | None = None
    #: 控制台窗口位置
    console_x: int | None = None
    console_y: int | None = None
    #: 上次用的教务网址与适配器（方便下次直接连）
    eas_url: str = ""
    eas_adapter: str = "zfsoft"
    eas_login_path: str = ""
    #: 上次用的学校名（仅显示用）
    eas_school: str = ""
    #: 面板自动整合间隔（分钟）
    pipeline_minutes: float = 15.0
    #: 面板图层：desktop=像桌面小部件待在底层（默认，不挡游戏）；topmost=总在最前
    window_mode: str = "desktop"
    #: 桌面模式下是否让面板不吃鼠标事件。
    #: 默认 **False** —— 开着就点不动面板，用户第一反应就是"这不能编辑啊"（真踩过）。
    #: 游戏全屏时面板本来就会自动隐身，所以默认不需要穿透。
    click_through: bool = False
    #: 只在你看着桌面时显示；有全屏应用（游戏/视频）时自动隐身
    desktop_only: bool = True
    #: 桌面宠物模式：显示桌面（Win+D / 点"显示桌面"）时面板会**浮到普通窗口之上**，
    #: 而不是沉到底下被压住。你一切回别的窗口它就自动让位，仍然不挡游戏。
    pet_mode: bool = True
    #: 课表里"一节课"的节数（面板按它排版；多了也没关系，多出来的节会照样显示）
    period_count: int = 12
    #: 导入课表前是否总是弹确认窗（识别结果先核对再入库）
    confirm_import: bool = True
    #: 提醒：即将开始的日程是否弹窗
    popup_reminders: bool = True
    #: 提前多少分钟提醒
    remind_before_minutes: int = 10
    #: 已提醒过的事项 id（避免重复弹）
    reminded: list[str] = field(default_factory=list)
    #: 剪贴板全局热键：按一下就把剪贴板里的群通知解析入库（登记在日程表面板进程上）
    hotkey_enabled: bool = True
    #: 热键写法（规范化形态，例如 Ctrl+Alt+Q）。改完面板会自己重挂，不必重启。
    hotkey: str = "Ctrl+Alt+Q"
    #: 面板主题：classic=经典深色 / auto=随时刻（清晨·白天·黄昏·深夜）/ photo=我的照片
    theme_mode: str = "classic"
    #: 照片模式用的背景图文件名（存在 data/theme/ 下；空串表示还没选照片）
    theme_photo: str = ""
    #: 按热键时是否先尝试抓取前台程序里选中的文字（借用剪贴板：合成一次 Ctrl+C）+
    #: 关掉它 = 只读剪贴板里已经有的内容（终端用户或不喜欢模拟按键的人可以关）
    hotkey_selection: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, data_dir: Path) -> "ClientConfig":
        path = Path(data_dir) / CONFIG_FILENAME
        if not path.exists():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        config = cls(**{k: v for k, v in payload.items() if k in known})
        # 老配置里的 "quit"（点 X 就整个退出）已经不再支持：点 X 只最小化到托盘。
        # 读到旧值就当"最小化"，免得升级上来的用户点一下 X 面板就没了。
        if config.close_action not in ("tray", "ask"):
            config.close_action = "tray"
        return config

    def save(self, data_dir: Path) -> None:
        path = Path(data_dir) / CONFIG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(self.to_json(), encoding="utf-8")
        os.replace(temp, path)

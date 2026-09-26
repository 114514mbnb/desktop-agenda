"""第三方组件目录（vendored dependencies）。

这里放的代码**不是本项目原创**，为了"下载即用、零安装"才随包一起发。
每个组件都保留原始许可证，并在这里登记来源、版本与校验值。

* `lunardate.py` —— 农历/节气换算，来自 **borax** 项目的 `borax/calendars/lunardate.py`
  - 上游：https://github.com/kinegratii/borax
  - 版本：borax 4.1.3（PyPI 上的 wheel 原样复制，**未做任何修改**）
  - 许可证：MIT（见 `LICENSE-borax.txt`，Copyright (c) 2015-2025 kinegratii）
  - SHA-256：`640e022f069117e3f5ab1dd3b3ebaefce7be1b4cedc099fe5e7c9a24a9f3d84d`
  - 范围：公元 1900-01-31 ~ 2101-01-28（农历 1900–2100 年）
  - 为什么随包：节日彩蛋要按农历算春节/元宵/端午/中秋，还要按节气算清明。
    自己写一套 200 年的压缩表既费力又容易错，而这个库是纯 Python 单文件、
    无第三方依赖、MIT 许可，正好能在"零安装"的前提下长期覆盖。

换版本的做法：把上游文件整份覆盖过来，更新这里的版本号与 SHA-256，然后跑
`runtime\\python.exe -m unittest tests.test_lunar`（用写死的历史日期做回归）。
"""

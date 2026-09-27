# 学校入口实测（学校通用性证据）

**结论先说**：客户端里"用浏览器打开教务系统自动读课表"那条路已按需求整体删除。
删除的理由不只是"识别不准"，更是**这条路对学校不通用**——下面是对 4 所学校的实测。

## 实测方式

`tools/probe_schools.py`：只做 HTTP GET + 跟随重定向，**不登录、不提交任何账号密码**，
只用特征词判断落到哪种页面（统一身份认证 / WebVPN 网关 / 正方教务 / SSL VPN 客户端）。

```powershell
runtime\python.exe tools\probe_schools.py                 # 测内置候选清单
runtime\python.exe tools\probe_schools.py <网址1> <网址2>  # 测指定网址
```

## 结果（2026-09-23 实测）

| 学校 | 校外访问方式 | 实测入口 | 结果 | 对"网页读课表"意味着什么 |
| --- | --- | --- | --- | --- |
| **青岛科技大学** | WebVPN 反代（wengine）+ CAS | `https://wvpn.qust.edu.cn` | 可达，登录后能进正方课表页 | 能走通，但依赖页面结构——实测新正方页面字段混在一起，识别质量差 |
| **青岛大学** | WebVPN（**免客户端**）+ 统一身份认证 | `https://webvpn.qdu.edu.cn` | HTTP 200 → 跳到 `/https/7772…/authserver/login`，标题「统一身份认证」，命中 wengine | 结构上和青科大同一套路，理论上可走，但仍要处理 iframe/改版 |
| **太原理工大学** | CAS 统一身份认证 + SSL VPN 客户端 | `https://cas.tyut.edu.cn/tpass/login` | HTTP 200，标题「统一身份认证」 | 认证页可达；但**访问校内教务需要 SSL VPN 客户端**，浏览器代理不到 |
| **山东科技大学** | WebVPN + 客户端式 SSL VPN | `https://jwgl.sdust.edu.cn` | HTTP 502（网关在、后端不到） | 教务系统本身要经过网关；官方校外访问说明指向 `svpn.sdust.edu.cn`（客户端） |
| **山东建筑大学** | **纯客户端 SSL VPN** | `wvpn/vpn/ssl-vpn.sdjzu.edu.cn`、`jwgl.sdjzu.edu.cn` | 全部域名解析失败 | 官方说明是"下载附件+客户端连接"（错误码 691/721），网页代理这条根本不存在 |

补充：`https://jwc.tyut.edu.cn`、`https://jwc.qdu.edu.cn` 这类教务处**门户**都能打开（HTTP 200），
但它们只是新闻站，不含个人课表；课表在校园内网的教务系统里，必须先进网关/客户端。

## 所以

1. **学校之间差异极大**：有的是免客户端 WebVPN（青大），有的是纯客户端 SSL VPN（山建），
   有的两者都有（山科、太原理工）。"一个浏览器脚本读所有学校"在架构上就不成立。
2. **即使能打开页面，识别也不稳**：正方新版课表页把课程名、教学班、班级名单、考核方式
   塞进同一个单元格，实测解析出来是
   `解析几何…中心校区 B-103 -D0001-01 应数1班;应数2班… 李文明、考试` 这种一行糊在一起的结果。
3. 因此客户端改为 **`从文件识别课表`**：`.ics` / `.pdf` / `.html` / `.json` / `.csv` / `.txt`
   都是**静态**文件——格式固定、可反复解析、坏了能加规则，不受登录方式和页面改版影响。
   这条路对**任何学校**都成立，因为"教务系统里能导出/打印课表"是普遍能力。

## 来源

- 山东建筑大学：[关于如何在校外加密访问教务系统的快捷方法](https://www.sdjzu.edu.cn/wlfw/info/1073/1085.htm)（"请下载附件和说明进行使用"，故障码 691/721 → 客户端式 VPN）
- 青岛大学：[校外访问-青岛大学图书馆](https://lib.qdu.edu.cn/dzfw/xwfw.htm)（"直接在浏览器中输入 https://webvpn.qdu.edu.cn 跳转至统一身份认证…无需安装客户端"）
- 山东科技大学：[校外访问-山东科技大学泰安校区图书馆](http://tatsg.sdust.edu.cn/index.php?a=lists&c=index&catid=100&m=content)（登录地址 `svpn.sdust.edu.cn`，学生账号为学号）
- 太原理工大学：[统一身份认证](https://cas.tyut.edu.cn/tpass/login)、[SSL VPN 远程接入校园网服务开通及使用步骤](https://iadmin.tyut.edu.cn/info/1155/16883.htm)

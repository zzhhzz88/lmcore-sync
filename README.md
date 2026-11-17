# lmcore-sync — Learning Mall Core 课件同步工具

把 Learning Mall Core（Moodle）的课件、作业要求、实验材料、公告、自己的提交和反馈同步到本地课程文件夹，生成资料清单、AI 阅读目录、作业截止时间汇总和日历文件。

面向 Windows 10/11、Python 3.10+，优先使用 Edge。使用你本人在学校官网完成的 SSO 登录；不需要 Moodle API Token。其他 Moodle 站点可配置，但页面结构兼容性需实际验证。

## 快速开始

1. 下载并解压分享包，或克隆仓库。文件夹不要放在临时目录。
2. 双击 **install.cmd**。安装依赖后，在弹出的学校登录页完成登录。
3. 选择课程和保存位置，默认放桌面。可以开启“自动发现本学期课程”。
4. 以后双击 **同步课件.cmd**。首次同步可能较慢，之后按记录跳过已有资源。

已经在本机使用旧版：把旧目录的 `config.json` 和 `user_data/cookies.json` 复制到新版相同位置即可尝试复用登录；过期时重新登录。课程目录内的 `.lmcore/` 保留原地。不要把这些私人文件发给别人。

本仓库是 Python 源码工具，分享 ZIP 不是独立 EXE。需要 Python、网络和有效学校账号；安装器尝试用 winget 安装缺少的 Python。文件转换优先使用本地 Filee，否则安装 markitdown；转换结果应对照原文检查公式和图片。

## 同步后得到什么

```text
桌面/
  作业DDL.md                 # 待办、截止日期、提交状态、最近7天新增或更新
  作业DDL.ics                # 可手动导入 Outlook / Apple / Google Calendar
  <课程代码>/
    00_从这里开始.md          # 仅在没有既有指南时生成
    00_资料清单.md            # 逐项来源、状态和文件路径
    01_课程信息/
    02_课件/
    03_实验/
    04_作业/<作业名称>/
    05_考试复习/
    06_AI可读版/00_AI目录.md
    07_参考资料/
    .lmcore/state.json       # 增量同步记录
```

默认中文目录；EEE 课程默认英文目录，照顾 Quartus 等路径限制。也可逐课选 `"layout": "en"` 或 `"zh"`，或提供字段覆盖对象。已有目录按大小写无关的课程代码复用，既有指南不覆盖。自动模式只增加 Moodle 标为本学期（inprogress）、符合课程代码规则的课程；不会清理过往课程。

- 用 SHA-256 校验去重，同名同大小不等于相同内容。
- 老师上传新版本时，工具管理且未被你改动的文件可原位更新，旧内容保留在同目录 `_旧版本/`，同一天多个版本也保留。
- 你改过的文件，以及同步前就存在并被去重复用的个人文件，更新时另存副本。
- 增量记录无法发现所有“URL没变但内容变了”的情况，怀疑漏更新时使用 `sync --refresh`。
- 网络短暂失败、HTTP 408/429/5xx 会有限重试，默认请求间隔0.3秒。失败条目显示在清单中，同步以非零状态退出。

## 常用命令

在工具目录打开终端；安装了虚拟环境时用 `.venv\Scripts\python.exe` 替代下列 `python`。

```powershell
python -m lmcore_sync setup
python -m lmcore_sync login
python -m lmcore_sync courses
python -m lmcore_sync sync
python -m lmcore_sync sync --only EEE339 COS301
python -m lmcore_sync sync --refresh
python -m lmcore_sync sync --no-convert
python -m lmcore_sync sync --all-forums
python -m lmcore_sync convert
python -m lmcore_sync ddl
python -m lmcore_sync doctor
python -m lmcore_sync schedule install --time 09:00
python -m lmcore_sync schedule status
python -m lmcore_sync schedule remove
```

`sync --videos` 可包含音视频，但大文件仍受单文件400MiB的写入限制（当前下载后才检查大小）。默认只存公告讨论区，不抓所有讨论。工具不会提交作业或修改 Moodle 内容。

## 每天定时同步

双击 **定时同步.cmd**，或运行 `schedule install --time 09:00`。安装工具本身不会自动注册定时任务。

- 使用 Windows 任务计划程序，按电脑本地时间运行。当前 Windows 用户必须已登录；错过时间可在条件满足后补跑，不唤醒电脑。
- 使用无窗口 Python；任务本身不会重复并发启动。手动同步时请等上一轮结束。
- 学校登录过期时退出码为2，不弹窗口等15分钟；在原 DDL 顶部标注需要登录，保留上次结果，ICS也保留。
- 本次日志在 `user_data/last_sync.log`，每次定时运行覆盖。执行 `login` 后再同步即可恢复。
- 移动项目目录前先 `schedule remove`，移动后重新安装任务；每个路径的任务名不同。

## DDL 与日历的边界

数据来自作业/测验列表、Module Handbook 的 Initial Assessment、课程资料中疑似考核的标题以及 Moodle 时间线。缺日期的考核保留为待确认，不猜具体时间；解析结果不能取代老师通知。

老师在邮件或公告调整的安排可以在 `config.json` 的 `extra_deadlines` 中补充：

```json
{"course":"ABC123","name":"In-class test","type":"线下测验","due":"2027-01-20T14:00:00+08:00","replaces":"In-class test"}
```

`due` 只有日期时日历生成全天事项并注明时间待确认；带时间无时区时按电脑本地时区解释，因此请保持系统时区与学校一致，或明确写 `+08:00`。已提交项目与过期项目不会作为新的待办日历事件导出。稳定 UID 有助于客户端识别同一事项，但反复导入、更新和删除旧事件的行为由日历软件决定。本地 `.ics` 文件不会自动变成在线订阅。

部分来源读取失败时保留旧报告，并提示尚未刷新。自检 `doctor` 只检查本机依赖、目录和登录文件存在，不保证账号会话仍有效。

## 配置

先通过 setup 创建配置，再参考 `config.example.json`。主要可选项：

| 字段 | 含义 |
|---|---|
| `courses_root` | 自动建课程目录的根路径，默认桌面 |
| `auto_courses` | 同步时发现并添加本学期课程，默认关闭（setup会询问） |
| `exclude` | 自动发现要排除的课程代码 |
| `layout_preset` / `ascii_prefixes` | 默认布局 / 默认英文布局的课程前缀 |
| `request_delay` / `retries` | 请求间隔秒数 / 最大额外重试次数 |
| `ddl_file` / `ics_file` | 覆盖默认报告路径 |
| `extra_deadlines` | 手工补充或修正截止时间 |

## 隐私与分享

`config.json`、`user_data/`（含cookies、浏览器资料、日志）、虚拟环境、课程文件、个人作业和本地交接记录不进入 Git。只发布程序源码、示例配置和测试。完整原版备份可能含登录凭证，留在本机，不要上传或发送。

密码只在学校官方登录页输入；本地cookies仍是敏感凭证。下载内容用于个人学习，使用前遵守学校的访问规则。

## 设计参考与后续方向

- [Moodle-DL](https://github.com/C0D3D3V/Moodle-DL)：成熟的Moodle课程下载项目。学校关闭Web Service时，需要其他访问方式。
- [moodle-browser-scraper](https://github.com/thntx/moodle-browser-scraper)：用真实浏览器会话支持SSO和API不可用的站点，按课程组织增量下载。
- [moodle_scraper](https://github.com/elonuziel/moodle_scraper)：浏览器抓取思路参考。

本次优先落实自动目录、网络重试、版本保留、近期资料、ICS、定时运行和可测试的错误处理。后续可考虑下载前大小限制与流式写入、跨平台定时器、更多Moodle页面样本测试；目前不声称已实现。

## 开发与验证

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m compileall -q lmcore_sync
```

GitHub Actions 在 Windows/Python 3.10和3.12上跑离线测试，不使用学校账号或上传课程资料。离线测试覆盖去重、旧版本保留、网络重试、认证失效、目录保护、报表、ICS和定时配置。真实学校网页变化仍需真实账号验证。

ZIP 的 AI 解压目录包含内容哈希。ZIP 更新会使用新的目录；旧解压目录为保护已有编辑而保留，可能仍出现在 AI 目录中，请按资料清单指向的当前路径阅读。

<div align="center">

# 📚 LMCore Sync · 西浦课件一键同步

**专为西交利物浦大学（XJTLU）学生做的 Learning Mall Core 课件同步助手**

登录一次 → 本学期每门课自动在桌面建好文件夹 → 课件、作业要求、Lab、Handbook、公告全部下载归类 → **往年试题自动配齐** → 截止日期一张表看清

[![XJTLU only](https://img.shields.io/badge/XJTLU-Learning%20Mall%20Core-6a1b9a)](https://core.xjtlu.edu.cn)
[![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078d4)](#-三步上手)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab)](https://www.python.org/)
[![Tests](https://github.com/zzhhzz88/lmcore-sync/actions/workflows/tests.yml/badge.svg)](https://github.com/zzhhzz88/lmcore-sync/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

> [!IMPORTANT]
> **西浦专属。** 默认对接 `core.xjtlu.edu.cn`，课程识别、Module Handbook 解析、XJTLU 统一身份认证登录都按西浦的系统写的。
> 这是**学生个人做的开源工具，不是学校官方产品**，和 XJTLU / ITS 没有任何关系。只读不写：不会替你提交作业、不会发帖。

---

## 😩 你是不是也这样

- 每周开 Learning Mall，一门门点进去下 Lecture，下完散落在「下载」文件夹里
- 老师悄悄更新了 Lecture 5-2 的 PDF，你手里还是旧的
- 机房上机测试、线下 in-class test 在 Moodle 上**根本没有提交入口**，日历里看不到，差点错过
- 考前去图书馆试卷库一份份找往年卷，课程还改过代码（ADS305 以前叫 INT303），按新代码根本搜不到
- 想让 AI 帮你复习，结果它读 PDF 读得一塌糊涂

## ✨ 它帮你做什么

| | 功能 | 说明 |
|---|---|---|
| 📁 | **自动在桌面建课程文件夹** | 识别本学期的真实课程（自动跳过 Communication Area、Career Centre 这类非课程），每门课一个 `桌面/<课程代码>/`，下好编号子文件夹。已有同名文件夹就沿用 |
| 📝 | **往年试题自动配齐** | 第一次给课程建文件夹时，自动去西浦图书馆 [Past Exam Papers](https://etd.xjtlu.edu.cn/index.html#/SearchDetail/EXAMXJTLU) 取最近 5–6 套期末/补考卷，放进 `05_考试复习/往年试题/`。**课程改过代码也能找到**：按代码搜不到，就按 Learning Mall 上的课程名搜出旧代码（ADS305 → INT303、ISA305 → INT301），同名的别校区课程（…TC）自动跳过 |
| 📥 | **全量下载并分类** | Lecture、Lab、作业题目和附件、Module Handbook、公告、你自己的提交和老师反馈，自动分进「课件 / 实验 / 作业 / 考试复习…」 |
| 🧹 | **不重复、不覆盖** | 按文件内容（SHA-256）去重：你以前手动下的、改过名、挪过位置的都认得出，不会再下一份；你自己放的文件永远不动 |
| 🔄 | **老师更新了也知道** | 老师替换了文件，新版放原位，旧版自动收进 `_旧版本/` |
| ⏰ | **一张表看清所有 DDL** | 桌面生成 `作业DDL.md`：Moodle 作业 + **Module Handbook 考核表** + 没有提交入口的考核提醒，带占比、剩余天数、提交状态，还有最近 7 天新增资料 |
| 📅 | **导入手机日历** | 同时生成 `作业DDL.ics`，导入 Outlook / 苹果 / 谷歌日历，提前 1 天和 2 小时提醒 |
| 🤖 | **给 AI 准备好的文字版** | PDF / PPT / Word / Notebook / 网页全部转成 Markdown（实验 zip 先解压），放在 `06_AI可读版/`，目录结构一一对应，还附 AI 阅读目录 |
| 🕘 | **每天自动同步** | 一键注册 Windows 定时任务；登录过期时会弹出登录窗口，没登录也不会卡住，只在 DDL 顶部提醒你 |

## 🚀 三步上手

1. **下载**：右上角 `Code → Download ZIP`（或 [Releases](https://github.com/zzhhzz88/lmcore-sync/releases)），解压到一个固定位置，比如 `文档/lmcore-sync`。
2. **双击 `install.cmd`**：自动装好依赖，弹出 XJTLU 统一身份认证页面 → 用你自己的学号密码登录。
3. **选课、建文件夹、开始同步**：列出本学期课程，**直接回车 = 全部**；再回车 = 建在桌面。然后它就开始下载了。

以后想更新，双击 **`同步课件.cmd`**；只想看截止时间，双击 **`查看作业DDL.cmd`**。

<details>
<summary>👀 第一次运行大概长这样</summary>

```text
本学期的课程：
   1. EEE339   EEE339-2627-S1-Digital System Design with HDL
   2. ADS305   ADS305-2627-S1-Big Data Analytics
   3. ISA305   ISA305-2627-S1-Bio-Computation
   4. COS301   COS301-2627-S1-Analogue and Digital Communications II

要同步哪些？输入编号或课程代码（空格分隔），直接回车 = 全部：
课程文件夹建在哪里？直接回车 = 桌面（C:\Users\you\Desktop）：
以后新学期的新课程也自动建文件夹？[Y/n]：

  📁 EEE339   → C:\Users\you\Desktop\eee339（新建）
  📁 ADS305   → C:\Users\you\Desktop\ads305（新建）
  📁 ISA305   → C:\Users\you\Desktop\isa305（已存在，沿用）
  📁 COS301   → C:\Users\you\Desktop\cos301（新建）

现在就开始第一次同步，把课件下载进这些文件夹吗？[Y/n]：

======== ADS305-2627-S1 → C:\Users\you\Desktop\ads305
   课程里共有 51 个活动
      + 02_课件/ADS305-Lecture7-booklet-COMPLETE.pdf
      + 04_作业/Coursework 2/ADS305 CW2 Data Brief.pdf
      ↻ 02_课件/Lecture5 Data Visualization.pdf（老师更新了文件，旧版在 _旧版本/）
   完成：新下载 103，已有跳过 26，失败 0，AI 可读版 142
      + 05_考试复习/往年试题/INT303_2025-2026_Final.pdf
   往年试题（往年代码 INT303）：5/5 份在本地

>> 作业 DDL 已更新：C:\Users\you\Desktop\作业DDL.md
   未交 12-02 23:59  ADS305  Project: Data Analysis and Modeling（占总评 20%）
   未交 12-09 23:59  ISA305  Assessment 2（占总评 10%）
```
</details>

## 🗂 同步完桌面上会多出什么

```text
桌面/
├── 作业DDL.md          ← 所有课的待办、考试、最近新增资料，一个文件看完
├── 作业DDL.ics         ← 双击导入日历
└── ads305/
    ├── 00_从这里开始.md   ← 这门课的导航（你已有的指南不会被覆盖）
    ├── 00_资料清单.md     ← Learning Mall 上每份资料 ↔ 本地哪个文件
    ├── 01_课程信息/       Module Handbook、公告.md、外部链接.md
    ├── 02_课件/           Lecture PDF / PPT
    ├── 03_实验/           Lab 材料（老师的文件夹按原名保留）
    ├── 04_作业/
    │   └── Coursework 2/  题目、数据、作业说明_Moodle页面.md、我的提交与反馈/
    ├── 05_考试复习/
    │   └── 往年试题/      INT303_2025-2026_Final.pdf … + 往年试题清单.md（每份卷子的网页链接）
    ├── 06_AI可读版/       ↑ 上面所有文档的 Markdown 版，丢给 AI 就行
    └── 07_参考资料/
```

> EEE 开头的课默认用英文目录名（`02_Lectures`、`04_Assignments/Assignment1`…），因为 Quartus 13 遇到中文或空格路径会出错。

<details>
<summary>📋 作业DDL.md 长什么样</summary>

```markdown
## ⚠ 待办（还没交的作业 / 还没考的考试）
- **12月02日 Wed（时间待定）** · ADS305 · Project: Data Analysis and Modeling（占总评 20%）（剩 15 天）
     — 02/Dec/2026 至 03/Dec/2026。This is an in-computer-lab closed-session test…
- **12月09日 Wed 23:59** · ISA305 · Assessment 2（占总评 10%）（剩 22 天）
- **12月13日 Sun 23:59** · EEE339 · Assignment 2 Submission（占总评 20%）（剩 26 天）

## 📥 最近 7 天新增 / 更新的资料（6 个）
**EEE339**
- 🆕 2026-11-17 [Lecture25_full.pdf](<eee339/02_Lectures/Lecture25_full.pdf>)

## ❓ 有考核但 Moodle 上没有截止时间（务必自己确认）
- ADS305 · Coursework 2: PineTrail Campervans（版块：Lecture 8 - Supervised Learning）
```
</details>

## 🤔 常见问题

<details>
<summary><b>往年试题是怎么来的？会不会把我账号弄出问题？</b></summary>

来源是西浦图书馆的 Past Exam Papers（`etd.xjtlu.edu.cn`）。工具在你已经登录的浏览器里打开它，用网站自己的「View Online」流程拿 PDF，和你手动点开是一样的，不绕过任何权限。

- **只在第一次建课程文件夹时查一次**，之后的每日同步不会再去碰试卷库；缺的卷子最多每天补一次。
- 每门课只取最近 5–6 套；**两份之间至少隔 25 秒，一次最多 12 份**。图书馆会把短时间内连续下载判定为“批量下载”，把账号锁 60 分钟。一旦遇到这种锁，工具立刻停手，**2 小时内不再访问**，之后的同步自动补齐。
- 学校没给下载权限、或者要求先在网页上解锁时，工具不会硬来：它在 `往年试题/往年试题清单.md` 里列出每份卷子和「打开」链接，你在浏览器里看就行。
- 试卷版权归学校，**只供本人复习**，不要上传、转发（`往年试题/` 默认不会被 git 提交）。
</details>

<details>
<summary><b>为什么不直接用 Moodle-DL？</b></summary>

[Moodle-DL](https://github.com/C0D3D3V/Moodle-DL) 很好用，但它依赖 Moodle 的手机端 Web Service。西浦的 Learning Mall Core 关闭了这个接口（`enablemobilewebservice = 0`），还走统一身份认证，所以连不上。本工具改用**真实浏览器（Edge）由你本人登录**，再用这个会话下载。
</details>

<details>
<summary><b>我的密码安全吗？</b></summary>

密码只在学校官方登录页里由你自己输入，工具从来不读、不存密码。登录后的 cookie 保存在本机 `user_data/`，**相当于登录凭证，不要发给别人、不要上传**（`.gitignore` 已排除）。
</details>

<details>
<summary><b>我之前手动下了一堆课件，会被覆盖或者重复下载吗？</b></summary>

不会。同步前会给课程文件夹里所有文件算哈希，内容相同的直接跳过，清单里标「已有(未重复下载)」并指向你那份。你改过的文件遇到老师更新时，新版另存副本，你的那份不动。
</details>

<details>
<summary><b>Moodle 上没写日期的考试怎么办？</b></summary>

工具会读每门课 Module Handbook 里的考核表（Initial Assessment，补考部分自动忽略），所以机房上机测试、in-class test 也能列出来；课程页上像考核但没有提交入口的资料会单独提醒。老师在邮件里改了时间、或者出了期末考试安排，在 `config.json` 里加一条 `extra_deadlines` 即可，`replaces` 写要替换的自动条目关键词：

```json
"extra_deadlines": [
  {"course": "ABC123", "name": "期末考试（闭卷 2 小时）", "type": "期末考试",
   "due": "2026-12-31T10:00", "note": "09:30 入场；考场见考试安排", "weight": "60%",
   "replaces": ["Written Examination"]}
]
```
</details>

<details>
<summary><b>下学期还要重新设置吗？</b></summary>

不用。开着「新学期的新课程也自动建文件夹」，下次同步时新课程会自动出现在桌面上；不想要的课写进 `config.json` 的 `exclude`。
</details>

<details>
<summary><b>转成 Markdown 用的是什么？</b></summary>

优先用本机装的 Filee（`filee-cli.exe`，离线、快、保留课件图片）；没装就自动用 [markitdown](https://github.com/microsoft/markitdown)。公式和图表请以原 PDF 为准。
</details>

<details>
<summary><b>别的学校能用吗？</b></summary>

把 `config.json` 的 `base_url` 换成你们的 Moodle 地址，理论上能跑（需要 Moodle 4.x）。但课程代码识别、Handbook 解析是按西浦格式写的，其他学校需要自己调整 `course_pattern`，Handbook 部分可能不生效。
</details>

<details>
<summary><b>出问题了怎么办？</b></summary>

双击 **`检查环境.cmd`**（`doctor` 命令）看看缺什么。登录过期就运行 `python -m lmcore_sync login` 重新登录。怀疑漏了老师的更新，用 `sync --refresh` 全部重新核对一遍。还不行欢迎提 [Issue](https://github.com/zzhhzz88/lmcore-sync/issues)（**不要贴 cookie、学号等个人信息**）。
</details>

## ⌨️ 命令速查

安装后在工具目录里用 `.venv\Scripts\python.exe` 代替下面的 `python`。

| 命令 | 作用 |
|---|---|
| `python -m lmcore_sync setup` | 登录 → 选本学期课程 → 在桌面建文件夹 → 第一次同步 |
| `python -m lmcore_sync sync` | 增量同步（新课程自动建文件夹） |
| `python -m lmcore_sync sync --only EEE339 COS301` | 只同步某几门 |
| `python -m lmcore_sync sync --refresh` | 不看记录，全部重新核对 |
| `python -m lmcore_sync ddl` | 只刷新 `作业DDL.md` / `.ics` |
| `python -m lmcore_sync past-papers` | 重新查往年试题（平时只在第一次建文件夹时自动查一次） |
| `python -m lmcore_sync convert` | 只重新生成 AI 可读版 |
| `python -m lmcore_sync login` | 登录过期时重新登录 |
| `python -m lmcore_sync doctor` | 检查环境 |
| `python -m lmcore_sync schedule install --time 09:00` | 每天 9 点自动同步（`status` / `remove` 查看、取消） |

<details>
<summary>⚙️ config.json 可选项</summary>

| 字段 | 默认 | 含义 |
|---|---|---|
| `courses_root` | 桌面 | 课程文件夹建在哪（自动识别 OneDrive 重定向的桌面） |
| `auto_courses` | `true` | 同步时自动发现本学期新课程并建文件夹 |
| `exclude` | `[]` | 不要的课程代码，如 `["PSP305"]` |
| `layout_preset` / `ascii_prefixes` | `zh` / `["EEE"]` | 默认中文目录 / 哪些课用英文目录 |
| `course_pattern` | `^[A-Z]{2,4}\d{3}` | 什么样的课程短名算「真课程」 |
| `request_delay` / `retries` | `0.3` / `3` | 请求间隔秒数 / 网络出错重试次数 |
| `ddl_file` / `ics_file` | 桌面 | DDL 报告和日历文件位置 |
| `extra_deadlines` | `[]` | 手动补充或修正的截止时间、考试安排 |

每门课还可以单独写 `"layout": "en"` / `"zh"`，或者一个字段对象覆盖具体文件夹名。
</details>

## 🙏 致谢与参考

- [thntx/moodle-browser-scraper](https://github.com/thntx/moodle-browser-scraper)、[elonuziel/moodle_scraper](https://github.com/elonuziel/moodle_scraper)（MIT）——「真实浏览器会话 + 抓取」思路来源
- [C0D3D3V/Moodle-DL](https://github.com/C0D3D3V/Moodle-DL) —— 最成熟的 Moodle 下载器（API 可用时首选它）
- [setsun-ai/moodle-sync](https://github.com/setsun-ai/moodle-sync)、[Deadline-Hub](https://github.com/Vatsalyarabari/Deadline-Hub)、[moodle2ia](https://github.com/estevao-tarifa/moodle2ia) —— 定时同步、旧版本保留、从课程文件里找 DDL、给 AI 的课程目录等想法

## ⚖️ 使用须知

- 课件和资料的版权属于学校和任课老师，**只供你本人学习**，不要二次传播；往年试题尤其不要上传。
- 工具默认每次请求间隔 0.3 秒，请别把间隔改得太小给学校服务器添麻烦。
- 使用前请遵守西浦关于 Learning Mall 和 IT 资源的使用规定。

<details>
<summary>🛠 开发者</summary>

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

GitHub Actions 在 Windows + Python 3.10 / 3.12 上跑离线测试（不用学校账号）。网页结构变化仍需要真实账号验证。代码结构：`moodle.py` 浏览器会话与请求、`sync.py` 单门课同步、`courses.py` 课程识别与桌面文件夹、`deadlines.py` DDL 与 Handbook 解析、`calendar_ics.py` 日历、`convert.py` 转 Markdown、`maintenance.py` 定时任务与自检。
</details>

---

<sub>English: an unofficial, student-made tool that syncs **XJTLU Learning Mall Core** course files into tidy per-course folders on your desktop, keeps old versions when lecturers update files, builds a deadline digest (including assessments listed only in the Module Handbook) and an `.ics` calendar, and converts everything to Markdown for AI study assistants. Works with XJTLU SSO via a real Edge browser session; no Moodle API token needed.</sub>

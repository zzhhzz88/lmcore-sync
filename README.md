# lmcore-sync — Learning Mall Core 课件一键同步

把 XJTLU Learning Mall Core（Moodle）上选定课程的**课件、作业要求、Lab、Handbook、公告、自己的提交和反馈**全部下载到本地课程文件夹，
**自动跳过你已经有的文件**，按类别整理成清楚的子文件夹，并把 PDF/PPT/Word 转成 **AI 好读的 Markdown**。

## 为什么不用 Moodle-DL？
学校的 Moodle 关掉了移动端 Web Service（`enablemobilewebservice = 0`），而且用 SSO 统一认证登录，
[Moodle-DL](https://github.com/C0D3D3V/Moodle-DL) 这类靠 API Token 的工具连不上。
本工具参考 [thntx/moodle-browser-scraper](https://github.com/thntx/moodle-browser-scraper)（MIT）和
[elonuziel/moodle_scraper](https://github.com/elonuziel/moodle_scraper)（MIT）的思路：
**用真实浏览器（Edge）由你本人登录一次**，之后用这个登录状态自动抓取。在此基础上增加了：

- 只同步指定课程，每门课放进你指定的文件夹；
- 去重：按文件内容（SHA-256）对比你文件夹里已有的所有文件，即使你改过名、挪过位置也能认出来；
- 分类：课程手册 / 课件 / 实验 / 作业要求 / 我的提交与反馈 / 往年试题 / 公告 / 其他；
- 作业页、Page、Book、公告讨论区的网页文字也存成 .md；
- 用 Filee（`filee-cli.exe`）离线把文档转成 Markdown，没有 Filee 时用 markitdown；
- 每门课生成 `00_资料清单.md`，写清每份材料的来源版块、状态、路径。

## 在新电脑上安装
1. 把整个 `lmcore-sync` 文件夹拷过去（**不要**拷 `user_data/`、`config.json`、`.venv/`，那是原主人的登录状态和配置）。
2. 双击 `install.cmd`：自动建环境、装依赖，然后弹出浏览器让你登录，再让你选课程和对应文件夹。
3. 以后每次双击 `同步课件.cmd` 就会增量同步（只下载新增的）。想换课程就双击 `重新选择课程.cmd`。

需要：Windows 10/11、Python 3.10+（安装脚本会尝试用 winget 自动装）、Edge 或 Chrome（没有的话自动下载 Chromium）。

## 命令行
```
python -m lmcore_sync setup                    # 登录并选择课程
python -m lmcore_sync courses                  # 列出你所有课程
python -m lmcore_sync sync                     # 同步全部已配置课程
python -m lmcore_sync sync --only EEE339       # 只同步一门
python -m lmcore_sync sync --refresh           # 不看记录，全部重新检查
python -m lmcore_sync sync --all-forums        # 所有讨论区都存档（默认只存公告）
python -m lmcore_sync convert                  # 只重新生成 AI 可读版
python -m lmcore_sync ddl                      # 只刷新作业 DDL 汇总
python -m lmcore_sync login                    # 登录过期时重新登录
```

## 作业 DDL 汇总
每次 `sync` 结束都会在桌面生成/更新 **`作业DDL.md`**：先列“还没交的”，再列全部即将截止和已截止的作业/测验，带剩余天数和提交状态。
数据来自各课程的作业列表页和 Moodle 时间线。只在邮件里通知、Moodle 上没有的考试，可以在 `config.json` 里手动加：
```json
"extra_deadlines": [{"course": "COS301", "name": "In-class test", "type": "线下测验", "due": "2026-11-24"}]
```
（`due` 只写日期时显示“时间待定”；也可以写 `2026-11-24T14:00`。想换保存位置就加 `"ddl_file": "D:/xxx.md"`。）
双击 `查看作业DDL.cmd` 可以只刷新 DDL。

## 输出结构（直接放进每门课的文件夹，和你自己的资料合在一起）
```
<课程文件夹>/
  00_资料清单.md          ← Learning Mall 每份资料在本地的位置（自动生成）
  01_课程信息/            ← 课程手册、公告.md、外部链接.md
  02_课件/
  03_实验/                ← Moodle 上的 Lab 文件夹按原名保留
  04_作业/<作业名>/       ← 题目文件 + 作业说明_Moodle页面.md（时间、要求、提交状态）
           └ 我的提交与反馈/
  05_考试复习/
  06_AI可读版/            ← 上面所有文档的 Markdown 版，目录结构一一对应（zip 会先解压）
  07_参考资料/
  .lmcore/                ← 增量同步记录，别删
```
- 你自己早就放好的文件（哪怕改了名、放在别的子文件夹）不会再复制一份，清单里标“已有(未重复下载)”并指向你那份。
- 文件夹名可以按课程改：在 `config.json` 该课程下加 `"layout"`，例如 EEE339 因为 Quartus 不支持中文/空格路径，用的是
  `{"lecture": "02_Lectures", "assessment": "04_Assignments", "ai": "06_AI_readable", "assignment_nospace": true, ...}`。

## 作业 DDL 的数据来源
1. 各课程 Moodle 的作业 / 测验列表（截止时间 + 你的提交状态）；
2. **Module Handbook 的考核表**（Initial Assessment：每项占比、发布和截止日期，补考部分自动忽略）——机房上机测试、线下测验这种 Moodle 上没有提交入口的考核也能列出来；
3. 课程页面上名字像考核、但没有提交入口的资料，列在“❓ 有考核但 Moodle 上没有截止时间”里提醒你确认；
4. `config.json` 的 `extra_deadlines`：老师在邮件/公告里改了的安排、期末考试时间表等，`replaces` 写要替换掉的自动条目名称关键词。

## 隐私
- `user_data/browser_profile` 保存的是**你自己的登录状态**，相当于登录凭证，不要发给别人。
- 工具不保存密码；密码只在学校官方登录页里由你自己输入。
- 下载的课件版权属于学校和老师，仅供个人学习。

## 其他学校的 Moodle
把 `config.json` 里的 `base_url` 改成你们学校的 Moodle 地址即可（需要 Moodle 4.x）。

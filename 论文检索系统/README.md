# 科研论文检索系统：最终演示版

本项目以组员提供的 `abc汇总.zip` 为基线，保留其 A 数据、B 检索内核、C 的 Streamlit 页面与 `search_api.py` 适配层。当前目录就是最终运行版本。先前 Codex 自建页面已停用，保留在 `_archive/codex_demo_previous_20261105/`，不参与启动，也不进入最终交付包。

## 直接启动

当前机器已安装依赖。双击 **启动项目.bat**，或在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

打开 <http://127.0.0.1:8501>。启动终端需要保持运行，停止时按 `Ctrl+C`。

换机器时先安装一次：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

需要 Python 3.10 或以上，已在 Python 3.12.5、Streamlit 1.45.0 上验收。启动脚本优先使用项目 `.venv`，否则使用当前 `python`。端口占用时使用 `-Port 8502`，浏览器相应打开 8502。使用虚拟环境时，下面命令中的 `python` 也可替换为 `.\.venv\Scripts\python.exe`。

首次安装依赖需要网络。搜索、分类、分页和摘要浏览使用本地数据，可离线操作；打开外部 arXiv 原文需要网络。

## 已有功能

- 正式论文库 800 篇，四个主分类各 200 篇。
- 空关键词时全量浏览，按分类筛选、排序和分页。
- 英文关键词搜索，BM25 排序、标题加权、短语加成。
- 默认任意词（OR）匹配，也可要求全部有效查询词（AND）匹配。
- 摘要片段高亮，标题命中标记，作者、年份、分类和得分展示。
- 每页 5 / 10 / 20 条，上一页、下一页及边界禁用。
- 独立详情页：完整摘要、作者、arXiv ID、来源和原文链接。
- 详情返回保留搜索词、分类、排序、AND、每页条数和原页码。
- 无结果、非法分页、索引初始化失败均有提示；正式数据缺失时明确报错，不退回 18 篇样例。

当前论文全部为 2026 年，页面静态展示实际首次提交年份，不演示跨年份筛选。英文语料请使用英文查询，中文查询不自动翻译。

## 演示步骤

| 操作 | 当前正式数据上的结果 |
| --- | --- |
| 搜索框保持为空 | 浏览 800 篇 |
| 输入 `dense retrieval`，点击“检索” | OR 命中 182 篇 |
| 勾选“所有关键词都必须命中（AND）” | 33 篇 |
| 翻到第 2 页并查看详情，再返回 | 返回第 2 页，搜索与筛选保留 |
| 清空关键词，分类选择 `cs.IR` | 浏览 200 篇 |
| 输入 `qxzvbnmqxzvbnm` | 显示无匹配提示 |

完整的约 3 分钟讲解稿见 `docs/演示脚本.md`。实际录制的无声示范为 `demo/组员版_演示.webm`，浏览器可打开观看。`demo/01_home.png` 至 `04_filter.png` 是最终组员版运行截图。

## 本地验收

```powershell
python -B scripts/check_project.py
```

预期：先输出 `DATA_VALIDATION_OK: formal=800`，最后显示 `Ran 23 tests` 和 `OK`。包含组员原有 12 项 API/页面测试、A 的 8 项采集清洗测试及 3 项最终回归测试。脚本把临时文件放在 `.verification/tmp/`，不修改正式数据，也不重新联网采集。

真实浏览器另完成 9 项检查，录制过程使用正式语料。验收详情见 `docs/检查与修改说明.md`。这些是功能验证，800 篇正式数据尚无完整人工相关性标注，不能据此宣称 Recall 或 Precision。

原有 `evaluation_report.json` 是 18 篇样例上的历史评测，不是正式数据的效果报告。

## 目录与模块边界

```text
app.py                         C：组员的 Streamlit 页面，做少量状态修补
search_api.py                  C：字段适配、空查询浏览与论文详情
paper_search/                  B：原始 BM25 内核，保持不变
paper-data/                    A：原始数据、脚本、说明与测试，保持不变
  data/papers_final.jsonl       页面优先使用的正式数据
  data/papers.jsonl             同内容正式副本
run.ps1 / 启动项目.bat           启动入口
scripts/check_project.py       本地验收入口
tests/                        组员原始测试与新增回归测试
docs/演示脚本.md                操作顺序和可照读讲解稿
docs/项目说明.md                项目报告文字稿
docs/检查与修改说明.md           功能检查、版本来源与修改边界
demo/                         最终页面截图与无声演示视频
deliverables/                 供分享的最终项目压缩包
```

应用不需要 Flask/FastAPI、数据库、Elasticsearch 或大模型 API。不要将 `_archive`、`_incoming`、`.verification`、虚拟环境或浏览器缓存当作交付内容。

按最新要求不制作、不交付 PPT。

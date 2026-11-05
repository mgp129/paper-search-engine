# 科研论文搜索引擎 A 模块

从 arXiv 官方 API 采集论文元数据，按真实主分类筛选、去重、清洗，输出供 B、C 直接读取的 JSONL。
只负责数据，不包含数据库、BM25、Streamlit 或后台服务。不下载 PDF。

## 环境和安装

Python 3.10 或更高版本，全部使用标准库，无第三方运行依赖。采集需连接 `export.arxiv.org`；读取数据和离线验收无需网络。
在 `paper-data` 目录执行：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Linux / macOS：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

也可以不激活环境，直接使用 `.venv/bin/python`（Windows 为 `.venv\Scripts\python.exe`）。

## 采集和验收

交付包已经包含采集完成的数据；B、C 不需要重新运行采集程序。
如需自行重新采集，按以下顺序运行：

```bash
python scripts/fetch_arxiv.py --stage sample --page-size 20
python scripts/validate_data.py --file .cache/papers_sample.jsonl --min-records 20 --max-records 20
python scripts/fetch_arxiv.py --stage dev
python scripts/validate_data.py --file data/papers_dev.jsonl
python scripts/fetch_arxiv.py --stage final
python scripts/validate_data.py --check-urls 4
```

默认开发版 150 篇、正式版 800 篇；四个主分类尽量均衡。参数 `--dev-size` 可设 100–200，`--final-size` 可设 500–1000。
查询按首次提交时间降序；每类分页直到配额达标。标题/摘要保留原文和大小写，仅作必要格式清理。

连续命令会复用 `.cache` 中的官方 XML 页面，自动继续分页，不重复请求成功缓存的页面。
`.cache` 仅是可再生成的本地采集缓存，不属于交付数据，不打包。
同一缓存固定查询截止时间；需要新的采集会话时，使用 `--root ../paper-data-new` 另选输出目录，避免覆盖现有交付数据。
默认请求结束后等待 3.1 秒、超时 45 秒、最多重试 2 次；每个学科累计最多 40 个成功页面，避免无限分页。
失败时非零退出并保留缓存，可排查后重运行同一命令。若开发版已存在，final 阶段保留该开发版。

日常离线验收：

```bash
python scripts/validate_data.py
```

验收内容包括 UTF-8 无 BOM、逐行 JSON、字段和类型、空值、重复 ID、分类、URL 与 ID 对应、文本清洗、统计守恒及两个正式文件逐字节一致。
`--check-urls 4` 才会联网：固定随机种子、每类抽一篇、间隔至少 3 秒；结果写入统计与验收报告。
退出码：`0` 检查通过；`1` 本地数据有问题；`2` 本地通过但本次 URL 抽样未能全部验证。
抽样成功不代表逐一验证了全部论文 URL；离线验收也不代表完成了网络访问。

## 目录

```text
paper-data/
├── data/
│   ├── papers_dev.jsonl
│   ├── papers_final.jsonl
│   ├── papers.jsonl
│   └── data_stats.json
├── scripts/
│   ├── fetch_arxiv.py
│   └── validate_data.py
├── docs/
│   ├── data_card.md
│   └── validation_report.json
├── tests/
│   └── test_pipeline.py
├── requirements.txt
└── README.md
```

采集日志、实际学科分布、年份范围、丢弃数量、文件 SHA-256 和 URL 抽查结果见 `data/data_stats.json`。
数据来源、字段与局限见 `docs/data_card.md`；完整数据验收结果见 `docs/validation_report.json`。
可用 `python -m unittest discover -s tests -v` 运行清洗、异常输入和缓存恢复测试；测试数据不会混入正式数据。

## 交给 B、C

将完整 `paper-data` 文件夹交给组员，或至少提供 `data/papers.jsonl` 和 `docs/data_card.md`。
正式程序统一读取 **`data/papers.jsonl`**；开发联调可先读取 `data/papers_dev.jsonl`。
`papers.jsonl` 与 `papers_final.jsonl` 内容和 SHA-256 完全一致。

以 `paper-data` 为运行目录，直接读取：

```python
import json

papers = []
with open("data/papers.jsonl", "r", encoding="utf-8") as f:
    for line in f:
        papers.append(json.loads(line))
```

B 使用 `title` 和 `abstract` 建立索引、`year` 和 `category` 进行筛选。
C 使用 `title`、`authors`、`abstract`、`url` 展示信息。`authors` 已是字符串数组，不需再次转换字段。

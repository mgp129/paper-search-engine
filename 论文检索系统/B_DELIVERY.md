# B 模块最终集成状态

B 的 `paper_search/engine.py`、`cli.py` 和 `__init__.py` 与组员压缩包逐字节一致。

已经实现 JSONL 加载、英文分词与停用词过滤、标题加权倒排索引、BM25、短语加成、OR/AND、年份与分类过滤、分页、排序、摘要片段、命中词和索引保存加载。

A 的正式数据已到位：`paper-data/data/papers_final.jsonl`，800 篇，无跳过。C 的 `search_api.py` 与 `app.py` 已接入，直接启动项目即可演示。

验收命令：

```powershell
python -B scripts/check_project.py
python -B -m paper_search.cli stats --data paper-data/data/papers_final.jsonl
python -B -m paper_search.cli search --data paper-data/data/papers_final.jsonl --query "dense retrieval" --top-k 5
```

原 B 说明中列出的独立 `build_index.py`、`evaluate_recall.py` 与 9 项 B 测试没有包含在组员压缩包里。当前页面直接从 JSONL 建立内存索引，不依赖这些缺失脚本；正式项目使用现有 API/页面测试与最终回归测试验收。根目录历史样例评测报告不代表正式语料检索质量。

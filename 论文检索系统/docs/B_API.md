# B 部分 API 交接说明

## 1. A 与 B 的边界

A 交付 UTF-8 JSONL，一行一篇论文。B 在加载时读取以下字段：

```text
paper_id, title, abstract, authors, year, category, url, source
```

B 负责：

- 小写化与英文分词
- 停用词过滤
- 标题加权
- 倒排索引
- BM25 打分
- 过滤、排序、分页
- 摘要片段
- 命中词与命中字段标记

A 不应提前做分词、去停用词、拼接 search_text 或生成词频。否则前端显示和高亮会受影响。

## 2. C 与 B 的边界

C 只调用以下公开接口：

```python
engine = PaperSearchEngine.from_jsonl("paper-data/data/papers_final.jsonl", strict=False)
hits = engine.search(...)
response = engine.search_with_meta(...)
engine.save(path)
engine = PaperSearchEngine.load(path)
engine.stats
engine.papers
```

建议 C 不要访问以 `_` 开头的内部字段。

## 3. `search()` 参数

```python
search(
    query: str,
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    category: str | list[str] | None = None,
    top_k: int = 20,
    offset: int = 0,
    require_all: bool = False,
    sort_by: str = "score",
) -> list[dict]
```

`sort_by` 可选：

- `score`：相关性降序，默认值
- `year_desc`：年份降序
- `year_asc`：年份升序
- `title`：标题字典序

## 4. `search_with_meta()` 返回

```json
{
  "query": "dense retrieval",
  "total": 182,
  "hits": [],
  "filters": {
    "year_from": 2026,
    "year_to": 2026,
    "category": null,
    "require_all": false,
    "sort_by": "score"
  }
}
```

以上示例为当前正式数据上的查询，省略了实际 `hits` 内容。C 使用 `total` 做分页和“共找到 N 篇”提示。数据全部是 2026 年，不要使用截止于 2025 年的过滤条件。

## 5. 错误处理建议

- `query` 为空：B 返回空结果，不抛异常。
- 数据文件不存在：抛 `FileNotFoundError`，C 应显示初始化失败。
- 数据文件某条记录字段缺失：`strict=False` 时跳过并记录到 `engine.skipped_records`。
- 非法 JSON 行：当前会直接抛 `ValueError`，因为整行无法解析，建议 A 修正数据。
- 某个分类无结果：返回空结果，不抛异常。

C 在应用启动时构建一次引擎并缓存，不要每次搜索都重新读取 JSONL。

# C 部分交接说明（Streamlit 页面）

负责人：C  
页面：`app.py`  
适配层：`search_api.py`  
数据：A 的 `paper-data/data/papers_final.jsonl`（800 篇）  
检索：B 的 `paper_search/engine.py`（BM25）

## 1. 启动方式

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

安装后浏览器打开 `http://localhost:8501`。页面启动时构建一次索引，并用
`@st.cache_resource` 缓存；后续交互不会重复读取 JSONL。

## 2. 已完成功能

- 搜索框和检索按钮；关键词为空时按筛选条件浏览全部 800 篇，不报错。
- 结果卡片显示标题、作者、年份、分类、BM25 摘要片段和得分。
- 使用 B 的 `matched_terms` 做词边界高亮，并显示“标题命中”标记。
- 学科分类多选，支持 cs.IR、cs.CL、cs.LG、cs.AI。
- 排序支持相关度、最新、最早、标题四种方式。
- 支持 OR 检索和 `require_all` 对应的 AND 检索。
- 使用 B 返回的 `total` 分页，支持上一页、下一页、首尾页按钮置灰。
- 筛选变化回到第 1 页；越界页码会立即夹回最后一页并重新取数。
- 详情页显示完整摘要、全部作者、arXiv ID、原文链接、数据来源。
- 覆盖空输入、无结果、全停用词、非法参数、详情不存在和索引初始化失败。

## 3. 字段与参数适配

B 的输出由 `search_api.py` 统一转换为页面字段，A/B 无需修改：

| B 字段 | 页面字段 | 说明 |
|---|---|---|
| `paper_id` | `id` | 保留原始论文标识 |
| `year` | `published` | A 只有年份，页面按年份展示 |
| `category` | `categories` | 页面使用分类列表 |
| `snippet` + `matched_terms` | `snippet` | C 负责按词边界添加高亮标记 |
| 无 DOI | `doi` | 详情页无数据时自动隐藏 |

排序映射为 `relevance→score`、`newest→year_desc`、`oldest→year_asc`、
`title→title`；分页映射为 `page/page_size→offset/top_k`。

另外补了两个 B 未提供的页面能力：

- 空关键词全量浏览：筛选、排序和分页在适配层完成。
- `get_paper(id)`：通过 `engine.papers` 建立只读 ID 映射供详情页使用。

## 4. 原始验收结果（随包记录）

已使用 A 的 800 篇正式数据和 B 的检索实现完成联调：

- 索引文档 800 篇，词表 11672，跳过异常记录 0 条。
- 空关键词浏览总数为 800，分页结果不重叠且 `total` 一致。
- `dense retrieval` 的 OR 搜索为 182 篇，AND 搜索为 33 篇。
- `language model` 可展示多页结果，分页和详情往返正常。
- 分类筛选、四种排序、无结果和非法参数均已验证。
- 页面自动化测试共 12 项，全部通过：

```powershell
python -m unittest discover -s tests -v
```

- Web 服务健康检查通过：`/_stcore/health` 返回 HTTP 200 和 `ok`。
- 浏览器运行截图：`C_运行截图.png`。

测试使用 Streamlit 自带 `AppTest`，覆盖真实页面脚本，不只测试内部函数。

## 5. 演示建议

1. 保持搜索框为空，展示 800 篇论文的全量浏览。
2. 搜索 `dense retrieval`，展示 182 篇 OR 结果。
3. 勾选“所有关键词都必须命中”，展示 33 篇 AND 结果。
4. 搜索 `language model`，演示分页和结果卡片。
5. 点击“查看详情”，展示完整摘要和原文链接。
6. 清空关键词并选择 `cs.IR`，展示分类筛选为 200 篇。

## 6. 已知限制

- A 的数据全部来自 2026 年，年份筛选在页面中显示为静态信息，演示时不要强调“跨年份筛选”。
- 语料是纯英文，中文关键词会返回 0 条；演示统一使用英文词。
- 当前共检索 800 篇，采用纯 Python 内存索引，足以覆盖课堂演示规模。

## 7. C 部分文件

```text
app.py                    Streamlit 页面
search_api.py             A/B 到页面的适配层
requirements.txt          页面运行依赖
tests/test_search_api.py  适配层测试
tests/test_streamlit_app.py 页面交互测试
C_运行截图.png            实际运行截图
C_交接说明.md             本文件
```

## 8. 最终集成补充

最终项目以本组员版本为基础，保留原页面结构与检索内核。详情页返回时显式保留全部搜索控件状态，改用回调进入和返回详情，避免状态丢失或多次 rerun 的中间状态。浏览模式不显示无意义的 0 分，年份文案改为“首次提交年份”，详情不再截断超过 50 人的作者列表。

正式数据缺失时不再退回 18 篇样例。运行环境固定 Streamlit 1.45.0，新增启动入口、项目说明、演示讲解稿、实际截图和无声示范。

最终本地验收为 23 项测试通过，真实浏览器 9 项检查通过，详情见 `docs/检查与修改说明.md`。不制作、不交付 PPT。

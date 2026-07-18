# 候选池 Topic 多样性设计

## 目标
解决“同一批推荐经常扎堆在相似 topic”这个体感问题。核心不是随机打散，而是让 discovery pool 和推荐挑选都具备显式的 topic 多样性控制。

第一版目标：
- 给候选内容补稳定 `topic_key`
- `reshuffle` 和常规推荐按 `topic_key` 先分桶，再回填
- 优先修 `related_chain` 和 `search` 这两个最容易把池子灌偏的来源

不做的事情：
- 不引入复杂 taxonomy
- 不新增独立 topic 表
- 不做基于 embedding 的内容聚类

## 现状与问题
当前主线里，推荐丰富度不足主要有三层原因：

1. 上游池子本身就偏  
`related_chain` 很容易沿着少数 seed 一路扩出很多同 topic 内容，一轮补货就把池子灌偏。

2. 推荐层多样性约束信息不足  
虽然推荐层已经开始做轻量多样性选择，但很多候选的 `tags` 为空，导致“这几条其实是一类内容”无法稳定识别。

3. 缺少池子层配额  
现在主要是在推荐挑选时才做控制，池子层还没有“同 topic 限额 / 先分桶再取样”的逻辑。

因此，单纯在最终排序层继续加规则是不够的，必须把 topic 信息和配额前移。

## 核心方案

### 1. 为候选内容补 `topic_key`
在 `content_cache` 上新增：
- `topic_key TEXT DEFAULT ''`

它不是完整分类体系，而是第一版可稳定工作的轻量主题键。

生成优先级：
1. `tags`
   - 如果内容已有标签，取前 1 到 2 个高信息量标签组合，例如 `国际时事:地缘政治`
2. 策略上下文
   - `SearchStrategy`：从 query 归一出 `topic_key`
   - `RelatedChainStrategy`：从 seed topic 或 seed trace 归一出 `topic_key`
   - 其它策略后续再细化
3. 标题兜底
   - 若没有标签和上下文，就从标题提取轻量关键词

这样至少能让同类候选拥有稳定分组键，而不是完全依赖标题文本偶然相似。

### 2. 推荐挑选改成“先分桶，再回填”
`generate_recommendations()` 和 `reshuffle_recommendations()` 共享同一套多样性逻辑：

1. 按 `topic_key` 分桶
2. 每个桶先取 1 条最高分候选
3. 桶之间按桶内最高分排序
4. 若不足 `limit`，再从剩余候选里按总分回填

这样可以保证：
- 当池子里有多个方向时，一批推荐会更自然地覆盖不同方向
- 当池子本来就不够丰富时，仍然能回填高分内容，不会“为了多样性把质量做没”

### 3. 池子层做轻量 topic 配额
仅靠推荐时分桶还不够，池子本身也要避免被同一条链一路灌满。

第一版不在所有策略里都加配额，而是：
- `RelatedChainStrategy`
  - 补稳定 `topic_key`
  - 结果列表在返回前做一次按 `topic_key` 的轻量压缩
- `SearchStrategy`
  - 结果带 query 派生的 `topic_key`

这样能先把最明显的同 topic 泛滥压下去，不用一次性重写所有 discovery 策略。

## 验收标准
- `content_cache` 能稳定写入并读取 `topic_key`
- `reshuffle_recommendations()` 在候选足够时，不再把同一 topic 连续塞满一批
- `generate_recommendations()` 也共享这套 topic 分桶逻辑
- `SearchStrategy` 和 `RelatedChainStrategy` 新入池候选会带 `topic_key`
- 候选不足时仍能回填到目标数量，不牺牲基本可用性

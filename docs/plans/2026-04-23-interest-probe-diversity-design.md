# Interest Probe Diversity Design

**Problem:** 当前兴趣探针不是“探索太大胆”，而是 active probe 在用户体感上塌缩成同一种高概念解释型方向，导致方向集中、丰富度不够。

**Observed Evidence:**

- speculation prompt 以心理桥接、跨维度分散、远距离桥接和纯新奇方向为硬约束，鼓励“高相关但有陌生感”的候选。
- `category` 分散并不等于用户感受到的体验分散；不同 `category` 仍然可能都落在“重入口、知识解释型”的同一体验轴上。
- speculative candidate 生成后基本直接进入 active pool，生产链路缺少“这个人真会点开吗”和“整批候选体验是否塌缩”的本地过滤。
- probe push 侧主要按 `confirmation_count` 选择候选，不会主动避开最近已经推过的同体验轴内容。

**Root Cause:**

1. speculative diversity 的定义错位了。当前强调学科/桥接距离分散，但没有显式约束用户体感上的轻重、入口形式和内容模式。
2. active pool 是“小池子直接承载最终体验”，但生成阶段没有先做过采样和本地 balanced selection，导致模型一次偏向就直接体现在用户面前。
3. push 排序只解决“谁还没被验证”，没有解决“不要连续推同一种感觉的内容”。

**Design Decision:**

- 保留大胆探索，不把 speculative probe 收缩成纯相邻推荐。
- 在 speculative candidate 中引入两条新的体验维度：
  - `experience_mode`: `knowledge` / `aesthetic` / `hands_on` / `people_story` / `wander_observe`
  - `entry_load`: `light` / `heavy`
- 生成阶段改成“先多生成，再本地筛选”：
  1. LLM 继续生成一批大胆候选
  2. 本地 balanced selector 选出最终进入 active pool 的 3-5 条
  3. selector 优先保证至少一条轻入口、至少一条非知识解释型、且整批不被 `far/novel` 吞掉
- push 阶段保留 `confirmation_count` 作为验证信号，但增加“最近体验轴去重”，避免连续把同一 `experience_mode` / `entry_load` 推给用户。
- 所有新约束都保留降级路径：如果模型没给出足够多样的候选，则放宽规则，不阻塞 active pool 和 probe push。

**Expected Outcome:**

- speculative probe 仍然允许跨域和陌生感，但一组 active hypotheses 不会再被单一高概念解释型内容霸屏。
- 用户看到的 probe 在体感上会更丰富，至少能同时出现轻入口、审美/人物/动手等不同内容模式。
- runtime push 和 OpenClaw `get_next_probe` 的 probe 选择口径保持一致，不会一边变丰富、一边继续塌缩。

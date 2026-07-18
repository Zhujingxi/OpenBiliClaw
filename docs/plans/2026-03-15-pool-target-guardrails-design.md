# Pool Target Guardrails Design

当前 `scheduler.pool_target_count` 没有上限，运行时补货又会直接把 `pool_target_count - current_pool_count` 作为 discover 请求量。一旦把目标值配得过大，单轮 refresh 就会突然拉很重，代价和默认配置完全不是一个量级。

这次只加最小护栏，不改现有默认行为：

1. 保持默认 `pool_target_count = 150`
2. 配置校验新增范围限制：`pool_target_count` 只能在 `1..300`
3. 运行时单轮 discover 请求量新增硬上限 `60`
4. 文档和测试同步更新，明确“目标池子容量”和“单轮补货上限”是两个不同概念

这样可以同时解决两个问题：

- 防止把池子目标误配到明显失控的量级
- 即使目标值靠近上限，单轮补货也不会一次性打满全部缺口

权衡上，这比继续放开无限制更安全，也比把默认值进一步压低更稳妥；对现有 `150` 默认值不会造成行为倒退，只会在极端配置时生效。

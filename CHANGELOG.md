# Changelog

## v3.0.0 (2026-06-20)

深度优化版，由 Claude Code (mimo-v2.5-pro) 生成。

### 新增
- **Cooldown 冷却机制** — 同一 chat_id N 秒内不重复发 ack（默认 5s，可配置）
- **语言检测** — 中/英/日/韩 Unicode 范围匹配，ack 语言自动跟随
- **统计计数器** — 内存中追踪 total/success/failed/per-mode/cooldown_skip/filter_skip
- **最小消息长度** — <2 字符直接跳过
- **定期清理** — 每 100 次 hook 自动清理过期冷却记录，防内存泄漏

### 优化
- Smart prompt 重写：删除所有例子，只保留风格约束，防止模板化
- Preview prompt 重写：三层策略（简单→直接答 / 复杂→给方向 / 不确定→初步判断）
- Static 模式内置 15 条多样化中文消息，random.choice 随机选
- 所有 async task 全 try-except 兜底
- 代码注释全部中文

## v2.0.0

- 三种模式：smart / preview / static
- 重写 prompt，不再说"正在思考中"

## v1.0.0

- 初始版本

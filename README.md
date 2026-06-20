# hermes-quick-ack

> ⚡ Hermes Agent 插件 —— 先秒回，再深想

[![License: GPL-3.0](https://img.shields.io/badge/License-GPL%203.0-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Hermes Agent](https://img.shields.io/badge/Hermes%20Agent-Plugin-purple)](https://hermes-agent.nousresearch.com)

## 这是什么？

`quick-ack` 是 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的插件。当用户发来消息时，**先立即回一条确认消息**，然后主模型再慢慢思考、给出完整回答。

简单说：**秒回 ≠ 随便答**。用户不用盯着空白等，Hermes 在后台该想多久想多久。

## 为什么需要它？

### 痛点

当你用大模型（特别是深度推理模型）时，从发送消息到收到回复可能要等 10-60 秒。这段时间用户看到的是——什么都没有。不确定消息有没有收到，不确定是不是卡住了。

### 解法

```
用户发消息 → [0.1s] "收到，让我想想…" → [15s] 完整详细回答
               ↑ 即时反馈                ↑ 深度思考不受影响
```

## 两种模式

| 模式 | 延迟 | 效果 | 适用场景 |
|------|------|------|----------|
| `static` | ~0ms | 发固定的确认语 | 追求零延迟，不关心个性化 |
| `smart` | ~300-800ms | 用快模型生成上下文相关的回复 | 想要自然、多样的体验 |

### static 模式

发送可自定义的固定消息：

```
用户: 帮我写一个 Python 爬虫
Bot:  收到，正在思考中…
Bot:  好的，下面是一个完整的爬虫方案...(详细回答)
```

### smart 模式

调用快速模型（如 Gemini Flash、GPT-4o-mini）生成一句话：

```
用户: 帮我写一个 Python 爬虫
Bot:  爬虫！有意思，让我想想怎么做～
Bot:  好的，下面是一个完整的爬虫方案...(详细回答)
```

## 安装

### 方式一：手动安装

```bash
# 1. 克隆仓库
git clone https://github.com/MrLing1202/hermes-quick-ack.git

# 2. 复制到 Hermes 插件目录
cp -r hermes-quick-ack ~/.hermes/plugins/quick-ack

# 3. 重启 Hermes Gateway
hermes gateway restart
```

### 方式二：直接下载

```bash
mkdir -p ~/.hermes/plugins/quick-ack
curl -sL https://raw.githubusercontent.com/MrLing1202/hermes-quick-ack/main/__init__.py \
  -o ~/.hermes/plugins/quick-ack/__init__.py
curl -sL https://raw.githubusercontent.com/MrLing1202/hermes-quick-ack/main/plugin.yaml \
  -o ~/.hermes/plugins/quick-ack/plugin.yaml
hermes gateway restart
```

## 配置

在 `~/.hermes/config.yaml` 中添加：

```yaml
plugins:
  entries:
    quick-ack:
      enabled: true

      # --- 模式选择 ---
      mode: smart              # 'static' 或 'smart'

      # --- static 模式配置 ---
      ack_message: "收到，正在思考中..."   # 自定义确认消息

      # --- smart 模式配置 ---
      model:
        provider: google       # 快速模型的 provider
        model: gemini-2.0-flash  # 模型名称（要快！）
      max_length: 60           # 确认消息最大字符数

      # --- 过滤规则 ---
      skip_patterns:           # 这些消息不发确认
        - "^/"                 # 斜杠命令
        - "^\\s*$"             # 空消息
```

### 推荐的快速模型

| Provider | Model | 免费？ | 延迟 |
|----------|-------|--------|------|
| Google | gemini-2.0-flash | ✅ 免费 | ~300ms |
| DashScope | qwen-turbo | ✅ 免费额度 | ~400ms |
| DeepSeek | deepseek-chat | 💰 便宜 | ~500ms |
| OpenRouter | meta-llama/llama-3.1-8b-instruct:free | ✅ 免费 | ~300ms |

## 应用场景

### 1. 🤖 客服/聊天机器人
用户发来问题，先回一句"收到，让我帮您看看"，然后慢慢处理。用户体验大幅提升。

### 2. 💻 编程助手
用户发来复杂编程问题，先回"好问题！让我分析一下…"，然后深度推理 30 秒给出完整方案。

### 3. 📝 长文生成
写文章、翻译、总结等耗时任务，先回"好的，正在为您处理…"，用户知道系统在工作。

### 4. 🔍 深度研究
需要搜索、分析、综合多源信息的任务，先回"让我深入研究一下…"，避免用户等待焦虑。

### 5. 🌐 多平台部署
Telegram、Discord、微信等聊天平台，用户期望即时响应。quick-ack 确保 bot 不会"已读不回"。

## 工作原理

```
┌─────────────┐     ┌──────────────────┐     ┌───────────────┐
│  用户消息    │────▶│ pre_gateway_     │────▶│ adapter.send()│
│             │     │ dispatch hook    │     │ (即时 ack)    │
└─────────────┘     └──────────────────┘     └───────────────┘
                           │
                           ▼
                    ┌──────────────────┐     ┌───────────────┐
                    │ return allow     │────▶│ 主模型深度思考 │
                    │                  │     │ 完整回答      │
                    └──────────────────┘     └───────────────┘
```

1. 用户消息到达 Gateway
2. `pre_gateway_dispatch` hook 拦截
3. 通过 `asyncio.create_task()` 异步发送 ack（不阻塞）
4. 返回 `allow`，正常流程继续
5. 主模型完成思考，发送完整回答

**关键：ack 发送是完全异步的，不会延迟主模型的任何处理。**

## 免责声明

- 本插件不会修改用户的原始消息
- 本插件不会影响主模型的输出内容和质量
- smart 模式会额外消耗少量 token（约 100-200 tokens/次）
- 插件不收集任何数据，不联网（除了 smart 模式调用 LLM）

## 开发

```bash
# 克隆
git clone https://github.com/MrLing1202/hermes-quick-ack.git
cd hermes-quick-ack

# 安装到本地 Hermes
ln -sf $(pwd) ~/.hermes/plugins/quick-ack

# 开启调试日志
export HERMES_PLUGINS_DEBUG=1
hermes gateway run
```

## License

[GPL-3.0](https://www.gnu.org/licenses/gpl-3.0) — 自由使用、修改和分发，但修改后的版本必须同样开源。

## 贡献

欢迎 PR！请确保：
- 不破坏 Hermes 的 prompt caching
- 不引入新的环境变量（配置走 config.yaml）
- 异步代码要健壮，不能阻塞主流程

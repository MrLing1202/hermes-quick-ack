# hermes-quick-ack

> ⚡ Hermes Agent 插件 — 先秒回一句"像人说的话"，再深度思考

[![License: GPL-3.0](https://img.shields.io/badge/License-GPL%203.0-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Hermes Agent](https://img.shields.io/badge/Hermes%20Agent-Plugin-purple)](https://hermes-agent.nousresearch.com)

## 解决什么问题？

大模型深度推理要 10-60 秒。这段时间用户看到的是——一片空白。

`quick-ack` 的做法：**先用快模型回一句有信息量的话，再让主模型慢慢想。**

不是"正在思考中…"，而是：

```
用户: 帮我写个爬虫
Bot:  爬虫啊，抓哪个网站的？我先想个方案
Bot:  好的，下面是一个完整的爬虫方案，使用 requests + BeautifulSoup...
```

```
用户: 这段代码为什么报错
Bot:  报错了？让我看看什么情况
Bot:  问题找到了，你在第 12 行把 list 当成 dict 用了...
```

```
用户: 解释一下量子计算
Bot:  量子计算！这个话题有意思
Bot:  量子计算是利用量子力学原理进行信息处理的计算方式...
```

## 三种模式

| 模式 | 延迟 | 效果 | 适合场景 |
|------|------|------|----------|
| `smart` | ~300ms | 根据消息内容回一句自然的话 | **默认推荐** |
| `preview` | ~500ms | 直接给一个简短回答预览 | 想要即时价值感 |
| `static` | ~0ms | 固定话术 | 只想要个提示 |

### smart 模式（推荐）

快模型理解用户消息后，生成一句自然的回应——像朋友看到消息后的第一反应。

不是确认收到，是展示理解：
- ✅ "爬虫啊，抓哪个网站的数据？"
- ✅ "报错了？让我看看什么情况"
- ❌ "收到，正在为您处理"
- ❌ "好的，稍等一下"

### preview 模式

快模型直接给出 1-2 句简短回答，主模型随后给出完整版：
```
用户: Python怎么读取Excel
Bot:  用pandas库，一行搞定：pd.read_excel('文件.xlsx')
Bot:  详细用法如下，包括读取指定sheet、设置列名、处理大文件...
```

### static 模式

固定消息，零延迟：
```yaml
ack_message: "⏳"
```

## 安装

```bash
git clone https://github.com/MrLing1202/hermes-quick-ack.git
cp -r hermes-quick-ack ~/.hermes/plugins/quick-ack
hermes gateway restart
```

## 配置

```yaml
plugins:
  entries:
    quick-ack:
      enabled: true
      mode: smart              # smart / preview / static

      # smart & preview 模式
      model:
        provider: google       # 快模型 provider
        model: gemini-2.0-flash  # 要快！
      max_length: 80

      # static 模式
      ack_message: "⏳"

      # 过滤（这些消息不回 ack）
      skip_patterns:
        - "^/"                 # 斜杠命令
        - "^\\s*$"             # 空消息
```

### 推荐快模型

| Provider | Model | 免费 | 延迟 |
|----------|-------|------|------|
| Google | gemini-2.0-flash | ✅ | ~300ms |
| DashScope | qwen-turbo | ✅ 额度 | ~400ms |
| DeepSeek | deepseek-chat | 💰 | ~500ms |
| OpenRouter | llama-3.1-8b-instruct:free | ✅ | ~300ms |

## 应用场景

**客服机器人** — 用户发来问题，先回一句"让我看看"而不是沉默。体验天差地别。

**编程助手** — 复杂 debug 需要时间，先回一句"我看到问题了"，用户安心等待。

**长文生成** — 写文章、翻译、总结，先回一句展示理解，用户知道系统在认真工作。

**多平台部署** — Telegram/微信/Discord 用户期望即时响应，quick-ack 让 bot 不"已读不回"。

## 工作原理

```
用户消息 ──▶ pre_gateway_dispatch hook
                │
                ├── asyncio.create_task() ──▶ 快模型生成 ──▶ adapter.send() ──▶ 用户看到秒回
                │
                └── return {"action": "allow"}
                         │
                         ▼
                    主模型深度思考 ──▶ 完整回答 ──▶ 用户看到详细回复
```

- ack 发送是**完全异步**的，不阻塞主模型
- 不修改用户消息，不影响主模型输出
- 不破坏 prompt caching

## License

[GPL-3.0](https://www.gnu.org/licenses/gpl-3.0)

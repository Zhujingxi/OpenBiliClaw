# YouTube Content Provider

目标包 `content/providers/youtube/` 当前提供匿名 `search`、公开 `trending` feed、单视频 fetch 与 channel creator feed。`HttpxYouTubeTransport` 通过 scoped `HttpClientFactory` 调用匿名 InnerTube search/browse/player endpoint，在 HTTP 边界解析 renderer、continuation、canonical 11 字符 video ID、published timestamp、duration、view count 与 channel identity，输出 strict `YouTubePage`；transport/network/status/schema 错误均转换为不含 response body 的 typed integration error。

`takeout.py` 保留并收紧旧 Google Takeout 核心能力：读取 extracted directory 或 zip 内默认 HTML/JSON watch history、subscriptions CSV 和 liked videos CSV，输出 typed、bounded observation proposals，不再返回 legacy raw event dict。缺失文件允许 partial import，schema 错误产生安全 warning。

当前未接入 production Composition；旧 `youtube/` 与 runtime producer 在 caller cutover 前保留。浏览器会话、写操作和下载媒体不在 manifest。

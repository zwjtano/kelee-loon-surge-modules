# Loon 插件批量转换 Surge 模块

本仓库提供 `scripts/convert_kelee_loon_to_surge.py`，用于读取 `https://hub.kelee.one/list.json` 中的 Loon 插件列表，并将 `.lpx` 转换为 Surge `.sgmodule`。

## 基本用法

```bash
python scripts/convert_kelee_loon_to_surge.py --out-dir generated-surge-modules
```

脚本会生成：

- `generated-surge-modules/*.sgmodule`
- `generated-surge-modules/README.md`
- `generated-surge-modules/conversion-report.json`

## 使用本地 Loon 插件源

如果远程 `.lpx` 源地址无法直接下载，可以先把 `.lpx` 文件放进一个目录，文件名保持和列表 URL 一致，再运行：

```bash
python scripts/convert_kelee_loon_to_surge.py --source-dir downloaded-lpx --out-dir generated-surge-modules
```

例如列表中的 `https://kelee.one/Tool/Loon/Lpx/Block_HTTPDNS.lpx`，本地文件应为：

```text
downloaded-lpx/Block_HTTPDNS.lpx
```

## 远程下载说明

`https://kelee.one/Tool/Loon/Lpx/*.lpx` 会按 User-Agent 限制访问。脚本默认使用 Loon 风格 User-Agent：

```text
Loon/3.4.0 CFNetwork/1496.0.7 Darwin/23.5.0
```

不要把插件地址改成 `https://hub.kelee.one/Tool/Loon/Lpx/*.lpx`；该路径返回的是插件中心 HTML，不是插件本体。脚本会校验下载内容，避免生成错误模块。

转换器会自动处理常见段落：

- `[Rule]`
- `[Rewrite]` -> `[URL Rewrite]`
- `[Script]`
- `[MITM]`

其中 Loon 脚本行会转换为 Surge 模块格式，例如：

```text
http-response ^https:\/\/example\.com script-path=https://example.com/a.js, requires-body=true, tag=demo
```

会转换为：

```text
demo = type=http-response, pattern=^https:\/\/example\.com, script-path=https://example.com/a.js, requires-body=true
```

复杂插件仍建议查看 `conversion-report.json` 中的 `needs-review` 项并人工抽查。

## 自动同步

`.github/workflows/update-kelee-loon-to-surge.yml` 会定时运行转换脚本，并更新 `generated-surge-modules/` 目录。

最近一次本地转换结果：

```text
Converted: 232, needs review: 0, failed: 0
```

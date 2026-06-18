#!/usr/bin/env python3
"""Convert Loon .lpx plugins from hub.kelee.one into Surge modules.

The converter is intentionally conservative: Loon and Surge overlap a lot, but
some options are app-specific. It converts the common sections and writes a
report for entries that could not be downloaded or lines that need review.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


LIST_URL = "https://hub.kelee.one/list.json"
RAW_MODULE_BASE_URL = "https://raw.githubusercontent.com/zwjtano/kelee-loon-surge-modules/master/modules"
SURGE_INSTALL_BRIDGE_URL = "https://link.lxya.de/surge/install-module"
SCRIPT_HUB_REWRITE_PARSER = "https://raw.githubusercontent.com/Script-Hub-Org/Script-Hub/main/Rewrite-Parser.js"
USER_AGENT = "Loon/3.4.0 CFNetwork/1496.0.7 Darwin/23.5.0"
SECTION_MAP = {
    "rewrite": "URL Rewrite",
    "url rewrite": "URL Rewrite",
    "rule": "Rule",
    "script": "Script",
    "mitm": "MITM",
    "host": "Host",
}
SCRIPT_RE = re.compile(r"^(http-request|http-response|cron|generic)\s+(.+)$")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
SURGE_INTERNAL_POLICIES = {"DIRECT", "REJECT", "REJECT-TINYGIF"}


@dataclass
class ConvertResult:
    text: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class PluginItem:
    name: str
    url: str
    plugin_url: str
    icon_url: str = ""


def fetch_text(url: str, timeout: int) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/plain,application/json,*/*",
            "Referer": "https://hub.kelee.one/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8-sig")


def load_plugin_list(list_url: str, timeout: int) -> list[PluginItem]:
    data = json.loads(fetch_text(list_url, timeout))
    items: list[PluginItem] = []
    for entry in data.get("lists", []):
        import_url = entry.get("url", "")
        parsed = urllib.parse.urlparse(import_url)
        query = urllib.parse.parse_qs(parsed.query)
        plugin_url = query.get("plugin", [import_url])[0]
        if plugin_url:
            items.append(
                PluginItem(
                    name=entry.get("name") or Path(plugin_url).stem,
                    url=import_url,
                    plugin_url=plugin_url,
                    icon_url=entry.get("icon") or "",
                )
            )
    return items


def safe_stem(plugin_url: str, fallback_name: str) -> str:
    path_name = Path(urllib.parse.urlparse(plugin_url).path).name
    stem = Path(path_name).stem or fallback_name
    stem = SAFE_NAME_RE.sub("_", stem).strip("._-")
    return stem or "plugin"


def parse_csv_options(options: str) -> list[tuple[str, str | None]]:
    options = options.strip()
    if options.startswith(","):
        options = options[1:].strip()
    if not options:
        return []

    parsed: list[tuple[str, str | None]] = []
    reader = csv.reader([options], skipinitialspace=True)
    for token in next(reader, []):
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            key, value = token.split("=", 1)
            parsed.append((key.strip(), value.strip()))
        else:
            parsed.append((token, None))
    return parsed


def format_options(options: Iterable[tuple[str, str | None]]) -> str:
    rendered = []
    for key, value in options:
        if value is None:
            rendered.append(key)
        else:
            rendered.append(f"{key}={value}")
    return ", ".join(rendered)


def unquote_argument_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def format_surge_argument_value(value: str) -> str:
    value = unquote_argument_value(value)
    if re.fullmatch(r"(true|false|null|-?\d+(?:\.\d+)?)", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def convert_argument_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, raw_options = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    options = parse_csv_options(raw_options)
    if len(options) < 2:
        return None
    return key, unquote_argument_value(options[1][0])


def replace_argument_placeholders(line: str) -> str:
    return re.sub(r"\{([A-Za-z][A-Za-z0-9_]*)\}", r"{{{\1}}}", line)


def convert_rule_line(line: str) -> tuple[str | None, str | None]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return line, None
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 2:
        return line, None
    policy = parts[-1].split()[0]
    if policy in SURGE_INTERNAL_POLICIES:
        return line, None
    return None, f"Dropped rule with non-built-in policy {policy}"


def parse_json_path(path: str) -> list[str | int]:
    path = path.strip()
    output: list[str | int] = []
    for match in re.finditer(r"\.?([^\.\[\]]+)|\[(['\"])(.*?)\2\]|\[(\d+)\]", path):
        if match.group(1) is not None:
            output.append(match.group(1))
        elif match.group(3) is not None:
            output.append(match.group(3))
        elif match.group(4) is not None:
            output.append(int(match.group(4)))
    return output


def inline_jq_value(value: str) -> tuple[str | None, str | None]:
    match = re.search(r'jq-path="([^"]+)"', value)
    if not match:
        return value, None
    jq_path = match.group(1)
    if not jq_path.startswith(("http://", "https://")):
        return None, f"Dropped unsupported local jq-path {jq_path}"
    jq_text = fetch_text(jq_path, 30)
    jq_text = re.sub(r"^\s*#.*$", "", jq_text, flags=re.MULTILINE)
    jq_text = re.sub(r"\r?\n", " ", jq_text).strip()
    return repr(jq_text), None


def option_value(options: str, key: str) -> str | None:
    match = re.search(rf"(?:^|\s){re.escape(key)}=", options)
    if not match:
        return None
    start = match.end()
    next_match = re.search(
        r"\s(?:data-type|status-code|data|data-path|mock-data-is-base64|header)=",
        options[start:],
    )
    end = start + next_match.start() if next_match else len(options)
    value = options[start:end].strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return value


def convert_mock_response(pattern: str, rest: str) -> str:
    data_type = option_value(rest, "data-type") or "text"
    status_code = option_value(rest, "status-code")
    data = option_value(rest, "data")
    data_path = option_value(rest, "data-path")
    is_base64 = option_value(rest, "mock-data-is-base64") == "true"
    if is_base64:
        data_type = "base64"
    pieces = [pattern, f"data-type={data_type}"]
    if data is not None:
        pieces.append(f'data="{data}"')
    elif data_path is not None:
        pieces.append(f'data="{data_path}"')
    elif data_type == "text":
        pieces.append('data=""')
    if status_code:
        pieces.append(f"status-code={status_code}")
    pieces.append(f'header="Content-Type:{"application/json" if data_type == "json" else "text/plain"}"')
    return " ".join(pieces)


def convert_url_rewrite_line(line: str) -> tuple[str, str, str | None]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return "URL Rewrite", line, None
    parts = stripped.split(maxsplit=2)
    if len(parts) < 2:
        return "URL Rewrite", line, None
    pattern, action = parts[0], parts[1]
    rest = parts[2] if len(parts) > 2 else ""
    if pattern in {"http-request", "http-response"} and rest:
        nested_parts = rest.split(maxsplit=1)
        if len(nested_parts) == 2:
            nested_action, nested_value = nested_parts
            if nested_action == "response-body-json-jq":
                value, warning = inline_jq_value(nested_value)
                if value is None:
                    return "drop", "", warning
                return "Body Rewrite", f"{pattern}-jq {action} {value}", warning
    if action == "response-body-json-jq":
        value, warning = inline_jq_value(rest)
        if value is None:
            return "drop", "", warning
        return "Body Rewrite", f"http-response-jq {pattern} {value}", warning
    if action == "response-body-json-del":
        paths = [parse_json_path(item) for item in rest.split() if item.strip()]
        if not paths:
            return "drop", "", "Dropped empty JSON delete rewrite"
        return "Body Rewrite", f"http-response-jq {pattern} 'delpaths({json.dumps(paths, ensure_ascii=False)})'", None
    if action == "mock-response-body":
        return "Map Local", convert_mock_response(pattern, rest), None
    if action == "reject-dict":
        return "Map Local", f'{pattern} data-type=text data="{{}}" status-code=200 header="Content-Type:application/json"', None
    if action == "response-header-add":
        header_parts = rest.split(maxsplit=1)
        if len(header_parts) == 2:
            return "Header Rewrite", f"http-response {pattern} header-add {header_parts[0]!r} {header_parts[1]!r}", None
        return "Header Rewrite", f"http-response {pattern} header-add {rest}", None
    return "URL Rewrite", line, None


def remove_empty_sections(lines: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.match(r"^\[[^\]]+\]\s*$", line):
            section: list[str] = [line]
            index += 1
            while index < len(lines) and not re.match(r"^\[[^\]]+\]\s*$", lines[index]):
                section.append(lines[index])
                index += 1
            if any(item.strip() and not item.lstrip().startswith("#") for item in section[1:]):
                result.extend(section)
            continue
        result.append(line)
        index += 1
    return result


def script_tag(options: list[tuple[str, str | None]], fallback: str) -> tuple[str, list[tuple[str, str | None]]]:
    tag = None
    kept: list[tuple[str, str | None]] = []
    for key, value in options:
        if key == "tag" and value:
            tag = value
        else:
            kept.append((key, value))
    return tag or fallback, kept


def convert_script_line(line: str, fallback_tag: str) -> tuple[str, str | None]:
    stripped = line.strip()
    match = SCRIPT_RE.match(stripped)
    if not match:
        return line, "Unconverted script line"

    script_type, payload = match.groups()
    if "script-path=" not in payload:
        return line, "Script line does not contain script-path"

    before_script, after_script = payload.split("script-path=", 1)
    if "," in after_script:
        script_path, raw_options = after_script.split(",", 1)
    else:
        script_path, raw_options = after_script, ""

    pre_options = ""
    pattern = ""
    if script_type in {"http-request", "http-response"}:
        left_parts = before_script.strip().split(None, 1)
        pattern = left_parts[0] if left_parts else ""
        pre_options = left_parts[1] if len(left_parts) > 1 else ""
    elif script_type == "cron":
        left_parts = before_script.strip().split(None, 1)
        cronexp = left_parts[0] if left_parts else ""
        pre_options = left_parts[1] if len(left_parts) > 1 else ""
        pattern = ""
    else:
        pre_options = before_script.strip()

    options = parse_csv_options(",".join(part for part in (pre_options, raw_options) if part.strip()))
    tag, options = script_tag(options, fallback_tag)
    surge_options = [("type", script_type)]
    if script_type in {"http-request", "http-response"}:
        surge_options.append(("pattern", pattern.strip()))
    elif script_type == "cron":
        surge_options.append(("cronexp", cronexp.strip()))
    surge_options.extend([("script-path", script_path.strip()), *options])
    return f"{tag} = {format_options(surge_options)}", None


def convert_header(line: str, source_url: str) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    if line.startswith("#!loon_version="):
        return None, warnings
    if line.startswith("#!name="):
        name = line.split("=", 1)[1].strip()
        return f"#!name={name} - Surge", warnings
    if line.startswith("#!desc="):
        desc = line.split("=", 1)[1].strip()
        return f"#!desc={desc}；由 Loon 插件自动转换为 Surge 模块，建议导入前抽查。", warnings
    if line.startswith("#!homepage="):
        return line, warnings
    if line.startswith("#!"):
        return line, warnings
    return line, warnings


def convert_lpx_to_surge(text: str, source_url: str, fallback_tag: str) -> ConvertResult:
    warnings: list[str] = []
    output: list[str] = []
    argument_items: list[tuple[str, str]] = []
    extra_sections: dict[str, list[str]] = {"Body Rewrite": [], "Map Local": [], "Header Rewrite": []}
    current_section: str | None = None
    inserted_source = False

    for line_no, original_line in enumerate(text.splitlines(), start=1):
        line = original_line.rstrip()
        section_match = re.match(r"^\[([^\]]+)\]\s*$", line)
        if section_match:
            section = section_match.group(1).strip()
            current_section = SECTION_MAP.get(section.lower(), section)
            if section.lower() == "argument":
                current_section = "Argument"
                continue
            output.append(f"[{current_section}]")
            continue

        if current_section == "Argument":
            argument = convert_argument_line(line)
            if argument:
                argument_items.append(argument)
            continue

        if line.startswith("#!"):
            converted, header_warnings = convert_header(line, source_url)
            warnings.extend(f"line {line_no}: {warning}" for warning in header_warnings)
            if converted is not None:
                output.append(converted)
            if not inserted_source and line.startswith("#!desc="):
                output.append(f"#!homepage={source_url}")
                output.append("#!system=ios")
                inserted_source = True
            continue

        if current_section == "Script" and line.strip() and not line.lstrip().startswith("#"):
            line = replace_argument_placeholders(line)
            converted, warning = convert_script_line(line, fallback_tag)
            output.append(converted)
            if warning:
                warnings.append(f"line {line_no}: {warning}: {line}")
            continue

        if current_section == "MITM":
            stripped = line.strip()
            if stripped.startswith("hostname="):
                output.append("hostname = %APPEND% " + stripped.split("=", 1)[1].strip())
                continue
            if stripped.startswith("hostname =") and "%APPEND%" not in stripped:
                output.append("hostname = %APPEND% " + stripped.split("=", 1)[1].strip())
            continue

        if current_section == "Rule":
            converted, warning = convert_rule_line(line)
            if converted is not None:
                output.append(converted)
            if warning:
                warnings.append(f"line {line_no}: {warning}: {line}")
            continue

        if current_section == "URL Rewrite":
            target_section, converted, warning = convert_url_rewrite_line(line)
            if warning:
                warnings.append(f"line {line_no}: {warning}: {line}")
            if target_section == "URL Rewrite":
                output.append(converted)
            elif target_section != "drop":
                extra_sections[target_section].append(converted)
            continue

        line = replace_argument_placeholders(line)
        output.append(line)

    if not inserted_source:
        output.insert(0, "#!system=ios")
        output.insert(0, f"#!homepage={source_url}")
    if argument_items:
        arguments = ",".join(f"{key}:{format_surge_argument_value(value)}" for key, value in argument_items)
        insert_at = 0
        while insert_at < len(output) and output[insert_at].startswith("#!"):
            insert_at += 1
        output.insert(insert_at, f"#!arguments={arguments}")
    for section, section_lines in extra_sections.items():
        if section_lines:
            output.extend(["", f"[{section}]", *section_lines])
    output = remove_empty_sections(output)

    return ConvertResult(text="\n".join(output).rstrip() + "\n", warnings=warnings)


def read_source(item: PluginItem, source_dir: Path | None, timeout: int) -> str:
    file_name = Path(urllib.parse.urlparse(item.plugin_url).path).name
    if source_dir:
        local_path = source_dir / file_name
        if local_path.exists():
            text = local_path.read_text(encoding="utf-8-sig")
            validate_lpx_source(text, str(local_path))
            return text
    text = fetch_text(item.plugin_url, timeout)
    validate_lpx_source(text, item.plugin_url)
    return text


def validate_lpx_source(text: str, source: str) -> None:
    head = text[:2048].lstrip()
    if head.startswith("<!DOCTYPE") or head.startswith("<html"):
        raise ValueError(f"Downloaded HTML instead of a Loon plugin: {source}")
    if "#!name=" not in head and "[Rule]" not in text and "[Script]" not in text:
        raise ValueError(f"Downloaded content does not look like a Loon plugin: {source}")


def markdown_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def icon_cell(name: str, icon_url: str) -> str:
    if not icon_url.startswith(("http://", "https://")):
        return ""
    alt = markdown_cell(name).replace('"', "&quot;")
    return f'<img src="{icon_url}" alt="{alt}" width="28" height="28">'


def install_url_for_path(path: str) -> tuple[str, str]:
    raw_url = f"{RAW_MODULE_BASE_URL}/{path}"
    install_url = f"{SURGE_INSTALL_BRIDGE_URL}?url=" + urllib.parse.quote(raw_url, safe="")
    return raw_url, install_url


def write_index(out_dir: Path, converted: list[dict[str, str]]) -> None:
    lines = [
        "# 已转换的 Surge 模块",
        "",
        "| 图标 | 模块 | 原始链接 | 一键导入 |",
        "| --- | --- | --- | --- |",
    ]
    for item in converted:
        raw_url, install_url = install_url_for_path(item["path"])
        lines.append(
            f"| {icon_cell(item['name'], item.get('icon_url', ''))} | "
            f"{markdown_cell(item['name'])} | [原始文件]({raw_url}) | [导入 Surge]({install_url}) |"
        )
    lines.append("")
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def write_root_readme(readme_path: Path, converted: list[dict[str, str]], report: list[dict[str, object]]) -> None:
    ok = sum(1 for item in report if item["status"] == "ok")
    needs_review = sum(1 for item in report if item["status"] == "needs-review")
    failed = sum(1 for item in report if item["status"] == "failed")
    lines = [
        "# Kelee Loon 插件转 Surge 模块",
        "",
        "本仓库收录由以下 Kelee Loon 插件列表转换而来的 Surge 模块：",
        "",
        "```text",
        "https://hub.kelee.one/",
        "```",
        "",
        "## 一键导入",
        "",
        "请在已安装 Surge 的设备上打开导入链接。导入链接使用 HTTPS 跳转桥接，方便从 GitHub README 页面直接唤起 Surge。",
        "",
        f"转换成功：{ok}，需要复查：{needs_review}，失败：{failed}",
        "",
        "## 转换方式",
        "",
        "本仓库默认通过 [Script-Hub](https://github.com/Script-Hub-Org/Script-Hub) 的 `Rewrite-Parser.js` 将 Loon 插件转换为 Surge 模块。",
        "",
        "转换参数等同于：",
        "",
        "```text",
        "type=loon-plugin&target=surge-module&del=true&jqEnabled=true",
        "```",
        "",
        "| 图标 | 模块 | 原始链接 | 一键导入 |",
        "| --- | --- | --- | --- |",
    ]
    for item in converted:
        raw_url, install_url = install_url_for_path(item["path"])
        lines.append(
            f"| {icon_cell(item['name'], item.get('icon_url', ''))} | "
            f"{markdown_cell(item['name'])} | [原始文件]({raw_url}) | [导入 Surge]({install_url}) |"
        )
    lines.extend(
        [
            "",
            "## 自动同步",
            "",
            "`.github/workflows/update-kelee-loon-to-surge.yml` 会定期读取上游 Kelee 列表，并通过 Script-Hub 重新生成 `modules/`、`modules/README.md` 和本 README。",
            "",
            "转换脚本：",
            "",
            "```text",
            "python scripts/convert_kelee_loon_to_surge.py --converter scripthub --out-dir modules --root-readme README.md",
            "```",
            "",
            "Script-Hub 调用脚本：",
            "",
            "```text",
            "scripts/scripthub_convert.js",
            "```",
            "",
            "源插件版权归原作者所有。本仓库仅保留自动转换后的 Surge 格式镜像。",
        ]
    )
    readme_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def convert_with_scripthub(items: list[PluginItem], out_dir: Path, timeout: int) -> list[dict[str, object]]:
    helper = Path(__file__).with_name("scripthub_convert.js")
    payload = []
    for item in items:
        stem = safe_stem(item.plugin_url, item.name)
        payload.append(
            {
                "name": item.name,
                "plugin_url": item.plugin_url,
                "output_name": f"{stem}.sgmodule",
            }
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        items_path = Path(tmp_dir) / "items.json"
        report_path = Path(tmp_dir) / "report.json"
        parser_path = Path(tmp_dir) / "Rewrite-Parser.js"
        items_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        parser_path.write_text(fetch_text(SCRIPT_HUB_REWRITE_PARSER, timeout), encoding="utf-8")
        subprocess.run(
            ["node", str(helper), str(items_path), str(out_dir), str(report_path), str(parser_path)],
            check=True,
            timeout=max(timeout * max(len(items), 1), 300),
        )
        return json.loads(report_path.read_text(encoding="utf-8"))


def convert_with_native_converter(
    items: list[PluginItem],
    out_dir: Path,
    source_dir: Path | None,
    timeout: int,
) -> list[dict[str, object]]:
    report: list[dict[str, object]] = []
    for item in items:
        stem = safe_stem(item.plugin_url, item.name)
        output_path = out_dir / f"{stem}.sgmodule"
        entry: dict[str, object] = {
            "name": item.name,
            "source": item.plugin_url,
            "output": str(output_path),
            "status": "ok",
            "warnings": [],
        }
        try:
            source = read_source(item, source_dir, timeout)
            result = convert_lpx_to_surge(source, item.plugin_url, stem)
            output_path.write_text(result.text, encoding="utf-8")
            entry["warnings"] = result.warnings
            if result.warnings:
                entry["status"] = "needs-review"
        except Exception as exc:  # noqa: BLE001 - report every plugin independently.
            entry["status"] = "failed"
            entry["error"] = str(exc)
        report.append(entry)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-url", default=LIST_URL)
    parser.add_argument("--out-dir", default="generated-surge-modules")
    parser.add_argument("--source-dir", help="Optional directory containing downloaded .lpx files")
    parser.add_argument("--root-readme", help="Optional root README path to regenerate with the full module list")
    parser.add_argument("--converter", choices=["scripthub", "native"], default="scripthub")
    parser.add_argument("--limit", type=int, help="Convert only the first N plugins")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_dir = Path(args.source_dir) if args.source_dir else None

    items = load_plugin_list(args.list_url, args.timeout)
    if args.limit:
        items = items[: args.limit]

    if args.converter == "scripthub":
        report = convert_with_scripthub(items, out_dir, args.timeout)
    else:
        report = convert_with_native_converter(items, out_dir, source_dir, args.timeout)

    items_by_output = {f"{safe_stem(item.plugin_url, item.name)}.sgmodule": item for item in items}
    converted_index: list[dict[str, str]] = []
    for entry in report:
        if entry["status"] == "failed":
            continue
        output_name = Path(str(entry["output"])).name
        item = items_by_output.get(output_name)
        converted_index.append(
            {
                "name": str(entry["name"]),
                "path": output_name,
                "icon_url": item.icon_url if item else "",
            }
        )

    (out_dir / "conversion-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_index(out_dir, converted_index)
    if args.root_readme:
        write_root_readme(Path(args.root_readme), converted_index, report)

    ok = sum(1 for item in report if item["status"] == "ok")
    needs_review = sum(1 for item in report if item["status"] == "needs-review")
    failed = sum(1 for item in report if item["status"] == "failed")
    print(f"Converted: {ok}, needs review: {needs_review}, failed: {failed}")
    print(f"Output: {out_dir}")
    return 1 if failed and not converted_index else 0


if __name__ == "__main__":
    raise SystemExit(main())

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
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


LIST_URL = "https://hub.kelee.one/list.json"
RAW_MODULE_BASE_URL = "https://raw.githubusercontent.com/zwjtano/kelee-loon-surge-modules/master/modules"
SURGE_INSTALL_BRIDGE_URL = "https://link.lxya.de/surge/install-module"
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


@dataclass
class ConvertResult:
    text: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class PluginItem:
    name: str
    url: str
    plugin_url: str


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
    current_section: str | None = None
    inserted_source = False

    for line_no, original_line in enumerate(text.splitlines(), start=1):
        line = original_line.rstrip()
        section_match = re.match(r"^\[([^\]]+)\]\s*$", line)
        if section_match:
            section = section_match.group(1).strip()
            current_section = SECTION_MAP.get(section.lower(), section)
            output.append(f"[{current_section}]")
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

        output.append(line)

    if not inserted_source:
        output.insert(0, "#!system=ios")
        output.insert(0, f"#!homepage={source_url}")

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


def write_index(out_dir: Path, converted: list[dict[str, str]]) -> None:
    lines = ["# Converted Surge Modules", ""]
    for item in converted:
        raw_url = f"{RAW_MODULE_BASE_URL}/{item['path']}"
        install_url = f"{SURGE_INSTALL_BRIDGE_URL}?url=" + urllib.parse.quote(raw_url, safe="")
        lines.append(f"- [{item['name']}]({raw_url}) | [一键导入 Surge]({install_url})")
    lines.append("")
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-url", default=LIST_URL)
    parser.add_argument("--out-dir", default="generated-surge-modules")
    parser.add_argument("--source-dir", help="Optional directory containing downloaded .lpx files")
    parser.add_argument("--limit", type=int, help="Convert only the first N plugins")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_dir = Path(args.source_dir) if args.source_dir else None
    report: list[dict[str, object]] = []
    converted_index: list[dict[str, str]] = []

    items = load_plugin_list(args.list_url, args.timeout)
    if args.limit:
        items = items[: args.limit]

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
            source = read_source(item, source_dir, args.timeout)
            result = convert_lpx_to_surge(source, item.plugin_url, stem)
            output_path.write_text(result.text, encoding="utf-8")
            entry["warnings"] = result.warnings
            converted_index.append({"name": item.name, "path": output_path.name})
            if result.warnings:
                entry["status"] = "needs-review"
        except Exception as exc:  # noqa: BLE001 - report every plugin independently.
            entry["status"] = "failed"
            entry["error"] = str(exc)
        report.append(entry)

    (out_dir / "conversion-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_index(out_dir, converted_index)

    ok = sum(1 for item in report if item["status"] == "ok")
    needs_review = sum(1 for item in report if item["status"] == "needs-review")
    failed = sum(1 for item in report if item["status"] == "failed")
    print(f"Converted: {ok}, needs review: {needs_review}, failed: {failed}")
    print(f"Output: {out_dir}")
    return 1 if failed and not converted_index else 0


if __name__ == "__main__":
    raise SystemExit(main())

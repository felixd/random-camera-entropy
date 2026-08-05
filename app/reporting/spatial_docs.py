#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe read-only documentation browser for the persistent control server."""
from __future__ import annotations

import datetime as dt
import html
import re
from pathlib import Path

from flask import abort, render_template_string

IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", ".venv-agent", "__pycache__",
    "data", "pki", "certs", "keys", "secrets", "node_modules",
}
ALLOWED_SUFFIXES = {".md", ".markdown", ".txt"}
DOCUMENT_PREFIXES = (
    "README", "INSTRUCTION", "DOCUMENT", "PARAMETER", "SECURITY",
    "SOURCE_MODEL", "DISTRIBUTED_ARCHITECTURE", "CHANGELOG",
    "BUILD_VERIFICATION",
)

INDEX_TEMPLATE = r'''<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dokumentacja — Camera Entropy</title><style>
:root{color-scheme:dark;--bg:#0d1117;--p:#161b22;--b:#30363d;--t:#f0f6fc;--m:#8b949e;--a:#58a6ff}
body{margin:0;background:var(--bg);color:var(--t);font-family:system-ui,sans-serif}main{max-width:1200px;margin:auto;padding:24px}.top{display:flex;justify-content:space-between;gap:16px;align-items:center}a{color:var(--a)}table{width:100%;border-collapse:collapse;background:var(--p);border:1px solid var(--b)}th,td{padding:10px;border-bottom:1px solid var(--b);text-align:left}th{color:var(--m)}code{background:#21262d;padding:2px 5px;border-radius:4px}.muted{color:var(--m)}</style></head><body><main><div class="top"><div><h1>Dokumentacja projektu</h1><p class="muted">Pliki Markdown i instrukcje udostępniane tylko do odczytu.</p></div><a href="/">← panel sterowania</a></div><table><thead><tr><th>Plik</th><th>Rozmiar</th><th>Modyfikacja</th></tr></thead><tbody>{% for item in items %}<tr><td><a href="/docs/{{ item.relative }}">{{ item.relative }}</a></td><td>{{ item.size }}</td><td>{{ item.modified }}</td></tr>{% endfor %}</tbody></table></main></body></html>'''

VIEW_TEMPLATE = r'''<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{ relative }} — dokumentacja</title><style>
:root{color-scheme:dark;--bg:#0d1117;--p:#161b22;--b:#30363d;--t:#f0f6fc;--m:#8b949e;--a:#58a6ff}body{margin:0;background:var(--bg);color:var(--t);font-family:system-ui,sans-serif;line-height:1.55}main{max-width:1100px;margin:auto;padding:24px}a{color:var(--a)}article{background:var(--p);border:1px solid var(--b);border-radius:10px;padding:24px;overflow-wrap:anywhere}pre{overflow:auto;background:#0d1117;border:1px solid var(--b);padding:14px;border-radius:8px}code{background:#21262d;padding:2px 5px;border-radius:4px}pre code{background:none;padding:0}blockquote{border-left:4px solid var(--b);margin-left:0;padding-left:14px;color:var(--m)}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%}th,td{border:1px solid var(--b);padding:7px;text-align:left}.meta{color:var(--m)}.nav{display:flex;justify-content:space-between;gap:16px;align-items:center}</style></head><body><main><div class="nav"><div><h1>{{ relative }}</h1><p class="meta">{{ size }} B · {{ modified }}</p></div><div><a href="/docs/">← dokumentacja</a> · <a href="/">panel</a></div></div><article>{{ rendered|safe }}</article></main></body></html>'''


def _is_document(path: Path) -> bool:
    if path.suffix.lower() in ALLOWED_SUFFIXES:
        return True
    upper = path.name.upper()
    return any(upper.startswith(prefix) for prefix in DOCUMENT_PREFIXES)


def discover_documents(root: Path, max_depth: int = 5) -> list[Path]:
    root = root.resolve()
    result: list[Path] = []
    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if len(relative.parts) > max_depth:
            continue
        if any(part.startswith(".") or part in IGNORED_DIRS for part in relative.parts[:-1]):
            continue
        if path.is_file() and _is_document(path):
            result.append(path)
    return sorted(result, key=lambda item: str(item.relative_to(root)).lower())


def _safe_href(value: str) -> str:
    candidate = value.strip()
    lowered = candidate.lower()
    if lowered.startswith(("http://", "https://", "/")):
        return html.escape(candidate, quote=True)
    if ":" not in candidate and not candidate.startswith("//"):
        return html.escape(candidate, quote=True)
    return "#"


def _inline(text: str) -> str:
    placeholders: list[str] = []

    def link(match: re.Match[str]) -> str:
        label = html.escape(match.group(1))
        href = _safe_href(match.group(2))
        placeholders.append(f'<a href="{href}">{label}</a>')
        return f"\x00LINK{len(placeholders)-1}\x00"

    linked = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)
    safe = html.escape(linked)
    safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
    safe = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", safe)
    for index, value in enumerate(placeholders):
        safe = safe.replace(f"\x00LINK{index}\x00", value)
    return safe


def _table_cells(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def render_markdown(source: str) -> str:
    """Render a safe dependency-free Markdown subset.

    Raw HTML is escaped. Supported constructs include headings, fenced code,
    paragraphs, links, unordered/ordered lists, blockquotes and simple tables.
    """
    lines = source.splitlines()
    output: list[str] = []
    paragraph: list[str] = []
    in_code = False
    code_lines: list[str] = []
    list_kind: str | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            output.append("<p>" + _inline(" ".join(part.strip() for part in paragraph)) + "</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            output.append(f"</{list_kind}>")
            list_kind = None

    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            flush_paragraph(); close_list()
            if in_code:
                output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []; in_code = False
            else:
                in_code = True
            index += 1
            continue
        if in_code:
            code_lines.append(line)
            index += 1
            continue

        if index + 1 < len(lines) and "|" in line and _is_table_separator(lines[index + 1]):
            flush_paragraph(); close_list()
            headers = _table_cells(line)
            output.append("<div class=\"table-wrap\"><table><thead><tr>" + "".join(
                f"<th>{_inline(cell)}</th>" for cell in headers
            ) + "</tr></thead><tbody>")
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                cells = _table_cells(lines[index])
                output.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in cells) + "</tr>")
                index += 1
            output.append("</tbody></table></div>")
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_paragraph(); close_list()
            level = len(heading.group(1))
            output.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        bullet = re.match(r"^\s*[-*+]\s+(.*)$", line)
        ordered = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if bullet or ordered:
            flush_paragraph()
            wanted = "ul" if bullet else "ol"
            if list_kind != wanted:
                close_list(); output.append(f"<{wanted}>"); list_kind = wanted
            match = bullet or ordered
            assert match is not None
            output.append("<li>" + _inline(match.group(1)) + "</li>")
            index += 1
            continue
        close_list()

        if line.startswith("> "):
            flush_paragraph(); output.append("<blockquote>" + _inline(line[2:]) + "</blockquote>")
        elif not line.strip():
            flush_paragraph()
        elif re.match(r"^\s*[-=]{3,}\s*$", line):
            flush_paragraph(); output.append("<hr>")
        else:
            paragraph.append(line)
        index += 1

    if in_code:
        output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    flush_paragraph(); close_list()
    return "\n".join(output)

def _safe_document(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        abort(404)
    if not candidate.is_file() or not _is_document(candidate):
        abort(404)
    if any(part.startswith(".") or part in IGNORED_DIRS for part in rel.parts[:-1]):
        abort(404)
    return candidate


def register_documentation_routes(app, root: Path) -> None:
    root = Path(root).resolve()

    @app.get("/docs/")
    def documentation_index():
        items = []
        for path in discover_documents(root):
            stat = path.stat()
            items.append({
                "relative": path.relative_to(root).as_posix(),
                "size": stat.st_size,
                "modified": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
        return render_template_string(INDEX_TEMPLATE, items=items)

    @app.get("/docs/<path:relative>")
    def documentation_view(relative: str):
        path = _safe_document(root, relative)
        stat = path.stat()
        source = path.read_text(encoding="utf-8", errors="replace")
        return render_template_string(
            VIEW_TEMPLATE,
            relative=path.relative_to(root).as_posix(),
            size=stat.st_size,
            modified=dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            rendered=render_markdown(source),
        )

#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from app.reporting.report_ui import chart_div, fmt_rate, html_page, rate_span, rate_unit_selector


def main() -> int:
    assert fmt_rate(8_000, unit="kB/s") == "1.000 kB/s"
    assert fmt_rate(1_000, unit="kbit/s") == "1.000 kbit/s"
    assert fmt_rate(8_000_000, unit="MB/s") == "1.000 MB/s"
    assert fmt_rate(8 * 1024 * 1024, unit="MiB/s") == "1.000 MiB/s"
    assert 'data-rate-bps="8000"' in str(rate_span(8_000))
    selector = rate_unit_selector()
    for unit in ("bit/s", "kbit/s", "kB/s", "MiB/s", "MB/s"):
        assert f'value="{unit}"' in selector
    assert '<option value="kB/s" selected>' in selector

    report = html_page(
        title="rate test",
        navigation=selector,
        body=chart_div("rate", "rate"),
        plot_specs=[{
            "id": "rate", "rateAxis": "y", "rateTitle": "Przepustowość",
            "data": [{"type": "bar", "x": ["a"], "y": [8_000]}],
        }],
        standalone=True,
    )
    assert "/static/vendor/plotly" not in report
    assert len(report.encode("utf-8")) > 4_000_000
    assert "rateUnits" in report and "rateAxis" in report

    node = shutil.which("node")
    if node:
        scripts = re.findall(r'<script(?: [^>]*)?>(.*?)</script>', report, flags=re.S)
        with tempfile.TemporaryDirectory() as temporary:
            for index, script in enumerate(scripts):
                path = Path(temporary) / f"script-{index}.js"
                path.write_text(script, encoding="utf-8")
                subprocess.run([node, "--check", str(path)], check=True, stdout=subprocess.DEVNULL)

    print("report throughput unit self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

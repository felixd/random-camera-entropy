#!/usr/bin/env python3
"""Static and JavaScript regression test for the live worker status header."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app" / "core" / "camera_entropy_server.py"


def extract_functions(source: str) -> str:
    start = source.index("function rate(v)")
    end = source.index("async function fetchJson", start)
    return source[start:end]


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    required = (
        'id="stateSummary"',
        'id="stateDetail"',
        'id="stateProgress"',
        'id="stateProgressBar"',
        "function stateHeaderModel(s)",
        "function renderStateHeader(s)",
        "renderStateHeader(s);",
    )
    missing = [item for item in required if item not in source]
    if missing:
        raise AssertionError(f"missing worker header elements: {missing}")

    functions = extract_functions(source)
    cases = [
        {
            "name": "warmup",
            "status": {
                "state": "WARMING_UP",
                "warmup": {"remaining_seconds": 41.2, "configured_seconds": 60, "source_age_seconds": 18.8},
            },
        },
        {
            "name": "calibration",
            "status": {
                "state": "CALIBRATING",
                "calibration": {"pairs": 116, "target": 128},
            },
        },
        {
            "name": "conditioned-output",
            "status": {
                "state": "RUNNING",
                "conditioner": {
                    "enabled": True,
                    "written_bytes": 50 * 1024 * 1024,
                    "target_bytes": 100 * 1024 * 1024,
                },
                # A non-zero raw writer value must not replace a legitimate
                # zero/partial conditioner value.
                "output_limit": {"written_bytes": 99, "target_bytes": 123},
            },
        },
        {
            "name": "api-error",
            "status": {"state": "API ERROR", "error": "connection refused"},
        },
    ]

    script = f"""
const n=v=>Number.isFinite(Number(v))?new Intl.NumberFormat('pl-PL').format(Number(v)):'n/a';
const elements=Object.fromEntries(['state','stateDetail','stateProgress','stateProgressBar'].map(id=>[id,{{id,textContent:'',className:'',hidden:false,style:{{}},attributes:{{}},setAttribute(name,value){{this.attributes[name]=String(value)}},removeAttribute(name){{delete this.attributes[name]}}}}]));
const byId=id=>elements[id];
{functions}
const cases={json.dumps(cases, ensure_ascii=False)};
const models=Object.fromEntries(cases.map(item=>[item.name,stateHeaderModel(item.status)]));
renderStateHeader(cases.find(item=>item.name==='calibration').status);
console.log(JSON.stringify({{models,rendered:elements}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    payload = json.loads(result.stdout)
    models = payload["models"]

    warmup = models["warmup"]
    assert warmup["detail"] == "pozostało 42s z 1m 0s · wiek źródła 19s", warmup
    assert abs(float(warmup["progress"]) - (18.8 / 60.0)) < 1e-9, warmup

    calibration = models["calibration"]
    assert calibration["detail"] == "para kalibracyjna 116 / 128 · 90,6%", calibration
    assert abs(float(calibration["progress"]) - 0.90625) < 1e-9, calibration

    output = models["conditioned-output"]
    assert output["detail"] == "SHA3 50 MiB / 100 MiB · 50%", output
    assert float(output["progress"]) == 0.5, output

    api_error = models["api-error"]
    assert api_error["detail"] == "connection refused", api_error
    assert api_error["progress"] is None, api_error

    rendered = payload["rendered"]
    assert rendered["state"]["textContent"] == "CALIBRATING", rendered
    assert rendered["state"]["className"] == "warn", rendered
    assert rendered["stateDetail"]["textContent"] == calibration["detail"], rendered
    assert rendered["stateProgress"]["hidden"] is False, rendered
    assert rendered["stateProgress"]["attributes"]["aria-valuenow"] == "90.6", rendered
    assert rendered["stateProgressBar"]["style"]["width"] == "90.6%", rendered

    print("worker state header self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

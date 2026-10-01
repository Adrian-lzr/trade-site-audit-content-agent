"""Generate a dependency license inventory from the checked-in lock files.

The Python license value comes from installed package metadata, while the npm
value comes from the resolved package-lock entries. Missing metadata is kept as
``UNKNOWN`` so a release review can resolve it explicitly.
"""

from __future__ import annotations

import argparse
import json
import re
from importlib import metadata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_LOCK = ROOT / "backend" / "requirements.lock"
NPM_LOCK = ROOT / "apps" / "web" / "package-lock.json"


def _normalise(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _python_lock_packages() -> set[str]:
    pattern = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==")
    return {
        _normalise(match.group(1))
        for line in PYTHON_LOCK.read_text(encoding="utf-8").splitlines()
        if (match := pattern.match(line))
    }


def _license_from_metadata(dist: metadata.Distribution) -> str:
    value = dist.metadata.get("License-Expression") or dist.metadata.get("License")
    if value and value.strip() and value.strip().lower() not in {"unknown", "none"}:
        return value.strip().replace("\n", " ")
    classifiers = [item.split(" :: ", 2)[-1] for item in dist.metadata.get_all("Classifier") or [] if item.startswith("License ::")]
    return "; ".join(classifiers) if classifiers else "UNKNOWN"


def _python_rows() -> list[tuple[str, str, str]]:
    locked = _python_lock_packages()
    rows: list[tuple[str, str, str]] = []
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if not name or _normalise(name) not in locked:
            continue
        rows.append((name, dist.version, _license_from_metadata(dist)))
    return sorted(set(rows), key=lambda row: _normalise(row[0]))


def _npm_rows() -> list[tuple[str, str, str]]:
    document = json.loads(NPM_LOCK.read_text(encoding="utf-8"))
    rows: list[tuple[str, str, str]] = []
    for location, package in document.get("packages", {}).items():
        if not location or not location.startswith("node_modules/") or package.get("link"):
            continue
        name = location.removeprefix("node_modules/")
        version = str(package.get("version", "unknown"))
        license_name = package.get("license") or "UNKNOWN"
        rows.append((name, version, str(license_name)))
    return sorted(set(rows), key=lambda row: row[0])


def render() -> str:
    lines = [
        "# Third-party license inventory",
        "",
        "This file is generated from `backend/requirements.lock` and `apps/web/package-lock.json`.",
        "Python license values are read from the installed environment used to generate the file;",
        "npm values are read from resolved lock entries. `UNKNOWN` requires release-review follow-up.",
        "",
        "## Python",
        "",
        "| Package | Version | License metadata |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {name} | {version} | {license_name} |" for name, version, license_name in _python_rows())
    lines.extend([
        "",
        "## Web",
        "",
        "| Package | Version | License metadata |",
        "| --- | --- | --- |",
    ])
    lines.extend(f"| `{name}` | {version} | {license_name} |" for name, version, license_name in _npm_rows())
    lines.extend([
        "",
        "## Review rule",
        "",
        "Before distribution, resolve every `UNKNOWN`, retain required notices, and rerun this generator after dependency changes.",
        "This inventory does not grant permission to copy third-party site content or enterprise materials.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "license-inventory.md")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()

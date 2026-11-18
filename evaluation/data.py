import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASETS = Path(__file__).resolve().parent / "datasets"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    paths = sorted((ROOT / "app").rglob("*.py")) + sorted((ROOT / "knowledge").rglob("*.md"))
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in paths}


def load_dataset(split: str) -> dict:
    if split not in ("dev", "acceptance", "week4"):
        raise ValueError("Unknown split")
    data = json.loads((DATASETS / f"{split}.json").read_text(encoding="utf-8-sig"))
    cases = data["cases"]
    expected_count = {"dev": 20, "acceptance": 60, "week4": 12}[split]
    if len(cases) != expected_count or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Dataset size or unique IDs are invalid")
    if split == "acceptance" and (
        sum(case["kind"] == "business" for case in cases) != 40
        or sum(case["kind"] == "abnormal" for case in cases) != 20
    ):
        raise ValueError("Acceptance must contain 40 business and 20 abnormal/security cases")
    for case in cases:
        if case["split"] != split or case["execution_mode"] not in ("live", "fault"):
            raise ValueError("Invalid case split or execution mode")
        if not case["question"].strip() or not case["expected"]["status"]:
            raise ValueError("Missing question or expected status")
        for source in case["expected"].get("source_files", []):
            path = (ROOT / source.split("::", 1)[0]).resolve()
            if not path.is_relative_to(ROOT) or not path.exists():
                raise ValueError("Source file must exist inside project")
    return data


def freeze_datasets(split: str | None = None) -> dict:
    selected = ("week4",) if split == "week4" else ("dev", "acceptance")
    for name in selected:
        load_dataset(name)
    freeze_path = DATASETS / ("week4-freeze.json" if split == "week4" else "freeze.json")
    hashes = {f"{name}.json": digest(DATASETS / f"{name}.json") for name in selected}
    if freeze_path.exists():
        existing = json.loads(freeze_path.read_text(encoding="utf-8"))
        if existing["datasets"] != hashes:
            raise ValueError("Frozen dataset changed; create an explicitly versioned new evaluation")
        return existing
    from datetime import UTC, datetime

    result = {
        "frozen_at": datetime.now(UTC).isoformat(),
        "datasets": hashes,
        "scope": (
            "Exposed week-three cases and new variants; focused regression, "
            "not blind acceptance; AI-assisted labels"
            if split == "week4"
            else "Synthetic source-grounded cases authored before model output; AI-assisted labels"
        ),
    }
    freeze_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

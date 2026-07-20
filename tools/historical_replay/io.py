import csv
import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .schemas import Candle, ReplayResult, SyntheticSignal

ModelT = TypeVar("ModelT", bound=BaseModel)


def _load_jsonl(path: Path, model: type[ModelT]) -> list[ModelT]:
    rows: list[ModelT] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(model.model_validate_json(line))
            except ValueError as exc:
                raise ValueError(f"invalid {path}:{line_number}: {exc}") from exc
    return rows


def load_candles(path: Path) -> list[Candle]:
    return _load_jsonl(path, Candle)


def load_signals(path: Path) -> list[SyntheticSignal]:
    return _load_jsonl(path, SyntheticSignal)


def write_inputs(
    directory: Path, candles: list[Candle], signals: list[SyntheticSignal]
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for filename, rows in (("candles.jsonl", candles), ("signals.jsonl", signals)):
        with (directory / filename).open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(row.model_dump_json() + "\n")


def write_report(directory: Path, result: ReplayResult) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(
        result.summary.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (directory / "config.json").write_text(
        result.config.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(directory / "trades.csv", result.trades)
    _write_csv(directory / "decisions.csv", result.decisions)
    _write_csv(directory / "equity_curve.csv", result.equity_curve)


def _write_csv(path: Path, rows: list[BaseModel]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    serialized = [row.model_dump(mode="json") for row in rows]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(serialized[0]))
        writer.writeheader()
        for row in serialized:
            writer.writerow(
                {
                    key: json.dumps(value) if isinstance(value, (dict, list)) else value
                    for key, value in row.items()
                }
            )

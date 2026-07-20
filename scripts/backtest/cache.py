import hashlib
import json
import sqlite3
from pathlib import Path


class ResponseCache:
    """Caches `/analyze` responses keyed by a hash of the full request
    payload — which already encodes candle_ts, indicators, sentiment, and
    position_context — so a re-run only calls the LLM for genuinely new
    (candle, position-state) combinations."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS analyze_cache (
                request_hash TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                candle_close_time TEXT NOT NULL,
                request_json TEXT NOT NULL,
                response_json TEXT NOT NULL
            )"""
        )
        self._conn.commit()

    @staticmethod
    def _hash(payload: dict) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def get(self, payload: dict) -> dict | None:
        row = self._conn.execute(
            "SELECT response_json FROM analyze_cache WHERE request_hash = ?",
            (self._hash(payload),),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, payload: dict, response: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO analyze_cache "
            "(request_hash, symbol, candle_close_time, request_json, response_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                self._hash(payload),
                payload["symbol"],
                payload["candle_close_time"],
                json.dumps(payload),
                json.dumps(response),
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class CandleCache:
    """Local mirror of fetched OHLCV so re-running a backtest over the same
    range never re-hits Binance."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS ohlcv (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                ts INTEGER NOT NULL,
                o REAL NOT NULL,
                h REAL NOT NULL,
                l REAL NOT NULL,
                c REAL NOT NULL,
                v REAL NOT NULL,
                PRIMARY KEY (symbol, timeframe, ts)
            )"""
        )
        self._conn.commit()

    def get_range(self, symbol: str, timeframe: str, since_ms: int, until_ms: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT ts, o, h, l, c, v FROM ohlcv WHERE symbol=? AND timeframe=? "
            "AND ts >= ? AND ts < ? ORDER BY ts ASC",
            (symbol, timeframe, since_ms, until_ms),
        ).fetchall()
        return [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]} for r in rows]

    def put_many(self, symbol: str, timeframe: str, candles: list[dict]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO ohlcv (symbol, timeframe, ts, o, h, l, c, v) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (symbol, timeframe, c["t"], c["o"], c["h"], c["l"], c["c"], c["v"])
                for c in candles
            ],
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

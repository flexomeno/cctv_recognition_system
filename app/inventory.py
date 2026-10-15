from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path


def epoch_to_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


def start_of_local_day(moment: datetime | None = None) -> str:
    moment = moment or datetime.now()
    return moment.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def format_counts(rows: list[tuple[str, str, int]]) -> str:
    if not rows:
        return "Todavía no hay visitas."
    headers = ("cámara", "clase", "visitas")
    body = [(camera, class_name, str(count)) for camera, class_name, count in rows]
    widths = [len(cell) for cell in headers]
    for row in body:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells))

    lines = [
        line(headers),
        line(tuple("-" * width for width in widths)),
        *(line(row) for row in body),
        f"Total: {sum(count for _, _, count in rows)}",
    ]
    return "\n".join(lines)


class Inventory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id TEXT NOT NULL,
                class_name TEXT NOT NULL,
                track_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                confidence REAL NOT NULL,
                clip_path TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_visits_started ON visits(camera_id, started_at)"
        )
        self._conn.commit()

    def add_visit(
        self,
        *,
        camera_id: str,
        class_name: str,
        track_id: int,
        started_at: float,
        ended_at: float,
        confidence: float,
        clip_path: str | None,
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO visits (
                    camera_id, class_name, track_id, started_at, ended_at, confidence, clip_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    camera_id,
                    class_name,
                    track_id,
                    epoch_to_iso(started_at),
                    epoch_to_iso(ended_at),
                    round(float(confidence), 3),
                    clip_path,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def counts(self, since: str | None = None) -> list[tuple[str, str, int]]:
        sql = "SELECT camera_id, class_name, COUNT(*) FROM visits"
        params: list[str] = []
        if since is not None:
            sql += " WHERE started_at >= ?"
            params.append(since)
        sql += " GROUP BY camera_id, class_name ORDER BY camera_id, class_name"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [(str(camera), str(class_name), int(count)) for camera, class_name, count in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

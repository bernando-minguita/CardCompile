"""Export history row access."""

from __future__ import annotations

import uuid
from datetime import datetime

from database.repositories.base import BaseRepository
from models.export_record import ExportRecord


class ExportRepository(BaseRepository):
    """Keeps track of the PDF files produced from a project."""

    def insert(
        self,
        project_id: str,
        output_path: str,
        page_count: int,
        quality: str,
    ) -> ExportRecord:
        record = ExportRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            output_path=str(output_path),
            page_count=int(page_count),
            quality=quality,
            exported_at=datetime.now().isoformat(timespec="seconds"),
        )
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO export_history (id, project_id, output_path, "
                "page_count, quality, exported_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.project_id,
                    record.output_path,
                    record.page_count,
                    record.quality,
                    record.exported_at,
                ),
            )
        return record

    def list_for_project(self, project_id: str, limit: int = 50) -> list[ExportRecord]:
        rows = self._all(
            "SELECT * FROM export_history WHERE project_id = ? "
            "ORDER BY exported_at DESC LIMIT ?",
            (project_id, limit),
        )
        return [ExportRecord.from_row(row) for row in rows]

    def count(self, project_id: str) -> int:
        return int(
            self._scalar(
                "SELECT COUNT(*) FROM export_history WHERE project_id = ?",
                (project_id,),
                default=0,
            )
        )

    def delete(self, record_id: str) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM export_history WHERE id = ?", (record_id,))

from enum import Enum


class ExportFormat(str, Enum):
    CSV = "csv"
    XLSX = "xlsx"


class ExportRequest:
    """Request to export query results."""

    def __init__(
        self,
        database_id: str,
        sql: str,
        format: ExportFormat,
        filename: str | None = None,
    ) -> None:
        self.database_id = database_id
        self.sql = sql
        self.format = format
        self.filename = filename or self._default_filename()

    def _default_filename(self) -> str:
        import datetime

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = self.format.value
        return f"export_{timestamp}.{ext}"
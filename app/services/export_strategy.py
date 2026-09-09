from abc import ABC, abstractmethod
from io import BytesIO, StringIO
import csv
from typing import Any

from openpyxl import Workbook


class ExportStrategy(ABC):
    """Abstract base for export strategies."""

    @abstractmethod
    async def export(
        self,
        columns: list[str],
        rows: list[list[Any]],
    ) -> bytes:
        """Export data to bytes."""
        pass

    @abstractmethod
    def content_type(self) -> str:
        """MIME type for the export format."""
        pass


class CsvExportStrategy(ExportStrategy):
    """Export to CSV format."""

    async def export(
        self,
        columns: list[str],
        rows: list[list[Any]],
    ) -> bytes:
        """Export data to CSV."""
        output = StringIO()
        writer = csv.writer(output)

        writer.writerow(columns)

        for row in rows:
            writer.writerow(row)

        return output.getvalue().encode("utf-8")

    def content_type(self) -> str:
        return "text/csv"


class XlsxExportStrategy(ExportStrategy):
    """Export to XLSX format."""

    async def export(
        self,
        columns: list[str],
        rows: list[list[Any]],
    ) -> bytes:
        """Export data to XLSX."""
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Data"

        worksheet.append(columns)

        for row in rows:
            worksheet.append(row)

        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter

            for cell in column:
                try:
                    if cell.value:
                        max_length = max(
                            max_length,
                            len(str(cell.value)),
                        )
                except Exception:
                    pass

            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width

        output = BytesIO()
        workbook.save(output)
        output.seek(0)

        return output.getvalue()

    def content_type(self) -> str:
        return (
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        )


class ExportStrategyFactory:
    """Factory for creating export strategies."""

    _strategies = {
        "csv": CsvExportStrategy,
        "xlsx": XlsxExportStrategy,
    }

    @classmethod
    def create(cls, format: str) -> ExportStrategy:
        """Create an export strategy by format."""
        strategy_class = cls._strategies.get(format.lower())

        if strategy_class is None:
            raise ValueError(f"Unsupported export format: {format}")

        return strategy_class()
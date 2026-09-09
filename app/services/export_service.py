from typing import Any

from app.models.mcp_tools import RunSqlResponse
from app.services.export_strategy import ExportStrategyFactory


class ExportService:
    """
    Service for exporting query results.
    """

    async def export(
        self,
        result: RunSqlResponse,
        format: str,
    ) -> bytes:
        """
        Export query results to the specified format.

        Args:
            result: Query result with columns and rows
            format: Export format (csv, xlsx)

        Returns:
            Exported data as bytes
        """
        if not result.columns or not result.rows:
            raise ValueError("Cannot export empty result set")

        strategy = ExportStrategyFactory.create(format)

        return await strategy.export(result.columns, result.rows)

    @staticmethod
    def get_content_type(format: str) -> str:
        """Get MIME type for format."""
        strategy = ExportStrategyFactory.create(format)
        return strategy.content_type()
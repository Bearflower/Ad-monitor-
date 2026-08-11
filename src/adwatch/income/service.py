from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from adwatch.income.models import FeeStatement
from adwatch.income.repository import FeeStatementRepository
from adwatch.income.shopee_pdf import IncomeParseError, parse_shopee_pdf
from adwatch.integrations.commerce import CommerceRepository
from adwatch.storage.db import Database


class IncomeScanError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportResult:
    status: str
    reason_code: str = ""
    statement: FeeStatement | None = None


@dataclass(frozen=True)
class ScanResult:
    discovered: int = 0
    imported: int = 0
    revised: int = 0
    skipped: int = 0
    quarantined: int = 0
    failed: int = 0

    def add(self, result: ImportResult) -> ScanResult:
        field = result.status
        if field not in {
            "imported",
            "revised",
            "skipped",
            "quarantined",
            "failed",
        }:
            field = "failed"
        values = self.__dict__ | {
            "discovered": self.discovered + 1,
            field: getattr(self, field) + 1,
        }
        return ScanResult(**values)


class IncomeImportService:
    def __init__(
        self,
        database: Database,
        *,
        scan_root: Path,
        parsers: dict[str, Callable[[Path], FeeStatement]] | None = None,
        commerce_repository: CommerceRepository | None = None,
    ) -> None:
        self.database = database
        self.scan_root = scan_root
        self.repository = FeeStatementRepository(database)
        self.parsers = parsers or {"shopee": parse_shopee_pdf}
        self.commerce_repository = commerce_repository or CommerceRepository(
            database
        )

    def import_file(
        self,
        path: Path,
        *,
        platform: str,
        retry_terminal: bool = False,
    ) -> ImportResult:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return ImportResult("failed", "file_read_failed")
        processed_status = self.repository.processed_status(digest)
        terminal_retry = processed_status in {"quarantined", "failed"}
        if processed_status is not None and not (
            retry_terminal and terminal_retry
        ):
            return ImportResult("skipped")
        parser = self.parsers.get(platform)
        if parser is None:
            return ImportResult("failed", "platform_pending_external")
        try:
            parsed = parser(path)
        except IncomeParseError:
            self._record_terminal(
                digest,
                path.name,
                platform,
                "failed",
                "parse_failed",
            )
            return ImportResult("failed", "parse_failed")
        except Exception:  # noqa: BLE001 - isolate pluggable parser failures
            self._record_terminal(
                digest,
                path.name,
                platform,
                "failed",
                "parser_failed",
            )
            return ImportResult("failed", "parser_failed")
        if parsed.source_sha256 != digest or parsed.platform != platform:
            self._record_terminal(
                digest,
                path.name,
                platform,
                "failed",
                "source_contract_mismatch",
            )
            return ImportResult("failed", "source_contract_mismatch")
        canonical_store = self.repository.canonical_store(
            platform,
            parsed.source_store,
        )
        if canonical_store is None:
            self._record_terminal(
                digest,
                path.name,
                platform,
                "quarantined",
                "store_unmapped",
            )
            return ImportResult("quarantined", "store_unmapped")
        statement = replace(parsed, store=canonical_store)
        try:
            with self.database.transaction() as connection:
                if terminal_retry:
                    connection.execute(
                        """
                        DELETE FROM income_import_events
                        WHERE source_sha256=?
                          AND status IN ('quarantined', 'failed')
                        """,
                        (digest,),
                    )
                status = self.repository.save_with_connection(
                    connection,
                    statement,
                )
                if status != "skipped":
                    self.commerce_repository.upsert_income_statement_facts(
                        connection,
                        statement,
                    )
        except Exception:  # noqa: BLE001 - convert atomic fact-write failure
            self._record_terminal(
                digest,
                path.name,
                platform,
                "failed",
                "fact_write_failed",
            )
            return ImportResult("failed", "fact_write_failed")
        return ImportResult(status, statement=statement)

    def scan(self) -> ScanResult:
        result = ScanResult()
        try:
            if not self.scan_root.is_dir():
                return result
            paths = sorted(self.scan_root.rglob("weekly_report_*.pdf"))
        except OSError as error:
            raise IncomeScanError("income_scan_io_failed") from error
        for path in paths:
            result = result.add(self.import_file(path, platform="shopee"))
        return result

    def _record_terminal(
        self,
        digest: str,
        source_name: str,
        platform: str,
        status: str,
        reason_code: str,
    ) -> None:
        self.repository.record_event(
            source_sha256=digest,
            source_name=source_name,
            platform=platform,
            store="",
            status=status,
            reason_code=reason_code,
        )

"""Python access to the SQL ownership functions (see core/sql/ownership.sql)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import connection
from django.utils import timezone


@dataclass(frozen=True)
class ClassOwnership:
    company_id: int
    share_class_id: int
    share_class: str
    is_common: bool
    seniority: int
    conversion_ratio: Decimal
    shares_outstanding: Decimal
    my_shares: Decimal
    pct_of_class: Decimal | None
    inconsistent: bool
    missing_issuance: bool
    negative_position: bool

    @property
    def has_warning(self):
        return self.inconsistent or self.missing_issuance or self.negative_position


@dataclass(frozen=True)
class CompanyOwnership:
    company_id: int
    name: str
    my_as_converted: Decimal
    total_as_converted: Decimal
    option_pool: int
    warrants: int
    my_warrants: Decimal
    fully_diluted_total: Decimal
    pct_basic: Decimal | None
    pct_fully_diluted: Decimal | None


def _rows(sql, params, cls):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [cls(*row) for row in cursor.fetchall()]


def _as_of(value: date | None) -> date:
    return value or timezone.localdate()


def ownership_by_class(company_id=None, as_of=None, entity_id=None):
    """Cap table per class at ``as_of`` (default today), optionally for one entity."""
    sql = "SELECT * FROM fn_ownership_by_class(%s, %s)"
    params = [_as_of(as_of), entity_id]
    if company_id is not None:
        sql += " WHERE company_id = %s"
        params.append(company_id)
    sql += " ORDER BY company_id, seniority DESC, share_class"
    return _rows(sql, params, ClassOwnership)


def ownership_company(company_id=None, as_of=None, entity_id=None):
    sql = "SELECT * FROM fn_ownership_company(%s, %s)"
    params = [_as_of(as_of), entity_id]
    if company_id is not None:
        sql += " WHERE company_id = %s"
        params.append(company_id)
        rows = _rows(sql, params, CompanyOwnership)
        return rows[0] if rows else None
    return _rows(sql + " ORDER BY name", params, CompanyOwnership)


def class_outstanding_before(share_class_id, on_date):
    """Shares of a class outstanding just before ``on_date`` (used for split deltas)."""
    from datetime import timedelta

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT shares_outstanding FROM fn_class_outstanding(%s) WHERE share_class_id = %s",
            [on_date - timedelta(days=1), share_class_id],
        )
        row = cursor.fetchone()
    return row[0] if row else Decimal(0)


def consistency_warnings(company_id=None, as_of=None):
    """Classes where my shares exceed the class total, lack issuance events or are negative."""
    return [row for row in ownership_by_class(company_id, as_of) if row.has_warning]

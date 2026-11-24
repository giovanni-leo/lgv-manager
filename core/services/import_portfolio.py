"""Import a fund's portfolio from the backoffice workbook ("Portfolio Summary" sheet).

One position per company and account is created, without shares: its cost, and the fair value reported
by the fund on the date of the sheet. The investment date is read from the company's own sheet when
there is one ("Nov. 21" = November 2021), otherwise it is the fund's first closing. Everything created
carries a marker in its notes, so running the import again updates the same rows instead of adding more.
The workbook has known errors: every doubtful value is reported, nothing is guessed silently.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from core.models import Company, Investment, Realization

MARKER = "[import:{fund}:{account}]"
STATUS = {"open": "portfolio", "exit": "exited", "liquidated": "written_off"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"], 1)}
FALLBACK_DATE = date(2021, 11, 9)                      # first closing of LGV1


@dataclass
class Position:
    row: int
    company: str
    account: str
    status: str
    cost: Decimal | None
    fair_value: Decimal | None
    realized: Decimal | None
    invested_on: date | None = None
    date_note: str = ""


@dataclass
class ImportReport:
    as_of: date | None = None
    positions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    created: int = 0
    updated: int = 0


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    text = str(value).strip().replace("'", "").replace(",", "")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001 - "n.a", "#VALUE!"
        return None


def _month_date(value):
    """'Nov. 21' -> 2021-11-01; a real date stays itself; anything else -> None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = re.fullmatch(r"\s*([A-Za-z]{3})[a-z]*\.?\s*[-/ ]?\s*'?(\d{2}|\d{4})\s*", str(value or ""))
    if not match or match.group(1).lower() not in MONTHS:
        return None
    year = int(match.group(2))
    return date(year + 2000 if year < 100 else year, MONTHS[match.group(1).lower()], 1)


def _find_header(ws):
    for r, row in enumerate(ws.iter_rows(min_row=1, max_row=40, values_only=True), 1):
        labels = [str(c).strip().lower() if c is not None else "" for c in row]
        if "portfolio company" in labels:
            return r, {name: labels.index(name) for name in ("portfolio company", "fund", "status", "fmv in portfolio",
                                                             "total cost", "realized value") if name in labels}
    return None, {}


def _company_sheet(book, name):
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    for ws in book.worksheets:
        sheet = re.sub(r"[^a-z0-9]", "", ws.title.lower())
        if sheet and key.startswith(sheet) and ws.title != "Sheet1":
            return ws
    return None


def _investment_dates(ws):
    """{'GSI': date, 'LGV': date} from the row "Investment Date" of a company sheet."""
    out = {}
    for row in ws.iter_rows(min_row=1, max_row=30, values_only=True):
        labels = [str(c).strip().lower() if c is not None else "" for c in row]
        if "investment date" in labels:
            i = labels.index("investment date")
            for offset, account in ((1, "GSI"), (2, "LGV")):
                if i + offset < len(row):
                    parsed = _month_date(row[i + offset])
                    if parsed:
                        out[account] = parsed
            break
    return out


def read_positions(book, sheet_name):
    report = ImportReport()
    ws = book[sheet_name]
    header_row, cols = _find_header(ws)
    if header_row is None or "portfolio company" not in cols:
        raise ValueError(f"Sheet {sheet_name!r}: no 'Portfolio Company' header found.")
    for row in ws.iter_rows(min_row=1, max_row=header_row, values_only=True):
        labels = [str(c).strip().lower() if c is not None else "" for c in row]
        if "updates on :" in labels or "updates on:" in labels:
            report.as_of = next((_month_date(c) for c in row if isinstance(c, (date, datetime))), None)
    if report.as_of is None:
        report.warnings.append("The sheet has no 'Updates on' date: the reported values are dated today.")
        report.as_of = date.today()
    first_account = {}
    for r, row in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), header_row + 1):
        label = next((str(c).strip() for c in row if isinstance(c, str) and c.strip()), "")
        if label.upper().startswith("GSI +") or label.upper().startswith("TOTAL PORTFOLIO"):
            break                                    # the combined section repeats positions already counted
        account = str(row[cols["fund"]] or "").strip() if "fund" in cols else ""
        company = str(row[cols["portfolio company"]] or "").strip()
        if account not in ("GSI", "LGV") or not company or company.lower() == "total":
            continue
        status_text = str(row[cols["status"]] or "").strip().lower()
        pos = Position(row=r, company=company, account=account, status=STATUS.get(status_text, "portfolio"),
                       cost=_number(row[cols["total cost"]]), fair_value=_number(row[cols["fmv in portfolio"]]),
                       realized=_number(row[cols["realized value"]]))
        if status_text not in STATUS:
            report.warnings.append(f"Row {r} {company}: unknown status {status_text!r}, taken as open.")
        sheet = _company_sheet(book, company)
        dates = _investment_dates(sheet) if sheet else {}
        pos.invested_on = dates.get(account)
        if pos.invested_on is None:
            pos.invested_on, pos.date_note = FALLBACK_DATE, "date unknown: first closing used"
            report.warnings.append(f"Row {r} {company} ({account}): investment date not found, "
                                   f"{FALLBACK_DATE} used. Correct it in Data › Investments if you know it.")
        if pos.cost is None and status_text != "liquidated":
            report.warnings.append(f"Row {r} {company} ({account}): no cost, position skipped.")
        first_account.setdefault(company, account)
        report.positions.append(pos)
    return report


def check_totals(book, sheet_name, report):
    """Compare the sum of what was read with the 'Total' rows of the sheet."""
    ws = book[sheet_name]
    header_row, cols = _find_header(ws)
    sections = {}
    current = None
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        text = [str(c).strip() for c in row if isinstance(c, str)]
        if text and text[0] in ("GSI", "LGV"):
            current = text[0]
        if text and text[0].lower() == "total" and current:
            sections[current] = (_number(row[cols["total cost"]]), _number(row[cols["fmv in portfolio"]]))
    for account, (cost, fmv) in sections.items():
        mine_cost = sum((p.cost or 0 for p in report.positions if p.account == account), Decimal(0))
        mine_fmv = sum((p.fair_value or 0 for p in report.positions if p.account == account), Decimal(0))
        for label, mine, theirs in (("cost", mine_cost, cost), ("fair value", mine_fmv, fmv)):
            if theirs is not None and abs(mine - theirs) > Decimal("1"):
                report.warnings.append(f"{account} total {label}: the rows add up to {mine:,.0f} but the sheet's "
                                       f"Total row says {theirs:,.0f}. Check the sheet.")


def apply_import(report, fund, source_name):
    """Create or update the companies, investments and realizations of the report."""
    from core.ai.jobs import match_company

    for pos in report.positions:
        company = match_company(pos.company, None)
        if company is None:
            company = Company.objects.create(name=pos.company, status=pos.status)
        elif company.status in ("prospect", "archived") or pos.status in ("exited", "written_off"):
            company.status = pos.status
            company.save(update_fields=["status", "updated_at"])
        marker = MARKER.format(fund=fund.code or fund.pk, account=pos.account)
        note = f"{marker} imported from {source_name}" + (f"; {pos.date_note}" if pos.date_note else "")
        if pos.cost is not None:
            inv = Investment.objects.filter(company=company, entity=fund, notes__contains=marker).first()
            values = dict(invest_date=pos.invested_on, instrument="reported", amount=pos.cost, currency=fund.base_currency,
                          reported_value=pos.fair_value if pos.fair_value is not None else Decimal(0),
                          reported_value_date=report.as_of, notes=note)
            if inv is None:
                Investment.objects.create(company=company, entity=fund, **values)
                report.created += 1
            else:
                for k, v in values.items():
                    setattr(inv, k, v)
                inv.save()
                report.updated += 1
        if pos.realized:
            real = Realization.objects.filter(company=company, entity=fund, notes__contains=marker).first()
            values = dict(realized_on=report.as_of, kind=Realization.Kind.SALE, proceeds=pos.realized,
                          currency=fund.base_currency, notes=f"{marker} realized value reported by the fund "
                                                              f"(date of the sheet; the real date is unknown)")
            if real is None:
                Realization.objects.create(company=company, entity=fund, **values)
            else:
                for k, v in values.items():
                    setattr(real, k, v)
                real.save()

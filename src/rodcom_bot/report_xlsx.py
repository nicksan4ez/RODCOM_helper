from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .db import CollectionMember, CollectionSummary, Database


@dataclass(frozen=True)
class Sheet:
    name: str
    rows: list[list[object]]


def build_collections_report(db: Database, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    summaries = db.list_collection_summaries(active_only=False)

    sheets = [
        Sheet("Сводка", _summary_rows(summaries)),
        Sheet("Должники", _debtors_rows(db, summaries)),
    ]
    used_names = {sheet.name for sheet in sheets}
    for summary in summaries:
        sheet_name = _unique_sheet_name(_safe_sheet_name(summary.collection.title), used_names)
        sheets.append(Sheet(sheet_name, _collection_rows(db, summary)))
        used_names.add(sheet_name)

    _write_xlsx(output, sheets)
    return output


def _summary_rows(summaries: list[CollectionSummary]) -> list[list[object]]:
    active = [summary for summary in summaries if summary.collection.status == "active"]
    total_expected = sum(summary.expected_total for summary in active)
    total_paid = sum(summary.paid_total for summary in active)
    total_debtors = sum(summary.members_count - summary.paid_count for summary in active)
    rows: list[list[object]] = [
        ["Сборы родкома"],
        ["Обновлено", datetime.now().strftime("%d.%m.%Y %H:%M")],
        [],
        ["Активные сборы", len(active)],
        ["Всего к сбору", total_expected],
        ["Собрано", total_paid],
        ["Осталось", total_expected - total_paid],
        ["Должников", total_debtors],
        [],
        ["Сбор", "Статус", "Сумма с ученика", "Участников", "Сдали", "Собрано", "Осталось"],
    ]
    for summary in summaries:
        rows.append(
            [
                summary.collection.title,
                "Активен" if summary.collection.status == "active" else "Закрыт",
                summary.collection.expected_amount,
                summary.members_count,
                summary.paid_count,
                summary.paid_total,
                summary.expected_total - summary.paid_total,
            ]
        )
    return rows


def _debtors_rows(db: Database, summaries: list[CollectionSummary]) -> list[list[object]]:
    rows: list[list[object]] = [["Ребенок", "Сбор", "Нужно сдать", "Сдано", "Осталось"]]
    for summary in summaries:
        if summary.collection.status != "active":
            continue
        for member in db.list_collection_members(summary.collection.id):
            remaining = member.expected_amount - member.paid_amount
            if remaining > 0:
                rows.append([member.full_name, summary.collection.title, member.expected_amount, member.paid_amount, remaining])
    return rows


def _collection_rows(db: Database, summary: CollectionSummary) -> list[list[object]]:
    rows: list[list[object]] = [
        [summary.collection.title],
        ["Статус", "Активен" if summary.collection.status == "active" else "Закрыт"],
        ["Сумма с ученика", summary.collection.expected_amount],
        [],
        ["№", "Ребенок", "Нужно сдать", "Сдано", "Осталось", "Статус", "Комментарий"],
    ]
    for index, member in enumerate(db.list_collection_members(summary.collection.id), 1):
        rows.append(
            [
                index,
                member.full_name,
                member.expected_amount,
                member.paid_amount,
                member.expected_amount - member.paid_amount,
                _status_label(member),
                member.comment,
            ]
        )
    return rows


def _status_label(member: CollectionMember) -> str:
    if member.status == "paid":
        return "Сдал"
    if member.status == "partial":
        return "Частично"
    return "Не сдал"


def _safe_sheet_name(value: str) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", " ", value).strip() or "Сбор"
    return cleaned[:31]


def _unique_sheet_name(value: str, used_names: set[str]) -> str:
    if value not in used_names:
        return value
    for index in range(2, 100):
        suffix = f" {index}"
        candidate = f"{value[:31 - len(suffix)]}{suffix}"
        if candidate not in used_names:
            return candidate
    raise ValueError("Could not build a unique XLSX sheet name")


def _write_xlsx(path: Path, sheets: list[Sheet]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types(len(sheets)))
        archive.writestr("_rels/.rels", _root_rels())
        archive.writestr("docProps/core.xml", _core_props())
        archive.writestr("docProps/app.xml", _app_props(len(sheets)))
        archive.writestr("xl/workbook.xml", _workbook(sheets))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheets)))
        archive.writestr("xl/styles.xml", _styles())
        for index, sheet in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet(sheet.rows))


def _worksheet(rows: list[list[object]]) -> str:
    body = []
    for row_index, row in enumerate(rows, 1):
        cells = []
        for col_index, value in enumerate(row, 1):
            ref = f"{_column_name(col_index)}{row_index}"
            cells.append(_cell(ref, value))
        body.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<cols><col min="1" max="1" width="28" customWidth="1"/><col min="2" max="7" width="18" customWidth="1"/></cols>'
        "<sheetData>"
        + "".join(body)
        + "</sheetData></worksheet>"
    )


def _cell(ref: str, value: object) -> str:
    if value is None or value == "":
        return f'<c r="{ref}"/>'
    if isinstance(value, int | float):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _content_types(sheet_count: int) -> str:
    overrides = [
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    overrides.extend(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(overrides)
        + "</Types>"
    )


def _root_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )


def _workbook(sheets: list[Sheet]) -> str:
    sheet_xml = "".join(
        f'<sheet name="{_xml_attr(sheet.name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, sheet in enumerate(sheets, 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheet_xml}</sheets></workbook>"
    )


def _workbook_rels(sheet_count: int) -> str:
    rels = [
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    ]
    rels.append(
        f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rels)
        + "</Relationships>"
    )


def _styles() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        "</styleSheet>"
    )


def _core_props() -> str:
    created_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:creator>RODCOM bot</dc:creator>"
        "<cp:lastModifiedBy>RODCOM bot</cp:lastModifiedBy>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{created_at}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{created_at}</dcterms:modified>'
        "</cp:coreProperties>"
    )


def _app_props(sheet_count: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>RODCOM bot</Application>"
        f"<Worksheets>{sheet_count}</Worksheets>"
        "</Properties>"
    )


def _xml_attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})

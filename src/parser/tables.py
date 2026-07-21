"""Line-item extraction from chandra's HTML tables.

Calibrated on the Amazon Seller Services credit note fixture, which uses
a multi-page "Details of Fees" table with this 8-column header:

    Date | Original Invoice Number | Original Invoice Date |
    Category of Service | Description of Service | Tax Rate |
    Fee Amount | Tax Amount

Each fee row uses ``rowspan="3"`` on the first four cells, followed by
SGST/CGST sub-rows. Rows can split across ``<table>`` elements at page
boundaries — the parser walks every matching table in document order and
preserves the in-progress parent item across tables.
"""

import logging
import re

from .patterns import parse_amount, parse_pct, clean_hsn

logger = logging.getLogger(__name__)


# Cells whose presence in a ``<th>`` row identifies the "details" table.
_DETAILS_TABLE_REQUIRED = ("fee amount", "tax amount")

# Cells that identify the 7-column summary table (excluded from details).
_SUMMARY_REQUIRED = ("si no",)

_TAX_TYPES = {"SGST", "CGST", "IGST", "UTGST", "UTGST"}
_TOTAL_LABEL = "total"


# ---------------------------------------------------------------------------
# Cell helpers
# ---------------------------------------------------------------------------

def _row_cells(tr) -> list:
    """Return the stripped text of each ``<td>`` / ``<th>`` in *tr*.

    ``<br/>`` tags within a cell are converted to newlines so multi-line
    content (e.g. GNV's ``<td>Item Name<br/>HSN: 23091000</td>``) survives
    as two separate lines instead of being concatenated without a
    separator. Leading/trailing whitespace per cell is stripped.

    NOTE: this does NOT propagate ``rowspan`` — sub-rows inside a rowspan
    group come back with fewer cells. Use :func:`_expand_table_rows` when
    you need consistent row width across rowspan groups.
    """
    out = []
    for c in tr.find_all(["td", "th"]):
        for br in c.find_all("br"):
            br.replace_with("\n")
        out.append(c.get_text().strip())
    return out


def _flatten_header(table) -> list:
    """Flatten a multi-row ``<thead>`` (with rowspan/colspan) into one row.

    For tables whose header spans two ``<tr>`` rows — common pattern:
    most column headers use ``rowspan="2"``, while one or more use
    ``colspan="2"`` with sub-headers in the second row (e.g. GNV's
    ``IGST`` column splits into ``%`` and ``Amt``) — this returns a
    single list with the colspan cells expanded into their sub-headers,
    prefixed by the parent label.

    For a single-row header (or no ``<thead>``), returns the same as
    :func:`_row_cells` on the first ``<tr>``.
    """
    rows = table.find_all("tr")
    if not rows:
        return []
    if len(rows) < 2 or not _is_header_row(rows[0]) or not _is_header_row(rows[1]):
        return _row_cells(rows[0])

    row1 = rows[0].find_all(["th", "td"])
    row2_cells = rows[1].find_all(["th", "td"])
    row2_iter = iter(row2_cells)
    merged = []
    for cell in row1:
        try:
            colspan = int(cell.get("colspan") or 1)
        except (TypeError, ValueError):
            colspan = 1
        text = cell.get_text(strip=True)
        if colspan > 1:
            for _ in range(colspan):
                try:
                    sub = next(row2_iter)
                    sub_text = sub.get_text(strip=True)
                    if sub_text:
                        merged.append(f"{text} {sub_text}" if text else sub_text)
                    elif text:
                        merged.append(text)
                except StopIteration:
                    if text:
                        merged.append(text)
        else:
            merged.append(text)
    return merged


def _expand_table_rows(table) -> list:
    """Yield effective row cells for every ``<tr>`` with rowspan propagated.

    A ``rowspan="N"`` cell is repeated in the same column for the next
    ``N-1`` rows; the actual cells of those rows are then shifted past the
    occupied columns. This produces rows of (mostly) consistent width and
    lets downstream code use stable column indices regardless of rowspan.

    Required to handle Amazon's page-break rowspan pattern: when an
    invoice/credit-note table splits across pages, the OCR sometimes
    re-emits the date+category cells with ``rowspan="5"`` and packs two
    parents' worth of sub-rows under one rowspan header (the first
    SGST/CGST pair belongs to the orphan parent from the prior page, the
    description+taxes pair belongs to the rowspan parent). Without
    expansion, the description row of the second parent looks like a
    4-cell "collapsed" row and gets misclassified as a sub-row.
    """
    pending = {}  # col_idx -> (value, remaining_rows)
    rows_out = []
    for tr in table.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        effective = []
        td_iter = iter(tds)
        col = 0
        while True:
            if col in pending:
                val, rem = pending[col]
                effective.append(val)
                if rem - 1 <= 0:
                    del pending[col]
                else:
                    pending[col] = (val, rem - 1)
                col += 1
                continue
            try:
                cell = next(td_iter)
            except StopIteration:
                # Trailing pending cells (rare): pad them in.
                while col in pending:
                    val, rem = pending[col]
                    effective.append(val)
                    if rem - 1 <= 0:
                        del pending[col]
                    else:
                        pending[col] = (val, rem - 1)
                    col += 1
                break
            val = cell.get_text(strip=True)
            effective.append(val)
            try:
                rowspan = int(cell.get("rowspan") or 1)
            except (TypeError, ValueError):
                rowspan = 1
            if rowspan > 1:
                pending[col] = (val, rowspan - 1)
            col += 1
        rows_out.append(effective)
    return rows_out


def _is_header_cells(cells: list) -> bool:
    """Heuristic: row is a header if it contains 'description' or 'fee amount'."""
    lowered = [c.lower() for c in cells]
    return any("description" in c for c in lowered) or any(
        "fee amount" in c for c in lowered
    )


def _is_header_row(tr) -> bool:
    return bool(tr.find("th"))


_TOTAL_LABELS_EXACT = {"total", "grand total", "total:", "sub total", "subtotal", "sub-total"}


def _is_total_row(cells: list) -> bool:
    """Detect a footer "Total" row.

    Matches any cell whose stripped text is exactly ``Total`` / ``Grand
    Total`` / ``Total:`` (optionally wrapped in ``<b>...</b>`` which BS4
    has usually already flattened). ``Total Invoice Amount`` and other
    compound labels do NOT match — they belong to the totals-table
    extractor in :mod:`metadata`, not the line-item filter.
    """
    if not cells:
        return False
    for c in cells:
        if not c:
            continue
        low = c.lower().strip()
        low = low.replace("<b>", "").replace("</b>", "").strip()
        low = low.rstrip(":.;,").strip()
        if low in _TOTAL_LABELS_EXACT:
            return True
    return False


# ---------------------------------------------------------------------------
# Header column mapping
# ---------------------------------------------------------------------------

def _map_columns(header_cells: list) -> dict:
    """Map column-index -> canonical role from a header row.

    Required: 'date', 'description', 'fee_amount', 'tax_amount'.
    """
    col = {}
    for i, raw in enumerate(header_cells):
        h = raw.lower().strip()
        if "date" in h and "date" not in col:
            col["date"] = i
        elif "description" in h:
            col["description"] = i
        elif "category" in h and "service" in h:
            col["category"] = i
        elif "tax" in h and "rate" in h:
            col["tax_rate"] = i
        elif "fee" in h and "amount" in h:
            col["fee_amount"] = i
        elif "tax" in h and "amount" in h:
            col["tax_amount"] = i
        elif "amount" in h and "amount" not in col:
            col["amount"] = i
        elif "si" in h and "no" in h:
            col["sr_no"] = i
    return col


# ---------------------------------------------------------------------------
# Details-table extractor (Amazon credit note style)
# ---------------------------------------------------------------------------

def _select_details_tables(tables: list) -> tuple:
    """Return (matching_tables, column_map) for the Amazon-style details table.

    Raises ``ValueError`` if no matching table is found.
    """
    matches = []
    header_cells = None
    for t in tables:
        first_header_row = t.find("tr")
        if not first_header_row or not _is_header_row(first_header_row):
            continue
        cells = _row_cells(first_header_row)
        lowered = [c.lower() for c in cells]
        if all(any(req in c for c in lowered) for req in _DETAILS_TABLE_REQUIRED):
            matches.append(t)
            if header_cells is None:
                header_cells = cells
    if not matches:
        return [], {}
    return matches, _map_columns(header_cells)


def _new_item(idx: int, cells: list, col: dict) -> dict:
    """Build a fresh parent line item from a fee row."""
    n = len(col)  # not exact width, just a sane upper bound for index safety

    def get(role):
        i = col.get(role)
        if i is None or i >= len(cells):
            return ""
        return cells[i]

    description = get("description")
    category = get("category")
    fee = parse_amount(get("fee_amount") or get("amount"))

    item = {
        "sr_no": idx,
        "description": description or None,
        "hsn_code": clean_hsn(category) if category else None,
        "quantity": None,
        "unit": None,
        "rate": None,
        "discount": None,
        "taxable_value": fee,
        "cgst_rate": None,
        "cgst_amount": None,
        "sgst_rate": None,
        "sgst_amount": None,
        "igst_rate": None,
        "igst_amount": None,
        "total_amount": None,
    }
    return item


def _merge_tax_subrow(item: dict, cells: list) -> None:
    """Attach a tax sub-row (SGST/CGST/IGST) to *item*.

    Expects *cells* to already be rowspan-expanded (full-width). Scans for
    the first cell whose text is exactly a tax-type label; the rate is in
    the next cell; the amount is the last parseable cell after that.
    No-op if no tax label is found.
    """
    if not cells:
        return

    tax_idx = next(
        (i for i, c in enumerate(cells) if (c or "").upper().strip() in _TAX_TYPES),
        None,
    )
    if tax_idx is None:
        logger.debug("Sub-row passed detection but no tax label found: %r", cells)
        return

    desc = cells[tax_idx].upper().strip()
    rate = parse_pct(cells[tax_idx + 1]) if tax_idx + 1 < len(cells) else None
    amount = None
    for c in reversed(cells[tax_idx + 2:]):
        v = parse_amount(c)
        if v is not None:
            amount = v
            break

    if desc == "SGST":
        item["sgst_rate"] = rate
        item["sgst_amount"] = amount
    elif desc == "CGST":
        item["cgst_rate"] = rate
        item["cgst_amount"] = amount
    elif desc == "IGST":
        item["igst_rate"] = rate
        item["igst_amount"] = amount
    elif desc == "UTGST":
        # No dedicated UTGST slot in the schema; fold into SGST.
        item["sgst_rate"] = rate
        item["sgst_amount"] = amount


def _finalize_item(item: dict) -> dict:
    """Compute ``total_amount = taxable_value + sum(taxes)``."""
    taxable = item.get("taxable_value") or 0
    tax = 0
    for k in ("sgst_amount", "cgst_amount", "igst_amount"):
        v = item.get(k)
        if v is not None:
            tax += v
    if item.get("taxable_value") is not None:
        item["total_amount"] = round(taxable + tax, 2)
    return item


def extract_credit_note_line_items(doc) -> list:
    """Extract line items from the Amazon-style details table.

    Returns a list of dicts shaped for ``assembler._map_line_items``.
    Empty list if no matching table is found.

    The extractor pre-expands ``rowspan`` cells so every row reaches the
    header width. This is essential for the Amazon page-break pattern
    where a ``rowspan="5"`` header packs sub-rows for *two* parents (the
    orphan from the prior page + the current one) under a single rowspan
    block. After expansion, the sub-row detection reduces to "row
    contains a tax-type label" — every other non-header/non-total row is
    a parent.
    """
    matches, col = _select_details_tables(doc.tables)
    if not matches:
        logger.info("No details table found for credit note line items")
        return []

    items = []
    current = None
    next_sr = 1

    for table in matches:
        for cells in _expand_table_rows(table):
            if not cells:
                continue
            if _is_header_cells(cells):
                continue
            if _is_total_row(cells):
                continue

            # Tax sub-row if any cell is a tax-type label (SGST / CGST / IGST
            # / UTGST). After rowspan expansion, every other non-header row
            # is a parent (full-width).
            tax_label_anywhere = any(
                (c or "").upper().strip() in _TAX_TYPES for c in cells
            )
            if tax_label_anywhere:
                if current is None:
                    logger.debug("Stray sub-row before any parent: %r", cells)
                    continue
                _merge_tax_subrow(current, cells)
                continue

            # Parent row.
            if current is not None:
                items.append(_finalize_item(current))
            current = _new_item(next_sr, cells, col)
            next_sr += 1

    if current is not None:
        items.append(_finalize_item(current))

    return items


# ---------------------------------------------------------------------------
# GST e-invoice extractor (Indian standard, 13-column single-row-per-item)
# ---------------------------------------------------------------------------

# Headers that identify the canonical GST e-invoice line-items table.
# Required: HSN + at least one tax column + a taxable/line-total column.
_GST_TABLE_REQUIRED = ("hsn",)
_GST_TABLE_TAX_HEADERS = ("cgst", "sgst", "igst")
_GST_TABLE_TOTAL_HEADERS = (
    "line value", "assessable", "taxable", "net amt", "net amount", "net value",
)


def _norm_header(raw: str) -> str:
    """Normalize a header cell for role matching.

    - lowercase
    - strip leading ``*`` / ``\\*`` (markdown emphasis + escape artifacts)
    - strip trailing ``(INR)`` / ``(%)`` qualifier
    - collapse internal whitespace
    """
    if not raw:
        return ""
    h = raw.lower().strip()
    h = re.sub(r"^[\\*]+\s*", "", h)            # leading *, **, \*
    h = re.sub(r"\s*\(.*?\)\s*$", "", h)         # trailing (INR) / (%)
    h = re.sub(r"\s+", " ", h)                   # collapse whitespace
    return h.strip()


def _select_gst_invoice_tables(tables: list) -> tuple:
    """Return (matching_tables, column_map) for the standard GST e-invoice table.

    A table qualifies if it has either:

    - an ``HSN`` column (standard government e-invoice schema), OR
    - a ``Taxable Amount`` / ``Assessable`` column paired with at least
      one tax column (CGST / SGST / IGST). This catches distributor
      layouts (GNV) that embed HSN inside the item-description cell
      instead of giving it its own column.

    And in both cases, a tax column + a totals column (``Line Value`` /
    ``Net Amt`` / ``Total`` / ``Taxable``) must be present so we don't
    pick up ancillary tables (HSN-summary, remarks, etc.).
    """
    matches = []
    header_cells = None
    for t in tables:
        first_header_row = t.find("tr")
        if not first_header_row or not _is_header_row(first_header_row):
            continue
        cells = _flatten_header(t)
        lowered = [c.lower() for c in cells]
        has_hsn = any("hsn" in c for c in lowered)
        has_tax_label = any(
            any(tx in c for c in lowered) for tx in _GST_TABLE_TAX_HEADERS
        )
        has_taxable = any(
            "taxable" in c or "assessable" in c for c in lowered
        )
        has_total = any(
            any(tot in c for c in lowered) for tot in _GST_TABLE_TOTAL_HEADERS
        )
        # Must have a per-item identifier column (HSN or description /
        # product / item). Without this, ancillary tables like the
        # bottom-of-page totals table (``Taxable Amount | CGST Amount |
        # SGST Amount | Total Invoice Amount``) would falsely match.
        has_description_col = any(
            "description" in c
            or "product name" in c
            or "product desc" in c
            or c.split()[0] == "item"
            or c == "item"
            or c.startswith("item ")
            for c in lowered
            if c
        )
        qualified = (
            has_tax_label
            and has_total
            and (has_hsn or has_description_col)
            and (has_taxable or has_hsn)
        )
        if qualified:
            matches.append(t)
            if header_cells is None:
                header_cells = cells
    if not matches:
        return [], {}
    return matches, _map_gst_columns(header_cells)


def _map_gst_columns(header_cells: list) -> dict:
    """Map GST e-invoice header cells to canonical roles.

    Handles three layouts:

    - **Standard GST e-invoice** (Royal Canin / Mars / government schema):
      ``SI No | Item Description | HSN Code | Quantity-UOM | Item Rate |
      Gross Amount | Discount | Assessable Amount | GST Rate | CGST | SGST
      | IGST | Line Value``. A single ``GST Rate`` column is split into
      CGST+SGST halves downstream by :func:`_split_tax_rate`.
    - **Distributor tax invoice** (Atlas, 17-col): ``S No | Product Name
      | HSN Code | MNF B.Code | MRP | CS | EA | S Rate | Free | Gross
      Amt | Disc+Gst Benefit | CD/RD/WSH | GST% | GST Amt | SGST% | SGST
      Amt | Net Amt``. Here the bare ``GST%`` / ``GST Amt`` columns are
      really the CGST half (the writer dropped the ``C`` because SGST is
      listed separately at the same rate). Detected and remapped to
      ``cgst_rate`` / ``cgst_amount`` post-loop.
    - **Mars 2-row**: standard headers but each item spans two body rows
      (financial + descriptive). Descriptive row is merged in
      :func:`extract_gst_invoice_line_items`.

    When no explicit ``taxable`` / ``assessable`` column is present
    (Atlas), :func:`_new_gst_item` derives ``taxable_value`` as
    ``gross - discount``.
    """
    col = {}
    for i, raw in enumerate(header_cells):
        h = _norm_header(raw)
        if not h:
            continue
        # Identifier / description columns.
        if ("si" in h and "no" in h) or h == "sl no" or h == "s no" or h == "s. no" or h == "#":
            col.setdefault("sr_no", i)
        elif h == "no" or h == "no.":
            col.setdefault("sr_no", i)
        elif ("product" in h and "name" in h) or "product description" in h or "item description" in h or "description" in h or "item name" in h or h == "item" or h == "product" or h == "item details":
            col.setdefault("description", i)
        elif "hsn" in h or "sac" in h:
            col.setdefault("hsn_code", i)
        elif "is service" in h:
            col.setdefault("is_service", i)
        elif "mnf" in h or "batch" in h or "b.code" in h or "b code" in h:
            col.setdefault("batch", i)
        elif "ean" in h or "barcode" in h or "bar code" in h:
            col.setdefault("ean", i)
        elif "expiry" in h or "exp date" in h or "exp. date" in h:
            col.setdefault("expiry", i)
        elif "mrp" in h:
            col.setdefault("mrp", i)
        # Quantity columns.
        elif "quantity" in h and "uom" in h:
            col.setdefault("quantity_uom", i)
        elif "quantity" in h or h == "qty":
            col.setdefault("quantity", i)
        elif h == "cs" or "cases" in h or "case qty" in h or "no of cs" in h:
            col.setdefault("qty_cases", i)
        elif h == "ea" or h == "each" or "eaches" in h or "no of ea" in h:
            col.setdefault("qty_eaches", i)
        elif "uom" in h or h == "unit" or h == "uom:":
            col.setdefault("unit", i)
        elif h == "free":
            col.setdefault("free", i)
        # Rate columns.
        elif h in ("s rate", "s.rate", "sale rate", "rate", "item rate", "unit price", "net unit price"):
            if "rate" not in col:
                col["rate"] = i
        elif "net unit price" in h:
            col.setdefault("net_rate", i)
        # Money columns.
        elif "gross" in h:
            col.setdefault("gross", i)
        elif "disc" in h:
            col.setdefault("discount", i)
        elif "cd" in h and "rd" in h:
            col.setdefault("cd_rd_wsh", i)
        elif "assessable" in h or "taxable amount" in h or "taxable value" in h or h == "taxable amount":
            col.setdefault("taxable_value", i)
        # Tax-rate columns.
        elif h in ("cgst rate", "cgst %", "cgst%", "cgst% rate"):
            col.setdefault("cgst_rate", i)
        elif h in ("sgst rate", "sgst %", "sgst%", "sgst% rate"):
            col.setdefault("sgst_rate", i)
        elif h in ("igst rate", "igst %", "igst%", "igst% rate") or h == "igst %":
            col.setdefault("igst_rate", i)
        elif "gst rate" in h or "tax rate" in h or h == "tax %" or h == "tax%":
            col.setdefault("gst_rate", i)
        elif h in ("gst%", "gst %", "gst"):
            col.setdefault("gst_rate_ambig", i)
        # Tax-amount columns.
        elif h in ("cgst amt", "cgst amount", "cgst"):
            col.setdefault("cgst_amount", i)
        elif h in ("sgst amt", "sgst amount", "sgst"):
            col.setdefault("sgst_amount", i)
        elif h in ("igst amt", "igst amount", "igst"):
            col.setdefault("igst_amount", i)
        elif h in ("utgst amt", "utgst amount", "utgst"):
            col.setdefault("utgst_amount", i)
        elif h in ("gst amt", "gst amount"):
            col.setdefault("gst_amount_ambig", i)
        # Substring fallbacks for compound header names like
        # "SGST/UTGST Amount" or "IGST Amt" that the exact-match sets
        # above miss. Excludes rate columns ("%") so we don't double-map.
        elif "cgst" in h and "rate" not in h and "%" not in h:
            col.setdefault("cgst_amount", i)
        elif ("sgst" in h or "utgst" in h) and "rate" not in h and "%" not in h:
            col.setdefault("sgst_amount", i)
        elif "igst" in h and "rate" not in h and "%" not in h:
            col.setdefault("igst_amount", i)
        # Total / line-value column.
        elif ("line value" in h or "net amt" in h or "net amount" in h
              or "net value" in h or "total amount" in h or h == "amount"
              or h == "total"):
            col.setdefault("total_amount", i)

    # Atlas-style post-processing: bare "GST%" / "GST Amt" paired with
    # an SGST column (and no CGST) means CGST. Rename to the canonical
    # CGST slots so _new_gst_item reads them directly without splitting.
    if "sgst_amount" in col and "cgst_amount" not in col and "gst_amount_ambig" in col:
        col["cgst_amount"] = col.pop("gst_amount_ambig")
    if "sgst_amount" in col and "cgst_rate" not in col and "gst_rate_ambig" in col:
        col["cgst_rate"] = col.pop("gst_rate_ambig")

    # Drop any leftover ambiguous keys (shouldn't happen, but defensive).
    col.pop("gst_rate_ambig", None)
    col.pop("gst_amount_ambig", None)

    return col


def _split_qty_uom(cell: str) -> tuple:
    """Split a combined quantity/UOM cell.

    Handles:
    - ``"10.00 - UNT"`` (Mars / government schema)
    - ``"10 UNT"`` / ``"10.00 UNT"``
    - ``"1.00 Nos."`` / ``"1.00 Nos"`` (GNV distributor convention)
    - ``"10"`` (qty only, no unit)
    """
    if not cell:
        return None, None
    m = re.match(
        r"\s*(-?\d+(?:\.\d+)?)\s*[-]?\s*([A-Za-z][A-Za-z0-9]*)?",
        cell,
    )
    if not m:
        return None, None
    qty = float(m.group(1)) if m.group(1) else None
    unit = m.group(2) or None
    return qty, unit


# Regex for an HSN code embedded inside a description cell, e.g. GNV's
# ``<td>Acana Sport &amp; Agility<br/>HSN: 23091000</td>``.
_HSN_EMBEDDED_RE = re.compile(
    r"\bHSN\s*(?:code)?\s*[:]?\s*(\d[\d ]{3,}\d)",
    re.IGNORECASE,
)


def _split_tax_rate(rate: float, has_cgst: bool, has_sgst: bool, has_igst: bool) -> tuple:
    """Split a single GST rate into (cgst_rate, sgst_rate, igst_rate).

    For an intra-state transaction (CGST+SGST both present), each gets half.
    For an inter-state transaction (only IGST), IGST gets the full rate.
    """
    if rate is None:
        return None, None, None
    if has_igst and not has_cgst and not has_sgst:
        return None, None, rate
    if has_cgst or has_sgst:
        return rate / 2, rate / 2, None
    return None, None, None


def _new_gst_item(idx: int, cells: list, col: dict, has_cgst: bool, has_sgst: bool, has_igst: bool) -> dict:
    """Build a line-item dict from a GST e-invoice body row.

    When the table has explicit per-component rate columns (``cgst_rate``
    / ``sgst_rate`` / ``igst_rate``), they are read directly. Otherwise a
    single combined ``gst_rate`` column is split via
    :func:`_split_tax_rate`.

    When the table has no explicit ``taxable_value`` column (Atlas
    distributor layout), ``taxable_value`` is derived as
    ``gross - discount`` if both are present.
    """

    def get(role):
        i = col.get(role)
        if i is None or i >= len(cells):
            return ""
        return cells[i]

    # Quantity + UoM
    qty = None
    unit = None
    if "quantity_uom" in col:
        qty, unit = _split_qty_uom(get("quantity_uom"))
    elif "quantity" in col:
        # Separate Qty column. GNV-style cells like "1.00 Nos." carry
        # both the number and the unit. _split_qty_uom pulls both.
        qty, unit = _split_qty_uom(get("quantity"))
        if unit is None and "unit" in col:
            unit = get("unit") or None
    elif "qty_cases" in col or "qty_eaches" in col:
        # Atlas-style: CS + EA columns. Prefer cases as the billing unit;
        # fall back to eaches when no cases were sold for the line.
        cases_raw = get("qty_cases")
        eaches_raw = get("qty_eaches")
        cases = parse_amount(cases_raw) if cases_raw else None
        eaches = parse_amount(eaches_raw) if eaches_raw else None
        if cases and cases > 0:
            qty = cases
            unit = "CS"
        elif eaches and eaches > 0:
            qty = eaches
            unit = "EA"
        else:
            qty = cases or eaches
            unit = "CS" if cases else "EA"

    # Gross / discount
    gross = parse_amount(get("gross")) if "gross" in col and get("gross") else None
    discount = parse_amount(get("discount")) if "discount" in col and get("discount") else None

    # Taxable value: prefer explicit column, else derive from gross - discount.
    taxable_str = get("taxable_value") if "taxable_value" in col else ""
    taxable = parse_amount(taxable_str) if taxable_str else None
    if taxable is None and gross is not None:
        disc_val = discount if discount is not None else 0.0
        taxable = round(gross - disc_val, 2)

    # Rate
    rate = parse_amount(get("rate") or get("net_rate"))

    # Tax amounts
    cgst_amount = parse_amount(get("cgst_amount")) if "cgst_amount" in col else None
    sgst_amount = parse_amount(get("sgst_amount")) if "sgst_amount" in col else None
    igst_amount = parse_amount(get("igst_amount")) if "igst_amount" in col else None
    # Fold UTGST into SGST (no dedicated slot in the schema).
    utgst_amount = parse_amount(get("utgst_amount")) if "utgst_amount" in col else None
    if utgst_amount is not None and sgst_amount is None:
        sgst_amount = utgst_amount

    # Tax rates: prefer explicit per-component columns, else split combined.
    if "cgst_rate" in col or "sgst_rate" in col or "igst_rate" in col:
        cgst_rate = parse_pct(get("cgst_rate")) if "cgst_rate" in col else None
        sgst_rate = parse_pct(get("sgst_rate")) if "sgst_rate" in col else None
        igst_rate = parse_pct(get("igst_rate")) if "igst_rate" in col else None
    else:
        gst_rate = parse_pct(get("gst_rate")) if "gst_rate" in col else None
        cgst_rate, sgst_rate, igst_rate = _split_tax_rate(
            gst_rate,
            has_cgst=has_cgst or cgst_amount is not None,
            has_sgst=has_sgst or sgst_amount is not None,
            has_igst=has_igst or igst_amount is not None,
        )

    # Total
    total_amount = parse_amount(get("total_amount")) if "total_amount" in col else None

    # Description + HSN: some layouts (GNV) embed "HSN: 23091000" inside
    # the item-description cell, separated by <br/>. _row_cells converts
    # <br/> to "\n", so the cell reads "Item Name\nHSN: 23091000".
    # Split it out so the description is clean and the HSN populates
    # hsn_code (when no dedicated column exists).
    description = get("description") or None
    hsn_code = clean_hsn(get("hsn_code")) if get("hsn_code") else None
    if description:
        m = _HSN_EMBEDDED_RE.search(description)
        if m:
            embedded_hsn = m.group(1).replace(" ", "")
            description = (description[: m.start()] + description[m.end():]).strip(" \n-|")
            if not description:
                description = None
            if not hsn_code and embedded_hsn:
                hsn_code = embedded_hsn

    item = {
        "sr_no": idx,
        "description": description,
        "hsn_code": hsn_code,
        "quantity": qty,
        "unit": unit,
        "rate": rate,
        "discount": discount,
        "taxable_value": taxable,
        "cgst_rate": cgst_rate,
        "cgst_amount": cgst_amount,
        "sgst_rate": sgst_rate,
        "sgst_amount": sgst_amount,
        "igst_rate": igst_rate,
        "igst_amount": igst_amount,
        "total_amount": total_amount,
    }
    return item


def _is_description_continuation(cells: list, col: dict) -> bool:
    """Detect a Mars-style description continuation row.

    Layout: ``<td></td><td colspan="5">Product Description</td><td>BATCH
    </td><td>Expiry</td>...``. Such rows have no S.No / rate / assessable
    value / tax amounts — only a long text cell sitting where the
    description header was.

    Conservative: rejects rows that look like totals / IRN / amount-in-words
    so the parser doesn't mistake a total row for a description.
    """
    if not cells:
        return False
    # Must NOT have a taxable value or any tax amount.
    for role in ("taxable_value", "total_amount", "cgst_amount", "sgst_amount", "igst_amount"):
        idx = col.get(role)
        if idx is not None and idx < len(cells):
            if parse_amount(cells[idx]) is not None:
                return False
    # Reject rows that contain total / amount-in-words / IRN style labels.
    _REJECT_KEYWORDS = (
        "total", "chargeable", "round off", "rounding off", "subtotal",
        "sub total", "irn", "amount in words", "gst taxes", "grand total",
        "taxable amount", "assessable value", "tax payableable",
        "whether", "reverse charge", "yes/no", "subject to",
    )
    for c in cells:
        low = c.lower()
        for kw in _REJECT_KEYWORDS:
            if kw in low:
                return False
    # Find the longest non-empty text cell — if it's substantive (>10 chars)
    # and not a tax label, treat the row as a description continuation.
    longest = ""
    for c in cells:
        if not c or len(c) < 3:
            continue
        if c.upper().strip() in _TAX_TYPES:
            continue
        if len(c) > len(longest):
            longest = c
    return len(longest) >= 10


def extract_gst_invoice_line_items(doc) -> list:
    """Extract line items from a standard Indian GST e-invoice table.

    The GST e-invoice schema is a single-row-per-item table with 13+
    columns (SI No, Item Description, HSN Code, Quantity-UOM, Item Rate,
    Gross Amount, Discount, Assessable Amount, GST Rate, CGST, SGST,
    IGST, Line Value). No ``rowspan`` / sub-row pattern.

    Some layouts (Mars International) split each item across two body
    rows: a financial row with all numeric fields, and a descriptive
    row holding the product description in a ``colspan="5"`` cell. This
    extractor merges the descriptive row into the most recent item.

    Returns ``[]`` if no matching table is found, so callers can fall
    back to alternative extractors.
    """
    matches, col = _select_gst_invoice_tables(doc.tables)
    if not matches:
        return []

    has_cgst = "cgst_amount" in col
    has_sgst = "sgst_amount" in col
    has_igst = "igst_amount" in col

    items = []
    next_sr = 1
    for table in matches:
        for tr in table.find_all("tr"):
            if _is_header_row(tr):
                continue
            cells = _row_cells(tr)
            if not cells or _is_total_row(cells):
                continue

            # Description-continuation row: attach to most recent item.
            desc_idx = col.get("description")
            taxable_idx = col.get("taxable_value")
            has_taxable = (
                taxable_idx is not None
                and taxable_idx < len(cells)
                and parse_amount(cells[taxable_idx]) is not None
            )
            has_desc = (
                desc_idx is not None
                and desc_idx < len(cells)
                and cells[desc_idx].strip()
            )

            if not has_taxable and not has_desc and items:
                if _is_description_continuation(cells, col):
                    # Find the longest text cell as the description.
                    longest = max(cells, key=len) if cells else ""
                    if len(longest) >= 10:
                        items[-1]["description"] = longest.strip()
                    continue

            # Require a non-empty description or parseable taxable value
            # — filters out stray blank rows and total rows that slipped past.
            if not has_taxable and not has_desc:
                continue

            item = _new_gst_item(
                next_sr, cells, col, has_cgst, has_sgst, has_igst
            )
            items.append(item)
            next_sr += 1

    return items


# ---------------------------------------------------------------------------
# Public dispatch (per doc type)
# ---------------------------------------------------------------------------

def extract_line_items(doc, doc_type: str) -> list:
    """Dispatch line-item extraction by *doc_type*.

    Two layouts are supported:

    - **Amazon-style** ("Details of Fees" with ``rowspan`` parent rows and
      SGST/CGST sub-rows): used by Amazon Seller Services invoices and
      credit notes. Detected by ``Fee Amount`` + ``Tax Amount`` headers.
    - **GST e-invoice** (single row per item, 13-column layout with HSN +
      Quantity + Item Rate + Assessable Amount + GST Rate + CGST + SGST +
      Line Value): the Indian government-standard invoice schema used by
      most non-Amazon vendors. Detected by ``HSN`` + (``CGST`` or ``SGST``
      or ``IGST``) + (``Line Value`` or ``Assessable``).

    The dispatch tries the GST e-invoice extractor first because some
    Amazon invoices also have a per-line GST table later in the document
    (we want the canonical GST view, not the Amazon fee summary). Falls
    back to the Amazon details-table extractor if no GST e-invoice table
    is present.

    ``purchase_order`` and other layouts will be calibrated on their own
    fixtures.
    """
    if doc_type in ("credit_note", "invoice"):
        gst_items = extract_gst_invoice_line_items(doc)
        if gst_items:
            return gst_items
        return extract_credit_note_line_items(doc)
    if doc_type == "purchase_order":
        # TODO: calibrate when a PO fixture is inspected.
        logger.warning(
            "Line-item extraction for %r not yet calibrated — returning []",
            doc_type,
        )
        return []
    logger.warning("Unknown doc_type %r — returning []", doc_type)
    return []

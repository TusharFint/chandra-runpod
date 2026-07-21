"""Party (seller / buyer) extraction for Indian GST documents.

Calibrated on the Amazon Seller Services credit note layout
(``test/output/KA-C-27-124630.md``). Future fixtures may require new
branching rules — keep this module additive.
"""

import re
import logging

from .patterns import (
    GSTIN_RE,
    PAN_RE,
    EMAIL_RE,
    PHONE_RE,
    PINCODE_RE,
    parse_amount,
    normalize_country,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------------

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_TRAILING_SPACES_RE = re.compile(r"[ \t]{2,}$")  # markdown hard break


def _clean_line(line: str) -> str:
    """Strip markdown link syntax + trailing whitespace from a single line."""
    if not line:
        return ""
    line = _MARKDOWN_LINK_RE.sub(r"\1", line)
    return line.rstrip()


def _normalize_lines(block: str) -> list:
    """Split a text block into cleaned, non-empty lines."""
    out = []
    for raw in block.splitlines():
        ln = _clean_line(raw).strip()
        if ln:
            out.append(ln)
    return out


def _find_labelled(text: str, label_patterns) -> str:
    """Return the value following any of the *label_patterns* (case-insensitive).

    Markdown bold/italic markers (``**``, ``*``) are stripped from input
    text before matching, so patterns don't need to defend against
    ``**Label:** value`` shapes. Whitespace and trailing punctuation are
    also stripped from the captured value. Patterns are matched with
    ``re.MULTILINE`` so ``^...$`` anchors work line-wise.

    Patterns without a capture group fall back to the whole match
    (``m.group(0)``) so callers can pass bare regexes like
    ``r"[\\w.+-]+@[\\w.-]+\\.\\w+"`` for "find anywhere" semantics.
    """
    cleaned_text = re.sub(r"\*+", "", text or "")
    for pat in label_patterns:
        m = re.search(pat, cleaned_text, re.IGNORECASE | re.MULTILINE)
        if m:
            val = m.group(1) if m.groups() else m.group(0)
            val = val.strip().rstrip(",;").strip()
            if val:
                return val
    return None


# Lines that are *not* party content (doc headers, metadata fields).
_HEADER_NOISE_PATTERNS = [
    re.compile(r"^\s*#"),
    re.compile(r"^\s*\("),                          # (Original for Recipient)
    re.compile(r"^\s*(credit\s*note|tax\s*invoice|invoice|purchase\s*order)\b", re.I),
    re.compile(r"^\s*original\s+for\b", re.I),
    re.compile(r"^\s*irn", re.I),
    re.compile(r"^\s*(credit\s*note|invoice|purchase\s*order)\s*(date|number|no)", re.I),
    re.compile(r"^\s*(date|number|no)\s*:", re.I),
    re.compile(r"^\s*qr\s*code", re.I),
    re.compile(r"^\s*page\s+\d+", re.I),
    # Footnote / asterisk-led lines (escaped or unescaped). Catches the
    # Atlas-style "* With applicable GST rate reduction benefit" preface
    # without swallowing real bold names ("**Acme Pvt Ltd**").
    re.compile(r"^\s*\\?\*\s+\S"),
    # Bare "(E. & O.E.)" / "Subject to" disclaimer lines.
    re.compile(r"^\s*\(?\s*e\.?\s*&\s*o\.?\s*e\.?", re.I),
    re.compile(r"^\s*subject\s+to\b", re.I),
    re.compile(r"^\s*declaration\s*:", re.I),
    re.compile(r"^\s*customers?\s*signature", re.I),
    re.compile(r"^\s*for\s*:", re.I),
    re.compile(r"^\s*stock\s+received\b", re.I),
    re.compile(r"^\s*benefit\s+of\s+reduced", re.I),
    # OCR page markers like "H (8)" — single letter + (number).
    re.compile(r"^\s*[A-Za-z]\s*\(\d+\)\s*$"),
    # "Certified that the particular given above are true" footer.
    re.compile(r"^\s*certified\s+that\b", re.I),
    # Section-divider labels inside the buyer block.
    re.compile(r"^\s*details\s+of\s+(receiver|consignee)\b", re.I),
]


def _is_header_noise(line: str) -> bool:
    return any(p.search(line) for p in _HEADER_NOISE_PATTERNS)


# ---------------------------------------------------------------------------
# Seller block
# ---------------------------------------------------------------------------

def _strip_markdown_emphasis(s: str) -> str:
    """Strip surrounding ``**bold**`` / ``*italic*`` markers."""
    if not s:
        return s
    return re.sub(r"^\*+|\*+$", "", s).strip()


def _extract_seller(block: str) -> dict:
    """Build a seller dict from the text block above 'Bill to'."""
    lines = _normalize_lines(block)
    if not lines:
        return {}

    full_text = "\n".join(lines)

    # Identify the name line: first non-noise line.
    name = None
    name_idx = None
    for i, ln in enumerate(lines):
        if _is_header_noise(ln):
            continue
        # Skip labelled lines that belong to the metadata block.
        if re.match(r"^\s*(credit\s*note|invoice|order)\s*(date|number|no)", ln, re.I):
            continue
        if re.match(r"^\s*irn", ln, re.I):
            continue
        if re.match(r"^\s*(date|number|no|qr)\s*:", ln, re.I):
            continue
        # Skip section labels that may remain at the top of the block
        # (e.g. "Supplier :" with no name on the same line).
        if re.match(r"^\*{0,2}\s*(supplier|seller|vendor|consignor)\b\s*:?\s*\*{0,2}\s*$", ln, re.I):
            continue
        # Found the name line.
        name = _strip_markdown_emphasis(ln)
        name_idx = i
        break

    # Address = subsequent lines until we hit the PAN/GST/CIN labelled block.
    address_lines = []
    if name_idx is not None:
        for ln in lines[name_idx + 1:]:
            # Stop at any labelled contact / registration field — these
            # aren't part of the postal address.
            if re.match(r"^\s*(pan|gst|cin|tin|gstin|phone|mobile|telephone|"
                        r"contact|e-?mail|website)\b", ln, re.I):
                break
            # Stop at bare (unlabelled) contact-info lines too — many
            # distributor layouts list phone / email as standalone lines
            # after the address with no "Phone:" prefix.
            if EMAIL_RE.search(ln):
                break
            if re.match(r"^\s*\+?\d[\d\s\-().]{6,}\d\s*,?\s*$", ln):
                break
            if re.match(r"^\s*website\s*:", ln, re.I):
                continue  # skip website label
            cleaned = _strip_markdown_emphasis(ln)
            if cleaned:
                address_lines.append(cleaned)

    # Permissive GST regex: handles "GSTIN:", "GST Reg No.", "GSTIN No.",
    # "GST Tax Registration No", etc.
    gstin = (
        _find_labelled(
            full_text,
            [
                r"gst(?:in)?\s*(?:reg(?:istration)?)?\s*(?:tax\s*registration\s*)?(?:no\.?)?\s*[:\-]?\s*(\d{2}[A-Z]{5}\d{4}[A-Z]\w Z[A-Z\d])",
            ],
        )
        or _first_match(GSTIN_RE, full_text)
    )
    pan = (
        _find_labelled(full_text, [r"pan\s*(?:no)?\.?\s*[:\-]?\s*([A-Z]{5}\d{4}[A-Z])"])
        or _first_match(PAN_RE, full_text, exclude=gstin)
    )
    email = _find_labelled(
        full_text,
        [
            r"e-?mail\s*:\s*([\w.+-]+@[\w.-]+\.\w+)",
            r"[\w.+-]+@[\w.-]+\.\w+",
        ],
    )
    phone = _find_labelled(
        full_text,
        [
            r"(?:telephone|phone|mobile|contact)\s*(?:no)?\.?\s*[:\-]?\s*(\+?\d[\d\s\-().]{7,}\d)",
        ],
    )

    address = " ".join(address_lines).strip() or None
    return _build_party(
        name=name,
        gstin=gstin,
        pan=pan,
        address=address,
        email=email,
        phone=phone,
    )


# ---------------------------------------------------------------------------
# Buyer block
# ---------------------------------------------------------------------------

def _extract_buyer(block: str) -> dict:
    """Build a buyer dict from the text block following 'Bill to'."""
    lines = _normalize_lines(block)
    if not lines:
        return {}

    full_text = "\n".join(lines)

    name = _find_labelled(
        full_text,
        [
            r"^\s*name\s*[:\-]?\s*(.+)$",
            r"^\s*(?:buyer|customer|bill\s*to)\s*[:\-]?\s*(.+)$",
        ],
    )
    # Fallback: first non-noise, non-section-label line.
    if not name:
        for ln in lines:
            if _is_header_noise(ln):
                continue
            if re.match(r"^\s*(address|place\s*of\s*supply|gstin|state|pincode|country|bill\s*to|ship\s*to|phone|email)\b", ln, re.I):
                continue
            # Skip section labels standing alone ("Bill To :", "Buyer :", etc.)
            if re.match(r"^\*{0,2}\s*(bill\s*to|buyer|customer|sold\s*to|ship\s*to|shipping\s*address)\b\s*:?\s*\*{0,2}\s*$", ln, re.I):
                continue
            name = _strip_markdown_emphasis(ln)
            break

    # Address collection. Two strategies:
    #   (a) Explicit "Address:" label + continuation lines.
    #   (b) Fallback: lines after the name until a labelled field (GSTIN /
    #       Place of Supply / etc.) is hit.
    address_lines = []
    in_address = False
    name_seen = False
    for ln in lines:
        cleaned = _strip_markdown_emphasis(ln)
        if not cleaned:
            continue
        # Strategy (a): explicit Address: label.
        if re.match(r"^\s*address\s*:", ln, re.I):
            in_address = True
            addr_part = re.sub(r"^\s*address\s*:", "", ln, flags=re.I).strip()
            addr_part = _strip_markdown_emphasis(addr_part)
            if addr_part:
                address_lines.append(addr_part)
            continue
        if in_address:
            if re.match(r"^\s*(place\s*of\s*supply|gstin|state|pincode|country|phone|email|bill\s*to|ship\s*to|shipping\s*address|shipping\s*details|reason)\b", ln, re.I):
                in_address = False
                continue
            if cleaned:
                address_lines.append(cleaned)
            continue
        # Strategy (b): collect after name is seen.
        if name and cleaned == name:
            name_seen = True
            continue
        if name_seen:
            if re.match(r"^\s*(place\s*of\s*supply|gstin|state|pincode|country|phone|email|bill\s*to|ship\s*to|shipping\s*address|shipping\s*details|reason|pan|gst)\b", ln, re.I):
                name_seen = False
                continue
            # Skip section labels like "**Shipping Address:**" that might
            # remain after section splitting.
            if re.match(r"^\*{0,2}\s*(shipping\s*address|ship\s*to)\b\s*:?\s*\*{0,2}\s*$", ln, re.I):
                name_seen = False
                continue
            # Skip lone contact-name lines (e.g. "Varun") at the end.
            if len(cleaned.split()) <= 2 and not re.search(r"\d", cleaned):
                continue
            address_lines.append(cleaned)

    address = " ".join(address_lines).strip() or None

    gstin = (
        _find_labelled(
            full_text,
            [
                r"gst(?:in)?\s*/?\s*u?in?\s*(?:no\.?)?\s*[:\-]?\s*(\d{2}[A-Z]{5}\d{4}[A-Z]\w Z[A-Z\d])",
                r"gst(?:in)?\s*(?:reg(?:istration)?)?\s*(?:no\.?)?\s*[:\-]?\s*(\d{2}[A-Z]{5}\d{4}[A-Z]\w Z[A-Z\d])",
            ],
        )
        or _first_match(GSTIN_RE, full_text)
    )
    pan = (
        _find_labelled(full_text, [r"pan\s*(?:no)?\.?\s*[:\-]?\s*([A-Z]{5}\d{4}[A-Z])"])
        or _first_match(PAN_RE, full_text, exclude=gstin)
    )
    email = _find_labelled(
        full_text,
        [
            r"e-?mail\s*:\s*([\w.+-]+@[\w.-]+\.\w+)",
        ],
    )
    phone = _find_labelled(
        full_text,
        [
            r"(?:telephone|phone|mobile|contact)\s*(?:no)?\.?\s*[:\-]?\s*(\+?\d[\d\s\-().]{7,}\d)",
        ],
    )

    billing = _find_labelled(full_text, [r"billing\s*address\s*:\s*(.+)$"]) or address
    shipping = (
        _find_labelled(full_text, [r"ship(?:ping|ped)?\s*(?:to|address)\s*:[ \t]*([^\n]+)"])
        or _find_labelled(full_text, [r"delivery\s*address\s*:[ \t]*([^\n]+)"])
    )

    return _build_party(
        name=name,
        gstin=gstin,
        pan=pan,
        address=address,
        email=email,
        phone=phone,
        billing_address=billing,
        shipping_address=shipping,
    )


# ---------------------------------------------------------------------------
# Shared party assembler
# ---------------------------------------------------------------------------

_STATE_NAMES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand",
    "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan",
    "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh",
    "Uttarakhand", "West Bengal", "Delhi", "Jammu", "Kashmir", "Ladakh",
    "Chandigarh", "Puducherry",
]


def _extract_state(address: str) -> str:
    if not address:
        return None
    low = address.lower()
    for st in _STATE_NAMES:
        if st.lower() in low:
            return st
    # Uppercase variant (e.g. "KARNATAKA")
    up = address.upper()
    for st in _STATE_NAMES:
        if st.upper() in up:
            return st.upper()
    return None


def _extract_pincode(address: str) -> str:
    if not address:
        return None
    m = PINCODE_RE.search(address)
    return m.group(1) if m else None


def _extract_country(address: str):
    if not address:
        return None
    # Take the last comma-separated chunk, normalize via patterns.normalize_country
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return None
    last = parts[-1]
    return normalize_country(last)


def _build_party(*, name, gstin, pan, address, email, phone,
                 billing_address=None, shipping_address=None) -> dict:
    state = _extract_state(address) if address else None
    pincode = _extract_pincode(address) if address else None
    country = _extract_country(address) if address else None

    party = {
        "name": name,
        "gstin": gstin,
        "pan": pan,
        "address": address,
        "state": state,
        "pincode": pincode,
        "country": country,
        "phone": phone,
        "email": email,
    }
    if billing_address is not None:
        party["billing_address"] = billing_address
    if shipping_address is not None:
        party["shipping_address"] = shipping_address
    return party


def _first_match(regex, text, exclude=None):
    """Return the first regex hit not contained in *exclude* (e.g. a GSTIN)."""
    for m in regex.finditer(text or ""):
        val = m.group(0)
        if exclude and val in exclude:
            continue
        return val
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Recognized party-section labels. Each entry is (regex, role) where role
# is "buyer" or "seller". Matching is line-anchored. Labels can be
# followed by trailing tokens (e.g. "Bill To 17593781" — an order/account
# number appended to the label).
_PARTY_LABELS = [
    # Buyer-side
    (re.compile(r"^\s*\*{0,2}\s*bill\s*to\b(?::|s|ed)?\s*\d*\s*:?\s*\*{0,2}\s*$", re.IGNORECASE | re.MULTILINE), "buyer"),
    (re.compile(r"^\s*\*{0,2}\s*bill\s*to\b\s+\S.*$", re.IGNORECASE | re.MULTILINE), "buyer"),
    (re.compile(r"^\s*\*{0,2}\s*sold\s*to\b.*$", re.IGNORECASE | re.MULTILINE), "buyer"),
    (re.compile(r"^\s*\*{0,2}\s*(?:buyer|customer)\s*(?:details)?\b\s*:?\s*\*{0,2}\s*$", re.IGNORECASE | re.MULTILINE), "buyer"),
    (re.compile(r"^\s*\*{0,2}\s*deliver\s*to\b.*$", re.IGNORECASE | re.MULTILINE), "buyer"),
    # Seller-side
    (re.compile(r"^\s*\*{0,2}\s*supplier\b\s*:?\s*\*{0,2}\s*$", re.IGNORECASE | re.MULTILINE), "seller"),
    (re.compile(r"^\s*\*{0,2}\s*seller\s*(?:details)?\b\s*:?\s*\*{0,2}\s*$", re.IGNORECASE | re.MULTILINE), "seller"),
    (re.compile(r"^\s*\*{0,2}\s*vendor\b\s*:?\s*\*{0,2}\s*$", re.IGNORECASE | re.MULTILINE), "seller"),
    (re.compile(r"^\s*\*{0,2}\s*consignor\b\s*:?\s*\*{0,2}\s*$", re.IGNORECASE | re.MULTILINE), "seller"),
]

# Boundaries that terminate a party block (next major section / table /
# sub-section within the same party — e.g. "Shipping Address" inside the
# buyer block).
_BLOCK_BOUNDARY_RE = re.compile(
    r"<table|\n#{2,6}\s|reason\s*for\s*credit|amount\s*charg"
    r"|^\*{0,2}\s*shipping\s*address\b\s*:?\s*\*{0,2}\s*$"
    r"|^\*{0,2}\s*ship\s*to\b\s*:?\s*\*{0,2}\s*$"
    r"|^\*{0,2}\s*dispatch\s*details\b\s*:?\s*\*{0,2}\s*$"
    r"|^\*{0,2}\s*buyer\s*details\b\s*:?\s*\*{0,2}\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _find_party_sections(text: str) -> list:
    """Return a list of ``(role, block_text)`` for each labelled party section.

    Block text extends from the end of the label line to the next label or
    to the next structural boundary (table, header, "Amount Chargable").
    """
    matches = []
    for regex, role in _PARTY_LABELS:
        for m in regex.finditer(text):
            matches.append((m.start(), m.end(), role))
    if not matches:
        return []
    matches.sort(key=lambda t: t[0])

    sections = []
    for i, (start, end, role) in enumerate(matches):
        next_start = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        block = text[end:next_start]
        # Truncate at first structural boundary (table, header, etc.)
        b = _BLOCK_BOUNDARY_RE.search(block)
        if b:
            block = block[: b.start()]
        sections.append((role, block))
    return sections


_BILL_TO_SPLIT_RE = re.compile(
    r"^\s*#{0,6}\s*(?:bill\s*to|buyer|customer|ship\s*to)\b",
    re.IGNORECASE | re.MULTILINE,
)

# Atlas / distributor-style: a bold "**Tax Invoice**" / "**Credit Note**"
# / "**Purchase Order**" header sitting between the seller block (above)
# and the buyer block (below). Acts as a fallback split point when no
# explicit "Bill To" / "Buyer" labels are present.
_DOC_TYPE_HEADER_SPLIT_RE = re.compile(
    r"^\s*\*{2}\s*(?:tax\s*invoice|credit\s*note|purchase\s*order|taxinvoice)"
    r"\s*\*{0,2}\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def extract_parties(doc) -> tuple:
    """Return ``(seller, buyer)`` dicts for a :class:`ChandraDoc`.

    Three strategies are tried in order:

    1. **Labelled sections**: if the document has explicit ``Bill To`` /
       ``Supplier`` / ``Seller`` / ``Buyer`` labels (Royal Canin, Mars,
       most non-Amazon Indian GST invoices), each block is extracted and
       categorised by its label. When buyer labels exist but no explicit
       seller label does, the text *before* the first buyer label is
       treated as the seller block.
    2. **Doc-type header split**: if no labels are present but a bold
       ``**Tax Invoice**`` / ``**Credit Note**`` / ``**Purchase Order**``
       header sits between the two party blocks (Atlas distributor
       layout), split there. Seller = above, buyer = below.
    3. **Amazon fallback**: if no explicit labels are present, assume the
       seller block sits above the first ``Bill to`` / ``Buyer`` header
       and the buyer block follows it. This is the Amazon Seller Services
       layout used by the first calibration fixtures.
    """
    text = doc.text

    sections = _find_party_sections(text)
    if sections:
        seller = {}
        buyer = {}
        for role, block in sections:
            if role == "seller" and not seller:
                seller = _extract_seller(block)
            elif role == "buyer" and not buyer:
                buyer = _extract_buyer(block)
        # Fallback: if no seller section was found but we did find a buyer
        # section, treat everything *before* the first buyer label as the
        # seller block (Mars International, many vendor-templates-first
        # layouts).
        if not seller and buyer:
            first_label_match = next(
                (
                    m for regex, _ in _PARTY_LABELS
                    for m in regex.finditer(text)
                ),
                None,
            )
            if first_label_match:
                seller = _extract_seller(text[: first_label_match.start()])
        _apply_footer_fallback(doc, seller, buyer)
        return seller, buyer

    # Distributor-style fallback: split at a bold "**Tax Invoice**" /
    # "**Credit Note**" header (Atlas layout: seller above, buyer below).
    m = _DOC_TYPE_HEADER_SPLIT_RE.search(text)
    if m:
        seller_block = text[: m.start()]
        buyer_block = text[m.end():]
        # Truncate buyer at first table / header / structural boundary.
        buyer_block = re.split(
            r"<table|\n#{2,3}\s|stock\s+received|in\s+words\s*:",
            buyer_block, 1, re.IGNORECASE,
        )[0]
        seller = _extract_seller(seller_block)
        buyer = _extract_buyer(buyer_block)
        _apply_footer_fallback(doc, seller, buyer)
        return seller, buyer

    # Amazon-style fallback: split at first "Bill to".
    m = _BILL_TO_SPLIT_RE.search(text)
    if m:
        seller_block = text[: m.start()]
        buyer_block = text[m.end():]
        buyer_block = re.split(r"<table|\n#{2,3}\s|reason\s*for\s*credit", buyer_block, 1, re.I)[0]
    else:
        seller_block = text
        buyer_block = ""

    seller = _extract_seller(seller_block)
    buyer = _extract_buyer(buyer_block)
    _apply_footer_fallback(doc, seller, buyer)
    return seller, buyer


def _apply_footer_fallback(doc, seller: dict, buyer: dict) -> None:
    """Pull seller phone/email from the footer if missing.

    Indian GST docs typically put the seller's contact info (telephone /
    email / regd office) in the last-page footer, which is *below* the
    party blocks. If the seller block didn't surface its own contact
    info, scan the whole document as a fallback — but only assign when
    the buyer doesn't already have it (the buyer block rarely carries
    phone/email for these layouts).
    """
    if not seller or (seller.get("phone") and seller.get("email")):
        return
    full = _MARKDOWN_LINK_RE.sub(r"\1", doc.full_text())
    if not seller.get("phone"):
        phone = _find_labelled(
            full,
            [r"(?:telephone|phone|mobile|contact)\s*:\s*(\+?\d[\d\s\-().]{7,}\d)"],
        )
        if phone and not buyer.get("phone"):
            seller["phone"] = phone
    if not seller.get("email"):
        m = EMAIL_RE.search(full)
        if m and not buyer.get("email"):
            seller["email"] = m.group(0)

Your task is to extract structured invoice data from HTML marker format and convert it to a JSON schema matching the provided invoice schema.
Instructions:
Extract complete invoice information from the HTML marker data including:
1. Invoice metadata (invoice_number, invoice_id, invoice_date, invoice_value, adjustment, bill_discount, order_number, order_date, make sure to format dates in DD-MM-YYYY format for consistency)
2. Purchase order details (if present)
3. Seller information (name, GST, PAN, address, state, pincode, country, bank_details, contact_details)
4. Buyer information (name, GST, PAN, address, state, pincode, country, contact_details, billing_address, shipping_address)
5. Place of supply and delivery information
6. RCM (Reverse Charge Mechanism) details
7. Line items with complete details (sl.no, hsn, description, unit_price, qty, net_amount, tax information, discounts, total_amount)
8. Transaction and payment details

Parse all HTML content from all pages to extract complete invoice data.
Handle multi-page documents by merging information across pages.
Extract actual values from HTML elements, tables, and text blocks.
Apply domain-specific logic for Indian GST invoices.
Output a complete JSON object matching the provided schema structure.

**NON-HALLUCINATION RULE**: Never invent rows or cell values that are not visible in either the markdown table or the corresponding crop image. If a value is unclear or unreadable in the markdown, read it from the crop image. If it is unreadable in both markdown and crop image, set that field to `null` instead of guessing.

MISSING VALUES: For any field that is not present, not visible, not applicable, or contains placeholder text (e.g., "Not specified", "NA", "N/A", "N.A.", "-", "NIL", "—", "--") — output null. This applies to ALL fields (metadata, line items, nested objects) regardless of type. Never output empty strings ("") for missing fields. Never output placeholder text as values. Never omit a schema field from the output.

HSN/SAC EXTRACTION RULE: When reading the HSN/SAC cell, read the ENTIRE cell contents. If the cell contains digit fragments separated by `<br/>`, newlines, or whitespace (e.g., `<td>8504909<br/>0</td>`), CONCATENATE ALL digit fragments into a single continuous HSN string — do NOT treat `<br/>` as a row or value separator, and do NOT keep only the first fragment. Then strip any non-numeric characters ('.', spaces, '-', '/'). Output the result as a string (identifier, not a number). Example: `<td>8504909<br/>0</td>` → "85049090".

**MULTI-PAGE LINE ITEM EXTRACTION RULE:**
Parse ALL pages. Each page may contain continuation of line items.
- Count line items across EVERY page table. Your output MUST contain items from ALL pages.
- Sum of ALL total_amount values across ALL extracted line items MUST equal invoice_value.
- If your extracted items sum to less than invoice_value, you are MISSING rows from later pages. Re-scan those pages and extract the missing rows.
- Before final output, verify: sum(total_amount) == invoice_value. If not, go back and find the missing rows.
- NEVER output only page 1 items when the document has multiple pages.
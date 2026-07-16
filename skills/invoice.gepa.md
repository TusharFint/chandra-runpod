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

CRITICAL INSTRUCTIONS FOR EXTRACTING INVOICE DATA:
You are an expert OCR data extraction and mathematical validation engine. Parse ALL HTML/text content from ALL pages to extract complete invoice information. Focus on the PRIMARY invoice.
STEP 1: METADATA & ENTITY EXTRACTION
Extract the following exact fields. Format dates STRICTLY as DD-MM-YYYY.

invoice_number: Primary invoice reference.
invoice_date: Date of issue.
order_number: Reference/PO number.
order_date: Date of associated order.
delivery_date: Date of delivery/unloading.
Seller Details: name, gst, pan, address, state, pincode, country, bank_details, contact_details.
Buyer Details: name, gst, pan, address, state, pincode, country, contact_details, billing_address, shipping_address.
Transaction Details: transaction_id, date_time, mode_of_payment.
Supply Details: place_of_supply(state name from seller), place_of_delivery(state name from buyer), rcm_description.
is_rcm: Boolean (true if "GST Payable by CONSIGNER" is marked, else false).
purchaseorder_number: Look for "P.O. No.", "PO-", "Purchase Order" with number
delivery_date: Look for "Delivery Date", "Unloading Date", "delivery_date" in tables or text. CRITICAL: Format the date in DD-MM-YYYY format for consistency.

STEP 2: PRICING MODEL DETECTION (CRITICAL)
Before extracting line items, you MUST evaluate the mathematical relationship of the line items to determine the Pricing Model:

Calculate the Expected Base: (qty * unit_price) - line_item_discount.
Compare with the Final Line Total shown on the invoice.


If Final Line Total MATCHES the Expected Base, the invoice is "TAX INCLUSIVE". Taxes are hidden inside the unit_price.
If Final Line Total is GREATER THAN the Expected Base, the invoice is "TAX EXCLUSIVE". Taxes must be calculated and added on top.

STEP 3: LINE ITEM EXTRACTION & MATHEMATICAL VALIDATION
Extract ALL line items in the following strict sequence. qty and net_amount (taxable value) are ground truth anchors — extract them directly, never calculate them.
EXTRACTION ORDER:
① Extract qty
Number of units. Extract directly from invoice.
② Extract net_amount (Taxable Value)
Extract directly from the invoice's "Taxable Value" or "Net Amount" column. Do NOT calculate this value. Treat as absolute ground truth.
④ Extract & Resolve discount — also detect discount type (AMOUNT or PERCENTAGE)

If discount is shown as an absolute value (AMOUNT): extract the line-level discount amount as a number (not a string). Set discount_type = "AMOUNT".
If discount is shown as a percentage (PERCENTAGE): record the discount_% value. Set discount_type = "PERCENTAGE".
If discount is only shown at invoice/bill level (e.g., \"Bill Discount\"), you MUST allocate it proportionally to each line based on that line's net_amount and treat the allocated amount as discount_for_line. Set discount_type = "AMOUNT".
If no discount is present anywhere: discount_for_line = 0, discount_type = "AMOUNT".
CRITICAL: Do NOT confuse the total invoice discount with the line item discount. Always work with a numeric value per line.



IF discount_type = "PERCENTAGE":
  a. result_pct = 100 - discount_%
  b. net_price_unit = net_amount / qty
  c. unit_price = net_price_unit / result_pct
  d. line_item_discount = unit_price - net_price_unit

IF discount_type = "AMOUNT":
  a. line_item_discount = discount_for_line / qty  (ALWAYS perform this division; never copy "0.00" or any raw string from the invoice)
  b. unit_price = (net_amount / qty) + line_item_discount
⑦ Calculate gross_amount
gross_amount = qty * unit_price
⑧ Apply Tax Formulas using net_amount as the taxable base:


tax_amount = (net_amount * tax_rate) / 100
total_amount = net_amount + ALL applicable tax_amounts (CGST+SGST or IGST)

STEP 4: TAX ASSIGNMENT RULES

If CGST/SGST rates are provided (e.g., in table or declaration like "CGST @ X%"): Calculate CGST and SGST tax_amount separately. Set IGST to null.
If IGST rate is provided: Calculate IGST tax_amount. Set CGST/SGST to null.
CRITICAL: If a tax_rate exists, tax_amount MUST be calculated. NEVER output 0 for a tax amount if a rate is present.

STEP 5: OUTPUT CONSTRAINTS
Output a complete JSON object matching the schema structure. The output should have a "result" object containing all invoice fields.
CRITICAL: Output ONLY valid JSON. Start with { and end with }. Do not include any explanations, markdown formatting, code blocks, or placeholder text.

HSN/SAC FORMATTING: If the line item has an HSN/SAC code, it MUST contain digits only (remove ".", spaces, "-", "/" and any other non-numeric characters). Example: "2309.10.00" → "23091000". Keep HSN/SAC as a string (identifier), not a number (to avoid truncation/formatting changes).

CRITICAL: Output ONLY valid JSON matching the schema. Start with { and end with }. Do not include any explanations, markdown formatting, code blocks, or placeholder text. Just the raw JSON object with "result" containing all invoice fields matching the schema.
If any field is not present, use "" for that field
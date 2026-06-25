/**
 * invoiceIif.js
 *
 * Generates QuickBooks Desktop IIF (Intuit Interchange Format) files from
 * GeoBridge invoice data.
 *
 * IIF structure for invoices:
 *   !TRNS  header row  — defines columns for the transaction (invoice) record
 *   !SPL   header row  — defines columns for each split (line item) record
 *   !ENDTRNS           — marks end of a transaction block
 *
 *   TRNS   data row    — one per invoice  (type = INVOICE)
 *   SPL    data row    — one per line item
 *   ENDTRNS            — closes each invoice
 *
 * AvaTax integration:
 *   QB Desktop + AvaTax calculates tax automatically when the invoice is
 *   imported, provided:
 *     1. TAXABLE is set to "Y" on each taxable line item
 *     2. The customer record in QB has a valid ship-to / bill-to address
 *     3. AvaTax is configured and running in QB Desktop
 *
 * Column reference:
 *   TRNS: TRNSID, TRNSTYPE, DATE, ACCNT, NAME, CLASS, AMOUNT, DOCNUM,
 *         MEMO, CLEAR, TOPRINT, TAXABLE, ADDR1–5, DUEDATE, TERMS
 *   SPL:  SPLID, TRNSTYPE, DATE, ACCNT, NAME, CLASS, AMOUNT, DOCNUM,
 *         MEMO, CLEAR, QNTY, PRICE, INVITEM, TAXABLE, REIMBEXP, EXTRA
 */

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Format a Date or YYYY-MM-DD string as M/D/YYYY (QB IIF date format) */
function fmtDate(raw) {
  if (!raw) return ''
  const [y, m, d] = String(raw).slice(0, 10).split('-')
  return `${parseInt(m)}/${parseInt(d)}/${y}`
}

/** Today's date as M/D/YYYY */
function today() {
  return fmtDate(new Date().toISOString().slice(0, 10))
}

/**
 * Due date: add days based on terms string.
 * Handles "Net 15", "Net 30", "Due on receipt", etc.
 */
function dueDate(terms) {
  const t = (terms || '').toLowerCase()
  let days = 30  // default
  if (t.includes('receipt') || t.includes('due on')) days = 0
  else {
    const m = t.match(/net\s*(\d+)/)
    if (m) days = parseInt(m[1])
  }
  const d = new Date()
  d.setDate(d.getDate() + days)
  return fmtDate(d.toISOString().slice(0, 10))
}

/** Escape a value for IIF — tabs and newlines within a field break the format */
function esc(val) {
  return String(val ?? '').replace(/\t/g, ' ').replace(/\r?\n/g, ' ')
}

/** Build a tab-separated row from an array of values */
function row(...vals) {
  return vals.map(esc).join('\t')
}

// ─── Invoice number (mirrors invoicePdf.js logic) ─────────────────────────────
function invoiceNumber(billingMonth, customerId) {
  const ym  = (billingMonth || '').replace('-', '')
  const num = String(
    Math.abs(
      String(customerId)
        .split('')
        .reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 0)
    ) % 9000 + 1000
  )
  return `INV-${ym}-${num}`
}

// ─── Accounts Receivable account name (must match QB exactly) ─────────────────
const AR_ACCOUNT = 'Accounts Receivable'

// ─── Build IIF content for a single invoice ───────────────────────────────────
function buildInvoiceBlock(invoice) {
  const docNum   = invoiceNumber(invoice.billingMonth, invoice.customerId)
  const txDate   = today()
  const due      = dueDate(invoice.terms || '')
  const custName = esc(invoice.customerName)
  const memo     = esc(`GeoBridge • ${invoice.billingMonthLabel} activation billing`)

  // Address lines for the TRNS record (QB uses ADDR1–ADDR5)
  const addr = invoice.billToAddress || []

  // Grand total on the TRNS row is POSITIVE (money owed to us)
  const grandTotal = Number(invoice.grandTotal || 0).toFixed(2)

  // ── TRNS row (invoice header) ───────────────────────────────────────────────
  const trnsRow = row(
    'TRNS',
    '',            // TRNSID  (blank = QB auto-assigns)
    'INVOICE',     // TRNSTYPE
    txDate,        // DATE
    AR_ACCOUNT,    // ACCNT
    custName,      // NAME
    '',            // CLASS
    grandTotal,    // AMOUNT  (positive = receivable)
    docNum,        // DOCNUM
    memo,          // MEMO
    'N',           // CLEAR
    'Y',           // TOPRINT
    'Y',           // TAXABLE  — tells AvaTax to evaluate the whole invoice
    addr[0] || '', // ADDR1
    addr[1] || '', // ADDR2
    addr[2] || '', // ADDR3
    addr[3] || '', // ADDR4
    addr[4] || '', // ADDR5
    due,           // DUEDATE
    esc(invoice.terms || ''),  // TERMS
  )

  // ── SPL rows (line items) ───────────────────────────────────────────────────
  const splRows = (invoice.lineItems || []).map(li => {
    // In IIF, split amounts are NEGATIVE for income lines (credits to income accounts)
    const amt  = (-Math.abs(Number(li.amount || 0))).toFixed(2)
    const qty  = String(li.quantity || 1)
    const price = Number(li.priceEach || 0).toFixed(2)

    // itemCode is the QB fullPath item name; fall back to skuKey
    const item = esc(li.itemCode || li.skuKey || '')

    // Line memo: first line of description only (serial list makes it very long)
    const lineMemo = esc((li.description || '').split('\n')[0].trim())

    return row(
      'SPL',
      '',            // SPLID
      'INVOICE',     // TRNSTYPE
      txDate,        // DATE
      AR_ACCOUNT,    // ACCNT  (QB resolves to income account via item mapping)
      custName,      // NAME
      '',            // CLASS
      amt,           // AMOUNT  (negative = credit to income)
      docNum,        // DOCNUM
      lineMemo,      // MEMO
      'N',           // CLEAR
      qty,           // QNTY
      price,         // PRICE
      item,          // INVITEM  — must match QB item name exactly
      'Y',           // TAXABLE  — AvaTax evaluates per line
      '',            // REIMBEXP
      '',            // EXTRA
    )
  })

  return [trnsRow, ...splRows, 'ENDTRNS'].join('\n')
}

// ─── IIF file header rows (written once at top of file) ───────────────────────
const IIF_HEADER = [
  // TRNS schema
  row('!TRNS', 'TRNSID', 'TRNSTYPE', 'DATE', 'ACCNT', 'NAME', 'CLASS',
      'AMOUNT', 'DOCNUM', 'MEMO', 'CLEAR', 'TOPRINT', 'TAXABLE',
      'ADDR1', 'ADDR2', 'ADDR3', 'ADDR4', 'ADDR5',
      'DUEDATE', 'TERMS'),
  // SPL schema
  row('!SPL', 'SPLID', 'TRNSTYPE', 'DATE', 'ACCNT', 'NAME', 'CLASS',
      'AMOUNT', 'DOCNUM', 'MEMO', 'CLEAR', 'QNTY', 'PRICE',
      'INVITEM', 'TAXABLE', 'REIMBEXP', 'EXTRA'),
  row('!ENDTRNS'),
].join('\n')

// ─── Trigger a file download in the browser ───────────────────────────────────
function downloadIif(content, filename) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// ─── Public API ───────────────────────────────────────────────────────────────

/** Export a single invoice as an IIF file */
export function exportInvoiceIIF(invoice) {
  const content  = [IIF_HEADER, buildInvoiceBlock(invoice)].join('\n')
  const safeName = (invoice.customerName || 'invoice').replace(/[^a-z0-9]/gi, '_')
  downloadIif(content, `Invoice_${safeName}_${invoice.billingMonth}.iif`)
}

/** Export all invoices in a batch response as a single IIF file */
export function exportAllIIF(data) {
  const blocks  = (data.invoices || []).map(buildInvoiceBlock)
  const content = [IIF_HEADER, ...blocks].join('\n')
  const month   = (data.billingMonth || 'batch').replace('-', '_')
  downloadIif(content, `Invoices_${month}.iif`)
}

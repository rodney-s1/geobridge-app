import { jsPDF } from 'jspdf'
import autoTable from 'jspdf-autotable'
import LOGO_BASE64 from './logoBase64'

// ─── Colours (Blue Arrow brand) ───────────────────────────────────────────────
const NAVY   = [30,  45,  90]   // #1E2D5A  deep navy header
const BLUE   = [52,  96, 171]   // #3460AB  Blue Arrow blue
const LTBLUE = [235, 241, 251]  // #EBF1FB  light blue bg for section headers
const AMBER  = [180, 120,  10]  // amber for prorated section header
const GREEN  = [30,  130,  80]  // green for forward section header
const GRAY   = [100, 110, 125]  // body text grey
const LGRAY  = [230, 232, 236]  // light grey rule / table stripe
const WHITE  = [255, 255, 255]
const BLACK  = [20,  20,  20]
// Han-CS section colours (distinct from standard HANOVER)
const HANCS_PROT_BG = [230, 240, 255]  // soft indigo for Han-CS prorated bg
const HANCS_FWD_BG  = [235, 255, 245]  // soft teal for Han-CS forward bg

// ─── Company info ─────────────────────────────────────────────────────────────
const COMPANY = {
  name:    'Blue Arrow Telematics',
  addr1:   '',           // add street if desired
  addr2:   '',           // city, state, zip
  phone:   '',
  email:   '',
  website: 'bluearrowtelematics.com',
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function money(n) {
  if (n == null) return '—'
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function pct(f) {
  if (f == null) return '—'
  return (f * 100).toFixed(1) + '%'
}
function todayStr() {
  return new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
}
// Simple sequential invoice number: INV-YYYYMM-NNNN (NNNN = hash of customer id)
function invoiceNumber(billingMonth, customerId) {
  const ym  = billingMonth.replace('-', '')
  const num = String(Math.abs(customerId.split('').reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 0)) % 9000 + 1000)
  return `INV-${ym}-${num}`
}

// ─── Draw the letterhead ──────────────────────────────────────────────────────
function drawHeader(doc, invoice) {
  const W = doc.internal.pageSize.getWidth()

  // Navy top bar
  doc.setFillColor(...NAVY)
  doc.rect(0, 0, W, 28, 'F')

  // Logo (white background area not needed — logo has transparent bg)
  try {
    doc.addImage(LOGO_BASE64, 'PNG', 10, 3, 55, 22)
  } catch (_) { /* skip if broken */ }

  // "INVOICE" title in header bar
  doc.setFont('helvetica', 'bold')
  doc.setFontSize(22)
  doc.setTextColor(...WHITE)
  doc.text('INVOICE', W - 14, 18, { align: 'right' })

  // Blue rule below header
  doc.setDrawColor(...BLUE)
  doc.setLineWidth(0.8)
  doc.line(0, 28, W, 28)

  // Company info block (left, below header)
  let y = 36
  doc.setFontSize(9)
  doc.setFont('helvetica', 'bold')
  doc.setTextColor(...NAVY)
  doc.text(COMPANY.name, 14, y)
  y += 5
  doc.setFont('helvetica', 'normal')
  doc.setTextColor(...GRAY)
  if (COMPANY.addr1) { doc.text(COMPANY.addr1, 14, y); y += 4.5 }
  if (COMPANY.addr2) { doc.text(COMPANY.addr2, 14, y); y += 4.5 }
  if (COMPANY.phone) { doc.text(`Tel: ${COMPANY.phone}`,  14, y); y += 4.5 }
  if (COMPANY.email) { doc.text(`Email: ${COMPANY.email}`, 14, y); y += 4.5 }
  if (COMPANY.website) { doc.text(COMPANY.website, 14, y) }

  // Invoice meta box (right side) — 3 rows, no Invoice Type
  // Billing Period = nextMonthLabel because we bill one month in advance
  const boxX = W - 90
  const boxW = 76
  doc.setFillColor(...LTBLUE)
  doc.roundedRect(boxX, 32, boxW, 28, 2, 2, 'F')
  doc.setDrawColor(...BLUE)
  doc.setLineWidth(0.3)
  doc.roundedRect(boxX, 32, boxW, 28, 2, 2, 'S')

  const metaRows = [
    ['Invoice #:',      invoiceNumber(invoice.billingMonth, invoice.customerId)],
    ['Date Issued:',    todayStr()],
    ['Billing Period:', invoice.nextMonthLabel],
  ]
  let my = 38
  for (const [label, val] of metaRows) {
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(8)
    doc.setTextColor(...GRAY)
    doc.text(label, boxX + 4, my)
    doc.setFont('helvetica', 'normal')
    doc.setTextColor(...BLACK)
    doc.text(val, boxX + boxW - 4, my, { align: 'right', maxWidth: 44 })
    my += 7.5
  }

  // "BILL TO" block
  const billY = 76
  doc.setFillColor(...NAVY)
  doc.rect(14, billY, 36, 5.5, 'F')
  doc.setFont('helvetica', 'bold')
  doc.setFontSize(7.5)
  doc.setTextColor(...WHITE)
  doc.text('BILL TO', 16, billY + 3.8)

  doc.setFont('helvetica', 'bold')
  doc.setFontSize(10)
  doc.setTextColor(...NAVY)
  doc.text(invoice.customerName, 14, billY + 12)

  doc.setFont('helvetica', 'normal')
  doc.setFontSize(8.5)
  doc.setTextColor(...GRAY)
  doc.text('Attention: Accounts Payable', 14, billY + 18)

  // Horizontal rule
  doc.setDrawColor(...LGRAY)
  doc.setLineWidth(0.4)
  doc.line(14, billY + 24, W - 14, billY + 24)

  return billY + 28   // return Y position for content start
}

// ─── Draw summary stat boxes ──────────────────────────────────────────────────
function drawSummaryBoxes(doc, invoice, startY) {
  const W    = doc.internal.pageSize.getWidth()
  const boxH = 16
  const gap  = 4
  const bW   = (W - 28 - gap * 2) / 3

  const boxes = [
    { label: 'New Devices',          value: String(invoice.newDeviceCount),      color: NAVY  },
    { label: `Prorated (${invoice.billingMonthLabel})`, value: money(invoice.proratedTotal), color: [160, 100, 10] },
    { label: `Forward (${invoice.nextMonthLabel})`,     value: money(invoice.forwardTotal),  color: [25, 115, 65]  },
  ]

  boxes.forEach((b, i) => {
    const x = 14 + i * (bW + gap)
    doc.setFillColor(b.color[0], b.color[1], b.color[2])
    doc.roundedRect(x, startY, bW, boxH, 2, 2, 'F')
    doc.setFont('helvetica', 'normal')
    doc.setFontSize(7)
    doc.setTextColor(...WHITE)
    doc.text(b.label, x + bW / 2, startY + 5, { align: 'center' })
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(11)
    doc.text(b.value, x + bW / 2, startY + 12, { align: 'center' })
  })

  return startY + boxH + 6
}

// ─── Build autoTable rows for one section ────────────────────────────────────
// Both sections: Description | Quantity | Price Each | Amount  (4 cols)
function buildRows(lineItems) {
  return lineItems.map(li => [
    li.description,
    li.quantity,
    money(li.priceEach),
    money(li.amount),
  ])
}

// ─── Draw line items table ────────────────────────────────────────────────────
function drawLineItemsTable(doc, invoice, startY) {
  const W = doc.internal.pageSize.getWidth()

  // Partition line items into up to 4 ordered sections:
  //   1. Standard HANOVER prorated   (sectionGroup='hanover', type='prorated')
  //   2. Standard HANOVER forward    (sectionGroup='hanover', type='forward')
  //   3. Han-CS prorated             (sectionGroup='hancs',   type='prorated')
  //   4. Han-CS forward              (sectionGroup='hancs',   type='forward')
  // For non-Hanover invoices every line has sectionGroup undefined / 'hanover'
  // and the split is simply prorated vs forward — the Han-CS buckets will be empty.
  const hanoverProrated = invoice.lineItems.filter(li => li.type === 'prorated' && li.sectionGroup !== 'hancs')
  const hanoverForward  = invoice.lineItems.filter(li => li.type === 'forward'  && li.sectionGroup !== 'hancs')
  const hancsProrated   = invoice.lineItems.filter(li => li.type === 'prorated' && li.sectionGroup === 'hancs')
  const hancsForward    = invoice.lineItems.filter(li => li.type === 'forward'  && li.sectionGroup === 'hancs')

  // Both sections share the same 4-column layout
  const colStyles = {
    0: { cellWidth: 'auto' },               // Description
    1: { cellWidth: 22, halign: 'center' }, // Quantity
    2: { cellWidth: 28, halign: 'right' },  // Price Each
    3: { cellWidth: 28, halign: 'right' },  // Amount
  }
  const HEAD = ['Description', 'Quantity', 'Price Each', 'Amount']

  const sections = []
  if (hanoverProrated.length > 0) sections.push({
    label:     `PRORATED NEW ACTIVATIONS  ·  ${invoice.billingMonthLabel}`,
    rows:      buildRows(hanoverProrated),
    colStyles, head: HEAD,
    color:     [255, 245, 225],
  })
  if (hanoverForward.length > 0) sections.push({
    label:     `FULL MONTH FORWARD  ·  ${invoice.nextMonthLabel}`,
    rows:      buildRows(hanoverForward),
    colStyles, head: HEAD,
    color:     [235, 250, 240],
  })
  if (hancsProrated.length > 0) sections.push({
    label:     `HAN-CS  ·  PRORATED NEW ACTIVATIONS  ·  ${invoice.billingMonthLabel}`,
    rows:      buildRows(hancsProrated),
    colStyles, head: HEAD,
    color:     HANCS_PROT_BG,
  })
  if (hancsForward.length > 0) sections.push({
    label:     `HAN-CS  ·  FULL MONTH FORWARD  ·  ${invoice.nextMonthLabel}`,
    rows:      buildRows(hancsForward),
    colStyles, head: HEAD,
    color:     HANCS_FWD_BG,
  })

  const PAGE_H      = doc.internal.pageSize.getHeight()
  // Footer occupies the bottom 16 mm (rule at H-14, text at H-7).
  // FOOTER_H is the margin autoTable must leave clear on every page.
  const FOOTER_H    = 20   // generous — keeps last table row above footer rule
  const LABEL_H     = 6    // height of the coloured section label bar
  const HEAD_H      = 10   // approximate height of the table header row
  // Minimum space needed before starting a new section (label + header + 1 row)
  const MIN_SECTION = LABEL_H + HEAD_H + 14

  let tableEndY = startY
  for (const section of sections) {
    // ── Orphan guard: if there isn't enough room for label + header + one   ──
    // ── row on this page, push to the next page before drawing the label.  ──
    const spaceLeft = PAGE_H - FOOTER_H - tableEndY
    if (spaceLeft < MIN_SECTION) {
      doc.addPage()
      drawPageHeader(doc, invoice)
      drawPageFooter(doc, doc.internal.getNumberOfPages())
      tableEndY = 18  // top margin after continuation header
    }

    // Section label bar
    doc.setFillColor(section.color[0], section.color[1], section.color[2])
    doc.rect(14, tableEndY, W - 28, LABEL_H, 'F')
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(7)
    doc.setTextColor(...GRAY)
    doc.text(section.label, 16, tableEndY + 4.2)
    tableEndY += LABEL_H

    autoTable(doc, {
      startY: tableEndY,
      // bottom margin tells autoTable to start a new page before encroaching
      // on the footer zone — this is the primary fix for the overlap bug
      margin: { left: 14, right: 14, bottom: FOOTER_H },
      head: [section.head],
      body: section.rows,
      columnStyles: section.colStyles,
      // Never split a data row across pages
      rowPageBreak: 'avoid',
      headStyles: {
        fillColor: NAVY,
        textColor: WHITE,
        fontStyle: 'bold',
        fontSize: 7.5,
        cellPadding: 3,
      },
      bodyStyles: {
        fontSize: 7.5,
        textColor: BLACK,
        cellPadding: { top: 3, right: 3, bottom: 3, left: 3 },
        lineColor: LGRAY,
        lineWidth: 0.2,
      },
      alternateRowStyles: {
        fillColor: [248, 249, 252],
      },
      styles: {
        font: 'helvetica',
        overflow: 'linebreak',
      },
      didDrawPage: (data) => {
        // Re-draw continuation header on pages after the first
        if (data.pageNumber > 1) {
          drawPageHeader(doc, invoice)
        }
        drawPageFooter(doc, data.pageNumber)
      },
    })

    tableEndY = doc.lastAutoTable.finalY + 4
  }
  return tableEndY
}

// ─── Totals block ─────────────────────────────────────────────────────────────
function drawTotals(doc, invoice, startY) {
  const W   = doc.internal.pageSize.getWidth()
  const bX  = W - 90
  const bW  = 76
  let   y   = startY + 4

  doc.setDrawColor(...LGRAY)
  doc.setLineWidth(0.3)
  doc.line(bX, y, W - 14, y)
  y += 5

  const rows = [
    ['Prorated Subtotal',      money(invoice.proratedTotal), false],
    ['Forward Month Subtotal', money(invoice.forwardTotal),  false],
  ]

  for (const [label, val, bold] of rows) {
    doc.setFont('helvetica', bold ? 'bold' : 'normal')
    doc.setFontSize(8.5)
    doc.setTextColor(...GRAY)
    doc.text(label, bX, y)
    doc.setTextColor(...BLACK)
    doc.text(val, W - 14, y, { align: 'right' })
    y += 6
  }

  // Grand total box
  doc.setFillColor(...NAVY)
  doc.roundedRect(bX - 2, y, bW + 2, 10, 1.5, 1.5, 'F')
  doc.setFont('helvetica', 'bold')
  doc.setFontSize(10)
  doc.setTextColor(...WHITE)
  doc.text('INVOICE TOTAL', bX + 2, y + 7)
  doc.text(money(invoice.grandTotal), W - 16, y + 7, { align: 'right' })
  y += 16

  // Price warning note
  if (invoice.hasPriceWarnings) {
    doc.setFillColor(255, 245, 210)
    doc.roundedRect(14, y, W - 28, 8, 1.5, 1.5, 'F')
    doc.setFont('helvetica', 'normal')
    doc.setFontSize(7.5)
    doc.setTextColor(150, 80, 10)
    doc.text('⚠  Some line items have unmapped SKUs or missing prices. Please review before sending.', 18, y + 5)
    y += 12
  }

  // "Thank you" note
  y += 4
  doc.setFont('helvetica', 'italic')
  doc.setFontSize(8)
  doc.setTextColor(...GRAY)
  doc.text('Thank you for your business. Please contact us with any billing questions.', 14, y)

  return y
}

// ─── Minimal header for continuation pages ───────────────────────────────────
function drawPageHeader(doc, invoice) {
  const W = doc.internal.pageSize.getWidth()
  doc.setFillColor(...NAVY)
  doc.rect(0, 0, W, 12, 'F')
  try { doc.addImage(LOGO_BASE64, 'PNG', 8, 1, 22, 9) } catch (_) {}
  doc.setFont('helvetica', 'bold')
  doc.setFontSize(7.5)
  doc.setTextColor(...WHITE)
  doc.text(`INVOICE  ·  ${invoice.customerName}  ·  ${invoice.billingMonthLabel}`, W / 2, 7.5, { align: 'center' })
}

// ─── Footer ───────────────────────────────────────────────────────────────────
function drawPageFooter(doc, pageNum) {
  const W = doc.internal.pageSize.getWidth()
  const H = doc.internal.pageSize.getHeight()
  doc.setDrawColor(...LGRAY)
  doc.setLineWidth(0.3)
  doc.line(14, H - 12, W - 14, H - 12)
  doc.setFont('helvetica', 'normal')
  doc.setFontSize(7)
  doc.setTextColor(...GRAY)
  doc.text(COMPANY.website || COMPANY.name, 14, H - 7)
  doc.text(`Page ${pageNum}`, W - 14, H - 7, { align: 'right' })
  doc.text('Confidential — For billing purposes only', W / 2, H - 7, { align: 'center' })
}

// ─── Shared build function ────────────────────────────────────────────────────
function buildDoc(invoice) {
  const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'letter' })
  const PAGE_H = doc.internal.pageSize.getHeight()
  const FOOTER_H = 20   // must match the value used in drawLineItemsTable

  const contentStartY = drawHeader(doc, invoice)
  const afterSummaryY = drawSummaryBoxes(doc, invoice, contentStartY)
  const afterTableY   = drawLineItemsTable(doc, invoice, afterSummaryY + 2)

  // The totals block is ~40 mm tall (2 subtitle rows + grand total box + notes).
  // If it won't fit above the footer, push it to a fresh page.
  const TOTALS_H = 50
  let totalsY = afterTableY
  if (PAGE_H - FOOTER_H - totalsY < TOTALS_H) {
    doc.addPage()
    drawPageHeader(doc, invoice)
    drawPageFooter(doc, doc.internal.getNumberOfPages())
    totalsY = 18
  }

  drawTotals(doc, invoice, totalsY)
  // Footer on page 1 is drawn here; subsequent pages are handled by didDrawPage
  drawPageFooter(doc, 1)
  return doc
}

// ─── Download PDF ─────────────────────────────────────────────────────────────
export function exportInvoicePDF(invoice) {
  const doc      = buildDoc(invoice)
  const safeName = invoice.customerName.replace(/[^a-z0-9]/gi, '_')
  doc.save(`Invoice_${safeName}_${invoice.billingMonth}.pdf`)
}

// ─── Preview PDF — returns a blob URL for display in an iframe ───────────────
export function previewInvoicePDF(invoice) {
  const doc  = buildDoc(invoice)
  const blob = doc.output('blob')
  return URL.createObjectURL(blob)
}

// ─── Export ALL invoices as separate PDFs (zip via browser) ──────────────────
export function exportAllPDF(data) {
  // Generate each invoice individually — simplest for now
  for (const invoice of data.invoices) {
    exportInvoicePDF(invoice)
  }
}

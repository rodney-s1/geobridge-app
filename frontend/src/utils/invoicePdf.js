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

  // Invoice meta box (right side)
  const boxX = W - 90
  const boxW = 76
  doc.setFillColor(...LTBLUE)
  doc.roundedRect(boxX, 32, boxW, 36, 2, 2, 'F')
  doc.setDrawColor(...BLUE)
  doc.setLineWidth(0.3)
  doc.roundedRect(boxX, 32, boxW, 36, 2, 2, 'S')

  const metaRows = [
    ['Invoice #:',    invoiceNumber(invoice.billingMonth, invoice.customerId)],
    ['Date Issued:',  todayStr()],
    ['Billing Period:', invoice.billingMonthLabel],
    ['Invoice Type:',  invoice.billingType],
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
function buildRows(lineItems) {
  return lineItems.map(li => {
    const isProrated = li.type === 'prorated'
    const desc = li.description.split('\n')[0]   // first line only in table
    const serials = li.serials.length > 0
      ? li.serials.join(', ')
      : ''
    const prorate = isProrated
      ? `${li.daysActive}/${li.daysInMonth} days\n${pct(li.prorateFactor)}`
      : 'Full Month'
    return [
      li.itemCode,
      desc + (serials ? `\n${serials}` : ''),
      li.quantity,
      money(isProrated ? li.monthlyRate : li.priceEach),
      prorate,
      money(li.priceEach),
      money(li.amount),
    ]
  })
}

// ─── Draw line items table ────────────────────────────────────────────────────
function drawLineItemsTable(doc, invoice, startY) {
  const W = doc.internal.pageSize.getWidth()

  const proratedLines = invoice.lineItems.filter(li => li.type === 'prorated')
  const forwardLines  = invoice.lineItems.filter(li => li.type === 'forward')

  const colStyles = {
    0: { cellWidth: 30, fontStyle: 'normal' },   // Item Code
    1: { cellWidth: 'auto' },                     // Description
    2: { cellWidth: 10, halign: 'center' },       // Qty
    3: { cellWidth: 20, halign: 'right' },        // Monthly Rate
    4: { cellWidth: 22, halign: 'center' },       // Prorate
    5: { cellWidth: 20, halign: 'right' },        // Price Each
    6: { cellWidth: 22, halign: 'right' },        // Amount
  }

  const sections = []
  if (proratedLines.length > 0) sections.push({ label: `PRORATED NEW ACTIVATIONS  ·  ${invoice.billingMonthLabel}`, rows: buildRows(proratedLines), color: [255, 245, 225] })
  if (forwardLines.length  > 0) sections.push({ label: `FULL MONTH FORWARD  ·  ${invoice.nextMonthLabel}`,          rows: buildRows(forwardLines),  color: [235, 250, 240] })

  let tableEndY = startY
  for (const section of sections) {
    // Section label bar
    doc.setFillColor(section.color[0], section.color[1], section.color[2])
    doc.rect(14, tableEndY, W - 28, 6, 'F')
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(7)
    doc.setTextColor(...GRAY)
    doc.text(section.label, 16, tableEndY + 4.2)
    tableEndY += 6

    autoTable(doc, {
      startY: tableEndY,
      margin: { left: 14, right: 14 },
      head: [['Item Code', 'Description', 'Qty', 'Monthly Rate', 'Prorate', 'Price Each', 'Amount']],
      body: section.rows,
      columnStyles: colStyles,
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
        // Re-draw header on continuation pages
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

// ─── Main export function ─────────────────────────────────────────────────────
export function exportInvoicePDF(invoice) {
  const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'letter' })

  // Page 1
  const contentStartY  = drawHeader(doc, invoice)
  const afterSummaryY  = drawSummaryBoxes(doc, invoice, contentStartY)
  const afterTableY    = drawLineItemsTable(doc, invoice, afterSummaryY + 2)
  drawTotals(doc, invoice, afterTableY)
  drawPageFooter(doc, 1)

  const safeName = invoice.customerName.replace(/[^a-z0-9]/gi, '_')
  doc.save(`Invoice_${safeName}_${invoice.billingMonth}.pdf`)
}

// ─── Export ALL invoices as separate PDFs (zip via browser) ──────────────────
export function exportAllPDF(data) {
  // Generate each invoice individually — simplest for now
  for (const invoice of data.invoices) {
    exportInvoicePDF(invoice)
  }
}

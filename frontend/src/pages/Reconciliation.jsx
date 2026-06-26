import React, { useState, useEffect, useCallback } from 'react'

const API = 'http://127.0.0.1:8001'

// ─── Tiny helpers ─────────────────────────────────────────────────────────────
function fmt$(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (isNaN(n)) return '—'
  return '$' + n.toFixed(2)
}

function fmtDelta(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (isNaN(n)) return '—'
  return (n > 0 ? '+' : '') + '$' + n.toFixed(2)
}

// ─── Quantity status metadata ──────────────────────────────────────────────────
const QTY_META = {
  match:        { label: 'Match',          cls: 'bg-emerald-900/50 text-emerald-300 border-emerald-700/40' },
  under_billed: { label: 'Under-billed',   cls: 'bg-red-900/50    text-red-300    border-red-700/40'    },
  over_billed:  { label: 'Over-billed',    cls: 'bg-blue-900/50   text-blue-300   border-blue-700/40'   },
  no_qb_data:   { label: 'No QB Data',     cls: 'bg-amber-900/50  text-amber-300  border-amber-700/40'  },
}

// ─── Price status metadata (kept for price tab) ────────────────────────────────
const PRICE_META = {
  ok:         { label: 'OK',           cls: 'bg-emerald-900/50 text-emerald-300 border-emerald-700/40' },
  over:       { label: 'Over-billed',  cls: 'bg-blue-900/50   text-blue-300   border-blue-700/40'   },
  under:      { label: 'Under-billed', cls: 'bg-red-900/50    text-red-300    border-red-700/40'    },
  unmapped:   { label: 'Unmapped',     cls: 'bg-amber-900/50  text-amber-300  border-amber-700/40'  },
  no_price:   { label: 'No Price',     cls: 'bg-slate-700/80  text-slate-300  border-slate-600/40'  },
  not_in_qb:      { label: 'Not in QB',       cls: 'bg-purple-900/50 text-purple-300 border-purple-700/40' },
  discrepancy:     { label: 'Discrepancy',      cls: 'bg-red-900/50    text-red-300    border-red-700/40'    },
  never_activated: { label: 'Never Activated',  cls: 'bg-yellow-900/50 text-yellow-300 border-yellow-700/40' },
}

function QtyChip({ status, size = 'sm' }) {
  const meta = QTY_META[status] || { label: status, cls: 'bg-slate-700 text-slate-300 border-slate-600' }
  const pad  = size === 'xs' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-0.5 text-xs'
  return <span className={`inline-flex items-center rounded border font-medium ${pad} ${meta.cls}`}>{meta.label}</span>
}

function PriceChip({ status, size = 'sm' }) {
  const meta = PRICE_META[status] || { label: status, cls: 'bg-slate-700 text-slate-300 border-slate-600' }
  const pad  = size === 'xs' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-0.5 text-xs'
  return <span className={`inline-flex items-center rounded border font-medium ${pad} ${meta.cls}`}>{meta.label}</span>
}

// ─── Summary stat card ────────────────────────────────────────────────────────
function SummaryCard({ label, value, sub, color = 'blue', onClick, active }) {
  const colorMap = {
    green:  { icon: 'text-emerald-400', ring: 'border-emerald-500/40', bg: 'bg-emerald-900/20' },
    red:    { icon: 'text-red-400',     ring: 'border-red-500/40',     bg: 'bg-red-900/20'     },
    amber:  { icon: 'text-amber-400',   ring: 'border-amber-500/40',   bg: 'bg-amber-900/20'   },
    blue:   { icon: 'text-blue-400',    ring: 'border-blue-500/40',    bg: 'bg-blue-900/20'    },
    purple: { icon: 'text-purple-400',  ring: 'border-purple-500/40',  bg: 'bg-purple-900/20'  },
    slate:  { icon: 'text-slate-400',   ring: 'border-slate-500/40',   bg: 'bg-slate-800'      },
  }
  const c = colorMap[color] || colorMap.blue
  const activeRing = active ? `ring-2 ring-offset-1 ring-offset-slate-900 ${c.ring.replace('border-', 'ring-')}` : ''
  return (
    <button
      onClick={onClick}
      className={`text-left bg-slate-800 border ${active ? c.ring + ' ' + c.bg : 'border-slate-700'}
        rounded-xl p-4 transition-all hover:border-slate-500 ${activeRing} ${onClick ? 'cursor-pointer' : 'cursor-default'}`}
    >
      <div className={`text-2xl font-bold ${c.icon}`}>{value}</div>
      <div className="text-xs font-medium text-slate-200 mt-1">{label}</div>
      {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </button>
  )
}

// ─── Qty breakdown table (SKU-level, shown inside expanded customer row) ───────
function QtyBreakdownTable({ rows }) {
  if (!rows || rows.length === 0) return (
    <div className="px-6 py-4 text-xs text-slate-500 italic">No SKU quantity data available.</div>
  )
  return (
    <table className="w-full table-fixed text-xs">
      <colgroup>
        <col style={{ width: '35%' }} />
        <col style={{ width: '15%' }} />
        <col style={{ width: '15%' }} />
        <col style={{ width: '15%' }} />
        <col style={{ width: '20%' }} />
      </colgroup>
      <thead>
        <tr className="border-b border-slate-700/40">
          <th className="px-4 py-2 text-left text-xs text-slate-500 font-medium">SKU Key</th>
          <th className="px-4 py-2 text-right text-xs text-slate-500 font-medium">MyAdmin</th>
          <th className="px-4 py-2 text-right text-xs text-slate-500 font-medium">QB Invoice</th>
          <th className="px-4 py-2 text-right text-xs text-slate-500 font-medium">Difference</th>
          <th className="px-4 py-2 text-left text-xs text-slate-500 font-medium">Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const diff = r.qtyDelta
          // If MyAdmin=0 but there are unmapped devices, the discrepancy is likely
          // caused by those devices having no rate plan — annotate rather than alarm
          const unmappedExplains = r.myAdminCount === 0 && (r.unmappedCount > 0)
          // If never-activated devices inflate the MyAdmin count vs QB, note it
          const neverActivatedCount = r.neverActivatedCount || 0
          return (
            <tr key={`${r.skuKey}-${i}`} className="border-t border-slate-700/30 hover:bg-slate-700/20">
              <td className="px-4 py-2 font-mono text-slate-300 truncate" title={r.skuKey}>{r.skuKey || '—'}</td>
              <td className="px-4 py-2 text-right font-mono text-slate-200">
                {r.hanoverMaster
                  ? <span>{r.myAdminCount}<div className="text-violet-400/70 text-xs font-sans normal-case">all Hanover customers</div></span>
                  : r.qbOnly
                    ? <span className="text-slate-600">—</span>
                    : r.myAdminCount
                }
                {unmappedExplains && (
                  <div className="text-amber-400/70 text-xs font-sans normal-case">
                    {r.unmappedCount} unmapped
                  </div>
                )}
                {neverActivatedCount > 0 && (
                  <div className="text-yellow-400/70 text-xs font-sans normal-case">
                    {neverActivatedCount} never activated
                  </div>
                )}
              </td>
              <td className="px-4 py-2 text-right font-mono text-slate-200">
                {r.qbQty ?? '—'}
                {r.hanoverConsolidated && (
                  <div className="text-violet-400/70 text-xs font-sans normal-case">via Hanover Ins.</div>
                )}
              </td>
              <td className="px-4 py-2 text-right font-mono">
                {diff === null || diff === undefined
                  ? <span className="text-slate-600">—</span>
                  : <span className={diff > 0 ? 'text-red-400' : diff < 0 ? 'text-blue-400' : 'text-emerald-400'}>
                      {diff > 0 ? `+${diff}` : diff}
                    </span>
                }
              </td>
              <td className="px-4 py-2">
                {unmappedExplains
                  ? <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-amber-900/40 text-amber-300 border border-amber-700/40">
                      No rate plan
                    </span>
                  : <QtyChip status={r.qtyStatus} size="xs" />
                }
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ─── Price breakdown table (device-level, shown in price tab) ─────────────────
function PriceBreakdownTable({ devices }) {
  if (!devices || devices.length === 0) return (
    <div className="px-6 py-4 text-xs text-slate-500 italic">No device data available.</div>
  )
  const noneCount          = devices.filter(d => !d.ratePlanCode || d.ratePlanCode === '(none)').length
  const neverActivatedDevs  = devices.filter(d => d.neverActivated)
  const neverActivatedCount = neverActivatedDevs.length
  return (
    <div>
      {noneCount > 0 && (
        <div className="mx-4 mt-3 mb-1 px-3 py-2 rounded-lg bg-amber-900/25 border border-amber-700/40 text-xs text-amber-300 flex items-start gap-2">
          <span className="mt-0.5">⚠</span>
          <span>
            <strong>{noneCount} device{noneCount > 1 ? 's' : ''}</strong> {noneCount > 1 ? 'have' : 'has'} no billing plan name that matches a Rate Plan Mapping.
            Go to <strong>Settings → Rate Plan Mappings</strong> and add an entry for this customer's billing plan
            (e.g. <code className="font-mono bg-slate-700/60 px-1 rounded">PROPLUS MODE</code> → <code className="font-mono bg-slate-700/60 px-1 rounded">Service Fee Geotab (ProPlus)</code>).
            Most customers use a billing plan name, not a promo code.
          </span>
        </div>
      )}
      {neverActivatedCount > 0 && (
        <div className="mx-4 mt-3 mb-1 px-3 py-2 rounded-lg bg-yellow-900/20 border border-yellow-700/40 text-xs text-yellow-300 flex items-start gap-2">
          <span className="mt-0.5">ℹ</span>
          <span>
            <strong>{neverActivatedCount} device{neverActivatedCount > 1 ? 's' : ''}</strong> {neverActivatedCount > 1 ? 'have' : 'has'} never been activated in MyAdmin.
            {' '}This customer is billed <strong>Standard</strong> — these devices are included in the expected quantity at the account's active rate plan price.
            They will show <strong>no actual QB price</strong> until they appear on an invoice.
          </span>
        </div>
      )}
      <table className="w-full table-fixed text-xs">
      <colgroup>
        <col style={{ width: '14%' }} />
        <col style={{ width: '14%' }} />
        <col style={{ width: '18%' }} />
        <col style={{ width: '9%' }} />
        <col style={{ width: '9%' }} />
        <col style={{ width: '9%' }} />
        <col style={{ width: '10%' }} />
        <col style={{ width: '17%' }} />
      </colgroup>
      <thead>
        <tr className="border-b border-slate-700/40">
          {['Serial','Rate Plan','SKU Key','Expected','Actual','Delta','Source','Status'].map(h => (
            <th key={h} className="px-4 py-2 text-left text-xs text-slate-500 font-medium">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {devices.map((d, i) => (
          <tr key={`${d.serialNumber}-${i}`} className="border-t border-slate-700/30 hover:bg-slate-700/20">
            <td className="px-4 py-2 font-mono text-slate-300">{d.serialNumber || '—'}</td>
            <td className="px-4 py-2 font-mono">
              {d.neverActivated
                ? <span className="bg-yellow-900/50 text-yellow-300 border border-yellow-700/40 px-1.5 py-0.5 rounded text-[10px]">Never Activated</span>
                : d.ratePlanCode
                  ? <span className="bg-slate-700 text-slate-200 px-1.5 py-0.5 rounded">{d.ratePlanCode}</span>
                  : <span className="text-slate-600">—</span>}
            </td>
            <td className="px-4 py-2 text-slate-400 truncate">{d.skuKey || '—'}</td>
            <td className="px-4 py-2 font-mono text-slate-300">{fmt$(d.expectedPrice)}</td>
            <td className="px-4 py-2 font-mono text-slate-300">{fmt$(d.actualPrice)}</td>
            <td className="px-4 py-2 font-mono">
              {d.delta !== null && d.delta !== undefined
                ? <span className={d.delta > 0.005 ? 'text-blue-400' : d.delta < -0.005 ? 'text-red-400' : 'text-emerald-400'}>
                    {fmtDelta(d.delta)}
                  </span>
                : <span className="text-slate-600">—</span>}
            </td>
            <td className="px-4 py-2 text-slate-500 capitalize text-[10px]">{d.priceSource || '—'}</td>
            <td className="px-4 py-2"><PriceChip status={d.status} size="xs" /></td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  )
}

// ─── Locations Panel ──────────────────────────────────────────────────────────
function LocationsPanel({ locationNames, devices, customerName }) {
  const [openLoc, setOpenLoc] = React.useState(null)

  // Group devices by location; devices with no location go under ""
  const byLocation = React.useMemo(() => {
    const map = {}
    ;(devices || []).forEach(d => {
      const loc = d.location || ''
      if (!map[loc]) map[loc] = []
      map[loc].push(d)
    })
    return map
  }, [devices])

  return (
    <div className="px-6 py-4">
      <p className="text-xs text-slate-500 mb-4">
        All locations share one QuickBooks invoice under{' '}
        <span className="text-slate-300 font-medium">{customerName}</span>.
        Click a location to see its devices.
      </p>
      <div className="flex flex-col gap-2">
        {locationNames.map(loc => {
          const locDevices = byLocation[loc] || []
          const isOpen = openLoc === loc
          return (
            <div key={loc} className="border border-slate-700/50 rounded overflow-hidden">
              {/* Location header button */}
              <button
                onClick={() => setOpenLoc(isOpen ? null : loc)}
                className="w-full flex items-center justify-between px-4 py-2.5 bg-slate-800/80 hover:bg-slate-700/60 transition-colors text-left"
              >
                <span className="flex items-center gap-2 text-sm text-slate-200">
                  <span className="text-slate-400">📍</span>
                  <span className="font-medium">{loc}</span>
                </span>
                <span className="flex items-center gap-3">
                  <span className="text-xs text-slate-400 font-mono">{locDevices.length} device{locDevices.length !== 1 ? 's' : ''}</span>
                  <span className={`text-slate-500 text-xs transition-transform inline-block ${isOpen ? 'rotate-90' : ''}`}>▶</span>
                </span>
              </button>
              {/* Device list */}
              {isOpen && (
                <div className="bg-slate-900/40 border-t border-slate-700/40">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-slate-700/30">
                        <th className="px-4 py-2 text-left text-slate-500 font-medium">Serial</th>
                        <th className="px-4 py-2 text-left text-slate-500 font-medium">Rate Plan</th>
                        <th className="px-4 py-2 text-left text-slate-500 font-medium">SKU</th>
                        <th className="px-4 py-2 text-left text-slate-500 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {locDevices.map((d, i) => (
                        <tr key={i} className="border-b border-slate-700/20 hover:bg-slate-800/30">
                          <td className="px-4 py-1.5 font-mono text-slate-300">{d.serialNumber}</td>
                          <td className="px-4 py-1.5 text-slate-400">{d.ratePlanCode || '—'}</td>
                          <td className="px-4 py-1.5 text-slate-400">{d.skuKey || <span className="text-amber-500/70 italic">unmapped</span>}</td>
                          <td className="px-4 py-1.5">
                            <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                              d.status === 'ok'             ? 'bg-emerald-900/40 text-emerald-400' :
                              d.status === 'over'           ? 'bg-blue-900/40 text-blue-400' :
                              d.status === 'under'          ? 'bg-red-900/40 text-red-400' :
                              d.status === 'unmapped'       ? 'bg-amber-900/40 text-amber-400' :
                              d.status === 'never_activated'? 'bg-yellow-900/40 text-yellow-400' :
                              d.status === 'no_price'       ? 'bg-purple-900/40 text-purple-400' :
                              'bg-slate-700/40 text-slate-400'
                            }`}>
                              {d.status === 'never_activated' ? 'never activated' : d.status || '—'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function CustomerRow({ customer }) {
  const [tab, setTab] = useState('qty')   // 'qty' | 'price' | 'locations'
  const [expanded, setExpanded] = useState(false)

  const {
    customerName, deviceCount, billingType,
    myAdminTotal, qbTotal, qtyDelta, hasQbData,
    qtyMatch, qtyUnderBilled, qtyOverBilled, qtyMissing,
    ok, over, under, unmapped, noPrice, neverActivated,
    expectedMonthly, actualMonthly, delta,
    status, devices, skuQtyBreakdown,
    qbOnly, locationNames,
  } = customer

  const hasLocations = locationNames && locationNames.length > 0

  // Quantity status for the row badge
  const qtyMismatch = hasQbData && qtyDelta !== 0 && qtyDelta !== null
  const qtyRowStatus = !hasQbData
    ? 'no_qb_data'
    : qtyDelta === 0
      ? 'match'
      : qtyDelta > 0 ? 'under_billed' : 'over_billed'

  return (
    <>
      <tr
        className={`border-t border-slate-700/40 cursor-pointer hover:bg-slate-700/30 transition-colors
          ${expanded ? 'bg-slate-700/20' : ''}`}
        onClick={() => setExpanded(e => !e)}
      >
        {/* Expand chevron */}
        <td className="px-3 py-3 w-8">
          <span className={`text-slate-500 text-xs transition-transform inline-block ${expanded ? 'rotate-90' : ''}`}>▶</span>
        </td>

        {/* Customer name */}
        <td className="px-4 py-3">
          <span className="text-sm font-medium text-slate-200">{customerName}</span>
          {hasLocations && (
            <span className="ml-2 text-xs bg-slate-700/60 text-slate-400 border border-slate-600/40 rounded px-1.5 py-0.5 align-middle">
              {locationNames.length} location{locationNames.length !== 1 ? 's' : ''}
            </span>
          )}
          {qbOnly && (
            <span className="ml-2 text-xs bg-violet-900/50 text-violet-300 border border-violet-700/40 rounded px-1.5 py-0.5 align-middle">QB Only</span>
          )}
        </td>

        {/* MyAdmin device count */}
        <td className="px-4 py-3 text-right">
          {qbOnly
            ? <span className="text-slate-600 text-sm">—</span>
            : <span className="text-sm font-mono font-bold text-slate-200">{myAdminTotal}</span>
          }
        </td>

        {/* QB invoice count */}
        <td className="px-4 py-3 text-right">
          {hasQbData
            ? <span className="text-sm font-mono font-bold text-slate-200">{qbTotal}</span>
            : <span className="text-xs text-amber-500 italic">no QB data</span>
          }
        </td>

        {/* Difference */}
        <td className="px-4 py-3 text-right">
          {hasQbData && qtyDelta !== null
            ? <span className={`text-sm font-mono font-bold ${
                qtyDelta > 0 ? 'text-red-400' : qtyDelta < 0 ? 'text-blue-400' : 'text-emerald-400'
              }`}>
                {qtyDelta > 0 ? `+${qtyDelta}` : qtyDelta}
              </span>
            : <span className="text-slate-600 text-sm">—</span>
          }
        </td>

        {/* SKU breakdown badges */}
        <td className="px-4 py-3">
          <div className="flex items-center gap-1.5 flex-wrap">
            {qtyUnderBilled > 0 && (
              <span className="text-xs bg-red-900/50 text-red-300 border border-red-700/40 rounded px-1.5 py-0.5">
                {qtyUnderBilled} under
              </span>
            )}
            {qtyOverBilled > 0 && (
              <span className="text-xs bg-blue-900/50 text-blue-300 border border-blue-700/40 rounded px-1.5 py-0.5">
                {qtyOverBilled} over
              </span>
            )}
            {unmapped > 0 && (
              <span className="text-xs bg-amber-900/50 text-amber-300 border border-amber-700/40 rounded px-1.5 py-0.5">
                {unmapped} unmapped
              </span>
            )}
            {neverActivated > 0 && (
              <span className="text-xs bg-yellow-900/50 text-yellow-300 border border-yellow-700/40 rounded px-1.5 py-0.5">
                {neverActivated} never activated
              </span>
            )}
            {hasQbData && qtyMismatch === false && unmapped === 0 && !neverActivated && (
              <span className="text-xs text-emerald-500">✓ Match</span>
            )}
            {!hasQbData && (
              <span className="text-xs text-slate-600 italic">import QB to compare</span>
            )}
          </div>
        </td>

        {/* Qty status */}
        <td className="px-4 py-3">
          <QtyChip status={qtyRowStatus} />
        </td>
      </tr>

      {/* Expanded detail */}
      {expanded && (
        <tr>
          <td colSpan={7} className="p-0">
            <div className="bg-slate-900/60 border-t border-b border-slate-700/40">

              {/* Sub-tabs */}
              <div className="flex items-center gap-1 px-4 pt-3 pb-0 border-b border-slate-700/40">
                <button
                  onClick={e => { e.stopPropagation(); setTab('qty') }}
                  className={`px-3 py-1.5 text-xs font-medium rounded-t-lg transition-colors ${
                    tab === 'qty'
                      ? 'bg-slate-700 text-slate-200 border border-b-0 border-slate-600'
                      : 'text-slate-500 hover:text-slate-300'
                  }`}
                >
                  📊 Quantity ({myAdminTotal} MyAdmin{hasQbData ? ` · ${qbTotal} QB` : ''})
                </button>
                <button
                  onClick={e => { e.stopPropagation(); setTab('price') }}
                  className={`px-3 py-1.5 text-xs font-medium rounded-t-lg transition-colors ${
                    tab === 'price'
                      ? 'bg-slate-700 text-slate-200 border border-b-0 border-slate-600'
                      : 'text-slate-500 hover:text-slate-300'
                  }`}
                >
                  💲 Price Detail ({devices?.length ?? 0} devices)
                </button>
                {hasLocations && (
                  <button
                    onClick={e => { e.stopPropagation(); setTab('locations') }}
                    className={`px-3 py-1.5 text-xs font-medium rounded-t-lg transition-colors ${
                      tab === 'locations'
                        ? 'bg-slate-700 text-slate-200 border border-b-0 border-slate-600'
                        : 'text-slate-500 hover:text-slate-300'
                    }`}
                  >
                    📍 Locations ({locationNames.length})
                  </button>
                )}
              </div>

              {tab === 'qty' && (
                <QtyBreakdownTable rows={skuQtyBreakdown} />
              )}
              {tab === 'price' && (
                <PriceBreakdownTable devices={devices} />
              )}
              {tab === 'locations' && (
                <LocationsPanel
                  locationNames={locationNames}
                  devices={devices}
                  customerName={customerName}
                />
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ─── Empty / error states ─────────────────────────────────────────────────────
function EmptyState({ icon, title, body }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-3 text-center">
      <div className="text-5xl">{icon}</div>
      <div className="text-base font-semibold text-slate-300">{title}</div>
      {body && <div className="text-sm text-slate-500 max-w-sm">{body}</div>}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
//  ROOT COMPONENT
// ═══════════════════════════════════════════════════════════════════════════════
export default function Reconciliation() {
  const [data,         setData]        = useState(null)
  const [loading,      setLoading]     = useState(false)
  const [error,        setError]       = useState(null)
  const [search,       setSearch]      = useState('')
  const [qtyFilter,    setQtyFilter]   = useState('')  // '' | 'under_billed' | 'over_billed' | 'no_qb_data' | 'match'
  const [sortBy,       setSortBy]      = useState('alpha') // 'alpha' | 'status' | 'under_first' | 'over_first' | 'no_qb_first' | 'match_first'

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`${API}/api/reconciliation`)
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${r.status}`)
      }
      setData(await r.json())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchData() }, [])  // eslint-disable-line

  const summary              = data?.summary
  const customers            = data?.customers || []
  const qbFallbackCustomers  = data?.qbFallbackCustomers || []
  const qbHanCsUnmatched     = data?.qbHanCsUnmatched    || []

  // Client-side filtering + sorting
  const getQtyStatus = c => !c.hasQbData ? 'no_qb_data'
    : c.qtyDelta === 0 ? 'match'
    : c.qtyDelta > 0   ? 'under_billed' : 'over_billed'

  const STATUS_ORDER = { under_billed: 0, over_billed: 1, no_qb_data: 2, match: 3 }

  const visible = customers
    .filter(c => {
      const nameMatch = !search.trim() || (c.customerName || '').toLowerCase().includes(search.toLowerCase())
      if (!nameMatch) return false
      if (!qtyFilter) return true
      return getQtyStatus(c) === qtyFilter
    })
    .sort((a, b) => {
      const alpha = (a.customerName || '').localeCompare(b.customerName || '')
      switch (sortBy) {
        case 'alpha':        return alpha
        case 'status':       return STATUS_ORDER[getQtyStatus(a)] - STATUS_ORDER[getQtyStatus(b)] || alpha
        case 'under_first':  return (getQtyStatus(a) === 'under_billed' ? 0 : 1) - (getQtyStatus(b) === 'under_billed' ? 0 : 1) || alpha
        case 'over_first':   return (getQtyStatus(a) === 'over_billed'  ? 0 : 1) - (getQtyStatus(b) === 'over_billed'  ? 0 : 1) || alpha
        case 'no_qb_first':  return (getQtyStatus(a) === 'no_qb_data'   ? 0 : 1) - (getQtyStatus(b) === 'no_qb_data'   ? 0 : 1) || alpha
        case 'match_first':  return (getQtyStatus(a) === 'match'         ? 0 : 1) - (getQtyStatus(b) === 'match'         ? 0 : 1) || alpha
        default:             return alpha
      }
    })

  const hasQbData = summary?.hasQbData

  return (
    <div className="space-y-6">

      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-xl font-bold text-white">Reconciliation</h2>
          <p className="text-sm text-slate-400 mt-0.5">
            Compare MyAdmin device counts against QB invoice quantities per customer and SKU.
            {!hasQbData && data && (
              <span className="ml-2 text-amber-400">
                ⚠ No QB invoice data imported yet — go to Settings → Import CSV to upload a QB invoice export.
              </span>
            )}
          </p>
        </div>
        <button
          onClick={fetchData}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50
            text-white text-sm font-medium rounded-xl transition-colors"
        >
          {loading
            ? <><svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
              </svg> Running…</>
            : <>↻ Run Reconciliation</>}
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="bg-red-900/40 border border-red-700/40 rounded-xl px-5 py-4 text-sm text-red-300">
          <span className="font-semibold">Error: </span>{error}
          {error.includes('No MyAdmin contract data') && (
            <span className="block mt-1 text-red-400/80">
              Go to the <strong>Customers</strong> page and wait for the sync to complete, then come back here.
            </span>
          )}
        </div>
      )}

      {/* ── Quantity summary bar ── */}
      {summary && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl px-6 py-4">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Device Count Reconciliation</div>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
            <SummaryCard
              label="MyAdmin Devices"
              value={(summary.myAdminTotal ?? summary.totalDevices).toLocaleString()}
              sub="active contracts"
              color="blue"
            />
            <SummaryCard
              label="QB Invoice Qty"
              value={hasQbData ? summary.qbTotal.toLocaleString() : '—'}
              sub={hasQbData ? 'from invoice export' : 'import QB CSV first'}
              color={hasQbData ? 'blue' : 'slate'}
            />
            <SummaryCard
              label="Net Difference"
              value={hasQbData ? (summary.qtyDelta > 0 ? `+${summary.qtyDelta}` : summary.qtyDelta).toString() : '—'}
              sub={hasQbData ? (summary.qtyDelta === 0 ? 'counts match!' : summary.qtyDelta > 0 ? 'MyAdmin > QB' : 'QB > MyAdmin') : ''}
              color={!hasQbData ? 'slate' : summary.qtyDelta === 0 ? 'green' : 'red'}
            />
            <SummaryCard
              label="SKU Matches"
              value={summary.qtyMatch.toLocaleString()}
              sub="count agrees"
              color="green"
              active={qtyFilter === 'match'}
              onClick={() => setQtyFilter(f => f === 'match' ? '' : 'match')}
            />
            <SummaryCard
              label="Under-billed SKUs"
              value={summary.qtyUnderBilled.toLocaleString()}
              sub="MyAdmin > QB qty"
              color="red"
              active={qtyFilter === 'under_billed'}
              onClick={() => setQtyFilter(f => f === 'under_billed' ? '' : 'under_billed')}
            />
            <SummaryCard
              label="Over-billed SKUs"
              value={summary.qtyOverBilled.toLocaleString()}
              sub="QB qty > MyAdmin"
              color="blue"
              active={qtyFilter === 'over_billed'}
              onClick={() => setQtyFilter(f => f === 'over_billed' ? '' : 'over_billed')}
            />
            <SummaryCard
              label="No QB Data"
              value={summary.qtyMissing.toLocaleString()}
              sub="not in QB invoice"
              color="amber"
              active={qtyFilter === 'no_qb_data'}
              onClick={() => setQtyFilter(f => f === 'no_qb_data' ? '' : 'no_qb_data')}
            />
          </div>
        </div>
      )}

      {/* ── QB Fallback Warning Banner ── */}
      {qbFallbackCustomers.length > 0 && (
        <div className="rounded-xl border border-amber-600/40 bg-amber-950/30 px-4 py-3">
          <div className="flex items-start gap-3">
            <span className="text-amber-400 text-lg mt-0.5">⚠️</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-amber-300 mb-1">
                {qbFallbackCustomers.length === 1
                  ? '1 customer is billed as Han-CS based on QB invoice history only'
                  : `${qbFallbackCustomers.length} customers are billed as Han-CS based on QB invoice history only`}
              </p>
              <p className="text-xs text-amber-200/70 mb-2">
                These customers have no <code className="bg-slate-800 px-1 rounded text-amber-300">{'{Han-CS}'}</code> tag
                in MyAdmin. Their billing type was inferred from a past QB invoice line.
                If any have left the Hanover Cost Share program, add them to{' '}
                <code className="bg-slate-800 px-1 rounded text-amber-300">billing_type_overrides.json</code>{' '}
                with value <code className="bg-slate-800 px-1 rounded text-amber-300">"Standard"</code>.
              </p>
              <div className="flex flex-wrap gap-2">
                {qbFallbackCustomers.map((fc, i) => (
                  <span key={i} className="inline-flex items-center gap-1.5 text-xs bg-slate-800/70
                    border border-amber-700/30 text-amber-200 rounded-lg px-2.5 py-1">
                    <span className="font-medium">{fc.customerName}</span>
                    <span className="text-amber-400/60">·</span>
                    <span className="text-amber-400/80">{fc.activeDevices} devices</span>
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── QB HANOVER-CS Unmatched Warning Banner ── */}
      {qbHanCsUnmatched.length > 0 && (
        <div className="rounded-xl border border-red-600/40 bg-red-950/30 px-4 py-3">
          <div className="flex items-start gap-3">
            <span className="text-red-400 text-lg mt-0.5">🔴</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-red-300 mb-1">
                {qbHanCsUnmatched.length === 1
                  ? '1 QB invoice has a HANOVER-CS SKU with no matching MyAdmin account'
                  : `${qbHanCsUnmatched.length} QB invoices have a HANOVER-CS SKU with no matching MyAdmin account`}
              </p>
              <p className="text-xs text-red-200/70 mb-2">
                These customers appear on QB invoices with a HANOVER-CS line item but have no{' '}
                <code className="bg-slate-800 px-1 rounded text-red-300">{'{Han-CS}'}</code>{' '}
                account in MyAdmin. This may indicate a departed customer, a name mismatch, or
                a QB invoice that needs correction.
              </p>
              <div className="flex flex-col gap-1.5">
                {qbHanCsUnmatched.map((fc, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs bg-slate-800/70
                    border border-red-700/30 text-red-200 rounded-lg px-2.5 py-1.5 flex-wrap">
                    <span className="font-medium">{fc.customerName}</span>
                    <span className="text-red-400/60">·</span>
                    <span className="text-red-400/80">QB qty: {fc.qbQty}</span>
                    <span className="text-red-400/60">·</span>
                    <span className="text-red-300/60 truncate max-w-xs" title={fc.skuKey}>{fc.skuKey}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Search + filter + sort controls ── */}
      {data && (
        <div className="flex items-center gap-3 flex-wrap">
          {/* Search */}
          <div className="relative flex-1 max-w-sm">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm">🔍</span>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search customers…"
              className="w-full pl-8 pr-8 py-2 bg-slate-800 border border-slate-700 rounded-xl
                text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-blue-500"
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500
                  hover:text-slate-200 transition-colors leading-none"
                title="Clear search"
              >
                ✕
              </button>
            )}
          </div>

          {/* Sort dropdown */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500 whitespace-nowrap">Sort:</span>
            <select
              value={sortBy}
              onChange={e => setSortBy(e.target.value)}
              className="bg-slate-800 border border-slate-700 text-slate-200 text-xs rounded-xl
                px-3 py-2 focus:outline-none focus:border-blue-500 cursor-pointer"
            >
              <option value="alpha">A → Z</option>
              <option value="status">By Status (Under → Over → No QB → Match)</option>
              <option value="under_first">Under-billed First</option>
              <option value="over_first">Over-billed First</option>
              <option value="no_qb_first">No QB Data First</option>
              <option value="match_first">Matches First</option>
            </select>
          </div>

          {/* Active filter badge */}
          {qtyFilter && (
            <button
              onClick={() => setQtyFilter('')}
              className="text-xs text-amber-400 hover:text-amber-300 border border-amber-700/40
                bg-amber-900/20 px-3 py-2 rounded-xl transition-colors"
            >
              ✕ Clear filter: {qtyFilter.replace(/_/g, ' ')}
            </button>
          )}

          <span className="text-xs text-slate-500 ml-auto">
            {visible.length} customer{visible.length !== 1 ? 's' : ''}
          </span>
        </div>
      )}

      {/* ── Loading / empty states ── */}
      {loading && !data && (
        <div className="flex items-center gap-3 text-slate-400 py-16 justify-center">
          <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
          </svg>
          Running reconciliation…
        </div>
      )}
      {!loading && !error && !data && (
        <EmptyState icon="⚖️" title="No data yet"
          body='Click "Run Reconciliation" to compare MyAdmin devices against QB quantities.' />
      )}
      {!loading && !error && data && visible.length === 0 && (
        <EmptyState icon="✅"
          title={search ? 'No customers match your search' : 'No customers in this filter'}
          body={search ? 'Try a different name.' : 'Clear the filter to see all customers.'} />
      )}

      {/* ── Main table ── */}
      {!loading && visible.length > 0 && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
          <div className="overflow-y-auto max-h-[calc(100vh-22rem)]">
          <table className="w-full table-fixed text-sm">
            <colgroup>
              <col style={{ width: '32px' }} />
              <col style={{ width: '28%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '27%' }} />
              <col style={{ width: '13%' }} />
            </colgroup>
            <thead className="bg-slate-900/80 sticky top-0 z-10">
              <tr>
                <th className="px-3 py-3"></th>
                <th className="px-4 py-3 text-left text-xs text-slate-400 font-semibold uppercase tracking-wide">Customer</th>
                <th className="px-4 py-3 text-right text-xs text-slate-400 font-semibold uppercase tracking-wide">MyAdmin</th>
                <th className="px-4 py-3 text-right text-xs text-slate-400 font-semibold uppercase tracking-wide">QB Invoice</th>
                <th className="px-4 py-3 text-right text-xs text-slate-400 font-semibold uppercase tracking-wide">Diff</th>
                <th className="px-4 py-3 text-left text-xs text-slate-400 font-semibold uppercase tracking-wide">Issues</th>
                <th className="px-4 py-3 text-left text-xs text-slate-400 font-semibold uppercase tracking-wide">Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(c => <CustomerRow key={c.customerId} customer={c} />)}
            </tbody>
          </table>
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="flex flex-wrap gap-3 text-xs text-slate-500 pt-1">
        <span className="font-medium text-slate-400">Quantity legend:</span>
        {Object.entries(QTY_META).map(([k, v]) => (
          <span key={k} className={`inline-flex items-center gap-1 border rounded px-2 py-0.5 ${v.cls}`}>{v.label}</span>
        ))}
        <span className="text-slate-600 ml-2">
          Difference = MyAdmin count − QB invoice qty · positive = under-billed · negative = over-billed
        </span>
      </div>

    </div>
  )
}

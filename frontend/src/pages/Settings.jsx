import React, { useState, useEffect, useRef, useCallback } from 'react'

const API = 'http://127.0.0.1:8001'

// ─── Tiny helpers ─────────────────────────────────────────────────────────────
function fmtPrice(v) {
  if (v === null || v === undefined) return '—'
  return '$' + Number(v).toFixed(2)
}

function Badge({ children, color = 'slate' }) {
  const map = {
    slate:  'bg-slate-700 text-slate-200',
    blue:   'bg-blue-900/60 text-blue-300',
    green:  'bg-emerald-900/60 text-emerald-300',
    amber:  'bg-amber-900/60 text-amber-300',
    red:    'bg-red-900/60 text-red-300',
    purple: 'bg-purple-900/60 text-purple-300',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${map[color]}`}>
      {children}
    </span>
  )
}

function StatCard({ icon, label, value, color = 'blue' }) {
  const iconMap = {
    blue:   'text-blue-400',
    green:  'text-emerald-400',
    amber:  'text-amber-400',
    red:    'text-red-400',
    purple: 'text-purple-400',
  }
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-4 flex items-center gap-4">
      <div className={`text-2xl ${iconMap[color]}`}>{icon}</div>
      <div>
        <div className="text-2xl font-bold text-white">{value}</div>
        <div className="text-xs text-slate-400 mt-0.5">{label}</div>
      </div>
    </div>
  )
}

// ─── Tab button ───────────────────────────────────────────────────────────────
function TabBtn({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
        active
          ? 'bg-blue-600 text-white'
          : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700'
      }`}
    >
      {children}
    </button>
  )
}

// ─── Confirm-delete button ────────────────────────────────────────────────────
function DeleteBtn({ onConfirm, small = false }) {
  const [confirming, setConfirming] = useState(false)
  const timer = useRef(null)

  function start() {
    setConfirming(true)
    timer.current = setTimeout(() => setConfirming(false), 3000)
  }
  function confirm() {
    clearTimeout(timer.current)
    setConfirming(false)
    onConfirm()
  }

  return confirming ? (
    <button
      onClick={confirm}
      className={`${small ? 'text-xs px-2 py-0.5' : 'text-sm px-3 py-1'} bg-red-600 hover:bg-red-500 text-white rounded font-medium transition-colors`}
    >
      Confirm
    </button>
  ) : (
    <button
      onClick={start}
      className={`${small ? 'text-xs px-1.5 py-0.5' : 'text-sm px-2 py-1'} text-slate-400 hover:text-red-400 transition-colors`}
      title="Delete"
    >
      🗑
    </button>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
//  TAB 1 — SKU Catalog
// ═══════════════════════════════════════════════════════════════════════════════
function SkuCatalogTab({ catalog, onRefresh }) {
  const [search, setSearch] = useState('')
  const [editKey, setEditKey] = useState(null)   // skuKey being edited
  const [editForm, setEditForm] = useState({})
  const [adding, setAdding] = useState(false)
  const [newForm, setNewForm] = useState({ skuKey: '', fullPath: '', defaultPrice: '', cost: '', category: '', desc: '' })
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)

  const filtered = catalog.filter(s =>
    !search || s.skuKey.toLowerCase().includes(search.toLowerCase()) ||
    s.fullPath?.toLowerCase().includes(search.toLowerCase()) ||
    s.category?.toLowerCase().includes(search.toLowerCase())
  )

  // Group by category
  const groups = {}
  filtered.forEach(s => {
    const cat = s.category || 'Uncategorized'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(s)
  })

  async function saveSku(sku) {
    setSaving(true)
    try {
      const r = await fetch(`${API}/api/settings/sku-catalog`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sku),
      })
      if (!r.ok) throw new Error(await r.text())
      setMsg({ type: 'ok', text: 'SKU saved.' })
      setEditKey(null)
      setAdding(false)
      setNewForm({ skuKey: '', fullPath: '', defaultPrice: '', cost: '', category: '', desc: '' })
      onRefresh()
    } catch (e) {
      setMsg({ type: 'err', text: e.message })
    } finally {
      setSaving(false)
    }
  }

  async function deleteSku(skuKey) {
    try {
      await fetch(`${API}/api/settings/sku-catalog/${encodeURIComponent(skuKey)}`, { method: 'DELETE' })
      onRefresh()
    } catch (e) {
      setMsg({ type: 'err', text: e.message })
    }
  }

  return (
    <div className="space-y-4">
      {msg && (
        <div className={`px-4 py-2 rounded-lg text-sm ${msg.type === 'ok' ? 'bg-emerald-900/50 text-emerald-300' : 'bg-red-900/50 text-red-300'}`}>
          {msg.text}
          <button onClick={() => setMsg(null)} className="ml-3 text-slate-400 hover:text-white">✕</button>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search SKUs…"
          className="flex-1 min-w-[200px] bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button
          onClick={() => { setAdding(true); setEditKey(null) }}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg font-medium transition-colors"
        >
          + Add SKU
        </button>
      </div>

      {/* Add form */}
      {adding && (
        <div className="bg-slate-800 border border-blue-500/40 rounded-xl p-4 space-y-3">
          <div className="text-sm font-semibold text-blue-300 mb-1">New SKU</div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">SKU Key *</label>
              <input value={newForm.skuKey} onChange={e => setNewForm(f => ({ ...f, skuKey: e.target.value }))}
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block text-xs text-slate-400 mb-1">Our Cost</label>
                <input type="number" step="0.01" value={newForm.cost} onChange={e => setNewForm(f => ({ ...f, cost: e.target.value }))}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Default Price</label>
                <input type="number" step="0.01" value={newForm.defaultPrice} onChange={e => setNewForm(f => ({ ...f, defaultPrice: e.target.value }))}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
              </div>
            </div>
            <div className="col-span-2">
              <label className="block text-xs text-slate-400 mb-1">Full QB Path (col P)</label>
              <input value={newForm.fullPath} onChange={e => setNewForm(f => ({ ...f, fullPath: e.target.value }))}
                placeholder="e.g. Geotab Service:Service Fee Geotab (HOS V2) (Service Fee Geotab (HOS))"
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Category (QB group)</label>
              <input value={newForm.category} onChange={e => setNewForm(f => ({ ...f, category: e.target.value }))}
                placeholder="e.g. Geotab Service"
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Description (QB memo)</label>
              <input value={newForm.desc} onChange={e => setNewForm(f => ({ ...f, desc: e.target.value }))}
                placeholder="e.g. Service Fee Geotab (HOS)"
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
          </div>
          <div className="flex gap-2">
            <button disabled={saving || !newForm.skuKey.trim()}
              onClick={() => saveSku({ ...newForm, defaultPrice: parseFloat(newForm.defaultPrice) || 0, cost: parseFloat(newForm.cost) || 0, desc: newForm.desc || '' })}
              className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg font-medium">
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button onClick={() => setAdding(false)} className="px-4 py-1.5 text-slate-400 hover:text-white text-sm">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Single table with category subheadings — fixed column layout */}
      {filtered.length === 0 ? (
        <div className="text-center py-12 text-slate-500">No SKUs match your search.</div>
      ) : (
        <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
          <table className="w-full table-fixed text-sm">
            {/* Fixed column proportions shared across ALL category groups */}
            <colgroup>
              <col style={{ width: '38%' }} />
              <col style={{ width: '20%' }} className="hidden md:table-column" />
              <col style={{ width: '12%' }} />
              <col style={{ width: '12%' }} />
              <col style={{ width: '18%' }} />
            </colgroup>
            <thead>
              <tr className="text-xs text-slate-500 border-b border-slate-700 bg-slate-900/40">
                <th className="text-left px-4 py-2.5 font-medium">SKU Name</th>
                <th className="text-left px-4 py-2.5 font-medium hidden md:table-cell">Category</th>
                <th className="text-right px-4 py-2.5 font-medium">Our Cost</th>
                <th className="text-right px-4 py-2.5 font-medium">Default Price</th>
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {Object.keys(groups).sort().map(cat => (
                <React.Fragment key={cat}>
                  {/* Category subheading row */}
                  <tr key={`hdr-${cat}`} className="border-t border-slate-700 bg-slate-900/60">
                    <td colSpan={5} className="px-4 py-1.5">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">{cat}</span>
                        <span className="text-xs text-slate-600">{groups[cat].length}</span>
                      </div>
                    </td>
                  </tr>
                  {/* SKU rows for this category */}
                  {groups[cat].map(sku => (
                    editKey === sku.skuKey ? (
                      <tr key={sku.skuKey} className="border-b border-slate-700/50 bg-slate-750">
                        <td className="px-4 py-3" colSpan={5}>
                          <div className="grid grid-cols-2 gap-3 mb-2">
                            <div>
                              <label className="block text-xs text-slate-400 mb-1">SKU Name</label>
                              <input value={editForm.skuKey} readOnly
                                className="w-full bg-slate-600 border border-slate-500 rounded px-2 py-1 text-sm text-slate-300 cursor-not-allowed" />
                            </div>
                            <div className="grid grid-cols-2 gap-2">
                              <div>
                                <label className="block text-xs text-slate-400 mb-1">Our Cost</label>
                                <input type="number" step="0.01" value={editForm.cost ?? ''}
                                  onChange={e => setEditForm(f => ({ ...f, cost: e.target.value }))}
                                  className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
                              </div>
                              <div>
                                <label className="block text-xs text-slate-400 mb-1">Default Price</label>
                                <input type="number" step="0.01" value={editForm.defaultPrice}
                                  onChange={e => setEditForm(f => ({ ...f, defaultPrice: e.target.value }))}
                                  className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
                              </div>
                            </div>
                            <div className="col-span-2">
                              <label className="block text-xs text-slate-400 mb-1">Full QB Path (col P)</label>
                              <input value={editForm.fullPath}
                                onChange={e => setEditForm(f => ({ ...f, fullPath: e.target.value }))}
                                className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
                            </div>
                            <div>
                              <label className="block text-xs text-slate-400 mb-1">Description (QB memo)</label>
                              <input value={editForm.desc || ''}
                                onChange={e => setEditForm(f => ({ ...f, desc: e.target.value }))}
                                className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
                            </div>
                          </div>
                          <div className="flex gap-2">
                            <button disabled={saving}
                              onClick={() => saveSku({ ...editForm, defaultPrice: parseFloat(editForm.defaultPrice) || 0, cost: parseFloat(editForm.cost) || 0, desc: editForm.desc || '' })}
                              className="px-3 py-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded font-medium">
                              {saving ? 'Saving…' : 'Save'}
                            </button>
                            <button onClick={() => setEditKey(null)} className="px-3 py-1 text-slate-400 hover:text-white text-sm">Cancel</button>
                          </div>
                        </td>
                      </tr>
                    ) : (
                      <tr key={sku.skuKey} className="border-b border-slate-700/30 hover:bg-slate-750/60 transition-colors group" title={sku.fullPath || ''}>
                        <td className="px-4 py-2.5 overflow-hidden">
                          <div>
                            <span className="font-mono text-xs text-slate-200 bg-slate-700/80 px-1.5 py-0.5 rounded cursor-help" title={sku.fullPath}>
                              {sku.skuKey}
                            </span>
                            {sku.desc && sku.desc !== sku.skuKey && (
                              <div className="text-xs text-slate-500 mt-0.5 pl-0.5 truncate" title={sku.desc}>{sku.desc}</div>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-2.5 text-slate-400 text-xs hidden md:table-cell truncate" title={sku.category}>
                          {sku.category}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono text-slate-400 whitespace-nowrap text-xs">
                          {(sku.cost > 0) ? fmtPrice(sku.cost) : <span className="text-slate-600">—</span>}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono text-slate-200 whitespace-nowrap">
                          {fmtPrice(sku.defaultPrice)}
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                            <button
                              onClick={() => { setEditKey(sku.skuKey); setEditForm({ ...sku }) }}
                              className="text-xs px-2 py-0.5 text-blue-400 hover:text-blue-300"
                            >
                              Edit
                            </button>
                            <DeleteBtn small onConfirm={() => deleteSku(sku.skuKey)} />
                          </div>
                        </td>
                      </tr>
                    )
                  ))}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
//  TAB 2 — Rate Plan Mappings
// ═══════════════════════════════════════════════════════════════════════════════
function RatePlanMappingsTab({ mappings, catalog, unmapped, onRefresh }) {
  const [editCode,         setEditCode]         = useState(null)
  const [editOriginalCode, setEditOriginalCode] = useState(null)  // tracks code before rename
  const [editForm, setEditForm] = useState({})
  const [adding, setAdding] = useState(false)
  const [addCode, setAddCode] = useState('')
  const [addSku, setAddSku] = useState('')
  const [addPrice, setAddPrice] = useState('')
  const [addNotes, setAddNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)
  const [search, setSearch] = useState('')
  const [showUnmapped, setShowUnmapped] = useState(true)

  const skuOptions = catalog.map(s => s.skuKey).sort()

  const filtered = mappings.filter(m =>
    !search || m.ratePlanCode.toLowerCase().includes(search.toLowerCase()) ||
    m.skuKey.toLowerCase().includes(search.toLowerCase())
  )

  // When SKU is selected in add form, prefill price from catalog
  function onAddSkuChange(key) {
    setAddSku(key)
    const found = catalog.find(s => s.skuKey === key)
    if (found) setAddPrice(String(found.defaultPrice))
  }

  async function saveMapping(data, originalCode = null) {
    setSaving(true)
    try {
      // If the rate plan code was renamed, delete the old entry first
      if (originalCode && originalCode.toUpperCase() !== data.ratePlanCode.toUpperCase()) {
        await fetch(`${API}/api/settings/sku-mappings/${encodeURIComponent(originalCode)}`, { method: 'DELETE' })
      }
      const r = await fetch(`${API}/api/settings/sku-mappings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      if (!r.ok) throw new Error(await r.text())
      setMsg({ type: 'ok', text: 'Mapping saved.' })
      setEditCode(null)
      setEditOriginalCode(null)
      setAdding(false)
      setAddCode(''); setAddSku(''); setAddPrice(''); setAddNotes('')
      onRefresh()
    } catch (e) {
      setMsg({ type: 'err', text: e.message })
    } finally {
      setSaving(false)
    }
  }

  async function deleteMapping(code) {
    await fetch(`${API}/api/settings/sku-mappings/${encodeURIComponent(code)}`, { method: 'DELETE' })
    onRefresh()
  }

  // Quick-add from unmapped panel
  function quickAdd(code) {
    setAdding(true)
    setAddCode(code)
    setShowUnmapped(false)
  }

  return (
    <div className="space-y-4">
      {msg && (
        <div className={`px-4 py-2 rounded-lg text-sm ${msg.type === 'ok' ? 'bg-emerald-900/50 text-emerald-300' : 'bg-red-900/50 text-red-300'}`}>
          {msg.text}
          <button onClick={() => setMsg(null)} className="ml-3 text-slate-400 hover:text-white">✕</button>
        </div>
      )}

      {/* Unmapped codes banner */}
      {unmapped.length > 0 && (
        <div className="bg-amber-900/20 border border-amber-700/40 rounded-xl overflow-hidden">
          <button
            onClick={() => setShowUnmapped(v => !v)}
            className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-amber-900/10"
          >
            <div className="flex items-center gap-2">
              <span className="text-amber-400">⚠</span>
              <span className="text-sm font-medium text-amber-300">
                {unmapped.length} Unmapped Rate Plan Code{unmapped.length !== 1 ? 's' : ''}
              </span>
              <span className="text-xs text-amber-500">— these codes have no QB SKU assigned</span>
            </div>
            <span className="text-slate-500 text-xs">{showUnmapped ? '▲ Hide' : '▼ Show'}</span>
          </button>
          {showUnmapped && (
            <div className="px-4 pb-3">
              <div className="flex flex-wrap gap-2">
                {unmapped.map(u => (
                  <div key={u.ratePlanCode} className="flex items-center gap-1 bg-slate-800 border border-slate-600 rounded-lg px-2 py-1">
                    <span className="font-mono text-xs text-amber-300">{u.ratePlanCode}</span>
                    {u.deviceCount > 0 && (
                      <span className="text-xs text-slate-500">({u.deviceCount})</span>
                    )}
                    <button
                      onClick={() => quickAdd(u.ratePlanCode)}
                      className="ml-1 text-xs text-blue-400 hover:text-blue-300 font-medium"
                    >
                      Map →
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search mappings…"
          className="flex-1 min-w-[200px] bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button
          onClick={() => { setAdding(true); setEditCode(null) }}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg font-medium transition-colors"
        >
          + Add Mapping
        </button>
      </div>

      {/* Add form */}
      {adding && (
        <div className="bg-slate-800 border border-blue-500/40 rounded-xl p-4 space-y-3">
          <div className="text-sm font-semibold text-blue-300 mb-1">New Rate Plan → SKU Mapping</div>
          <div className="text-xs text-slate-500 mb-2">
            Enter either a <span className="text-amber-300">promo code</span> (e.g. <code className="font-mono bg-slate-700/60 px-1 rounded">SWELL-NOINS3</code>) or a
            <span className="text-amber-300"> billing plan name</span> from MyAdmin exactly as it appears
            (e.g. <code className="font-mono bg-slate-700/60 px-1 rounded">PROPLUS MODE</code>, <code className="font-mono bg-slate-700/60 px-1 rounded">BASE MODE: LIVE</code>). Most customers use a billing plan name.
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Rate Plan Code or Billing Plan Name *</label>
              <input value={addCode} onChange={e => setAddCode(e.target.value.toUpperCase())}
                placeholder="e.g. PROPLUS MODE or SWELL-NOINS3"
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 font-mono focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">QB SKU *</label>
              <select value={addSku} onChange={e => onAddSkuChange(e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500">
                <option value="">— select SKU —</option>
                {skuOptions.map(k => <option key={k} value={k}>{k}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Default Price</label>
              <input type="number" step="0.01" value={addPrice} onChange={e => setAddPrice(e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Notes</label>
              <input value={addNotes} onChange={e => setAddNotes(e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
          </div>
          <div className="flex gap-2">
            <button disabled={saving || !addCode.trim() || !addSku}
              onClick={() => saveMapping({ ratePlanCode: addCode, skuKey: addSku, defaultPrice: parseFloat(addPrice) || 0, notes: addNotes })}
              className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg font-medium">
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button onClick={() => { setAdding(false); setAddCode('') }} className="px-4 py-1.5 text-slate-400 hover:text-white text-sm">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Mappings table */}
      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <table className="w-full table-fixed text-sm">
          <colgroup>
            <col style={{ width: '20%' }} />
            <col style={{ width: '34%' }} />
            <col style={{ width: '13%' }} />
            <col style={{ width: '23%' }} className="hidden lg:table-column" />
            <col style={{ width: '10%' }} />
          </colgroup>
          <thead>
            <tr className="text-xs text-slate-500 border-b border-slate-700 bg-slate-750">
              <th className="text-left px-4 py-3 font-medium">Rate Plan Code</th>
              <th className="text-left px-4 py-3 font-medium">QB SKU</th>
              <th className="text-right px-4 py-3 font-medium">Default Price</th>
              <th className="text-left px-4 py-3 font-medium hidden lg:table-cell">Notes</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-500">
                  {mappings.length === 0 ? 'No mappings yet. Use the unmapped panel above to add some.' : 'No results.'}
                </td>
              </tr>
            ) : filtered.map(m => (
              <React.Fragment key={m.ratePlanCode}>
                {editCode === m.ratePlanCode ? (
                  <tr className="border-b border-slate-700/50 bg-slate-750">
                    <td className="px-4 py-2" colSpan={5}>
                      <div className="grid grid-cols-2 gap-3 mb-2">
                        <div>
                          <label className="block text-xs text-slate-400 mb-1">Rate Plan Code</label>
                          <input value={editForm.ratePlanCode || ''}
                            onChange={e => setEditForm(f => ({ ...f, ratePlanCode: e.target.value.toUpperCase() }))}
                            className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm font-mono text-slate-100 focus:outline-none focus:border-blue-500" />
                        </div>
                        <div>
                          <label className="block text-xs text-slate-400 mb-1">QB SKU</label>
                          <select value={editForm.skuKey}
                            onChange={e => {
                              const key = e.target.value
                              const found = catalog.find(s => s.skuKey === key)
                              setEditForm(f => ({ ...f, skuKey: key, defaultPrice: found ? found.defaultPrice : f.defaultPrice }))
                            }}
                            className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-slate-100 focus:outline-none focus:border-blue-500">
                            <option value="">— select SKU —</option>
                            {skuOptions.map(k => <option key={k} value={k}>{k}</option>)}
                          </select>
                        </div>
                        <div>
                          <label className="block text-xs text-slate-400 mb-1">Default Price</label>
                          <input type="number" step="0.01" value={editForm.defaultPrice}
                            onChange={e => setEditForm(f => ({ ...f, defaultPrice: e.target.value }))}
                            className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
                        </div>
                        <div>
                          <label className="block text-xs text-slate-400 mb-1">Notes</label>
                          <input value={editForm.notes || ''}
                            onChange={e => setEditForm(f => ({ ...f, notes: e.target.value }))}
                            className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <button disabled={saving}
                          onClick={() => saveMapping({ ...editForm, defaultPrice: parseFloat(editForm.defaultPrice) || 0 }, editOriginalCode)}
                          className="px-3 py-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded font-medium">
                          {saving ? 'Saving…' : 'Save'}
                        </button>
                        <button onClick={() => { setEditCode(null); setEditOriginalCode(null) }} className="px-3 py-1 text-slate-400 hover:text-white text-sm">Cancel</button>
                      </div>
                    </td>
                  </tr>
                ) : (
                  <tr className="border-b border-slate-700/50 hover:bg-slate-750 transition-colors group">
                    <td className="px-4 py-2.5 overflow-hidden">
                      <span className="font-mono text-xs bg-slate-700 text-amber-300 px-1.5 py-0.5 rounded block truncate" title={m.ratePlanCode}>
                        {m.ratePlanCode}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 overflow-hidden">
                      {m.skuKey ? (
                        <span className="font-mono text-xs bg-slate-700 text-blue-300 px-1.5 py-0.5 rounded block truncate" title={m.skuKey}>
                          {m.skuKey}
                        </span>
                      ) : (
                        <span className="text-slate-600 text-xs italic">not mapped</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-slate-200">
                      <div className="flex flex-col items-end gap-0.5">
                        <span>{fmtPrice(m.defaultPrice)}</span>
                        {(m.cost > 0) && (
                          <span className="text-xs text-slate-500" title="Our cost">cost {fmtPrice(m.cost)}</span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-slate-400 text-xs hidden lg:table-cell truncate" title={m.notes}>{m.notes || '—'}</td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button onClick={() => { setEditCode(m.ratePlanCode); setEditOriginalCode(m.ratePlanCode); setEditForm({ ...m }) }}
                          className="text-xs px-2 py-0.5 text-blue-400 hover:text-blue-300">Edit</button>
                        <DeleteBtn small onConfirm={() => deleteMapping(m.ratePlanCode)} />
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
//  TAB 3 — Customer Price Overrides
// ═══════════════════════════════════════════════════════════════════════════════
function CustomerOverridesTab({ overrides, catalog, onRefresh }) {
  const [search, setSearch] = useState('')
  const [adding, setAdding] = useState(false)
  const [addCust, setAddCust] = useState('')
  const [addSku, setAddSku] = useState('')
  const [addPrice, setAddPrice] = useState('')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 50

  const filtered = overrides.filter(o =>
    !search ||
    o.customerName.toLowerCase().includes(search.toLowerCase()) ||
    o.skuKey.toLowerCase().includes(search.toLowerCase())
  )

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  function onSkuChange(key) {
    setAddSku(key)
    const found = catalog.find(s => s.skuKey === key)
    if (found) setAddPrice(String(found.defaultPrice))
  }

  async function saveOverride() {
    setSaving(true)
    try {
      const r = await fetch(`${API}/api/settings/customer-overrides`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ customerName: addCust, skuKey: addSku, price: parseFloat(addPrice) || 0 }),
      })
      if (!r.ok) throw new Error(await r.text())
      setMsg({ type: 'ok', text: 'Override saved.' })
      setAdding(false); setAddCust(''); setAddSku(''); setAddPrice('')
      onRefresh()
    } catch (e) {
      setMsg({ type: 'err', text: e.message })
    } finally {
      setSaving(false)
    }
  }

  async function deleteOverride(id) {
    await fetch(`${API}/api/settings/customer-overrides/${encodeURIComponent(id)}`, { method: 'DELETE' })
    onRefresh()
  }

  return (
    <div className="space-y-4">
      {msg && (
        <div className={`px-4 py-2 rounded-lg text-sm ${msg.type === 'ok' ? 'bg-emerald-900/50 text-emerald-300' : 'bg-red-900/50 text-red-300'}`}>
          {msg.text}
          <button onClick={() => setMsg(null)} className="ml-3 text-slate-400 hover:text-white">✕</button>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <input
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
          placeholder="Search by customer or SKU…"
          className="flex-1 min-w-[200px] bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button
          onClick={() => setAdding(v => !v)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg font-medium transition-colors"
        >
          + Add Override
        </button>
      </div>

      {/* Add form */}
      {adding && (
        <div className="bg-slate-800 border border-blue-500/40 rounded-xl p-4 space-y-3">
          <div className="text-sm font-semibold text-blue-300 mb-1">New Per-Customer Price Override</div>
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-1">
              <label className="block text-xs text-slate-400 mb-1">Customer Name *</label>
              <input value={addCust} onChange={e => setAddCust(e.target.value)}
                placeholder="Exact QB name"
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">SKU *</label>
              <select value={addSku} onChange={e => onSkuChange(e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500">
                <option value="">— select —</option>
                {catalog.map(s => <option key={s.skuKey} value={s.skuKey}>{s.skuKey}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Price *</label>
              <input type="number" step="0.01" value={addPrice} onChange={e => setAddPrice(e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
          </div>
          <div className="flex gap-2">
            <button disabled={saving || !addCust.trim() || !addSku || !addPrice}
              onClick={saveOverride}
              className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg font-medium">
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button onClick={() => setAdding(false)} className="px-4 py-1.5 text-slate-400 hover:text-white text-sm">Cancel</button>
          </div>
        </div>
      )}

      {/* Table */}
      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <div className="px-4 py-2 border-b border-slate-700 flex items-center justify-between">
          <span className="text-xs text-slate-400">
            {filtered.length.toLocaleString()} override{filtered.length !== 1 ? 's' : ''}
            {search ? ' (filtered)' : ''}
          </span>
          {totalPages > 1 && (
            <div className="flex items-center gap-1 text-xs text-slate-400">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}
                className="px-2 py-0.5 rounded disabled:opacity-30 hover:bg-slate-700">‹</button>
              <span>Page {page} / {totalPages}</span>
              <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}
                className="px-2 py-0.5 rounded disabled:opacity-30 hover:bg-slate-700">›</button>
            </div>
          )}
        </div>
        <table className="w-full table-fixed text-sm">
          <colgroup>
            <col style={{ width: '32%' }} />
            <col style={{ width: '34%' }} />
            <col style={{ width: '12%' }} />
            <col style={{ width: '12%' }} className="hidden sm:table-column" />
            <col style={{ width: '10%' }} />
          </colgroup>
          <thead>
            <tr className="text-xs text-slate-500 border-b border-slate-700">
              <th className="text-left px-4 py-2 font-medium">Customer Name</th>
              <th className="text-left px-4 py-2 font-medium">SKU</th>
              <th className="text-right px-4 py-2 font-medium">Price</th>
              <th className="text-right px-4 py-2 font-medium hidden sm:table-cell">Default</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {paged.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-500">No overrides found.</td>
              </tr>
            ) : paged.map(o => {
              const catalogEntry = catalog.find(s => s.skuKey === o.skuKey)
              const isCustom = catalogEntry && o.price !== catalogEntry.defaultPrice
              return (
                <tr key={o.id} className="border-b border-slate-700/50 hover:bg-slate-750 transition-colors group">
                  <td className="px-4 py-2.5 text-slate-200 text-sm truncate" title={o.customerName}>{o.customerName}</td>
                  <td className="px-4 py-2.5 overflow-hidden">
                    <span className="font-mono text-xs bg-slate-700 text-blue-300 px-1.5 py-0.5 rounded block truncate" title={o.skuKey}>
                      {o.skuKey}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    <span className={isCustom ? 'text-amber-300 font-semibold' : 'text-slate-200'}>
                      {fmtPrice(o.price)}
                    </span>
                    {isCustom && <span className="ml-1 text-xs text-amber-500">custom</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-slate-500 text-xs hidden sm:table-cell">
                    {catalogEntry ? fmtPrice(catalogEntry.defaultPrice) : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <div className="opacity-0 group-hover:opacity-100 transition-opacity">
                      <DeleteBtn small onConfirm={() => deleteOverride(o.id)} />
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
//  TAB 4 — Import CSV
// ═══════════════════════════════════════════════════════════════════════════════

// Reusable drop-zone uploader card
function ImportCard({ title, description, columns, columnNote, endpoint, resultFields, onRefresh, acceptHint }) {
  const [file, setFile]         = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult]     = useState(null)
  const [error, setError]       = useState(null)
  const fileRef                 = useRef()

  async function handleUpload() {
    if (!file) return
    setUploading(true); setResult(null); setError(null)
    const fd = new FormData()
    fd.append('file', file)
    try {
      const r    = await fetch(`${API}${endpoint}`, { method: 'POST', body: fd })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || JSON.stringify(data))
      setResult(data)
      setFile(null)
      if (fileRef.current) fileRef.current.value = ''
      onRefresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-6 space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-slate-200 mb-1">{title}</h3>
        <p className="text-xs text-slate-400">{description}</p>
      </div>

      {/* Column layout reference */}
      <div className="bg-slate-700/50 rounded-lg p-4 text-xs text-slate-400">
        <div className="font-medium text-slate-300 mb-2">Expected Column Layout</div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-0.5 font-mono">
          {columns.map(([col, label]) => (
            <React.Fragment key={col}>
              <span className="text-blue-400">{col}</span>
              <span>{label}</span>
            </React.Fragment>
          ))}
        </div>
        {columnNote && <div className="mt-2 text-slate-500">{columnNote}</div>}
      </div>

      {/* Drop zone */}
      <div
        className="border-2 border-dashed border-slate-600 rounded-xl p-6 text-center cursor-pointer hover:border-blue-500 transition-colors"
        onClick={() => fileRef.current?.click()}
        onDragOver={e => e.preventDefault()}
        onDrop={e => { e.preventDefault(); setFile(e.dataTransfer.files[0]) }}
      >
        {file ? (
          <div>
            <div className="text-blue-400 text-2xl mb-2">📄</div>
            <div className="text-sm text-slate-200 font-medium">{file.name}</div>
            <div className="text-xs text-slate-400 mt-1">{(file.size / 1024).toFixed(1)} KB</div>
          </div>
        ) : (
          <div>
            <div className="text-slate-500 text-3xl mb-2">📂</div>
            <div className="text-sm text-slate-400">Drop a CSV here or click to browse</div>
            <div className="text-xs text-slate-500 mt-1">{acceptHint}</div>
          </div>
        )}
        <input ref={fileRef} type="file" accept=".csv,text/csv,text/plain" className="hidden"
          onChange={e => setFile(e.target.files[0])} />
      </div>

      <button
        disabled={!file || uploading}
        onClick={handleUpload}
        className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm font-medium rounded-xl transition-colors"
      >
        {uploading ? 'Importing…' : 'Import'}
      </button>

      {error && (
        <div className="bg-red-900/40 border border-red-700/40 rounded-lg px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {result && (
        <div className="bg-emerald-900/30 border border-emerald-700/40 rounded-lg px-4 py-3 space-y-2">
          <div className="text-sm font-medium text-emerald-300">✓ Import successful</div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-0.5 text-xs mt-1">
            {resultFields.map(({ key, label, bold }) => (
              result[key] !== undefined &&
              <React.Fragment key={key}>
                <span className="text-slate-400">{label}:</span>
                <span className={bold ? 'font-semibold text-white' : 'text-slate-200'}>
                  {typeof result[key] === 'number' ? result[key].toLocaleString() : result[key]}
                </span>
              </React.Fragment>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function ImportCsvTab({ onRefresh }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

      {/* ── Card 1: QB Invoice Export ── */}
      <ImportCard
        title="QB Monthly Invoice Export"
        description="Upload a QuickBooks monthly invoice export to populate the SKU catalog and per-customer price overrides. SKU keys are extracted from the Item column (Group:SKU Name (Description) format)."
        columns={[
          ['Col N (13)', 'Customer Name'],
          ['Col P (15)', 'Item / SKU path'],
          ['Col R (17)', 'Qty'],
          ['Col T (19)', 'Sales Price (per customer)'],
        ]}
        columnNote="Note: doubled-comma format — every other column is blank."
        endpoint="/api/settings/import-qb-skus"
        acceptHint="QuickBooks monthly invoice export (.csv)"
        resultFields={[
          { key: 'skusAdded',       label: 'SKUs added' },
          { key: 'skusUpdated',     label: 'SKUs updated' },
          { key: 'ovrAdded',        label: 'Customer prices added' },
          { key: 'ovrUpdated',      label: 'Customer prices updated' },
          { key: 'mappingsSynced',  label: 'Rate plan prices synced' },
          { key: 'totalSkus',       label: 'Total SKUs in catalog', bold: true },
          { key: 'totalCustomers',  label: 'Customers processed', bold: true },
        ]}
        onRefresh={onRefresh}
      />

      {/* ── Card 2: QB Item Price List ── */}
      <ImportCard
        title="QB Item Price List"
        description="Upload a QuickBooks Item Price List to fill pricing gaps and add new SKUs. Existing SKUs with a price already set are never overwritten — this is the source of truth for default pricing only."
        columns={[
          ['Col C (2)', 'Item (Group:SKU Name)'],
          ['Col E (4)', 'Description'],
          ['Col G (6)', 'Our Cost'],
          ['Col I (8)', 'Price to Customer'],
        ]}
        columnNote="Note: Item Price List uses standard (non-doubled) column format."
        endpoint="/api/settings/import-price-list"
        acceptHint="QuickBooks Item Price List export (.csv)"
        resultFields={[
          { key: 'added',           label: 'New SKUs added' },
          { key: 'updated',         label: 'Prices filled ($0 → real price)' },
          { key: 'skipped',         label: 'Skipped (price already set)' },
          { key: 'mappingsSynced',  label: 'Rate plan prices synced' },
          { key: 'totalItems',      label: 'Items in file' },
          { key: 'totalSkus',       label: 'Total SKUs in catalog', bold: true },
        ]}
        onRefresh={onRefresh}
      />

    </div>
  )
}


// ===============================================================================
//  TAB 5 — Customer-Specific Rate Plan Mappings
//  Maps (customerName + ratePlanCode) -> skuKey, overriding the global mapping.
// ===============================================================================
function CustRatePlanTab({ custMappings, catalog, onRefresh }) {
  const [search, setSearch]     = useState('')
  const [adding, setAdding]     = useState(false)
  const [addCust, setAddCust]   = useState('')
  const [addCode, setAddCode]   = useState('')
  const [addSku,  setAddSku]    = useState('')
  const [addPrice, setAddPrice] = useState('')
  const [addNotes, setAddNotes] = useState('')
  const [saving, setSaving]     = useState(false)
  const [msg, setMsg]           = useState(null)

  const skuOptions = catalog.map(s => s.skuKey).sort()

  const filtered = custMappings.filter(m =>
    !search ||
    m.customerName.toLowerCase().includes(search.toLowerCase()) ||
    m.ratePlanCode.toLowerCase().includes(search.toLowerCase()) ||
    (m.skuKey || '').toLowerCase().includes(search.toLowerCase())
  )

  function onAddSkuChange(key) {
    setAddSku(key)
    const found = catalog.find(s => s.skuKey === key)
    if (found) setAddPrice(String(found.defaultPrice))
  }

  async function saveMapping() {
    setSaving(true)
    try {
      const r = await fetch(`${API}/api/settings/customer-rate-plan-mappings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          customerName:  addCust.trim(),
          ratePlanCode:  addCode.trim().toUpperCase(),
          skuKey:        addSku,
          defaultPrice:  parseFloat(addPrice) || 0,
          notes:         addNotes,
        }),
      })
      if (!r.ok) throw new Error(await r.text())
      setMsg({ type: 'ok', text: 'Customer mapping saved.' })
      setAdding(false)
      setAddCust(''); setAddCode(''); setAddSku(''); setAddPrice(''); setAddNotes('')
      onRefresh()
    } catch (e) {
      setMsg({ type: 'err', text: e.message })
    } finally {
      setSaving(false)
    }
  }

  async function deleteMapping(id) {
    try {
      await fetch(`${API}/api/settings/customer-rate-plan-mappings/${encodeURIComponent(id)}`, { method: 'DELETE' })
      onRefresh()
    } catch (e) {
      setMsg({ type: 'err', text: e.message })
    }
  }

  return (
    <div className="space-y-4">
      {/* Explainer */}
      <div className="bg-blue-900/20 border border-blue-700/40 rounded-xl px-4 py-3 text-sm text-blue-300">
        <span className="font-semibold text-blue-200">Customer-specific overrides</span>
        {' '}— when the same promo code means different SKUs for different customers,
        add an entry here. These take priority over the global Rate Plan Mappings.
      </div>

      {msg && (
        <div className={`px-4 py-2 rounded-lg text-sm ${msg.type === 'ok' ? 'bg-emerald-900/50 text-emerald-300' : 'bg-red-900/50 text-red-300'}`}>
          {msg.text}
          <button onClick={() => setMsg(null)} className="ml-3 text-slate-400 hover:text-white">x</button>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex items-center gap-3 flex-wrap">
        <input
          value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search by customer, rate plan code, or SKU..."
          className="flex-1 min-w-[240px] bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button onClick={() => setAdding(true)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg font-medium transition-colors">
          + Add Override
        </button>
      </div>

      {/* Add form */}
      {adding && (
        <div className="bg-slate-800 border border-blue-500/40 rounded-xl p-4 space-y-3">
          <div className="text-sm font-semibold text-blue-300 mb-1">New Customer Rate Plan Override</div>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <label className="block text-xs text-slate-400 mb-1">Customer Name * (exact QB name)</label>
              <input value={addCust} onChange={e => setAddCust(e.target.value)}
                placeholder="e.g. Enterprise Holdings"
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Rate Plan Code * (promo code)</label>
              <input value={addCode} onChange={e => setAddCode(e.target.value.toUpperCase())}
                placeholder="e.g. CELU-TP-250"
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 font-mono focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">QB SKU *</label>
              <select value={addSku} onChange={e => onAddSkuChange(e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500">
                <option value="">-- select SKU --</option>
                {skuOptions.map(k => <option key={k} value={k}>{k}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Default Price</label>
              <input type="number" step="0.01" value={addPrice} onChange={e => setAddPrice(e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Notes</label>
              <input value={addNotes} onChange={e => setAddNotes(e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
          </div>
          <div className="flex gap-2">
            <button disabled={saving || !addCust.trim() || !addCode.trim() || !addSku}
              onClick={saveMapping}
              className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg font-medium">
              {saving ? 'Saving...' : 'Save'}
            </button>
            <button onClick={() => { setAdding(false); setAddCust(''); setAddCode(''); setAddSku(''); setAddPrice(''); setAddNotes('') }}
              className="px-4 py-1.5 text-slate-400 hover:text-white text-sm">Cancel</button>
          </div>
        </div>
      )}

      {/* Table */}
      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <div className="px-4 py-2 border-b border-slate-700">
          <span className="text-xs text-slate-400">
            {filtered.length.toLocaleString()} customer-specific mapping{filtered.length !== 1 ? 's' : ''}
            {search ? ' (filtered)' : ''}
          </span>
        </div>
        <table className="w-full table-fixed text-sm">
          <colgroup>
            <col style={{ width: '26%' }} />
            <col style={{ width: '16%' }} />
            <col style={{ width: '26%' }} />
            <col style={{ width: '10%' }} />
            <col style={{ width: '14%' }} className="hidden lg:table-column" />
            <col style={{ width: '8%' }} />
          </colgroup>
          <thead>
            <tr className="text-xs text-slate-500 border-b border-slate-700 bg-slate-900/40">
              <th className="text-left px-4 py-2.5 font-medium">Customer Name</th>
              <th className="text-left px-4 py-2.5 font-medium">Rate Plan Code</th>
              <th className="text-left px-4 py-2.5 font-medium">QB SKU</th>
              <th className="text-right px-4 py-2.5 font-medium">Price</th>
              <th className="text-left px-4 py-2.5 font-medium hidden lg:table-cell">Notes</th>
              <th className="px-4 py-2.5"></th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-slate-500">
                  {custMappings.length === 0
                    ? 'No customer-specific overrides yet. Add one above when a promo code maps to different SKUs per customer.'
                    : 'No results.'}
                </td>
              </tr>
            ) : filtered.map(m => (
              <React.Fragment key={m.id}>
                <tr className="border-b border-slate-700/30 hover:bg-slate-750/60 transition-colors group">
                  <td className="px-4 py-2.5 text-slate-200 text-sm truncate" title={m.customerName}>{m.customerName}</td>
                  <td className="px-4 py-2.5">
                    <span className="font-mono text-xs bg-slate-700 text-amber-300 px-1.5 py-0.5 rounded">{m.ratePlanCode}</span>
                  </td>
                  <td className="px-4 py-2.5 overflow-hidden">
                    {m.skuKey ? (
                      <span className="font-mono text-xs bg-slate-700 text-blue-300 px-1.5 py-0.5 rounded block truncate" title={m.skuKey}>{m.skuKey}</span>
                    ) : (
                      <span className="text-slate-600 text-xs italic">not set</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-slate-200 whitespace-nowrap">{fmtPrice(m.defaultPrice)}</td>
                  <td className="px-4 py-2.5 text-slate-400 text-xs hidden lg:table-cell truncate" title={m.notes}>{m.notes || '--'}</td>
                  <td className="px-4 py-2.5 text-right">
                    <div className="opacity-0 group-hover:opacity-100 transition-opacity">
                      <DeleteBtn small onConfirm={() => deleteMapping(m.id)} />
                    </div>
                  </td>
                </tr>
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}


// ═══════════════════════════════════════════════════════════════════════════════
//  TAB — Serial Prefix Mappings
// ═══════════════════════════════════════════════════════════════════════════════
function SerialPrefixTab({ prefixMappings, catalog, onRefresh }) {
  const [prefix,     setPrefix]     = useState('')
  const [skuKey,     setSkuKey]     = useState('')
  const [notes,      setNotes]      = useState('')
  const [dmExcluded, setDmExcluded] = useState(false)
  const [saving,     setSaving]     = useState(false)
  const [error,      setError]      = useState(null)
  const [search,     setSearch]     = useState('')

  const skuOptions = [...catalog].sort((a, b) =>
    (a.skuKey || '').localeCompare(b.skuKey || '')
  )

  const filtered = (prefixMappings || []).filter(p => {
    const q = search.toLowerCase()
    return !q
      || (p.prefix || '').toLowerCase().includes(q)
      || (p.skuKey || '').toLowerCase().includes(q)
      || (p.notes  || '').toLowerCase().includes(q)
  })

  async function handleAdd(e) {
    e.preventDefault()
    if (!prefix.trim()) return
    setSaving(true); setError(null)
    try {
      const res = await fetch(`${API}/api/settings/serial-prefix-mappings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prefix: prefix.trim().toUpperCase(), skuKey, notes, dmExcluded }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${res.status}`)
      }
      setPrefix(''); setSkuKey(''); setNotes(''); setDmExcluded(false)
      await onRefresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(pfx) {
    try {
      await fetch(`${API}/api/settings/serial-prefix-mappings/${encodeURIComponent(pfx)}`, {
        method: 'DELETE',
      })
      await onRefresh()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="space-y-6">

      {/* ── Info banner ──────────────────────────────────────────── */}
      <div className="bg-slate-800/60 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-400 leading-relaxed">
        Serial prefixes are matched against the first 2 characters of a device serial number.
        Entries with a <span className="text-amber-400 font-medium">QB SKU</span> resolve directly
        to that SKU (bypassing promoCode/billing-plan lookup).
        Entries marked <span className="text-blue-400 font-medium">DM Excluded</span> are skipped
        during prorated invoice calculations (Digital Matter devices).
      </div>

      {/* ── Add form ─────────────────────────────────────────────── */}
      <form onSubmit={handleAdd}
        className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-4">
        <h3 className="text-sm font-semibold text-slate-200 uppercase tracking-wide">
          Add / Update Prefix Mapping
        </h3>

        {error && (
          <div className="text-sm text-red-400 bg-red-900/30 border border-red-800 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {/* Prefix */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 font-medium">Serial Prefix *</label>
            <input
              value={prefix}
              onChange={e => setPrefix(e.target.value.toUpperCase().slice(0, 6))}
              placeholder="e.g. C3"
              maxLength={6}
              className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm
                text-slate-200 placeholder-slate-600 focus:outline-none focus:border-blue-500
                font-mono uppercase tracking-widest"
            />
          </div>

          {/* SKU dropdown */}
          <div className="flex flex-col gap-1 sm:col-span-1">
            <label className="text-xs text-slate-400 font-medium">QB SKU (leave blank if DM-excluded only)</label>
            <select
              value={skuKey}
              onChange={e => setSkuKey(e.target.value)}
              className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm
                text-slate-200 focus:outline-none focus:border-blue-500"
            >
              <option value="">— none —</option>
              {skuOptions.map(s => (
                <option key={s.skuKey} value={s.skuKey}>{s.skuKey}</option>
              ))}
            </select>
          </div>

          {/* Notes */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 font-medium">Notes</label>
            <input
              value={notes}
              onChange={e => setNotes(e.target.value)}
              placeholder="Optional description"
              className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm
                text-slate-200 placeholder-slate-600 focus:outline-none focus:border-blue-500"
            />
          </div>

          {/* DM Excluded toggle + submit */}
          <div className="flex flex-col gap-1 justify-end">
            <label className="flex items-center gap-2 cursor-pointer select-none mb-1">
              <div
                onClick={() => setDmExcluded(v => !v)}
                className={`relative w-10 h-5 rounded-full transition-colors ${
                  dmExcluded ? 'bg-blue-600' : 'bg-slate-600'
                }`}
              >
                <div className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white
                  transition-transform ${dmExcluded ? 'translate-x-5' : 'translate-x-0'}`} />
              </div>
              <span className="text-xs text-slate-400">DM Excluded</span>
            </label>
            <button
              type="submit"
              disabled={saving || !prefix.trim()}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40
                text-white text-sm font-medium rounded-lg transition-colors"
            >
              {saving ? 'Saving…' : 'Save Mapping'}
            </button>
          </div>
        </div>
      </form>

      {/* ── Table ────────────────────────────────────────────────── */}
      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        {/* Table toolbar */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700">
          <span className="text-sm font-semibold text-slate-200">
            Serial Prefix Mappings
            <span className="ml-2 text-xs text-slate-500 font-normal">
              ({(prefixMappings || []).length} entries)
            </span>
          </span>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm">🔍</span>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Filter…"
              className="pl-8 pr-7 py-1.5 bg-slate-900 border border-slate-600 rounded-lg
                text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-blue-500 w-44"
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500
                  hover:text-slate-200 transition-colors leading-none"
                title="Clear"
              >✕</button>
            )}
          </div>
        </div>

        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 bg-slate-900/50">
              <th className="px-4 py-2.5 text-left text-xs text-slate-400 font-semibold uppercase tracking-wide w-24">Prefix</th>
              <th className="px-4 py-2.5 text-left text-xs text-slate-400 font-semibold uppercase tracking-wide">QB SKU</th>
              <th className="px-4 py-2.5 text-left text-xs text-slate-400 font-semibold uppercase tracking-wide w-16 text-center">DM Excl.</th>
              <th className="px-4 py-2.5 text-left text-xs text-slate-400 font-semibold uppercase tracking-wide">Notes</th>
              <th className="px-4 py-2.5 w-12" />
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-500 text-sm">
                  {search ? 'No entries match your filter.' : 'No serial prefix mappings defined.'}
                </td>
              </tr>
            )}
            {filtered.map(p => (
              <tr key={p.prefix} className="border-b border-slate-700/50 hover:bg-slate-700/30 group transition-colors">
                <td className="px-4 py-2.5">
                  <code className="text-blue-300 font-mono font-semibold text-sm bg-slate-900/60 px-1.5 py-0.5 rounded">
                    {p.prefix}
                  </code>
                </td>
                <td className="px-4 py-2.5">
                  {p.skuKey
                    ? <Badge color="green">{p.skuKey}</Badge>
                    : <span className="text-slate-600 text-xs italic">—</span>
                  }
                </td>
                <td className="px-4 py-2.5 text-center">
                  {p.dmExcluded
                    ? <Badge color="blue">DM</Badge>
                    : <span className="text-slate-700">—</span>
                  }
                </td>
                <td className="px-4 py-2.5 text-slate-400 text-xs max-w-xs truncate" title={p.notes}>
                  {p.notes || <span className="text-slate-700">—</span>}
                </td>
                <td className="px-4 py-2.5 text-right">
                  <div className="opacity-0 group-hover:opacity-100 transition-opacity">
                    <DeleteBtn small onConfirm={() => handleDelete(p.prefix)} />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}


// ===============================================================================
//  ROOT COMPONENT
// ═══════════════════════════════════════════════════════════════════════════════
export default function Settings() {
  const [activeTab, setActiveTab] = useState('catalog')
  const [catalog,        setCatalog]        = useState([])
  const [mappings,       setMappings]       = useState([])
  const [overrides,      setOverrides]      = useState([])
  const [custMappings,   setCustMappings]   = useState([])
  const [unmapped,       setUnmapped]       = useState([])
  const [summary,        setSummary]        = useState(null)
  const [loading,        setLoading]        = useState(true)
  const [prefixMappings, setPrefixMappings] = useState([])

  const [fetchError, setFetchError] = useState(null)

  const fetchAll = useCallback(async () => {
    setFetchError(null)
    // Use allSettled so one failing endpoint doesn't cancel the others
    const [catR, mapR, ovrR, crpR, unmR, sumR, sprR] = await Promise.allSettled([
      fetch(`${API}/api/settings/sku-catalog`),
      fetch(`${API}/api/settings/sku-mappings`),
      fetch(`${API}/api/settings/customer-overrides`),
      fetch(`${API}/api/settings/customer-rate-plan-mappings`),
      fetch(`${API}/api/settings/unmapped-rate-plans`),
      fetch(`${API}/api/settings/summary`),
      fetch(`${API}/api/settings/serial-prefix-mappings`),
    ])
    const errors = []
    try {
      if (catR.status === 'fulfilled' && catR.value.ok) setCatalog(await catR.value.json())
      else errors.push(`catalog: ${catR.reason?.message || 'HTTP ' + catR.value?.status}`)
    } catch(e) { errors.push(`catalog parse: ${e.message}`) }
    try {
      if (mapR.status === 'fulfilled' && mapR.value.ok) setMappings(await mapR.value.json())
      else errors.push(`mappings: ${mapR.reason?.message || 'HTTP ' + mapR.value?.status}`)
    } catch(e) { errors.push(`mappings parse: ${e.message}`) }
    try {
      if (ovrR.status === 'fulfilled' && ovrR.value.ok) setOverrides(await ovrR.value.json())
      else errors.push(`overrides: ${ovrR.reason?.message || 'HTTP ' + ovrR.value?.status}`)
    } catch(e) { errors.push(`overrides parse: ${e.message}`) }
    try {
      if (crpR.status === 'fulfilled' && crpR.value.ok) setCustMappings(await crpR.value.json())
      // customer rate plan mappings non-critical — don't surface as error
    } catch(e) { /* non-critical */ }
    try {
      if (unmR.status === 'fulfilled' && unmR.value.ok) {
        const d = await unmR.value.json(); setUnmapped(d.unmapped || [])
      }
      // unmapped failing is non-critical
    } catch(e) { /* non-critical */ }
    try {
      if (sumR.status === 'fulfilled' && sumR.value.ok) setSummary(await sumR.value.json())
      // summary failing is non-critical
    } catch(e) { /* non-critical */ }
    try {
      if (sprR.status === 'fulfilled' && sprR.value.ok) setPrefixMappings(await sprR.value.json())
      // serial prefix mappings non-critical
    } catch(e) { /* non-critical */ }
    if (errors.length) setFetchError(`Fetch errors — ${errors.join(', ')}`)
    setLoading(false)
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div>
        <h2 className="text-xl font-bold text-white">Settings</h2>
        <p className="text-sm text-slate-400 mt-0.5">
          Manage QuickBooks SKU catalog, rate plan mappings, and per-customer price overrides.
        </p>
      </div>

      {/* Fetch error banner */}
      {fetchError && (
        <div className="flex items-start gap-3 bg-red-900/40 border border-red-700 rounded-xl px-4 py-3 text-sm text-red-300">
          <span className="text-red-400 mt-0.5">⚠</span>
          <div className="flex-1">
            <span className="font-semibold text-red-200">Failed to load settings data: </span>
            {fetchError}
            <div className="mt-1 text-red-400 text-xs">
              Open <a href="http://127.0.0.1:8001/api/settings/debug" target="_blank" rel="noreferrer" className="underline hover:text-red-200">http://127.0.0.1:8001/api/settings/debug</a> in your browser to diagnose the file paths being used.
            </div>
          </div>
          <button onClick={() => setFetchError(null)} className="text-red-500 hover:text-red-300 ml-2 text-lg leading-none">×</button>
        </div>
      )}

      {/* Summary stat cards */}
      {summary && (
        <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
          <StatCard icon="📦" label="SKUs in Catalog"        value={summary.skuCount}              color="blue"   />
          <StatCard icon="🔗" label="Rate Plan Mappings"     value={summary.mappingCount}           color="green"  />
          <StatCard icon="👤" label="Customer Rate Plans"    value={summary.custMappingCount ?? 0}  color="amber"  />
          <StatCard icon="💲" label="Customer Overrides"     value={summary.overrideCount.toLocaleString()} color="purple" />
          <StatCard icon="🔢" label="Serial Prefix Mappings" value={summary.serialPrefixCount ?? prefixMappings.length} color="slate" />
          <StatCard icon="⚠" label="Unmapped Codes"         value={summary.unmappedCount}          color={summary.unmappedCount > 0 ? 'red' : 'green'} />
        </div>
      )}

      {/* Tab navigation */}
      <div className="flex items-center gap-1 bg-slate-800 border border-slate-700 rounded-xl p-1 w-fit flex-wrap">
        <TabBtn active={activeTab === 'catalog'}       onClick={() => setActiveTab('catalog')}>SKU Catalog</TabBtn>
        <TabBtn active={activeTab === 'mappings'}      onClick={() => setActiveTab('mappings')}>
          Rate Plan Mappings
          {unmapped.length > 0 && (
            <span className="ml-1.5 inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 text-xs bg-amber-500 text-white rounded-full font-bold">
              {unmapped.length}
            </span>
          )}
        </TabBtn>
        <TabBtn active={activeTab === 'custRatePlans'} onClick={() => setActiveTab('custRatePlans')}>Customer Rate Plans</TabBtn>
        <TabBtn active={activeTab === 'overrides'}     onClick={() => setActiveTab('overrides')}>Customer Prices</TabBtn>
        <TabBtn active={activeTab === 'serialPrefixes'} onClick={() => setActiveTab('serialPrefixes')}>Serial Prefixes</TabBtn>
        <TabBtn active={activeTab === 'import'}        onClick={() => setActiveTab('import')}>Import CSV</TabBtn>
      </div>

      {/* Tab content */}
      {loading ? (
        <div className="flex items-center gap-3 text-slate-400 py-12">
          <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
          </svg>
          Loading settings…
        </div>
      ) : (
        <>
          {activeTab === 'catalog'        && <SkuCatalogTab      catalog={catalog}   onRefresh={fetchAll} />}
          {activeTab === 'mappings'       && <RatePlanMappingsTab mappings={mappings} catalog={catalog} unmapped={unmapped} onRefresh={fetchAll} />}
          {activeTab === 'custRatePlans'  && <CustRatePlanTab custMappings={custMappings} catalog={catalog} onRefresh={fetchAll} />}
          {activeTab === 'overrides'      && <CustomerOverridesTab overrides={overrides} catalog={catalog} onRefresh={fetchAll} />}
          {activeTab === 'serialPrefixes' && <SerialPrefixTab prefixMappings={prefixMappings} catalog={catalog} onRefresh={fetchAll} />}
          {activeTab === 'import'         && <ImportCsvTab        onRefresh={fetchAll} />}
        </>
      )}
    </div>
  )
}

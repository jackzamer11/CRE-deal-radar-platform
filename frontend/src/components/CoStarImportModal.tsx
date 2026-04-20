import { useRef, useState } from 'react'
import { X, Upload, FileSpreadsheet, CheckCircle, AlertTriangle, RefreshCw, Filter } from 'lucide-react'
import { importCoStarExport } from '../api/client'
import type { CoStarImportResult } from '../api/client'

interface Props {
  onClose: () => void
  onDone: () => void
}

export default function CoStarImportModal({ onClose, onDone }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile]           = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult]       = useState<CoStarImportResult | null>(null)
  const [apiError, setApiError]   = useState<string | null>(null)
  const [showErrors, setShowErrors] = useState(false)
  const [showUnmapped, setShowUnmapped] = useState(false)

  const handleFile = (f: File) => {
    setFile(f)
    setResult(null)
    setApiError(null)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }

  const handleUpload = async () => {
    if (!file) return
    setUploading(true)
    setApiError(null)
    setResult(null)
    try {
      const res = await importCoStarExport(file)
      setResult(res)
      if (res.inserted > 0 || res.updated > 0) onDone()
    } catch (e: any) {
      setApiError(
        e?.response?.data?.detail
          || e?.message
          || 'Import failed — check the server log for details.'
      )
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-surface-card border border-surface-border rounded-2xl shadow-2xl w-full max-w-xl mx-4 max-h-[90vh] overflow-y-auto">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-surface-border sticky top-0 bg-surface-card z-10">
          <div className="flex items-center gap-2.5">
            <FileSpreadsheet size={18} className="text-accent-blue" />
            <h2 className="text-sm font-bold text-ink-primary">Import CoStar Export</h2>
          </div>
          <button onClick={onClose} className="text-ink-muted hover:text-ink-primary p-1">
            <X size={17} />
          </button>
        </div>

        <div className="p-6 space-y-5">

          {/* Instructions */}
          {!result && (
            <div className="text-xs text-ink-muted bg-surface-muted rounded-lg p-3 space-y-1">
              <p className="font-semibold text-ink-secondary">Expected CoStar columns (22 required):</p>
              <p>Property Address · Building Class · RBA · Submarket Name · City · State · Zip ·
                 Year Built · Year Renovated · Last Sale Date · Last Sale Price · Origination Amount ·
                 Origination Date · Maturity Date · Percent Leased · Rent/SF/Yr · Building Status ·
                 For Sale Status · For Sale Price · True Owner Contact · True Owner Name · True Owner Phone</p>
              <p className="text-[10px] mt-1 text-ink-muted">Rows are filtered to VA + mapped NoVA submarkets + Existing buildings only.</p>
            </div>
          )}

          {/* Drop zone */}
          {!result && (
            <div
              onDrop={handleDrop}
              onDragOver={e => e.preventDefault()}
              onClick={() => inputRef.current?.click()}
              className={`
                border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors
                ${file
                  ? 'border-accent-blue/50 bg-accent-blue/5'
                  : 'border-surface-border hover:border-accent-blue/40 hover:bg-surface-muted'}
              `}
            >
              <input
                ref={inputRef}
                type="file"
                accept=".csv,.xlsx,.xls"
                className="hidden"
                onChange={e => e.target.files?.[0] && handleFile(e.target.files[0])}
              />
              <Upload size={22} className={`mx-auto mb-3 ${file ? 'text-accent-blue' : 'text-ink-muted'}`} />
              {file ? (
                <>
                  <p className="text-sm font-semibold text-ink-primary">{file.name}</p>
                  <p className="text-xs text-ink-muted mt-1">
                    {(file.size / 1024).toFixed(1)} KB · Click to change
                  </p>
                </>
              ) : (
                <>
                  <p className="text-sm text-ink-secondary">Drop a CoStar .csv or .xlsx export here</p>
                  <p className="text-xs text-ink-muted mt-1">or click to browse</p>
                </>
              )}
            </div>
          )}

          {/* API-level error */}
          {apiError && (
            <div className="flex items-start gap-2.5 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/25">
              <AlertTriangle size={14} className="text-red-400 mt-0.5 flex-shrink-0" />
              <p className="text-xs text-red-300">{apiError}</p>
            </div>
          )}

          {/* Results */}
          {result && (
            <div className="space-y-4">
              {/* Row counts */}
              <div className="bg-surface-muted rounded-lg p-3">
                <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-2">Input</div>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-ink-secondary">Total rows</span>
                  <span className="text-xs font-bold mono text-ink-primary">{result.total_rows}</span>
                </div>
              </div>

              {/* Filter pipeline */}
              <div className="bg-surface-muted rounded-lg p-3">
                <div className="flex items-center gap-1.5 mb-2">
                  <Filter size={11} className="text-ink-muted" />
                  <div className="text-[10px] text-ink-muted uppercase tracking-wider">Filter Pipeline</div>
                </div>
                <div className="space-y-1.5">
                  <FilterRow label="Removed — Non-VA state"       count={result.filtered_state}     color="text-red-400" />
                  <FilterRow label="Removed — Unmapped submarket" count={result.filtered_submarket} color="text-amber-400" />
                  <FilterRow label="Removed — Not Existing status" count={result.filtered_status}   color="text-amber-400" />
                </div>
              </div>

              {/* Import results */}
              <div className="grid grid-cols-3 gap-2">
                <StatBox value={result.inserted} label="Inserted" color="emerald" />
                <StatBox value={result.updated}  label="Updated"  color="blue" />
                <StatBox value={result.skipped}  label="Skipped"  color={result.skipped > 0 ? 'amber' : 'muted'} />
              </div>

              {(result.inserted > 0 || result.updated > 0) && (
                <div className="flex items-center gap-2 text-xs text-emerald-400">
                  <CheckCircle size={13} />
                  Signals computed automatically. Update in-place rent manually for accurate scoring.
                </div>
              )}

              {/* Unmapped submarkets */}
              {result.unmapped_submarkets.length > 0 && (
                <div>
                  <button
                    onClick={() => setShowUnmapped(u => !u)}
                    className="flex items-center gap-1.5 text-xs text-amber-400 hover:text-amber-300 font-medium"
                  >
                    <AlertTriangle size={12} />
                    {showUnmapped ? 'Hide' : 'Show'} unmapped submarkets ({result.unmapped_submarkets.length})
                  </button>
                  {showUnmapped && (
                    <div className="mt-2 rounded-lg border border-surface-border bg-surface-muted p-3">
                      <p className="text-[10px] text-ink-muted mb-2">
                        Add these to COSTAR_SUBMARKET_MAP in properties.py to import them next time:
                      </p>
                      <ul className="space-y-1">
                        {result.unmapped_submarkets.map(s => (
                          <li key={s} className="text-xs font-mono text-amber-400">{s}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {/* Skipped row errors */}
              {result.errors.length > 0 && (
                <div>
                  <button
                    onClick={() => setShowErrors(e => !e)}
                    className="flex items-center gap-1.5 text-xs text-amber-400 hover:text-amber-300 font-medium"
                  >
                    <AlertTriangle size={12} />
                    {showErrors ? 'Hide' : 'Show'} skipped rows ({result.errors.length})
                  </button>
                  {showErrors && (
                    <div className="mt-2 max-h-48 overflow-y-auto rounded-lg border border-surface-border">
                      <table className="w-full text-[11px]">
                        <thead>
                          <tr className="bg-surface-muted text-ink-muted">
                            <th className="px-3 py-2 text-left font-medium">Row</th>
                            <th className="px-3 py-2 text-left font-medium">Address</th>
                            <th className="px-3 py-2 text-left font-medium">Reason</th>
                          </tr>
                        </thead>
                        <tbody>
                          {result.errors.map((e, i) => (
                            <tr key={i} className="border-t border-surface-border">
                              <td className="px-3 py-2 mono text-ink-muted">{e.row}</td>
                              <td className="px-3 py-2 text-ink-secondary max-w-[140px] truncate" title={e.address}>
                                {e.address}
                              </td>
                              <td className="px-3 py-2 text-amber-400">{e.reason}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              <button
                onClick={() => { setResult(null); setFile(null) }}
                className="text-xs text-ink-muted hover:text-ink-primary underline"
              >
                Import another file
              </button>
            </div>
          )}
        </div>

        {/* Footer */}
        {!result && (
          <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-surface-border">
            <button
              onClick={onClose}
              className="px-4 py-2 text-xs text-ink-muted hover:text-ink-primary transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleUpload}
              disabled={!file || uploading}
              className="flex items-center gap-2 px-5 py-2 rounded-lg bg-accent-blue text-white
                         text-xs font-semibold hover:bg-accent-blueDim transition-colors
                         disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {uploading
                ? <><RefreshCw size={13} className="animate-spin" /> Processing…</>
                : <><Upload size={13} /> Import</>}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function FilterRow({ label, count, color }: { label: string; count: number; color: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-ink-muted">{label}</span>
      <span className={`text-xs font-bold mono ${count > 0 ? color : 'text-ink-muted'}`}>{count}</span>
    </div>
  )
}

function StatBox({ value, label, color }: { value: number; label: string; color: string }) {
  const colorMap: Record<string, string> = {
    emerald: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400',
    blue:    'bg-accent-blue/10 border-accent-blue/20 text-accent-blue',
    amber:   'bg-amber-500/10 border-amber-500/20 text-amber-400',
    muted:   'bg-surface-muted border-surface-border text-ink-muted',
  }
  const cls = colorMap[color] || colorMap.muted
  return (
    <div className={`rounded-lg border p-3 text-center ${cls}`}>
      <div className="text-xl font-bold mono">{value}</div>
      <div className="text-[10px] text-ink-muted uppercase tracking-wider mt-0.5">{label}</div>
    </div>
  )
}

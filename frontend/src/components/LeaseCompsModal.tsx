import { useRef, useState } from 'react'
import {
  X, Upload, FileText, CheckCircle, AlertTriangle, RefreshCw, Clock,
} from 'lucide-react'
import {
  previewLeaseComps, confirmLeaseComps,
  type LeaseCompsPreviewResult,
} from '../api/client'

interface Props {
  onClose: () => void
  onDone: () => void   // refresh parent (companies changed)
}

export default function LeaseCompsModal({ onClose, onDone }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile]             = useState<File | null>(null)
  const [processing, setProcessing] = useState(false)
  const [result, setResult]         = useState<LeaseCompsPreviewResult | null>(null)
  const [apiError, setApiError]     = useState<string | null>(null)

  // Per-review checkbox state, keyed by company_id (default: checked).
  const [reviewChecked, setReviewChecked] = useState<Record<string, boolean>>({})
  const [confirming, setConfirming]       = useState(false)
  const [confirmedCount, setConfirmedCount] = useState<number | null>(null)

  const handleFile = (f: File) => {
    setFile(f)
    setResult(null)
    setApiError(null)
    setConfirmedCount(null)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }

  const handleProcess = async () => {
    if (!file) return
    setProcessing(true)
    setApiError(null)
    setResult(null)
    try {
      const res = await previewLeaseComps(file)
      setResult(res)
      // default every review row to checked
      setReviewChecked(Object.fromEntries(res.needs_review.map(r => [r.company_id, true])))
      // exact matches were auto-applied server-side → refresh parent now
      if (res.auto_applied.length > 0) onDone()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      setApiError(err?.response?.data?.detail || err?.message || 'Could not parse the PDF.')
    } finally {
      setProcessing(false)
    }
  }

  const handleConfirm = async () => {
    if (!result) return
    const matches = result.needs_review
      .filter(r => reviewChecked[r.company_id])
      .map(r => ({ company_id: r.company_id, expiration_date: r.proposed_expiry }))
    if (matches.length === 0) return
    setConfirming(true)
    try {
      const res = await confirmLeaseComps(matches)
      setConfirmedCount(res.applied.length)
      // Remove confirmed rows from the review list so it can't be double-applied.
      const appliedIds = new Set(res.applied.map(a => a.company_id))
      setResult({
        ...result,
        needs_review: result.needs_review.filter(r => !appliedIds.has(r.company_id)),
      })
      if (res.applied.length > 0) onDone()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      setApiError(err?.response?.data?.detail || err?.message || 'Could not save confirmed matches.')
    } finally {
      setConfirming(false)
    }
  }

  const selectedReviewCount = result
    ? result.needs_review.filter(r => reviewChecked[r.company_id]).length
    : 0

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-surface-card border border-surface-border rounded-2xl shadow-2xl w-full max-w-xl mx-4 max-h-[90vh] flex flex-col">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-surface-border flex-shrink-0">
          <div className="flex items-center gap-2.5">
            <FileText size={18} className="text-accent-blue" />
            <h2 className="text-sm font-bold text-ink-primary">Import Lease Comps</h2>
          </div>
          <button onClick={onClose} className="text-ink-muted hover:text-ink-primary p-1">
            <X size={17} />
          </button>
        </div>

        <div className="p-6 space-y-5 overflow-y-auto">

          <p className="text-xs text-ink-muted">
            Upload a CoStar <span className="text-ink-secondary font-medium">Lease Activity PDF</span>.
            Tenant lease expirations are matched to existing companies by name. Exact
            matches apply automatically; close matches are listed for your confirmation.
            Companies that already have a lease expiry are never overwritten.
          </p>

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
                accept=".pdf,application/pdf"
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
                  <p className="text-sm text-ink-secondary">Drop a .pdf file here</p>
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
              {/* Summary badges */}
              <div className="grid grid-cols-4 gap-2">
                <Badge value={result.auto_applied.length} label="Applied" tone="emerald" />
                <Badge value={result.needs_review.length} label="Review" tone="blue" />
                <Badge value={result.skipped_no_match.length} label="No match" tone="amber" />
                <Badge value={result.skipped_already_set.length} label="Already set" tone="muted" />
              </div>

              {result.auto_applied.length > 0 && (
                <div className="flex items-center gap-2 text-xs text-emerald-400">
                  <CheckCircle size={13} />
                  {result.auto_applied.length} exact match{result.auto_applied.length === 1 ? '' : 'es'} applied
                  {' '}and signals recomputed.
                </div>
              )}

              {confirmedCount !== null && (
                <div className="flex items-center gap-2 text-xs text-emerald-400">
                  <CheckCircle size={13} />
                  {confirmedCount} confirmed match{confirmedCount === 1 ? '' : 'es'} saved.
                </div>
              )}

              {/* Needs review */}
              {result.needs_review.length > 0 && (
                <div className="space-y-2">
                  <div className="text-[11px] font-semibold text-ink-secondary uppercase tracking-wider">
                    Confirm close matches
                  </div>
                  <div className="max-h-56 overflow-y-auto rounded-lg border border-surface-border divide-y divide-surface-border">
                    {result.needs_review.map(r => (
                      <label
                        key={r.company_id}
                        className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-surface-muted"
                      >
                        <input
                          type="checkbox"
                          checked={reviewChecked[r.company_id] ?? false}
                          onChange={e =>
                            setReviewChecked(s => ({ ...s, [r.company_id]: e.target.checked }))
                          }
                          className="accent-emerald-500 flex-shrink-0"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="text-xs text-ink-secondary truncate">
                            <span className="text-ink-primary font-medium">{r.tenant_name}</span>
                            <span className="text-ink-muted"> → </span>
                            <span className="text-ink-primary font-medium">{r.company_name}</span>
                          </div>
                          <div className="text-[10px] text-ink-muted">
                            Expires {r.proposed_expiry} · {Math.round(r.confidence * 100)}% match
                          </div>
                        </div>
                      </label>
                    ))}
                  </div>
                  <button
                    onClick={handleConfirm}
                    disabled={confirming || selectedReviewCount === 0}
                    className="flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-600 text-white
                               text-xs font-semibold hover:bg-emerald-700 transition-colors
                               disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {confirming
                      ? <><RefreshCw size={13} className="animate-spin" /> Saving…</>
                      : <><CheckCircle size={13} /> Confirm selected ({selectedReviewCount})</>}
                  </button>
                </div>
              )}

              {/* Skipped — no match */}
              {result.skipped_no_match.length > 0 && (
                <SkipList
                  title={`No company match (${result.skipped_no_match.length})`}
                  items={result.skipped_no_match.map(s => `${s.tenant_name} — expires ${s.proposed_expiry}`)}
                  icon="amber"
                />
              )}

              {/* Skipped — already set */}
              {result.skipped_already_set.length > 0 && (
                <SkipList
                  title={`Already had an expiry — not overwritten (${result.skipped_already_set.length})`}
                  items={result.skipped_already_set.map(
                    s => `${s.company_name}${s.existing_expiry ? ` — keeps ${s.existing_expiry}` : ''}`,
                  )}
                  icon="muted"
                />
              )}

              <button
                onClick={() => { setResult(null); setFile(null); setConfirmedCount(null) }}
                className="text-xs text-ink-muted hover:text-ink-primary underline"
              >
                Import another PDF
              </button>
            </div>
          )}
        </div>

        {/* Footer */}
        {!result && (
          <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-surface-border flex-shrink-0">
            <button
              onClick={onClose}
              className="px-4 py-2 text-xs text-ink-muted hover:text-ink-primary transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleProcess}
              disabled={!file || processing}
              className="flex items-center gap-2 px-5 py-2 rounded-lg bg-accent-blue text-white
                         text-xs font-semibold hover:bg-accent-blueDim transition-colors
                         disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {processing
                ? <><Clock size={13} className="animate-pulse" /> Reading PDF…</>
                : <><Upload size={13} /> Process PDF</>}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function Badge({ value, label, tone }: {
  value: number; label: string; tone: 'emerald' | 'blue' | 'amber' | 'muted'
}) {
  const tones: Record<string, string> = {
    emerald: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400',
    blue:    'bg-accent-blue/10 border-accent-blue/20 text-accent-blue',
    amber:   'bg-amber-500/10 border-amber-500/20 text-amber-400',
    muted:   'bg-surface-muted border-surface-border text-ink-muted',
  }
  return (
    <div className={`rounded-lg border p-3 text-center ${tones[tone]}`}>
      <div className="text-xl font-bold mono">{value}</div>
      <div className="text-[10px] text-ink-muted uppercase tracking-wider mt-0.5">{label}</div>
    </div>
  )
}

function SkipList({ title, items, icon }: {
  title: string; items: string[]; icon: 'amber' | 'muted'
}) {
  const color = icon === 'amber' ? 'text-amber-400' : 'text-ink-muted'
  return (
    <div>
      <div className={`flex items-center gap-1.5 text-xs font-medium ${color}`}>
        <AlertTriangle size={12} /> {title}
      </div>
      <div className="mt-2 max-h-32 overflow-y-auto rounded-lg border border-surface-border bg-surface-muted/50 p-2 space-y-1">
        {items.map((t, i) => (
          <div key={i} className="text-[11px] text-ink-secondary truncate" title={t}>{t}</div>
        ))}
      </div>
    </div>
  )
}

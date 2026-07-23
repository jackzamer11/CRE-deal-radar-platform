import { useEffect, useRef, useState } from 'react'
import { ClipboardCheck, Check, Pencil, FileText, X, Upload, Wand2 } from 'lucide-react'
import axios from 'axios'
import {
  getObservations, verifyObservation, uploadDocument, extractDocument,
  getActivityMiningStatus, mineActivityLogs,
} from '../api/client'
import type { Observation, ActivityMiningStatus } from '../types'

// Turn a raw field name (e.g. "base_rent_annual") into a readable label.
function fieldLabel(field: string): string {
  return field
    .split('_')
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

// Confidence as a simple three-band visual, tolerant of null.
function ConfidenceBadge({ confidence }: { confidence: number | null }) {
  if (confidence === null || confidence === undefined) {
    return (
      <span className="text-[10px] px-2 py-0.5 rounded-full border font-semibold
                       bg-surface-muted text-ink-muted border-surface-border">
        no score
      </span>
    )
  }
  const pct = Math.round(confidence * 100)
  const band =
    confidence >= 0.75 ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
    : confidence >= 0.5 ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
    : 'bg-red-500/15 text-red-400 border-red-500/30'
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold ${band}`}>
      {pct}% confident
    </span>
  )
}

function ReviewRow({
  obs,
  onResolved,
}: {
  obs: Observation
  onResolved: (id: number) => void
}) {
  const [editing, setEditing] = useState(false)
  const [input, setInput] = useState(obs.value ?? '')
  const [busy, setBusy] = useState(false)

  const confirm = async () => {
    setBusy(true)
    try {
      await verifyObservation(obs.id)
      onResolved(obs.id)
    } catch {
      setBusy(false)
    }
  }

  const correct = async () => {
    setBusy(true)
    try {
      await verifyObservation(obs.id, input)
      onResolved(obs.id)
    } catch {
      setBusy(false)
    }
  }

  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[10px] font-bold uppercase tracking-wider text-ink-muted">
            {fieldLabel(obs.field)}
          </div>
          {obs.value === null ? (
            <div className="mt-0.5 text-sm font-bold text-red-400">NOT FOUND</div>
          ) : (
            <div className="mt-0.5 text-sm font-bold text-ink-primary break-words">{obs.value}</div>
          )}
        </div>
        <ConfidenceBadge confidence={obs.confidence} />
      </div>

      {/* Source provenance */}
      <div className="mt-2 flex items-center gap-2 text-[11px] text-ink-muted flex-wrap">
        <FileText size={12} className="flex-shrink-0" />
        <span className="text-accent-blue">{obs.source_doc ?? 'unknown source'}</span>
        {obs.source_page !== null && <span>· p.{obs.source_page}</span>}
        <span className="text-ink-muted">· {obs.entity_type} #{obs.entity_id}</span>
      </div>
      {obs.source_snippet && (
        <p className="mt-1.5 text-[11px] italic text-ink-secondary leading-snug border-l-2 border-surface-border pl-2">
          “{obs.source_snippet}”
        </p>
      )}

      {/* Actions */}
      {editing ? (
        <div className="mt-3 space-y-2">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder="Corrected value…"
            className="w-full text-xs bg-surface-muted border border-surface-border rounded-lg px-3 py-2
                       text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue/50"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={correct}
              disabled={busy}
              className="text-[10px] px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700
                         text-white font-semibold disabled:opacity-50"
            >
              {busy ? 'Saving…' : 'Save Correction'}
            </button>
            <button
              onClick={() => { setEditing(false); setInput(obs.value ?? '') }}
              className="text-[10px] text-ink-muted hover:text-ink-primary flex items-center gap-1"
            >
              <X size={11} /> Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex items-center gap-2">
          <button
            onClick={confirm}
            disabled={busy}
            className="flex items-center gap-1.5 text-[10px] px-3 py-1.5 rounded-lg bg-accent-blue
                       hover:bg-accent-blueDim text-white font-semibold disabled:opacity-50"
          >
            <Check size={12} /> Confirm
          </button>
          <button
            onClick={() => setEditing(true)}
            disabled={busy}
            className="flex items-center gap-1.5 text-[10px] px-3 py-1.5 rounded-lg bg-surface-muted
                       hover:bg-surface-hover text-ink-secondary hover:text-ink-primary font-semibold
                       border border-surface-border disabled:opacity-50"
          >
            <Pencil size={11} /> Correct
          </button>
        </div>
      )}
    </div>
  )
}

// ── Activity-log mining panel ────────────────────────────────────────────────
// Turns freeform notes into structured facts. Runs in small batches so a long
// backfill never blocks on a single HTTP request. Never edits the logs.
const MINE_BATCH_SIZE = 20

function ActivityMiningPanel({ onMined }: { onMined: () => void }) {
  const [status, setStatus] = useState<ActivityMiningStatus | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    try {
      setStatus(await getActivityMiningStatus())
    } catch {
      /* panel is non-critical; stay quiet */
    }
  }

  useEffect(() => { refresh() }, [])

  const run = async () => {
    setRunning(true)
    setError(null)
    try {
      // Loop batches until nothing is left (or a batch makes no progress).
      for (;;) {
        const res = await mineActivityLogs(MINE_BATCH_SIZE)
        const next = await getActivityMiningStatus()
        setStatus(next)
        onMined()
        if (next.remaining <= 0 || res.processed === 0) break
      }
    } catch (err) {
      let msg = 'Mining failed.'
      if (axios.isAxiosError(err) && err.response?.data?.detail) msg = String(err.response.data.detail)
      setError(msg)
    } finally {
      setRunning(false)
    }
  }

  if (!status || status.total_logs === 0) return null

  const pct = status.total_logs
    ? Math.round((status.mined / status.total_logs) * 100)
    : 0
  const done = status.remaining === 0

  return (
    <div className="mb-5 bg-surface-card border border-surface-border rounded-xl p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-ink-primary flex items-center gap-2">
            <Wand2 size={14} className="text-accent-blue" />
            Activity log intelligence
          </div>
          <p className="text-[11px] text-ink-muted mt-0.5 leading-relaxed">
            Reads your call and email notes and turns what tenants actually said into
            structured facts. Your activity logs are never changed.
          </p>
        </div>
        {!done && (
          <button
            onClick={run}
            disabled={running}
            className="flex-shrink-0 text-[10px] px-3 py-1.5 rounded-lg bg-accent-blue
                       hover:bg-accent-blueDim text-white font-semibold disabled:opacity-50"
          >
            {running ? 'Mining…' : `Mine ${status.remaining} logs`}
          </button>
        )}
      </div>

      {/* Progress */}
      <div className="mt-3">
        <div className="h-1.5 bg-surface-muted rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${done ? 'bg-emerald-500' : 'bg-accent-blue'}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="flex items-center gap-3 mt-1.5 text-[10px] text-ink-muted flex-wrap">
          <span>{status.mined} / {status.total_logs} notes read ({pct}%)</span>
          <span className="text-emerald-400">{status.facts_extracted} facts extracted</span>
          {status.failed > 0 && <span className="text-red-400">{status.failed} failed</span>}
          {done && <span className="text-emerald-400 font-semibold">✓ all notes processed</span>}
        </div>
      </div>

      {error && <p className="mt-2 text-[11px] text-red-400">{error}</p>}
    </div>
  )
}

// Where a fact came from, derived from its source_doc.
function isFromNote(obs: Observation): boolean {
  return !!obs.source_doc && obs.source_doc.startsWith('activity_log:')
}

type SourceFilter = 'all' | 'notes' | 'docs'
const PAGE_SIZE = 50

export default function ReviewPage() {
  const [rows, setRows] = useState<Observation[]>([])
  const [loading, setLoading] = useState(true)
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all')
  const [visible, setVisible] = useState(PAGE_SIZE)

  const load = async () => {
    setLoading(true)
    try {
      // Unverified only, sorted by confidence ascending (server-side).
      const data = await getObservations({ human_verified: false })
      setRows(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  // Confirmed/corrected items drop out of the queue immediately.
  const handleResolved = (id: number) => {
    setRows(prev => prev.filter(r => r.id !== id))
  }

  // ── Upload + extract a lease PDF ───────────────────────────────────────────
  const fileInput = useRef<HTMLInputElement>(null)
  const [uploadState, setUploadState] = useState<'idle' | 'uploading' | 'extracting'>('idle')
  const [uploadMsg, setUploadMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (fileInput.current) fileInput.current.value = ''  // allow re-selecting the same file
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setUploadMsg({ kind: 'error', text: 'Please choose a PDF file.' })
      return
    }
    setUploadMsg(null)
    try {
      setUploadState('uploading')
      const doc = await uploadDocument(file)
      setUploadState('extracting')
      const result = await extractDocument(doc.id)
      setUploadMsg({
        kind: 'ok',
        text: `Extracted ${result.observations.length} facts from ${file.name}. Review them below.`,
      })
      await load()  // new facts appear in the queue
    } catch (err) {
      // Surface the backend's clear message (e.g. missing API key, no text in PDF).
      let text = 'Upload or extraction failed.'
      if (axios.isAxiosError(err) && err.response?.data?.detail) {
        text = String(err.response.data.detail)
      }
      setUploadMsg({ kind: 'error', text })
    } finally {
      setUploadState('idle')
    }
  }

  const busy = uploadState !== 'idle'

  const noteCount = rows.filter(isFromNote).length
  const filtered = rows.filter(obs =>
    sourceFilter === 'all' ? true
      : sourceFilter === 'notes' ? isFromNote(obs)
      : !isFromNote(obs),
  )

  return (
    <div className="p-6 max-w-3xl">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <ClipboardCheck size={20} className="text-blue-400" />
          <h1 className="text-xl font-bold text-ink-primary">Review</h1>
          <span className="text-ink-muted text-sm">
            {rows.length} fact{rows.length === 1 ? '' : 's'} awaiting review
          </span>
        </div>
        <input
          ref={fileInput}
          type="file"
          accept="application/pdf,.pdf"
          onChange={handleFile}
          className="hidden"
        />
        <button
          onClick={() => fileInput.current?.click()}
          disabled={busy}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-blue text-white text-xs font-semibold
                     hover:bg-accent-blueDim transition-colors disabled:opacity-50"
        >
          <Upload size={13} className={busy ? 'animate-pulse' : ''} />
          {uploadState === 'uploading' ? 'Uploading…'
            : uploadState === 'extracting' ? 'Extracting…'
            : 'Upload Lease PDF'}
        </button>
      </div>

      <ActivityMiningPanel onMined={load} />

      {uploadMsg && (
        <div
          className={`mb-5 rounded-xl p-3 text-xs flex items-start justify-between gap-3 border
            ${uploadMsg.kind === 'ok'
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
              : 'bg-red-500/10 border-red-500/30 text-red-300'}`}
        >
          <span>{uploadMsg.text}</span>
          <button onClick={() => setUploadMsg(null)} className="flex-shrink-0 opacity-70 hover:opacity-100">
            <X size={13} />
          </button>
        </div>
      )}

      {/* Source filter — separates conversation intel from lease abstracts */}
      {!loading && rows.length > 0 && (
        <div className="flex items-center gap-1.5 mb-4 flex-wrap">
          {([
            ['all', 'All', rows.length],
            ['notes', 'From call/email notes', noteCount],
            ['docs', 'From lease documents', rows.length - noteCount],
          ] as [SourceFilter, string, number][]).map(([key, label, count]) => (
            <button
              key={key}
              onClick={() => { setSourceFilter(key); setVisible(PAGE_SIZE) }}
              className={`text-[11px] px-2.5 py-1 rounded-full border font-semibold transition-colors
                ${sourceFilter === key
                  ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/50'
                  : 'bg-surface-card text-ink-muted border-surface-border hover:text-ink-secondary'}`}
            >
              {label} <span className="ml-1 text-ink-muted">{count}</span>
            </button>
          ))}
        </div>
      )}

      {loading ? (
        <div className="text-center py-12 text-ink-muted">Loading…</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12 text-ink-muted">
          <ClipboardCheck size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">Nothing awaiting review.</p>
          <p className="text-xs mt-1 text-ink-muted">
            Upload a lease PDF or mine your activity notes above — extracted facts
            land here for you to confirm before they drive signals.
          </p>
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {filtered.slice(0, visible).map(obs => (
              <ReviewRow key={obs.id} obs={obs} onResolved={handleResolved} />
            ))}
          </div>
          {filtered.length > visible && (
            <button
              onClick={() => setVisible(v => v + PAGE_SIZE)}
              className="mt-4 w-full text-[11px] py-2 rounded-lg bg-surface-muted hover:bg-surface-hover
                         text-ink-secondary hover:text-ink-primary font-semibold border border-surface-border"
            >
              Show more ({filtered.length - visible} remaining)
            </button>
          )}
        </>
      )}
    </div>
  )
}

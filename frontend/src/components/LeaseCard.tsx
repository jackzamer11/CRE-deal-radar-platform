// The lease document card: link, upload, and the extraction review panel.
//
// Lives in its own component because both entry points render it — the
// thread's Deal card and the company record. Two rules shape it:
//   - Everything found is checked already. Jack SCANS and unchecks what is
//     wrong; he does not approve nine values one at a time.
//   - Every value sits next to the clause it came from. The reading is the
//     risk — commencement vs. expiration, rentable vs. usable SF, an option
//     term mistaken for the expiry — and each of those would silently move a
//     past client's re-entry date by years.
//
// Lease content is private: none of it reaches generated outreach copy.
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Check, ChevronDown, ChevronRight, FileText, TriangleAlert, Upload,
} from 'lucide-react'
import {
  confirmLeaseExtraction, getLease, leaseFileUrl, reextractLease, uploadLease,
} from '../api/client'
import type { ExtractedLeaseField, LeaseStatus } from '../types'

export default function LeaseCard({
  companyPk, onConfirmed,
}: {
  companyPk: number
  onConfirmed: () => void
}) {
  const [lease, setLease] = useState<LeaseStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  // Field name -> checked. Seeded from the extraction: found means checked.
  const [accepted, setAccepted] = useState<Record<string, boolean>>({})
  const [reviewing, setReviewing] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const seedAccepted = (status: LeaseStatus) => {
    const next: Record<string, boolean> = {}
    for (const f of status.fields) next[f.field] = f.accepted
    setAccepted(next)
  }

  const load = useCallback(async () => {
    try {
      const status = await getLease(companyPk)
      setLease(status)
      seedAccepted(status)
    } catch {
      // A company with no lease is the normal case, not an error state.
      setLease(null)
    }
  }, [companyPk])

  useEffect(() => { void load() }, [load])

  const pick = () => fileInput.current?.click()

  const upload = async (file: File) => {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const status = await uploadLease(companyPk, file)
      setLease(status)
      seedAccepted(status)
      // The file is stored and linked before extraction is attempted, so an
      // extraction problem is a note here, never a failed upload.
      if (status.extraction_error) setNote(status.extraction_error)
      if (status.has_extraction) setReviewing(true)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not upload this lease.')
    } finally {
      setBusy(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const reextract = async () => {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const status = await reextractLease(companyPk)
      setLease(status)
      seedAccepted(status)
      if (status.extraction_error) setNote(status.extraction_error)
      if (status.has_extraction) setReviewing(true)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not read this lease.')
    } finally {
      setBusy(false)
    }
  }

  const confirm = async () => {
    if (!lease) return
    setBusy(true)
    setError(null)
    try {
      const checked = lease.fields.filter(f => accepted[f.field]).map(f => f.field)
      const result = await confirmLeaseExtraction(companyPk, checked)
      const wrote = Object.keys(result.written).length
      setNote(
        wrote === 0
          ? 'Nothing was written — every value that writes to the company record was unchecked.'
          : 'Wrote ' + wrote + (wrote === 1 ? ' field' : ' fields') + ' to the company record.',
      )
      setReviewing(false)
      await load()
      // The company's expiry drives everything downstream, so the thread
      // header has to re-read it.
      onConfirmed()
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not save these values.')
    } finally {
      setBusy(false)
    }
  }

  const writeBacks = (lease?.fields ?? []).filter(f => f.writes_to_company)
  const others = (lease?.fields ?? []).filter(f => !f.writes_to_company)

  return (
    <div className="mt-3 pt-3 border-t border-surface-border">
      <input
        ref={fileInput}
        type="file"
        accept="application/pdf,.pdf"
        className="hidden"
        onChange={e => {
          const f = e.target.files?.[0]
          if (f) void upload(f)
        }}
      />

      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] font-bold uppercase tracking-widest text-ink-muted">
          Lease
        </span>

        {lease?.lease_file_name ? (
          <>
            {/* Goes through the API, which resolves the stored filename against
                the configured folder — so a moved folder needs no data change,
                and a browser will actually open it (a file:// link will not). */}
            <a
              href={leaseFileUrl(companyPk)}
              target="_blank"
              rel="noreferrer"
              className="text-[11px] text-accent-blue hover:underline flex items-center gap-1 truncate"
            >
              <FileText size={11} /> {lease.lease_file_name}
            </a>
            {lease.file_missing && (
              <span
                className="text-[9px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-300
                           border border-red-500/30 flex items-center gap-1"
                title="Linked, but not in the leases folder — it may have been moved or renamed."
              >
                <TriangleAlert size={9} /> file missing
              </span>
            )}
            {lease.has_extraction && !reviewing && (
              <button
                onClick={() => setReviewing(true)}
                className="text-[10px] text-ink-muted hover:text-accent-blue"
              >
                review extraction
              </button>
            )}
            {!lease.has_extraction && (
              <button
                onClick={reextract}
                disabled={busy}
                className="text-[10px] text-ink-muted hover:text-accent-blue disabled:opacity-50"
              >
                {busy ? 'reading…' : 'read the lease'}
              </button>
            )}
            <button
              onClick={pick}
              disabled={busy}
              className="text-[10px] text-ink-muted hover:text-accent-blue disabled:opacity-50"
            >
              replace
            </button>
          </>
        ) : (
          <button
            onClick={pick}
            disabled={busy}
            className="text-[11px] px-2 py-1 rounded-lg border border-surface-border
                       bg-surface-muted text-ink-secondary hover:border-accent-blue/50
                       disabled:opacity-50 flex items-center gap-1"
          >
            <Upload size={11} /> {busy ? 'Uploading…' : 'Link a lease PDF'}
          </button>
        )}
      </div>

      {error && <p className="text-[11px] text-red-400 mt-1.5">{error}</p>}
      {note && <p className="text-[11px] text-ink-secondary mt-1.5">{note}</p>}

      {reviewing && lease?.has_extraction && (
        <div className="mt-2 bg-surface-muted/40 border border-surface-border rounded-lg p-3">
          <p className="text-[11px] text-ink-secondary mb-2">
            Everything found is checked. Uncheck anything the clause does not
            support — only the three marked{' '}
            <span className="text-teal-300">writes to record</span> change the
            company.
          </p>

          <div className="space-y-2">
            {[...writeBacks, ...others].map(f => (
              <LeaseFieldRow
                key={f.field}
                field={f}
                checked={!!accepted[f.field]}
                onToggle={() =>
                  setAccepted(prev => ({ ...prev, [f.field]: !prev[f.field] }))
                }
              />
            ))}
          </div>

          <div className="flex items-center gap-2 mt-3">
            <button
              onClick={confirm}
              disabled={busy}
              className="text-[10px] px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-700
                         text-white font-semibold flex items-center gap-1 disabled:opacity-50"
            >
              <Check size={10} /> {busy ? 'Saving…' : 'Confirm'}
            </button>
            <button
              onClick={() => setReviewing(false)}
              className="text-[10px] px-2.5 py-1 rounded bg-surface-muted hover:bg-surface-border
                         text-ink-secondary font-semibold"
            >
              Not now
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// One extracted value beside the clause it came from.
function LeaseFieldRow({
  field, checked, onToggle,
}: {
  field: ExtractedLeaseField
  checked: boolean
  onToggle: () => void
}) {
  const [open, setOpen] = useState(false)

  // Not found is neither a failure nor a blank to fill in by guesswork: the
  // document did not state it, or stated it without a quotable clause. It has
  // no checkbox, because there is nothing to accept.
  if (!field.found) {
    return (
      <div className="flex items-start gap-2 text-[11px]">
        <span className="w-3 flex-shrink-0" />
        <span className="text-ink-muted w-44 flex-shrink-0">{field.label}</span>
        <span className="text-ink-muted/70 italic">not found in the document</span>
      </div>
    )
  }

  return (
    <div className="text-[11px]">
      <div className="flex items-start gap-2">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="mt-0.5 flex-shrink-0 accent-emerald-500"
        />
        <span className="text-ink-muted w-44 flex-shrink-0">{field.label}</span>
        <span className="text-ink-primary font-semibold flex-1">{field.value}</span>
        {field.writes_to_company && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-teal-500/10 text-teal-300
                           border border-teal-500/30 flex-shrink-0">
            writes to record
          </span>
        )}
      </div>
      {field.source_text && (
        <button
          onClick={() => setOpen(v => !v)}
          className="ml-[13.5rem] mt-0.5 text-[10px] text-ink-muted hover:text-accent-blue
                     flex items-center gap-1"
        >
          {open ? <ChevronDown size={9} /> : <ChevronRight size={9} />}
          {open ? 'hide the clause' : field.page ? 'source · page ' + field.page : 'source'}
        </button>
      )}
      {open && field.source_text && (
        <blockquote className="ml-[13.5rem] mt-1 pl-2 border-l-2 border-surface-border
                               text-[10px] text-ink-secondary italic">
          {field.source_text}
        </blockquote>
      )}
    </div>
  )
}

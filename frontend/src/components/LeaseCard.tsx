// The lease card: every lease a company has signed, and the review panel.
//
// Lives in its own component because both entry points render it — the
// thread's Deal card and the company record. The rules that shape it:
//   - Leases are a list. A new upload is a new lease; the previous one becomes
//     a prior term with its own file and extraction — never overwritten.
//   - Everything found is checked already. Jack SCANS and unchecks what is
//     wrong; he does not approve nine values one at a time.
//   - Every row is editable, including "not found in the document". A typed
//     value is marked manual, and what the page said stays visible beside it:
//     Jack must always be able to tell what came off the page from what he
//     supplied.
//   - Every value sits next to the clause it came from. The reading is the
//     risk — commencement vs. expiration, rentable vs. usable SF, an option
//     term mistaken for the expiry.
//
// Lease content is private: none of it reaches generated outreach copy.
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Check, ChevronDown, ChevronRight, FileText, History, Trash2, TriangleAlert, Upload,
} from 'lucide-react'
import {
  confirmLeaseExtraction, getLease, leaseFileUrl, leaseFileUrlById, reextractLease,
  removeLease, uploadLease,
} from '../api/client'
import type { ExtractedLeaseField, LeaseStatus, LeaseTerm } from '../types'

function fmt(d: string | null): string {
  if (!d) return '—'
  const [y, m, day] = d.slice(0, 10).split('-').map(Number)
  return new Date(y, m - 1, day).toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
  })
}

// The current lease as a term, so the panel and removal treat every lease alike.
function currentTerm(status: LeaseStatus | null): LeaseTerm | null {
  if (!status || status.lease_id === null) return null
  return {
    lease_id: status.lease_id,
    lease_file_name: status.lease_file_name,
    lease_uploaded_at: status.lease_uploaded_at,
    is_current: true,
    confirmed_at: status.confirmed_at,
    commencement_date: status.commencement_date,
    expiration_date: status.expiration_date,
    file_missing: status.file_missing,
    has_extraction: status.has_extraction,
    fields: status.fields,
  }
}

export default function LeaseCard({
  companyPk, companyBusinessId, onConfirmed,
}: {
  companyPk: number
  // The CO-nnn id, which is what the /companies/ routes key on. Removal lives
  // there (DELETE /companies/{id}/lease), beside the PATCH that sets the
  // expiry by hand; every other call here uses the integer pk.
  companyBusinessId: string | null
  onConfirmed: () => void
}) {
  const [status, setStatus] = useState<LeaseStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  // The lease whose review panel is open, if any.
  const [reviewingId, setReviewingId] = useState<number | null>(null)
  // Removal is destructive — it deletes the PDF — so it goes behind a
  // confirmation that names the file.
  const [removingId, setRemovingId] = useState<number | null>(null)
  const [showPriors, setShowPriors] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const current = currentTerm(status)
  const priors = status?.prior_leases ?? []
  const allTerms = [...(current ? [current] : []), ...priors]
  const reviewing = allTerms.find(t => t.lease_id === reviewingId) ?? null
  const removing = allTerms.find(t => t.lease_id === removingId) ?? null

  const load = useCallback(async () => {
    try {
      setStatus(await getLease(companyPk))
    } catch {
      // A company with no lease is the normal case, not an error state.
      setStatus(null)
    }
  }, [companyPk])

  useEffect(() => { void load() }, [load])

  const pick = () => fileInput.current?.click()

  const afterRead = (next: LeaseStatus, leaseId: number | null) => {
    setStatus(next)
    // The file is stored and linked before extraction is attempted, so an
    // extraction problem is a note here, never a failed upload.
    if (next.extraction_error) setNote(next.extraction_error)
    const term = [currentTerm(next), ...next.prior_leases].find(t => t?.lease_id === leaseId)
    if (term?.has_extraction) setReviewingId(term.lease_id)
  }

  const upload = async (file: File) => {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const next = await uploadLease(companyPk, file)
      afterRead(next, next.lease_id)
      if (next.prior_leases.length > 0) {
        setNote(prev => prev ?? 'Linked as the current lease. The previous lease is kept as a prior term.')
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not upload this lease.')
    } finally {
      setBusy(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const reextract = async (term: LeaseTerm) => {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      afterRead(await reextractLease(companyPk, term.lease_id), term.lease_id)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not read this lease.')
    } finally {
      setBusy(false)
    }
  }

  const confirm = async (
    term: LeaseTerm, accepted: string[], manualValues: Record<string, string>,
  ) => {
    setBusy(true)
    setError(null)
    try {
      const result = await confirmLeaseExtraction(companyPk, accepted, manualValues, term.lease_id)
      const typed = Object.values(result.sources).filter(s => s === 'manual').length
      const typedNote = typed > 0 ? ` ${typed} marked manual (typed by you).` : ''
      if (!result.is_current) {
        setNote(
          'Saved to this lease as a prior term. The company record is unchanged — '
          + 'it follows the lease with the latest commencement date.' + typedNote,
        )
      } else {
        const wrote = Object.keys(result.written).length
        const submarket = result.submarket_created && result.current_submarket
          ? ` Added ${result.current_submarket} to the submarket list.`
          : ''
        setNote(
          (wrote === 0
            ? 'Nothing was written to the company — every value that writes to the record was unchecked.'
            : 'Wrote ' + wrote + (wrote === 1 ? ' field' : ' fields') + ' to the company record.')
          + typedNote + submarket,
        )
      }
      setReviewingId(null)
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

  const remove = async (term: LeaseTerm) => {
    if (!companyBusinessId) return
    setBusy(true)
    setError(null)
    try {
      const result = await removeLease(companyBusinessId, term.lease_id)
      setRemovingId(null)
      setReviewingId(null)
      // "absent" means the file was already gone — a clean outcome, worth
      // saying plainly rather than dressing up as a deletion.
      const what = result.warning
        ? result.warning
        : result.file_outcome === 'absent'
          ? `Lease removed. ${result.removed_file_name} was not in the leases folder.`
          : `${result.removed_file_name} removed and deleted from the leases folder.`
      const promoted = result.promoted_file_name
        ? ` ${result.promoted_file_name} is now the current lease.`
        : ''
      setNote(what + promoted)
      await load()
      if (result.promoted_lease_id !== null) onConfirmed()
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not remove this lease.')
    } finally {
      setBusy(false)
    }
  }

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

        {current ? (
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
              <FileText size={11} /> {current.lease_file_name}
            </a>
            {current.expiration_date && (
              <span className="text-[10px] text-ink-muted">
                {fmt(current.commencement_date)} – {fmt(current.expiration_date)}
              </span>
            )}
            <TermActions
              term={current}
              busy={busy}
              reviewing={reviewingId === current.lease_id}
              canRemove={!!companyBusinessId}
              onReview={() => setReviewingId(current.lease_id)}
              onReextract={() => reextract(current)}
              onRemove={() => setRemovingId(current.lease_id)}
            />
            <button
              onClick={pick}
              disabled={busy}
              className="text-[10px] text-ink-muted hover:text-accent-blue disabled:opacity-50"
              title="Link another lease. This one is kept as a prior term."
            >
              {busy ? 'uploading…' : 'add a newer lease'}
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

      {/* Prior terms: the base rent, escalations and option language of every
          earlier lease — what Jack walks into a renewal conversation with. */}
      {priors.length > 0 && (
        <div className="mt-1.5">
          <button
            onClick={() => setShowPriors(v => !v)}
            className="text-[10px] text-ink-muted hover:text-accent-blue flex items-center gap-1"
          >
            {showPriors ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
            <History size={10} /> Prior terms ({priors.length})
          </button>
          {showPriors && (
            <div className="mt-1 ml-3 space-y-1">
              {priors.map(term => (
                <div key={term.lease_id} className="flex items-center gap-2 flex-wrap">
                  <a
                    href={leaseFileUrlById(term.lease_id)}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[11px] text-accent-blue hover:underline flex items-center gap-1 truncate"
                  >
                    <FileText size={11} /> {term.lease_file_name}
                  </a>
                  <span className="text-[10px] text-ink-muted">
                    {term.expiration_date
                      ? `${fmt(term.commencement_date)} – ${fmt(term.expiration_date)}`
                      : `uploaded ${fmt(term.lease_uploaded_at)}`}
                  </span>
                  <TermActions
                    term={term}
                    busy={busy}
                    reviewing={reviewingId === term.lease_id}
                    canRemove={!!companyBusinessId}
                    onReview={() => setReviewingId(term.lease_id)}
                    onReextract={() => reextract(term)}
                    onRemove={() => setRemovingId(term.lease_id)}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {error && <p className="text-[11px] text-red-400 mt-1.5">{error}</p>}
      {note && <p className="text-[11px] text-ink-secondary mt-1.5">{note}</p>}

      {/* Names the file, and says what removal does and does NOT do. */}
      {removing && (
        <div className="mt-2 bg-red-500/5 border border-red-500/30 rounded-lg p-3">
          <p className="text-xs text-ink-primary">
            Remove <span className="font-bold">{removing.lease_file_name}</span>?
          </p>
          <p className="text-[11px] text-ink-secondary mt-1">
            This deletes this lease and its file from the leases folder. Other
            leases are untouched.{' '}
            {removing.is_current && priors.length > 0
              ? 'The most recent prior term becomes the current lease, and its confirmed values move onto the company record.'
              : removing.is_current
                ? 'Values you already confirmed from it — lease expiry, premises address, rentable SF — stay on the company record with their markers.'
                : 'The current lease and the company record are unchanged.'}
          </p>
          <div className="flex items-center gap-2 mt-2.5">
            <button
              onClick={() => remove(removing)}
              disabled={busy}
              className="text-[10px] px-2.5 py-1 rounded bg-red-600 hover:bg-red-700
                         text-white font-semibold flex items-center gap-1 disabled:opacity-50"
            >
              <Trash2 size={10} /> {busy ? 'Removing…' : 'Remove lease'}
            </button>
            <button
              onClick={() => setRemovingId(null)}
              className="text-[10px] px-2.5 py-1 rounded bg-surface-muted hover:bg-surface-border
                         text-ink-secondary font-semibold"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {reviewing?.has_extraction && (
        <ReviewPanel
          key={reviewing.lease_id}
          term={reviewing}
          busy={busy}
          onConfirm={(accepted, manual) => confirm(reviewing, accepted, manual)}
          onClose={() => setReviewingId(null)}
        />
      )}
    </div>
  )
}

function TermActions({
  term, busy, reviewing, canRemove, onReview, onReextract, onRemove,
}: {
  term: LeaseTerm
  busy: boolean
  reviewing: boolean
  canRemove: boolean
  onReview: () => void
  onReextract: () => void
  onRemove: () => void
}) {
  return (
    <>
      {term.file_missing && (
        <span
          className="text-[9px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-300
                     border border-red-500/30 flex items-center gap-1"
          title="Linked, but not in the leases folder — it may have been moved or renamed."
        >
          <TriangleAlert size={9} /> file missing
        </span>
      )}
      {term.has_extraction && !reviewing && (
        <button onClick={onReview} className="text-[10px] text-ink-muted hover:text-accent-blue">
          {term.confirmed_at ? 'review extraction' : 'review & confirm'}
        </button>
      )}
      {!term.has_extraction && (
        <button
          onClick={onReextract}
          disabled={busy}
          className="text-[10px] text-ink-muted hover:text-accent-blue disabled:opacity-50"
        >
          {busy ? 'reading…' : 'read the lease'}
        </button>
      )}
      {canRemove && (
        <button
          onClick={onRemove}
          disabled={busy}
          className="text-[10px] text-ink-muted hover:text-red-400 disabled:opacity-50
                     flex items-center gap-1"
        >
          <Trash2 size={10} /> remove
        </button>
      )}
    </>
  )
}

// The panel for one lease. Checked = write it; the input holds the value that
// will be written. An input left matching the page writes "lease_document";
// anything Jack typed writes "manual".
function ReviewPanel({
  term, busy, onConfirm, onClose,
}: {
  term: LeaseTerm
  busy: boolean
  onConfirm: (accepted: string[], manualValues: Record<string, string>) => void
  onClose: () => void
}) {
  const [accepted, setAccepted] = useState<Record<string, boolean>>(
    () => Object.fromEntries(term.fields.map(f => [f.field, f.accepted])),
  )
  const [values, setValues] = useState<Record<string, string>>(
    () => Object.fromEntries(term.fields.map(f => [f.field, f.manual_value ?? f.value ?? ''])),
  )

  const isManual = (f: ExtractedLeaseField) => {
    const typed = (values[f.field] ?? '').trim()
    return typed !== '' && typed !== (f.value ?? '').trim()
  }

  const edit = (f: ExtractedLeaseField, next: string) => {
    const before = (values[f.field] ?? '').trim()
    setValues(prev => ({ ...prev, [f.field]: next }))
    // Typing into an empty row means "use this"; emptying a row means "don't".
    if (before === '' && next.trim() !== '') setAccepted(prev => ({ ...prev, [f.field]: true }))
    if (next.trim() === '') setAccepted(prev => ({ ...prev, [f.field]: false }))
  }

  const submit = () => {
    const checked = term.fields.filter(f => accepted[f.field]).map(f => f.field)
    const manual: Record<string, string> = {}
    for (const f of term.fields) {
      if (isManual(f)) manual[f.field] = values[f.field].trim()
    }
    onConfirm(checked, manual)
  }

  const writeBacks = term.fields.filter(f => f.writes_to_company)
  const others = term.fields.filter(f => !f.writes_to_company)

  return (
    <div className="mt-2 bg-surface-muted/40 border border-surface-border rounded-lg p-3">
      <p className="text-[11px] text-ink-secondary mb-2">
        {term.is_current ? '' : <span className="font-semibold">Prior term · </span>}
        Everything found is checked. Uncheck anything the clause does not
        support, and type over anything it got wrong or did not state — a typed
        value is marked <span className="text-amber-300">manual</span>.
        {term.is_current
          ? <> Only the three marked <span className="text-teal-300">writes to record</span> change the company.</>
          : <> A prior term saves to its own record; the company follows the current lease.</>}
      </p>

      <div className="space-y-2">
        {[...writeBacks, ...others].map(f => (
          <LeaseFieldRow
            key={f.field}
            field={f}
            value={values[f.field] ?? ''}
            manual={isManual(f)}
            checked={!!accepted[f.field]}
            showWritesToRecord={term.is_current}
            onEdit={next => edit(f, next)}
            onToggle={() => setAccepted(prev => ({ ...prev, [f.field]: !prev[f.field] }))}
          />
        ))}
      </div>

      <div className="flex items-center gap-2 mt-3">
        <button
          onClick={submit}
          disabled={busy}
          className="text-[10px] px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-700
                     text-white font-semibold flex items-center gap-1 disabled:opacity-50"
        >
          <Check size={10} /> {busy ? 'Saving…' : 'Confirm'}
        </button>
        <button
          onClick={onClose}
          className="text-[10px] px-2.5 py-1 rounded bg-surface-muted hover:bg-surface-border
                     text-ink-secondary font-semibold"
        >
          Not now
        </button>
      </div>
    </div>
  )
}

// One value: editable, beside the clause it came from and — when Jack typed
// over it — what the document actually said.
function LeaseFieldRow({
  field, value, manual, checked, showWritesToRecord, onEdit, onToggle,
}: {
  field: ExtractedLeaseField
  value: string
  manual: boolean
  checked: boolean
  showWritesToRecord: boolean
  onEdit: (next: string) => void
  onToggle: () => void
}) {
  const [open, setOpen] = useState(false)
  const empty = value.trim() === ''

  return (
    <div className="text-[11px]">
      <div className="flex items-start gap-2">
        <input
          type="checkbox"
          checked={checked}
          disabled={empty}
          onChange={onToggle}
          className="mt-1 flex-shrink-0 accent-emerald-500 disabled:opacity-40"
        />
        <span className="text-ink-muted w-44 flex-shrink-0 pt-0.5">{field.label}</span>
        <input
          value={value}
          onChange={e => onEdit(e.target.value)}
          placeholder={field.found ? '' : 'not found in the document — type it'}
          className={`flex-1 min-w-0 bg-surface-card border rounded px-1.5 py-0.5 text-[11px]
                      focus:outline-none focus:border-accent-blue/50
                      ${manual ? 'border-amber-500/40 text-amber-200' : 'border-surface-border text-ink-primary font-semibold'}
                      placeholder:italic placeholder:font-normal placeholder:text-ink-muted/70`}
        />
        {manual ? (
          <span
            className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300
                       border border-amber-500/30 flex-shrink-0 mt-0.5"
            title="Typed by you, not read off the document."
          >
            manual
          </span>
        ) : field.found && !empty ? (
          <span
            className="text-[9px] px-1.5 py-0.5 rounded bg-teal-500/10 text-teal-300
                       border border-teal-500/30 flex-shrink-0 mt-0.5"
            title="Read off the document."
          >
            lease
          </span>
        ) : null}
        {field.writes_to_company && showWritesToRecord && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-teal-500/10 text-teal-300
                           border border-teal-500/30 flex-shrink-0 mt-0.5">
            writes to record
          </span>
        )}
      </div>
      {manual && field.found && (
        <p className="ml-[13.5rem] mt-0.5 text-[10px] text-ink-muted">
          document said: <span className="text-ink-secondary">{field.value}</span>
        </p>
      )}
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

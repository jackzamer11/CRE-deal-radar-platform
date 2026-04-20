import { useState } from 'react'
import { X, Users, ChevronRight } from 'lucide-react'
import { createCompany } from '../api/client'
import type { CompanyOut } from '../types'

const SUBMARKETS = [
  'Arlington (Clarendon)',
  'Arlington (Rosslyn)',
  'Arlington (Ballston)',
  'Arlington (Columbia Pike)',
  'Alexandria (Old Town)',
  'Tysons',
  'Reston',
  'Falls Church',
  'McLean',
  'Vienna',
  'Fairfax City',
]

interface Props {
  onClose: () => void
  onSaved: (company: CompanyOut) => void
}

type Step = 'company' | 'size' | 'location' | 'contact'

const STEPS: { key: Step; label: string }[] = [
  { key: 'company',  label: 'Company Info' },
  { key: 'size',     label: 'Size & Growth' },
  { key: 'location', label: 'Location & Lease' },
  { key: 'contact',  label: 'Contact' },
]

const defaultForm = {
  name:                  '',
  industry:              '',
  description:           '',
  current_headcount:     '',
  headcount_12mo_ago:    '',
  open_positions:        '',
  current_address:       '',
  current_submarket:     '',
  current_sf:            '',
  lease_expiry_months:   '',
  primary_contact_name:  '',
  primary_contact_title: '',
  primary_contact_phone: '',
  linkedin_url:          '',
  website:               '',
}

type FormState = typeof defaultForm

function Field({
  label, required, hint, children,
}: {
  label: string
  required?: boolean
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div>
      <label className="block text-xs text-ink-muted mb-1.5">
        {label}
        {required && <span className="text-red-400 ml-0.5">*</span>}
        {hint && <span className="text-ink-muted font-normal ml-1">({hint})</span>}
      </label>
      {children}
    </div>
  )
}

const inputCls = `w-full bg-surface border border-surface-border text-ink-primary text-sm
  rounded-lg px-3 py-2 outline-none focus:border-accent-blue transition-colors placeholder:text-ink-muted`

const selectCls = `w-full bg-surface border border-surface-border text-ink-primary text-sm
  rounded-lg px-3 py-2 outline-none focus:border-accent-blue transition-colors`

export default function AddCompanyModal({ onClose, onSaved }: Props) {
  const [step, setStep] = useState<Step>('company')
  const [form, setForm] = useState<FormState>(defaultForm)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (field: keyof FormState) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>
  ) => setForm(f => ({ ...f, [field]: e.target.value }))

  const currentStepIndex = STEPS.findIndex(s => s.key === step)
  const isLast = currentStepIndex === STEPS.length - 1

  const canAdvance = () => {
    if (step === 'company') return form.name && form.industry
    if (step === 'size')    return form.current_headcount
    return true
  }

  // Live derived previews
  const headcount    = parseInt(form.current_headcount) || 0
  const prev         = parseInt(form.headcount_12mo_ago) || 0
  const growthPct    = prev > 0 ? ((headcount - prev) / prev * 100).toFixed(0) : null
  const sfPerHead    = form.current_sf && headcount > 0
    ? (parseInt(form.current_sf) / headcount).toFixed(0) : null
  const sfNeeded     = headcount > 0 && growthPct
    ? Math.round(headcount * (1 + parseFloat(growthPct) / 100 * 1.25) * 175)
    : headcount > 0 ? headcount * 175 : 0

  const handleSubmit = async () => {
    setSaving(true)
    setError(null)
    try {
      const payload = {
        name:                  form.name,
        industry:              form.industry,
        description:           form.description || undefined,
        current_headcount:     parseInt(form.current_headcount),
        headcount_12mo_ago:    form.headcount_12mo_ago ? parseInt(form.headcount_12mo_ago) : undefined,
        open_positions:        form.open_positions ? parseInt(form.open_positions) : 0,
        current_address:       form.current_address || undefined,
        current_submarket:     form.current_submarket || undefined,
        current_sf:            form.current_sf ? parseInt(form.current_sf) : undefined,
        lease_expiry_months:   form.lease_expiry_months ? parseInt(form.lease_expiry_months) : undefined,
        primary_contact_name:  form.primary_contact_name || undefined,
        primary_contact_title: form.primary_contact_title || undefined,
        primary_contact_phone: form.primary_contact_phone || undefined,
        linkedin_url:          form.linkedin_url || undefined,
        website:               form.website || undefined,
      }
      const result = await createCompany(payload)
      onSaved(result)
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to save company')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-surface-card border border-surface-border rounded-2xl w-full max-w-2xl max-h-[90vh] flex flex-col shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-surface-border">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-emerald-500/15 flex items-center justify-center">
              <Users size={15} className="text-emerald-400" />
            </div>
            <div>
              <div className="font-semibold text-ink-primary">Add Company</div>
              <div className="text-[11px] text-ink-muted">Tenant signals scored automatically after save</div>
            </div>
          </div>
          <button onClick={onClose} className="text-ink-muted hover:text-ink-primary p-1 rounded-lg hover:bg-surface-muted">
            <X size={18} />
          </button>
        </div>

        {/* Step indicator */}
        <div className="flex items-center gap-0 px-6 py-3 border-b border-surface-border">
          {STEPS.map((s, i) => (
            <div key={s.key} className="flex items-center">
              <button
                onClick={() => i < currentStepIndex && setStep(s.key)}
                className={`flex items-center gap-2 text-xs font-medium transition-colors ${
                  s.key === step ? 'text-emerald-400' :
                  i < currentStepIndex ? 'text-ink-secondary cursor-pointer hover:text-ink-primary' :
                  'text-ink-muted cursor-default'
                }`}
              >
                <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold
                  ${s.key === step ? 'bg-emerald-500 text-white' :
                    i < currentStepIndex ? 'bg-emerald-500/20 text-emerald-400' :
                    'bg-surface-border text-ink-muted'}`}>
                  {i < currentStepIndex ? '✓' : i + 1}
                </div>
                {s.label}
              </button>
              {i < STEPS.length - 1 && (
                <ChevronRight size={12} className="text-surface-border mx-2" />
              )}
            </div>
          ))}
        </div>

        {/* Form body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">

          {/* Step 1: Company Info */}
          {step === 'company' && (
            <div className="space-y-4">
              <Field label="Company Name" required>
                <input className={inputCls} placeholder="e.g. Apex Federal Solutions"
                  value={form.name} onChange={set('name')} />
              </Field>
              <Field label="Industry" required hint="be specific — used in call scripts">
                <input className={inputCls} placeholder="e.g. Government IT / Data Analytics"
                  value={form.industry} onChange={set('industry')} />
              </Field>
              <Field label="Brief Description">
                <textarea className={`${inputCls} resize-none`} rows={2}
                  placeholder="What do they do? Any relevant intel?"
                  value={form.description} onChange={set('description')} />
              </Field>
              <Field label="Website">
                <input className={inputCls} placeholder="https://example.com"
                  value={form.website} onChange={set('website')} />
              </Field>
            </div>
          )}

          {/* Step 2: Size & Growth */}
          {step === 'size' && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <Field label="Current Headcount" required>
                  <input className={inputCls} type="number" placeholder="e.g. 45"
                    value={form.current_headcount} onChange={set('current_headcount')} />
                </Field>
                <Field label="Headcount 12 Months Ago" hint="drives growth signal">
                  <input className={inputCls} type="number" placeholder="e.g. 32"
                    value={form.headcount_12mo_ago} onChange={set('headcount_12mo_ago')} />
                </Field>
              </div>
              <Field label="Open Positions Right Now" hint="drives hiring velocity signal">
                <input className={inputCls} type="number" placeholder="e.g. 12"
                  value={form.open_positions} onChange={set('open_positions')} />
              </Field>

              {/* Live signal preview */}
              {headcount > 0 && (
                <div className="p-3 rounded-lg bg-surface-muted border border-surface-border space-y-2">
                  <div className="text-[10px] font-bold uppercase tracking-widest text-ink-muted mb-2">
                    Signal Preview
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-ink-muted">YoY Headcount Growth</span>
                    <span className={growthPct ? (parseInt(growthPct) >= 25 ? 'text-red-400 font-bold' : 'text-amber-400') : 'text-ink-muted'}>
                      {growthPct ? `+${growthPct}%` : '— (enter prior headcount)'}
                    </span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-ink-muted">Estimated SF Needed (18mo)</span>
                    <span className="text-ink-primary mono">{sfNeeded.toLocaleString()} SF</span>
                  </div>
                  {sfPerHead && (
                    <div className="flex justify-between text-xs">
                      <span className="text-ink-muted">SF per Head (current)</span>
                      <span className={parseInt(sfPerHead) <= 110 ? 'text-red-400 font-bold' : parseInt(sfPerHead) <= 140 ? 'text-amber-400' : 'text-ink-secondary'}>
                        {sfPerHead} SF/head
                        {parseInt(sfPerHead) <= 110 && ' ← cramped'}
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Step 3: Location & Lease */}
          {step === 'location' && (
            <div className="space-y-4">
              <Field label="Current Address">
                <input className={inputCls} placeholder="e.g. 4075 Wilson Blvd, Arlington, VA 22203"
                  value={form.current_address} onChange={set('current_address')} />
              </Field>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Current Submarket" hint="drives geo matching">
                  <select className={selectCls} value={form.current_submarket} onChange={set('current_submarket')}>
                    <option value="">Select submarket...</option>
                    {SUBMARKETS.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </Field>
                <Field label="Current SF Leased">
                  <input className={inputCls} type="number" placeholder="e.g. 5500"
                    value={form.current_sf} onChange={set('current_sf')} />
                </Field>
              </div>
              <Field label="Months Until Lease Expiry" hint="most important signal — be precise">
                <input className={inputCls} type="number" placeholder="e.g. 14"
                  value={form.lease_expiry_months} onChange={set('lease_expiry_months')} />
                {form.lease_expiry_months && (
                  <div className={`text-[11px] mt-1.5 font-medium ${
                    parseInt(form.lease_expiry_months) <= 6 ? 'text-red-400' :
                    parseInt(form.lease_expiry_months) <= 12 ? 'text-amber-400' :
                    parseInt(form.lease_expiry_months) <= 18 ? 'text-blue-400' : 'text-ink-muted'
                  }`}>
                    {parseInt(form.lease_expiry_months) <= 6 ? '● IMMEDIATE urgency — call this week' :
                     parseInt(form.lease_expiry_months) <= 12 ? '◆ HIGH urgency — call this month' :
                     parseInt(form.lease_expiry_months) <= 18 ? '○ Decision window open' :
                     'Monitor — not yet urgent'}
                  </div>
                )}
              </Field>
              <Field label="LinkedIn Company URL">
                <input className={inputCls} placeholder="https://linkedin.com/company/..."
                  value={form.linkedin_url} onChange={set('linkedin_url')} />
              </Field>
            </div>
          )}

          {/* Step 4: Contact */}
          {step === 'contact' && (
            <div className="space-y-4">
              <div className="p-3 rounded-lg bg-surface-muted border border-surface-border text-[11px] text-ink-muted leading-relaxed">
                The decision-maker contact is embedded in the call script. For companies under 50 employees,
                target the CEO or COO. For larger firms, target VP Real Estate, Facilities, or CFO.
              </div>
              <Field label="Contact Name">
                <input className={inputCls} placeholder="e.g. Sarah Thompson"
                  value={form.primary_contact_name} onChange={set('primary_contact_name')} />
              </Field>
              <Field label="Contact Title">
                <input className={inputCls} placeholder="e.g. CFO"
                  value={form.primary_contact_title} onChange={set('primary_contact_title')} />
              </Field>
              <Field label="Contact Phone">
                <input className={inputCls} placeholder="703-555-0000"
                  value={form.primary_contact_phone} onChange={set('primary_contact_phone')} />
              </Field>
            </div>
          )}

          {error && (
            <div className="mt-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-xs">
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-surface-border bg-surface-muted rounded-b-2xl">
          <button
            onClick={() => currentStepIndex > 0 && setStep(STEPS[currentStepIndex - 1].key)}
            disabled={currentStepIndex === 0}
            className="px-4 py-2 text-sm text-ink-muted hover:text-ink-primary disabled:opacity-30"
          >
            Back
          </button>
          {!isLast ? (
            <button
              onClick={() => canAdvance() && setStep(STEPS[currentStepIndex + 1].key)}
              disabled={!canAdvance()}
              className="px-5 py-2 rounded-lg bg-emerald-600 text-white text-sm font-semibold
                         hover:bg-emerald-700 transition-colors disabled:opacity-40"
            >
              Continue
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={saving}
              className="px-5 py-2 rounded-lg bg-emerald-600 text-white text-sm font-semibold
                         hover:bg-emerald-700 transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving & Scoring...' : 'Save Company'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

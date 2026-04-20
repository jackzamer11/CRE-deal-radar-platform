import { useState } from 'react'
import { X, Building2, ChevronRight } from 'lucide-react'
import { createProperty } from '../api/client'
import type { PropertyOut } from '../types'

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
  onSaved: (prop: PropertyOut) => void
}

type Step = 'location' | 'ownership' | 'financials' | 'leasing'

const STEPS: { key: Step; label: string }[] = [
  { key: 'location',   label: 'Location & Building' },
  { key: 'ownership',  label: 'Ownership' },
  { key: 'financials', label: 'Financials' },
  { key: 'leasing',    label: 'Leasing & Status' },
]

const defaultForm = {
  // Location
  address:              '',
  submarket:            '',
  asset_class:          'Class B',
  total_sf:             '',
  year_built:           '',
  last_renovation_year: '',
  // Ownership
  owner_name:           '',
  owner_type:           'LLC',
  owner_phone:          '',
  owner_email:          '',
  acquisition_year:     '',
  acquisition_price:    '',
  // Financials
  in_place_rent_psf:    '',
  occupancy_pct:        '',
  asking_price:         '',
  // Leasing
  sf_expiring_12mo:     '',
  sf_expiring_24mo:     '',
  last_lease_signed_year: '',
  is_listed:            false,
  days_on_market:       '',
  estimated_loan_maturity_year: '',
  notes:                '',
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

export default function AddPropertyModal({ onClose, onSaved }: Props) {
  const [step, setStep] = useState<Step>('location')
  const [form, setForm] = useState<FormState>(defaultForm)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (field: keyof FormState) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>
  ) => setForm(f => ({ ...f, [field]: e.target.value }))

  const setCheck = (field: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm(f => ({ ...f, [field]: e.target.checked }))

  const currentStepIndex = STEPS.findIndex(s => s.key === step)
  const isLast = currentStepIndex === STEPS.length - 1

  const canAdvance = () => {
    if (step === 'location')   return form.address && form.submarket && form.total_sf && form.year_built
    if (step === 'ownership')  return form.owner_name
    if (step === 'financials') return form.in_place_rent_psf && form.occupancy_pct
    return true
  }

  const handleSubmit = async () => {
    setSaving(true)
    setError(null)
    try {
      const payload = {
        address:              form.address,
        submarket:            form.submarket,
        asset_class:          form.asset_class,
        total_sf:             parseInt(form.total_sf),
        year_built:           parseInt(form.year_built),
        last_renovation_year: form.last_renovation_year ? parseInt(form.last_renovation_year) : undefined,
        owner_name:           form.owner_name,
        owner_type:           form.owner_type,
        owner_phone:          form.owner_phone || undefined,
        owner_email:          form.owner_email || undefined,
        acquisition_year:     form.acquisition_year ? parseInt(form.acquisition_year) : undefined,
        acquisition_price:    form.acquisition_price ? parseFloat(form.acquisition_price) : undefined,
        in_place_rent_psf:    parseFloat(form.in_place_rent_psf),
        occupancy_pct:        parseFloat(form.occupancy_pct),
        sf_expiring_12mo:     form.sf_expiring_12mo ? parseFloat(form.sf_expiring_12mo) : 0,
        sf_expiring_24mo:     form.sf_expiring_24mo ? parseFloat(form.sf_expiring_24mo) : 0,
        last_lease_signed_year: form.last_lease_signed_year ? parseInt(form.last_lease_signed_year) : undefined,
        is_listed:            form.is_listed,
        asking_price:         form.asking_price ? parseFloat(form.asking_price) : undefined,
        days_on_market:       form.days_on_market ? parseInt(form.days_on_market) : undefined,
        estimated_loan_maturity_year: form.estimated_loan_maturity_year
          ? parseInt(form.estimated_loan_maturity_year) : undefined,
        notes:                form.notes || undefined,
      }
      const result = await createProperty(payload)
      onSaved(result)
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to save property')
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
            <div className="w-8 h-8 rounded-lg bg-accent-blue/15 flex items-center justify-center">
              <Building2 size={15} className="text-accent-blue" />
            </div>
            <div>
              <div className="font-semibold text-ink-primary">Add Property</div>
              <div className="text-[11px] text-ink-muted">Signals computed automatically after save</div>
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
                  s.key === step
                    ? 'text-accent-blue'
                    : i < currentStepIndex
                    ? 'text-ink-secondary cursor-pointer hover:text-ink-primary'
                    : 'text-ink-muted cursor-default'
                }`}
              >
                <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold
                  ${s.key === step ? 'bg-accent-blue text-white' :
                    i < currentStepIndex ? 'bg-emerald-500/20 text-emerald-400' : 'bg-surface-border text-ink-muted'}`}>
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

          {/* Step 1: Location */}
          {step === 'location' && (
            <div className="space-y-4">
              <Field label="Street Address" required>
                <input className={inputCls} placeholder="e.g. 1800 N Kent St, Suite 600, Arlington, VA 22209"
                  value={form.address} onChange={set('address')} />
              </Field>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Submarket" required>
                  <select className={selectCls} value={form.submarket} onChange={set('submarket')}>
                    <option value="">Select submarket...</option>
                    {SUBMARKETS.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </Field>
                <Field label="Asset Class">
                  <select className={selectCls} value={form.asset_class} onChange={set('asset_class')}>
                    <option>Class A</option>
                    <option>Class B</option>
                    <option>Class C</option>
                  </select>
                </Field>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Total SF" required hint="rentable">
                  <input className={inputCls} type="number" placeholder="e.g. 14500"
                    value={form.total_sf} onChange={set('total_sf')} />
                </Field>
                <Field label="Year Built" required>
                  <input className={inputCls} type="number" placeholder="e.g. 2001"
                    value={form.year_built} onChange={set('year_built')} />
                </Field>
              </div>
              <Field label="Last Renovation Year" hint="leave blank if never renovated">
                <input className={inputCls} type="number" placeholder="e.g. 2015"
                  value={form.last_renovation_year} onChange={set('last_renovation_year')} />
              </Field>
            </div>
          )}

          {/* Step 2: Ownership */}
          {step === 'ownership' && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <Field label="Owner Name" required>
                  <input className={inputCls} placeholder="e.g. Rosslyn Gateway LLC"
                    value={form.owner_name} onChange={set('owner_name')} />
                </Field>
                <Field label="Owner Type">
                  <select className={selectCls} value={form.owner_type} onChange={set('owner_type')}>
                    <option>LLC</option>
                    <option>LP</option>
                    <option>Individual</option>
                    <option>REIT/FUND</option>
                    <option>Corporation</option>
                  </select>
                </Field>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Owner Phone">
                  <input className={inputCls} placeholder="703-555-0100"
                    value={form.owner_phone} onChange={set('owner_phone')} />
                </Field>
                <Field label="Owner Email">
                  <input className={inputCls} type="email" placeholder="owner@example.com"
                    value={form.owner_email} onChange={set('owner_email')} />
                </Field>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Year Acquired" hint="approx. OK">
                  <input className={inputCls} type="number" placeholder="e.g. 2013"
                    value={form.acquisition_year} onChange={set('acquisition_year')} />
                </Field>
                <Field label="Acquisition Price" hint="$">
                  <input className={inputCls} type="number" placeholder="e.g. 4200000"
                    value={form.acquisition_price} onChange={set('acquisition_price')} />
                </Field>
              </div>
              <Field label="Loan Maturity Year" hint="estimate OK — drives debt pressure signal">
                <input className={inputCls} type="number" placeholder="e.g. 2027"
                  value={form.estimated_loan_maturity_year} onChange={set('estimated_loan_maturity_year')} />
              </Field>
            </div>
          )}

          {/* Step 3: Financials */}
          {step === 'financials' && (
            <div className="space-y-4">
              <div className="p-3 rounded-lg bg-surface-muted border border-surface-border text-[11px] text-ink-muted">
                Market rent and cap rate benchmarks are auto-filled from your selected submarket.
                You only need to enter the in-place (actual) numbers.
              </div>
              <div className="grid grid-cols-2 gap-4">
                <Field label="In-Place Rent ($/SF/yr NNN)" required>
                  <input className={inputCls} type="number" step="0.50" placeholder="e.g. 26.00"
                    value={form.in_place_rent_psf} onChange={set('in_place_rent_psf')} />
                </Field>
                <Field label="Current Occupancy %" required>
                  <input className={inputCls} type="number" min="0" max="100" placeholder="e.g. 62"
                    value={form.occupancy_pct} onChange={set('occupancy_pct')} />
                </Field>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Asking Price" hint="if known or listed">
                  <input className={inputCls} type="number" placeholder="e.g. 3800000"
                    value={form.asking_price} onChange={set('asking_price')} />
                </Field>
              </div>
            </div>
          )}

          {/* Step 4: Leasing & Status */}
          {step === 'leasing' && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <Field label="SF Expiring in 12 Months" hint="drives rollover signal">
                  <input className={inputCls} type="number" placeholder="e.g. 5000"
                    value={form.sf_expiring_12mo} onChange={set('sf_expiring_12mo')} />
                </Field>
                <Field label="SF Expiring in 24 Months">
                  <input className={inputCls} type="number" placeholder="e.g. 8000"
                    value={form.sf_expiring_24mo} onChange={set('sf_expiring_24mo')} />
                </Field>
              </div>
              <Field label="Last New Lease Signed (year)" hint="drives leasing drought signal">
                <input className={inputCls} type="number" placeholder="e.g. 2022"
                  value={form.last_lease_signed_year} onChange={set('last_lease_signed_year')} />
              </Field>
              <div className="flex items-center gap-3 p-3 bg-surface-muted rounded-lg border border-surface-border">
                <input type="checkbox" id="is_listed" checked={form.is_listed}
                  onChange={setCheck('is_listed')} className="accent-amber-500 w-4 h-4" />
                <label htmlFor="is_listed" className="text-sm text-ink-secondary cursor-pointer">
                  This property is currently listed for sale
                </label>
              </div>
              {form.is_listed && (
                <Field label="Days on Market">
                  <input className={inputCls} type="number" placeholder="e.g. 45"
                    value={form.days_on_market} onChange={set('days_on_market')} />
                </Field>
              )}
              <Field label="Intel / Notes">
                <textarea className={`${inputCls} resize-none`} rows={3}
                  placeholder="Owner context, deal history, market intel..."
                  value={form.notes} onChange={set('notes')} />
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
              className="px-5 py-2 rounded-lg bg-accent-blue text-white text-sm font-semibold
                         hover:bg-accent-blueDim transition-colors disabled:opacity-40"
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
              {saving ? 'Saving & Computing Signals...' : 'Save Property'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

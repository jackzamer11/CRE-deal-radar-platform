import axios from 'axios'
import type {
  DailyBriefing,
  PropertyListOut,
  PropertyOut,
  CompanyListOut,
  CompanyOut,
  OpportunityListOut,
  OpportunityOut,
  ActivityLog,
  OutreachDraft,
  OutreachDraftRecord,
  OutreachLog,
  TenantOutreachDraft,
  Observation,
  IntelOpportunity,
  IntelDisposition,
  IntelHistoryItem,
  IntelDispositionResult,
  IntelCriterion,
  IntelGenerateResult,
  RequeueDatesResult,
  DocumentOut,
  ExtractionResult,
  ActivityMineResult,
  ActivityMiningStatus,
  Contact,
  ContactListRow,
  ContactFact,
  ThreadHeader,
  TimelinePage,
  CompanyTimelinePage,
  DataConflict,
  PendingUpdate,
  PendingUpdateDigest,
  LeaseStatus,
  LeaseConfirmResult,
  LeaseRemovalResult,
  Submarket,
} from '../types'

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

// ── Dashboard ──────────────────────────────────────────────────────────────

export const getDailyBriefing = (): Promise<DailyBriefing> =>
  api.get('/dashboard/briefing').then(r => r.data)

// ── Properties ─────────────────────────────────────────────────────────────

export interface PropertyFilters {
  submarket?: string
  priority?: string
  listed_for_sale?: boolean
  min_score?: number
  sort_by?: string
  dominant_score_type?: string
  needs_outreach?: boolean
}

export const getProperties = (filters?: PropertyFilters): Promise<PropertyListOut[]> =>
  api.get('/properties/', { params: filters }).then(r => r.data)

export const getProperty = (propertyId: string): Promise<PropertyOut> =>
  api.get(`/properties/${propertyId}`).then(r => r.data)

export const createProperty = (payload: Record<string, unknown>): Promise<PropertyOut> =>
  api.post('/properties/', payload).then(r => r.data)

export const updateProperty = (propertyId: string, payload: Record<string, unknown>): Promise<PropertyOut> =>
  api.put(`/properties/${propertyId}`, payload).then(r => r.data)

export const snoozeProperty = (
  propertyId: string,
  payload: { snoozed_until: string; snooze_reason?: string },
): Promise<PropertyOut> =>
  api.post(`/properties/${propertyId}/snooze`, payload).then(r => r.data)

export const unsnoozeProperty = (propertyId: string): Promise<PropertyOut> =>
  api.post(`/properties/${propertyId}/unsnooze`).then(r => r.data)

export const deleteProperty = (propertyId: string): Promise<{ deleted: string }> =>
  api.delete(`/properties/${propertyId}`).then(r => r.data)

export const getTenantOutreach = (propertyId: string): Promise<TenantOutreachDraft[]> =>
  api.get(`/properties/${propertyId}/tenant-outreach`).then(r => r.data)

export const refreshAllSignals = (): Promise<{ refreshed: number; timestamp: string }> =>
  api.post('/properties/refresh-signals').then(r => r.data)

export const refreshPropertySignals = (propertyId: string): Promise<PropertyOut> =>
  api.post(`/properties/${propertyId}/refresh-signals`).then(r => r.data)

// ── Companies ──────────────────────────────────────────────────────────────

export interface CompanyFilters {
  submarket?: string
  priority?: string
  expansion_only?: boolean
  min_score?: number
  rep_filter?: string         // BLANK | MAJOR | OTHER
  outreach_status?: string    // needs-outreach
}

export const getCompanies = (filters?: CompanyFilters): Promise<CompanyListOut[]> =>
  api.get('/companies/', { params: filters }).then(r => r.data)

export interface CompanyPickerRow {
  id: number
  company_id: string
  name: string
  submarket: string | null
}

// Type-ahead for the company pickers. Four columns rather than the full row —
// getCompanies() is an unpaginated fetch of every company.
export const searchCompanies = (q: string): Promise<CompanyPickerRow[]> =>
  api.get('/companies/search', { params: { q } }).then(r => r.data)

export const getCompany = (companyId: string): Promise<CompanyOut> =>
  api.get(`/companies/${companyId}`).then(r => r.data)

export const snoozeCompany = (
  companyId: string,
  payload: { snoozed_until: string; snooze_reason?: string },
): Promise<CompanyOut> =>
  api.post(`/companies/${companyId}/snooze`, payload).then(r => r.data)

export const unsnoozeCompany = (companyId: string): Promise<CompanyOut> =>
  api.post(`/companies/${companyId}/unsnooze`).then(r => r.data)

export const deleteCompany = (companyId: string): Promise<{ deleted: string }> =>
  api.delete(`/companies/${companyId}`).then(r => r.data)

export const createCompany = (payload: Record<string, unknown>): Promise<CompanyOut> =>
  api.post('/companies/', payload).then(r => r.data)

export interface LeaseExpiryUpdate {
  lease_expiry_months?: number
  lease_expiry_date?: string       // ISO "YYYY-MM-DD"
  lease_expiry_source?: string     // costar | manual | sec_filing | landlord_confirmed | public_record
}

export const updateCompanyLease = (
  companyId: string,
  payload: LeaseExpiryUpdate,
): Promise<CompanyOut> =>
  api.patch(`/companies/${companyId}/lease`, payload).then(r => r.data)

export const updateCompanyTrajectory = (
  companyId: string,
  lease_trajectory: string,
): Promise<CompanyOut> =>
  api.patch(`/companies/${companyId}/trajectory`, { lease_trajectory }).then(r => r.data)

export const updateCompanyBuildingClass = (
  companyId: string,
  current_building_class: string | null,
): Promise<CompanyOut> =>
  api.patch(`/companies/${companyId}/building-class`, { current_building_class }).then(r => r.data)

// Any name not on the submarket list joins it; "sterling" resolves to Sterling.
export const updateCompanySubmarket = (
  companyId: string,
  current_submarket: string | null,
): Promise<CompanyOut> =>
  api.patch(`/companies/${companyId}/submarket`, { current_submarket }).then(r => r.data)

// ── Submarkets (a list that grows) ───────────────────────────────────────────

export const getSubmarkets = (): Promise<Submarket[]> =>
  api.get('/submarkets/').then(r => r.data)

// Returns the existing row when the name is already on the list in any casing.
export const createSubmarket = (name: string): Promise<Submarket & { created: boolean }> =>
  api.post('/submarkets/', { name }).then(r => r.data)

export const updateCompanySfOccupied = (
  companyId: string,
  current_sf_occupied: number | null,
): Promise<CompanyOut> =>
  api.patch(`/companies/${companyId}/sf-occupied`, { current_sf_occupied }).then(r => r.data)

export const updateCompanyMedical = (
  companyId: string,
  is_medical: boolean,
): Promise<CompanyOut> =>
  api.patch(`/companies/${companyId}/medical`, { is_medical }).then(r => r.data)

export const updateCompanyRents = (
  companyId: string,
  payload: {
    effective_rent_psf: number | null
    starting_rent_psf: number | null
    building_asking_rent_psf: number | null
    lease_signed_year: number | null
  },
): Promise<CompanyOut> =>
  api.patch(`/companies/${companyId}/rents`, payload).then(r => r.data)

export const draftOutreach = (companyId: string): Promise<OutreachDraft> =>
  api.post(`/companies/${companyId}/draft-outreach`).then(r => r.data)

export const logOutreach = (
  companyId: string,
  payload: {
    email_subject: string; email_body: string
    call_script_opening: string; call_script_hook?: string | null
    call_script_data?: string | null; call_script_core: string
    call_script_pain_probe: string; call_script_close: string
    projected_sf: number | null; score_at_generation: number
    priority_at_generation: string; email_sent: boolean; call_made: boolean
  },
): Promise<OutreachLog> =>
  api.post(`/companies/${companyId}/log-outreach`, payload).then(r => r.data)

export const updateOutreachLog = (
  logId: number,
  payload: { outcome_notes?: string; marked_contacted?: boolean; email_sent?: boolean; call_made?: boolean; pair_company_id?: string },
): Promise<OutreachLog> =>
  api.patch(`/outreach-log/${logId}`, payload).then(r => r.data)

export const getOutreachHistory = (companyId: string): Promise<OutreachLog[]> =>
  api.get(`/companies/${companyId}/outreach-history`).then(r => r.data)

// Property-side outreach (Part 4)
export const draftPropertyOutreach = (
  propertyId: string,
  outreachType: string,
  tenantContext?: string,
  targetType?: string,
  intelContext?: string,
  direction?: string,
  companyId?: string,
): Promise<OutreachDraft> =>
  api.post(`/properties/${propertyId}/draft-outreach`, null, {
    params: {
      outreach_type: outreachType,
      ...(tenantContext ? { tenant_context: tenantContext }   : {}),
      ...(targetType    ? { target_type: targetType }         : {}),
      ...(intelContext  ? { intel_context_raw: intelContext } : {}),
      ...(direction     ? { direction }                       : {}),
      ...(companyId     ? { company_id: companyId }           : {}),
    },
  }).then(r => r.data)

export const logPropertyOutreach = (
  propertyId: string,
  payload: {
    email_subject: string; email_body: string
    call_script_opening: string; call_script_hook?: string | null
    call_script_data?: string | null; call_script_core: string
    call_script_pain_probe: string; call_script_close: string
    projected_sf: number | null; score_at_generation: number
    priority_at_generation: string; email_sent: boolean; call_made: boolean
    outreach_type?: string
  },
): Promise<OutreachLog> =>
  api.post(`/properties/${propertyId}/log-outreach`, payload).then(r => r.data)

export const getPropertyOutreachHistory = (propertyId: string): Promise<OutreachLog[]> =>
  api.get(`/properties/${propertyId}/outreach-history`).then(r => r.data)

// In-Place Rent pencil update (Part 7)
export const updatePropertyInPlaceRent = (
  propertyId: string,
  payload: { in_place_rent_psf: number; in_place_rent_source?: string },
): Promise<PropertyOut> =>
  api.patch(`/properties/${propertyId}/in-place-rent`, payload).then(r => r.data)

// ── Outreach Drafts (persistent) ───────────────────────────────────────────

export interface OutreachDraftPayload {
  property_id: string
  company_id?: string | null
  outreach_type: string
  direction?: string | null
  subject: string
  body: string
  call_script_opening?: string | null
  call_script_hook?: string | null
  call_script_data?: string | null
  call_script_core?: string | null
  call_script_pain_probe?: string | null
  call_script_close?: string | null
  target_type: string
  recipient_name?: string | null
  recipient_email?: string | null
  internal_context?: string | null
  /** JSON-encoded IntelFinding[] to cache on the draft record */
  intelligence_findings?: string | null
  score?: number | null
  priority?: string | null
}

export const listOutreachDrafts = (propertyId: string): Promise<OutreachDraftRecord[]> =>
  api.get(`/outreach-drafts/${propertyId}`).then(r => r.data)

export const getOutreachDraft = (
  propertyId: string,
  companyId: string,
  outreachType?: string,
  direction?: string,
): Promise<OutreachDraftRecord | null> =>
  api.get(`/outreach-drafts/${propertyId}/${companyId}`, {
    params: {
      ...(outreachType ? { outreach_type: outreachType } : {}),
      ...(direction    ? { direction }                   : {}),
    },
  }).then(r => r.data)

export const saveOutreachDraft = (payload: OutreachDraftPayload): Promise<OutreachDraftRecord> =>
  api.post('/outreach-drafts/', payload).then(r => r.data)

export const deleteOutreachDraft = (draftId: number): Promise<{ deleted: number }> =>
  api.delete(`/outreach-drafts/${draftId}`).then(r => r.data)

export const searchIntelligence = (
  propertyId: string,
  companyId: string | null | undefined,
  direction: string,
  forceRefresh = false,
): Promise<{ findings: import('../types').IntelFinding[] }> =>
  api.post('/outreach-drafts/search-intelligence', {
    property_id:   propertyId,
    company_id:    companyId ?? null,
    direction,
    force_refresh: forceRefresh,
  }).then(r => r.data)

// ── Opportunities ──────────────────────────────────────────────────────────

export interface OpportunityFilters {
  priority?: string
  deal_type?: string
  stage?: string
  active_only?: boolean
}

export const getOpportunities = (filters?: OpportunityFilters): Promise<OpportunityListOut[]> =>
  api.get('/opportunities/', { params: filters }).then(r => r.data)

export const getOpportunity = (opportunityId: string): Promise<OpportunityOut> =>
  api.get(`/opportunities/${opportunityId}`).then(r => r.data)

export const updateStage = (
  opportunityId: string,
  stage: string,
  note?: string,
): Promise<OpportunityOut> =>
  api.patch(`/opportunities/${opportunityId}/stage`, { stage, note }).then(r => r.data)

// ── Activity ───────────────────────────────────────────────────────────────

export interface ActivityFilters {
  since?: string
  action_type?: string
  limit?: number
  // Free text over the entry AND the names of the contact and company linked
  // to it. Both halves matter: summaries are written cleanly now, with the
  // person and the company as structured links rather than repeated in the
  // prose, so matching only the prose would return nothing for "Corcoran".
  q?: string
}

export const getActivity = (filters?: ActivityFilters): Promise<ActivityLog[]> =>
  api.get('/activity/', { params: filters }).then(r => r.data)

export const updateActivityNote = (
  entryId: number,
  notes: string,
): Promise<ActivityLog> =>
  api.patch(`/activity/${entryId}/notes`, { notes }).then(r => r.data)

// Edit any correctable field. A prose change re-mines the entry so the
// intelligence layer's extracted facts stay in sync with what the note now
// says; a channel or date correction saves without paying for that.
//
// contact_id and company_stamp_id are deliberately absent — they move through
// assignActivity() and restampActivity(), which are re-attachments, not edits.
export const editActivity = (
  entryId: number,
  payload: {
    action_type?: string
    action_taken?: string
    outcome?: string
    notes?: string
    follow_up_action?: string
    subject?: string
    direction?: string
    channel?: string
    log_date?: string
    disc_current_rent_psf?: number
    disc_current_sf?: number
    disc_lease_expiry?: string
    disc_decision_timeline?: string
    disc_buildout_needs?: string
    disc_decision_maker?: string
    // None means "omitted", so blanking a field names it here instead.
    clear_fields?: string[]
  },
): Promise<ActivityLog> =>
  api.patch(`/activity/${entryId}`, payload).then(r => r.data)

// Move one entry to a different company. Separate from editActivity on
// purpose: company_stamp_id is what keeps a departed contact's history on the
// old company's page, so it changes only through a deliberate action.
export const restampActivity = (
  entryId: number,
  companyId: number | null,
): Promise<ActivityLog> =>
  api.patch(`/activity/${entryId}/company-stamp`, { company_id: companyId })
    .then(r => r.data)

// Delete an entry and the facts the intelligence layer derived from it.
export const deleteActivity = (
  entryId: number,
): Promise<{ deleted: number }> =>
  api.delete(`/activity/${entryId}`).then(r => r.data)

export const updateActivityStage = (
  entryId: number,
  payload: { stage: string; next_touch_date?: string | null },
): Promise<ActivityLog> =>
  api.patch(`/activity/${entryId}/stage`, payload).then(r => r.data)

export const getReEngage = (): Promise<ActivityLog[]> =>
  api.get('/activity/re-engage').then(r => r.data)

export const createActivity = (payload: {
  action_type: string
  action_taken: string
  outcome?: string
  property_id?: number
  company_id?: number
  opportunity_id?: number
  follow_up_date?: string
  follow_up_action?: string
  outreach_type?: string
  target_type?: string
  contact_method?: string
  subject?: string
  // Contact threads — every field optional, so existing callers are unchanged.
  contact_id?: number
  company_stamp_id?: number
  direction?: string
  channel?: string
  source_message_id?: string
  disc_current_rent_psf?: number | null
  disc_current_sf?: number | null
  disc_lease_expiry?: string | null
  disc_decision_timeline?: string | null
  disc_buildout_needs?: string | null
  disc_decision_maker?: string | null
}): Promise<ActivityLog> =>
  api.post('/activity/', payload).then(r => r.data)

// Attach an existing entry to a contact — retroactive assignment (a March
// voicemail attached to Dana once you learn her name) or manual correction.
export const assignActivity = (
  entryId: number,
  payload: { contact_id: number | null; company_stamp_id?: number },
): Promise<ActivityLog> =>
  api.patch(`/activity/${entryId}/assign`, payload).then(r => r.data)

// ── Contacts ───────────────────────────────────────────────────────────────

export interface ContactFilters {
  contact_type?: string
  triaged?: boolean
  responded?: boolean
  stage?: string
  // Closed contacts are out of the default list. Pass stage:'Closed' to see
  // only them, or include_closed:true to see everything. A past client whose
  // company is back in the 6-9 month window comes back on its own.
  include_closed?: boolean
  q?: string
  limit?: number
  offset?: number
}

export const getContacts = (filters?: ContactFilters): Promise<ContactListRow[]> =>
  api.get('/contacts/', { params: filters }).then(r => r.data)

export const getContactThread = (contactId: number): Promise<ThreadHeader> =>
  api.get(`/contacts/${contactId}`).then(r => r.data)

export const getContactTimeline = (
  contactId: number,
  params?: { limit?: number; offset?: number },
): Promise<TimelinePage> =>
  api.get(`/contacts/${contactId}/timeline`, { params }).then(r => r.data)

export const searchContacts = (q: string): Promise<Contact[]> =>
  api.get('/contacts/search', { params: { q } }).then(r => r.data)

export const resolveContact = (
  payload: { email?: string; name?: string },
): Promise<{ found: boolean; contact: Contact | null }> =>
  api.post('/contacts/resolve', payload).then(r => r.data)

export const createContact = (payload: {
  name: string
  email?: string | null
  phone?: string | null
  title?: string | null
  company_id?: number | null
  contact_type?: string
  stage?: string
  next_touch_date?: string | null
  triaged?: boolean
}): Promise<Contact> =>
  api.post('/contacts/', payload).then(r => r.data)

export const updateContact = (
  contactId: number,
  payload: {
    name?: string
    email?: string | null
    phone?: string | null
    title?: string | null
    company_id?: number | null
    contact_type?: string
    stage?: string
    next_touch_date?: string | null
    responded?: boolean
    triaged?: boolean
    clear_next_touch?: boolean
  },
): Promise<Contact> =>
  api.patch(`/contacts/${contactId}`, payload).then(r => r.data)

// ── Contact facts ──────────────────────────────────────────────────────────

export const getContactFacts = (
  contactId: number,
  includeSuperseded = false,
): Promise<ContactFact[]> =>
  api.get('/contacts/facts', {
    params: { contact_id: contactId, include_superseded: includeSuperseded },
  }).then(r => r.data)

export const addContactFact = (payload: {
  contact_id: number
  fact_text: string
  source_entry_id?: number | null
  learned_date?: string | null
  supersedes_id?: number | null
}): Promise<ContactFact> =>
  api.post('/contacts/facts', payload).then(r => r.data)

// Supersede rather than delete when a new fact contradicts an old one — the
// old one stops showing but stays retrievable.
export const supersedeContactFact = (
  factId: number,
  payload: { fact_text: string; source_entry_id?: number | null },
): Promise<ContactFact> =>
  api.post(`/contacts/facts/${factId}/supersede`, payload).then(r => r.data)

// Correct a fact's wording in place — for one typed wrong, as opposed to one
// that stopped being true (that is supersedeContactFact).
export const editContactFact = (
  factId: number,
  payload: { fact_text: string; learned_date?: string },
): Promise<ContactFact> =>
  api.patch(`/contacts/facts/${factId}`, payload).then(r => r.data)

export const deleteContactFact = (factId: number): Promise<{ deleted: number }> =>
  api.delete(`/contacts/facts/${factId}`).then(r => r.data)

export interface ContactDeleteResult {
  deleted_contact_id: number
  mode: 'unattach' | 'cascade'
  entries_deleted: number
  entries_unattached: number
  facts_deleted: number
}

// unattach keeps the entries, detached, in All Activity; cascade deletes them.
// Facts go either way — a fact cannot outlive the person it describes.
export const deleteContact = (
  contactId: number,
  mode: 'unattach' | 'cascade',
): Promise<ContactDeleteResult> =>
  api.delete(`/contacts/${contactId}`, { params: { mode } }).then(r => r.data)

// ── Data conflicts ─────────────────────────────────────────────────────────
// Lease expiry, headcount, growth rate and SF never write silently.

export const getConflicts = (companyPk: number): Promise<DataConflict[]> =>
  api.get(`/contacts/conflicts/${companyPk}`).then(r => r.data)

export const reportConflict = (
  companyPk: number,
  payload: { field: string; value: string; source_entry_id?: number | null },
): Promise<DataConflict[]> =>
  api.post(`/contacts/conflicts/${companyPk}/report`, payload).then(r => r.data)

export const acceptConflict = (
  companyPk: number, field: string,
): Promise<DataConflict[]> =>
  api.post(`/contacts/conflicts/${companyPk}/${field}/accept`).then(r => r.data)

export const rejectConflict = (
  companyPk: number, field: string,
): Promise<DataConflict[]> =>
  api.post(`/contacts/conflicts/${companyPk}/${field}/reject`).then(r => r.data)

// ── Pending company updates ────────────────────────────────────────────────
// Values an EMAIL stated about a company. Same decision as a data conflict —
// both values side by side, Jack chooses — sourced from written correspondence
// rather than a call, so each carries the sentence it came from.

export const getPendingUpdates = (
  companyId?: number,
): Promise<PendingUpdateDigest> =>
  api.get('/pending-updates/', {
    params: companyId ? { company_id: companyId } : undefined,
  }).then(r => r.data)

export const acceptPendingUpdate = (updateId: number): Promise<PendingUpdate> =>
  api.post(`/pending-updates/${updateId}/accept`).then(r => r.data)

export const rejectPendingUpdate = (updateId: number): Promise<PendingUpdate> =>
  api.post(`/pending-updates/${updateId}/reject`).then(r => r.data)

// Every entry stamped to a company, interleaved across all contacts —
// including entries with no contact attached.
export const getCompanyTimeline = (
  companyId: string,
  params?: { limit?: number; offset?: number },
): Promise<CompanyTimelinePage> =>
  api.get(`/companies/${companyId}/timeline`, { params }).then(r => r.data)

// ── Pipeline ───────────────────────────────────────────────────────────────

export const runPipeline = (): Promise<{
  status: string
  properties_enriched: number
  properties_refreshed: number
  companies_refreshed: number
  new_opportunities: number
  elapsed_seconds: number
}> => api.post('/pipeline/run').then(r => r.data)

export const refreshPublicRecords = (): Promise<{
  status: string
  properties_enriched: number
}> => api.post('/pipeline/refresh-public-records').then(r => r.data)

// ── Bulk upload ────────────────────────────────────────────────────────────

export interface BulkUploadError {
  row: number
  address: string
  reason: string
}

export interface BulkUploadResult {
  inserted: number
  updated: number
  skipped: number
  errors: BulkUploadError[]
}

export const uploadPropertiesBulk = (file: File): Promise<BulkUploadResult> => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/properties/bulk-upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

export interface CoStarImportResult {
  total_rows: number
  filtered_state: number
  filtered_submarket: number
  filtered_status: number
  inserted: number
  updated: number
  skipped: number
  unmapped_submarkets: string[]
  errors: BulkUploadError[]
}

export const importCoStarExport = (file: File): Promise<CoStarImportResult> => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/properties/costar-import', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

export interface CoStarTenantImportResult {
  total_rows: number
  filtered_state: number
  filtered_submarket: number
  filtered_size: number
  inserted: number
  updated: number
  skipped: number
  unmapped_submarkets: string[]
  errors: BulkUploadError[]
}

export const importCoStarTenants = (file: File): Promise<CoStarTenantImportResult> => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/companies/costar-import', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

export interface LeaseActivityImportResult {
  updated: number
  skipped_no_match: number
  skipped_existing: number
  errors: string[]
  // Tenant effective-rent pass (optional for older backend payloads)
  tenants_matched?: number
  tenants_skipped?: number
  tenant_skips?: { tenant_name: string; reason: string }[]
}

export const importLeaseActivity = (file: File): Promise<LeaseActivityImportResult> => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/import/costar-lease-activity', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

// ── Benchmarks (CBRE Q1 2026 — config single source of truth) ─────────────────

export interface SubmarketBenchmark {
  market_rent_psf: number
  vacancy_pct: number
  source: string
  /** True = placeholder/proxy numbers, not measured CBRE data — verify before quoting */
  provisional?: boolean
}

export const getBenchmarks = (): Promise<{
  nova: Record<string, number | string>
  submarkets: Record<string, SubmarketBenchmark>
}> => api.get('/benchmarks/nova').then(r => r.data)

// ── Import Lease Comps (PDF → tenant company lease expirations) ───────────────

export interface LeaseCompMatch {
  tenant_name: string
  company_id: string
  company_name: string
  proposed_expiry: string   // ISO YYYY-MM-DD
  confidence: number
}

export interface LeaseCompSkippedNoMatch {
  tenant_name: string
  proposed_expiry: string
}

export interface LeaseCompSkippedAlreadySet {
  tenant_name: string
  company_id: string
  company_name: string
  existing_expiry: string | null
}

export interface LeaseCompsPreviewResult {
  parsed_count: number
  auto_applied: LeaseCompMatch[]
  needs_review: LeaseCompMatch[]
  skipped_no_match: LeaseCompSkippedNoMatch[]
  skipped_already_set: LeaseCompSkippedAlreadySet[]
}

export interface LeaseCompsConfirmResult {
  applied: { company_id: string; company_name: string; expiration_date: string }[]
  skipped: { company_id: string; company_name?: string; reason: string }[]
}

// Parse the PDF, auto-apply exact matches, and return the four result lists.
export const previewLeaseComps = (file: File): Promise<LeaseCompsPreviewResult> => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/lease-comps/preview', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

// Write the user-confirmed fuzzy ("needs review") matches.
export const confirmLeaseComps = (
  matches: { company_id: string; expiration_date: string }[],
): Promise<LeaseCompsConfirmResult> =>
  api.post('/lease-comps/confirm', { matches }).then(r => r.data)

// ── Observations (Private Intelligence Layer) ────────────────────────────────

export interface ObservationFilters {
  entity_type?: string
  entity_id?: number
  human_verified?: boolean
}

export const getObservations = (filters?: ObservationFilters): Promise<Observation[]> =>
  api.get('/observations/', { params: filters }).then(r => r.data)

// Confirm (value omitted) or correct (value supplied). Backend creates a new
// verified row that supersedes the original — the old row never edits in place.
export const verifyObservation = (
  observationId: number,
  value?: string,
): Promise<Observation> =>
  api.post(`/observations/${observationId}/verify`, { value: value ?? null }).then(r => r.data)

// ── Intel (Phase D — signal-driven opportunities) ────────────────────────────

// Returns the opportunities AND a summary of what was scanned — a run that
// finds nothing must be distinguishable from a button that did nothing.
export const generateIntelOpportunities = (): Promise<IntelGenerateResult> =>
  api.post('/intel/opportunities/generate').then(r => r.data)

// One-time backfill: send auto-approved but imprecise lease dates back to Review.
export const requeueFuzzyDates = (): Promise<RequeueDatesResult> =>
  api.post('/intel/activity/requeue-dates').then(r => r.data)

export const getIntelOpportunities = (status = 'open'): Promise<IntelOpportunity[]> =>
  api.get('/intel/opportunities', { params: { status } }).then(r => r.data)

// ── Intel feedback loop (Phase E) ────────────────────────────────────────────

export const dispositionIntelOpportunity = (
  opportunityId: number,
  payload: { disposition: IntelDisposition; reason_category?: string; reason_text?: string },
): Promise<IntelDispositionResult> =>
  api.post(`/intel/opportunities/${opportunityId}/disposition`, payload).then(r => r.data)

export const getIntelHistory = (): Promise<IntelHistoryItem[]> =>
  api.get('/intel/history').then(r => r.data)

export const getIntelCriteria = (): Promise<IntelCriterion[]> =>
  api.get('/intel/criteria').then(r => r.data)

export const saveIntelCriterion = (
  statement: string,
  criterion_type?: string,
): Promise<IntelCriterion> =>
  api.post('/intel/criteria', { statement, criterion_type }).then(r => r.data)

// ── Lease documents (upload + extract) ───────────────────────────────────────

export const uploadDocument = (file: File): Promise<DocumentOut> => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/documents/', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

export const extractDocument = (documentId: number): Promise<ExtractionResult> =>
  api.post(`/documents/${documentId}/extract`).then(r => r.data)

// ── Activity-log mining (freeform notes → structured facts) ──────────────────

export const getActivityMiningStatus = (): Promise<ActivityMiningStatus> =>
  api.get('/intel/activity/status').then(r => r.data)

// Mined in batches so a long backfill never blocks on one HTTP request.
export const mineActivityLogs = (
  limit?: number,
  force = false,
): Promise<ActivityMineResult> =>
  api.post('/intel/activity/mine', { limit, force }).then(r => r.data)

// ── Lease documents ─────────────────────────────────────────────────────────
// The stored filename is bare; the folder is a backend setting. The file link
// therefore goes through the API rather than a file:// URL, which a browser
// will not open from a page.

export const getLease = (companyPk: number): Promise<LeaseStatus> =>
  api.get(`/leases/companies/${companyPk}`).then(r => r.data)

// Stores and links the file FIRST, then reads it. An extraction failure comes
// back as extraction_error on a successful response — the document is never
// lost because the reading failed.
export const uploadLease = (companyPk: number, file: File): Promise<LeaseStatus> => {
  const body = new FormData()
  body.append('file', file)
  return api
    .post(`/leases/companies/${companyPk}/upload`, body, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then(r => r.data)
}

// Re-read an already-stored lease — for after an API key is added. The current
// lease unless leaseId names a prior term.
export const reextractLease = (companyPk: number, leaseId?: number): Promise<LeaseStatus> =>
  api
    .post(`/leases/companies/${companyPk}/reextract`, null, {
      params: leaseId != null ? { lease_id: leaseId } : undefined,
    })
    .then(r => r.data)

// Saves the checked fields to the lease and — for the current lease — writes
// expiry, address and SF to the company record. manualValues holds what Jack
// typed; a typed value is marked 'manual', and only writes if its row is
// checked. leaseId confirms a prior term (which never touches the company).
export const confirmLeaseExtraction = (
  companyPk: number,
  acceptedFields: string[],
  manualValues?: Record<string, string>,
  leaseId?: number,
): Promise<LeaseConfirmResult> =>
  api
    .post(`/leases/companies/${companyPk}/confirm`, {
      accepted_fields: acceptedFields,
      manual_values: manualValues ?? null,
      lease_id: leaseId ?? null,
    })
    .then(r => r.data)

// Removes ONE lease — its record and its PDF. Omitting leaseId removes the
// current lease, and the next most recent is promoted. Confirmed company
// values are never rolled back to nothing. Keyed by the CO-nnn business id,
// like every other /companies/ call.
export const removeLease = (
  companyBusinessId: string,
  leaseId?: number,
): Promise<LeaseRemovalResult> =>
  api
    .delete(`/companies/${companyBusinessId}/lease`, {
      params: leaseId != null ? { lease_id: leaseId } : undefined,
    })
    .then(r => r.data)

// The URL the "open the lease" link points at. Not a request — the browser
// navigates to it, and a missing file comes back as a plain 404 message.
export const leaseFileUrl = (companyPk: number): string =>
  `/api/leases/companies/${companyPk}/file`

// Any lease's own file — how a prior term opens.
export const leaseFileUrlById = (leaseId: number): string =>
  `/api/leases/${leaseId}/file`

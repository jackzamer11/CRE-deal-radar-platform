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
  api.get('/properties', { params: filters }).then(r => r.data)

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
  api.get('/companies', { params: filters }).then(r => r.data)

export const getCompany = (companyId: string): Promise<CompanyOut> =>
  api.get(`/companies/${companyId}`).then(r => r.data)

export const snoozeCompany = (
  companyId: string,
  payload: { snoozed_until: string; snooze_reason?: string },
): Promise<CompanyOut> =>
  api.post(`/companies/${companyId}/snooze`, payload).then(r => r.data)

export const unsnoozeCompany = (companyId: string): Promise<CompanyOut> =>
  api.post(`/companies/${companyId}/unsnooze`).then(r => r.data)

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

export const draftOutreach = (companyId: string): Promise<OutreachDraft> =>
  api.post(`/companies/${companyId}/draft-outreach`).then(r => r.data)

export const logOutreach = (
  companyId: string,
  payload: {
    email_subject: string; email_body: string
    call_script_opening: string; call_script_core: string
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
    call_script_opening: string; call_script_core: string
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
  api.get('/opportunities', { params: filters }).then(r => r.data)

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
}

export const getActivity = (filters?: ActivityFilters): Promise<ActivityLog[]> =>
  api.get('/activity', { params: filters }).then(r => r.data)

export const updateActivityNote = (
  entryId: number,
  notes: string,
): Promise<ActivityLog> =>
  api.patch(`/activity/${entryId}/notes`, { notes }).then(r => r.data)

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
}): Promise<ActivityLog> =>
  api.post('/activity', payload).then(r => r.data)

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
}

export const importLeaseActivity = (file: File): Promise<LeaseActivityImportResult> => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/import/costar-lease-activity', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

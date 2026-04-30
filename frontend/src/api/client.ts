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
  is_listed?: boolean
  min_score?: number
  sort_by?: string
}

export const getProperties = (filters?: PropertyFilters): Promise<PropertyListOut[]> =>
  api.get('/properties', { params: filters }).then(r => r.data)

export const getProperty = (propertyId: string): Promise<PropertyOut> =>
  api.get(`/properties/${propertyId}`).then(r => r.data)

export const createProperty = (payload: Record<string, unknown>): Promise<PropertyOut> =>
  api.post('/properties/', payload).then(r => r.data)

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
}

export const getCompanies = (filters?: CompanyFilters): Promise<CompanyListOut[]> =>
  api.get('/companies', { params: filters }).then(r => r.data)

export const getCompany = (companyId: string): Promise<CompanyOut> =>
  api.get(`/companies/${companyId}`).then(r => r.data)

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

export const createActivity = (payload: {
  action_type: string
  action_taken: string
  outcome?: string
  property_id?: number
  company_id?: number
  opportunity_id?: number
  follow_up_date?: string
  follow_up_action?: string
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

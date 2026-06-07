export type Priority = 'IMMEDIATE' | 'HIGH' | 'WORKABLE' | 'IGNORE'
export type DealType = 'PRE_MARKET' | 'ACTIVE_MISPRICED' | 'TENANT_DRIVEN'
export type Confidence = 'HIGH' | 'MEDIUM' | 'LOW'
export type Stage = 'IDENTIFIED' | 'CONTACTED' | 'ACTIVE' | 'UNDER_LOI' | 'CLOSED' | 'DEAD'
export type ActionType = 'CALL' | 'EMAIL' | 'MEETING' | 'SIGNAL_UPDATE' | 'RESEARCH' | 'NOTE'

// Activity-log pipeline stage — current state only; can move any direction.
// (Named ActivityStage to avoid colliding with the opportunity `Stage` above.)
export type ActivityStage = 'Sent' | 'Replied' | 'Interested' | 'In Play' | 'Not Interested' | 'Dormant'
export const STAGES: ActivityStage[] = ['Sent', 'Replied', 'Interested', 'In Play', 'Not Interested', 'Dormant']
// Stages that prompt for a revisit (next_touch_date) when selected.
export const REVISIT_STAGES: ActivityStage[] = ['Not Interested', 'Dormant']

export interface SignalBreakdown {
  lease_rollover: number
  vacancy_trend: number
  ownership_duration: number
  leasing_drought: number
  capex_gap: number
  hold_period: number
  occupancy_decline: number
  rent_stagnation: number
  reinvestment_inactivity: number
  debt_pressure: number
  rent_gap: number
  price_psf: number
  dom_premium: number
  cap_rate_spread: number
}

export interface MatchedTenant {
  company_id: string
  name: string
  industry: string
  headcount: number | null
  sf_needed: number
  submarket: string | null
  match_score: number
  match_reasons: string[]
}

export interface PropertyListOut {
  id: number
  property_id: string
  address: string
  submarket: string
  asset_class: string
  total_sf: number
  owner_name: string
  occupancy_pct: number | null
  years_owned: number | null
  lease_rollover_pct: number
  prediction_score: number
  mispricing_score: number
  signal_score: number
  priority: Priority
  listed_for_sale: boolean
  listed_for_lease: boolean
  notes: string | null
  signals_scored_count: number
  insufficient_data: boolean
  // Part 1/3 enrichment + scores
  in_place_rent_psf: number | null
  market_rent_psf: number | null
  tenant_match_score: number | null
  listing_rep_score: number | null
  acquisition_score: number | null
  dominant_score_type: string | null
  star_rating: number | null
  sf_avail: number | null
  landlord_representative: string | null
  landlord_rep_contact: string | null
  sales_contact: string | null
  // Snooze state (null = not snoozed)
  snoozed_until: string | null
  snooze_reason: string | null
  returned_from_snooze: boolean | null
  // Owner confirmed open to leasing while listed
  owner_confirmed_leasing: boolean
  owner_confirmed_leasing_date: string | null
}

export interface TenantOutreachDraft {
  company_id: string
  company_name: string
  contact_name: string | null
  sf_needed: number
  lease_expiry_months: number | null
  email_draft: string
  call_script: string
}

export interface PropertyOut extends PropertyListOut {
  year_built: number
  last_renovation_year: number | null
  owner_type: string
  owner_phone: string | null
  owner_email: string | null
  acquisition_date: string | null
  acquisition_price: number | null
  asking_price: number | null
  asking_price_psf: number | null
  in_place_rent_psf: number | null
  market_rent_psf: number | null
  in_place_rent_source: string | null
  in_place_rent_last_verified: string | null
  noi: number | null
  cap_rate: number | null
  market_cap_rate: number
  vacancy_pct: number | null
  vacancy_12mo_ago: number | null
  vacant_sf: number | null
  sf_expiring_12mo: number
  sf_expiring_24mo: number
  last_lease_signed_date: string | null
  estimated_loan_maturity_year: number | null
  lease_rollover_pct: number
  years_since_last_lease: number
  listed_for_sale: boolean
  listed_for_lease: boolean
  days_on_market: number | null
  owner_behavior_score: number
  deal_type: string | null
  signal_breakdown: SignalBreakdown | null
  // CoStar enrichment fields
  sales_company: string | null
  tenancy: string | null
  stories: number | null
  parking_ratio: number | null
  matched_tenants: MatchedTenant[]
}

export type RepClass = 'BLANK' | 'MAJOR' | 'OTHER'

export interface CompanyListOut {
  id: number
  company_id: string
  name: string
  industry: string
  current_headcount: number | null
  headcount_growth_pct: number | null
  current_submarket: string | null
  current_sf: number | null
  current_rent_psf: number | null
  lease_expiry_months: number | null
  lease_expiry_date: string | null
  lease_expiry_source: string | null
  lease_trajectory: string
  tenant_representative: string | null
  rep_class: RepClass
  primary_contact_name: string | null
  primary_contact_title: string | null
  primary_contact_phone: string | null
  future_move_flag: boolean | null
  future_move_type: string | null
  expansion_signal: boolean
  contraction_signal: boolean
  opportunity_score: number
  priority: Priority
  signals_scored_count: number
  insufficient_data: boolean
  late_stage: boolean
  expiry_priority_override: boolean
  snoozed_until: string | null
  snooze_reason: string | null
  returned_from_snooze: boolean | null
}

export interface OutreachCallScript {
  opening: string
  core_message: string
  pain_probe: string
  the_close: string
}

export interface OutreachDraft {
  email_subject: string
  email_body: string
  call_script: OutreachCallScript
  projected_sf: number | null
  score: number
  priority: Priority
  generated_at: string
  outreach_type?: string
  target_type?: string
}

export interface OutreachLog {
  id: number
  company_id: number | null
  property_id: number | null
  outreach_type: string
  generated_at: string
  email_subject: string
  email_body: string
  call_script_opening: string
  call_script_core: string
  call_script_pain_probe: string
  call_script_close: string
  projected_sf: number | null
  score_at_generation: number
  priority_at_generation: string
  marked_contacted: boolean
  email_sent: boolean
  call_made: boolean
  outcome_notes: string | null
  contacted_at: string | null
}

export interface MatchedProperty {
  property_id: string
  address: string
  submarket: string
  sf_avail: number | null
  vacancy_pct: number | null
  in_place_rent_psf: number | null
  market_rent_psf: number | null
  landlord_representative: string | null
  landlord_rep_contact: string | null
  sales_contact: string | null
  listed_for_sale: boolean
  match_score: number
  match_reasons: string[]
}

export interface CompanyOut extends CompanyListOut {
  description: string | null
  open_positions: number
  hiring_velocity: number | null
  current_sf: number | null
  sf_per_head: number | null
  estimated_sf_needed: number | null
  sig_headcount_growth: number
  sig_hiring_velocity: number
  sig_lease_expiry: number
  sig_space_utilization: number
  sig_geo_clustering: number
  lease_expiry_source: string | null
  lease_expiry_last_verified: string | null
  primary_contact_name: string | null
  primary_contact_title: string | null
  primary_contact_phone: string | null
  matched_properties: MatchedProperty[]
}

export interface OpportunityListOut {
  id: number
  opportunity_id: string
  deal_type: DealType
  opportunity_category: string
  score: number
  priority: Priority
  confidence_level: Confidence
  thesis: string
  next_action: string
  stage: Stage
  estimated_deal_value: number | null
  estimated_commission: number | null
  property_address: string | null
  property_submarket: string | null
  property_str_id: string | null
  company_name: string | null
  company_str_id: string | null
  prediction_score: number | null
  owner_behavior_score: number | null
  mispricing_score: number | null
  tenant_opportunity_score: number | null
  property_ref: string | null
  company_ref: string | null
}

export interface OpportunityOut extends OpportunityListOut {
  property_id: number | null
  company_id: number | null
  prediction_score: number | null
  owner_behavior_score: number | null
  mispricing_score: number | null
  tenant_opportunity_score: number | null
  call_script: string | null
  is_active: boolean
}

export interface ActivityLog {
  id: number
  log_date: string
  opportunity_id: number | null
  property_id: number | null
  company_id: number | null
  action_type: ActionType
  action_taken: string
  outcome: string | null
  follow_up_date: string | null
  follow_up_action: string | null
  created_by: string
  stage: ActivityStage
  next_touch_date: string | null
  property_address: string | null
  company_name: string | null
  opportunity_ref: string | null
  contact_name: string | null
  outreach_type: string | null
  notes: string | null
}

export interface CallTarget {
  rank: number
  opportunity_id: string
  deal_type: DealType
  priority: Priority
  score: number
  confidence_level: Confidence
  property_id: string | null
  company_id: string | null
  property_address: string | null
  property_submarket: string | null
  company_name: string | null
  owner_name: string | null
  thesis: string
  next_action: string
  call_script: string | null
  estimated_commission: number | null
}

export interface DashboardStats {
  total_properties: number
  total_companies: number
  total_opportunities: number
  immediate_count: number
  high_count: number
  pre_market_count: number
  tenant_driven_count: number
  active_mispriced_count: number
  avg_prediction_score: number
  avg_signal_score: number
}

export interface TenantMatchTarget {
  rank: number
  property_id: string
  address: string
  submarket: string
  asset_class: string
  total_sf: number
  owner_name: string
  vacancy_pct: number | null
  sf_avail: number | null
  tenant_match_score: number
  in_place_rent_psf: number | null
  market_rent_psf: number | null
}

export interface TenantMatchAction {
  property_id: string
  address: string
  submarket: string
  sf_avail: number | null
  landlord_representative: string | null
  listed_for_sale: boolean
  outreach_type: string
  target_type: string
  tenant_company_id: string
  tenant_name: string
  tenant_industry: string
  tenant_headcount: number | null
  tenant_sf_needed: number
  match_score: number
  lease_expiry_months: number | null
  contact_status: string
}

export interface AcquisitionTarget {
  property_id: string
  address: string
  submarket: string
  year_built: number | null
  total_sf: number
  vacancy_pct: number | null
  signal_score: number
  dominant_signal: string | null
  asking_price: number | null
  estimated_value: number | null
  target_type: string
  owner_name: string
  sales_contact: string | null
  contact_status: string
}

export interface ExpiredLease {
  company_id: string
  name: string
  industry: string
  sf_needed: number | null
  submarket: string | null
  headcount: number | null
}

export interface DailyBriefing {
  briefing_date: string
  stats: DashboardStats
  immediate_deals: CallTarget[]
  high_priority_deals: CallTarget[]
  pre_market_predictions: CallTarget[]
  tenant_opportunities: CallTarget[]
  tenant_match_properties: TenantMatchTarget[]
  tenant_match_actions: TenantMatchAction[]
  acquisition_targets: AcquisitionTarget[]
  expired_leases: ExpiredLease[]
  returned_from_snooze_property_ids: string[]
  signal_refresh_timestamp: string | null
}

export interface IntelFinding {
  fact: string
  source_url: string
  source_name: string
  relevance_score: number
  checked?: boolean
}

export interface OutreachDraftRecord {
  id: number
  property_id: string
  company_id: string | null
  outreach_type: string
  direction?: string
  subject: string
  body: string
  call_script_opening: string | null
  call_script_core: string | null
  call_script_pain_probe: string | null
  call_script_close: string | null
  target_type: string
  recipient_name: string | null
  recipient_email: string | null
  internal_context: string | null
  /** JSON-encoded IntelFinding[]; null = search not yet run; "[]" = ran, no results */
  intelligence_findings: string | null
  score: number | null
  priority: string | null
  created_at: string
  last_viewed_at: string
}

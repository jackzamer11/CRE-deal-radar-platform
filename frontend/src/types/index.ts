export type Priority = 'IMMEDIATE' | 'HIGH' | 'WORKABLE' | 'IGNORE'
export type DealType = 'PRE_MARKET' | 'ACTIVE_MISPRICED' | 'TENANT_DRIVEN'
export type Confidence = 'HIGH' | 'MEDIUM' | 'LOW'
export type Stage = 'IDENTIFIED' | 'CONTACTED' | 'ACTIVE' | 'UNDER_LOI' | 'CLOSED' | 'DEAD'
export type ActionType = 'CALL' | 'EMAIL' | 'MEETING' | 'SIGNAL_UPDATE' | 'RESEARCH' | 'NOTE'

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

export interface PropertyListOut {
  id: number
  property_id: string
  address: string
  submarket: string
  asset_class: string
  total_sf: number
  owner_name: string
  occupancy_pct: number
  years_owned: number | null
  lease_rollover_pct: number
  prediction_score: number
  mispricing_score: number
  signal_score: number
  priority: Priority
  is_listed: boolean
  notes: string | null
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
  in_place_rent_psf: number
  market_rent_psf: number
  noi: number | null
  cap_rate: number | null
  market_cap_rate: number
  vacancy_pct: number
  vacancy_12mo_ago: number | null
  vacant_sf: number | null
  sf_expiring_12mo: number
  lease_rollover_pct: number
  years_since_last_lease: number
  is_listed: boolean
  days_on_market: number | null
  owner_behavior_score: number
  deal_type: string | null
  signal_breakdown: SignalBreakdown | null
}

export interface CompanyListOut {
  id: number
  company_id: string
  name: string
  industry: string
  current_headcount: number
  headcount_growth_pct: number | null
  current_submarket: string | null
  lease_expiry_months: number | null
  expansion_signal: boolean
  opportunity_score: number
  priority: Priority
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
  primary_contact_name: string | null
  primary_contact_title: string | null
  primary_contact_phone: string | null
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
  company_name: string | null
  prediction_score: number | null
  owner_behavior_score: number | null
  mispricing_score: number | null
  tenant_opportunity_score: number | null
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
  property_address: string | null
  company_name: string | null
  opportunity_ref: string | null
}

export interface CallTarget {
  rank: number
  opportunity_id: string
  deal_type: DealType
  priority: Priority
  score: number
  confidence_level: Confidence
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

export interface DailyBriefing {
  briefing_date: string
  stats: DashboardStats
  immediate_deals: CallTarget[]
  pre_market_predictions: CallTarget[]
  tenant_opportunities: CallTarget[]
  signal_refresh_timestamp: string | null
}

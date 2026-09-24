export type Priority = 'IMMEDIATE' | 'HIGH' | 'WORKABLE' | 'IGNORE'
export type DealType = 'PRE_MARKET' | 'ACTIVE_MISPRICED' | 'TENANT_DRIVEN'
export type Confidence = 'HIGH' | 'MEDIUM' | 'LOW'
export type Stage = 'IDENTIFIED' | 'CONTACTED' | 'ACTIVE' | 'UNDER_LOI' | 'CLOSED' | 'DEAD'
export type ActionType =
  | 'CALL' | 'EMAIL' | 'MEETING' | 'SIGNAL_UPDATE' | 'RESEARCH' | 'NOTE'
  // A stage transition. Not a touch: no direction, no channel, not outreach.
  // Rendered as a thin divider, excluded from entry counts.
  | 'STAGE_CHANGE'

export const STAGE_CHANGE_ACTION: ActionType = 'STAGE_CHANGE'

// The action types Jack can pick when logging or correcting an entry.
// STAGE_CHANGE is absent on purpose — it is written by moving a stage pill,
// never by choosing it from a list.
export const LOGGABLE_ACTION_TYPES: ActionType[] =
  ['CALL', 'EMAIL', 'MEETING', 'RESEARCH', 'NOTE', 'SIGNAL_UPDATE']

// Activity-log pipeline stage — current state only; can move any direction.
// (Named ActivityStage to avoid colliding with the opportunity `Stage` above.)
export type ActivityStage =
  | 'Sent' | 'Replied' | 'Interested' | 'In Play' | 'Not Interested' | 'Dormant'
  | 'Closed'
// Entry-level stages: what an ActivityLog row can be set to. Deliberately does
// NOT include Closed — Closed belongs to the PERSON (it means Jack placed them),
// and the entry-level stage endpoint rejects it.
export const STAGES: ActivityStage[] = ['Sent', 'Replied', 'Interested', 'In Play', 'Not Interested', 'Dormant']
// Contact stages: the seven a person can be in. Used by the thread's stage
// pills and the By Contact filter bar.
export const CONTACT_STAGES: ActivityStage[] = [...STAGES, 'Closed']
// Closed drops a contact out of the default list and the active queue, the same
// way untriaged does.
export const CLOSED_STAGE: ActivityStage = 'Closed'
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
  sf_needed: number | null        // real occupied SF; null when unknown
  sf_display: string              // "11,000 SF" when known, else "Unknown"
  submarket: string | null
  match_score: number
  match_reasons: string[]
  adjacent_submarket?: boolean
  is_medical: boolean
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
  is_medical: boolean
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
  is_medical: boolean
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
  current_sf_occupied: number | null
  current_rent_psf: number | null
  // Rent-gap ladder inputs (plain company fields; null = unknown)
  effective_rent_psf: number | null
  starting_rent_psf: number | null
  building_asking_rent_psf: number | null
  lease_signed_year: number | null
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
  is_medical: boolean
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
  /** DATA — CALL SHEET block of labeled raw values (tenant sheets only; "not on file" when missing) */
  data?: string
  /** ANGLE — one line from the same rung + direction logic as the email rent line (tenant sheets only) */
  angle?: string
  /** DISCOVERY checklist on tenant sheets; prose core message on property scripts */
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
  /** ANGLE line (reused column — legacy rows hold THE HOOK prose) */
  call_script_hook?: string | null
  /** DATA block of the CALL SHEET (labeled raw values) */
  call_script_data?: string | null
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
  adjacent_submarket?: boolean
  is_medical: boolean
}

export interface CompanyOut extends CompanyListOut {
  description: string | null
  is_medical: boolean
  open_positions: number | null
  hiring_velocity: number | null
  current_sf_occupied: number | null
  current_building_class: string | null
  current_address: string | null
  linkedin_url: string | null
  website: string | null
  sf_per_head: number | null
  sig_headcount_growth: number
  sig_hiring_velocity: number
  sig_lease_expiry: number
  sig_space_utilization: number
  sig_geo_clustering: number
  lease_expiry_source: string | null
  lease_expiry_last_verified: string | null
  // Which fields came off the signed lease rather than CoStar. A lease
  // outranks CoStar, so the UI marks the difference (LEASE_SOURCE).
  current_address_source: string | null
  current_sf_occupied_source: string | null
  // The linked lease document — a bare filename; the folder is a backend
  // setting, so no path ever reaches the client.
  lease_file_name: string | null
  lease_uploaded_at: string | null
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
  // Contact threads
  contact_id: number | null
  company_stamp_id: number | null
  company_stamp_name: string | null
  direction: Direction | null
  channel: Channel | null
  source_message_id: string | null
  // Where a split entry came from — a multi-deal email such as a weekly
  // leasing roundup. Null on direct correspondence and hand entries.
  source_note?: string | null
  // Discovery capture — displayed only; nothing consumes these.
  disc_current_rent_psf: number | null
  disc_current_sf: number | null
  disc_lease_expiry: string | null
  disc_decision_timeline: string | null
  disc_buildout_needs: string | null
  disc_decision_maker: string | null
  // Set only on a STAGE_CHANGE row — the transition the divider shows.
  stage_from: string | null
  stage_to: string | null
  // Out of the Needs a Contact queue and its badge; still searchable, still in
  // All Activity, still on its company's card.
  archived?: boolean
}

// ── Contact threads ─────────────────────────────────────────────────────────
// A Contact owns its pipeline stage and next-touch date; entries attach to it.

// owner exists in the backend so the owner side never needs a second
// migration, but only tenant and counterparty surface in the UI this build.
export type ContactType = 'tenant' | 'counterparty' | 'owner'
export const UI_CONTACT_TYPES: ContactType[] = ['tenant', 'counterparty']
export const CONTACT_TYPE_LABELS: Record<ContactType, string> = {
  tenant:       'Tenant',
  counterparty: 'Counterparty',
  owner:        'Owner',
}

export type Direction = 'outbound' | 'inbound'
export type Channel = 'email' | 'call' | 'meeting' | 'text' | 'linkedin' | 'other'
export const CHANNELS: Channel[] = ['email', 'call', 'meeting', 'text', 'linkedin', 'other']

export interface Contact {
  id: number
  name: string
  email: string | null
  phone: string | null
  title: string | null
  company_id: number | null
  contact_type: ContactType
  stage: ActivityStage
  stage_changed_at: string | null
  // Set when the stage moves to Closed, cleared when it moves off.
  closed_at: string | null
  next_touch_date: string | null
  // Jack's own line about where this person stands, and when he wrote it.
  current_status: string | null
  current_status_updated_at: string | null
  responded: boolean
  // True once Jack has placed this tenant — permanent, never cleared by moving
  // off Closed.
  is_past_client: boolean
  triaged: boolean
  auto_created: boolean
  company_name: string | null
}

export interface ContactListRow {
  id: number
  name: string
  email: string | null
  title: string | null
  contact_type: ContactType
  stage: ActivityStage
  stage_changed_at: string | null
  closed_at: string | null
  days_in_stage: number | null
  next_touch_date: string | null
  overdue: boolean
  // Shown on the card IN PLACE OF latest_entry_summary when set; the card
  // falls back to latest_entry_summary when this is empty.
  current_status: string | null
  current_status_updated_at: string | null
  responded: boolean
  triaged: boolean
  auto_created: boolean
  is_past_client: boolean
  // Their company's lease has come back into the 6-9 month window, which is why
  // a Closed contact is on this list at all.
  past_client_reentry: boolean
  lease_expiry_months: number | null
  company_id: number | null
  company_name: string | null
  // Real correspondence only; copies counted separately. copied_only means this
  // person has never been written to directly.
  entry_count: number
  copied_count: number
  copied_only: boolean
  // The list's sort key: newest entry date first, entry id breaking same-day
  // ties. Both halves come from the server so the merged list (contacts plus
  // company cards) orders by one rule.
  latest_entry_date: string | null
  latest_entry_id: number | null
  latest_entry_summary: string | null
  latest_entry_channel: Channel | null
}

// One card in the By Contact list for a company holding entries that are not
// on a person yet. Shaped like ContactListRow where the two overlap so the
// list can render both in one stream.
export interface CompanyCardRow {
  kind: 'company'
  id: number                      // Company primary key
  company_key: string | null      // CO-nnn, for the timeline panel
  name: string
  entry_count: number
  // How many of entry_count are archived. The card still counts them.
  archived_count: number
  last_touch: string | null
  // 0 means "no contacts yet" — why there is nobody to put these entries on.
  contact_count: number
  // Always false. A card exists because the work has NOT been done.
  triaged: false
  // last_touch is this card's latest_entry_date; latest_entry_id is the same
  // tie-break the contact rows carry. Together they let a card sort into the
  // contact list rather than sitting in a group above it.
  latest_entry_id: number | null
  latest_entry_summary: string | null
  latest_entry_channel: Channel | null
}

// A row in the needs-a-contact queue: the entry, plus enough company context
// to open the picker on the right company.
export interface NeedsContactEntry extends ActivityLog {
  effective_company_id: number | null
  effective_company_name: string | null
}

export interface NeedsContactPage {
  // How much is LEFT, not how much this page shows — the badge number.
  total: number
  limit: number
  offset: number
  entries: NeedsContactEntry[]
}

export interface ContactFact {
  id: number
  contact_id: number
  fact_text: string
  source_entry_id: number | null
  learned_date: string
  superseded_by_id: number | null
  is_active: boolean
}

export interface TimelineEntry {
  id: number
  log_date: string
  contact_id: number | null
  contact_name: string | null
  company_stamp_id: number | null
  company_stamp_name: string | null
  action_type: ActionType
  action_taken: string
  outcome: string | null
  notes: string | null
  follow_up_action: string | null
  direction: Direction | null
  channel: Channel | null
  outreach_type: string | null
  subject: string | null
  // Set only on a STAGE_CHANGE row — the transition the divider shows.
  stage_from: string | null
  stage_to: string | null
  disc_current_rent_psf: number | null
  disc_current_sf: number | null
  disc_lease_expiry: string | null
  disc_decision_timeline: string | null
  disc_buildout_needs: string | null
  disc_decision_maker: string | null
  // True when this person was only copied on the email. Rendered distinctly —
  // it is history on their thread, not correspondence with them.
  participation: boolean
  // Set when the entry was split out of a multi-deal email.
  source_note?: string | null
  attachments: TimelineAttachment[]
}

// A file that arrived on an ingested email. file_name is the name it arrived
// under and what is shown; stored_path is where the copy went, relative to the
// documents folder, which is a setting joined at read time.
export interface TimelineAttachment {
  id: number
  file_name: string
  stored_year: number
  stored_path: string | null
  // Recorded but never written — over the size ceiling. Distinct from
  // `missing`: there is nothing to go looking for.
  oversize: boolean
  description: string | null
  saved_date: string | null
  missing: boolean
}

// The fields the shared entry editor writes. Both ActivityLog (the flat feed)
// and TimelineEntry (a contact thread) satisfy it structurally, so there is one
// editor rather than one per surface.
export interface EditableEntry {
  id: number
  action_type: ActionType
  action_taken: string
  outcome: string | null
  notes: string | null
  follow_up_action: string | null
  direction: Direction | null
  channel: Channel | null
  log_date: string
  disc_current_rent_psf: number | null
  disc_current_sf: number | null
  disc_lease_expiry: string | null
  disc_decision_timeline: string | null
  disc_buildout_needs: string | null
  disc_decision_maker: string | null
}

export interface TimelinePage {
  total: number
  limit: number
  offset: number
  entries: TimelineEntry[]
}

export interface CompanyTimelineEntry {
  id: number
  log_date: string
  contact_id: number | null
  contact_name: string | null
  action_type: ActionType
  action_taken: string
  outcome: string | null
  notes: string | null
  direction: Direction | null
  channel: Channel | null
  outreach_type: string | null
  subject: string | null
  source_note?: string | null
}

export interface CompanyTimelinePage {
  total: number
  limit: number
  offset: number
  company_id: number
  company_name: string
  has_data_conflict: boolean
  entries: CompanyTimelineEntry[]
}

// A claim a contact made that contradicts the verified record. Accepting copies
// it onto the company; rejecting keeps the verified value and marks the company.
export interface DataConflict {
  field: string
  label: string
  company_id: number
  company_name: string
  reported_value: string | null
  verified_value: string | null
  reported_at: string | null
  source_entry_id: number | null
  resolution: string | null
}

// A value an email STATED about a company, waiting on Jack. The same decision
// as a DataConflict and rendered by the same panel — both values side by side
// with where the claim came from — but sourced from written correspondence
// rather than a call, so it carries the sentence it came from.
export interface PendingUpdate {
  id: number
  company_id: number
  company_name: string | null
  field: string
  label: string
  proposed_value: string | null
  current_value: string | null
  source_sentence: string | null
  source_entry_id: number | null
  source_entry_date: string | null
  status: string
  created_at: string | null
}

export interface PendingUpdateDigest {
  total: number
  by_company: { company_id: number; company_name: string | null; count: number }[]
  updates: PendingUpdate[]
}

export interface RelationshipLine {
  text: string
  source_entry_id: number | null
  fact_id: number
}

// The thread header's three slots, in render order: where we are, relationship
// context, deal context.
export interface ThreadHeader {
  contact: Contact
  days_in_stage: number | null
  last_touch_date: string | null
  last_touch_channel: Channel | null
  days_of_silence: number | null
  open_loop: string | null
  // Real correspondence only. copied_count is emails this person was merely on
  // the Cc line of; copied_only means every entry on the thread is one of
  // those — "copied, never directly contacted", which is a different kind of
  // person from an active one and has to read that way.
  entry_count: number
  copied_count: number
  copied_only: boolean
  relationship_lines: RelationshipLine[]
  facts: ContactFact[]
  company_name: string | null
  company_business_id: string | null
  company_lease_expiry: string | null
  company_lease_expiry_months: number | null
  company_sf: number | null
  company_submarket: string | null
  company_address: string | null
  // Which company fields came off the signed lease rather than CoStar.
  company_lease_expiry_source: string | null
  company_address_source: string | null
  company_sf_source: string | null
  company_lease_file_name: string | null
  company_lease_uploaded_at: string | null
  company_lease_file_missing: boolean
  has_lease_extraction: boolean
  past_client_reentry: boolean
  has_data_conflict: boolean
  conflicts: DataConflict[]
  pending_updates: PendingUpdate[]
}

// ── Lease document ──────────────────────────────────────────────────────────
// The marker written into a company's *_source column for a value read off the
// signed lease. A lease outranks CoStar.
export const LEASE_SOURCE = 'lease_document'
// The marker for a value Jack typed in the lease review panel (or by hand).
// Also outranks CoStar — but it is his, not the page's, and the UI says so.
export const MANUAL_SOURCE = 'manual'

export interface ExtractedLeaseField {
  field: string
  label: string
  value: string | null
  // The clause the value came from. A value with no source text was never
  // extracted — it comes back found=false rather than inferred.
  source_text: string | null
  page: number | null
  found: boolean
  writes_to_company: boolean
  accepted: boolean
  // What Jack typed over the page (or into a not-found row). `value` above
  // always stays what the document said.
  manual_value: string | null
  // 'lease_document' | 'manual' once confirmed; null before.
  source: string | null
}

// One lease — current or a prior term — with its own file and extraction.
export interface LeaseTerm {
  lease_id: number
  lease_file_name: string | null
  lease_uploaded_at: string | null
  is_current: boolean
  confirmed_at: string | null
  commencement_date: string | null
  expiration_date: string | null
  file_missing: boolean
  has_extraction: boolean
  fields: ExtractedLeaseField[]
}

// The top-level lease fields describe the CURRENT lease; prior_leases holds
// the rest, newest first.
export interface LeaseStatus {
  company_id: number
  company_name: string | null
  lease_id: number | null
  lease_file_name: string | null
  lease_uploaded_at: string | null
  is_current: boolean
  confirmed_at: string | null
  commencement_date: string | null
  expiration_date: string | null
  file_missing: boolean
  has_extraction: boolean
  fields: ExtractedLeaseField[]
  prior_leases: LeaseTerm[]
  // Set when extraction could not run or could not be read. The file is stored
  // and linked either way.
  extraction_error: string | null
  extraction_skipped: boolean
}

// What became of the link and the file when a lease is removed.
export interface LeaseRemovalResult {
  company_id: string
  removed_lease_id: number | null
  removed_file_name: string | null
  // The lease promoted to current when the current one was removed.
  promoted_lease_id: number | null
  promoted_file_name: string | null
  // deleted | absent | refused | error: ... — "absent" means the file was
  // already gone from the folder, which is a clean outcome, not a failure.
  file_outcome: string
  // Set when the fields cleared but the file could not be deleted, so the UI
  // says so rather than implying the document is gone.
  warning: string | null
}

export interface LeaseConfirmResult {
  company_id: number
  lease_id: number | null
  // Only the current lease writes to the company; a prior term fills in its
  // own record.
  is_current: boolean
  // Written to the COMPANY record.
  written: Record<string, string | null>
  sources: Record<string, string>
  saved_to_lease: string[]
  skipped: string[]
  lease_expiry_date: string | null
  lease_expiry_months: number | null
  current_address: string | null
  current_sf_occupied: number | null
  current_submarket: string | null
  submarket_created: boolean
}

// The growing submarket list the dropdowns read from.
export interface Submarket {
  id: number
  name: string
  auto_created: boolean
  created_at: string
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
  adjacent_submarket?: boolean
  lease_expiry_months: number | null
  contact_status: string
  property_is_medical: boolean
  tenant_is_medical: boolean
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
  is_medical: boolean
}

// A tenant Jack placed whose lease has come back into the 6-9 month window.
export interface PastClientReentry {
  contact_id: number
  contact_name: string
  contact_stage: ActivityStage
  closed_at: string | null
  company_id: number | null
  company_business_id: string | null
  company_name: string | null
  submarket: string | null
  lease_expiry_date: string | null
  lease_expiry_months: number | null
  sf_occupied: number | null
  lease_sourced_expiry: boolean
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
  snoozed_tenant_match_actions?: TenantMatchAction[]
  snoozed_acquisition_targets?: AcquisitionTarget[]
  expired_leases: ExpiredLease[]
  past_client_reentries: PastClientReentry[]
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
  /** ANGLE line (reused column — legacy rows hold THE HOOK prose) */
  call_script_hook?: string | null
  /** DATA block of the CALL SHEET (labeled raw values) */
  call_script_data?: string | null
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

// ── Observations (Private Intelligence Layer) ────────────────────────────────
export interface Observation {
  id: number
  entity_type: string
  entity_id: number
  field: string
  value: string | null
  confidence: number | null
  source_doc: string | null
  source_page: number | null
  source_snippet: string | null
  human_verified: boolean
  superseded_by_id: number | null
  created_at: string
  /** Derived, never stored: clean ISO date read out of a hedged value
   *  ("~February 2027" -> "2027-02-28"). Null when nothing needs pinning down. */
  suggested_value?: string | null
  /** How precise the stored text really was: exact | month | quarter | year */
  value_precision?: string | null
}

export interface IntelSignalRef {
  signal_type: string
  value?: string | null
  evidence_observation_id?: number | null
  days_to_expiry?: number
  missing_fields?: string[]
  /** e.g. "activity_log:188" or "acme_lease.pdf" — where the fact came from */
  source_doc?: string | null
  /** Verbatim words behind the card, so it can be judged without leaving the page */
  source_snippet?: string | null
  /** stated_requirement only: which requirement fields the tenant actually stated */
  stated_fields?: string[]
  days_since_touch?: number | null
}

export interface IntelOpportunity {
  id: number
  title: string
  entity_type: string
  entity_id: number
  score: number
  rationale: string | null
  signals: IntelSignalRef[]
  surfaced_at: string
  status: string
}

export type IntelDisposition = 'accepted' | 'rejected' | 'deferred'

/** What a generate run actually scanned — so an empty result is explainable. */
export interface IntelGenerateStats {
  facts_scanned: number
  expirations_found: number
  expirations_unreadable: number
  expirations_past: number
  expirations_beyond_horizon: number
  opportunities: number
  by_signal_type: Record<string, number>
}

export interface IntelGenerateResult {
  opportunities: IntelOpportunity[]
  stats: IntelGenerateStats
}

export interface RequeueDatesResult {
  requeued: number
  unreadable: number
  checked: number
}

export interface IntelHistoryItem extends IntelOpportunity {
  disposition: IntelDisposition | null
  reason_category: string | null
  reason_text: string | null
}

export interface IntelDispositionResult {
  opportunity: IntelOpportunity
  suggested_rule: string | null
}

export interface IntelCriterion {
  id: number
  statement: string
  criterion_type: string | null
  active: boolean
  created_at: string
}

export interface DocumentOut {
  id: number
  filename: string
  storage_path: string
  uploaded_at: string
  entity_type: string | null
  entity_id: number | null
  extraction_status: string
}

export interface ExtractionResult {
  document_id: number
  observations: Observation[]
}

export interface ActivityMineResult {
  processed: number
  facts: number
  skipped: number
  failed: number
}

export interface ActivityMiningStatus {
  total_logs: number
  mined: number
  remaining: number
  facts_extracted: number
  failed: number
}

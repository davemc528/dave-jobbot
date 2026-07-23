export type ExtensionState =
  | "bridge_disconnected"
  | "bridge_connected"
  | "no_job_detected"
  | "job_detected"
  | "job_captured"
  | "analysis_ready"
  | "resume_draft_ready"
  | "resume_needs_review"
  | "resume_approved"
  | "application_page_detected"
  | "human_intervention_required"
  | "stopped_before_submit";

export interface CapturePayload {
  capture_id: string;
  tab_key: string;
  current_url: string;
  canonical_url?: string;
  application_url?: string;
  hostname: string;
  employer?: string;
  job_title?: string;
  location?: string;
  workplace_arrangement?: string;
  requisition_id?: string;
  posting_date?: string;
  compensation?: string;
  responsibilities: string[];
  required_qualifications: string[];
  preferred_qualifications: string[];
  full_text: string;
  ats_type: string;
  extraction_method: string;
  extraction_confidence: number;
  page_title: string;
  json_ld?: Record<string, unknown>;
  captured_at: string;
  requires_human_review?: boolean;
  stabilization_timed_out?: boolean;
}

export interface ExtractionDiagnostics {
  capture_id: string;
  visible_body_text_length: number;
  best_candidate_text_length: number;
  page_title: string;
  url: string;
  hostname: string;
  ats_type: string;
  document_ready_state: string;
  json_ld_block_count: number;
  likely_container_count: number;
  page_still_mutating: boolean;
  selection_present: boolean;
  stabilization_timed_out: boolean;
  attempts: Array<{ method: string; text_length: number; rejection_reason?: string }>;
}

export interface CaptureResult {
  capture_id: string;
  payload?: CapturePayload;
  diagnostics: ExtractionDiagnostics;
  short_content: boolean;
}

export interface FieldInventory {
  identifier: string;
  element_type: string;
  input_type: string;
  name?: string;
  element_id?: string;
  label: string;
  aria_label?: string;
  placeholder?: string;
  nearby_text?: string;
  required: boolean;
  has_value: boolean;
  options: string[];
  section_heading?: string;
  page_url: string;
  frame_identifier: string;
  terminal: boolean;
  sensitive: boolean;
}

export interface FillInstruction {
  field_identifier: string;
  proposed_value?: string;
  verified_application_answer_id?: number;
  confidence?: number;
  decision: "fill" | "withhold";
  withhold_reason?: string;
  human_review_required: boolean;
}

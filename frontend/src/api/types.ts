// Short names for the generated API types. Components import from here, never from
// schema.gen.ts, so a regenerated file changes one import site, not every component.
import type { components } from './schema.gen'

type Schemas = components['schemas']

export type ArchetypeOption = Schemas['ArchetypeOption']
export type DefenseReport = Schemas['DefenseReport']
export type DefenseType = Schemas['DefenseType']
export type ErrorBody = Schemas['ErrorBody']
export type Evaluation = Schemas['Evaluation']
export type HealthResponse = Schemas['HealthResponse']
export type OtherSubtype = Schemas['OtherSubtype']
export type AnswerResponse = Schemas['AnswerResponse']
export type PanelConfig = Schemas['PanelConfig']
export type PanelistOut = Schemas['PanelistOut']
export type PanelistReportSection = Schemas['PanelistReportSection']
export type PanelOptions = Schemas['PanelOptions']
export type PanelRoster = Schemas['PanelRoster']
export type PushbackOutcome = Schemas['PushbackOutcome']
export type QuestionOut = Schemas['QuestionOut']
export type QuotaStatus = Schemas['QuotaStatus']
export type ReportResponse = Schemas['ReportResponse']
export type SessionCreated = Schemas['SessionCreated']
export type SessionState = Schemas['SessionState']
export type Source = Schemas['Source']
export type StartResponse = Schemas['StartResponse']
export type TranscriptTurn = Schemas['TranscriptTurn']

// Which limit a 429 hit, and why an upload failed, as the backend codes them.
export type LimitCode = NonNullable<ErrorBody['code']>
export type IngestErrorCode = NonNullable<SessionState['error_code']>

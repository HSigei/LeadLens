# Kenya Compliance Implementation Notes

This is an engineering control document, not legal advice. A Kenyan advocate and the client's Data Protection Officer must approve the configuration before launch.

## Implemented controls

- The voice flow gives a clear recording/AI-processing notice and requires an affirmative recorded response before recording or AI processing begins.
- Consent evidence records time, method, notice version, tenant, and call identifier. Declined consent transfers to a human without starting recording.
- Tenant configuration must name a lawful basis, approved DPIA, privacy-notice version, and a cross-border safeguard before the number can accept live calls.
- Transcripts sent to the AI analyzer are redacted for common Kenyan mobile numbers, email addresses, national IDs, and SSN-like numbers. Stored transcripts are redacted by default; raw storage needs an explicit controlled exception.
- Every call, report-access request, consent decision, escalation, and deletion is audit logged. Tenant-scoped access is enforced through JWT claims and DynamoDB queries.
- Verified data-subject erasure can delete the call's S3 artifacts and DynamoDB record unless a legal hold is applied. S3 lifecycle and DynamoDB TTL support the approved retention period.

## Kenyan approvals required before launch

- Confirm the client’s controller/processor role and whether registration with the Office of the Data Protection Commissioner is required.
- Obtain a documented Data Protection Impact Assessment for call recording, AI analysis, scoring, and automated recommendations.
- Approve the privacy notice, consent script, purpose limitation, retention timetable, data-subject request verification, breach response, and subcontractor data-processing agreements.
- Because OpenAI processing can involve cross-border transfer, legal counsel must document consent, adequacy, or contractual safeguards as required by the client’s assessment. Set `cross_border_safeguard` only after that approval.
- Confirm the Communications Authority of Kenya requirements and any sector-specific rules that apply to the client, including call-recording notices and marketing/contact suppression obligations.

## Sources to verify with counsel

- Kenya Data Protection Act, 2019.
- Data Protection (General) Regulations, 2021.
- Office of the Data Protection Commissioner guidance and registration requirements.
- Kenya Information and Communications Act and Communications Authority consumer-protection requirements applicable to the client’s sector.
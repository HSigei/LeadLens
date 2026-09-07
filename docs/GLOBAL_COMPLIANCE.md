# Global Compliance Architecture

This platform is jurisdiction-configured, not jurisdiction-assumed. It is not legal advice and every tenant requires local counsel approval before activation.

## Global launch rule

Each phone number maps to exactly one tenant policy. The service refuses live calls unless that policy declares its jurisdiction, processing region, notice version, lawful basis, human escalation number, and cross-border safeguard. Recording consent defaults to required. A country policy may relax that only after documented legal approval.

## Jurisdiction profiles

| Profile | Engineering requirement |
| --- | --- |
| EU / EEA and UK | GDPR/UK GDPR assessment, lawful basis, controller-processor DPA, international-transfer safeguard, DPIA for high-risk processing, subject-right workflow. |
| United States | Per-state recording-consent policy, privacy notice, opt-out/suppression workflow, and state privacy-law assessment. Do not assume one-party consent. |
| Canada / Quebec | PIPEDA and provincial assessment, including Quebec privacy-impact and cross-border requirements where applicable. |
| Brazil | LGPD legal basis, data-subject rights, international transfer assessment, and DPO/contact details. |
| Kenya | Data Protection Act, 2019 / General Regulations assessment, ODPC registration/DPO review where applicable, DPIA, and cross-border safeguards. |
| Other markets | Set `recording_consent_required=true` and require counsel to approve the exact notice, legal basis, transfer safeguard, sector rules, and retention period before activation. |

## Product controls

- Explicit consent evidence includes timestamp and notice version. Declines transfer to a human without recording.
- AI analysis receives PII-redacted transcript text. Raw transcript storage is off by default.
- Access is tenant-scoped, authenticated, role-limited, and audited.
- Erasure requires a verified request and cannot override legal hold. Lifecycle and TTL enforce retention.
- No tenant is activated by code alone: a compliance owner must approve scripts, transfer paths, disclosure language, data processors, and CRM purpose limits.
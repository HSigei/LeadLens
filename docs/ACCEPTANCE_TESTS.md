# Client Acceptance Tests

| Scenario | Expected result |
| --- | --- |
| Recorded inbound call | Caller hears disclosure, recording is dual channel, and the call state is tenant-scoped. |
| Caller asks for human | AI stops the automated flow and dials the tenant escalation number. |
| Caller opts out | AI hands off immediately; client CRM receives the event for suppression processing. |
| Duplicate recording callback | Exactly one processing message and one report are created. |
| PII present | Transcript remains encrypted; OpenAI analysis receives redacted email, phone, and SSN patterns. |
| Bad model response | Worker fails safely, message retries, then reaches DLQ without reporting a fabricated result. |
| Supervisor token from another tenant | API returns no calls or report URL for the target tenant. |
| CRM outage | Worker retries through SQS and no delivery email is sent before CRM synchronization succeeds. |
| Retention test | Lifecycle and TTL controls remove eligible records on the approved schedule. |

Run these against a Twilio subaccount and non-production AWS account before each client launch. Add a load test using a consented synthetic call generator; do not use real customer numbers in performance tests.
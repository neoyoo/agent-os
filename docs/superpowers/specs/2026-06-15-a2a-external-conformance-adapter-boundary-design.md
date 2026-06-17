# A2A External Conformance Adapter Boundary Design

Date: 2026-06-15

## Target Conclusion

SDK self-conformance is not enough for production A2A interoperability. The SDK
should not own running official or vendor-specific conformance suites, but it
should own a stable adapter that imports external suite or CI results into the
same `A2AConformanceReport` evidence model used by agent-os readiness.

## Design

- `A2AExternalConformanceReportImporter` parses JSON text or mapping payloads
  produced by an external conformance runner.
- The canonical input shape is:

```json
{
  "suite": "external-suite-name",
  "version": "suite-version",
  "runId": "ci-run-id",
  "target": "https://agent.example/a2a",
  "checks": [
    {
      "checkId": "operation-message-send",
      "title": "message/send",
      "passed": true,
      "detail": "optional detail",
      "evidence": {"durationMs": 12}
    }
  ]
}
```

- The importer also accepts common aliases (`id`, `name`, `status`,
  `success`, `error`, `message`) so CI adapters can be shallow.
- Imported checks become existing `A2AConformanceFinding` records.
- Imported reports use existing `A2AConformanceReport.passed`,
  `.failed_checks`, `.check_ids`, and `.to_dict()` behavior.
- Report metadata is retained as suite-level fields on `A2AConformanceReport`:
  `source`, `version`, `run_id`, `target`, and `metadata`.
- Invalid JSON, missing checks, non-list checks, and malformed check entries
  raise `A2AExternalConformanceImportError`.

## Non-Goals

- No official conformance runner execution inside the SDK.
- No network calls.
- No certification claim.
- No binding to a single external vendor schema.
- No QueryLoop or AsyncQueryLoop integration.

## Acceptance Criteria

- Importer parses canonical external JSON into `A2AConformanceReport`.
- Importer handles common alias fields and status strings.
- Importer rejects malformed reports with actionable errors.
- Public API exports include the importer and import error.
- Readiness/docs distinguish external conformance result import from official
  suite execution or certification.

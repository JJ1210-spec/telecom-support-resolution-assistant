# Issues and Errors

This log records issues found during the project. Keep the original symptom, attempted methods, final resolution and verification together. Open issues remain visible.

## DATA-001 Duplicate generated complaints — resolved

- **Phase:** 1, synthetic data.
- **Observed:** The first generator pass produced 29 duplicate resolved complaint strings and 5 duplicate unresolved complaint strings.
- **Cause:** A limited set of symptom and context phrases was reused without a case-specific detail.
- **Method tried:** Counted exact `body` strings in each output and inspected the repeated patterns.
- **Resolution:** Added distinct, plausible duration details to indexed complaints and a duplicate assertion to the validator.
- **Verification:** Final generator and validator run passed: all 288 indexed complaint strings are unique.

## DATA-002 Closure evidence absent from resolved records — resolved

- **Phase:** 1, synthetic data.
- **Observed:** Some generated resolution steps ended by escalating or opening an investigation, yet the record was marked resolved.
- **Cause:** The generator reused troubleshooting guidance as if it were a completed historical resolution.
- **Method tried:** Reviewed the resolution steps and status relationship across issue families.
- **Resolution:** Added a closure confirmation step and explicit synthetic closure evidence to every resolved record. Unresolved records keep these fields empty.
- **Verification:** Final validator run passed: all 192 resolved records have steps and closure evidence; all 96 unresolved records have neither.

## DATA-003 Inaccurate edge-case tags — resolved

- **Phase:** 1, synthetic data.
- **Observed:** The generator initially tagged a complaint as containing a typo or abbreviation even when no matching word was changed.
- **Cause:** Tags were appended based on a schedule rather than the mutation result.
- **Method tried:** Compared the generated text with the pre-mutation text.
- **Resolution:** Add a tag only when the corresponding mutation changed the complaint.
- **Verification:** Final generator and validator run passed; spot checks confirmed tags correspond to changed text.

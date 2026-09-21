# Sample review - Movalabs-crew/mova-store#361

Input PR: https://github.com/Movalabs-crew/mova-store/pull/361

## Summary
This PR removes the duplicated image strip from the landing-page slider, renders each configured image once, and switches React keys from array indexes to the image source. It also adds regressions that assert five unique rendered images and no duplicate-key warning.

## Identified risks
- `key={src}` assumes every configured image URL remains unique; a future legitimate repeated URL would reintroduce duplicate React keys.
- The test asserts a fixed total of five `img` roles, so an unrelated future decorative image inside `Slider` could break the regression even if the duplicate-strip bug remains fixed.
- The `console.error` spy is restored only at the end of the test body; an assertion that throws before `mockRestore()` can leak the mock into later tests in the same process.

## Improvement suggestions
- Prefer an explicit stable image identifier if the data model may ever allow repeated URLs, or make the uniqueness invariant obvious where the image list is declared.
- Restore the `console.error` spy in `finally` or through the suite's standard cleanup hook so failing assertions cannot contaminate following tests.

## Confidence
High

---
Reviewed: [Movalabs-crew/mova-store#361](https://github.com/Movalabs-crew/mova-store/pull/361)

# Open Collective: current expense status evidence

A provider expense can have a **historical paid transition** but a **current
non-paid status**. That is not proof of a currently paid contribution. The
Appium expense [#347817](https://opencollective.com/appium/expenses/347817)
demonstrates the failure: paid (September 29), error, incomplete (October 6),
then approved (October 8); as inspected October 9 its top-level banner is
**Approved**, not Paid. [#347815](https://opencollective.com/appium/expenses/347815)
is a separate current **Paid** example. Prior sponsor intake logic must never
promote a search-result snippet or an old activity to current payment proof.

Use the source-level parser in \`concierge.open_collective_status\` with a
**complete, independently captured** visible provider detail-page text. Capture
time must reflect the actual fetch; the tool does not fetch pages itself:

\`\`\`bash
python -m concierge.open_collective_status expense.txt \
  --expense-url https://opencollective.com/appium/expenses/347817 \
  --observed-at 2026-10-09T08:15:00Z
\`\`\`

The JSON includes the current banner status, dated activity transitions in
provider display order, historical paid event count, capture UTC time, canonical
expense ID and SHA-256 of the exact supplied capture. The parser checks the
invoice identity, all major page sections, chronology, and latest dated state;
it holds on missing, mixed, stale or contradictory evidence. Do not supply a
snippet or declare a historical \`paid\` event as a current status.

The output is an **offline assessment of supplied source text**. It does not
authenticate who fetched the page, establish sponsor acceptance, prove our
contributor identity, verify a real transfer to a bank, or substitute for an
authenticated provider account. Downstream payout policy must retain its
independent actual claimant, acceptance and transfer gates.

Focused fixture: \`python -m unittest tests.test_open_collective_status\`.

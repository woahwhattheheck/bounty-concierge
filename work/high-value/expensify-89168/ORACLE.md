# Expensify/App #89168 - keyword-boundary acceptance oracle

Status: independent source/test artifact only. Upstream implementation and bounty ownership remain with the assigned Expensify contributor/C+ lane; this packet is not an Upwork application, assignment request, upstream proposal, payout claim, or customer-data action.

## Pinned source and authority

- Issue: `Expensify/App#89168`, advertised $250, OPEN / External / Reviewing when observed.
- Assigned issue participants observed: `nabi-ebrahimi`, `jasperhuangg`, `truph01`.
- Current upstream source pin used for the donor: `Expensify/App@dd0e8b65546b6e2e8914e74c535da26cd85dacc5`.
- Owner fork source observed: `woahwhattheheck/App main@9f3de0ae2459c3b980fb2518ff9c359ceb6579c7`.
- Owner-fork topology at observation: fork main was 24 commits ahead and 4,887 commits behind upstream pin, merge base `0283d2bebad28796ca74b9506d358232988fe376`.
- Because of that drift, this packet deliberately does **not** merge a source fix into `woahwhattheheck/App/main`. The donor is test-only and pinned to current upstream semantics.

## Accepted current root cause

The Search results input is keyword-only. On submit, `getKeywordQueryWithCurrentSearchContext()` removes old keyword filters from the current context and appends `escapeKeyword(typedText)`.

The current `escapeKeyword()` implementation tokenizes on whitespace and only quotes syntax-looking text when key, operator, and value are already in one token:

```ts
const syntaxRegex = new RegExp(
    `^-?(...keys...|report-?field(-.+)+)[:><=].+$`,
);

function escapeKeyword(keywords: string) {
    return keywords
        .match(/"([^"]*)"|(\S+)/g)
        ?.map((q) => (q.toLowerCase().match(syntaxRegex) ? `"${q}"` : q))
        .join(' ') ?? '';
}
```

That is narrower than the Peggy grammar, which tolerates whitespace around search operators. Therefore:

- `group-by:reports` is one token and is quoted as a keyword.
- `group-by: reports` becomes two tokens and leaks through unquoted.
- The parser can then interpret the leaked text as real query syntax, producing the invalid `groupBy = "reports"`.
- The same boundary defect applies to shapes such as `from: bob`, `amount > 100`, and `category : travel`.

This packet treats the keyword-input boundary as the primary invariant. Normalizing invalid parsed `groupBy` values can be useful defense-in-depth, but it does not replace quoting syntax-shaped text entered into a keyword-only field.

## Acceptance contract

For a keyword-only input, syntax-shaped user text must remain keyword text even when whitespace separates the key, operator, and value. Existing non-keyword filters in the current search context must survive.

Required vectors are in `vectors.json`; the high-value core is:

| typed keyword input | required escaped keyword payload |
|---|---|
| `group-by: reports` | `"group-by: reports"` |
| `group-by:reports` | `"group-by:reports"` |
| `group-by : reports` | `"group-by : reports"` |
| `amount > 100` | `"amount > 100"` |
| `from: bob` | `"from: bob"` |
| `category : travel` | `"category : travel"` |
| `group-by:` | `"group-by:"` |
| `coffee group-by: card` | `coffee "group-by: card"` |
| `from: bob category:x` | `"from: bob" "category:x"` |
| `"group-by: reports"` | unchanged |
| `hello world` | unchanged |
| `foo:bar` | unchanged; `foo` is not a search key |
| `TYPE:expense` | `"TYPE:expense"` |
| `-type:expense` | `"-type:expense"` |
| `report-field-trip-id: 42` | `"report-field-trip-id: 42"` |

Context preservation must also hold. Given current query context `type:expense from:21462841` and typed keyword input `group-by: reports`, the submitted query must retain `type:expense` and `from:21462841` while containing the quoted keyword `"group-by: reports"`. It must not materialize `groupBy = "reports"`.

## Executable reference

`escape_keyword_oracle.mjs` is a dependency-free reference implementation of the boundary contract. `escape_keyword_oracle.test.mjs` contains 16 deterministic cases. It was executed with:

```sh
node --test escape_keyword_oracle.test.mjs
```

Result at packet construction: **16 passed / 0 failed**.

This is a host reference oracle, not execution of Expensify's application test suite. The current application implementation's failure for whitespace-separated syntax is established by the pinned source structure above; the donor patch intentionally adds tests that should fail until the assigned source implementation closes the gap.

## Test-only donor

`DONOR.patch` is a minimal regression donor against the pinned upstream test file `tests/unit/Search/SearchQueryUtilsTest.ts`. It does **not** claim or prescribe the assigned source implementation.

The donor adds three properties:

1. whitespace-separated syntax is escaped as one keyword span;
2. existing `from` context survives the exact reported `group-by: reports` input;
3. adjacent syntax expressions are quoted independently rather than swallowed into one phrase.

The assigned implementation can consume these tests directly or translate them into the current branch's preferred style.

## Merge / payout fence

Passing this packet means only that the keyword-boundary contract is coherent and independently executable. It does **not** establish:

- upstream issue assignment;
- an accepted Expensify proposal or PR;
- Upwork hiring or payout eligibility;
- production/browser reproduction;
- customer-data validation;
- final sponsor acceptance.

Before upstream merge, re-run the application Jest target on the exact implementation head and repeat the normal Expensify CI/review path.

import assert from 'node:assert/strict';
import test from 'node:test';

import {escapeKeywordReference} from './escape_keyword_oracle.mjs';

const syntaxKeys = new Set(['group-by', 'amount', 'from', 'category', 'type']);

const cases = [
    ['group-by: reports', '"group-by: reports"'],
    ['group-by:reports', '"group-by:reports"'],
    ['group-by : reports', '"group-by : reports"'],
    ['amount > 100', '"amount > 100"'],
    ['from: bob', '"from: bob"'],
    ['category : travel', '"category : travel"'],
    ['group-by:', '"group-by:"'],
    ['coffee group-by: card', 'coffee "group-by: card"'],
    ['from: bob category:x', '"from: bob" "category:x"'],
    ['"group-by: reports"', '"group-by: reports"'],
    ['hello world', 'hello world'],
    ['foo:bar', 'foo:bar'],
    ['TYPE:expense', '"TYPE:expense"'],
    ['-type:expense', '"-type:expense"'],
    ['report-field-trip-id: 42', '"report-field-trip-id: 42"'],
    ['from: "Bob Smith"', '"from: \\"Bob Smith\\""'],
];

for (const [input, expected] of cases) {
    test(input, () => {
        assert.equal(escapeKeywordReference(input, syntaxKeys), expected);
    });
}

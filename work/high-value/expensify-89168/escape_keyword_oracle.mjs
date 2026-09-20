export function escapeKeywordReference(keywords, syntaxKeys) {
    const keyAlternation = [...syntaxKeys]
        .sort((a, b) => b.length - a.length)
        .map((key) => key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
        .join('|');

    const syntaxKeyPattern = `-?(?:${keyAlternation}|report-?field(?:-.+)+)`;
    const directSyntaxRegex = new RegExp(`^${syntaxKeyPattern}[:><=]=?.+$`, 'i');
    const keyOnlyRegex = new RegExp(`^${syntaxKeyPattern}$`, 'i');
    const keyOperatorRegex = new RegExp(`^(${syntaxKeyPattern})([:><=]=?)(.*)$`, 'i');
    const operatorRegex = /^([:><=]=?)(.*)$/;
    const tokens = keywords.match(/"(?:[^"\\]|\\.)*"|\S+/g) ?? [];

    const quote = (value) => `"${value.replaceAll('\\', '\\\\').replaceAll('"', '\\"')}"`;
    const isQuoted = (value) => value.length >= 2 && value.startsWith('"') && value.endsWith('"');

    const escaped = [];
    for (let index = 0; index < tokens.length; index += 1) {
        const token = tokens[index];

        if (isQuoted(token)) {
            escaped.push(token);
            continue;
        }

        if (directSyntaxRegex.test(token)) {
            escaped.push(quote(token));
            continue;
        }

        const joinedOperator = token.match(keyOperatorRegex);
        if (joinedOperator) {
            const inlineValue = joinedOperator[3];
            if (inlineValue) {
                escaped.push(quote(token));
                continue;
            }

            const nextToken = tokens[index + 1];
            if (nextToken === undefined || keyOnlyRegex.test(nextToken)) {
                escaped.push(quote(token));
                continue;
            }

            escaped.push(quote(`${token} ${nextToken}`));
            index += 1;
            continue;
        }

        if (keyOnlyRegex.test(token)) {
            const nextToken = tokens[index + 1];
            const operatorMatch = nextToken?.match(operatorRegex);
            if (operatorMatch) {
                let span = `${token} ${nextToken}`;
                index += 1;

                if (!operatorMatch[2] && tokens[index + 1] !== undefined) {
                    span += ` ${tokens[index + 1]}`;
                    index += 1;
                }

                escaped.push(quote(span));
                continue;
            }
        }

        escaped.push(token);
    }

    return escaped.join(' ');
}

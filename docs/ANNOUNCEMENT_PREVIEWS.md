# Bounty announcement previews

`concierge.announcer.format_announcement(rows)` produces the existing `short`, `medium` and `long` strings. It does not fetch providers or post anything. An empty supplied list returns three empty strings.

The existing `concierge announce --dry-run` caller still fetches bounty data, maps its `reward_rtc` field to `rtc`, and prints the formats. That caller already supplied the correct reward; the mapping was not broken. The formatter also accepts raw index-shaped rows now, without requiring a second adapter. Do not describe the CLI's dry run as offline: only the formatter itself is network-free.

## Input and reward meaning

Every row needs a string `title`. The source link `url`, `difficulty`, `labels` and reward may be supplied. Both legacy `rtc` and index `reward_rtc` are supported. When both reward fields exist they must agree; an explicit unknown in one and an amount in the other is a conflict, not permission to choose the more favorable figure.

Missing amounts remain `unknown`, explicit zero stays zero, and finite nonnegative numeric values retain their value in display. The shared README formatter handles literal table text and numeric formatting; it is not a second reward extractor. Input order remains unchanged, so the caller retains selection and ranking behavior.

Each format labels the entries as candidates rather than newly available work. Medium and long previews explain that indexed amounts may be campaign pools, caps or estimates. No fixed RTC-to-USD equivalence is inserted. A preview does not establish expiry, assignment, eligible claimant, sponsor acceptance or payment setup. Use the existing work thread and actual sponsor terms for those facts; no new approval gate is introduced.

## Short-format links

The short format reserves space for the complete source link and indexed amount before shortening the title. A long title therefore cannot cut a link in half. If metadata alone exceeds the 280-character budget, the amount is omitted from the short version first. A link that still cannot fit is replaced by an explicit instruction to use the full preview; the complete source link remains in medium/long output.

The bound is **280 Python characters**, not a claim of compatibility with any provider's weighted-length or URL-shortening rules. No platform API was called to establish provider acceptance.

## Publication remains separate

`post_announcement(platform, content, platform_config)` and its existing handlers retain their behavior. The CLI preview does not call them. No automatic posting, retry, claim, wallet action, provider credential, new destination or scheduled job was added. Formatting a candidate is not authorization to advertise or submit it.

This source repair adds no test suite, fixtures, receipt archive, dependency or workflow. See [README bounty publication](README_BOUNTY_PUBLICATION.md) for the adjacent cached-index publication path. A merged source change is not evidence of a refreshed or published external announcement.

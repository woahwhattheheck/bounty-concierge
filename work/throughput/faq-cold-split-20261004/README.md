# Cold FAQ paragraph splitting — 4 October 2026

## Shipped change

Product commit: `c2c465b2bff75fee586e4b6b91846c4ec24a6d32`.

`concierge/_faq_doc_index.py` now yields slices from the existing blank-line separator matches rather than constructing `re.split`'s entire paragraph list. The caller still reads one complete Markdown file. This is not a streaming file reader or a file-size limit. Matching, tokenization, paragraph order, short-paragraph filtering, cache generation checks, four-entry limit and 8 MiB retained-cache budget are unchanged.

The synthetic large corpus is 100,000 paragraphs / 7,099,998 Markdown bytes. Its retained paragraph/token objects exceed the cache budget even though its input file is smaller than 8 MiB. Avoiding the eager list reduces temporary allocations and lets an early-stopping consumer return without splitting every paragraph.

## Source identity and execution

Python 3.13.5, GCC 14.2.0, Linux. Before source blob: `66cad740873ea70581d7d29330a2d150c130ce6c`; after: `53d357bf28373989d561e73ff46b8a0bc2bdd96e`. Full production modules were imported without source rewriting or import stubs. The supplied tokenizer is `re.findall(r"\w+", value.lower())`.

The retained `measure.py` is the exact executed driver. It includes file reads, tokenization and streaming output-digest calculation. Timing samples are three alternating before/after pairs; memory is a separate untimed `tracemalloc` run per variant. These are local component measurements, not whole-command latency, process RSS, network/quota savings or fleet-wide throughput.

| Workload | Before median ms | After median ms | Before peak traced bytes | After peak traced bytes |
|---|---:|---:|---:|---:|
| Cold 100 paragraphs, 7,098 bytes | 1.774971 | 1.650077 | 140,668 | 140,346 |
| Cold complete 100,000 paragraphs | 826.064494 | 841.839260 | 26,332,614 | 15,229,803 |
| Cold first paragraph, same large file | 21.009911 | 3.192741 | 18,906,465 | 14,207,015 |

**Tradeoff:** the complete scan was approximately 1.9% slower in this run while using approximately 42.2% less peak traced allocation. First-result time was approximately 6.58 times faster. The small timings are noisy; no universal speedup is asserted. Existing warm-cache reuse remains separate from these cold measurements.

Raw timing samples, milliseconds:

```json
{
  "small_cold": {
    "before": [2.707881, 1.435213, 1.774971],
    "after": [1.650077, 1.874657, 1.393162]
  },
  "oversized_complete": {
    "before": [887.992885, 826.064494, 810.603296],
    "after": [819.310565, 841.83926, 858.089238]
  },
  "oversized_first_row": {
    "before": [21.009911, 20.40275, 24.558695],
    "after": [4.594418, 3.192741, 2.969528]
  }
}
```

Returned row counts and streaming SHA-256 digests were identical before/after:

- Small: 100 / `b6174fdbcafd85680c5c2a08fe599ce50c258c0e089babc35d0393dabf8aed5f`.
- Complete large: 100000 / `569176218ceb3142d8778ad3878fedb96587a145f9a4cabe7dac2ca808cc923b`.
- Early stop: 1 / `8e4661bcecab03bd0600cba41c4dfb31294edaf7d920bfea7369e38de577761f`.

Eleven small compatibility inputs agreed on cold and warm reads: empty content, short filtered paragraphs, leading/trailing separators, CRLF, tabs, Unicode whitespace, and repeated blank lines. Three successive changed/added/deleted-file transitions also agreed. Early close did not populate the cache. No broad test suite, provider request, new dependency or workflow was introduced or executed.

## Reproduce from a checkout containing the product commit

```sh
git show c2c465b2bff75fee586e4b6b91846c4ec24a6d32^:concierge/_faq_doc_index.py > /tmp/faq-before.py
git show c2c465b2bff75fee586e4b6b91846c4ec24a6d32:concierge/_faq_doc_index.py > /tmp/faq-after.py
python work/throughput/faq-cold-split-20261004/measure.py /tmp/faq-before.py /tmp/faq-after.py
```

The driver prints source blob hashes and complete results. Reuse this evidence for the unchanged component rather than launching another acceptance worker. Normal corpus invalidation remains active; there is no migration or new command to adopt.

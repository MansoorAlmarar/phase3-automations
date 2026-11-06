# AI notes guardrail eval

Offline. The model reply is canned per case; this scores the code that checks it (`python/ai_notes.py`).

- Bad replies handled correctly: **22/22**
- Good replies kept intact: **6/6**

| Category | Passed | Cases |
|---|---|---|
| advice wording | 6/6 | advice-headline, advice-verdict, advice-risk, advice-condition, advice-target, advice-news |
| invented number | 4/4 | num-invented-price, num-invented-pct, num-invented-risk, num-invented-condition |
| policy (no Halal comment) | 2/2 | halal-claim, halal-risk |
| prompt injection | 2/2 | injection-echo, injection-fake-id |
| bad ids and shapes | 8/8 | shape-news-negative-id, shape-news-big-id, shape-news-text-id, shape-news-is-object, shape-risks-is-string, shape-conditions-is-string, shape-fundamentals-is-list, shape-reply-is-list |
| good reply (must be kept) | 6/6 | good-grounded-headline, good-meaning-notes, good-small-counts-and-years, good-valid-news, good-risk-kept, good-em-dash |

## Failures: none

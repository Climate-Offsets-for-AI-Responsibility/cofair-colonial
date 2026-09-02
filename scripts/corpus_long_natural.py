#!/usr/bin/env python3
"""The frozen long-context document behind task F.

Task D, the suite's other long task, is `"Policy review context sentence. " * 800`.
That makes it useful for exactly one thing — long-context billing behaviour, where
the payload only needs to be big — and useless for the thing the fitted content
rate needs, because a phrase repeated 800 times presents a tokenizer with one
merge decision amortized over 25,743 characters. Its lexical variety is 0.007, and
the rate it sets is the cost of re-merging that phrase rather than of tokenizing
text (D77).

This document exists to give the fit a long span of text that vocabularies can
genuinely disagree about, so it is deliberately heterogeneous: varied sentence
length, mixed register, proper nouns, dates in more than one format, decimals,
percentages, currency, hyphenation, an identifier-heavy code fragment, units, and
a handful of rare words. Those are the places BPE merge tables diverge.

It is original text written for this purpose, so there is no licence question, and
it is **frozen**: `test_task_corpus.py` pins its SHA-256 and its length. Editing it
is a corpus change that breaks comparability for task F and requires a
`CORPUS_VERSION` bump, exactly like editing any other prompt. Do not "improve" the
prose — its only job is to stay identical.
"""
from __future__ import annotations

LONG_NATURAL_CONTEXT = """\
The Harbor Ridge Regional Compact was chartered on 14 March 2019 to coordinate \
technology procurement across nine municipalities, four school districts, and a \
consortium of eleven community health clinics. Its founding brief was unglamorous: \
negotiate shared rates, publish what was paid, and retire the practice of each \
member signing its own contract in isolation. For six years that mandate covered \
photocopiers, network transit, and a student information system. In late 2024 the \
compact's oversight board extended it to machine-learning services, and the \
extension turned out to be considerably harder than anyone had budgeted for.

The difficulty was not price. Published rates for language-model inference were, \
if anything, easier to compare than the tiered licensing the compact had spent a \
decade untangling. The difficulty was that the unit being priced kept moving. A \
member could sign at $3.00 per million input tokens, hold that rate for a year, \
and still watch its monthly invoice climb, because the number of tokens the same \
document consumed had changed underneath the contract. Nobody had misrepresented \
anything. The rate card was accurate; the quantity was not a constant.

Clinic staff in Eastbrook noticed it first. A triage summarizer that had cost the \
clinic roughly $412 a month through the spring returned an invoice of $631 in \
August, against a caseload that had grown by four percent. The vendor's support \
desk confirmed there had been no change to the rate and suggested the clinic was \
simply busier. The clinic's operations lead, Maribel Okonjo-Vance, was unconvinced, \
and did something the compact had never thought to require: she kept the input text \
byte-identical for a week and logged the token counts the provider reported back.

They moved. Not by much, and not every day, but the same 1,840-character intake \
note that had been billed at 402 prompt tokens in June was billed at 511 in \
August — a 27% increase on an unchanged input, at an unchanged rate. When she \
raised it, the vendor explained, accurately and without apparent embarrassment, \
that the model behind the endpoint had been updated and the new model used a \
different tokenizer. The endpoint name had not changed. The rate had not changed. \
The contract said nothing whatsoever about tokenization.

This is the gap the compact now spends most of its time on. A price is a promise \
about dollars per unit; it is silent about how many units a given piece of work \
will require. Where the unit is a kilowatt-hour or a ream of paper, that silence is \
harmless, because the unit is defined outside the vendor's control. Where the unit \
is a token, the vendor defines it, revises it without notice, and bills against the \
revision. Two providers quoting an identical rate can therefore charge materially \
different amounts for identical work, and a single provider can raise an effective \
price without touching a published number.

Quantifying the spread required a fixed corpus and a boring, repetitive discipline. \
The compact assembled five documents it considered representative — a short \
eligibility question, a grant abstract of about eight hundred characters, a \
technical specification containing code, a very long appendix, and a transcript of \
a service call — and submitted them unchanged, on a daily schedule, to every model \
its members had contracted for. It recorded what came back and changed nothing \
else. The methodology is unremarkable. Its value lies entirely in the refusal to \
vary the input.

The first surprise was the size of the disagreement. On the grant abstract, the \
most efficient vocabulary in the panel encoded roughly 5.5 characters per token; \
the least efficient managed about 2.5. That is a 2.2-fold difference in billable \
quantity for the same paragraph, before any discussion of rates. A member \
comparing two providers on published price alone, both quoting the same figure per \
million tokens, would have been choosing between bills that differed by more than \
a factor of two.

The second surprise was that the disagreement did not respect vendor boundaries. \
Two models from the same vendor, released fourteen months apart, differed from each \
other by 48% on ordinary English prose — a larger gap than separated either of them \
from several competitors. Whatever intuition suggests that a company standardizes \
its tokenizer across a product family, the measurements did not support it. Nor was \
the ratio noisy: it reproduced to three decimal places every day for ten \
consecutive days, which is the signature of a deterministic encoding rather than of \
load, sampling, or billing error.

The third surprise was procedural rather than technical. Roughly a fifth of what \
the compact was being billed for on short requests had nothing to do with the text \
submitted at all. Every provider prepends material to a request — a chat template, \
a system preamble, an injected tool schema — and that material is billed. It is \
also invisible, and it changes. One model in the panel added 430 tokens to every \
request on a single day in August, with no announcement and no rate change. On the \
long appendix that was a 10% increase. On the short eligibility question, which ran \
to 157 characters, it was an increase of 181%.

That asymmetry matters more than its size suggests, because it falls hardest on the \
cheapest work. A member sending thousands of very short requests — an eligibility \
checker, a form validator, a routing classifier — pays the overhead thousands of \
times and the content cost almost never. Overhead drift is therefore regressive: it \
penalizes exactly the high-volume, low-value automation that a public-sector budget \
is most likely to rely on, and it is invisible in any per-request average that \
mixes long and short work together.

Separating the two turned out to be straightforward arithmetic, once the compact \
stopped dividing. Because the corpus spans a wide range of lengths, the counts for \
a single provider on a single day fit a line: total tokens equal a fixed cost per \
request plus a marginal cost per character. The intercept is the scaffolding, the \
slope is the tokenizer, and the two drift for entirely different reasons. A change \
in the intercept with a stable slope is a wrapper revision. A change in the slope is \
a re-tokenization. Reported as a single blended figure, the two are \
indistinguishable, and the compact spent most of 2025 chasing the wrong one.

There is a subtlety in the fit that cost the compact several months. The line is \
only as trustworthy as the range of lengths supporting it, and the long appendix — \
which supplied nearly all of that range — consisted of one sentence repeated \
several hundred times. Repetition is cheap to encode, and cheap in a way that \
depends on whether a particular vocabulary happens to contain a merge for that \
particular phrase. The slope it produced was stable, reproducible, and almost \
identical across the whole panel, which read as reassuring agreement and was in \
fact an artifact of the filler. Replacing the appendix with heterogeneous prose \
changed the fitted slope for most of the panel and restored the spread the short \
documents had shown all along.

The compact's current guidance to its members is short. Ask for the rate, then ask \
what the rate is multiplied by. Log the token counts your provider reports, against \
inputs you have not changed, because that log is the only record that survives an \
endpoint being repointed at a new model. Treat a stable published price as \
necessary and not sufficient. And when a vendor tells you the price has not \
changed, believe them, and then check the quantity, because both statements can be \
true at once and only one of them appears on the invoice.

Members occasionally ask whether this is worth the trouble for organizations \
spending a few hundred dollars a month. The compact's answer is that the \
measurement is cheap and the exposure compounds. Running the fixed corpus against \
fourteen models costs the compact about $130 a year, which is less than a single \
member's monthly spend on the smallest contracted service. The alternative is \
budgeting against a unit that the counterparty may redefine, which is a governance \
posture the compact would not accept for electricity, floor space, or bandwidth, \
and which it has decided not to accept here either.

Appendix C records the instrumentation. Counts are collected once daily, in a \
single scheduled batch, and stored append-only:

    def daily_snapshot(models, corpus, *, run_date):
        rows = []
        for model in models:
            for task_id, prompt in sorted(corpus.items()):
                usage = count_prompt_tokens(model, prompt)
                rows.append({
                    "run_date": run_date,
                    "model_id": model.identifier,
                    "task_id": task_id,
                    "input_chars": len(prompt),
                    "tokens_in": usage.prompt_tokens,
                    "api_model": usage.resolved_model,
                })
        return rows

Two invariants are enforced on write. A row records the model the provider actually \
served, not the alias that was requested, because an alias repointed mid-quarter is \
the failure mode the log exists to catch. And no row is ever amended: a correction \
is a new row, so that a figure quoted in a budget memo in February can still be \
reproduced in November. Both rules were adopted after the compact discovered it \
could no longer reconstruct the numbers behind its own 2024 forecast.
"""

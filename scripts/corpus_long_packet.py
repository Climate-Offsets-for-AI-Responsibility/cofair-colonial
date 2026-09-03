#!/usr/bin/env python3
"""The frozen long-context packet behind task D.

Task D used to be `"Policy review context sentence. " * 800`. That gave it the
length a long-context billing probe needs and nothing else: a phrase repeated 800
times presents a tokenizer with one merge decision amortized over 25,743
characters, so its lexical variety was 0.007 and the marginal rate it implied was
the cost of re-merging that phrase rather than of tokenizing text. It was
excluded from the fit for that reason (D77) and has now been replaced outright.

This packet keeps D's two jobs and drops the artifact. It is the same order of
magnitude of text (~25K characters, still the longest task, so it still sets the
top of the character span and still exercises whatever a provider does with a
large request), and it is deliberately heterogeneous: nine sections in different
registers — minutes, a rate table, an incident write-up, an email thread, a code
appendix, vendor correspondence, a glossary, audit findings, a closing memo —
with proper nouns, dates in several formats, decimals, currency, percentages,
units, identifiers, and hyphenation. Those are the places BPE merge tables
diverge.

It also keeps the *needle*. D's label is "long-context needle" and the question
asks for a single fact — the surcharge threshold on one named contract — that
appears exactly once, deep in section 6. That makes D a retrieval probe as well
as a billing probe, which the repeated-sentence version never was.

Frozen, like task F's document: `test_task_corpus.py` pins the SHA-256 and the
length. Editing the prose is a corpus change that breaks comparability for task D
and requires a `CORPUS_VERSION` bump. Do not tidy it — its only job is to stay
identical.
"""
from __future__ import annotations

LONG_CONTEXT_PACKET = """\
SECTION 1 — MINUTES OF THE JOINT TECHNOLOGY OVERSIGHT BOARD

Harbor Ridge Regional Compact, forty-third convened session, 11 February 2026, \
Council Annex B, Westmarch. Called to order 09:14 by Chair Delphine Aturu-Reyes. \
Present: nine municipal delegates, three district superintendents or their \
designees, two clinic administrators, the compact treasurer, and counsel. Absent \
with notice: the delegate for Little Fenwick, who joined by telephone for items \
four through seven.

The chair opened by noting that the session had been moved forward a fortnight at \
the treasurer's request. The compact's machine-learning services line had closed \
the prior quarter at $71,480 against an approved allocation of $58,000, and under \
the compact's own charter any overrun above twenty percent obliges the board to \
convene before the next disbursement rather than after it. Counsel confirmed the \
threshold had been crossed on 29 January and that the meeting satisfied the \
obligation.

Item one, the treasurer's reconciliation. Treasurer Ansel Bright-Kovacs \
distributed a four-page schedule and asked the board to note what it did not \
show. There had been no change to any contracted rate during the quarter. Not \
one of the eleven active service agreements had been renegotiated, repriced, \
amended, or renewed. Every published figure the compact had used to build the \
$58,000 allocation was still the figure in force on the day he presented. The \
overrun was therefore not a pricing event in any sense the board had language \
for, and he wished the minutes to record that plainly before the discussion \
turned to causes.

What had changed, he continued, was the quantity being billed. The compact pays \
for language-model inference by the token, and a token is not a unit the compact \
defines, observes, or controls. It is defined by the vendor, revised at the \
vendor's discretion, and applied to the vendor's own count of the compact's \
traffic. Over the quarter the same documents, submitted by the same programs, \
against the same endpoints, had been counted differently. He offered the \
Eastbrook triage summarizer as the cleanest instance available: byte-identical \
intake notes, held constant for a week in June and again in November, billed at \
402 prompt tokens and then at 511. An increase of 27.1 percent on an input that \
had not been touched.

Delegate Marchetti asked whether that constituted a breach. Counsel's answer, \
recorded at her request in full, was that it did not. The agreements warrant a \
price per million tokens and are silent on tokenization. No vendor had \
represented that the encoding would remain stable, because no vendor had been \
asked to. The compact had procured on the assumption that the unit was fixed, in \
the way a kilowatt-hour or a ream of twenty-pound bond is fixed, and that \
assumption had been imported into eleven contracts without ever being written \
into one of them.

Item two, the superintendent for Calder Vale asked what the district was \
supposed to tell its own finance committee in March. The chair's reply was that \
the compact could not yet answer the question and that the purpose of items \
three and four was to build the instrument that would. Discussion of the March \
reporting deadline was deferred by unanimous consent.

Item three, the proposed measurement programme. The chair recognised Maribel \
Okonjo-Vance, operations lead at the Eastbrook clinic, who had assembled the June \
and November comparison on her own initiative and had been asked to present a \
generalisation of it. Her proposal was procedurally dull and the board approved \
it in eleven minutes. Freeze a small set of documents. Submit them unchanged, on \
a schedule, to every model any member has contracted for. Record what the vendor \
says it counted. Change nothing else, ever, and keep the record append-only so \
that a figure quoted in a budget memo in one quarter can still be reproduced two \
years later.

She was explicit about the one discipline that makes the exercise worth anything, \
and the board asked that it be minuted verbatim: the value of the measurement \
lies entirely in the refusal to vary the input. Any improvement to the documents, \
any tidying of the prompts, any substitution of a better example destroys \
comparability with every prior observation and cannot be undone afterwards. \
Members were asked to treat the frozen corpus as they would treat a calibration \
weight.

Item four, scope and cost. Fourteen model configurations were named as being in \
scope, covering seven vendors at two service tiers each. Annualised cost of the \
programme was estimated at $132 on the vendors' own published rates, which the \
treasurer observed was less than the compact spends monthly on its smallest \
contracted service and approximately one nine-hundredth of the quarter's \
overrun. Approved without dissent.

Item five, the long-context question, was raised by the delegate for Northgate \
and is taken up in section 6 of this packet.

Items six and seven concerned the photocopier framework and are omitted here.

Adjourned 11:47. Minutes prepared by the clerk, circulated 13 February 2026, \
adopted as circulated at the forty-fourth session.

SECTION 2 — RATE SCHEDULE IN FORCE, QUARTER ENDING 31 JANUARY 2026

The following are the published rates the compact contracted against, in United \
States dollars per million tokens, input and output respectively. They are \
reproduced to show that none of them moved during the period under review.

  Vendor A, premium tier ......... 15.00 / 75.00
  Vendor A, economy tier .......... 0.80 /  4.00
  Vendor B, premium tier .......... 3.00 / 15.00
  Vendor B, economy tier .......... 0.25 /  1.25
  Vendor C, premium tier .......... 2.50 / 10.00
  Vendor C, economy tier .......... 0.15 /  0.60
  Vendor D, premium tier .......... 5.00 / 25.00
  Vendor D, economy tier .......... 0.60 /  2.40
  Vendor E, premium tier .......... 1.25 /  5.00
  Vendor E, economy tier .......... 0.10 /  0.40
  Vendor F, premium tier .......... 8.00 / 24.00
  Vendor F, economy tier .......... 0.35 /  1.40
  Vendor G, premium tier .......... 4.40 / 17.60
  Vendor G, economy tier .......... 0.20 /  0.80

Three notes attach to the schedule and the treasurer asked that they be read as \
part of it rather than as commentary.

First, the spread between the most and least expensive premium tier is a factor \
of twelve on input. That is the number procurement officers compare, and it is \
the only number most of them see.

Second, a rate is a promise about dollars per unit and says nothing about how \
many units a given piece of work requires. Where the unit is defined outside the \
vendor's control that silence is harmless. Where the vendor defines the unit, the \
silence is the entire exposure, and two vendors quoting an identical rate can \
bill materially different amounts for identical work.

Third, several agreements carry a long-context provision under which requests \
above a stated size are billed at an elevated rate, sometimes double. The \
provision is ordinary and defensible — very large requests genuinely cost more to \
serve — but the stated size differs from agreement to agreement, is expressed in \
tokens rather than in characters or pages, and therefore moves when tokenization \
moves. A programme that sat comfortably below a threshold in June can cross it in \
November without adding a word.

SECTION 3 — INCIDENT WRITE-UP HR-2026-0114

Classification: billing anomaly, no service degradation, no data exposure. \
Opened 14 January 2026 at 08:52 by the duty analyst, Osric Pemberton-Naidu. \
Closed 22 January. Root cause established. No vendor fault found.

Summary. Between 06:00 and 06:40 on 14 January the compact's shared inference \
gateway recorded a step increase in reported prompt tokens across every request \
routed to one economy-tier model. The increase was uniform in absolute terms \
rather than proportional: 430 tokens were added to each request irrespective of \
its size. Throughput, latency and error rates were unaffected. No deployment had \
been made on the compact's side in the preceding eleven days.

Detection. The step was not detected by the gateway's own alerting, which \
thresholds on cost per request and was tuned loosely enough that a 430-token \
addition to a large request fell inside normal variation. It was detected by the \
frozen-corpus job approved at the forty-third session, which submits documents \
of several different sizes and had by then been running for nine days. Because \
the addition was constant, its proportional effect varied with document length, \
and that pattern is what identified it: the short eligibility question, 157 \
characters, rose by 181 percent, while the long packet rose by roughly ten \
percent. A proportional change in tokenization cannot produce that signature. A \
constant addition to every request can produce nothing else.

Cause. The vendor confirmed on 19 January that a system preamble served with the \
model had been revised, adding tool-schema material to every request whether or \
not the caller used tools. The revision was intentional, was considered an \
internal implementation detail, was not announced, and did not change any \
published rate. The vendor's position, which the analyst records without \
objection, was that prepended material has always been billed and that no term \
of the agreement had been altered.

Impact. Assessed at $1,180 across the quarter, concentrated almost entirely in \
two high-volume, low-value programmes: a form validator and a routing \
classifier, each issuing tens of thousands of very short requests per day. Both \
pay the fixed addition on every call and the content cost almost never. The \
analyst notes the distributional point for the board's attention: overhead drift \
is regressive. It falls hardest on the cheapest work, which is exactly the \
automation a public-sector budget is most likely to depend on, and it is \
invisible in any per-request average that mixes long and short traffic.

Remediation. None available from the vendor, none sought. Two changes were made \
to the compact's own instrumentation. The frozen corpus was extended to span a \
wider range of document lengths, on the reasoning that the anomaly was only \
legible because two very different sizes were measured on the same day. And \
reporting was changed to separate the two components rather than publish a \
blended figure, as described in section 5.

Lessons recorded. A vendor telling you the price has not changed and your \
invoice rising are not in contradiction, and only one of those two statements \
appears on the invoice.

SECTION 4 — CORRESPONDENCE, EXTRACT

From: m.okonjo-vance@eastbrook-clinic.example
To: a.bright-kovacs@harborridge.example
Cc: o.pemberton-naidu@harborridge.example
Date: Tuesday, 27 January 2026, 16:31
Subject: Re: Re: your November figures — one thing I got wrong

Ansel,

Two corrections and a request.

The first correction is mine. When I sent the June and November comparison in \
December I described the change as the model getting more verbose. That was \
sloppy and it sent Osric down the wrong path for a week. Verbosity would show up \
in output tokens. What moved was prompt tokens, on inputs I had frozen myself, \
which cannot be verbosity and can only be the encoding. I should have said \
re-tokenization and I did not have the vocabulary at the time.

The second correction concerns the direction of the effect. I had assumed the \
newer model in a vendor's family would encode more efficiently, because that is \
the direction everything else moves. It is not reliably true. On ordinary English \
prose the premium model from one of our vendors encodes at roughly 300 tokens per \
thousand characters and its economy sibling at roughly 214 — the expensive model \
consuming forty percent more units of the thing you are billed for, at six times \
the rate per unit, on identical text. Two models from the same vendor, released \
fourteen months apart, differed from each other by 48 percent, which is a larger \
gap than separated either of them from several competitors. Whatever intuition \
says a company standardises its tokenizer across a product family, our \
measurements do not support it.

I should add the finding that surprised me most, because it cuts the other way \
and I do not want the board hearing only the alarming half. Four of our seven \
vendors return byte-identical prompt counts across their two tiers. Not close — \
identical, to the token, on every document, every day. Those families genuinely \
share one vocabulary, and for them the tier choice is a pure price decision with \
no hidden quantity effect at all. Osric's first instinct was that we were calling \
the same model twice and mislabelling it, which was the right instinct: we had \
made exactly that error with one vendor whose model identifier is resolved at \
call time, and its premium tier was measured against its economy model for a \
month before anyone caught it. The identifier the vendor reports back is now \
checked against the tier we asked for on every single row, because the token \
counts cannot tell those two situations apart and the identifier can.

The request. Please do not let the March paper report a single blended \
efficiency number. A blended figure moves for two unrelated reasons and tells \
the reader nothing about which one it was. We have the data to separate them and \
it would be a waste to average it back together.

Maribel

From: a.bright-kovacs@harborridge.example
Date: Tuesday, 27 January 2026, 18:02

Noted on all three, and the separation is already in the draft — Osric worked out \
that we get it almost for free, see the appendix. One question I could not answer \
when the Northgate delegate asked it: do we know our own long-context threshold \
on that contract? I could not find it in the schedule.

SECTION 5 — TECHNICAL APPENDIX: SEPARATING OVERHEAD FROM ENCODING

The compact reports two quantities where it previously reported one. The method \
is ordinary least squares on four points and requires no tooling beyond what the \
collection job already produces.

Observe that every request bills a constant plus a variable part. The constant is \
scaffolding the vendor prepends — chat template, system preamble, injected tool \
schemas — and it is billed, invisible, and revisable, as incident HR-2026-0114 \
demonstrated. The variable part is the text the caller actually sent, converted \
to tokens by the vendor's vocabulary. Dividing total tokens by total characters \
blends the two and yields a figure that drifts for either reason.

Because the frozen corpus spans a wide range of document lengths, the counts for \
one model on one day fit a line. Total tokens equal a fixed cost per request plus \
a marginal cost per character. The intercept is the scaffolding; the slope is the \
vocabulary. A change in the intercept with a stable slope is a wrapper revision. \
A change in the slope is a re-tokenization. Reported as one blended number the \
two are indistinguishable, and the compact spent most of the preceding year \
chasing the wrong one.

Two conditions on the fit, both learned the hard way. It needs at least three \
documents, and it needs them to differ in length by a wide margin — a narrow \
span produces an intercept and a slope that trade off against each other, and \
the reported values then move by a few percent for reasons that are pure \
conditioning rather than anything the vendor did.

The second condition is subtler and cost the compact several months. The line is \
only as trustworthy as the text supporting it. The original long document in the \
corpus consisted of one sentence repeated several hundred times, chosen because \
a long-context probe only needs the payload to be big. Repetition is cheap to \
encode, and cheap in a way that depends on whether a vocabulary happens to hold a \
merge for that particular phrase. The slope it produced was stable, reproducible \
to three decimal places, and nearly identical across the whole panel — which read \
as reassuring agreement between vendors and was in fact an artifact of the \
filler. Thirteen of fourteen configurations reported the same figure to within \
0.2, on vocabularies that demonstrably differ by half. Replacing that document \
with heterogeneous prose changed the fitted slope for most of the panel and \
restored the spread the short documents had been showing all along.

Collection runs once daily in a single scheduled batch and is stored \
append-only:

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
                    "tier_requested": model.tier,
                })
        return rows

Three invariants are enforced on write. A row records the model the vendor \
actually served, not the alias that was requested, because an alias repointed \
mid-quarter is the failure the log exists to catch. Where a vendor offers two \
tiers, the served identifiers for those tiers are compared and a run in which \
they match is rejected outright rather than published. And no row is ever \
amended: a correction is a new row, so that a number quoted in February can \
still be reproduced in November. All three were adopted after the compact found \
it could no longer reconstruct the figures behind its own prior-year forecast.

SECTION 6 — LONG-CONTEXT PROVISIONS BY AGREEMENT

The delegate for Northgate raised this at item five and the clerk was asked to \
set out the position across all eleven agreements, since the provisions were \
negotiated separately and at different times and no consolidated statement of \
them existed.

Nine of the eleven agreements carry some form of elevated charge for large \
requests. The mechanism is consistent in form: above a stated request size, \
input tokens bill at a multiple of the standard rate, most commonly two. The \
stated size is where they diverge, and the divergence is wide. The Calder Vale \
instructional-support agreement sets it at 128,000 tokens. The two clinic \
agreements at Eastbrook and Fenwick Cross both set it at 200,000. The compact's \
own shared-gateway agreement sets no threshold at all and instead prices large \
requests through a separate committed-throughput schedule that the treasurer \
described as impossible to forecast against and has asked counsel to revisit at \
renewal.

The Northgate ambulatory services agreement, contract number NG-4471-B, sets \
its long-context surcharge threshold at 192,000 tokens, and the delegate's \
concern was specific rather than general. Northgate's discharge-summary \
programme runs at a mean request size of 171,000 tokens with a long right tail. \
On the encoding in force when the agreement was signed in August 2024 that left \
what the delegate's finance office regarded as adequate headroom. Two \
re-tokenizations later the same clinical documents encode approximately eleven \
percent larger, the mean sits near 190,000, and roughly a third of daily traffic \
now crosses a threshold that nothing in the programme's own behaviour moved \
toward. Northgate has not changed its templates, its patient volume, or its \
retention window.

Counsel's view is that the provision operates as written and that the compact \
has no claim. The clerk records the delegate's closing remark for the minutes: \
the threshold is denominated in a unit the counterparty defines, so the \
counterparty can move the compact across a cliff edge in its own contract \
without amending the contract or changing a published price, and can do so \
without any intention of having done it.

The board directed that every threshold-bearing agreement be restated in \
characters at the current measured encoding, republished quarterly as the \
encoding moves, and flagged when measured headroom falls below fifteen percent.

SECTION 7 — GLOSSARY AS ADOPTED

Blended efficiency. Total tokens divided by total characters, without \
separating the fixed and variable parts. Deprecated by the board on 11 February \
2026 for reporting purposes; retained only for reconciliation against \
pre-programme figures.

Content rate. The fitted marginal cost in tokens per thousand characters of the \
text the caller actually sent, excluding anything the vendor prepends. Moves \
only on a re-tokenization. Withheld rather than published when no document in \
the corpus has sufficient lexical variety to support it.

Fixed request overhead. The fitted intercept. Scaffolding billed on every \
request regardless of its content. Ordinarily between four and fifteen tokens; \
observed as high as 638 on one configuration.

Frozen corpus. The documents submitted unchanged on every collection. Frozen \
means byte-identical, verified by digest, not merely intended to be stable.

Lexical variety. The share of a document's words that are distinct. A tokenizer \
comparison requires text whose vocabulary the vendors can genuinely disagree \
about; a document below the adopted floor is excluded from the fit however long \
it is.

Needle retrieval. Whether a stated fact placed once in a large document is \
returned correctly. Measured alongside billing behaviour on the same request, \
since both are properties of how a vendor handles a large payload.

Re-tokenization. A change in the vendor's vocabulary, changing the number of \
billable units a fixed input consumes. Not a price change and not announced as \
one.

Served identifier. The model the vendor reports having used, as distinct from \
the alias requested. Recorded on every row.

SECTION 8 — INTERNAL AUDIT, FINDINGS AND MANAGEMENT RESPONSE

Fieldwork 2 to 6 February 2026. Four findings, two of which management accepts \
in full.

Finding 8.1, moderate. The compact procured a variable quantity as though it \
were fixed. Eleven agreements warrant a price per million tokens; none defines \
the token, warrants stability of the encoding, or requires notice of a change to \
it. Management accepts. Counsel has been instructed to draft a notice-of-encoding \
-change clause for the next renewal cycle and to obtain external comparables, \
with the caveat that the compact's leverage on a $71,000 annual spend is limited \
and a vendor is unlikely to accept the clause.

Finding 8.2, moderate. Alerting thresholds on the shared gateway were tuned to \
cost per request and would not have detected the January anomaly. It was \
detected instead by a $132-per-year measurement programme approved for an \
unrelated purpose eleven days earlier. Management accepts and has rebuilt \
alerting on tokens per character of submitted input, evaluated separately for \
short and long traffic.

Finding 8.3, low, disputed. Audit observes that the compact publishes six \
derived measures on its internal dashboard where three would serve, and that \
several are meaningful only to the two staff who built the collection. \
Management's response, recorded in full at the treasurer's request: the derived \
measures exist because a blended figure concealed the January incident for six \
weeks, and separating them is what made it legible in a morning. But audit's \
point stands as to audience. A measure that names the instrument rather than the \
exposure belongs in the appendix and not on the summary page, and the March \
paper will report what a member is billed and hold the fitted decomposition \
behind it for the reader who asks why the billed figure moved.

Finding 8.4, low. No consolidated register of long-context thresholds existed \
before section 6 of this packet was prepared, notwithstanding that nine \
agreements contain one and that at least one member was operating within eleven \
percent of its threshold without knowing the figure. Management accepts and the \
register is now maintained by the clerk.

SECTION 9 — CLOSING MEMORANDUM TO MEMBER FINANCE OFFICERS

The compact's guidance to members is four sentences long and the board declined \
to lengthen it.

Ask for the rate, then ask what the rate is multiplied by. Log the token counts \
your vendor reports, against inputs you have not changed, because that log is \
the only record that survives an endpoint being repointed at a new model. Treat \
a stable published price as necessary and not sufficient. And when a vendor \
tells you the price has not changed, believe them, and then check the quantity, \
because both statements can be true at once.

Members spending a few hundred dollars a month occasionally ask whether this is \
proportionate. The board's answer is that the measurement is cheap and the \
exposure compounds. Running the frozen corpus against fourteen configurations \
costs $132 a year. The alternative is budgeting against a unit the counterparty \
may redefine, which is a posture the compact would not accept for electricity, \
floor space, or bandwidth, and has now decided not to accept here.

Circulated 24 February 2026 over the signature of the chair. Queries to the \
clerk in the first instance.
"""

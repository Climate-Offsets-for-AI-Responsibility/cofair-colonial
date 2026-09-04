import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  basisChangeFlags,
  computeSeriesBreaks,
  drawerObservedColumns,
  formatSuiteBaselineAwaitingStatus,
  scrubDisplayName,
  isFutureSchedulePlaceholder,
  effectiveModality,
  prettyModality,
  prettyContext,
  formatLegendLabel,
  contextsNeedDisambiguation,
  formatEstimatedSpend,
  formatCostDelta,
  parseOptionalNumber,
  resolveCostDetailRequestState,
  priceAtOrBefore,
  sortDatasetsForDate,
} from "./labels.js";

describe("scrubDisplayName", () => {
  it("strips scheduled starting/through bylines and keeps status parens", () => {
    assert.equal(
      scrubDisplayName("Claude Sonnet 5 starting September 1, 2026"),
      "Claude Sonnet 5",
    );
    assert.equal(
      scrubDisplayName("Claude Sonnet 5 through August 31, 2026"),
      "Claude Sonnet 5",
    );
    assert.equal(
      scrubDisplayName("Claude Opus 4 (retired, except on Google Cloud)"),
      "Claude Opus 4 (retired, except on Google Cloud)",
    );
  });
});

describe("isFutureSchedulePlaceholder", () => {
  it("flags starting-window SKUs and keeps the current through-window", () => {
    assert.equal(
      isFutureSchedulePlaceholder({
        model_id: "claude-sonnet-5-starting-september-1-2026",
        display_name: "Claude Sonnet 5 starting September 1, 2026",
      }),
      true,
    );
    assert.equal(
      isFutureSchedulePlaceholder({
        model_id: "claude-sonnet-5-through-august-31-2026",
        display_name: "Claude Sonnet 5 through August 31, 2026",
      }),
      false,
    );
  });
});

describe("effectiveModality", () => {
  it("treats missing modality as text", () => {
    assert.equal(effectiveModality(null), "text");
    assert.equal(effectiveModality(undefined), "text");
    assert.equal(effectiveModality("audio"), "audio");
  });
});

describe("formatLegendLabel", () => {
  it("omits the provider and adds modality when set", () => {
    assert.equal(
      formatLegendLabel(
        { display_name: "Gemini 2.5 Flash", modality: "audio", context_window: ">200k" },
        { showContext: false },
      ),
      "Gemini 2.5 Flash (audio)",
    );
  });

  it("adds context only when it disambiguates", () => {
    assert.equal(
      formatLegendLabel(
        { display_name: "gpt-5.6-sol", modality: null, context_window: "short_context" },
        { showContext: true },
      ),
      "gpt-5.6-sol (short context)",
    );
    assert.equal(
      formatLegendLabel(
        { display_name: "gpt-5.6-sol", modality: null, context_window: "short_context" },
        { showContext: false },
      ),
      "gpt-5.6-sol",
    );
  });

  it("does not add a text paren for untagged rows", () => {
    assert.equal(
      formatLegendLabel(
        { display_name: "Claude Fable 5", modality: null },
        { showContext: false },
      ),
      "Claude Fable 5",
    );
  });
});

describe("pretty helpers", () => {
  it("humanizes modality and context tokens", () => {
    assert.equal(prettyModality("multimodal"), "multimodal");
    assert.equal(prettyContext("long_context"), "long context");
    assert.equal(prettyContext(">200k"), ">200k");
  });
});

describe("contextsNeedDisambiguation", () => {
  it("is true when one model has more than one context window", () => {
    const rows = [
      { provider_id: "openai", model_id: "gpt-5.6-sol", context_window: "short_context" },
      { provider_id: "openai", model_id: "gpt-5.6-sol", context_window: "long_context" },
      { provider_id: "google", model_id: "gemini-2.5-flash", context_window: ">200k" },
    ];
    const need = contextsNeedDisambiguation(rows);
    assert.equal(need.has("openai|gpt-5.6-sol"), true);
    assert.equal(need.has("google|gemini-2.5-flash"), false);
  });
});

describe("priceAtOrBefore / sortDatasetsForDate", () => {
  const a = {
    pricingId: "a",
    data: [
      { x: "2026-04-01", y: 10 },
      { x: "2026-06-01", y: 4 },
    ],
  };
  const b = {
    pricingId: "b",
    data: [{ x: "2026-05-01", y: 8 }],
  };
  const c = {
    pricingId: "c",
    data: [{ x: "2026-07-01", y: 20 }],
  };

  it("carries the last known price forward", () => {
    assert.equal(priceAtOrBefore(a.data, "2026-05-15"), 10);
    assert.equal(priceAtOrBefore(a.data, "2026-06-01"), 4);
    assert.equal(priceAtOrBefore(c.data, "2026-05-01"), null);
  });

  it("sorts by price at the hovered date; unseen series sink", () => {
    const ordered = sortDatasetsForDate([a, b, c], "2026-05-15").map((d) => d.pricingId);
    assert.deepEqual(ordered, ["a", "b", "c"]);
  });
});

describe("cost formatters", () => {
  it("formatEstimatedSpend adapts precision", () => {
    assert.equal(formatEstimatedSpend(2.345), "$2.35");
    assert.equal(formatEstimatedSpend(0.23456), "$0.2346");
    assert.equal(formatEstimatedSpend(0.00123456), "$0.001235");
  });

  it("formatEstimatedSpend returns em dash for missing values", () => {
    assert.equal(formatEstimatedSpend(null), "—");
    assert.equal(formatEstimatedSpend(undefined), "—");
    assert.equal(formatEstimatedSpend(""), "—");
  });

  it("formatEstimatedSpend is locale-stable en-US and keeps exact zero", () => {
    assert.equal(formatEstimatedSpend(0), "$0.00");
    assert.equal(formatEstimatedSpend("1234.5"), "$1,234.50");
  });

  it("parseOptionalNumber rejects empty and nullable values", () => {
    assert.equal(parseOptionalNumber(null), null);
    assert.equal(parseOptionalNumber(undefined), null);
    assert.equal(parseOptionalNumber(""), null);
    assert.equal(parseOptionalNumber("  "), null);
    assert.equal(parseOptionalNumber("0"), 0);
  });

  it("formatCostDelta never invents a comparison", () => {
    assert.equal(
      formatCostDelta({ status: "comparison_unavailable" }),
      "Comparison unavailable",
    );
    assert.equal(
      formatCostDelta({ status: "ok", delta_pct: 8.2, delta_usd: 0.0142 }),
      "Increase · +8.2% · +$0.0142",
    );
  });

  it("formatCostDelta handles negative, zero, and baseline states", () => {
    assert.equal(
      formatCostDelta({ status: "ok", delta_pct: -2.4, delta_usd: -0.0142 }),
      "Decrease · -2.4% · -$0.0142",
    );
    assert.equal(
      formatCostDelta({ status: "ok", delta_pct: 0, delta_usd: 0 }),
      "No change · $0.00",
    );
    assert.equal(
      formatCostDelta({ status: "new_baseline", reason: "no_comparable_prior_period" }),
      "New baseline",
    );
  });
});

describe("cost detail request state", () => {
  it("marks cached selections as not loading", () => {
    assert.deepEqual(
      resolveCostDetailRequestState({ hasCachedDetail: true, hasPath: true }),
      { shouldFetch: false, loading: false, error: null },
    );
  });

  it("flags missing detail path as explicit error", () => {
    assert.deepEqual(
      resolveCostDetailRequestState({ hasCachedDetail: false, hasPath: false }),
      {
        shouldFetch: false,
        loading: false,
        error: "No detail path published for this run date.",
      },
    );
  });

  it("regression: A to B to cached A is never loading", () => {
    const stateA = resolveCostDetailRequestState({ hasCachedDetail: false, hasPath: true });
    const stateB = resolveCostDetailRequestState({ hasCachedDetail: false, hasPath: true });
    const backToCachedA = resolveCostDetailRequestState({ hasCachedDetail: true, hasPath: true });
    assert.equal(stateA.loading, true);
    assert.equal(stateB.loading, true);
    assert.equal(backToCachedA.loading, false);
  });
});

describe("suite baseline awaiting status", () => {
  it("surfaces missing canonical task E rows after epoch", () => {
    assert.equal(
      formatSuiteBaselineAwaitingStatus({
        pack: "suiteLong",
        metricSource: "meter",
        dashboardStartDate: "2026-09-03",
        today: "2026-09-04",
        latestCompleteDate: null,
        latestRunDate: "2026-09-04",
        chatCorpusVersion: "3.0.0",
        panelRows: [
          { provider_id: "openai", tier: "flagship" },
          { provider_id: "openai", tier: "workhorse" },
        ],
        latestRunRows: [
          { provider_id: "openai", tier: "flagship", task_id: "A", run_status: "ok" },
          { provider_id: "openai", tier: "workhorse", task_id: "A", run_status: "ok" },
          {
            provider_id: "openai",
            tier: "flagship",
            task_id: "E",
            run_status: "ok",
            chat_corpus_version: "2.0.0",
            canonical: true,
          },
        ],
      }),
      "Complete A-F baseline is still awaiting collection: 2 panel rows are missing canonical task E rows for chat corpus v3.0.0 on the latest run date.",
    );
  });
});

describe("series breaks", () => {
  const dayAt = (date, model, extra = {}) => ({ date, model_basis: model, ...extra });

  it("breaks the line where a provider re-pins its model", () => {
    const { breakAt, newModelAt } = computeSeriesBreaks([
      dayAt("2026-09-01", "grok-4.5"),
      dayAt("2026-09-02", "grok-4.5"),
      dayAt("2026-09-03", "grok-4.6"),
      dayAt("2026-09-04", "grok-4.6"),
    ]);
    assert.deepEqual(newModelAt, [false, false, true, false]);
    assert.deepEqual(breakAt, [false, false, true, false]);
  });

  it("breaks flagship and workhorse independently on their own versions", () => {
    const flagship = computeSeriesBreaks([
      dayAt("2026-09-01", "claude-opus-4"),
      dayAt("2026-09-02", "claude-opus-5"),
    ]);
    const workhorse = computeSeriesBreaks([
      dayAt("2026-09-01", "claude-haiku-4-5"),
      dayAt("2026-09-02", "claude-haiku-4-5"),
    ]);
    assert.deepEqual(flagship.breakAt, [false, true]);
    assert.deepEqual(workhorse.breakAt, [false, false]);
  });

  it("treats a day that spans two model versions as its own identity", () => {
    // The regression this fixes: the aggregate used to report whichever task's
    // row was summed first, so the mixed day looked like a plain continuation.
    const { breakAt } = computeSeriesBreaks([
      dayAt("2026-09-02", "gemini-flash-latest"),
      dayAt("2026-09-03", "gemini-flash-latest,gemini-flash-latest-high-res-exp"),
      dayAt("2026-09-04", "gemini-flash-latest-high-res-exp"),
    ]);
    assert.deepEqual(breakAt, [false, true, true]);
  });

  it("falls back to api_model then model_id when no basis is recorded", () => {
    const { modelAt } = computeSeriesBreaks([
      { api_model: "gpt-5.6-luna" },
      { model_id: "chat-latest" },
    ]);
    assert.deepEqual(modelAt, ["gpt-5.6-luna", "chat-latest"]);
  });

  it("breaks on a changed fit basis and on replaced prompts", () => {
    const fit = computeSeriesBreaks([
      { model_basis: "m", fit_basis: "A,B,C,D" },
      { model_basis: "m", fit_basis: "A,B,C,F" },
    ]);
    const corpus = computeSeriesBreaks([
      { model_basis: "m", corpus_basis: "1.0.0" },
      { model_basis: "m", corpus_basis: "2.0.0" },
    ]);
    assert.deepEqual(fit.newBasisAt, [false, true]);
    assert.deepEqual(fit.breakAt, [false, true]);
    assert.deepEqual(corpus.newCorpusAt, [false, true]);
    assert.deepEqual(corpus.breakAt, [false, true]);
  });

  it("keeps the line intact when a derived basis goes missing", () => {
    // A point that failed to record its own basis cannot assert a change; only
    // the model is allowed to break a line on a transition to blank.
    const { breakAt } = computeSeriesBreaks([
      { model_basis: "m", fit_basis: "A,B,C", corpus_basis: "2.0.0" },
      { model_basis: "m", fit_basis: "", corpus_basis: "" },
    ]);
    assert.deepEqual(breakAt, [false, false]);
  });

  it("never breaks the first point, and tolerates empty input", () => {
    assert.deepEqual(computeSeriesBreaks([dayAt("2026-09-01", "m")]).breakAt, [false]);
    assert.deepEqual(computeSeriesBreaks([]).breakAt, []);
    assert.deepEqual(computeSeriesBreaks(undefined).breakAt, []);
  });

  it("flags a transition to or from an unrecorded model", () => {
    assert.deepEqual(basisChangeFlags(["", "grok-4.6"]), [false, true]);
    assert.deepEqual(basisChangeFlags(["grok-4.6", ""]), [false, true]);
    assert.deepEqual(basisChangeFlags(["", ""]), [false, false]);
  });
});

describe("drawer observed columns", () => {
  it("uses aggregate totals for task E", () => {
    assert.deepEqual(
      drawerObservedColumns("E").map((col) => col.key),
      ["tokens_in", "tokens_out", "tokens_total"],
    );
  });

  it("keeps density columns for non-conversation tasks", () => {
    assert.deepEqual(
      drawerObservedColumns("A").map((col) => col.key),
      ["tokens_in", "tokens_out", "tokens_in_per_1k_chars"],
    );
  });
});

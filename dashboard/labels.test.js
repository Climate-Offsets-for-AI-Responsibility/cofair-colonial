import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
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
  COST_DATE_PAGE_SIZE,
  costDatesForPage,
  nextCostDateVisibleCount,
  costDatesToFetch,
  defaultExpandedCostDates,
  costTaskRowsForDate,
  costDayTotals,
  costCollapsedTotals,
  defaultFromDate,
  rewindFromDate,
  canRewindFromDate,
  canLoadEarlierDates,
  dateRangeWidenedBackward,
  nextDatePagingState,
  resolveRecentDateRange,
  ledgerDatesNewestFirst,
  ledgerRowsForDate,
  ledgerDayTotals,
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

describe("cost table date paging", () => {
  const dates = [
    "2026-09-08",
    "2026-09-07",
    "2026-09-06",
    "2026-09-05",
    "2026-09-04",
    "2026-09-03",
    "2026-09-02",
    "2026-09-01",
  ];

  it("reveals one page of newest dates and leaves the rest for later", () => {
    assert.equal(COST_DATE_PAGE_SIZE, 7);
    assert.deepEqual(costDatesForPage(dates, COST_DATE_PAGE_SIZE), dates.slice(0, 7));
    assert.equal(nextCostDateVisibleCount(7, dates.length), 8);
    assert.deepEqual(costDatesForPage(dates, 8), dates);
  });

  it("does not fetch a collapsed date, even when that date is on the page", () => {
    assert.deepEqual(defaultExpandedCostDates(dates), ["2026-09-08"]);
    assert.deepEqual(
      costDatesToFetch(costDatesForPage(dates, 7), new Set(["2026-09-08"])),
      ["2026-09-08"],
    );
  });
});

describe("cost rows grouped by date", () => {
  const detail = {
    provider_tiers: [
      {
        provider_id: "openai",
        tier: "flagship",
        tasks: [
          { task_id: "A", input_cost_usd: 0.1, output_cost_usd: 0.2, supporting_cost_usd: 0.05, estimated_spend_usd: 0.35 },
        ],
      },
      {
        provider_id: "anthropic",
        tier: "workhorse",
        tasks: [
          { task_id: "B", input_cost_usd: 0.01, output_cost_usd: 0.02, supporting_cost_usd: 0, estimated_spend_usd: 0.03 },
        ],
      },
    ],
  };

  it("keeps only visible provider-tiers and sums the day's filtered totals", () => {
    const rows = costTaskRowsForDate("2026-09-08", detail, (providerId) => providerId === "openai");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].task.task_id, "A");
    assert.deepEqual(costDayTotals(rows), {
      input: 0.1,
      output: 0.2,
      supporting: 0.05,
      total: 0.35,
      count: 1,
    });
  });
});

describe("collapsed cost totals", () => {
  it("preloads input/output/supporting from the index, falling back to daily", () => {
    assert.deepEqual(
      costCollapsedTotals(
        {
          date: "2026-09-08",
          estimated_spend_usd: 1.15,
          input_cost_usd: 0.3,
          output_cost_usd: 0.65,
          supporting_cost_usd: 0.2,
        },
        null,
      ),
      { input: 0.3, output: 0.65, supporting: 0.2, total: 1.15, count: 0 },
    );
    assert.deepEqual(
      costCollapsedTotals(
        { date: "2026-09-08", estimated_spend_usd: 1.15 },
        { input_cost_usd: 0.3, output_cost_usd: 0.65, supporting_cost_usd: 0.2 },
      ),
      { input: 0.3, output: 0.65, supporting: 0.2, total: 1.15, count: 0 },
    );
  });
});

describe("recent table date window", () => {
  const oldestFirst = [
    "2026-09-01",
    "2026-09-02",
    "2026-09-03",
    "2026-09-04",
    "2026-09-05",
    "2026-09-06",
    "2026-09-07",
    "2026-09-08",
  ];

  it("defaults From to a week back when enough published days exist", () => {
    assert.equal(defaultFromDate(oldestFirst), "2026-09-02");
    assert.deepEqual(resolveRecentDateRange("", "", oldestFirst), {
      from: "2026-09-02",
      to: "2026-09-08",
    });
  });

  it("uses the earliest day when fewer than a week of dates exist", () => {
    assert.equal(defaultFromDate(["2026-09-07", "2026-09-08"]), "2026-09-07");
  });

  it("rewinds From by another week so the reader can walk further back", () => {
    assert.equal(rewindFromDate("2026-09-02", oldestFirst), "2026-09-01");
    assert.equal(canRewindFromDate("2026-09-02", oldestFirst), true);
    assert.equal(canRewindFromDate("2026-09-01", oldestFirst), false);
    assert.equal(
      canLoadEarlierDates({
        shownCount: 7,
        rangeCount: 7,
        from: "2026-09-02",
        optionsOldestFirst: oldestFirst,
      }),
      true,
    );
    assert.equal(
      canLoadEarlierDates({
        shownCount: 8,
        rangeCount: 8,
        from: "2026-09-01",
        optionsOldestFirst: oldestFirst,
      }),
      false,
    );
  });

  it("keeps the newest day expanded when From widens backward", () => {
    const next = nextDatePagingState({
      prevKey: "2026-09-02|2026-09-08",
      from: "2026-09-01",
      to: "2026-09-08",
      visibleCount: 7,
      expandedDates: new Set(["2026-09-08"]),
      datesNewestFirst: [...oldestFirst].reverse(),
    });
    assert.equal(dateRangeWidenedBackward("2026-09-02|2026-09-08", "2026-09-01", "2026-09-08"), true);
    assert.equal(next.rangeKey, "2026-09-01|2026-09-08");
    assert.equal(next.visibleCount, 7);
    assert.deepEqual([...next.expandedDates], ["2026-09-08"]);
  });
});

describe("ledger rows grouped by date", () => {
  const rows = [
    { date: "2026-09-08", tokens_in: 100, input_chars: 200, task_id: "A" },
    { date: "2026-09-08", tokens_in: 50, input_chars: 100, task_id: "B" },
    { date: "2026-09-07", tokens_in: 10, input_chars: 40, task_id: "A" },
  ];

  it("orders dates newest first and sums prompt tokens plus density on the collapsed row", () => {
    assert.deepEqual(ledgerDatesNewestFirst(rows), ["2026-09-08", "2026-09-07"]);
    assert.equal(ledgerRowsForDate(rows, "2026-09-08").length, 2);
    assert.deepEqual(ledgerDayTotals(ledgerRowsForDate(rows, "2026-09-08")), {
      tokensIn: 150,
      density: 500,
      count: 2,
    });
  });
});

const dashDir = dirname(fileURLToPath(import.meta.url));

describe("dashboard chrome", () => {
  it("keeps From and To in one date-range group so they wrap together", () => {
    const html = readFileSync(join(dashDir, "tokens/index.html"), "utf8");
    assert.match(html, /class="date-range"[\s\S]*id="trendFrom"[\s\S]*id="trendTo"/);
    assert.match(html, /class="date-range"[\s\S]*id="ledgerFrom"[\s\S]*id="ledgerTo"/);
    assert.match(html, /class="date-range"[\s\S]*id="costFrom"[\s\S]*id="costTo"/);
  });

  it("titles /pricing Model Pricing Tracker", () => {
    const html = readFileSync(join(dashDir, "index.html"), "utf8");
    assert.match(html, /<title>Model Pricing Tracker/);
    assert.match(html, />Model Pricing Tracker</);
    assert.equal(html.includes("Platform Pricing Tracker"), false);
  });

  it("keeps the header tagline visible without scroll-reveal", () => {
    for (const file of ["index.html", "tokens/index.html"]) {
      const html = readFileSync(join(dashDir, file), "utf8");
      assert.match(html, /class="cofair-site-header__tagline">Climate Offsets for AI Responsibility/);
      assert.equal(html.includes("cofair-site-header__tagline cofair-site-header__reveal"), false);
    }
  });

  it("uses the shared Data Centers in America footer label", () => {
    for (const file of ["index.html", "tokens/index.html"]) {
      const html = readFileSync(join(dashDir, file), "utf8");
      assert.match(html, />Data Centers in America<\/a>/);
      assert.equal(html.includes(">Datacenter Ledger</a>"), false);
    }
  });
});

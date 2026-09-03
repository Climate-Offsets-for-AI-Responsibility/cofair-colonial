import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
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
  splitLeafCostColumns,
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
      "+8.2% · +$0.0142",
    );
  });

  it("formatCostDelta handles negative, zero, and baseline states", () => {
    assert.equal(
      formatCostDelta({ status: "ok", delta_pct: -2.4, delta_usd: -0.0142 }),
      "-2.4% · -$0.0142",
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

describe("cost leaf splits", () => {
  it("splits meter leaves into input/output only", () => {
    assert.deepEqual(
      splitLeafCostColumns({
        source: "meter",
        input_cost_usd: 0.0012,
        output_cost_usd: 0.0024,
        estimated_cost_usd: 0.0036,
      }),
      { input: 0.0012, output: 0.0024, supporting: null, total: 0.0036 },
    );
  });

  it("splits ledger leaves into supporting only", () => {
    assert.deepEqual(
      splitLeafCostColumns({
        source: "ledger",
        input_cost_usd: 99,
        output_cost_usd: 99,
        estimated_cost_usd: 0.0008,
      }),
      { input: null, output: null, supporting: 0.0008, total: 0.0008 },
    );
  });

  it("keeps missing leaf totals as null", () => {
    assert.deepEqual(
      splitLeafCostColumns({
        source: "meter",
        input_cost_usd: null,
        output_cost_usd: undefined,
        estimated_cost_usd: "",
      }),
      { input: null, output: null, supporting: null, total: null },
    );
  });
});

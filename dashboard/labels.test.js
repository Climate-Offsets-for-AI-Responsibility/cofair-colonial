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

import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { cycleProviderMode, providerVisible } from "./provider-mode.js";

describe("provider-mode", () => {
  it("isolates, adds a second provider, then hides one and restores the rest", () => {
    const modes = new Map();
    cycleProviderMode(modes, "openai");
    assert.deepEqual([...modes.entries()], [["openai", "solo"]]);
    assert.equal(providerVisible(modes, "openai"), true);
    assert.equal(providerVisible(modes, "anthropic"), false);

    cycleProviderMode(modes, "anthropic");
    assert.equal(providerVisible(modes, "openai"), true);
    assert.equal(providerVisible(modes, "anthropic"), true);

    cycleProviderMode(modes, "openai");
    assert.equal(providerVisible(modes, "openai"), false);
    assert.equal(providerVisible(modes, "anthropic"), true);
    assert.equal(modes.get("openai"), "hidden");

    cycleProviderMode(modes, "openai");
    assert.equal(modes.size, 0);
    assert.equal(providerVisible(modes, "openai"), true);
    assert.equal(providerVisible(modes, "anthropic"), true);
  });
});

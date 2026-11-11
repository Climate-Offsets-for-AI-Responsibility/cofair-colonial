import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

for (const page of ["dashboard/index.html", "dashboard/tokens/index.html"]) {
  test(`${page} consumes shared public chrome`, () => {
    const html = readFileSync(page, "utf8");
    for (const id of ["instrument-header", "instrument-waitlist", "instrument-footer"]) {
      assert.match(html, new RegExp(`id="${id}"`));
    }
    assert.match(html, /src="https:\/\/cofair\.org\/instrument-chrome\.js"/);
    assert.doesNotMatch(html, /signupCard|Receive insights and updates/);
    assert.doesNotMatch(html, /class="cofair-site-header/);
    assert.doesNotMatch(html, /class="cofair-site-footer/);
    assert.ok(html.indexOf('id="instrument-waitlist"') < html.indexOf("Latest News"));
    assert.ok(html.indexOf("</main>") < html.indexOf('id="instrument-waitlist"'));
  });
}

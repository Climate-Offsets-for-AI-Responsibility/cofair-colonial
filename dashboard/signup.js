// Insights signup, shared by /pricing and /tokens.
//
// Markup lives in each page's HTML (`#signupCard`), styles in the shared
// styles.css. Both pages carry the same box, so the behaviour is defined once
// here and each page passes only the `source` its submissions are tagged with.

// The handler lives on cofair-marketing, not here: these pages are static and
// colonial has no mail or sheet credentials, while marketing already holds the
// Resend key and is the origin `cofair.org/pricing/` and `/tokens/` are served
// through. Absolute on purpose — the pages are also reachable at
// cofair-colonial.netlify.app, where a relative path would post into a site
// with no such route (the function sends CORS headers for that case).
const SIGNUP_ENDPOINT = "https://cofair.org/api/insights-signup";

function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function setSignupStatus(tone, title, body) {
  const status = document.getElementById("signupStatus");
  if (!status) return;
  if (!tone) {
    status.innerHTML = "";
    return;
  }
  status.innerHTML = `<div class="cofair-callout cofair-callout--${esc(tone)}">
    <p class="cofair-callout__title">${esc(title)}</p>
    ${body ? `<p class="cofair-callout__body">${esc(body)}</p>` : ""}
  </div>`;
}

/** @param {string} source Which page the submission came from. */
export function setupSignupForm(source) {
  const form = document.getElementById("signupForm");
  const submit = document.getElementById("signupSubmit");
  if (!form || !submit) return;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    // `novalidate` is set so the callout carries the message rather than a
    // browser bubble, which means the check has to be made here.
    if (!form.reportValidity()) return;

    const data = new FormData(form);
    // A filled honeypot is a bot. Answer exactly as success does, so nothing is
    // learned from the difference.
    if (String(data.get("bot-field") || "").trim()) {
      form.reset();
      setSignupStatus("success", "Thanks — we have your details.", "We'll be in touch.");
      return;
    }

    submit.disabled = true;
    submit.textContent = "Sending…";
    setSignupStatus(null);

    try {
      const response = await fetch(SIGNUP_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: String(data.get("name") || "").trim(),
          email: String(data.get("email") || "").trim(),
          source,
        }),
      });
      if (!response.ok) throw new Error(`Submission failed: ${response.status}`);
      form.hidden = true;
      setSignupStatus(
        "success",
        "Thanks — we have your details.",
        "We'll email you when there is something worth reading. No newsletter, no list sharing.",
      );
    } catch (error) {
      console.error("Insights signup failed", error);
      setSignupStatus(
        "danger",
        "That didn't send.",
        "Something went wrong on our end. Email nick@cofair.org and we'll pick it up from there.",
      );
    } finally {
      submit.disabled = false;
      submit.textContent = "Send";
    }
  });
}

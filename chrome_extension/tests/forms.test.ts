import { JSDOM } from "jsdom";
import { discoverFields } from "../src/content/form-discovery";
import { fillFields } from "../src/content/form-fill";
import { captchaDetected, isTerminalLabel } from "../src/content/safety";

describe("supervised form handling", () => {
  it("discovers and fills supported controls but not passwords", () => {
    const dom = new JSDOM(`
      <form>
        <label for="email">Email</label><input id="email" type="email">
        <label for="country">Country</label><select id="country"><option>US</option></select>
        <label for="agree">Agree</label><input id="agree" type="checkbox">
        <label for="password">Password</label><input id="password" type="password">
        <button type="submit">Submit Application</button>
      </form>`, { url: "https://example.test/apply", pretendToBeVisual: true });
    Object.defineProperty(dom.window.HTMLElement.prototype, "getBoundingClientRect", {
      value: () => ({ width: 100, height: 20 })
    });
    const fields = discoverFields(dom.window.document);
    expect(fields.some((field) => field.sensitive)).toBe(true);
    const result = fillFields(dom.window.document, [
      {
        field_identifier: "#email",
        proposed_value: "verified@example.test",
        decision: "fill",
        human_review_required: false
      },
      {
        field_identifier: "#country",
        proposed_value: "US",
        decision: "fill",
        human_review_required: false
      },
      {
        field_identifier: "#agree",
        proposed_value: "Yes",
        decision: "fill",
        human_review_required: false
      },
      {
        field_identifier: "#password",
        proposed_value: "never",
        decision: "fill",
        human_review_required: false
      }
    ]);
    expect(result.filled).toEqual(["#email", "#country", "#agree"]);
    expect((dom.window.document.querySelector("#password") as HTMLInputElement).value).toBe("");
  });

  it("detects CAPTCHA and exact terminal controls without blocking Next", () => {
    const dom = new JSDOM("<iframe src='https://google.com/recaptcha/api'></iframe>");
    expect(captchaDetected(dom.window.document)).toBe(true);
    expect(isTerminalLabel("Submit Application")).toBe(true);
    expect(isTerminalLabel("Next")).toBe(false);
    expect(isTerminalLabel("Continue")).toBe(false);
  });
});

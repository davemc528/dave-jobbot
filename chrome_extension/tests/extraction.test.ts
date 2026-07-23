import { JSDOM } from "jsdom";
import { captureFreshPosting, extractPosting } from "../src/content/extractor";
import { normalizeUrl } from "../src/shared/urls";

function extract(html: string, url: string) {
  const dom = new JSDOM(html, { url });
  return extractPosting(dom.window.document, dom.window.location, "tab-1");
}

const body = (label: string) => `
  <h1>${label} Scientist</h1>
  <main>
    <h2>Responsibilities</h2>
    ${"Conduct translational oncology research and communicate results. ".repeat(15)}
    <h2>Required Qualifications</h2>
    PhD and five years research experience.
  </main>`;

describe("posting extraction", () => {
  it("prefers JSON-LD JobPosting data", () => {
    const payload = extract(`
      <script type="application/ld+json">
        {"@type":"JobPosting","title":"Oncology Scientist","description":"${"Verified job description ".repeat(40)}","hiringOrganization":{"name":"Example Bio"}}
      </script>`, "https://jobs.example.test/role?utm_source=email&id=7");
    expect(payload.extraction_method).toBe("json-ld-job-posting");
    expect(payload.employer).toBe("Example Bio");
    expect(payload.canonical_url).toContain("id=7");
    expect(payload.canonical_url).not.toContain("utm_source");
  });

  it.each([
    ["oracle", "https://ekgn.fa.us6.oraclecloud.com/job/5996", "oracle-rendered"],
    ["workday", "https://example.myworkdayjobs.com/site/job/R1", "workday-rendered"],
    ["greenhouse", "https://boards.greenhouse.io/example/jobs/1", "greenhouse-rendered"],
    ["lever", "https://jobs.lever.co/example/1", "lever-rendered"]
  ])("uses the %s adapter", (_name, url, method) => {
    expect(extract(body("Discovery"), url).extraction_method).toBe(method);
  });

  it("rejects navigation-only generic pages", () => {
    expect(() => extract("<nav>Home Jobs Login</nav>", "https://example.test/jobs")).toThrow(
      "too short"
    );
  });

  it("reads newly inserted Oracle content for a fresh capture", async () => {
    const dom = new JSDOM("<main><h1>Loading</h1></main>", {
      url: "https://ekgn.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/5996"
    });
    Object.defineProperty(dom.window.document, "readyState", {
      configurable: true,
      value: "complete"
    });
    setTimeout(() => {
      dom.window.document.querySelector("main")!.innerHTML = body("Dynamic Oracle");
    }, 20);
    const result = await captureFreshPosting(
      dom.window.document,
      dom.window.location,
      { tabKey: "tab-1", capture_id: "capture-dynamic-oracle", max_wait_ms: 1_500 }
    );
    expect(result.payload?.full_text).toContain("translational oncology");
    expect(result.payload?.extraction_method).toBe("oracle-rendered");
  });

  it("returns required diagnostics and a review fallback for short content", async () => {
    const dom = new JSDOM(
      `<main><h1>Scientist</h1>${"Visible rendered role details. ".repeat(5)}</main>`,
      { url: "https://example.test/jobs/9" }
    );
    Object.defineProperty(dom.window.document, "readyState", {
      configurable: true,
      value: "complete"
    });
    const result = await captureFreshPosting(
      dom.window.document,
      dom.window.location,
      { tabKey: "tab-1", capture_id: "capture-low-confidence", max_wait_ms: 300 }
    );
    expect(result.short_content).toBe(true);
    expect(result.payload?.requires_human_review).toBe(true);
    expect(result.payload?.extraction_method).toBe("full-visible-body-review");
    expect(result.diagnostics).toMatchObject({
      page_title: "",
      hostname: "example.test",
      ats_type: "generic",
      selection_present: false
    });
    expect(result.diagnostics.attempts.length).toBeGreaterThan(0);
    expect(result.diagnostics.json_ld_block_count).toBe(0);
    expect(result.diagnostics.likely_container_count).toBeGreaterThan(0);
  });

  it("removes tracking but preserves requisition parameters", () => {
    expect(
      normalizeUrl("https://example.test/job?jobId=5996&utm_campaign=x&ref=email")
    ).toBe("https://example.test/job?jobId=5996");
  });
});

import { JSDOM } from "jsdom";
import { extractPosting } from "../src/content/extractor";
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

  it("removes tracking but preserves requisition parameters", () => {
    expect(
      normalizeUrl("https://example.test/job?jobId=5996&utm_campaign=x&ref=email")
    ).toBe("https://example.test/job?jobId=5996");
  });
});

import { readFileSync } from "node:fs";

describe("Manifest V3 safety", () => {
  const manifest = JSON.parse(readFileSync("manifest.json", "utf8")) as {
    manifest_version: number;
    permissions: string[];
    host_permissions: string[];
    background: { service_worker: string };
  };

  it("uses Manifest V3 with minimum permissions", () => {
    expect(manifest.manifest_version).toBe(3);
    expect(manifest.permissions).toEqual(["activeTab", "scripting", "storage"]);
    expect(manifest.permissions).not.toContain("cookies");
    expect(manifest.host_permissions).not.toContain("<all_urls>");
    expect(manifest.host_permissions.every((host) =>
      host.startsWith("http://127.0.0.1:") || host.startsWith("http://localhost:")
    )).toBe(true);
    expect(manifest.background.service_worker).toBe("service-worker.js");
  });

  it("contains no remote executable assets", () => {
    const popup = readFileSync("src/popup/popup.html", "utf8");
    expect(popup).not.toMatch(/https?:\/\/.*\.(js|css)/);
    expect(popup).not.toContain("analytics");
  });
});

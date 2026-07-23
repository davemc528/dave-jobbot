import { isExtensionMessage } from "../src/shared/schemas";

it("validates content-script messages", () => {
  expect(isExtensionMessage({ type: "extract" })).toBe(true);
  expect(isExtensionMessage({ type: "execute-arbitrary-code" })).toBe(false);
  expect(isExtensionMessage("extract")).toBe(false);
});

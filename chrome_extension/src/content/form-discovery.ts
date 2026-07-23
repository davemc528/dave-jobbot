import { FieldInventory } from "../shared/types";
import { isTerminalLabel } from "./safety";

function labelFor(element: HTMLElement): string {
  const input = element as HTMLInputElement;
  const escapedId = element.ownerDocument.defaultView?.CSS?.escape(input.id) ?? input.id;
  const explicit = input.id
    ? element.ownerDocument.querySelector<HTMLLabelElement>(`label[for="${escapedId}"]`)
    : null;
  return (
    explicit?.innerText ||
    input.labels?.[0]?.innerText ||
    element.getAttribute("aria-label") ||
    element.getAttribute("placeholder") ||
    element.getAttribute("name") ||
    ""
  ).trim();
}

function visible(element: HTMLElement): boolean {
  const style = element.ownerDocument.defaultView!.getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
}

export function discoverFields(document: Document): FieldInventory[] {
  const fields: FieldInventory[] = [];
  document.querySelectorAll<HTMLElement>("input,select,textarea,[role='combobox']").forEach(
    (element, index) => {
      if (!visible(element)) return;
      const input = element as HTMLInputElement;
      const inputType = (input.type || element.tagName.toLowerCase()).toLowerCase();
      const label = labelFor(element);
      const section = element.closest("fieldset,section")?.querySelector("legend,h1,h2,h3,h4");
      const sensitive =
        inputType === "password" ||
        /password|one-time|verification code|security question|mfa/i.test(label);
      fields.push({
        identifier: input.id
          ? `#${element.ownerDocument.defaultView?.CSS?.escape(input.id) ?? input.id}`
          : input.name
            ? `[name="${element.ownerDocument.defaultView?.CSS?.escape(input.name) ?? input.name}"]`
            : `${element.tagName.toLowerCase()}:nth-of-type(${index + 1})`,
        element_type: element.tagName.toLowerCase(),
        input_type: inputType,
        name: input.name || undefined,
        element_id: input.id || undefined,
        label,
        aria_label: element.getAttribute("aria-label") || undefined,
        placeholder: input.placeholder || undefined,
        nearby_text: (
          element.parentElement?.innerText ??
          element.parentElement?.textContent ??
          ""
        ).slice(0, 300),
        required: input.required || element.getAttribute("aria-required") === "true",
        has_value: inputType === "password" ? Boolean(input.value) : Boolean(input.value),
        options:
          element instanceof HTMLSelectElement
            ? [...element.options].map((option) => option.text)
            : [],
        section_heading: section?.textContent?.trim(),
        page_url: location.href,
        frame_identifier: window === window.top ? "top" : "child",
        terminal: isTerminalLabel(label),
        sensitive
      });
    }
  );
  return fields;
}

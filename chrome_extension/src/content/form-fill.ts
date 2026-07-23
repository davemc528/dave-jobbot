import { FillInstruction } from "../shared/types";

function events(element: HTMLElement): void {
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  element.dispatchEvent(new FocusEvent("blur", { bubbles: true }));
}

export function fillFields(
  document: Document,
  instructions: FillInstruction[]
): { filled: string[]; withheld: Array<{ identifier: string; reason: string }> } {
  const filled: string[] = [];
  const withheld: Array<{ identifier: string; reason: string }> = [];
  for (const instruction of instructions) {
    if (
      instruction.decision !== "fill" ||
      instruction.human_review_required ||
      instruction.proposed_value === undefined
    ) {
      withheld.push({
        identifier: instruction.field_identifier,
        reason: instruction.withhold_reason ?? "not_permitted"
      });
      continue;
    }
    const element = document.querySelector<HTMLElement>(instruction.field_identifier);
    if (!element) {
      withheld.push({ identifier: instruction.field_identifier, reason: "field_not_found" });
      continue;
    }
    if (element instanceof HTMLInputElement && element.type === "file") {
      element.style.outline = "3px solid #ef6c00";
      withheld.push({
        identifier: instruction.field_identifier,
        reason: "explicit_manual_resume_attachment_required"
      });
      continue;
    }
    if (
      element instanceof HTMLInputElement &&
      ["password", "hidden"].includes(element.type)
    ) {
      withheld.push({ identifier: instruction.field_identifier, reason: "sensitive_field" });
      continue;
    }
    if (element instanceof HTMLSelectElement) {
      const option = [...element.options].find(
        (item) => item.text.trim().toLowerCase() === instruction.proposed_value?.toLowerCase()
      );
      if (!option) {
        withheld.push({ identifier: instruction.field_identifier, reason: "option_not_found" });
        continue;
      }
      element.value = option.value;
    } else if (
      element instanceof HTMLInputElement &&
      ["checkbox", "radio"].includes(element.type)
    ) {
      element.checked = ["yes", "true", "1", "on"].includes(
        instruction.proposed_value.toLowerCase()
      );
    } else if (
      element instanceof HTMLInputElement ||
      element instanceof HTMLTextAreaElement
    ) {
      element.value = instruction.proposed_value;
    } else if (element.getAttribute("role") === "combobox") {
      element.focus();
      element.setAttribute("aria-valuetext", instruction.proposed_value);
      withheld.push({
        identifier: instruction.field_identifier,
        reason: "custom_select_requires_confirmation"
      });
      continue;
    } else {
      withheld.push({ identifier: instruction.field_identifier, reason: "unsupported_control" });
      continue;
    }
    events(element);
    filled.push(instruction.field_identifier);
  }
  return { filled, withheld };
}

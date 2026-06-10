function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}

function optionButton(field, option, selected, onChange) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = selected ? "optionButton active" : "optionButton";
  button.textContent = option;
  button.addEventListener("click", () => onChange(option));
  return button;
}

export function renderField(container, field, store, onChange) {
  const wrapper = document.createElement("section");
  wrapper.className = "fieldControl";
  wrapper.dataset.fieldId = field.field_id;
  const label = document.createElement("h3");
  label.textContent = field.label || field.field_id;
  wrapper.append(label);
  const value = store.valueFor(field);
  const controls = document.createElement("div");
  controls.className = "fieldOptions";
  const update = (nextValue) => {
    store.setValue(field, nextValue);
    onChange?.();
    renderField(container, field, store, onChange);
  };

  if (field.kind === "score") {
    for (const option of field.options || [1, 2, 3, 4, 5]) {
      controls.append(optionButton(field, String(option), value === option, () => update(option)));
    }
  } else if (field.kind === "boolean") {
    controls.append(optionButton(field, "是", value === true, () => update(true)));
    controls.append(optionButton(field, "否", value === false, () => update(false)));
  } else if (field.kind === "single_select") {
    for (const option of field.options || []) {
      controls.append(optionButton(field, option, value === option, () => update(option)));
    }
  } else if (field.kind === "multi_select") {
    const current = Array.isArray(value) ? value : [];
    for (const option of field.options || []) {
      controls.append(optionButton(field, option, current.includes(option), () => {
        const next = current.includes(option) ? current.filter((entry) => entry !== option) : [...current, option];
        update(next);
      }));
    }
  } else if (field.kind === "text") {
    const textarea = document.createElement("textarea");
    textarea.rows = 3;
    textarea.value = value || "";
    textarea.addEventListener("input", () => update(textarea.value));
    controls.append(textarea);
  } else {
    controls.innerHTML = `<p class="mutedText">不支持的字段：${escapeHtml(field.kind)}</p>`;
  }

  wrapper.append(controls);
  const old = container.querySelector(`[data-field-id="${CSS.escape(field.field_id)}"]`);
  if (old) old.replaceWith(wrapper);
  else container.append(wrapper);
}

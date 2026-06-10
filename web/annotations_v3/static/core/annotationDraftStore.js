import {fieldKey, getNested, setNested} from "./annotationSchema.js";

export function createDraftStore(context) {
  const fields = context.fields || [];
  const draft = structuredClone(context.values?.draft || {});
  function valueFor(field) {
    return getNested(draft, field.path);
  }
  function setValue(field, value) {
    setNested(draft, field.path, value);
  }
  function changes() {
    return fields
      .map((field) => ({op: "set", path: field.path, value: getNested(draft, field.path)}))
      .filter((change) => change.value !== undefined);
  }
  return {fields, draft, valueFor, setValue, changes, fieldKey};
}

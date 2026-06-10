export function fieldKey(field) {
  return field.path.join("/");
}

export function getNested(values, path) {
  return path.reduce((target, key) => target && target[key], values);
}

export function setNested(values, path, value) {
  let target = values;
  for (const key of path.slice(0, -1)) {
    target[key] = target[key] || {};
    target = target[key];
  }
  target[path[path.length - 1]] = value;
}

export function readDatasetContext(root) {
  return {
    datasetId: root.dataset.datasetId,
    stage: root.dataset.stage || "rough",
    username: window.localStorage.getItem("annotations_v3_username") || "anonymous"
  };
}

export function setUsername(username) {
  const clean = String(username || "").trim() || "anonymous";
  window.localStorage.setItem("annotations_v3_username", clean);
  return clean;
}

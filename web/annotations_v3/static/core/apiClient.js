export async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

export function claimAssignment(datasetId, stage, username) {
  return requestJson(`/api/datasets/${datasetId}/assignments/claim`, {
    method: "POST",
    body: JSON.stringify({stage, username})
  });
}

export function saveAnnotationPatch(datasetId, itemId, payload) {
  return requestJson(`/api/datasets/${datasetId}/items/${itemId}/annotation-patch`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

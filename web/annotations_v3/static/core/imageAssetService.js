export function chooseAsset(sideAssets, mode = "preview") {
  if (!sideAssets || sideAssets.status !== "ready") return null;
  return sideAssets[mode] || sideAssets.preview || sideAssets.thumb || null;
}

export function createImageAssetService() {
  const states = new Map();
  function preload(url) {
    if (!url) return Promise.resolve("missing");
    if (states.has(url)) return states.get(url).promise;
    const image = new Image();
    const promise = new Promise((resolve) => {
      image.onload = () => resolve("loaded");
      image.onerror = () => resolve("failed");
    });
    states.set(url, {status: "loading", promise});
    image.src = url;
    return promise.then((status) => {
      states.set(url, {status, promise: Promise.resolve(status)});
      return status;
    });
  }
  return {chooseAsset, preload, stateFor: (url) => states.get(url)?.status || "idle"};
}

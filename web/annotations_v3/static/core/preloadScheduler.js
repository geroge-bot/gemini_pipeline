export function createPreloadScheduler(imageService) {
  let generation = 0;
  function cancel() {
    generation += 1;
  }
  function scheduleRatePreloads(items, currentIndex, network = {}) {
    const ownGeneration = generation;
    const slow = Number(network.rtt_ms || 0) > 250 || Number(network.downlink_mbps || 10) < 3;
    const previewAhead = slow ? 2 : 5;
    const indexes = new Set([currentIndex, currentIndex - 1]);
    for (let offset = 1; offset <= previewAhead; offset += 1) indexes.add(currentIndex + offset);
    for (const index of indexes) {
      const item = items[index];
      if (!item || ownGeneration !== generation) continue;
      for (const side of ["src", "dst"]) {
        const asset = imageService.chooseAsset(item.image_assets?.[side], "preview");
        imageService.preload(asset?.url);
      }
    }
  }
  return {cancel, scheduleRatePreloads};
}

export function scheduleRatePreloads(scheduler, items, currentIndex, network = {}) {
  return scheduler.scheduleRatePreloads(items, currentIndex, network);
}

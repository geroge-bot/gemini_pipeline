import {chooseAsset} from "../core/imageAssetService.js";

function sidePanel(title, sideAssets) {
  const asset = chooseAsset(sideAssets, "preview");
  const status = sideAssets?.status || "missing";
  if (!asset) {
    return `
      <figure class="imagePanel">
        <figcaption>${title}</figcaption>
        <div class="imagePlaceholder">${status === "error" ? "图片错误" : "暂无预览"}</div>
      </figure>
    `;
  }
  return `
    <figure class="imagePanel">
      <figcaption>${title}</figcaption>
      <div class="imageBox"><img src="${asset.url}" alt="${title}" loading="eager"></div>
    </figure>
  `;
}

export function renderImagePair(container, item) {
  container.innerHTML = `
    ${sidePanel("原图", item.image_assets?.src)}
    ${sidePanel("目标图", item.image_assets?.dst)}
  `;
}

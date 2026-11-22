// Keep the displayed PDF page and its interactive layer in the same coordinate space.
export function mountFieldPage(image) {
  const page = document.createElement('div');
  page.className = 'page-surface';
  const layer = document.createElement('div');
  layer.id = 'pdfHotspots';
  layer.className = 'pdf-hotspots';
  page.append(image, layer);
  return page;
}

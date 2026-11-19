/* El navegador suele rotar el video por sí mismo; solo compensamos si no lo hace. */
function cameraGeometry({width, height, edge, landscape = null, angle = 0, manual = 0}) {
  let rotation = 0;
  if (landscape !== null && width !== height && (width > height) !== landscape) {
    rotation = landscape ? (angle === 270 || angle === -90 ? -90 : 90) : -90;
  }
  rotation = ((rotation + manual) % 360 + 360) % 360;
  const swap = rotation % 180 !== 0;
  const ratio = Math.min(1, edge / Math.max(width, height));
  const sourceWidth = Math.max(1, Math.round(width * ratio));
  const sourceHeight = Math.max(1, Math.round(height * ratio));
  return {rotation, sourceWidth, sourceHeight,
    width: swap ? sourceHeight : sourceWidth, height: swap ? sourceWidth : sourceHeight};
}
function drawCameraFrame(context, video, geometry) {
  context.save();
  context.translate(geometry.width / 2, geometry.height / 2);
  context.rotate(geometry.rotation * Math.PI / 180);
  context.drawImage(video, -geometry.sourceWidth / 2, -geometry.sourceHeight / 2,
    geometry.sourceWidth, geometry.sourceHeight);
  context.restore();
}
if (typeof module !== 'undefined') module.exports = {cameraGeometry, drawCameraFrame};

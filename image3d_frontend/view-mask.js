/* Image-space strokes keep the mask aligned when the dialog is resized. */
(function (root) {
  "use strict";
  function imagePoint(event, rect, width, height) {
    return {
      x: Math.max(0, Math.min(width, (event.clientX - rect.left) * width / rect.width)),
      y: Math.max(0, Math.min(height, (event.clientY - rect.top) * height / rect.height)),
    };
  }
  function maskPixels(pixels) {
    let selected = 0;
    for (let i = 0; i < pixels.length; i += 4) {
      const edit = pixels[i + 3] >= 128;
      pixels[i] = pixels[i + 1] = pixels[i + 2] = 0;
      pixels[i + 3] = edit ? 0 : 255;
      if (edit) selected += 1;
    }
    return selected;
  }
  class Painter {
    constructor(canvas, onChange = () => {}) {
      this.canvas = canvas;
      this.context = canvas.getContext("2d", { willReadFrequently: true });
      this.onChange = onChange;
      this.strokes = [];
      this.tool = "paint";
      this.size = 40;
      this.enabled = true;
      this.activePointer = null;
      this.keyboardPoint = { x: canvas.width / 2, y: canvas.height / 2 };
      canvas.addEventListener("pointerdown", event => {
        if (!this.enabled || this.activePointer !== null || (event.pointerType === "mouse" && event.button !== 0)) return;
        event.preventDefault();
        canvas.focus({ preventScroll: true });
        this.activePointer = event.pointerId;
        canvas.setPointerCapture(event.pointerId);
        this.begin(imagePoint(event, canvas.getBoundingClientRect(), canvas.width, canvas.height));
      });
      canvas.addEventListener("pointermove", event => {
        if (this.activePointer !== event.pointerId || !this.enabled) return;
        event.preventDefault();
        this.extend(imagePoint(event, canvas.getBoundingClientRect(), canvas.width, canvas.height));
      });
      for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
        canvas.addEventListener(name, event => {
          if (this.activePointer !== event.pointerId) return;
          this.activePointer = null;
          this.onChange();
        });
      }
      canvas.addEventListener("keydown", event => {
        if (!this.enabled) return;
        const directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        if (directions[event.key]) {
          event.preventDefault();
          const [dx, dy] = directions[event.key], step = Math.max(1, this.size / 4);
          this.keyboardPoint = {
            x: Math.max(0, Math.min(canvas.width, this.keyboardPoint.x + dx * step)),
            y: Math.max(0, Math.min(canvas.height, this.keyboardPoint.y + dy * step)),
          };
          this.onChange();
        } else if (event.key === " " || event.key === "Enter") {
          event.preventDefault();
          this.begin({ ...this.keyboardPoint });
        }
      });
    }
    reset(width, height) {
      this.canvas.width = width;
      this.canvas.height = height;
      this.keyboardPoint = { x: width / 2, y: height / 2 };
      this.strokes = [];
      this.activePointer = null;
      this.redraw();
    }
    begin(point) {
      this.strokes.push({ tool: this.tool, size: this.size, points: [point] });
      this.keyboardPoint = point;
      this.redraw();
    }
    extend(point) {
      const stroke = this.strokes[this.strokes.length - 1];
      if (!stroke) return;
      const previous = stroke.points[stroke.points.length - 1];
      stroke.points.push(point);
      this.drawSegment(stroke, previous, point);
      this.keyboardPoint = point;
    }
    drawSegment(stroke, from, to) {
      const ctx = this.context;
      ctx.globalCompositeOperation = stroke.tool === "erase" ? "destination-out" : "source-over";
      ctx.strokeStyle = ctx.fillStyle = "#cf3e70";
      ctx.lineWidth = stroke.size;
      ctx.lineCap = ctx.lineJoin = "round";
      ctx.beginPath();
      ctx.arc(from.x, from.y, stroke.size / 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(from.x, from.y);
      ctx.lineTo(to.x, to.y);
      ctx.stroke();
    }
    redraw() {
      this.context.clearRect(0, 0, this.canvas.width, this.canvas.height);
      for (const stroke of this.strokes) {
        this.drawSegment(stroke, stroke.points[0], stroke.points[0]);
        for (let i = 1; i < stroke.points.length; i++) this.drawSegment(stroke, stroke.points[i - 1], stroke.points[i]);
      }
      this.onChange();
    }
    undo() { this.strokes.pop(); this.redraw(); }
    clear() { this.strokes = []; this.redraw(); }
    exportMask() {
      const pixels = this.context.getImageData(0, 0, this.canvas.width, this.canvas.height);
      if (!maskPixels(pixels.data)) throw new Error("请先涂抹需要修改的区域");
      const output = document.createElement("canvas");
      output.width = this.canvas.width;
      output.height = this.canvas.height;
      output.getContext("2d").putImageData(pixels, 0, 0);
      const dataUrl = output.toDataURL("image/png");
      if (dataUrl.length > 4_000_000) throw new Error("选区过于复杂，请清空后用更大的画笔重新涂抹");
      return dataUrl;
    }
  }
  const api = { Painter, imagePoint, maskPixels };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ViewMask = api;
})(typeof globalThis === "undefined" ? this : globalThis);

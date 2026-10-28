// The Site map (DESIGN.md section 5). The server draws the map and keeps its state in the URL;
// this adds zoom and pan, hover cards, Esc and background clicks to unpin, filters that apply
// on change, and the PNG export, drawn from the SVG the export sends.
let zoomed = { map: "", viewBox: "" };
let openCard = null;

function root() {
  return document.getElementById("sitemap");
}

function svg() {
  return document.querySelector("#sitemap .sitemap-svg");
}

function fullBox(map) {
  return map.viewBox.baseVal;
}

function load(url) {
  return htmx.ajax("GET", url, { target: "#sitemap", select: "#sitemap", swap: "outerHTML" });
}

function go(url) {
  if (!url) return;
  if (window.htmx) {
    load(url).then(() => history.pushState({ sitemap: true }, "", url));
  } else {
    window.location.href = url;
  }
}

// Back and forward over the pins and unpins pushed above: draw the map for that URL again.
// htmx restores the entries it pushed itself (site, layer and filter links).
window.addEventListener("popstate", (event) => {
  if (window.htmx && root() && !(event.state && event.state.htmx)) load(location.href);
});

function say(text) {
  const map = root();
  if (!map) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.setAttribute("role", "status");
  toast.textContent = text;
  map.prepend(toast);
}

// Zoom: change the viewBox around its centre. Fit goes back to the whole map.
function setBox(map, x, y, w, h) {
  map.setAttribute("viewBox", `${x} ${y} ${w} ${h}`);
  zoomed = { map: root().dataset.map, viewBox: map.getAttribute("viewBox") };
}

function zoom(how) {
  const map = svg();
  if (!map) return;
  if (how === "fit") {
    map.setAttribute("viewBox", map.dataset.fit);
    zoomed = { map: "", viewBox: "" };
    return;
  }
  const box = fullBox(map);
  const factor = how === "in" ? 0.8 : 1.25;
  const [, , fitW, fitH] = map.dataset.fit.split(" ").map(Number);
  const w = Math.min(fitW * 2, Math.max(fitW / 6, box.width * factor));
  const h = (w / box.width) * box.height;
  setBox(map, box.x + (box.width - w) / 2, box.y + (box.height - h) / 2, w, h);
}

function ready() {
  const map = svg();
  if (!map) return;
  map.dataset.fit = map.getAttribute("viewBox");
  // Pinning a device redraws the map; keep the engineer's zoom on the same site and layer.
  if (zoomed.map && zoomed.map === root().dataset.map) map.setAttribute("viewBox", zoomed.viewBox);
}

// Hover cards: shown next to the device on hover and keyboard focus, closed by Esc or leaving.
function showCard(node) {
  hideCard();
  const card = document.getElementById(node.dataset.card);
  if (!card) return;
  card.classList.add("is-open");
  const anchor = node.getBoundingClientRect();
  const width = card.offsetWidth || 320;
  const height = card.offsetHeight || 0;
  let left = anchor.right + 8;
  if (left + width > window.innerWidth - 8) left = Math.max(8, anchor.left - width - 8);
  const top = Math.max(8, Math.min(anchor.top, window.innerHeight - height - 8));
  card.style.left = `${left}px`;
  card.style.top = `${top}px`;
  openCard = card;
}

function hideCard() {
  if (openCard) openCard.classList.remove("is-open");
  openCard = null;
}

for (const name of ["mouseover", "focusin"]) {
  document.addEventListener(name, (event) => {
    const node = event.target.closest && event.target.closest("#sitemap .sitemap-node");
    if (node) showCard(node);
  });
}
for (const name of ["mouseout", "focusout"]) {
  document.addEventListener(name, (event) => {
    const node = event.target.closest && event.target.closest("#sitemap .sitemap-node");
    if (node && !node.contains(event.relatedTarget)) hideCard();
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !root()) return;
  if (openCard) {
    hideCard();
    return;
  }
  if (event.target.closest && event.target.closest("details[open], select")) return;
  go(root().dataset.clearUrl);
});

// Pan by dragging the background. A drag isn't a click, so it doesn't unpin.
let drag = null;
document.addEventListener("pointerdown", (event) => {
  const map = event.target.closest && event.target.closest("#sitemap .sitemap-svg");
  if (!map || event.button !== 0 || event.target.closest(".sitemap-node")) return;
  const box = fullBox(map);
  const rect = map.getBoundingClientRect();
  // The map keeps its aspect ratio, so the tighter side sets the scale.
  const scale = Math.max(box.width / rect.width, box.height / rect.height);
  drag = { map, x: event.clientX, y: event.clientY, box: [box.x, box.y, box.width, box.height], scale, moved: false };
});
document.addEventListener("pointermove", (event) => {
  if (!drag) return;
  const dx = event.clientX - drag.x;
  const dy = event.clientY - drag.y;
  if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
  drag.moved = true;
  drag.map.parentElement.classList.add("panning");
  const [x, y, w, h] = drag.box;
  setBox(drag.map, x - dx * drag.scale, y - dy * drag.scale, w, h);
});
document.addEventListener("pointercancel", () => {
  if (drag) drag.map.parentElement.classList.remove("panning");
  drag = null;
});
document.addEventListener("pointerup", (event) => {
  if (!drag) return;
  const { map, moved } = drag;
  drag = null;
  map.parentElement.classList.remove("panning");
  // A click on the background unpins.
  if (!moved && !event.target.closest(".sitemap-node")) go(root().dataset.clearUrl);
});

document.addEventListener("click", (event) => {
  const zoomButton = event.target.closest && event.target.closest("#sitemap [data-zoom]");
  if (zoomButton) {
    zoom(zoomButton.dataset.zoom);
    return;
  }
  // Devices in the SVG are SVG links, which htmx doesn't boost: swap the map the same way.
  const node = event.target.closest && event.target.closest("#sitemap .sitemap-node");
  if (node && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
    event.preventDefault();
    hideCard();
    go(node.getAttribute("href"));
  }
});

document.addEventListener("change", (event) => {
  const form = event.target.closest && event.target.closest("#sitemap form[data-autosubmit]");
  if (form) form.requestSubmit();
});

// PNG: the server logs the export and sends the SVG; the browser draws it at twice the size.
document.addEventListener("submit", async (event) => {
  if (event.target.id !== "sitemap-export" || event.submitter?.value !== "png") return;
  event.preventDefault();
  const form = event.target;
  form.querySelector("details")?.removeAttribute("open");
  const body = new FormData(form);
  body.set("format", "png");
  const failed = "The PNG couldn't be made. Try the SVG export instead.";
  let response;
  let text;
  try {
    response = await fetch(form.action, { method: "POST", body, credentials: "same-origin" });
    text = await response.text();
  } catch {
    say(failed);
    return;
  }
  if (!response.ok) {
    say(failed);
    return;
  }
  const disposition = response.headers.get("Content-Disposition") || "";
  const name = (disposition.match(/filename="([^"]+)"/) || [])[1] || "cartio-site-map.png";
  const url = URL.createObjectURL(new Blob([text], { type: "image/svg+xml" }));
  const image = new Image();
  image.onerror = () => {
    URL.revokeObjectURL(url);
    say(failed);
  };
  image.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth * 2;
    canvas.height = image.naturalHeight * 2;
    const context = canvas.getContext("2d");
    context.scale(2, 2);
    context.drawImage(image, 0, 0);
    URL.revokeObjectURL(url);
    canvas.toBlob((blob) => {
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }, "image/png");
  };
  image.src = url;
});

document.addEventListener("htmx:afterSettle", ready);
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
else ready();

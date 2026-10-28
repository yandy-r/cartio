// Hover cards (DESIGN.md, Hover cards). CSS opens a card on hover and keyboard focus; this places
// it under its anchor, inside the window, and lets Esc close it until the pointer or focus leaves.
function place(cell) {
  const card = cell.querySelector(".hover-card");
  if (!card) return;
  const anchor = cell.getBoundingClientRect();
  const width = card.offsetWidth || 320;
  const height = card.offsetHeight || 0;
  const below = anchor.bottom + 4;
  const top = below + height > window.innerHeight ? Math.max(8, anchor.top - height - 4) : below;
  card.style.top = `${top}px`;
  card.style.left = `${Math.max(8, Math.min(anchor.left, window.innerWidth - width - 8))}px`;
}

for (const name of ["mouseover", "focusin"]) {
  document.addEventListener(name, (event) => {
    const cell = event.target.closest && event.target.closest(".host-cell");
    if (cell) requestAnimationFrame(() => place(cell));
  });
}
for (const name of ["mouseout", "focusout"]) {
  document.addEventListener(name, (event) => {
    const cell = event.target.closest && event.target.closest(".host-cell");
    if (cell && !cell.contains(event.relatedTarget)) cell.classList.remove("card-dismissed");
  });
}
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  for (const cell of document.querySelectorAll(".host-cell:hover, .host-cell:focus-within")) {
    cell.classList.add("card-dismissed");
  }
});

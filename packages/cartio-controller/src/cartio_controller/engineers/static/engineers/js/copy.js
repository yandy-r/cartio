// "Copy command" buttons: copy the text of the element named by data-copy.
document.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-copy]");
  if (!button) return;
  const source = document.getElementById(button.dataset.copy);
  if (!source || !navigator.clipboard) return;
  navigator.clipboard.writeText(source.textContent.trim()).then(() => {
    button.textContent = "Copied";
  });
});

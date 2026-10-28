// Global search (DESIGN.md section 11): open with the sidebar button or ⌘K / Ctrl K, move with
// the arrow keys, open with Enter. Esc closes the dialog (a native <dialog>). Results come from
// the server as an htmx partial.
(() => {
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || "");

  function parts() {
    const dialog = document.getElementById("search-dialog");
    if (!dialog) return null;
    return {
      dialog,
      form: dialog.querySelector("form"),
      input: document.getElementById("search-q"),
      results: document.getElementById("search-results"),
    };
  }

  function options(p) {
    return Array.from(p.results.querySelectorAll('[role="option"]'));
  }

  function select(p, index) {
    const all = options(p);
    if (!all.length) {
      p.input.removeAttribute("aria-activedescendant");
      return;
    }
    const target = Math.max(0, Math.min(all.length - 1, index));
    all.forEach((option, i) => option.setAttribute("aria-selected", i === target ? "true" : "false"));
    p.input.setAttribute("aria-activedescendant", all[target].id);
    all[target].scrollIntoView({ block: "nearest" });
  }

  function current(p) {
    return options(p).findIndex((option) => option.getAttribute("aria-selected") === "true");
  }

  function refresh(p) {
    if (window.htmx) window.htmx.trigger(p.form, "search-refresh");
  }

  function open() {
    const p = parts();
    if (!p || p.dialog.open) return;
    p.input.value = "";
    p.dialog.showModal();
    p.input.focus();
    refresh(p);
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!isMac) document.querySelectorAll("[data-search-kbd]").forEach((el) => { el.textContent = "Ctrl K"; });
    const p = parts();
    if (!p) return;
    p.form.addEventListener("submit", (event) => event.preventDefault());
    p.input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        select(p, current(p) + (event.key === "ArrowDown" ? 1 : -1));
      } else if (event.key === "Enter") {
        event.preventDefault();
        const chosen = options(p)[current(p)];
        if (chosen) chosen.click();
      }
    });
    // A click on the backdrop (outside the dialog's box) closes it.
    p.dialog.addEventListener("click", (event) => {
      if (event.target === p.dialog) p.dialog.close();
    });
    p.dialog.addEventListener("mouseover", (event) => {
      const option = event.target.closest('[role="option"]');
      if (option) select(p, options(p).indexOf(option));
    });
    p.dialog.querySelectorAll("[data-search-scope]").forEach((button) => {
      button.addEventListener("click", () => {
        document.getElementById("search-scope").value = button.dataset.searchScope;
        p.dialog.querySelectorAll("[data-search-scope]").forEach((other) => {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        p.input.focus();
        refresh(p);
      });
    });
    p.results.addEventListener("htmx:afterSwap", () => select(p, 0));
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-search-open]")) open();
  });

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "k") {
      event.preventDefault();
      open();
    }
  });
})();

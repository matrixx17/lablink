(() => {
  const filters = Array.from(document.querySelectorAll("[data-filter]"));
  const stories = Array.from(document.querySelectorAll(".story[data-domain]"));

  function setFilter(domain) {
    filters.forEach((button) => {
      const active = button.dataset.filter === domain;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });

    stories.forEach((story) => {
      story.hidden = domain !== "all" && story.dataset.domain !== domain;
    });

    const url = new URL(window.location.href);
    if (domain === "all") url.searchParams.delete("story");
    else url.searchParams.set("story", domain);
    window.history.replaceState({}, "", url);
  }

  filters.forEach((button) => {
    button.addEventListener("click", () => setFilter(button.dataset.filter || "all"));
  });

  document.querySelector('[data-action="print"]')?.addEventListener("click", () => window.print());

  const requestedStory = new URLSearchParams(window.location.search).get("story");
  if (requestedStory === "compchem" || requestedStory === "wetlab") setFilter(requestedStory);
})();

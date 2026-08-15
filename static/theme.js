(function () {
  "use strict";

  const STORAGE_KEY = "taodaobao-color-scheme";
  const DARK_QUERY = "(prefers-color-scheme: dark)";

  function storedPreference() {
    try {
      const value = localStorage.getItem(STORAGE_KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch {
      return null;
    }
  }

  function systemPreference() {
    return globalThis.matchMedia?.(DARK_QUERY).matches ? "dark" : "light";
  }

  function currentTheme() {
    return document.documentElement.dataset.theme || storedPreference() || systemPreference();
  }

  function updateControls(theme) {
    const next = theme === "dark" ? "light" : "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.setAttribute("aria-label", `切换到${next === "dark" ? "黑夜" : "明亮"}模式`);
      button.setAttribute("title", `切换到${next === "dark" ? "黑夜" : "明亮"}模式`);
      button.setAttribute("aria-pressed", String(theme === "dark"));
      const label = button.querySelector("[data-theme-label]");
      if (label) label.textContent = theme === "dark" ? "明亮模式" : "黑夜模式";
    });
  }

  function notifyFrames(theme) {
    document.querySelectorAll("iframe").forEach((frame) => {
      try {
        frame.contentWindow?.postMessage({ type: "taodaobao-theme", theme }, location.origin);
      } catch {
        // Cross-origin frames are intentionally ignored.
      }
    });
  }

  function applyTheme(theme, options) {
    const resolved = theme === "dark" ? "dark" : "light";
    const persist = options?.persist === true;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      "content",
      resolved === "dark" ? "#0f1115" : "#f3f1ea",
    );
    if (persist) {
      try {
        localStorage.setItem(STORAGE_KEY, resolved);
      } catch {
        // A disabled storage API must not block theme switching.
      }
    }
    updateControls(resolved);
    notifyFrames(resolved);
    return resolved;
  }

  function toggleTheme() {
    return applyTheme(currentTheme() === "dark" ? "light" : "dark", { persist: true });
  }

  applyTheme(storedPreference() || systemPreference());

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-theme-toggle]")) toggleTheme();
  });
  document.addEventListener("DOMContentLoaded", () => updateControls(currentTheme()));
  globalThis.matchMedia?.(DARK_QUERY).addEventListener?.("change", (event) => {
    if (!storedPreference()) applyTheme(event.matches ? "dark" : "light");
  });
  globalThis.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEY && (event.newValue === "light" || event.newValue === "dark")) {
      applyTheme(event.newValue);
    }
  });
  globalThis.addEventListener("message", (event) => {
    if (event.origin === location.origin && event.data?.type === "taodaobao-theme") {
      applyTheme(event.data.theme);
    }
  });

  globalThis.TaodaobaoTheme = { apply: applyTheme, current: currentTheme, toggle: toggleTheme };
})();

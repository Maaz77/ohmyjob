// Apply Jobs: pop-ups as tabs (content script, page world).
//
// In a tab of an /apply-jobs run (bridge.js sets data-aa-popups-as-tabs on <html>), window.open
// with pop-up features (width, height, popup ...) opens a tab instead of a pop-up window. Chrome
// puts the new tab in the run's tab group, where Claude in Chrome can see it. The new tab keeps
// window.opener, so a sign-in page can send its result back and close itself.
// Without the attribute, window.open works as before.

(() => {
  const ATTR = "data-aa-popups-as-tabs";
  const nativeOpen = window.open;
  if (typeof nativeOpen !== "function") return;

  // Keep only noopener / noreferrer: any other feature makes Chrome open a pop-up window.
  const keptFeatures = (features) =>
    String(features)
      .split(",")
      .map((part) => part.trim())
      .filter((part) => /^(noopener|noreferrer)(\s*=.*)?$/i.test(part))
      .join(",");

  function open(url, target, features) {
    const root = document.documentElement;
    const popup = arguments.length >= 3 && features != null && String(features).trim() !== "";
    if (popup && root && root.hasAttribute(ATTR)) {
      const kept = keptFeatures(features);
      return kept ? nativeOpen.call(window, url, target, kept) : nativeOpen.call(window, url, target);
    }
    return nativeOpen.apply(window, arguments);
  }

  try {
    Object.defineProperty(window, "open", { value: open, writable: true, configurable: true, enumerable: true });
  } catch (e) {
    window.open = open;
  }
})();

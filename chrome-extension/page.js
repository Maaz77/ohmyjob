// ohmyjob: pop-ups as tabs (content script, page world).
//
// In a tab of an /ohmyjob:apply-jobs run (bridge.js sets data-aa-popups-as-tabs on <html>), window.open
// with pop-up features (width, height, popup ...) opens a tab instead of a pop-up window. Chrome
// puts the new tab in the run's tab group, where Claude in Chrome can see it. The new tab keeps
// window.opener, so a sign-in page can send its result back and close itself.
// Without the attribute, window.open works as before.
//
// In the same tabs, alert, confirm and prompt would block the worker, which cannot answer a dialog: they are answered
// at once (alert shown as text, confirm and prompt as Cancel) and a banner tells the worker what the page said. The
// "Leave site?" prompt of beforeunload is dropped, and showOpenFilePicker / showDirectoryPicker reject as a cancelled
// picker does. Outside a run group, and after the tab is grouped away at the end
// of a run, all of them are native again (the replacements are installed only while the attribute is there), so the candidate's own Submit is never cancelled.

(() => {
  const ATTR = "data-aa-popups-as-tabs";
  const nativeOpen = window.open;
  const active = () => !!document.documentElement && document.documentElement.hasAttribute(ATTR);

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

  // A banner with no href: an <a> without href shows in read_page's interactive view, a role=alert does not. It lets
  // clicks through (pointer-events:none), because a click lands on a screen point, so it must never take one.
  function note(kind, message) {
    const root = document.documentElement;
    if (!root) return;
    let el = document.querySelector("a[data-aa-dialog]");
    if (!el) {
      el = document.createElement("a");
      el.setAttribute("data-aa-dialog", "");
      el.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2147483647;pointer-events:none;" +
        "background:#fff8d6;color:#222;font:13px/1.4 sans-serif;padding:6px 12px;border-bottom:1px solid #c9a400";
      (document.body || root).appendChild(el);
    }
    const text = `[AA ${kind}] ${message}`;
    el.setAttribute("aria-label", text.length > 100 ? text.slice(0, 99) + "\u2026" : text);
    el.textContent = text + " (ohmyjob run tab: dialogs are answered Cancel; move the tab out of its group to answer them yourself)";
  }

  // The next real click or key press of the worker is a later step: the message of the last one must not stay.
  function dropBanner(event) {
    if (!event.isTrusted || !active()) return;
    const el = document.querySelector("a[data-aa-dialog]");
    if (el) el.remove();
  }

  function install(name, value) {
    try {
      Object.defineProperty(window, name, { value, writable: true, configurable: true, enumerable: true });
    } catch (e) {
      window[name] = value;
    }
  }

  // The replacements exist only while <html> carries the attribute, so every other site sees native functions. They
  // wrap whatever the page has installed by then (a site's own alert), and are taken off only while they are still
  // the ones in place: a site that replaced them since keeps its own.
  const answers = { alert: ["alert", undefined], confirm: ["confirm: answered Cancel", false], prompt: ["prompt: answered Cancel", null] };
  // The native file pickers cannot be used by the worker: in a run tab they fail the way a cancelled picker does
  // (an AbortError), so the page takes its own fallback, usually an <input type="file"> the worker can reach.
  const PICKERS = ["showOpenFilePicker", "showDirectoryPicker"];
  const cancelled = () => Promise.reject(new DOMException("The user aborted a request.", "AbortError"));
  const wrapped = {};
  const ours = {};
  let shimmed = false;

  function sync() {
    if (active() === shimmed) return;
    shimmed = !shimmed;
    for (const name of [...Object.keys(answers), ...PICKERS]) {
      if (shimmed) {
        const before = window[name];
        if (typeof before !== "function") continue;
        wrapped[name] = before;
        ours[name] = function (message) {
          if (!active()) return before.apply(window, arguments);
          if (!answers[name]) return cancelled();
          note(answers[name][0], arguments.length ? String(message) : "");
          return answers[name][1];
        };
        install(name, ours[name]);
      } else if (ours[name] && window[name] === ours[name]) {
        install(name, wrapped[name]);
        ours[name] = null;
      }
    }
  }

  // documentElement can be replaced (document.open, a parser restart): the attribute observer follows it
  let watched = null;
  let attrObserver = null;
  function bind() {
    const root = document.documentElement;
    if (!root || root === watched) return;
    watched = root;
    if (attrObserver) attrObserver.disconnect();
    attrObserver = new MutationObserver(sync);
    attrObserver.observe(root, { attributes: true, attributeFilter: [ATTR] });
    sync();
  }

  if (typeof nativeOpen === "function") install("open", open);
  new MutationObserver(bind).observe(document, { childList: true });
  bind();
  window.addEventListener("pointerdown", dropBanner, true);
  window.addEventListener("keydown", dropBanner, true);
  // registered first, so it runs before the page's own listeners and its onbeforeunload
  window.addEventListener("beforeunload", (e) => { if (active()) e.stopImmediatePropagation(); }, true);
})();

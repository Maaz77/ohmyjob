// Apply Jobs: pop-ups as tabs (content script, isolated world).
//
// Asks the service worker whether this tab is in a run group. If yes, it sets the attribute
// data-aa-popups-as-tabs on <html> (page.js reads it when the page calls window.open), and in the
// top frame it keeps the title tag at the start of the tab title. In a run group it also names icon-only
// Google buttons, because read_page shows them without a name and the guard refuses a nameless submit button.

(() => {
  const ATTR = "data-aa-popups-as-tabs";
  const TAG_RE = /^\[AA (on|from \d+|grouped \d+|adopted \d+)\] /;
  const isTop = window === window.top;
  const GOOGLE_NAME = "Continue with Google";
  const GOOGLE_COLORS = [/#4285f4/, /#ea4335/, /#fbbc0[45]/, /#34a853/];
  let tries = 0;
  let naming = false;

  function whenRoot(callback) {
    if (document.documentElement) {
      callback(document.documentElement);
      return;
    }
    const observer = new MutationObserver(() => {
      if (document.documentElement) {
        observer.disconnect();
        callback(document.documentElement);
      }
    });
    observer.observe(document, { childList: true });
  }

  function keepTitle(tag) {
    const apply = () => {
      const title = document.title || "";
      if (!title.startsWith(tag)) {
        document.title = tag + title.replace(TAG_RE, "");
      }
    };
    const headObserver = new MutationObserver(apply);
    const attach = () => {
      if (!document.head) return false;
      headObserver.observe(document.head, { subtree: true, childList: true, characterData: true });
      return true;
    };
    if (!attach()) {
      whenRoot((root) => {
        const rootObserver = new MutationObserver(() => {
          if (attach()) {
            rootObserver.disconnect();
            apply();
          }
        });
        rootObserver.observe(root, { childList: true });
      });
    }
    apply();
  }

  function hasGoogleIcon(scope) {
    for (const img of scope.querySelectorAll("img")) {
      // the alt text or the file name, never the host: icons on googleusercontent.com are not Google buttons
      const file = (img.getAttribute("src") || "").split(/[?#]/)[0].split("/").pop();
      if (/google/i.test(`${img.alt} ${file}`)) return true;
    }
    for (const svg of scope.querySelectorAll("svg")) {
      const html = svg.outerHTML.toLowerCase();
      // the "G" logo: its name in an id or title, or at least 3 of its 4 colours
      if (html.includes("google") || GOOGLE_COLORS.filter((c) => c.test(html)).length >= 3) return true;
    }
    return false;
  }

  function nameGoogleButtons() {
    for (const el of document.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"]')) {
      if (el.textContent.trim() || el.value || el.hasAttribute("aria-label") || el.hasAttribute("aria-labelledby")) continue;
      const label = el.closest("label");
      if (label && label.textContent.trim()) continue;
      if (!hasGoogleIcon(label || el)) continue;
      el.setAttribute("aria-label", GOOGLE_NAME);
      if (label) label.setAttribute("aria-label", GOOGLE_NAME); // the button can be hidden: its label takes the click
    }
  }

  function keepNamingGoogleButtons(root) {
    if (naming) return;
    naming = true;
    let timer = null;
    new MutationObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(nameGoogleButtons, 150);
    }).observe(root, { subtree: true, childList: true });
    nameGoogleButtons();
  }

  function ask() {
    tries += 1;
    try {
      chrome.runtime.sendMessage({ type: "aa-check", href: location.href, top: isTop }, (resp) => {
        if (chrome.runtime.lastError || !resp) return;
        if (isTop && resp.tag) keepTitle(resp.tag);
        if (resp.enabled) {
          whenRoot((root) => {
            root.setAttribute(ATTR, "");
            keepNamingGoogleButtons(root);
          });
        } else if (resp.retry && tries < 5) {
          setTimeout(ask, 400);
        }
      });
    } catch (e) {
      // the extension was reloaded or is not available
    }
  }

  ask();
  // a tab that the extension moved back into a run group (/continue-applying) asks again
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "aa-recheck") {
      tries = 0;
      ask();
    }
  });
})();

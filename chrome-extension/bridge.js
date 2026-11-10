// ohmyjob: pop-ups as tabs (content script, isolated world).
//
// Asks the service worker whether this tab is in a run group. If yes, it sets the attribute
// data-aa-popups-as-tabs on <html> (page.js reads it when the page calls window.open), and in the
// top frame it keeps the title tag at the start of the tab title. In a run group it also names icon-only
// Google buttons, because read_page shows them without a name and the guard refuses a nameless submit button.

(() => {
  const ATTR = "data-aa-popups-as-tabs";
  const TAG_RE = /^\[AA (on( [0-9][\w.+-]*)?|from \d+|grouped \d+|adopted \d+)\](?: |$)/;
  const isTop = window === window.top;
  const GOOGLE_NAME = "Continue with Google";
  const GOOGLE_COLORS = [/#4285f4/, /#ea4335/, /#fbbc0[45]/, /#34a853/];
  const CONTROLS = 'button, [role="button"], input[type="submit"], input[type="button"], a, [role="link"]';
  const GOOGLE_TAG_RE = /\[AA g=\d+ named=\d+\]/;
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
      // the tab title is trimmed: a page without a title of its own shows the bare tag, which is right as it is
      if (title !== tag.trimEnd() && !title.startsWith(tag)) {
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

  function hasName(el) {
    return !!(el.textContent.trim() || el.value || el.hasAttribute("aria-label") || el.hasAttribute("aria-labelledby"));
  }

  function isClickable(el) {
    if (el.hasAttribute("tabindex") || el.hasAttribute("onclick")) return true;
    try {
      return getComputedStyle(el).cursor === "pointer";
    } catch (e) {
      return false;
    }
  }

  // A <div> or <span> around an icon: some sites build their Google button that way. Only these are looked at, never
  // every element of the page: getComputedStyle on the whole document ran on every mutation and froze big forms.
  function iconHolders() {
    const out = new Set();
    for (const node of document.querySelectorAll("svg, img")) {
      const holder = node.closest("div, span");
      if (holder) out.add(holder);
    }
    return out;
  }

  function nameGoogleButtons() {
    for (const el of document.querySelectorAll(CONTROLS)) {
      if (hasName(el)) continue;
      const label = el.closest("label");
      if (label && label.textContent.trim()) continue;
      if (!hasGoogleIcon(label || el)) continue;
      el.setAttribute("aria-label", GOOGLE_NAME);
      // the control can be hidden: its label takes the click
      if (label && !hasName(label)) label.setAttribute("aria-label", GOOGLE_NAME);
    }
    for (const el of iconHolders()) {
      if (hasName(el) || !isClickable(el) || !hasGoogleIcon(el)) continue;
      el.setAttribute("aria-label", GOOGLE_NAME);
      const label = el.closest("label");
      if (label && !hasName(label)) label.setAttribute("aria-label", GOOGLE_NAME);
    }
  }

  // The title tag of a job tab: how many Google controls the page has, and how many of them have a name. The guard
  // uses it to tell a page with a Google sign-in from one without (GOOGLE_MISSED in the ledger).
  function countGoogleControls() {
    let count = 0;
    let named = 0;
    const seen = new Set();
    const add = (el, isNamed) => {
      if (seen.has(el)) return;
      seen.add(el);
      count += 1;
      if (isNamed) named += 1;
    };
    const candidates = [...document.querySelectorAll(CONTROLS), ...iconHolders()];
    for (const el of candidates) {
      const label = el.closest("label");
      if (hasGoogleIcon(label || el)) {
        add(el, hasName(el) || !!(label && label.textContent.trim()));
        continue;
      }
      const text = `${el.textContent} ${el.getAttribute("aria-label") || ""} ${el.title || ""} ${el.getAttribute("alt") || ""}`;
      if (/google/i.test(text)) {
        add(el, hasName(el));
        continue;
      }
      let background = "";
      try {
        background = getComputedStyle(el).backgroundImage || "";
      } catch (e) {
        background = "";
      }
      // the host googleusercontent.com serves user pictures, not the Google logo
      if (/google/i.test(background) && !/googleusercontent\.com/i.test(background)) add(el, hasName(el));
    }
    // Google's own sign-in widget: its button lives in a cross-origin frame, so it has no name here
    for (const frame of document.querySelectorAll('iframe[src*="accounts.google.com/gsi/"]')) add(frame, false);
    return { count, named };
  }

  function updateGoogleTag() {
    if (!isTop) return;
    const title = document.title || "";
    if (TAG_RE.test(title)) return;  // the home tab and a sign-in tab keep their own tag
    const { count, named } = countGoogleControls();
    const tag = `[AA g=${count} named=${named}]`;
    const current = title.match(GOOGLE_TAG_RE);
    if (current && current[0] === tag) return;
    const cleaned = title.replace(GOOGLE_TAG_RE, "").trim();
    document.title = cleaned + (cleaned ? " " : "") + tag;
  }

  function keepNamingGoogleButtons(root) {
    if (naming) return;
    naming = true;
    let timer = null;
    new MutationObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        nameGoogleButtons();
        updateGoogleTag();
      }, 150);
    }).observe(root, { subtree: true, childList: true });
    nameGoogleButtons();
    updateGoogleTag();
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

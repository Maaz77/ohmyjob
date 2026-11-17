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
  const FRAME_MARK = "[AA frame]";
  const FRAME_MIN = 200;  // an application form is never smaller; a tracker pixel or an ad slot often is
  let tries = 0;
  let naming = false;
  let frameSizes = null;            // ResizeObserver: a frame that grows late is looked at again
  const framePlace = new WeakMap(); // frame -> the address the frame itself reported

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

  // read_page names a control from its own text, an aria-label or an alt, never from a child element, so a button
  // that holds its text in a span (Vuetify and friends) is nameless there and needs a label like an icon-only one.
  function ownText(el) {
    let text = "";
    for (const node of el.childNodes) if (node.nodeType === Node.TEXT_NODE) text += node.textContent;
    return text.trim();
  }

  function hasName(el) {
    return !!(ownText(el) || el.value || el.hasAttribute("aria-label") || el.hasAttribute("aria-labelledby"));
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

  // read_page cannot see into a cross-origin frame, so a form inside one is out of reach: the page shows only the
  // frame, and read_page names it from its title ("Greenhouse Job Board") or not at all. The address of the frame is
  // an attribute of the page itself, which this script may read, so it puts a link to it beside the frame. The
  // worker navigates its tab to that address and works on the form as a page of its own.
  //
  // A link, not a name on the frame: read_page cuts a name at 100 characters, which breaks the token such an address
  // usually carries, while it prints an href whole. The link is 1x1 and out of the flow, so the page looks the same.
  function markFrame(frame, url) {
    const before = frame.previousElementSibling;
    if (before && before.dataset && before.dataset.aaFrame === "1") {
      if (before.getAttribute("href") !== url.href) before.setAttribute("href", url.href);
      return;
    }
    const link = document.createElement("a");
    link.dataset.aaFrame = "1";
    link.setAttribute("href", url.href);
    link.setAttribute("aria-label", FRAME_MARK);
    link.textContent = FRAME_MARK;
    link.style.cssText = "position:absolute;width:1px;height:1px;overflow:hidden";
    if (frame.parentNode) frame.parentNode.insertBefore(link, frame);
  }

  function usableFrameUrl(raw) {
    let url;
    try {
      url = new URL(raw, location.href);
    } catch (e) {
      return null;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (url.origin === location.origin) return null;  // read_page sees a same-origin frame already
    return url;
  }

  function bigEnough(frame) {
    const box = frame.getBoundingClientRect();
    return box.width >= FRAME_MIN && box.height >= FRAME_MIN;
  }

  // A frame grows after it loads (Greenhouse sizes its embed to the form), and a style change is no change of the
  // tree, so the observer below never fires for it: without this the frame is measured once, while it is still
  // small, and never looked at again.
  function watchFrameSize(frame) {
    if (!frameSizes) return;
    try {
      frameSizes.observe(frame);
    } catch (e) {
      // a frame that cannot be observed: its src is still read on every pass
    }
  }

  function considerFrame(frame) {
    const url = framePlace.get(frame) || usableFrameUrl(frame.getAttribute("src"));
    if (url && bigEnough(frame)) markFrame(frame, url);
  }

  function nameFrames() {
    if (!frameSizes && typeof ResizeObserver === "function") {
      frameSizes = new ResizeObserver((entries) => {
        for (const entry of entries) considerFrame(entry.target);
      });
    }
    for (const frame of document.querySelectorAll("iframe, frame")) {
      watchFrameSize(frame);
      considerFrame(frame);
    }
    askFramesWhereTheyAre();
  }

  // Both halves wait for the same answer from the service worker, so a frame can report before the page above it
  // listens. The page asks again on every naming pass, and a frame that is listening answers.
  function askFramesWhereTheyAre() {
    if (!isTop) return;
    for (const frame of document.querySelectorAll("iframe, frame")) {
      try {
        if (frame.contentWindow) frame.contentWindow.postMessage({ aaFrameAsk: 1 }, "*");
      } catch (e) {
        // a frame that cannot be reached
      }
    }
  }

  // The src attribute is the frame's FIRST address. A frame that its own script then navigates, and a srcdoc frame,
  // have no src worth following, so each frame of a run tab tells the page above it where it is now. This is the
  // frame's half: it runs in every frame of the group, the top one excepted.
  function reportFrameUrl() {
    const post = () => {
      try {
        parent.postMessage({ aaFrame: location.href }, "*");
      } catch (e) {
        // a parent that cannot be reached: the src attribute is all the page above gets
      }
    };
    post();
    window.addEventListener("load", post);
    window.addEventListener("hashchange", post);
    window.addEventListener("popstate", post);
    window.addEventListener("message", (event) => {
      if (event.data && event.data.aaFrameAsk) post();
    });
  }

  // The page's half: a message counts only when it comes from a frame of this page, which the window identity of the
  // sender proves. A page script cannot name a frame it is not inside.
  function listenForFrameUrls() {
    window.addEventListener("message", (event) => {
      const data = event.data;
      if (!data || typeof data.aaFrame !== "string") return;
      const url = usableFrameUrl(data.aaFrame);
      if (!url) return;
      for (const frame of document.querySelectorAll("iframe, frame")) {
        if (frame.contentWindow !== event.source) continue;
        framePlace.set(frame, url);  // kept: the frame can still be too small to judge
        watchFrameSize(frame);
        considerFrame(frame);
        return;
      }
    });
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
        nameFrames();
        updateGoogleTag();
      }, 150);
    }).observe(root, { subtree: true, childList: true });
    nameGoogleButtons();
    nameFrames();
    updateGoogleTag();
  }

  function ask() {
    tries += 1;
    try {
      chrome.runtime.sendMessage({ type: "aa-check", href: location.href, top: isTop }, (resp) => {
        if (chrome.runtime.lastError || !resp) return;
        if (isTop && resp.tag) keepTitle(resp.tag);
        if (resp.enabled) {
          if (isTop) listenForFrameUrls();
          else reportFrameUrl();
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

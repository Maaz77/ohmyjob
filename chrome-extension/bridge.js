// ohmyjob: pop-ups as tabs (content script, isolated world).
//
// Asks the service worker whether this tab is in a run group. If yes, it sets the attribute
// data-aa-popups-as-tabs on <html> (page.js reads it when the page calls window.open), and in the
// top frame it keeps the title tag at the start of the tab title. In a run group it also names icon-only
// Google buttons, because read_page shows them without a name and the guard refuses a nameless submit button.
// It also writes the state of form controls (ticked, required, value ...) into their aria-label, because read_page
// prints no state at all, and it leaves the run group again when the tab is moved out of it.

(() => {
  // An extension reload leaves every open tab running the OLD copy of this file, and `chrome.scripting` then puts
  // this one in beside it. A document must run one copy only: the first to arrive writes its version on <html>, and
  // a later copy of the same version stops here. A NEWER copy takes over, because only it knows the newer rules.
  const VERSION = chrome.runtime.getManifest().version;
  const mark = document.documentElement;
  if (mark) {
    if (mark.dataset.aaBridge === VERSION) return;
    mark.dataset.aaBridge = VERSION;
  }
  const ATTR = "data-aa-popups-as-tabs";
  const TAG_RE = /^\[AA (on( [0-9][\w.+-]*)?|from \d+|grouped \d+|adopted \d+)\](?: |$)/;
  const isTop = window === window.top;
  const GOOGLE_NAME = "Continue with Google";
  // A Google app is no sign-in option ("Upload from Google Drive"). The text is split into words first, because an
  // icon file names the app as one word ("GoogleDrive", "ic_google_drive.svg"): "_" is a word character, and a
  // name folded to one word has no boundary inside it at all. What no split can separate is the app glued to its
  // own "google" or to a bare "g" ("icongoogledrive.png", "gdrive"). Not a plain substring, which would read an
  // app name out of Paylocity, display, Cloudflare and driver. The same ten names are written by hand in
  // scripts/lexicon.py (GOOGLE_PRODUCTS, with the same split): change both together.
  const PRODUCTS = "drive|docs|maps|pay|play|photos|sheets|calendar|meet|cloud";
  const GOOGLE_PRODUCTS = new RegExp(`\\b(?:${PRODUCTS})\\b|google[\\s\\-.]*(?:${PRODUCTS})|\\bg(?:${PRODUCTS})`, "i");
  const splitWords = (text) => (text || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[\s\-_.]+/g, " ");
  const isGoogleSignin = (text) => /google/i.test(text) && !GOOGLE_PRODUCTS.test(splitWords(text));
  const GOOGLE_COLORS = [/#4285f4/, /#ea4335/, /#fbbc0[45]/, /#34a853/];
  const CONTROLS = 'button, [role="button"], input[type="submit"], input[type="button"], a, [role="link"]';
  const GOOGLE_TAG_RE = /\[AA g=\d+ named=\d+( v=[0-9][\w.+-]*)?\]/;
  const FRAME_MARK = "[AA frame]";
  const FRAME_MIN = 200;  // an application form is never smaller; a tracker pixel or an ad slot often is
  let tries = 0;
  let naming = false;
  let active = false;               // false once the tab left the run group: the passes stop
  let wired = false;                // the frame listeners are installed once, however often the tab is rechecked
  let timer = null;                 // the one debounced pass
  let due = false;                  // the maximum wait is over: the timer is left alone from then on
  let firstAt = 0;                  // when the oldest trigger of the pending pass came: a busy page cannot starve it
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
      if (isGoogleSignin(`${img.alt} ${file}`)) return true;
    }
    for (const svg of scope.querySelectorAll("svg")) {
      const html = svg.outerHTML.toLowerCase();
      // An app name is looked for in the parts that NAME the icon, never in the whole markup: every attribute
      // value stands in that, and `preserveAspectRatio="xMidYMid meet"` alone would make the Google logo a Meet
      // mark (`animation-play-state` is the next one). The colour count runs through the same test: Photos and the
      // Play triangle carry the four brand colours as the "G" logo does, so 4 of 4 is no sign-in evidence on a
      // control whose mark names an app.
      if (GOOGLE_PRODUCTS.test(splitWords(iconNames(svg, scope)))) continue;
      // the "G" logo: its name in an id or title, or at least 3 of its 4 colours
      if (/google/i.test(html) || GOOGLE_COLORS.filter((c) => c.test(html)).length >= 3) return true;
    }
    return false;
  }

  // what names an element, as a page writes it: no text node, no attribute that styles or lays out
  const NAME_ATTRS = ["id", "class", "aria-label", "data-testid", "data-name"];
  function nameParts(el) {
    return NAME_ATTRS.map((a) => el.getAttribute(a) || "").join(" ");
  }

  // the names an icon carries: the control around it, the svg, and the nodes inside it -- a drawing program writes
  // the app's name on a group or a path ("<g id='google-drive'>"), not only on the root
  function iconNames(svg, scope) {
    const parts = [nameParts(scope)];
    for (const el of [svg, ...svg.querySelectorAll("*")]) parts.push(nameParts(el));
    for (const el of svg.querySelectorAll("title, desc")) parts.push(el.textContent);
    const use = svg.querySelector("use");
    if (use) parts.push(use.getAttribute("href") || use.getAttribute("xlink:href") || "");
    return parts.join(" ");
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

  // hasName, but a label this script wrote does not count: the naming pass asks it again on every pass, so a name
  // that no longer applies goes (see siteLabel, restoreLabel).
  function hasSiteName(el) {
    return !!(ownText(el) || el.value || siteLabel(el) !== null || el.hasAttribute("aria-labelledby"));
  }

  // A name for a control that has none, read off its icon. Only the sources read_page cannot see (an svg <title>,
  // a sprite id, a test id, a class): a title attribute or an img alt already name the control there. The name is a
  // guess, so the guard gets it marked "[icon] " (asked about, never allowed on its own); "no name" stays the honest
  // answer whenever no source gives a word.
  const ICON_EDGE = /^(icon|ico|btn|button)$/;
  const ICON_TAIL = /^(button|btn|icon)$/;
  const STYLE_WORDS = new Set(["primary", "secondary", "default", "danger", "success", "warning", "info", "light", "dark",
    "link", "outline", "small", "large", "block", "round", "rounded", "flat", "raised", "active", "disabled", "sm", "md", "lg", "xs"]);
  const FRAMEWORK_CLASS = /^(mui|jss|css-|ant-|v-|ng-|sc-)/i;

  function iconWords(raw) {
    const words = (raw || "").replace(/([a-z])([A-Z])/g, "$1 $2").split(/[\s\-_.]+/).map((w) => w.toLowerCase()).filter(Boolean);
    while (words.length && ICON_EDGE.test(words[0])) words.shift();
    while (words.length && ICON_TAIL.test(words[words.length - 1])) words.pop();
    const text = words.join(" ");
    // word-like: a vowel, at least 3 characters, not all digits
    return text.length >= 3 && /[aeiouy]/.test(text) && /\D/.test(text) ? text[0].toUpperCase() + text.slice(1) : "";
  }

  function iconName(el) {
    if (el.tagName === "INPUT" || wordsOf(el, "svg, style, script")) return "";  // it has a name or visible text
    // A title attribute names the control to read_page, and the alt of an image inside it reaches the guard as the
    // line inside the button, which a nameless control takes as its name. A guessed name is written into
    // aria-label, which outranks both, and is not empty, so it would take their place: a trash button with
    // title="Delete" and class="trash" would reach the guard as "Trash", and the word it hard-refuses on would be
    // gone. So a control that is already named this way keeps the name it has, as the Google arm above does.
    if (el.hasAttribute("title")) return "";
    for (const img of el.querySelectorAll("img")) if ((img.getAttribute("alt") || "").trim()) return "";
    const title = el.querySelector("svg title");
    const fromTitle = title ? iconWords(title.textContent) : "";
    if (fromTitle) return fromTitle;
    const use = el.querySelector("svg use");
    const href = use ? use.getAttribute("href") || use.getAttribute("xlink:href") || "" : "";
    const fromUse = iconWords(href.split("#")[1]);
    if (fromUse) return fromUse;
    const idEl = el.hasAttribute("data-testid") ? el : el.querySelector("[data-testid]");
    const fromTestId = idEl ? iconWords(idEl.getAttribute("data-testid")) : "";
    if (fromTestId) return fromTestId;
    // a class names the control only when exactly one class is no framework prefix, no hash and no styling word
    const classes = [...el.classList].filter((c) => !FRAMEWORK_CLASS.test(c) && !/\d/.test(c))
      .map(iconWords).filter((w) => w && !STYLE_WORDS.has(w.toLowerCase()));
    return classes.length === 1 ? classes[0] : "";
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
  //
  // A frame with no address at all (srcdoc, about:blank) gets a tag with no href: an <a> without href still shows in
  // read_page's interactive view, so the worker learns that the form is in a frame it cannot open.
  const NO_ADDRESS = "[AA frame: no address]";

  function markFrame(frame, url) {
    const kind = url ? "1" : "none";
    const before = frame.previousElementSibling;
    const have = before && before.dataset ? before.dataset.aaFrame : null;
    if (have === kind) {
      if (url && before.getAttribute("href") !== url.href) before.setAttribute("href", url.href);
      return;
    }
    if (have) before.remove();  // a frame that got an address (or lost it) changes its marker
    const link = document.createElement("a");
    link.dataset.aaFrame = kind;
    if (url) link.setAttribute("href", url.href);
    link.setAttribute("aria-label", url ? FRAME_MARK : NO_ADDRESS);
    link.textContent = url ? FRAME_MARK : NO_ADDRESS;
    link.style.cssText = "position:absolute;width:1px;height:1px;overflow:hidden";
    if (frame.parentNode) frame.parentNode.insertBefore(link, frame);
  }

  // base: the document the address was read from, which is not this page for a frame inside a frame.
  function usableFrameUrl(raw, base) {
    if (!raw) return null;  // new URL(null) would be ".../null"
    let url;
    try {
      url = new URL(raw, base || location.href);
    } catch (e) {
      return null;
    }
    // read_page reads no frame at all, a frame of the same site included
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    // A frame the page fills itself (about:blank, then document.open/write) takes the PAGE's address as its own: a link
    // to it would send the worker in a circle, so that is no address.
    return url.href.split("#")[0] === location.href.split("#")[0] ? null : url;
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

  // The address the frame reported, else where a frame of this site is now, else its src attribute.
  function frameAddress(frame) {
    const reported = framePlace.get(frame);
    if (reported) return reported;
    let here = null;
    try {
      here = frame.contentWindow.location.href;  // throws for a frame of another site
    } catch (e) {
      here = null;
    }
    return usableFrameUrl(here) || usableFrameUrl(frame.getAttribute("src")) || deferredFrameUrl(frame)
      || addressInside(frame, 2);
  }

  // A consent tool that blocks an embed until the visitor agrees takes the address out of `src` and keeps it in an
  // attribute of its own: data-src (OneTrust, Klaro), data-cookieblock-src (Cookiebot), data-src-cmplz (Complianz),
  // data-cmp-src (consentmanager), uc-src (Usercentrics, not a data-* name at all). No list of tools here: the names
  // differ per tool, several tools publish none, and the next tool will invent another. The one thing they share is
  // "src" in the attribute's name, so that is what is looked for. This gives no consent: it only offers the worker
  // the address the page was going to load anyway, which it opens as a page of its own.
  //
  // The NAME must hold "src" as a word of its own. The worker navigates its tab to whatever this returns, so taking
  // any attribute that merely looks like a URL would offer it a data-poster image or an analytics beacon as the
  // application form. A word, not a substring: `srcdoc` holds a document, not an address, and usableFrameUrl resolves
  // whatever it is given against this page, so `srcdoc="<p>editor</p>"` would come back as a usable-looking URL.
  const DEFERRED_SRC = /(^|[^a-z])src([^a-z]|$)/i;

  function deferredFrameUrl(frame) {
    for (const attr of frame.attributes) {
      if (attr.name === "src" || !DEFERRED_SRC.test(attr.name)) continue;
      const url = usableFrameUrl(attr.value);
      if (url) return url;
    }
    return null;
  }

  // A frame with no address of its own (srcdoc, about:blank, written by the page) inherits this page's origin, so
  // its document can be read from here. The form is often one level deeper, and a link to a frame it cannot open is
  // no use to the worker, so take the address of the first big frame inside it instead.
  function addressInside(frame, depth) {
    if (depth <= 0) return null;
    let doc = null;
    try {
      doc = frame.contentDocument;  // null for a frame of another site
    } catch (e) {
      return null;
    }
    if (!doc) return null;
    for (const inner of doc.querySelectorAll("iframe, frame")) {
      if (!bigEnough(inner)) continue;
      const url = usableFrameUrl(inner.getAttribute("src"), inner.ownerDocument.baseURI)
        || addressInside(inner, depth - 1);
      if (url) return url;
    }
    return null;
  }

  function considerFrame(frame) {
    if (active && bigEnough(frame)) markFrame(frame, frameAddress(frame));
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

  // An app-store badge ("Get it on Google Play") carries a Google image but signs nobody in. True for a link to a
  // store, or for anything inside one.
  const STORE_HOSTS = new Set(["play.google.com", "apps.apple.com", "itunes.apple.com"]);
  function inStoreLink(el) {
    const link = el.closest("a[href]");
    if (!link) return false;
    try {
      return STORE_HOSTS.has(new URL(link.getAttribute("href"), location.href).hostname);
    } catch (e) {
      return false;
    }
  }

  // Every name written here is "[icon] <name>", through the same record as the field marks (OURS, ORIG): a name that
  // stops applying (the button now says "Submit application") is taken back by the next pass, and leaveRun takes
  // all of them back.
  function setIconName(el, name) {
    if (name) writeLabel(el, `[icon] ${name}`);
    else restoreLabel(el);
  }

  // ponytail: MARK_CAP controls per pass, and hasGoogleIcon walks the icons of each one and their nodes. A form
  // with more controls than that keeps the rest nameless; measure a big form before trading the ceiling for a
  // cheaper walk.
  function nameGoogleButtons() {
    let seen = 0;
    // Every label is written once, after both loops: a label holds more than one control, and the second one --
    // in either loop -- would otherwise take back the name the Google control gave it. A name wins over a "".
    const labels = new Map();
    const wants = (el, name) => {
      const label = el.closest("label");
      if (!label) return;
      if (name && !hasSiteName(label)) labels.set(label, name);
      else if (!labels.has(label)) labels.set(label, "");
    };
    for (const el of document.querySelectorAll(CONTROLS)) {
      if (++seen > MARK_CAP) break;
      const label = el.closest("label");
      let name = "";
      if (!hasSiteName(el) && !inStoreLink(el) && !(label && label.textContent.trim())) {
        name = hasGoogleIcon(label || el) ? GOOGLE_NAME : iconName(el);
      }
      setIconName(el, name);
      wants(el, name === GOOGLE_NAME ? name : "");  // the control can be hidden: its label takes the click
    }
    for (const el of iconHolders()) {
      if (el.matches(CONTROLS)) continue;  // a real control: the loop above
      const google = !hasSiteName(el) && isClickable(el) && !inStoreLink(el) && hasGoogleIcon(el);
      setIconName(el, google ? GOOGLE_NAME : "");
      wants(el, google ? GOOGLE_NAME : "");
    }
    for (const [label, name] of labels) setIconName(label, name);
  }

  // A div or span whose only sign of being a control is a click handler sits in no role read_page lists, so the
  // worker never sees it at all: several traces show a missing Apply or Next that way. It gets role="button", which
  // puts it in the interactive view, and read_page then names it from its own text.
  //
  // Candidates by attribute only. [onclick] and [tabindex] cost a selector match, while the cursor:pointer arm of
  // isClickable needs getComputedStyle, which the comment at iconHolders records as having frozen big forms when it
  // ran over the whole document on every mutation. An icon-only one has no text to be named from, so it goes through
  // iconName like any icon button, and stays nameless when no source gives a word.
  //
  // ponytail: a React div whose handler is on addEventListener and whose pointer cursor comes from a class has
  // NEITHER attribute, so it is not covered. Upgrade path: time a pass over `div, span` with getComputedStyle
  // against a big-form fixture first -- that walk is the measured freeze above, so widening needs a number, not a
  // guess.
  const PLAIN_CLICKABLE = "div[onclick], span[onclick], div[tabindex], span[tabindex]";

  function nameClickables() {
    let seen = 0;
    for (const el of document.querySelectorAll(PLAIN_CLICKABLE)) {
      if (++seen > MARK_CAP) break;
      // a real control, or a role the site set: both are in read_page's view already
      if (el.matches(CONTROLS) || (el.hasAttribute("role") && !el.hasAttribute(ROLE_OURS))) continue;
      el.setAttribute("role", "button");
      el.setAttribute(ROLE_OURS, "1");
      if (!hasSiteName(el)) setIconName(el, iconName(el));
    }
  }

  function dropPlainRoles() {
    for (const el of document.querySelectorAll(`[${ROLE_OURS}]`)) {
      el.removeAttribute("role");
      el.removeAttribute(ROLE_OURS);
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
      if (inStoreLink(el)) continue;
      const label = el.closest("label");
      if (hasGoogleIcon(label || el)) {
        add(el, hasName(el) || !!(label && label.textContent.trim()));
        continue;
      }
      const text = `${el.textContent} ${el.getAttribute("aria-label") || ""} ${el.title || ""} ${el.getAttribute("alt") || ""}`;
      if (isGoogleSignin(text)) {
        add(el, hasName(el));
        continue;
      }
      let background = "";
      try {
        background = getComputedStyle(el).backgroundImage || "";
      } catch (e) {
        background = "";
      }
      // the host googleusercontent.com serves user pictures, not the Google logo; and the address of an app's icon
      // ("/img/google-drive.svg") is as little a sign-in option here as an app name in the text above
      if (isGoogleSignin(background) && !/googleusercontent\.com/i.test(background)) add(el, hasName(el));
    }
    // Google's own sign-in widget: its button lives in a cross-origin frame, so it has no name here
    for (const frame of document.querySelectorAll('iframe[src^="https://accounts.google.com/gsi/"]')) add(frame, false);
    return { count, named };
  }

  function updateGoogleTag() {
    if (!isTop) return;
    const title = document.title || "";
    if (TAG_RE.test(title)) return;  // the home tab and a sign-in tab keep their own tag
    const { count, named } = countGoogleControls();
    // the version of THIS file, not the service worker's: `AA adopted` reads it to see that a tab still runs an old
    // copy, which is what an extension reload leaves behind in every open tab
    const tag = `[AA g=${count} named=${named} v=${VERSION}]`;
    const current = title.match(GOOGLE_TAG_RE);
    if (current && current[0] === tag) return;
    const cleaned = title.replace(GOOGLE_TAG_RE, "").trim();
    document.title = cleaned + (cleaned ? " " : "") + tag;
  }

  // Field marks. read_page prints no state (ticked, required, disabled ...) and names a textbox by its value, so the
  // state goes into the aria-label, which it does honour: "[hidden] [x] [req] [disabled] [invalid] <name>". The guard strips
  // the marks again (aa_lib.parse_refs). No getComputedStyle here, for the reason given at iconHolders.
  // Never on a pressable control (button, link ...): the label replaces its live name, and a pass that comes late would
  // leave a stale name that the guard trusts ("Next" on a button that now says "Submit application").
  const OURS = "data-aa-label";       // the exact label this script wrote
  const ROLE_OURS = "data-aa-role";   // a role this script wrote on a plain clickable (leaveRun removes it)
  const ORIG = "data-aa-orig-label";  // the site's own label, put back when no mark is left
  const MARK_CAP = 3000;
  const ROLE_KIND = {
    checkbox: "choice", radio: "choice", switch: "choice", menuitemcheckbox: "choice", menuitemradio: "choice",
    textbox: "text", combobox: "text", searchbox: "text", spinbutton: "text", option: "option",
  };
  const FIELDS = "input, textarea, " + Object.keys(ROLE_KIND).map((r) => `[role="${r}"]`).join(", ");
  const TEXT_TYPES = /^(text|email|tel|url|number|search|date|month|week|time|datetime-local|password)$/;
  const squash = (s) => (s || "").replace(/\s+/g, " ").trim();
  const clip = (s, n) => (s.length > n ? s.slice(0, n) + "\u2026" : s);

  // select is left out: read_page ignores its aria-label and names it by the selected option
  function fieldKind(el) {
    if (el.tagName === "SELECT" || el.tagName === "OPTION") return null;
    const byRole = ROLE_KIND[(el.getAttribute("role") || "").toLowerCase()];
    if (byRole) return byRole === "option" && el.getAttribute("aria-selected") !== "true" ? null : byRole;
    if (el.tagName === "TEXTAREA") return "text";
    if (el.tagName !== "INPUT") return null;
    if (el.type === "checkbox" || el.type === "radio") return "choice";
    return TEXT_TYPES.test(el.type) ? "text" : null;
  }

  // A text field no person can see or reach: display:none / visibility:hidden, a zero box, or off the DOCUMENT (not the
  // viewport: a field below the fold or scrolled away is real). opacity is not tested: real controls are styled opacity 0.
  // ponytail: one box read per text field per pass, the only layout read in this pass; checkVisibility answers the two
  // common cases first, so the read happens only for a field that is laid out. MARK_CAP bounds it.
  function isHidden(el) {
    // both spellings: the option was renamed (checkVisibilityCSS -> visibilityProperty) and an unknown key is
    // ignored, so this is right on an old Chromium and a new one. opacity is left out on purpose.
    if (!el.checkVisibility({ visibilityProperty: true, checkVisibilityCSS: true })) return true;
    const r = el.getBoundingClientRect();
    return (r.width === 0 && r.height === 0) || r.left + scrollX + r.width <= 0 || r.top + scrollY + r.height <= 0;
  }

  function fieldMarks(el, kind, reqRadios) {
    const flag = (name) => el.getAttribute(name) === "true";
    const form = kind !== "option";
    const on = kind === "option" || (kind === "choice" && (el.checked === true || flag("aria-checked")));
    const group = el.type === "radio" && el.name && reqRadios.get(el.form || document);
    const req = form && (el.required === true || flag("aria-required") || !!(group && group.has(el.name)));
    const off = form && (flag("aria-disabled") || el.matches(":disabled"));
    return (kind === "text" && isHidden(el) ? "[hidden] " : "") + (on ? "[x] " : "") + (req ? "[req] " : "") + (off ? "[disabled] " : "") + (form && flag("aria-invalid") ? "[invalid] " : "");
  }

  function requiredRadios() {
    const found = new Map();
    for (const radio of document.querySelectorAll("input[type=radio][required]")) {
      const key = radio.form || document;
      if (!found.has(key)) found.set(key, new Set());
      found.get(key).add(radio.name);
    }
    return found;
  }

  // the site's own label: what is in aria-label unless this script wrote it (then the saved original), null if none
  function siteLabel(el) {
    const now = el.getAttribute("aria-label");
    const ours = el.getAttribute(OURS);
    return ours !== null && now === ours ? el.getAttribute(ORIG) : now;
  }

  function idsText(el, attr) {
    const ids = (el.getAttribute(attr) || "").split(/\s+/).filter(Boolean);
    return squash(ids.map((id) => (document.getElementById(id) || {}).textContent || "").join(" "));
  }

  // the words of an element, without the text of the descendants that match `skip`
  function wordsOf(root, skip) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => n.nodeType === Node.TEXT_NODE ? NodeFilter.FILTER_ACCEPT
        : n.matches(skip) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP,
    });
    let text = "";
    while (walker.nextNode()) text += walker.currentNode.textContent;
    return squash(text);
  }

  // a label's own words: not the value or text of a control nested in it
  const labelText = (label) => wordsOf(label, "input, select, textarea, button");

  // control -> the words of its labels; built once per pass, so no field asks for el.labels
  let labelOf = new Map();
  function collectLabels() {
    labelOf = new Map();
    let seen = 0;
    for (const label of document.querySelectorAll("label")) {
      if (++seen > MARK_CAP) break;
      const control = label.control;
      if (control) labelOf.set(control, (labelOf.get(control) || "") + " " + labelText(label));
    }
  }

  function baseName(el, kind) {
    const site = squash(siteLabel(el));
    if (site) return site;
    const named = idsText(el, "aria-labelledby") || squash(labelOf.get(el)) || squash(el.title);
    if (named) return named;
    if (kind === "text") return squash(el.getAttribute("placeholder"));
    const value = typeof el.value === "string" ? el.value : "";
    return (kind === "choice" && value && value !== "on" ? value : "") || clip(squash(el.textContent), 80);
  }

  // "<question> / <option>" for a radio; in a table the option is the column header
  function radioName(el, base) {
    const legend = el.closest("fieldset") && el.closest("fieldset").querySelector(":scope > legend");
    const group = el.closest('[role="radiogroup"]');
    const row = el.closest("tr");
    let question = legend ? squash(legend.textContent) : "";
    if (!question && group) question = squash(group.getAttribute("aria-label")) || idsText(group, "aria-labelledby");
    let option = base;
    if (!question && row && row.cells[0]) {
      question = squash(row.cells[0].textContent);
      option = columnHeader(el, row) || base;
    }
    return question && option ? `${clip(question, 55)} / ${option}` : base;
  }

  function columnHeader(el, row) {
    const cell = el.closest("td, th");
    const table = row.closest("table");
    // one radio per cell: a header cannot name two options
    if (!cell || !table || cell.cellIndex === 0 || cell.querySelectorAll('input[type="radio"], [role="radio"]').length !== 1) return "";
    for (const other of table.rows) {
      const head = other.cells[cell.cellIndex];
      if (head && head.tagName === "TH") return squash(head.textContent);
    }
    return "";
  }

  function textValue(el) {
    if (el.type === "password") return "";  // never a secret
    // a role=textbox or role=combobox on a div or a button holds its value as text
    // not its own open listbox, and not a placeholder (data-placeholder: Radix and friends) shown as text
    if (el.tagName !== "INPUT" && el.tagName !== "TEXTAREA") {
      if (el.hasAttribute("data-placeholder") || el.querySelector("[data-placeholder]")) return "";
      return clip(wordsOf(el, "input, select, textarea, button, [role=listbox], [role=option]"), 40);
    }
    return clip(el.value.trim().replace(/[\r\n]+/g, " "), 40);
  }

  function fieldName(el, kind, value, base) {
    if (kind === "text") return base && value ? `${base} = ${value}` : base || value;
    return el.type === "radio" || el.getAttribute("role") === "radio" ? radioName(el, base) : base;
  }

  function writeLabel(el, label) {
    const text = label.length > 100 ? label.slice(0, 99) + "\u2026" : label;
    if (el.getAttribute("aria-label") === text && el.getAttribute(OURS) === text) return;
    const site = siteLabel(el);
    if (site === null) el.removeAttribute(ORIG);
    else el.setAttribute(ORIG, site);
    el.setAttribute("aria-label", text);
    el.setAttribute(OURS, text);
  }

  function restoreLabel(el) {
    const ours = el.getAttribute(OURS);
    if (ours === null) return;
    if (el.getAttribute("aria-label") === ours) {
      const orig = el.getAttribute(ORIG);
      if (orig === null) el.removeAttribute("aria-label");
      else el.setAttribute("aria-label", orig);
    }
    el.removeAttribute(OURS);
    el.removeAttribute(ORIG);
  }

  function markFields() {
    const reqRadios = requiredRadios();
    collectLabels();
    let seen = 0;
    for (const el of document.querySelectorAll(FIELDS)) {
      // ponytail: at most 3000 controls per pass, so a huge page cannot freeze; raise it or mark only what changed
      if (++seen > MARK_CAP) break;
      const kind = fieldKind(el);
      if (!kind) {
        restoreLabel(el);
        continue;
      }
      const marks = fieldMarks(el, kind, reqRadios);
      const value = kind === "text" ? textValue(el) : "";
      const base = baseName(el, kind);
      const name = fieldName(el, kind, value, base);
      // No name: never a label of marks only. Nothing to add to the site's own aria-label: it stays. Any other
      // improved name (a <label>, a placeholder, a question) is kept even with no mark.
      if (!name || (!marks && name === base && squash(siteLabel(el)))) restoreLabel(el);
      else writeLabel(el, marks + name);
    }
  }

  // One debounced pass for every change: new nodes, a click or a keystroke, and the attributes that carry state.
  // aria-label is NOT watched: this script writes it, and watching it would loop. "hidden" is: it reveals a field and
  // no page rewrites it in a loop. style and class are NOT: a busy page (a spinner, an animation, a sticky bar)
  // rewrites them all the time, the 1 s starvation guard would then run a pass in every worker tab every second, and
  // each pass now reads a box per text field (isHidden). A collapsed section is opened by a click, and a click
  // schedules a pass, so a field revealed that way loses its mark anyway.
  // ponytail: a field revealed by a timer alone, with no click and no new node, keeps a stale [hidden] mark until the
  // next trigger. The mark only refuses a fill, so the worker reports the field instead of filling it. Watch style and
  // class (scoped away from [data-aa-shadow], whose style every pass rewrites) if a real form ever needs it.
  const STATE_ATTRS = ["aria-checked", "aria-pressed", "aria-selected", "aria-disabled", "aria-invalid",
    "aria-required", "disabled", "required", "checked", "hidden"];

  // Shadow-DOM proxies. read_page does not enter a shadow root, so a form built from web components (SmartRecruiters'
  // spl-* elements) is an empty page to the worker. Flattening the shadow DOM would break such a page (thousands of
  // slots and adopted style sheets), so each real control inside an OPEN shadow root gets a light-DOM twin instead,
  // laid exactly over it, and the twin is what read_page lists.
  //
  // opacity 0.01, not 0: read_page skips an element with opacity 0. pointer-events none: Claude in Chrome clicks a ref
  // by its screen coordinate, so the click passes the twin and reaches the REAL control, and a candidate can still use
  // the page by hand. What a coordinate cannot carry (typing, a file) is forwarded by the listeners in makeProxy.
  //
  // Only controls within one viewport of the visible area get a twin: read_page lists only the viewport anyway, and a
  // 245-option country list must not flood it. A name comes from the real control; a nameless one stays nameless, so
  // the guard's nameless-submit refusal still fires. markFields then marks the twins like any light-DOM control.
  const INTERACTIVE = 'input, textarea, select, button, a[href], [role="button"], [role="link"], [role="checkbox"], ' +
    '[role="radio"], [role="switch"], [role="option"], [role="combobox"], [role="textbox"]';
  const SHADOW_CAP = 200;
  const SHADOW_STYLE = "position:absolute;margin:0;padding:0;border:0;background:transparent;opacity:0.01;" +
    "pointer-events:none;z-index:2147483646";
  const MIRRORED_ARIA = ["aria-checked", "aria-selected", "aria-disabled", "aria-required", "aria-invalid"];
  let shadowBox = null;               // the one <div data-aa-shadow> that holds every twin
  let shadowSeen = new WeakSet();     // shadow roots that already have an observer
  let shadowWatch = null;             // MutationObserver over the shadow roots: the page-level one cannot see into them
  const twinOf = new WeakMap();       // real control -> its twin
  const twinInfo = new WeakMap();     // twin -> { real, base }: the real control and the name the guard may have approved
  const shadowSizes = new WeakMap();  // select twin -> the option list it was built from

  // text across shadow boundaries: an spl-dropdown-item holds its words in a further nested shadow root
  function deepText(el) {
    let text = "";
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (text.length > 400) return;  // a host must not hand over the whole form
        if (child.nodeType === Node.TEXT_NODE) text += child.textContent + " ";
        else if (child.nodeType === Node.ELEMENT_NODE && !/^(SCRIPT|STYLE|TEMPLATE)$/.test(child.tagName)) {
          if (child.shadowRoot) walk(child.shadowRoot);
          walk(child);
        }
      }
    };
    if (el.shadowRoot) walk(el.shadowRoot);
    walk(el);
    return squash(text);
  }

  // label, aria-label, aria-labelledby, own text, then the text of the shadow host (<spl-button>Next</spl-button>)
  function shadowName(el) {
    let name = el.labels && el.labels[0] ? deepText(el.labels[0]) : "";
    name = name || squash(el.getAttribute("aria-label"));
    if (!name) {
      const root = el.getRootNode();
      const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
      if (root.getElementById) name = squash(ids.map((id) => { const n = root.getElementById(id); return n ? deepText(n) : ""; }).join(" "));
    }
    if (!name && !/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) name = deepText(el);
    if (!name && el.getRootNode().host) name = deepText(el.getRootNode().host);
    name = clip(name, 80);
    // aa_lib.parse_refs reads a name that starts with "[AA " as a marker of the extension, not a control of the page
    return name.startsWith("[AA ") ? " " + name : name;
  }

  // The native setter, so a framework that wraps the value property of the real input sees the change.
  function setValue(real, value) {
    const own = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(real), "value");
    if (own && own.set) own.set.call(real, value);
    else real.value = value;
  }

  function forward(real, type) {
    real.dispatchEvent(new Event(type, { bubbles: true, composed: true }));
  }

  function makeProxy(real) {
    const tag = real.tagName;
    let twin;
    if (tag === "INPUT") {
      twin = document.createElement("input");
      twin.type = real.type;  // the property is already normalised: an unknown type is "text"
    } else if (tag === "TEXTAREA") {
      twin = document.createElement("textarea");
    } else if (tag === "SELECT") {
      twin = document.createElement("select");
    } else if (tag === "BUTTON") {
      twin = document.createElement("button");
      twin.type = real.type;  // "submit" without a type attribute, as in HTML: the guard refuses a nameless submit
    } else if (tag === "A") {
      twin = document.createElement("a");
      twin.setAttribute("href", real.href);
    } else {
      twin = document.createElement("div");
      twin.setAttribute("role", real.getAttribute("role"));
    }
    twin.style.cssText = SHADOW_STYLE;
    twin.addEventListener("input", () => {
      const t = twin.type;
      if (t !== "file" && t !== "checkbox" && t !== "radio") setValue(real, twin.value);
      forward(real, "input");
    });
    twin.addEventListener("change", () => {
      const t = twin.type;
      if (t === "file") real.files = twin.files;
      else if (t === "checkbox" || t === "radio") real.checked = twin.checked;
      else setValue(real, twin.value);
      forward(real, "change");
    });
    twin.addEventListener("click", (event) => {
      event.preventDefault();
      // the guard approved the name the worker read: a button that is "Submit" now must not take a click meant for "Next"
      const info = twinInfo.get(twin);
      if (!info || shadowName(real) !== info.base) {
        schedule();
        return;
      }
      real.focus();
      real.click();
    });
    return twin;
  }

  // Every write is idempotent: required, disabled and checked are watched attributes, so a blind write would loop.
  function setAttr(el, name, value) {
    if (value === null) {
      if (el.hasAttribute(name)) el.removeAttribute(name);
    } else if (el.getAttribute(name) !== value) {
      el.setAttribute(name, value);
    }
  }

  function mirrorState(real, twin) {
    const tag = real.tagName;
    const type = tag === "INPUT" ? real.type : "";
    if (tag === "SELECT") {
      const sig = [...real.options].map((o) => o.value + "\u0001" + o.text).join("\u0002");
      if (shadowSizes.get(twin) !== sig) {
        shadowSizes.set(twin, sig);
        twin.replaceChildren(...[...real.options].map((o) => new Option(o.text, o.value)));
      }
    }
    if (tag === "TEXTAREA" || tag === "SELECT" || (tag === "INPUT" && !/^(file|checkbox|radio|password)$/.test(type))) {
      if (twin.value !== real.value) twin.value = real.value;
    }
    if (type === "checkbox" || type === "radio") {
      if (twin.checked !== real.checked) twin.checked = real.checked;
    }
    // not part of the name (a placeholder is no answer): markFields falls back to it on the twin, as on any light field
    setAttr(twin, "placeholder", real.getAttribute("placeholder"));
    setAttr(twin, "required", real.required || real.hasAttribute("required") ? "" : null);
    setAttr(twin, "disabled", real.disabled || real.hasAttribute("disabled") ? "" : null);
    for (const attr of MIRRORED_ARIA) setAttr(twin, attr, real.getAttribute(attr));
  }

  function watchRoot(root) {
    if (shadowSeen.has(root)) return;
    shadowSeen.add(root);
    if (!shadowWatch) shadowWatch = new MutationObserver(schedule);
    shadowWatch.observe(root, {
      subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: STATE_ATTRS,
    });
  }

  function shadowControls() {
    const roots = [];
    const collect = (root) => {
      for (const el of root.querySelectorAll("*")) {
        if (el.shadowRoot) {
          roots.push(el.shadowRoot);
          collect(el.shadowRoot);
        }
      }
    };
    collect(document);
    const found = [];
    const w = window.innerWidth;
    const h = window.innerHeight;
    for (const root of roots) {
      watchRoot(root);
      for (const el of root.querySelectorAll(INTERACTIVE)) {
        // ponytail: at most 200 twins per pass, so a pathological page cannot freeze it; raise it or rank by distance
        if (found.length >= SHADOW_CAP) break;
        const box = el.getBoundingClientRect();
        if (!box.width || !box.height) continue;
        if (box.bottom > -h && box.top < 2 * h && box.right > -w && box.left < 2 * w) found.push([el, box]);
      }
    }
    return found;
  }

  function shadowProxies() {
    if (!document.body) return;
    const found = shadowControls();
    if (!found.length && !shadowBox) return;
    if (!shadowBox || !shadowBox.isConnected) {
      shadowBox = document.createElement("div");
      shadowBox.setAttribute("data-aa-shadow", "");
      shadowBox.style.cssText = "position:absolute;left:0;top:0;width:0;height:0";
      document.body.appendChild(shadowBox);
    }
    // relative to the container itself, not to scrollX/scrollY: right even when the body is offset or transformed
    const origin = shadowBox.getBoundingClientRect();
    const used = new Map();
    const keep = [];
    for (const [real, box] of found) {
      let twin = twinOf.get(real);
      if (!twin) {
        twin = makeProxy(real);
        twinOf.set(real, twin);
      }
      const base = shadowName(real);
      let name = base;
      if (base) {
        const n = (used.get(base) || 0) + 1;
        used.set(base, n);
        if (n > 1) name = `${base} (${n})`;
      }
      twinInfo.set(twin, { real, base });
      const s = twin.style;
      s.left = `${box.x - origin.x}px`;
      s.top = `${box.y - origin.y}px`;
      s.width = `${box.width}px`;
      s.height = `${box.height}px`;
      mirrorState(real, twin);
      // aria-label only when the name changed: markFields rewrites it into "[req] <name> = <value>" afterwards
      if (twin.getAttribute("data-aa-shadow-name") !== name) {
        twin.setAttribute("data-aa-shadow-name", name);
        twin.removeAttribute(OURS);
        twin.removeAttribute(ORIG);
        if (name) twin.setAttribute("aria-label", name);
        else twin.removeAttribute("aria-label");
      }
      keep.push(twin);
    }
    const kept = new Set(keep);
    for (const child of [...shadowBox.children]) if (!kept.has(child)) child.remove();
    keep.forEach((twin, i) => {
      if (shadowBox.children[i] !== twin) shadowBox.insertBefore(twin, shadowBox.children[i] || null);
    });
  }

  function dropShadowProxies() {
    if (shadowBox) shadowBox.remove();
    shadowBox = null;
    if (shadowWatch) shadowWatch.disconnect();
    shadowWatch = null;
    shadowSeen = new WeakSet();
  }

  function pass() {
    if (!active) return;
    // nameClickables FIRST: the role it writes puts the element in CONTROLS ([role="button"]), so the Google arm
    // below sees it in its first loop and names it in the SAME pass. The other way round, the Google arm named a
    // clickable div and this pass then took the name off again (iconName returns "" for a div holding an
    // <img alt="Google">), and only a SECOND pass put it back -- correct by accident, and wrong on a page that
    // mutates once and never again.
    nameClickables();
    nameGoogleButtons();  // before markFields: no field mark ever stands in front of an "[icon] " name
    nameFrames();
    if (isTop) {
      shadowProxies();  // before markFields, so the fresh twins are marked in this same pass
      markFields();
    }  // a frame is out of read_page's sight; one that is opened as a tab is a top frame there
    updateGoogleTag();
  }

  function schedule() {
    const now = Date.now();
    if (!firstAt) firstAt = now;
    if (due) return;  // a pass is on its way: more triggers cannot push it back
    clearTimeout(timer);
    // debounced, but a page that mutates all the time still gets its pass after about a second
    due = now - firstAt >= 1000;
    timer = setTimeout(() => { firstAt = 0; due = false; pass(); }, due ? 0 : 150);
  }

  // Called on every answer "enabled": the observers are installed once, but the pass runs every time, so a tab that
  // was grouped at the end of a run and adopted again is marked again.
  function keepNamingGoogleButtons(root) {
    active = true;
    if (!naming) {
      naming = true;
      new MutationObserver(schedule).observe(root, {
        subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: STATE_ATTRS,
      });
      for (const type of ["change", "input", "click"]) document.addEventListener(type, schedule, true);
      // which controls are near the viewport depends on the scroll position (scroll does not bubble: capture)
      if (isTop) window.addEventListener("scroll", schedule, { capture: true, passive: true });
    }
    pass();
  }

  // The tab left the run group: native dialogs come back (page.js reads the attribute), the passes stop and the
  // labels and the dialog banner go.
  function leaveRun() {
    active = false;
    clearTimeout(timer);
    firstAt = 0;
    due = false;
    for (const el of document.querySelectorAll(`[${OURS}]`)) restoreLabel(el);
    dropPlainRoles();
    for (const banner of document.querySelectorAll("a[data-aa-dialog]")) banner.remove();
    dropShadowProxies();
    if (document.documentElement) document.documentElement.removeAttribute(ATTR);
  }

  // recheck: the tab was moved between groups just now (aa-recheck), so the service worker must not put it back into
  // its opener's run group: the candidate has to be able to take a tab out of the run.
  function ask(recheck) {
    tries += 1;
    try {
      chrome.runtime.sendMessage({ type: "aa-check", href: location.href, top: isTop, recheck: !!recheck }, (resp) => {
        if (chrome.runtime.lastError || !resp) return;
        if (isTop && resp.tag) keepTitle(resp.tag);
        if (resp.enabled) {
          active = true;
          if (!wired) {
            wired = true;
            if (isTop) listenForFrameUrls();
            else reportFrameUrl();
          }
          whenRoot((root) => {
            root.setAttribute(ATTR, "");
            keepNamingGoogleButtons(root);
          });
        } else if (resp.retry && tries < 5) {
          setTimeout(() => ask(recheck), 400);
        } else {
          leaveRun();  // not in a run group, and no retry left
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
      ask(true);
    }
  });
})();

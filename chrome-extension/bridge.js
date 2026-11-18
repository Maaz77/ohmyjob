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

  function usableFrameUrl(raw) {
    if (!raw) return null;  // new URL(null) would be ".../null"
    let url;
    try {
      url = new URL(raw, location.href);
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
    return usableFrameUrl(here) || usableFrameUrl(frame.getAttribute("src"));
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

  function nameGoogleButtons() {
    for (const el of document.querySelectorAll(CONTROLS)) {
      if (hasName(el) || inStoreLink(el)) continue;
      const label = el.closest("label");
      if (label && label.textContent.trim()) continue;
      if (!hasGoogleIcon(label || el)) continue;
      el.setAttribute("aria-label", GOOGLE_NAME);
      // the control can be hidden: its label takes the click
      if (label && !hasName(label)) label.setAttribute("aria-label", GOOGLE_NAME);
    }
    for (const el of iconHolders()) {
      if (hasName(el) || !isClickable(el) || inStoreLink(el) || !hasGoogleIcon(el)) continue;
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
      if (inStoreLink(el)) continue;
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
  // state goes into the aria-label, which it does honour: "[x] [req] [disabled] [invalid] <name>". The guard strips
  // the marks again (aa_lib.parse_refs). No getComputedStyle here, for the reason given at iconHolders.
  // Never on a pressable control (button, link ...): the label replaces its live name, and a pass that comes late would
  // leave a stale name that the guard trusts ("Next" on a button that now says "Submit application").
  const OURS = "data-aa-label";       // the exact label this script wrote
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

  function fieldMarks(el, kind, reqRadios) {
    const flag = (name) => el.getAttribute(name) === "true";
    const form = kind !== "option";
    const on = kind === "option" || (kind === "choice" && (el.checked === true || flag("aria-checked")));
    const group = el.type === "radio" && el.name && reqRadios.get(el.form || document);
    const req = form && (el.required === true || flag("aria-required") || !!(group && group.has(el.name)));
    const off = form && (flag("aria-disabled") || el.matches(":disabled"));
    return (on ? "[x] " : "") + (req ? "[req] " : "") + (off ? "[disabled] " : "") + (form && flag("aria-invalid") ? "[invalid] " : "");
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
  // aria-label is NOT watched: this script writes it, and watching it would loop.
  const STATE_ATTRS = ["aria-checked", "aria-pressed", "aria-selected", "aria-disabled", "aria-invalid",
    "aria-required", "disabled", "required", "checked"];

  function pass() {
    if (!active) return;
    nameGoogleButtons();
    nameFrames();
    if (isTop) markFields();  // a frame is out of read_page's sight; one that is opened as a tab is a top frame there
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
    for (const banner of document.querySelectorAll("a[data-aa-dialog]")) banner.remove();
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

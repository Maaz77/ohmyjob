// ohmyjob: pop-ups as tabs (service worker).
//
// A tab group becomes a "run group" when one of its tabs loads a marker page: https://www.linkedin.com/feed/#aa-popups-as-tabs
// (a run with a LinkedIn job: the feed also shows whether LinkedIn is signed in) or the public page
// https://www.linkedin.com/robots.txt#aa-popups-as-tabs (a run with no LinkedIn job). The application-master opens one
// of them in its first tab.
// In a run group, page.js opens page pop-ups as tabs, and Chrome puts those tabs in the group,
// where Claude in Chrome can see them. Tabs outside run groups are not changed.
//
// Title tags (the hooks read them in the tabs_context_mcp output):
//   "[AA on <version>] " the tab that registered the group (the preflight check reads it and compares the version)
//   "[AA from N] "   a tab that tab N opened (for example a Google sign-in pop-up)
//   "[AA grouped N] " the home tab, after it put N job tabs in their groups (end of a run)
//   "[AA adopted N] " the home tab, after it brought N job tabs back from "Needs Attention" (/continue-applying)
//
// End of a run: the master loads https://www.linkedin.com/robots.txt?aa-groups#aa-groups=<plan> in its home tab
// (a tab of the run group). The plan is {"Pending Review": [tab ids], "Needs Attention": [tab ids], "Deleted": [tab ids]}:
// each list goes to the group of that title in the window, created if missing.

const VERSION = chrome.runtime.getManifest().version; // the preflight warns when it differs from the plugin's manifest
const KEY = "aaRunGroups";
const OWNERS = "aaRunOwners"; // run group id -> the tab that registered it (the master's home tab)
// The tab that really opened a tab (window.open or a link), from webNavigation. Chrome's tab.openerTabId is the tab
// that was active at that moment, not the opener: workers click in background tabs, so several Google pop-ups
// would get "[AA from <the previous pop-up>]" and no worker would find its sign-in tab. One key per tab: no lost writes.
const SOURCE = "aaSource:";
chrome.webNavigation.onCreatedNavigationTarget.addListener((d) =>
  chrome.storage.session.set({ [SOURCE + d.tabId]: d.sourceTabId })
);
chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove(SOURCE + tabId));

async function sourceTab(tabId) {
  const value = (await chrome.storage.session.get(SOURCE + tabId))[SOURCE + tabId];
  return Number.isInteger(value) ? value : null;
}

const MARKER = "#aa-popups-as-tabs";
const GROUPS = "#aa-groups=";
const ADOPT = "#aa-adopt=";
const COLORS = { "Pending Review": "green", "Needs Attention": "orange", Deleted: "red" };

async function makeGroups(href, tab) {
  let plan;
  try {
    plan = JSON.parse(decodeURIComponent(href.slice(href.indexOf(GROUPS) + GROUPS.length)));
  } catch (e) {
    return 0;
  }
  let moved = 0;
  for (const title of Object.keys(COLORS)) {
    const ids = plan && Array.isArray(plan[title]) ? plan[title] : [];
    const found = await chrome.tabGroups.query({ windowId: tab.windowId, title });
    const tabIds = [];
    for (const id of ids) {
      if (!Number.isInteger(id)) continue;
      try {
        const t = await chrome.tabs.get(id);
        if (found.length && t.groupId === found[0].id) moved += 1; // in its group already (the page loaded twice)
        // only a job tab of this run: same window and still in this run's group
        else if (t.windowId === tab.windowId && t.groupId === tab.groupId && t.id !== tab.id) tabIds.push(id);
      } catch (e) {
        // the tab is closed
      }
    }
    if (!tabIds.length) continue;
    try {
      const groupId = found.length
        ? await chrome.tabs.group({ groupId: found[0].id, tabIds })
        : await chrome.tabs.group({ tabIds, createProperties: { windowId: tab.windowId } });
      await chrome.tabGroups.update(groupId, { title, color: COLORS[title], collapsed: false });
      moved += tabIds.length;
    } catch (e) {
      // a tab closed in the meantime: the other title still gets its group
    }
  }
  return moved;
}

async function runGroups() {
  const data = await chrome.storage.session.get(KEY);
  return Array.isArray(data[KEY]) ? data[KEY] : [];
}

async function addRunGroup(groupId, tabId) {
  const groups = await runGroups();
  if (!groups.includes(groupId)) {
    groups.push(groupId);
    await chrome.storage.session.set({ [KEY]: groups });
  }
  // the newest sign-in check page owns the group: the home tab of a stopped run can still be open in it
  const owners = (await chrome.storage.session.get(OWNERS))[OWNERS] || {};
  owners[groupId] = tabId;
  await chrome.storage.session.set({ [OWNERS]: owners });
  return groups;
}

// The master's marker page (a job page cannot be it): the LinkedIn feed or robots.txt, with the exact hash #aa-popups-as-tabs.
function isMarker(href) {
  try {
    const url = new URL(href);
    return url.origin === "https://www.linkedin.com" && /^\/(feed\/?|robots\.txt)$/.test(url.pathname) && url.hash === MARKER;
  } catch (e) {
    return false;
  }
}

// Only the master's own page can ask for groups: https://www.linkedin.com/robots.txt?aa-groups#aa-groups=..., loaded in the
// tab that registered the run group. A job page cannot: its address is not that page, or its tab is not the owner.
async function groupRequest(msg, tab, marker) {
  if (!msg.top || typeof msg.href !== "string" || !msg.href.includes(marker)) return false;
  let url;
  try {
    url = new URL(msg.href);
  } catch (e) {
    return false;
  }
  if (url.origin !== "https://www.linkedin.com" || url.pathname !== "/robots.txt") return false;
  const owners = (await chrome.storage.session.get(OWNERS))[OWNERS] || {};
  return owners[tab.groupId] === tab.id;
}

// /continue-applying: every tab of every group "Needs Attention" goes back into the run group of the home tab, where
// Claude in Chrome (and so the workers) can use it again. This includes tabs that the candidate opened or moved there
// themselves: 'ohmyjob apply adopted' matches them to jobs by page address afterwards. The tab ids in the page address
// (older versions moved only those) are not used. Tabs of any other group are left alone.
async function adoptTabs(tab) {
  const waiting = await chrome.tabGroups.query({ title: "Needs Attention" });
  let moved = 0;
  for (const group of waiting) {
    for (const t of await chrome.tabs.query({ groupId: group.id })) {
      if (t.id === tab.id) continue;
      try {
        await chrome.tabs.group({ groupId: tab.groupId, tabIds: [t.id] }); // one at a time: a closed tab stops no other
        moved += 1;
        // the page was loaded outside a run group: let it ask again, so pop-ups open as tabs (bridge.js)
        chrome.tabs.sendMessage(t.id, { type: "aa-recheck" }).catch(() => {});
      } catch (e) {
        // the tab is closed, or it cannot be grouped
      }
    }
  }
  return moved;
}

async function check(msg, tab) {
  let groupId = typeof tab.groupId === "number" ? tab.groupId : -1;
  if (await groupRequest(msg, tab, GROUPS)) {
    const moved = await makeGroups(msg.href, tab);
    return { enabled: false, tag: `[AA grouped ${moved}] `, retry: false };
  }
  if (await groupRequest(msg, tab, ADOPT)) {
    const moved = await adoptTabs(tab);
    return { enabled: false, tag: `[AA adopted ${moved}] `, retry: false };
  }
  const marked = groupId !== -1 && typeof msg.href === "string" && isMarker(msg.href);
  const groups = marked ? await addRunGroup(groupId, tab.id) : await runGroups();
  const source = await sourceTab(tab.id);
  const hasOpener = source !== null;

  // A tab that a run tab opened, but outside the group: put it in the opener's group.
  if (groupId === -1 && hasOpener) {
    try {
      const opener = await chrome.tabs.get(source);
      if (opener && groups.includes(opener.groupId)) {
        await chrome.tabs.group({ groupId: opener.groupId, tabIds: [tab.id] });
        groupId = opener.groupId;
      }
    } catch (e) {
      // the opener tab is closed, or the tab cannot be grouped
    }
  }

  const enabled = groupId !== -1 && groups.includes(groupId);
  let tag = null;
  if (enabled && msg.top) {
    if (marked) tag = `[AA on ${VERSION}] `;
    else if (hasOpener) tag = `[AA from ${source}] `;
  }
  // A new tab can join its group, or get its webNavigation source, a moment after it opens: the bridge asks again.
  const waiting = msg.top && !hasOpener && typeof tab.openerTabId === "number";
  return { enabled, tag, retry: (!enabled && groupId === -1 && hasOpener) || waiting };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "aa-check" || !sender.tab) return false;
  check(msg, sender.tab).then(sendResponse, () => sendResponse({ enabled: false, tag: null, retry: false }));
  return true; // the answer is sent later
});

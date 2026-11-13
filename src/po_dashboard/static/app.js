// The little script the pages need (DEC-45). HTMX swaps the page body instead of reloading it; this file adds the
// security token to uploads, keeps a person's place after a save, shows when the app is busy, and runs the vendor
// search box. Every page still works as plain links and forms; nothing here is needed to read or change data.
"use strict";

document.documentElement.classList.add("js");

// Boxes a person types into (not checkboxes, lists or buttons).
const TYPING = 'input:not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="hidden"]):not([type="submit"]), textarea';

// Only the element that updates itself is "background"; a link inside it is a person's click.
function isBackground(element) {
  return element instanceof Element && element.hasAttribute("data-background");
}

// The element that sent a request. HTMX puts it in different places depending on the event.
function sourceOf(event) {
  const detail = event.detail || {};
  return (detail.requestConfig && detail.requestConfig.elt) || detail.elt || event.target;
}

// ---- The security token. The gate reads it from a header when it cannot read the form (a file upload).
document.addEventListener("htmx:configRequest", (event) => {
  const detail = event.detail;
  const source = detail.elt instanceof Element ? detail.elt : null;
  if (detail.verb === "get") {
    // A filter left empty is left out of the address, so it reads /orders?q=PO-12, not /orders?q=PO-12&status=&...
    if (source && source.matches("form#filters")) {
      for (const name of Array.from(detail.parameters.keys())) {
        if (detail.parameters.get(name) === "") detail.parameters.delete(name);
      }
    }
    return;
  }
  const form = source ? source.closest("form") || source : null;
  const field = form ? form.querySelector('input[name="csrf_token"]') : null;
  if (field) detail.headers["X-CSRF-Token"] = field.value;
});

// ---- Busy: a thin bar at the top while a page or a save is on its way. Background updates do not show it.
// A page that updates itself skips a turn while the person is typing, or when the tab is not being looked at.
document.addEventListener("htmx:beforeRequest", (event) => {
  if (isBackground(sourceOf(event))) {
    const active = document.activeElement;
    if (document.hidden || (active && active.matches && active.matches(TYPING))) event.preventDefault();
    return;
  }
  document.documentElement.classList.add("busy");
});
for (const name of ["htmx:afterRequest", "htmx:afterSettle", "htmx:sendError", "htmx:timeout"]) {
  document.addEventListener(name, (event) => {
    if (!isBackground(sourceOf(event))) document.documentElement.classList.remove("busy");
  });
}

// ---- Keeping a person's place. A form that saves and comes back to the same page, or is refused, updates the page
// where it is: no jump to the top, the cursor is not moved to the first box, and a refusal is brought into view.
// A form answered with a new page of its own (an upload's preview or result) behaves like a new page.
// HTMX reports a settled swap once for each new element, so the work is done once, on the first report.
let pending = null;

document.addEventListener("htmx:beforeSwap", (event) => {
  const detail = event.detail;
  const source = sourceOf(event);
  pending = null;
  if (isBackground(source)) return;
  pending = { stay: false, target: detail.target };
  const form = source instanceof HTMLFormElement ? source : null;
  if (!form || !detail.boosted || (form.getAttribute("method") || "get").toLowerCase() !== "post") return;
  const info = detail.pathInfo || {};
  const landed = info.responsePath || info.finalRequestPath || "";
  const redirected = info.responsePath && info.responsePath !== info.finalRequestPath;
  const refused = detail.xhr && detail.xhr.status >= 400;
  if (refused || (redirected && new URL(landed, location.href).pathname === location.pathname)) {
    detail.swapOverride = "innerHTML show:none";
    Object.assign(pending, { stay: true, action: form.getAttribute("action"), scroll: window.scrollY });
  }
});

document.addEventListener("htmx:afterSwap", () => {
  if (pending && pending.stay) document.querySelectorAll("main [autofocus]").forEach((box) => box.removeAttribute("autofocus"));
});

document.addEventListener("htmx:afterSettle", () => {
  if (!pending) return;
  const done = pending;
  pending = null;
  const swapped = done.target === document.body || !done.target.id ? null : document.getElementById(done.target.id);
  announce(swapped || document.querySelector("main"));
  if (!done.stay) return;
  window.scrollTo(0, done.scroll);
  const problem = document.querySelector("main [role=alert]");
  if (problem) {
    problem.tabIndex = -1;
    problem.focus({ preventScroll: true });
    problem.scrollIntoView({ block: "center" });
    return;
  }
  const again = done.action ? document.querySelector(`form[action="${CSS.escape(done.action)}"]`) : null;
  const button = again ? again.querySelector('button:not([type="button"])') : null;
  if (button) button.focus({ preventScroll: true });
});

// A message that appears after a swap is read out by screen readers through one region that stays on the page.
function announce(area) {
  const region = document.getElementById("announce");
  const message = area ? area.querySelector(".notice, .error") : null;
  if (!region) return;
  region.textContent = "";
  if (message) setTimeout(() => { region.textContent = message.textContent.replace(/\s+/g, " ").trim(); }, 60);
}

// ---- Keys: "/" puts the cursor in the page's search box (as on many websites); Esc leaves it.
document.addEventListener("keydown", (event) => {
  if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
  const active = document.activeElement;
  if (active && active.matches && (active.matches(TYPING) || active.isContentEditable)) return;
  const box = document.querySelector("main [data-search]");
  if (!box) return;
  event.preventDefault();
  box.focus();
  if (box.select) box.select();
});

// The account menu in the top bar closes on a click elsewhere or on Esc, like any website's menu.
document.addEventListener("click", (event) => {
  document.querySelectorAll("details.account[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  document.querySelectorAll("details.account[open]").forEach((menu) => {
    menu.open = false;
    menu.querySelector("summary").focus();
  });
});

// ---- Things that are set up on every page, again after each swap.
function setUp(root) {
  // While a form is being sent its submit button is disabled and shows that it is working (DEC-45 item 3).
  root.querySelectorAll("form").forEach((form) => {
    if (form.querySelector('button:not([type="button"])')) form.setAttribute("hx-disabled-elt", 'find button:not([type="button"])');
  });
  // A "Saved" note fades away after a few seconds; it only tells the person the save went through.
  root.querySelectorAll(".fades").forEach((note) => {
    if (note.dataset.ready) return;
    note.dataset.ready = "1";
    setTimeout(() => {
      note.classList.add("gone");
      setTimeout(() => note.remove(), 700);
    }, 3000);
  });
  root.querySelectorAll("[data-picker]").forEach(vendorPicker);
}

if (window.htmx) {
  window.htmx.onLoad((root) => setUp(root instanceof Element ? root : document));
} else {
  setUp(document);
}

// ---- Vendor search box: shows matching vendors while a person types; the chosen vendor's id goes in the hidden
// field, which then tells its form it changed (a filter form updates the list at once).
function vendorPicker(box) {
  if (box.dataset.ready) return;
  box.dataset.ready = "1";
  const text = box.querySelector('input[type="text"]');
  const hidden = box.querySelector('input[type="hidden"]');
  const list = box.querySelector("ul");
  let found = [];
  let items = [];
  let active = -1;
  let timer = null;
  let ticket = 0;
  let chosenName = hidden.value ? text.value : "";

  const setChoice = (id, name) => {
    const changed = hidden.value !== String(id);
    hidden.value = id;
    chosenName = name;
    if (changed) hidden.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const close = () => {
    list.hidden = true;
    text.setAttribute("aria-expanded", "false");
    text.removeAttribute("aria-activedescendant");
    active = -1;
  };
  const mark = () => {
    items.forEach((item, i) => {
      item.classList.toggle("active", i === active);
      item.setAttribute("aria-selected", i === active ? "true" : "false");
    });
    if (active >= 0) {
      text.setAttribute("aria-activedescendant", items[active].id);
      items[active].scrollIntoView({ block: "nearest" });
    } else {
      text.removeAttribute("aria-activedescendant");
    }
  };
  const choose = (vendor) => {
    text.value = vendor.name;
    text.setCustomValidity("");
    close();
    setChoice(vendor.id, vendor.name);
  };
  const render = (vendors) => {
    found = vendors;
    list.replaceChildren();
    items = vendors.map((vendor, i) => {
      const item = document.createElement("li");
      item.id = list.id + "_" + i;
      item.setAttribute("role", "option");
      item.textContent = vendor.name;
      item.addEventListener("mousedown", (event) => {
        event.preventDefault();
        choose(vendor);
      });
      list.append(item);
      return item;
    });
    if (!vendors.length) {
      const none = document.createElement("li");
      none.className = "none";
      none.textContent = box.dataset.none || "No vendor matches. Add it under Vendors first.";
      list.append(none);
    }
    list.hidden = false;
    text.setAttribute("aria-expanded", "true");
    active = vendors.length ? 0 : -1;
    mark();
  };
  const search = async () => {
    const mine = ++ticket;
    try {
      const response = await fetch((box.dataset.searchUrl || "/vendors/search") + "?q=" + encodeURIComponent(text.value), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok || mine !== ticket || document.activeElement !== text) return;
      render(await response.json());
    } catch (problem) {
      close();
    }
  };

  text.addEventListener("input", (event) => {
    event.stopPropagation(); // typing a name is not yet a choice: the filter form waits for a pick
    if (text.value !== chosenName) setChoice("", "");
    text.setCustomValidity(text.value.trim() ? box.dataset.pick || "Pick a vendor from the list." : "");
    clearTimeout(timer);
    timer = setTimeout(search, 150);
  });
  text.addEventListener("change", (event) => event.stopPropagation());
  text.addEventListener("focus", () => {
    if (!hidden.value) search();
  });
  text.addEventListener("blur", close);
  text.addEventListener("keydown", (event) => {
    const open = !list.hidden && found.length > 0;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (list.hidden) {
        search();
      } else if (open) {
        active = (active + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        mark();
      }
    } else if (event.key === "Enter" && open && active >= 0) {
      event.preventDefault();
      choose(found[active]);
    } else if (event.key === "Escape" && !list.hidden) {
      event.preventDefault();
      close();
    }
  });
  if (text.value && !hidden.value) text.setCustomValidity("Pick a vendor from the list.");
}

// ---- Copy button for a drafted message (PD-5.3): copies the text box named in data-copy. Nothing is sent anywhere.
document.addEventListener("click", (event) => {
  const button = event.target instanceof Element ? event.target.closest("button[data-copy]") : null;
  if (!button) return;
  const box = document.getElementById(button.getAttribute("data-copy") || "");
  if (!(box instanceof HTMLTextAreaElement)) return;
  const done = () => {
    const note = button.parentElement && button.parentElement.querySelector(".copied");
    if (note) note.textContent = "Copied";
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(box.value).then(done, () => { box.select(); });
  } else {
    box.select();
  }
});

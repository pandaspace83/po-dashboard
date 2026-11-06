// Sends a file-upload form with the security token in a header (the gate reads the token from a header for uploads),
// then follows the redirect, or shows the page's own list of problems in place.
document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.matches("form[data-upload]")) return;
  event.preventDefault();
  const button = form.querySelector("button[type=submit]");
  const result = document.getElementById("import-result");
  const tokenField = form.querySelector('input[name="csrf_token"]');
  const body = new FormData(form);
  body.delete("csrf_token");
  if (button) button.disabled = true;
  try {
    const response = await fetch(form.action, {
      method: "POST",
      body,
      headers: { "X-CSRF-Token": tokenField ? tokenField.value : "" },
      credentials: "same-origin",
    });
    if (response.redirected) {
      window.location.href = response.url;
      return;
    }
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const fresh = page.getElementById("import-result");
    if (result) {
      if (fresh) result.replaceChildren(...fresh.childNodes);
      else result.textContent = "The file could not be sent (" + response.status + "). Reload the page and try again.";
    }
  } catch (problem) {
    if (result) result.textContent = "The file could not be sent. Check the connection and try again.";
  } finally {
    if (button) button.disabled = false;
  }
});

// A "Saved" note fades away after a few seconds; it only tells the person the save went through.
document.querySelectorAll(".fades").forEach((note) => {
  setTimeout(() => {
    note.classList.add("gone");
    setTimeout(() => note.remove(), 700);
  }, 3000);
});

// Vendor search box: shows matching vendors while a person types; the chosen vendor's id goes in the hidden field.
document.querySelectorAll("[data-picker]").forEach((box) => {
  const text = box.querySelector('input[type="text"]');
  const hidden = box.querySelector('input[type="hidden"]');
  const list = box.querySelector("ul");
  let shown = [];
  let active = -1;
  let timer = null;
  let ticket = 0;
  let chosenName = hidden.value ? text.value : "";

  const close = () => {
    list.hidden = true;
    text.setAttribute("aria-expanded", "false");
    active = -1;
  };
  const mark = () => {
    shown.forEach((item, i) => item.classList.toggle("active", i === active));
    if (active >= 0) text.setAttribute("aria-activedescendant", shown[active].id);
    else text.removeAttribute("aria-activedescendant");
  };
  const choose = (vendor) => {
    hidden.value = vendor.id;
    text.value = vendor.name;
    chosenName = vendor.name;
    text.setCustomValidity("");
    close();
  };
  const render = (vendors) => {
    list.replaceChildren();
    shown = vendors.map((vendor, i) => {
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
      none.textContent = "No vendor matches. Add it under Vendors first.";
      list.append(none);
    }
    list.hidden = false;
    text.setAttribute("aria-expanded", "true");
    active = vendors.length ? 0 : -1;
    list._vendors = vendors;
    mark();
  };
  const search = async () => {
    const mine = ++ticket;
    try {
      const response = await fetch("/vendors/search?q=" + encodeURIComponent(text.value), { credentials: "same-origin" });
      if (!response.ok || mine !== ticket) return;
      render(await response.json());
    } catch (problem) {
      close();
    }
  };

  text.addEventListener("input", () => {
    if (text.value !== chosenName) {
      hidden.value = "";
      chosenName = "";
    }
    text.setCustomValidity(text.value.trim() ? "Pick a vendor from the list." : "");
    clearTimeout(timer);
    timer = setTimeout(search, 150);
  });
  text.addEventListener("focus", () => {
    if (!hidden.value) search();
  });
  text.addEventListener("blur", close);
  text.addEventListener("keydown", (event) => {
    const open = !list.hidden && list._vendors && list._vendors.length;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (list.hidden) {
        search();
      } else if (open) {
        active = (active + (event.key === "ArrowDown" ? 1 : -1) + shown.length) % shown.length;
        mark();
      }
      event.preventDefault();
    } else if (event.key === "Enter" && open && active >= 0) {
      event.preventDefault();
      choose(list._vendors[active]);
    } else if (event.key === "Escape") {
      close();
    }
  });
  if (text.value && !hidden.value) text.setCustomValidity("Pick a vendor from the list.");
});

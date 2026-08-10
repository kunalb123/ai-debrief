/* ==========================================================================
   paperswipe — feed behaviour
   Desktop: a filterable glass grid. Mobile: a swipeable deck over the same data.
   Saves live in localStorage; nothing leaves the device.
   ========================================================================== */
(function () {
  "use strict";

  // ---------------------------------------------------------------- storage
  var SAVED_KEY = "paperswipe:saved:v1";
  var SEEN_KEY = "paperswipe:seen:v1";

  function readJSON(key, fallback) {
    try {
      var raw = window.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (err) {
      return fallback;
    }
  }

  function writeJSON(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch (err) {
      /* private mode / quota — saves just won't persist */
    }
  }

  // ------------------------------------------------------------------- data
  var dataNode = document.getElementById("paper-data");
  var payload = {};
  try {
    payload = JSON.parse(dataNode.textContent) || {};
  } catch (err) {
    // The grid still works from the server-rendered markup, but saves need the
    // data — fail loudly rather than degrading silently.
    console.error("paperswipe: could not parse embedded paper data", err);
    payload = {};
  }
  var papers = payload.papers || [];
  // Where "Read" goes: the arXiv PDF or its abstract page. Set at build time by
  // generate_site.py --link-target, and mirrored here so the JS-rendered deck
  // cards match the server-rendered ones exactly.
  var linkToPdf = payload.link_target !== "abstract";
  var byId = {};
  papers.forEach(function (paper) {
    byId[paper.id] = paper;
  });

  var saved = readJSON(SAVED_KEY, []);
  if (!Array.isArray(saved)) saved = [];
  var savedIds = {};
  saved.forEach(function (paper) {
    if (paper && paper.id) savedIds[paper.id] = true;
  });

  // "Seen" only persists for the current day's feed, so a reload picks up where
  // you left off but tomorrow's papers always start fresh.
  var seenStore = readJSON(SEEN_KEY, null);
  var seen = seenStore && seenStore.date === payload.date && Array.isArray(seenStore.ids)
    ? seenStore.ids.slice()
    : [];
  var seenSet = {};
  seen.forEach(function (id) {
    seenSet[id] = true;
  });

  function persistSeen() {
    writeJSON(SEEN_KEY, { date: payload.date, ids: Object.keys(seenSet) });
  }

  function persistSaved() {
    writeJSON(SAVED_KEY, saved);
  }

  // ------------------------------------------------------------------- dom
  var body = document.body;
  var feed = document.getElementById("feed");
  var emptyEl = document.querySelector("[data-empty]");
  var emptyTitle = document.querySelector("[data-empty-title]");
  var emptySub = document.querySelector("[data-empty-sub]");
  var restartBtn = document.querySelector("[data-restart]");
  var deckbar = document.querySelector("[data-deckbar]");
  var undoBtn = deckbar.querySelector('[data-action="undo"]');
  var progressCurrent = document.querySelector("[data-progress-current]");
  var progressTotal = document.querySelector("[data-progress-total]");
  var savedCountEl = document.querySelector("[data-saved-count]");
  var toastEl = document.querySelector("[data-toast]");
  var masthead = document.querySelector(".masthead");

  var todayCards = Array.prototype.slice.call(feed.querySelectorAll(".card"));

  // A second, client-rendered feed for the Saved view — saved papers can come
  // from earlier days that aren't in today's server-rendered markup.
  var savedFeed = document.createElement("section");
  savedFeed.className = "feed";
  savedFeed.id = "saved-feed";
  savedFeed.setAttribute("aria-label", "Saved papers");
  savedFeed.hidden = true;
  feed.parentNode.insertBefore(savedFeed, feed.nextSibling);

  var view = "today";
  var activeTag = "__all__";

  // ------------------------------------------------------------------ toast
  var toastTimer = null;
  function toast(message) {
    if (!toastEl) return;
    toastEl.textContent = message;
    toastEl.classList.add("is-visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      toastEl.classList.remove("is-visible");
    }, 1900);
  }

  // ------------------------------------------------------- card rendering
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  /** Build a card node from paper data — mirrors templates/index.html. */
  function renderCard(paper) {
    var card = el("article", "card");
    card.dataset.id = paper.id;
    card.dataset.rank = paper.rank || 0;
    card.dataset.upvotes = paper.upvotes || 0;
    card.dataset.tags = (paper.tags || []).join("|");
    card.tabIndex = 0;

    var ring = el("span", "card__ring");
    ring.setAttribute("aria-hidden", "true");
    card.appendChild(ring);
    ["save", "skip"].forEach(function (kind) {
      var stamp = el("span", "card__stamp card__stamp--" + kind, kind === "save" ? "Save" : "Skip");
      stamp.setAttribute("aria-hidden", "true");
      card.appendChild(stamp);
    });

    var bodyEl = el("div", "card__body");

    var head = el("div", "card__head");
    var tags = el("div", "card__tags");
    (paper.tags || []).forEach(function (tag) {
      var pill = el("span", "pill");
      var glyph = el("span", "pill__glyph", "◆");
      glyph.setAttribute("aria-hidden", "true");
      pill.appendChild(glyph);
      pill.appendChild(document.createTextNode(tag));
      tags.appendChild(pill);
    });
    head.appendChild(tags);
    if (paper.saved_from) {
      var stamp = el("span", "card__rank", paper.saved_from);
      stamp.setAttribute("aria-hidden", "true");
      head.appendChild(stamp);
    }
    bodyEl.appendChild(head);

    bodyEl.appendChild(el("h2", "card__title", paper.title));
    bodyEl.appendChild(el("hr", "card__rule"));

    var summary = el("div", "card__summary");
    summary.appendChild(el("p", "card__teaser", paper.teaser || paper.summary || ""));
    if (paper.teaser_rest) summary.appendChild(el("p", "card__rest", paper.teaser_rest));
    bodyEl.appendChild(summary);

    if (paper.teaser_rest) {
      var expand = el("button", "card__expand");
      expand.type = "button";
      expand.setAttribute("aria-expanded", "false");
      expand.appendChild(el("span", "card__expand-label", "Full summary"));
      var icon = el("span", "card__expand-icon", "↓");
      icon.setAttribute("aria-hidden", "true");
      expand.appendChild(icon);
      bodyEl.appendChild(expand);
    }

    bodyEl.appendChild(el("hr", "card__rule"));

    var meta = el("div", "card__meta");
    meta.appendChild(el("span", "card__votes", "↑ " + (paper.upvotes || 0)));
    meta.appendChild(el("span", "dot", "·"));
    var count = paper.author_count || (paper.authors || []).length;
    meta.appendChild(el("span", null, count + (count === 1 ? " author" : " authors")));
    var dayLabel = paper.daily_label || paper.published_label;
    if (dayLabel) {
      meta.appendChild(el("span", "dot", "·"));
      meta.appendChild(el("span", null, dayLabel));
    }
    bodyEl.appendChild(meta);
    card.appendChild(bodyEl);

    var actions = el("div", "card__actions");
    var read = el("a", "btn btn--primary", linkToPdf ? "Read PDF" : "Read paper");
    read.href =
      (linkToPdf ? paper.pdf_url : paper.arxiv_url) ||
      paper.arxiv_url ||
      paper.hf_url ||
      "#";
    read.target = "_blank";
    read.rel = "noopener";
    actions.appendChild(read);

    var save = el("button", "btn btn--save");
    save.type = "button";
    save.setAttribute("data-save", "");
    save.appendChild(el("span", "btn__label", "Save"));
    actions.appendChild(save);
    card.appendChild(actions);

    syncSaveButton(card);
    return card;
  }

  // ------------------------------------------------------------------ saves
  function syncSaveButton(card) {
    var btn = card.querySelector("[data-save]");
    if (!btn) return;
    var on = !!savedIds[card.dataset.id];
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.querySelector(".btn__label").textContent = on ? "Saved" : "Save";
  }

  function syncAllSaveButtons(id) {
    var selector = id ? '.card[data-id="' + cssEscape(id) + '"]' : ".card";
    Array.prototype.forEach.call(document.querySelectorAll(selector), syncSaveButton);
  }

  function cssEscape(value) {
    return String(value).replace(/["\\]/g, "\\$&");
  }

  function updateSavedCount() {
    if (savedCountEl) savedCountEl.textContent = String(saved.length);
  }

  function setSaved(id, on) {
    var paper = byId[id];
    if (on) {
      if (savedIds[id]) return false;
      if (!paper) return false;
      var record = {};
      Object.keys(paper).forEach(function (key) {
        record[key] = paper[key];
      });
      record.saved_at = new Date().toISOString();
      record.saved_from = payload.date || "";
      saved.unshift(record);
      savedIds[id] = true;
    } else {
      if (!savedIds[id]) return false;
      saved = saved.filter(function (item) {
        return item.id !== id;
      });
      delete savedIds[id];
    }
    persistSaved();
    updateSavedCount();
    syncAllSaveButtons(id);
    if (view === "saved") renderSavedView();
    return true;
  }

  function toggleSaved(id) {
    var next = !savedIds[id];
    setSaved(id, next);
    toast(next ? "Saved to your list" : "Removed from saved");
  }

  // ------------------------------------------------------------- filtering
  function matchesTag(card) {
    if (activeTag === "__all__") return true;
    var tags = (card.dataset.tags || "").split("|");
    return tags.indexOf(activeTag) !== -1;
  }

  // --------------------------------------------------------------- expand
  function toggleExpand(card, force) {
    var expanded = typeof force === "boolean" ? force : !card.classList.contains("is-expanded");
    card.classList.toggle("is-expanded", expanded);
    var btn = card.querySelector(".card__expand");
    if (btn) {
      btn.setAttribute("aria-expanded", expanded ? "true" : "false");
      btn.querySelector(".card__expand-label").textContent = expanded ? "Less" : "Full summary";
    }
  }

  // ------------------------------------------------------------------ deck
  var deckMQ = window.matchMedia("(max-width: 760px)");
  var isDeck = false;
  var order = [];
  var cursor = 0;
  var history = [];

  function measureChrome() {
    if (!isDeck) return;
    var mastheadHeight = masthead ? masthead.offsetHeight : 0;
    var barHeight = deckbar && !deckbar.hidden ? deckbar.offsetHeight : 0;
    document.documentElement.style.setProperty("--masthead-h", mastheadHeight + "px");
    document.documentElement.style.setProperty("--deckbar-h", barHeight + "px");
  }

  function buildOrder() {
    order = todayCards.filter(function (card) {
      return matchesTag(card) && !seenSet[card.dataset.id];
    });
    cursor = 0;
    history = [];
  }

  function transformFor(depth) {
    return "translate3d(0, " + depth * 12 + "px, 0) scale(" + (1 - depth * 0.045) + ")";
  }

  function paintDeck() {
    todayCards.forEach(function (card) {
      card.hidden = false; // grid mode uses [hidden]; the deck uses .is-hidden
      card.classList.remove("is-top", "is-flying", "is-dragging");
      card.classList.add("is-hidden");
      card.style.transform = "";
      card.style.opacity = "";
      card.style.zIndex = "";
      card.setAttribute("aria-hidden", "true");
      setStamps(card, 0);
    });

    for (var depth = 0; depth < 3; depth++) {
      var card = order[cursor + depth];
      if (!card) break;
      card.classList.remove("is-hidden");
      card.removeAttribute("aria-hidden");
      card.style.transform = transformFor(depth);
      card.style.opacity = depth === 2 ? "0.55" : "1";
      card.style.zIndex = String(30 - depth);
      if (depth === 0) card.classList.add("is-top");
    }

    var done = cursor >= order.length;
    feed.hidden = done;
    deckbar.hidden = done;
    if (done) {
      showEmpty();
    } else {
      emptyEl.hidden = true;
    }

    if (progressCurrent) progressCurrent.textContent = String(Math.min(cursor + 1, order.length));
    if (progressTotal) progressTotal.textContent = String(order.length);
    if (undoBtn) undoBtn.disabled = history.length === 0;
    measureChrome();
  }

  function showEmpty() {
    var savedToday = todayCards.filter(function (card) {
      return savedIds[card.dataset.id];
    }).length;
    emptyTitle.textContent = order.length ? "You're all caught up" : "Nothing here";
    emptySub.textContent = order.length
      ? "You went through " + order.length + " paper" + (order.length === 1 ? "" : "s") +
        " and saved " + savedToday + "."
      : "No papers match this topic. Try another filter.";
    restartBtn.hidden = !order.length;
    emptyEl.hidden = false;
  }

  function setStamps(card, dx) {
    var saveStamp = card.querySelector(".card__stamp--save");
    var skipStamp = card.querySelector(".card__stamp--skip");
    if (!saveStamp || !skipStamp) return;
    var ratio = Math.min(Math.abs(dx) / 110, 1);
    saveStamp.style.opacity = dx > 0 ? String(ratio) : "0";
    skipStamp.style.opacity = dx < 0 ? String(ratio) : "0";
  }

  function commit(card, direction) {
    // direction: 1 = save (right), -1 = skip (left)
    var id = card.dataset.id;
    var didSave = false;
    if (direction > 0) didSave = setSaved(id, true);

    seenSet[id] = true;
    persistSeen();
    history.push({ id: id, direction: direction, didSave: didSave });

    card.classList.remove("is-dragging");
    card.classList.add("is-flying");
    var width = window.innerWidth || 400;
    card.style.transform =
      "translate3d(" + direction * (width * 1.15) + "px, -40px, 0) rotate(" + direction * 22 + "deg)";
    card.style.opacity = "0";

    cursor += 1;
    window.setTimeout(function () {
      setStamps(card, 0);
      paintDeck();
    }, 260);

    if (direction > 0) toast(didSave ? "Saved to your list" : "Already saved");
  }

  function undo() {
    var last = history.pop();
    if (!last) return;
    delete seenSet[last.id];
    persistSeen();
    if (last.didSave) setSaved(last.id, false);
    cursor = Math.max(0, cursor - 1);
    paintDeck();
    toast("Undone");
  }

  // ---- drag ----------------------------------------------------------------
  var drag = null;

  function topCard() {
    return order[cursor] || null;
  }

  function onPointerDown(event) {
    if (!isDeck || drag) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    var card = topCard();
    if (!card) return;
    if (!card.contains(event.target)) return;
    // Let buttons, links and the scrollable abstract behave normally.
    if (event.target.closest("a, button")) return;

    drag = {
      id: event.pointerId,
      card: card,
      startX: event.clientX,
      startY: event.clientY,
      dx: 0,
      dy: 0,
      active: false,
      startTime: Date.now(),
    };
  }

  function onPointerMove(event) {
    if (!drag || event.pointerId !== drag.id) return;
    drag.dx = event.clientX - drag.startX;
    drag.dy = event.clientY - drag.startY;

    if (!drag.active) {
      if (Math.abs(drag.dx) < 10 || Math.abs(drag.dx) < Math.abs(drag.dy)) return;
      drag.active = true;
      drag.card.classList.add("is-dragging");
      try {
        drag.card.setPointerCapture(drag.id);
      } catch (err) {
        /* capture is best-effort */
      }
    }

    if (event.cancelable) event.preventDefault();
    var rotation = Math.max(-16, Math.min(16, drag.dx / 14));
    drag.card.style.transform =
      "translate3d(" + drag.dx + "px, " + drag.dy * 0.24 + "px, 0) rotate(" + rotation + "deg)";
    setStamps(drag.card, drag.dx);
  }

  function onPointerUp(event) {
    if (!drag || (event && event.pointerId !== drag.id)) return;
    var current = drag;
    drag = null;

    try {
      current.card.releasePointerCapture(current.id);
    } catch (err) {
      /* nothing to release */
    }

    if (!current.active) {
      current.card.classList.remove("is-dragging");
      return;
    }

    var elapsed = Math.max(Date.now() - current.startTime, 1);
    var velocity = current.dx / elapsed; // px per ms
    var threshold = Math.min(140, (window.innerWidth || 400) * 0.28);
    var flung = Math.abs(current.dx) > threshold || Math.abs(velocity) > 0.65;

    if (flung && Math.abs(current.dx) > 24) {
      commit(current.card, current.dx > 0 ? 1 : -1);
    } else {
      current.card.classList.remove("is-dragging");
      current.card.style.transform = transformFor(0);
      setStamps(current.card, 0);
    }
  }

  // ------------------------------------------------------------ view modes
  function applyGridVisibility() {
    todayCards.forEach(function (card) {
      var show = matchesTag(card);
      card.hidden = !show;
      card.classList.remove("is-hidden", "is-top", "is-flying", "is-dragging");
      card.style.transform = "";
      card.style.opacity = "";
      card.style.zIndex = "";
      card.removeAttribute("aria-hidden");
    });

    var visible = todayCards.filter(function (card) {
      return !card.hidden;
    }).length;
    feed.hidden = visible === 0;
    if (visible === 0) {
      emptyTitle.textContent = "Nothing here";
      emptySub.textContent = "No papers match this topic. Try another filter.";
      restartBtn.hidden = true;
      emptyEl.hidden = false;
    } else {
      emptyEl.hidden = true;
    }
  }

  function applyMode() {
    isDeck = deckMQ.matches && view === "today";
    body.classList.toggle("is-deck", isDeck);
    feed.classList.toggle("is-deck", isDeck);

    if (view === "saved") {
      feed.hidden = true;
      deckbar.hidden = true;
      savedFeed.hidden = false;
      emptyEl.hidden = true;
      renderSavedView();
      return;
    }

    savedFeed.hidden = true;
    if (isDeck) {
      buildOrder();
      paintDeck();
    } else {
      deckbar.hidden = true;
      applyGridVisibility();
    }
  }

  function renderSavedView() {
    savedFeed.textContent = "";
    if (!saved.length) {
      emptyTitle.textContent = "No saved papers yet";
      emptySub.textContent = deckMQ.matches
        ? "Swipe right — or tap ★ — to keep a paper here."
        : "Hit Save on any card to keep it here.";
      restartBtn.hidden = true;
      emptyEl.hidden = false;
      savedFeed.hidden = true;
      return;
    }
    emptyEl.hidden = true;
    savedFeed.hidden = false;
    saved.forEach(function (paper) {
      savedFeed.appendChild(renderCard(paper));
    });
  }

  function setView(next) {
    view = next;
    Array.prototype.forEach.call(document.querySelectorAll(".views__tab"), function (tab) {
      var on = tab.dataset.view === next;
      tab.classList.toggle("is-active", on);
      tab.setAttribute("aria-pressed", on ? "true" : "false");
    });
    applyMode();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ----------------------------------------------------------------- events
  document.addEventListener("click", function (event) {
    var tab = event.target.closest(".views__tab");
    if (tab) {
      setView(tab.dataset.view);
      return;
    }

    var chip = event.target.closest(".chip");
    if (chip) {
      activeTag = chip.dataset.tag;
      Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (other) {
        var on = other === chip;
        other.classList.toggle("is-active", on);
        other.setAttribute("aria-pressed", on ? "true" : "false");
      });
      if (view !== "today") setView("today");
      else applyMode();
      return;
    }

    var expand = event.target.closest(".card__expand");
    if (expand) {
      toggleExpand(expand.closest(".card"));
      return;
    }

    var save = event.target.closest("[data-save]");
    if (save) {
      toggleSaved(save.closest(".card").dataset.id);
      return;
    }

    var action = event.target.closest("[data-action]");
    if (action) {
      if (action.dataset.action === "undo") {
        undo();
      } else {
        var card = topCard();
        if (card) commit(card, action.dataset.action === "save" ? 1 : -1);
      }
      return;
    }

    if (event.target.closest("[data-restart]")) {
      seenSet = {};
      persistSeen();
      applyMode();
      toast("Deck reset");
    }
  });

  // Enter/Space on a focused card expands it (the whole card is a tap target
  // for "go deeper" without stealing clicks from the buttons inside it).
  document.addEventListener("keydown", function (event) {
    var card = event.target.closest ? event.target.closest(".card") : null;
    if (card && (event.key === "Enter" || event.key === " ") && event.target === card) {
      event.preventDefault();
      toggleExpand(card);
      return;
    }

    if (!isDeck || view !== "today") return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;

    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      var top = topCard();
      if (!top) return;
      event.preventDefault();
      commit(top, event.key === "ArrowRight" ? 1 : -1);
    } else if (event.key === "z" || event.key === "Z") {
      undo();
    }
  });

  feed.addEventListener("pointerdown", onPointerDown);
  window.addEventListener("pointermove", onPointerMove, { passive: false });
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerUp);

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(function () {
      var wasDeck = isDeck;
      applyMode();
      if (wasDeck === isDeck) measureChrome();
    }, 140);
  });

  if (deckMQ.addEventListener) {
    deckMQ.addEventListener("change", applyMode);
  } else if (deckMQ.addListener) {
    deckMQ.addListener(applyMode);
  }

  // Another tab saved something — keep this one in step.
  window.addEventListener("storage", function (event) {
    if (event.key !== SAVED_KEY) return;
    saved = readJSON(SAVED_KEY, []) || [];
    savedIds = {};
    saved.forEach(function (paper) {
      if (paper && paper.id) savedIds[paper.id] = true;
    });
    updateSavedCount();
    syncAllSaveButtons();
    if (view === "saved") renderSavedView();
  });

  // -------------------------------------------------------------- bootstrap
  updateSavedCount();
  syncAllSaveButtons();
  applyMode();
  measureChrome();

  if (!papers.length) {
    emptyTitle.textContent = "No papers today";
    emptySub.textContent = "HuggingFace hasn't listed any yet. Check back tomorrow.";
    restartBtn.hidden = true;
    emptyEl.hidden = false;
  }
})();

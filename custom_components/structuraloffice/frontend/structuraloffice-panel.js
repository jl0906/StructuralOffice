const WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

class StructuralOfficePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._tab = "dashboard";
    this._loading = false;
    this._error = "";
    this._modal = null;
  }

  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this._data && !this._loading) this.load();
  }

  set narrow(value) { this._narrow = value; }
  set panel(value) { this._panel = value; }

  connectedCallback() {
    this.render();
    if (this._hass) this.load();
    if (this._hass?.connection && !this._unsubscribePromise) {
      this._unsubscribePromise = this._hass.connection.subscribeEvents(
        () => this.load(true),
        "structuraloffice_updated",
      );
    }
  }

  disconnectedCallback() {
    if (this._unsubscribePromise) {
      this._unsubscribePromise.then((unsubscribe) => unsubscribe());
      this._unsubscribePromise = null;
    }
  }

  async load(silent = false) {
    if (!silent) this._loading = true;
    this._error = "";
    if (!silent) this.render();
    try {
      this._data = await this.call("structuraloffice/get_data");
    } catch (error) {
      this._error = this.errorText(error);
    } finally {
      this._loading = false;
      this.render();
    }
  }

  call(type, payload = {}) {
    return this._hass.connection.sendMessagePromise({ type, ...payload });
  }

  errorText(error) {
    return error?.message || error?.body?.message || String(error);
  }

  h(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  formatDate(raw) {
    return new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" })
      .format(new Date(`${raw}T12:00:00`));
  }

  render() {
    const style = `
      :host { display:block; min-height:100%; color:var(--primary-text-color); background:var(--primary-background-color); font-family:var(--paper-font-body1_-_font-family, sans-serif); }
      * { box-sizing:border-box; }
      .shell { max-width:1280px; margin:0 auto; padding:24px clamp(14px,3vw,36px) 60px; }
      header { display:flex; align-items:center; justify-content:space-between; gap:20px; margin-bottom:20px; }
      .brand { display:flex; gap:14px; align-items:center; }
      .logo { width:46px; height:46px; border-radius:14px; background:linear-gradient(145deg,#0f766e,#14b8a6); color:white; display:grid; place-items:center; box-shadow:0 8px 24px #0f766e40; }
      h1 { font-size:26px; margin:0; line-height:1.15; } .subtitle { color:var(--secondary-text-color); font-size:13px; margin-top:4px; }
      nav { display:flex; gap:6px; overflow:auto; padding:5px; background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:14px; margin-bottom:22px; }
      nav button { white-space:nowrap; border:0; background:transparent; color:var(--secondary-text-color); padding:10px 15px; border-radius:10px; font-weight:600; cursor:pointer; }
      nav button.active { color:white; background:#0f766e; }
      button { font:inherit; } .primary,.secondary,.danger,.icon-btn { border:0; border-radius:10px; padding:10px 14px; cursor:pointer; font-weight:600; }
      .primary { background:#0f766e; color:white; } .secondary { background:var(--secondary-background-color); color:var(--primary-text-color); }
      .danger { background:#b91c1c; color:white; } .icon-btn { padding:8px; background:transparent; color:var(--secondary-text-color); }
      button:disabled { opacity:.45; cursor:not-allowed; }
      .cards { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-bottom:22px; }
      .stat,.card,.empty { background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:16px; box-shadow:var(--ha-card-box-shadow,none); }
      .stat { padding:18px; } .stat .value { font-size:32px; font-weight:750; margin-top:10px; } .stat .label { color:var(--secondary-text-color); font-size:13px; }
      .section-head { display:flex; align-items:center; justify-content:space-between; gap:15px; margin:22px 0 12px; }
      h2 { margin:0; font-size:20px; } h3 { margin:0 0 6px; font-size:16px; }
      .list { display:grid; gap:10px; } .card { padding:16px; display:flex; gap:14px; justify-content:space-between; align-items:center; }
      .card-main { min-width:0; } .meta { color:var(--secondary-text-color); font-size:13px; margin-top:5px; } .description { margin-top:7px; white-space:pre-wrap; }
      .actions { display:flex; align-items:center; gap:7px; flex-shrink:0; } .badge { display:inline-block; padding:4px 8px; border-radius:999px; background:var(--secondary-background-color); font-size:12px; }
      .badge.overdue { background:#b91c1c; color:#fff; } .badge.today { background:#d97706; color:#fff; } .badge.done { background:#15803d; color:#fff; }
      .empty { padding:38px 20px; text-align:center; color:var(--secondary-text-color); }
      .error { border-radius:10px; padding:12px 14px; background:#b91c1c; color:white; margin-bottom:14px; }
      .loading { display:grid; place-items:center; min-height:320px; color:var(--secondary-text-color); }
      .modal-backdrop { position:fixed; inset:0; z-index:10; background:#0008; display:grid; place-items:center; padding:18px; }
      .modal { width:min(680px,100%); max-height:90vh; overflow:auto; border-radius:18px; padding:22px; background:var(--card-background-color); box-shadow:0 22px 70px #0007; }
      .modal-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:18px; }
      form { display:grid; gap:15px; } label { display:grid; gap:7px; font-size:13px; color:var(--secondary-text-color); }
      input,textarea,select { width:100%; border:1px solid var(--divider-color); border-radius:9px; padding:11px 12px; background:var(--primary-background-color); color:var(--primary-text-color); font:inherit; }
      textarea { min-height:86px; resize:vertical; } .row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
      .checks { display:flex; flex-wrap:wrap; gap:8px; } .check { display:flex; align-items:center; gap:7px; padding:8px 10px; border:1px solid var(--divider-color); border-radius:9px; color:var(--primary-text-color); }
      .check input { width:auto; } .form-actions { display:flex; justify-content:flex-end; gap:9px; margin-top:7px; }
      .hint { font-size:12px; color:var(--secondary-text-color); margin-top:-3px; }
      .toolbar { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
      .amount { font-size:18px; font-weight:700; white-space:nowrap; }
      .invoice-card { border-left:4px solid #0f766e; }
      .invoice-card.overdue { border-left-color:#b91c1c; }
      .file-input { display:none; }
      .import-summary { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:12px 0; }
      .import-summary .stat { padding:12px; }
      .error-list { max-height:220px; overflow:auto; padding-left:20px; color:#b91c1c; }
      .chart { display:grid; grid-template-columns:repeat(12,minmax(34px,1fr)); gap:8px; min-height:230px; align-items:end; padding:20px 8px 8px; overflow:auto; }
      .month { height:190px; display:flex; gap:3px; align-items:flex-end; justify-content:center; position:relative; padding-bottom:25px; }
      .bar { width:15px; min-height:2px; border-radius:5px 5px 0 0; background:#0f766e; }
      .bar.out { background:#f59e0b; } .month-label { position:absolute; bottom:0; font-size:10px; color:var(--secondary-text-color); }
      .legend { display:flex; gap:16px; color:var(--secondary-text-color); font-size:12px; margin:10px; } .dot { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:5px; background:#0f766e; } .dot.out { background:#f59e0b; }
      .role-row { display:grid; grid-template-columns:1fr 180px; gap:12px; align-items:center; width:100%; }
      .readonly [data-new-topic], .readonly [data-new-routine], .readonly [data-new-invoice],
      .readonly [data-edit-topic], .readonly [data-edit-routine], .readonly [data-edit-invoice],
      .readonly [data-delete-topic], .readonly [data-delete-routine], .readonly [data-delete-invoice],
      .readonly [data-status], .readonly [data-pay-invoice], .readonly [data-import] { display:none; }
      @media(max-width:800px){ .cards{grid-template-columns:1fr 1fr}.card{align-items:flex-start;flex-direction:column}.actions{width:100%;justify-content:flex-end}.row{grid-template-columns:1fr} header{align-items:flex-start}.shell{padding-top:16px} }
    `;

    let body = "";
    if (this._loading && !this._data) body = `<div class="loading">StructuralOffice wird geladen …</div>`;
    else if (this._data) {
      if (this._tab === "dashboard") body = this.dashboardTemplate();
      if (this._tab === "topics") body = this.topicsTemplate();
      if (this._tab === "routines") body = this.routinesTemplate();
      if (this._tab === "accounting") body = this.accountingTemplate();
      if (this._tab === "analytics") body = this.analyticsTemplate();
      if (this._tab === "settings") body = this.settingsTemplate();
    }

    this.shadowRoot.innerHTML = `<style>${style}</style><div class="shell ${this._data?.access === "viewer" ? "readonly" : ""}">
      <header><div class="brand"><div class="logo"><ha-icon icon="mdi:office-building-cog"></ha-icon></div><div><h1>StructuralOffice</h1><div class="subtitle">Strukturelle Abläufe verlässlich im Blick</div></div></div></header>
      <nav>${this.navButton("dashboard", "mdi:view-dashboard-outline", "Übersicht")}${this.navButton("topics", "mdi:shape-outline", "Topics")}${this.navButton("routines", "mdi:calendar-sync", "Routinen")}${this.navButton("accounting", "mdi:calculator-variant-outline", "Buchhaltung")}${this.navButton("analytics", "mdi:chart-bar", "Auswertungen")}${this.navButton("settings", "mdi:cog-outline", "Einstellungen")}</nav>
      ${this._error ? `<div class="error">${this.h(this._error)}</div>` : ""}${body}${this.modalTemplate()}
    </div>`;
    this.bindEvents();
  }

  navButton(tab, icon, label) {
    return `<button data-tab="${tab}" class="${this._tab === tab ? "active" : ""}"><ha-icon icon="${icon}"></ha-icon> ${label}</button>`;
  }

  canEdit() { return this._data?.access === "admin" || this._data?.access === "editor"; }

  dashboardTemplate() {
    const s = this._data.summary;
    const items = this._data.occurrences.filter((x) => x.status === "open").slice(0, 40);
    return `<div class="cards">
      ${this.stat("Offen", s.open, "mdi:clipboard-text-clock")}${this.stat("Heute", s.today, "mdi:calendar-today")}${this.stat("Überfällig", s.overdue, "mdi:calendar-alert")}${this.stat("Demnächst", s.upcoming, "mdi:calendar-arrow-right")}
    </div>${this.accountingDashboardTemplate()}<div class="section-head"><h2>Aufgaben</h2></div>
    <div class="list">${items.length ? items.map((item) => this.taskCard(item)).join("") : `<div class="empty"><ha-icon icon="mdi:check-circle-outline"></ha-icon><p>Aktuell sind keine Aufgaben offen.</p></div>`}</div>`;
  }

  accountingDashboardTemplate() {
    const s = this._data.accounting_summary;
    if (!s) return "";
    return `<div class="section-head"><h2>Buchhaltung</h2><button class="secondary" data-go-accounting>Öffnen</button></div><div class="cards">
      ${this.stat("Offene Eingangsrechnungen", s.open_payables, "mdi:invoice-arrow-left-outline")}
      ${this.stat("Fällige Zahlungen", s.due_payments, "mdi:cash-clock")}
      ${this.stat("Offene Forderungen", s.open_receivables, "mdi:invoice-arrow-right-outline")}
      ${this.stat("Überfällige Forderungen", s.overdue_receivables, "mdi:cash-remove")}
    </div>`;
  }

  stat(label, value, icon) {
    return `<div class="stat"><div class="label"><ha-icon icon="${icon}"></ha-icon> ${label}</div><div class="value">${value}</div></div>`;
  }

  taskCard(item) {
    const kind = item.due_date < this._data.today ? "overdue" : item.due_date === this._data.today ? "today" : "";
    const badge = kind === "overdue" ? "Überfällig" : kind === "today" ? "Heute" : this.formatDate(item.due_date);
    return `<article class="card"><div class="card-main"><h3>${this.h(item.topic_name)}</h3><div><span class="badge ${kind}">${badge}</span></div><div class="meta">${this.h(item.routine_name)} · ${this.h(item.due_time)} Uhr${item.category ? ` · ${this.h(item.category)}` : ""}</div>${item.description ? `<div class="description">${this.h(item.description)}</div>` : ""}</div>${this.canEdit() ? `<div class="actions"><button class="secondary" data-status="skipped" data-id="${item.id}">Überspringen</button><button class="primary" data-status="completed" data-id="${item.id}"><ha-icon icon="mdi:check"></ha-icon> Erledigt</button></div>` : ""}</article>`;
  }

  topicsTemplate() {
    return `<div class="section-head"><div><h2>Topics</h2><div class="subtitle">Wiederverwendbare Aufgabenbausteine</div></div>${this.canEdit() ? `<button class="primary" data-new-topic><ha-icon icon="mdi:plus"></ha-icon> Topic</button>` : ""}</div><div class="list">${this._data.topics.length ? this._data.topics.map((topic) => `<article class="card"><div class="card-main"><h3>${this.h(topic.name)}</h3>${topic.category ? `<span class="badge">${this.h(topic.category)}</span>` : ""}${topic.description ? `<div class="description">${this.h(topic.description)}</div>` : ""}<div class="meta">${topic.checklist.length ? `${topic.checklist.length} Checklistenpunkte` : "Keine Checkliste"}</div></div>${this.canEdit() ? `<div class="actions"><button class="secondary" data-edit-topic="${topic.id}">Bearbeiten</button><button class="icon-btn" title="Löschen" data-delete-topic="${topic.id}"><ha-icon icon="mdi:delete-outline"></ha-icon></button></div>` : ""}</article>`).join("") : `<div class="empty">Noch keine Topics vorhanden.</div>`}</div>`;
  }

  routinesTemplate() {
    const frequency = { once: "Einmalig", daily: "Täglich", weekly: "Wöchentlich", monthly: "Monatlich", yearly: "Jährlich" };
    return `<div class="section-head"><div><h2>Routinen</h2><div class="subtitle">Topics und Fälligkeitstage verbinden</div></div><button class="primary" data-new-routine ${this._data.topics.length ? "" : "disabled"}><ha-icon icon="mdi:plus"></ha-icon> Routine</button></div>${!this._data.topics.length ? `<div class="error">Lege zuerst mindestens ein Topic an.</div>` : ""}<div class="list">${this._data.routines.length ? this._data.routines.map((routine) => `<article class="card"><div class="card-main"><h3>${this.h(routine.name)} ${routine.enabled ? "" : `<span class="badge">Deaktiviert</span>`}</h3><div class="meta">${frequency[routine.schedule.frequency]} · ${routine.topic_ids.length} Topics · ${this.h(routine.due_time)} Uhr</div><div class="meta">Erinnerungen: ${routine.reminder_offsets.map((x) => x === 0 ? "am Fälligkeitstag" : x < 0 ? `${Math.abs(x)} T. vorher` : `${x} T. danach`).join(", ") || "keine"}</div></div><div class="actions"><button class="secondary" data-edit-routine="${routine.id}">Bearbeiten</button><button class="icon-btn" title="Löschen" data-delete-routine="${routine.id}"><ha-icon icon="mdi:delete-outline"></ha-icon></button></div></article>`).join("") : `<div class="empty">Noch keine Routinen vorhanden.</div>`}</div>`;
  }

  money(amount, currency = "EUR") {
    return new Intl.NumberFormat("de-DE", { style: "currency", currency }).format(Number(amount || 0));
  }

  analyticsTemplate() {
    const analytics = this._data.analytics || {};
    const workflow = analytics.workflow || {};
    const accounting = analytics.accounting || { months: [], aging: {}, max_monthly_cents: 0 };
    const max = Math.max(accounting.max_monthly_cents || 0, 1);
    const aging = accounting.aging || {};
    const bars = accounting.months.map((month) => `<div class="month" title="${this.h(month.label)}: Forderungen ${this.money(month.receivables_cents / 100)} / Eingangsrechnungen ${this.money(month.payables_cents / 100)}"><div class="bar" style="height:${Math.max(2, month.receivables_cents / max * 160)}px"></div><div class="bar out" style="height:${Math.max(2, month.payables_cents / max * 160)}px"></div><span class="month-label">${this.h(month.label)}</span></div>`).join("");
    return `<div class="section-head"><div><h2>Auswertungen</h2><div class="subtitle">Rollierende 12 Monate und aktueller Forderungsstand</div></div></div><div class="cards">${this.stat("Aufgaben gesamt", workflow.total || 0, "mdi:clipboard-list-outline")}${this.stat("Erledigt", workflow.completed || 0, "mdi:check-circle-outline")}${this.stat("Offen", workflow.open || 0, "mdi:clock-outline")}${this.stat("Erledigungsquote", `${workflow.completion_rate || 0} %`, "mdi:percent")}</div><article class="stat"><h3>Rechnungsvolumen nach Rechnungsmonat</h3><div class="legend"><span><i class="dot"></i>Ausgangsrechnungen</span><span><i class="dot out"></i>Eingangsrechnungen</span></div><div class="chart">${bars}</div></article><div class="section-head"><h2>Offene Forderungen nach Fälligkeit</h2></div><div class="cards">${this.stat("Noch nicht fällig", this.money((aging.not_due_cents || 0) / 100), "mdi:calendar-check")}${this.stat("1–7 Tage", this.money((aging.days_1_7_cents || 0) / 100), "mdi:calendar-clock")}${this.stat("8–30 Tage", this.money(((aging.days_8_14_cents || 0) + (aging.days_15_30_cents || 0)) / 100), "mdi:calendar-alert")}${this.stat("Mehr als 30 Tage", this.money((aging.days_31_plus_cents || 0) / 100), "mdi:alert-circle")}</div>`;
  }

  accountingTemplate() {
    const invoices = [...(this._data.invoices || [])].sort((a, b) => {
      if (a.status === "open" && b.status !== "open") return -1;
      if (a.status !== "open" && b.status === "open") return 1;
      return a.due_date.localeCompare(b.due_date);
    });
    const status = { open: "Offen", paid: "Bezahlt", cancelled: "Storniert" };
    const direction = { payable: "Eingangsrechnung", receivable: "Ausgangsrechnung" };
    const editing = this.canEdit();
    return `<div class="section-head"><div><h2>Buchhaltung</h2><div class="subtitle">Zahlungen, Forderungen und Mahnfristen</div></div><div class="toolbar"><button class="secondary" data-template><ha-icon icon="mdi:file-excel-outline"></ha-icon> Vorlage</button><button class="secondary" data-export><ha-icon icon="mdi:download"></ha-icon> Excel</button><button class="secondary" data-csv><ha-icon icon="mdi:file-delimited-outline"></ha-icon> CSV</button>${editing ? `<button class="secondary" data-import><ha-icon icon="mdi:upload"></ha-icon> Import</button><input class="file-input" data-file-input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"><button class="primary" data-new-invoice><ha-icon icon="mdi:plus"></ha-icon> Buchung</button>` : ""}</div></div><div class="list">${invoices.length ? invoices.map((invoice) => `<article class="card invoice-card ${invoice.is_overdue ? "overdue" : ""}"><div class="card-main"><h3>${this.h(invoice.invoice_number)} · ${this.h(invoice.contact)}</h3><div><span class="badge ${invoice.is_overdue ? "overdue" : invoice.status === "paid" ? "done" : ""}">${invoice.is_overdue ? "Überfällig" : status[invoice.status]}</span> <span class="badge">${direction[invoice.direction]}</span>${invoice.dunning_level ? ` <span class="badge">Mahnstufe ${invoice.dunning_level}</span>` : ""}</div><div class="meta">Rechnung: ${this.formatDate(invoice.invoice_date)} · Fällig: ${this.formatDate(invoice.due_date)}</div>${invoice.note ? `<div class="description">${this.h(invoice.note)}</div>` : ""}</div><div class="actions"><div class="amount">${this.money(invoice.gross_amount, invoice.currency)}</div>${editing && invoice.direction === "receivable" && invoice.status === "open" ? `<button class="secondary" data-pdf-invoice="${invoice.id}"><ha-icon icon="mdi:file-pdf-box"></ha-icon> Mahn-PDF</button>` : ""}${editing && invoice.status === "open" ? `<button class="primary" data-pay-invoice="${invoice.id}"><ha-icon icon="mdi:check"></ha-icon> Bezahlt</button>` : ""}${editing ? `<button class="secondary" data-edit-invoice="${invoice.id}">Bearbeiten</button><button class="icon-btn" title="Löschen" data-delete-invoice="${invoice.id}"><ha-icon icon="mdi:delete-outline"></ha-icon></button>` : ""}</div></article>`).join("") : `<div class="empty">Noch keine Buchungen vorhanden.</div>`}</div>`;
  }

  settingsTemplate() {
    const targets = this._data.options.notify_targets;
    const users = this._data.users || [];
    const roles = this._data.access === "admin" ? `<div class="section-head"><h2>Zugriffsrollen</h2></div><div class="list">${users.map((user) => `<article class="card"><div class="role-row"><div><h3>${this.h(user.name)}</h3><div class="meta">${user.is_admin ? "Home-Assistant-Administrator" : user.is_active ? "Aktiv" : "Inaktiv"}</div></div><select data-user-role="${user.id}" ${user.is_admin ? "disabled" : ""}><option value="none" ${!user.role ? "selected" : ""}>Kein Zugriff</option><option value="viewer" ${user.role === "viewer" ? "selected" : ""}>Betrachter</option><option value="editor" ${user.role === "editor" ? "selected" : ""}>Bearbeiter</option><option value="admin" ${user.role === "admin" ? "selected" : ""}>Administrator</option></select></div></article>`).join("")}</div>` : "";
    return `<div class="section-head"><div><h2>Einstellungen</h2><div class="subtitle">Grundeinstellungen und Firmendaten werden im Integrationsdialog verwaltet.</div></div></div><article class="card"><div class="card-main"><h3>Pushbenachrichtigungen</h3><div class="meta">${targets.length ? targets.map((x) => this.h(x)).join(", ") : "Kein Gerät ausgewählt"}</div><div class="meta">Standardzeit: ${this.h(this._data.options.default_reminder_time)} Uhr · Nachholen: ${this._data.options.catch_up_hours} Stunden</div></div>${this._data.access === "admin" ? `<div class="actions"><button class="secondary" data-test-notification ${targets.length ? "" : "disabled"}>Test senden</button></div>` : ""}</article><p class="hint">Ändern unter Einstellungen → Geräte & Dienste → StructuralOffice → Konfigurieren.</p>${roles}`;
  }

  modalTemplate() {
    if (!this._modal) return "";
    if (this._modal.type === "topic") return this.topicModal(this._modal.value || {});
    if (this._modal.type === "routine") return this.routineModal(this._modal.value || {});
    if (this._modal.type === "invoice") return this.invoiceModal(this._modal.value || {});
    if (this._modal.type === "import") return this.importModal(this._modal.value || {});
    return "";
  }

  topicModal(topic) {
    return `<div class="modal-backdrop"><div class="modal"><div class="modal-head"><h2>${topic.id ? "Topic bearbeiten" : "Topic anlegen"}</h2><button class="icon-btn" type="button" data-close><ha-icon icon="mdi:close"></ha-icon></button></div><form data-topic-form><input type="hidden" name="id" value="${this.h(topic.id)}"><label>Name *<input name="name" required maxlength="200" value="${this.h(topic.name)}"></label><div class="row"><label>Kategorie<input name="category" maxlength="100" value="${this.h(topic.category)}"></label></div><label>Beschreibung<textarea name="description" maxlength="5000">${this.h(topic.description)}</textarea></label><label>Checkliste<textarea name="checklist" placeholder="Ein Punkt pro Zeile">${this.h((topic.checklist || []).join("\n"))}</textarea></label><div class="form-actions"><button type="button" class="secondary" data-close>Abbrechen</button><button class="primary" type="submit">Speichern</button></div></form></div></div>`;
  }

  routineModal(routine) {
    const schedule = routine.schedule || {};
    const selectedTopics = new Set(routine.topic_ids || []);
    const selectedWeekdays = new Set(schedule.weekdays || [new Date().getDay() === 0 ? 6 : new Date().getDay() - 1]);
    const start = schedule.start_date || new Date().toISOString().slice(0, 10);
    return `<div class="modal-backdrop"><div class="modal"><div class="modal-head"><h2>${routine.id ? "Routine bearbeiten" : "Routine anlegen"}</h2><button class="icon-btn" type="button" data-close><ha-icon icon="mdi:close"></ha-icon></button></div><form data-routine-form><input type="hidden" name="id" value="${this.h(routine.id)}"><label>Name *<input name="name" required maxlength="200" value="${this.h(routine.name)}"></label><label>Beschreibung<textarea name="description">${this.h(routine.description)}</textarea></label><label class="check"><input name="enabled" type="checkbox" ${routine.enabled !== false ? "checked" : ""}> Routine aktiv</label><label>Topics *</label><div class="checks">${this._data.topics.map((topic) => `<label class="check"><input type="checkbox" name="topic_ids" value="${topic.id}" ${selectedTopics.has(topic.id) ? "checked" : ""}> ${this.h(topic.name)}</label>`).join("")}</div><div class="row"><label>Wiederholung<select name="frequency"><option value="once" ${schedule.frequency === "once" ? "selected" : ""}>Einmalig</option><option value="daily" ${schedule.frequency === "daily" ? "selected" : ""}>Täglich</option><option value="weekly" ${schedule.frequency === "weekly" ? "selected" : ""}>Wöchentlich</option><option value="monthly" ${!schedule.frequency || schedule.frequency === "monthly" ? "selected" : ""}>Monatlich</option><option value="yearly" ${schedule.frequency === "yearly" ? "selected" : ""}>Jährlich</option></select></label><label>Intervall<input name="interval" type="number" min="1" max="100" value="${schedule.interval || 1}"></label></div><div class="row"><label>Startdatum<input name="start_date" type="date" required value="${this.h(start)}"></label><label>Uhrzeit<input name="due_time" type="time" required value="${this.h(routine.due_time || this._data.options.default_reminder_time)}"></label></div><label>Wochentage (bei wöchentlicher Wiederholung)</label><div class="checks">${WEEKDAYS.map((day, i) => `<label class="check"><input type="checkbox" name="weekdays" value="${i}" ${selectedWeekdays.has(i) ? "checked" : ""}> ${day}</label>`).join("")}</div><div class="row"><label>Monatstage<input name="month_days" value="${this.h((schedule.month_days || [new Date(`${start}T12:00:00`).getDate()]).join(", "))}" placeholder="1, 15, 28"><span class="hint">Für monatlich und jährlich</span></label><label>Monate<input name="months" value="${this.h((schedule.months || [new Date(`${start}T12:00:00`).getMonth() + 1]).join(", "))}" placeholder="1, 4, 7, 10"><span class="hint">Nur für jährlich</span></label></div><label>Einmalige Fälligkeitstage<input name="dates" value="${this.h((schedule.dates || []).join(", "))}" placeholder="2026-08-20, 2026-09-15"><span class="hint">Nur bei „Einmalig“; mehrere Daten sind möglich.</span></label><label>Erinnerungen in Tagen relativ zur Fälligkeit<input name="reminder_offsets" value="${this.h((routine.reminder_offsets || [-1, 0]).join(", "))}" placeholder="-7, -1, 0, 3"><span class="hint">Negativ = vorher, 0 = am Termin, positiv = danach.</span></label><div class="form-actions"><button type="button" class="secondary" data-close>Abbrechen</button><button class="primary" type="submit">Speichern</button></div></form></div></div>`;
  }

  invoiceModal(invoice) {
    const today = new Date().toISOString().slice(0, 10);
    return `<div class="modal-backdrop"><div class="modal"><div class="modal-head"><h2>${invoice.id ? "Buchung bearbeiten" : "Buchung anlegen"}</h2><button class="icon-btn" type="button" data-close><ha-icon icon="mdi:close"></ha-icon></button></div><form data-invoice-form><input type="hidden" name="id" value="${this.h(invoice.id)}"><div class="row"><label>Typ *<select name="direction"><option value="payable" ${invoice.direction !== "receivable" ? "selected" : ""}>Eingangsrechnung</option><option value="receivable" ${invoice.direction === "receivable" ? "selected" : ""}>Ausgangsrechnung</option></select></label><label>Status<select name="status"><option value="open" ${!invoice.status || invoice.status === "open" ? "selected" : ""}>Offen</option><option value="paid" ${invoice.status === "paid" ? "selected" : ""}>Bezahlt</option><option value="cancelled" ${invoice.status === "cancelled" ? "selected" : ""}>Storniert</option></select></label></div><div class="row"><label>Kontakt *<input name="contact" required maxlength="300" value="${this.h(invoice.contact)}"></label><label>Rechnungsnummer *<input name="invoice_number" required maxlength="200" value="${this.h(invoice.invoice_number)}"></label></div><div class="row"><label>Rechnungsdatum *<input name="invoice_date" type="date" required value="${this.h(invoice.invoice_date || today)}"></label><label>Fälligkeitsdatum *<input name="due_date" type="date" required value="${this.h(invoice.due_date || today)}"></label></div><div class="row"><label>Nettobetrag<input name="net_amount" type="number" min="0" step="0.01" value="${this.h(invoice.net_amount || "0.00")}"></label><label>Steuerbetrag<input name="tax_amount" type="number" min="0" step="0.01" value="${this.h(invoice.tax_amount || "0.00")}"></label></div><div class="row"><label>Bruttobetrag<input name="gross_amount" type="number" min="0" step="0.01" value="${this.h(invoice.gross_amount || "")}" placeholder="Wird sonst berechnet"></label><label>Währung<input name="currency" maxlength="3" value="${this.h(invoice.currency || "EUR")}"></label></div><div class="row"><label>Bezahlt am<input name="paid_date" type="date" value="${this.h(invoice.paid_date)}"></label><label>Mahnstufe<input name="dunning_level" type="number" min="0" max="9" value="${invoice.dunning_level || 0}"></label></div><label>Zahlungserinnerungen relativ zur Fälligkeit<input name="payment_reminder_offsets" value="${this.h((invoice.payment_reminder_offsets || [-7, -1, 0]).join(", "))}"><span class="hint">Beispiel: -7, -1, 0</span></label><label>Mahnfristen nach Fälligkeit<input name="dunning_offsets" value="${this.h((invoice.dunning_offsets || [3, 10, 20]).join(", "))}"><span class="hint">Für Ausgangsrechnungen, z. B. 3, 10, 20</span></label><label>Notiz<textarea name="note">${this.h(invoice.note)}</textarea></label><div class="form-actions"><button type="button" class="secondary" data-close>Abbrechen</button><button class="primary" type="submit">Speichern</button></div></form></div></div>`;
  }

  importModal(preview) {
    const errors = preview.errors || [];
    const warnings = preview.warnings || [];
    return `<div class="modal-backdrop"><div class="modal"><div class="modal-head"><h2>Excel-Import prüfen</h2><button class="icon-btn" type="button" data-close><ha-icon icon="mdi:close"></ha-icon></button></div><div class="import-summary"><div class="stat"><div class="label">Neu</div><div class="value">${preview.created || 0}</div></div><div class="stat"><div class="label">Aktualisiert</div><div class="value">${preview.updated || 0}</div></div><div class="stat"><div class="label">Warnungen</div><div class="value">${warnings.length}</div></div><div class="stat"><div class="label">Fehler</div><div class="value">${errors.length}</div></div></div>${errors.length ? `<h3>Bitte in Excel korrigieren</h3><ul class="error-list">${errors.slice(0, 50).map((error) => `<li>Zeile ${error.row}: ${this.h(error.message)}</li>`).join("")}</ul>` : `<p>${preview.records.length} Datensätze sind gültig und können importiert werden.</p>`}${warnings.length ? `<h3>Hinweise</h3><ul>${warnings.slice(0, 50).map((warning) => `<li>Zeile ${warning.row}: ${this.h(warning.message)}</li>`).join("")}</ul>` : ""}<div class="form-actions"><button type="button" class="secondary" data-close>Abbrechen</button><button class="primary" data-apply-import ${errors.length || !preview.records.length ? "disabled" : ""}>Importieren</button></div></div></div>`;
  }

  bindEvents() {
    this.shadowRoot.querySelectorAll("[data-tab]").forEach((el) => el.addEventListener("click", () => { this._tab = el.dataset.tab; this._modal = null; this.render(); }));
    this.shadowRoot.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", () => { this._modal = null; this._error = ""; this.render(); }));
    this.shadowRoot.querySelector("[data-new-topic]")?.addEventListener("click", () => { this._modal = { type: "topic", value: {} }; this.render(); });
    this.shadowRoot.querySelector("[data-new-routine]")?.addEventListener("click", () => { this._modal = { type: "routine", value: {} }; this.render(); });
    this.shadowRoot.querySelector("[data-new-invoice]")?.addEventListener("click", () => { this._modal = { type: "invoice", value: {} }; this.render(); });
    this.shadowRoot.querySelector("[data-go-accounting]")?.addEventListener("click", () => { this._tab = "accounting"; this.render(); });
    this.shadowRoot.querySelectorAll("[data-edit-topic]").forEach((el) => el.addEventListener("click", () => { this._modal = { type: "topic", value: this._data.topics.find((x) => x.id === el.dataset.editTopic) }; this.render(); }));
    this.shadowRoot.querySelectorAll("[data-edit-routine]").forEach((el) => el.addEventListener("click", () => { this._modal = { type: "routine", value: this._data.routines.find((x) => x.id === el.dataset.editRoutine) }; this.render(); }));
    this.shadowRoot.querySelectorAll("[data-edit-invoice]").forEach((el) => el.addEventListener("click", () => { this._modal = { type: "invoice", value: this._data.invoices.find((x) => x.id === el.dataset.editInvoice) }; this.render(); }));
    this.shadowRoot.querySelectorAll("[data-delete-topic]").forEach((el) => el.addEventListener("click", () => this.remove("topic", el.dataset.deleteTopic)));
    this.shadowRoot.querySelectorAll("[data-delete-routine]").forEach((el) => el.addEventListener("click", () => this.remove("routine", el.dataset.deleteRoutine)));
    this.shadowRoot.querySelectorAll("[data-delete-invoice]").forEach((el) => el.addEventListener("click", () => this.remove("invoice", el.dataset.deleteInvoice)));
    this.shadowRoot.querySelectorAll("[data-pay-invoice]").forEach((el) => el.addEventListener("click", () => this.markInvoicePaid(el.dataset.payInvoice)));
    this.shadowRoot.querySelectorAll("[data-status]").forEach((el) => el.addEventListener("click", () => this.setStatus(el.dataset.id, el.dataset.status)));
    this.shadowRoot.querySelector("[data-topic-form]")?.addEventListener("submit", (event) => this.saveTopic(event));
    this.shadowRoot.querySelector("[data-routine-form]")?.addEventListener("submit", (event) => this.saveRoutine(event));
    this.shadowRoot.querySelector("[data-invoice-form]")?.addEventListener("submit", (event) => this.saveInvoice(event));
    this.shadowRoot.querySelector("[data-template]")?.addEventListener("click", () => this.downloadExcel(true));
    this.shadowRoot.querySelector("[data-export]")?.addEventListener("click", () => this.downloadExcel(false));
    this.shadowRoot.querySelector("[data-csv]")?.addEventListener("click", () => this.downloadCsv());
    this.shadowRoot.querySelectorAll("[data-pdf-invoice]").forEach((el) => el.addEventListener("click", () => this.downloadPdf(el.dataset.pdfInvoice)));
    this.shadowRoot.querySelector("[data-import]")?.addEventListener("click", () => this.shadowRoot.querySelector("[data-file-input]").click());
    this.shadowRoot.querySelector("[data-file-input]")?.addEventListener("change", (event) => this.previewImport(event.target.files[0]));
    this.shadowRoot.querySelector("[data-apply-import]")?.addEventListener("click", () => this.applyImport());
    this.shadowRoot.querySelector("[data-test-notification]")?.addEventListener("click", () => this.testNotification());
    this.shadowRoot.querySelectorAll("[data-user-role]").forEach((el) => el.addEventListener("change", () => this.setUserRole(el.dataset.userRole, el.value)));
  }

  values(input) {
    return input.split(",").map((x) => x.trim()).filter(Boolean);
  }

  async saveTopic(event) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    const topic = { id: form.get("id") || undefined, name: form.get("name"), category: form.get("category"), description: form.get("description"), checklist: String(form.get("checklist") || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean) };
    await this.mutate("structuraloffice/upsert_topic", { topic });
  }

  async saveRoutine(event) {
    event.preventDefault(); const node = event.currentTarget; const form = new FormData(node);
    const routine = { id: form.get("id") || undefined, name: form.get("name"), description: form.get("description"), enabled: form.has("enabled"), topic_ids: form.getAll("topic_ids"), due_time: form.get("due_time"), reminder_offsets: this.values(String(form.get("reminder_offsets"))).map(Number), schedule: { frequency: form.get("frequency"), interval: Number(form.get("interval")), start_date: form.get("start_date"), weekdays: form.getAll("weekdays").map(Number), month_days: this.values(String(form.get("month_days"))).map(Number), months: this.values(String(form.get("months"))).map(Number), dates: this.values(String(form.get("dates"))) } };
    await this.mutate("structuraloffice/upsert_routine", { routine });
  }

  async saveInvoice(event) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    const invoice = { id: form.get("id") || undefined, direction: form.get("direction"), status: form.get("status"), contact: form.get("contact"), invoice_number: form.get("invoice_number"), invoice_date: form.get("invoice_date"), due_date: form.get("due_date"), net_amount: form.get("net_amount"), tax_amount: form.get("tax_amount"), gross_amount: form.get("gross_amount"), currency: form.get("currency"), paid_date: form.get("paid_date") || null, dunning_level: Number(form.get("dunning_level")), payment_reminder_offsets: this.values(String(form.get("payment_reminder_offsets"))).map(Number), dunning_offsets: this.values(String(form.get("dunning_offsets"))).map(Number), note: form.get("note") };
    await this.mutate("structuraloffice/upsert_invoice", { invoice });
  }

  async markInvoicePaid(id) {
    const source = this._data.invoices.find((item) => item.id === id);
    if (!source) return;
    await this.mutate("structuraloffice/upsert_invoice", { invoice: { ...source, status: "paid", paid_date: new Date().toISOString().slice(0, 10) } });
  }

  async downloadExcel(empty) {
    this._error = "";
    try {
      const result = await this.call("structuraloffice/export_invoices", { empty });
      const binary = atob(result.content); const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
      const link = document.createElement("a"); link.href = url; link.download = result.filename; link.click(); URL.revokeObjectURL(url);
    } catch (error) { this._error = this.errorText(error); this.render(); }
  }

  downloadResult(result, mime) {
    const binary = atob(result.content); const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
    const link = document.createElement("a"); link.href = url; link.download = result.filename; link.click(); URL.revokeObjectURL(url);
  }

  async downloadCsv() {
    try { this.downloadResult(await this.call("structuraloffice/export_invoices_csv"), "text/csv;charset=utf-8"); }
    catch (error) { this._error = this.errorText(error); this.render(); }
  }

  async downloadPdf(id) {
    const invoice = this._data.invoices.find((item) => item.id === id);
    const suggested = invoice?.dunning_level ? `dunning_${Math.min(3, invoice.dunning_level)}` : "payment_reminder";
    const input = prompt("Dokumenttyp: payment_reminder, dunning_1, dunning_2 oder dunning_3", suggested);
    if (!input) return;
    try { this.downloadResult(await this.call("structuraloffice/generate_invoice_pdf", { invoice_id: id, document_type: input }), "application/pdf"); }
    catch (error) { this._error = this.errorText(error); this.render(); }
  }

  async setUserRole(userId, role) {
    await this.mutate("structuraloffice/set_user_role", { user_id: userId, role }, false, "Zugriffsrolle wurde aktualisiert.");
  }

  async previewImport(file) {
    if (!file) return;
    this._error = "";
    try {
      const content = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = reject; reader.readAsDataURL(file); });
      const preview = await this.call("structuraloffice/preview_invoice_import", { content });
      this._modal = { type: "import", value: preview }; this.render();
    } catch (error) { this._error = this.errorText(error); this.render(); }
  }

  async applyImport() {
    const records = this._modal?.value?.records || [];
    await this.mutate("structuraloffice/apply_invoice_import", { records }, true, `${records.length} Buchungen wurden importiert.`);
  }

  async remove(kind, id) {
    if (!confirm(`${kind === "topic" ? "Topic" : "Routine"} wirklich löschen?`)) return;
    await this.mutate(`structuraloffice/delete_${kind}`, { [`${kind}_id`]: id });
  }

  async setStatus(id, status) { await this.mutate("structuraloffice/set_occurrence_status", { occurrence_id: id, status }); }
  async testNotification() { await this.mutate("structuraloffice/test_notification", {}, false, "Testbenachrichtigung wurde gesendet."); }

  async mutate(type, payload, close = true, successMessage = "") {
    this._error = "";
    try {
      await this.call(type, payload);
      if (close) this._modal = null;
      await this.load(true);
      if (successMessage) this._hass.callService("persistent_notification", "create", { title: "StructuralOffice", message: successMessage });
    } catch (error) {
      this._error = this.errorText(error);
      this.render();
    }
  }
}

if (!customElements.get("structuraloffice-panel")) {
  customElements.define("structuraloffice-panel", StructuralOfficePanel);
}

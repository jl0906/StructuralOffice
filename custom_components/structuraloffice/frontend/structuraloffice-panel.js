class StructuralOfficePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.data = null;
    this.error = "";
    this.busy = false;
  }

  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this.data) this.load();
  }

  connectedCallback() {
    this.render();
    if (this._hass) this.load();
    if (this._hass?.connection && !this.unsubscribePromise) {
      this.unsubscribePromise = this._hass.connection.subscribeEvents(
        () => this.load(true),
        "structuraloffice_updated",
      );
    }
  }

  disconnectedCallback() {
    if (this.unsubscribePromise) this.unsubscribePromise.then((unsubscribe) => unsubscribe());
    this.unsubscribePromise = null;
  }

  async call(type, payload = {}) {
    return this._hass.connection.sendMessagePromise({ type, ...payload });
  }

  async load(silent = false) {
    if (!silent) this.busy = true;
    this.error = "";
    this.render();
    try {
      this.data = await this.call("structuraloffice/get_data");
    } catch (error) {
      this.error = error?.message || error?.body?.message || String(error);
    } finally {
      this.busy = false;
      this.render();
    }
  }

  escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  bytes(value) {
    const units = ["B", "KB", "MB", "GB"];
    let amount = Number(value || 0);
    let index = 0;
    while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; }
    return `${amount.toFixed(index ? 1 : 0)} ${units[index]}`;
  }

  date(value) {
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" })
      .format(new Date(value));
  }

  render() {
    const style = `
      :host{display:block;min-height:100%;background:var(--primary-background-color);color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,sans-serif)}
      *{box-sizing:border-box}.shell{max-width:1080px;margin:auto;padding:28px clamp(14px,3vw,36px) 60px}
      header{display:flex;align-items:center;gap:14px;margin-bottom:26px}.logo{width:48px;height:48px;display:grid;place-items:center;border-radius:14px;background:#0f766e;color:#fff}h1,h2{margin:0}.sub,.label,.meta{color:var(--secondary-text-color)}.sub{margin-top:4px}
      .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:18px 0 28px}.card,.panel{background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:16px;box-shadow:var(--ha-card-box-shadow,none)}.card{padding:18px}.value{font-size:30px;font-weight:750;margin-top:9px}.panel{padding:20px;margin-top:14px}.head{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:15px}
      button{border:0;border-radius:10px;padding:10px 14px;background:#0f766e;color:#fff;font:inherit;font-weight:650;cursor:pointer}button.secondary{background:var(--secondary-background-color);color:var(--primary-text-color)}button.danger{background:#b91c1c}button:disabled{opacity:.5;cursor:wait}.error{padding:12px;border-radius:10px;background:#b91c1c;color:#fff;margin-bottom:15px}
      table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px 8px;border-bottom:1px solid var(--divider-color)}th{color:var(--secondary-text-color);font-size:12px}.actions{display:flex;gap:7px;justify-content:flex-end}.good{color:#15803d;font-weight:700}.bad{color:#b91c1c;font-weight:700}.empty{padding:25px;text-align:center;color:var(--secondary-text-color)}
      @media(max-width:760px){.grid{grid-template-columns:1fr 1fr}.head{align-items:flex-start;flex-direction:column}.table-wrap{overflow:auto}}
    `;
    const body = !this.data
      ? `<div class="empty">${this.busy ? "Loading StructuralOffice…" : "No data available."}</div>`
      : this.content();
    this.shadowRoot.innerHTML = `<style>${style}</style><main class="shell"><header><div class="logo"><ha-icon icon="mdi:office-building-cog"></ha-icon></div><div><h1>StructuralOffice</h1><div class="sub">Home Assistant backend administration · ${this.escape(this.data?.version || "")}</div></div></header>${this.error ? `<div class="error">${this.escape(this.error)}</div>` : ""}${body}</main>`;
    this.bind();
  }

  content() {
    const db = this.data.database || {};
    const backups = this.data.backups || [];
    return `<section><h2>Database</h2><div class="grid">
      ${this.stat("Database size", this.bytes(db.database_bytes), "mdi:database")}
      ${this.stat("Stored records", db.record_count || 0, "mdi:table-row")}
      ${this.stat("CSV imports", db.import_count || 0, "mdi:file-delimited")}
      ${this.stat("Integrity", db.integrity || "unknown", db.integrity === "ok" ? "mdi:check-decagram" : "mdi:alert")}
    </div></section><section class="panel"><div class="head"><div><h2>Database backups</h2><div class="sub">Backups are stored locally inside the StructuralOffice data directory.</div></div><button data-create ${this.busy ? "disabled" : ""}><ha-icon icon="mdi:database-plus"></ha-icon> Create backup</button></div>
      <div class="table-wrap">${backups.length ? `<table><thead><tr><th>Created</th><th>Size</th><th>Filename</th><th></th></tr></thead><tbody>${backups.map((backup) => `<tr><td>${this.escape(this.date(backup.created_at))}</td><td>${this.bytes(backup.size_bytes)}</td><td>${this.escape(backup.filename)}</td><td><div class="actions"><button class="secondary" data-download="${this.escape(backup.filename)}">Download</button><button class="secondary" data-restore="${this.escape(backup.filename)}">Restore</button><button class="danger" data-delete="${this.escape(backup.filename)}">Delete</button></div></td></tr>`).join("")}</tbody></table>` : `<div class="empty">No backups have been created.</div>`}</div></section>`;
  }

  stat(label, value, icon) {
    return `<article class="card"><div class="label"><ha-icon icon="${icon}"></ha-icon> ${this.escape(label)}</div><div class="value ${label === "Integrity" ? (value === "ok" ? "good" : "bad") : ""}">${this.escape(value)}</div></article>`;
  }

  bind() {
    this.shadowRoot.querySelector("[data-create]")?.addEventListener("click", async () => {
      await this.action(() => this.call("structuraloffice/create_backup"));
    });
    this.shadowRoot.querySelectorAll("[data-restore]").forEach((button) => button.addEventListener("click", async () => {
      if (confirm(`Restore ${button.dataset.restore}? A safety backup will be created first.`)) {
        await this.action(() => this.call("structuraloffice/restore_backup", { filename: button.dataset.restore }));
      }
    }));
    this.shadowRoot.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", async () => {
      if (confirm(`Delete ${button.dataset.delete}?`)) {
        await this.action(() => this.call("structuraloffice/delete_backup", { filename: button.dataset.delete }));
      }
    }));
    this.shadowRoot.querySelectorAll("[data-download]").forEach((button) => button.addEventListener("click", () => this.download(button.dataset.download)));
  }

  async action(operation) {
    this.busy = true; this.error = ""; this.render();
    try { await operation(); await this.load(true); }
    catch (error) { this.error = error?.message || String(error); }
    finally { this.busy = false; this.render(); }
  }

  async download(filename) {
    try {
      const response = await fetch(`/api/structuraloffice/v1/backups/${encodeURIComponent(filename)}`, {
        headers: { Authorization: `Bearer ${this._hass.auth.data.access_token}` },
      });
      if (!response.ok) throw new Error(await response.text());
      const link = document.createElement("a");
      link.href = URL.createObjectURL(await response.blob()); link.download = filename; link.click();
      URL.revokeObjectURL(link.href);
    } catch (error) { this.error = error?.message || String(error); this.render(); }
  }
}

customElements.define("structuraloffice-panel", StructuralOfficePanel);

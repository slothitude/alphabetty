/* Alphabetty — Dashboard Module
   Tab management, macro controls, system status
*/
const dashboard = {

    async load(container) {
        container.innerHTML = `
            <div class="dash-section">
                <div class="dash-header">Chrome Tabs</div>
                <div id="dash-tabs" class="dash-list"></div>
                <div class="dash-actions">
                    <input id="dash-new-url" placeholder="URL..." class="dash-input">
                    <button class="dash-btn" onclick="dashboard.newTab()">+ Tab</button>
                </div>
            </div>
            <div class="dash-section">
                <div class="dash-header">Macros</div>
                <div id="dash-macros" class="dash-list"></div>
                <div class="dash-actions">
                    <input id="dash-macro-name" placeholder="Macro name..." class="dash-input">
                    <button class="dash-btn" onclick="dashboard.startRecording()">Record</button>
                    <button class="dash-btn dash-btn-stop" id="dash-macro-stop" style="display:none" onclick="dashboard.stopRecording()">Stop</button>
                </div>
            </div>
            <div class="dash-section">
                <div class="dash-header">System</div>
                <div id="dash-system" class="dash-sys-grid"></div>
            </div>
        `;
        await this.refresh();
    },

    async refresh() {
        await Promise.all([
            this.loadTabs(),
            this.loadMacros(),
            this.loadSystem(),
        ]);
    },

    // ─── Tabs ───

    async loadTabs() {
        const el = document.getElementById('dash-tabs');
        if (!el) return;
        try {
            const r = await fetch('/api/cdp/tabs');
            const d = await r.json();
            const tabs = d.tabs || [];
            if (!tabs.length) {
                el.innerHTML = '<div class="dash-empty">No tabs open</div>';
                return;
            }
            el.innerHTML = tabs.map(t => `
                <div class="dash-tab-row" onclick="dashboard.activateTab('${t.id}')">
                    <span class="dash-tab-favicon">${this._favicon(t.url)}</span>
                    <span class="dash-tab-title" title="${this._esc(t.title || t.url)}">${this._esc(this._truncate(t.title || t.url, 28))}</span>
                    <button class="dash-tab-close" onclick="event.stopPropagation();dashboard.closeTab('${t.id}')">&times;</button>
                </div>
            `).join('');
        } catch {
            el.innerHTML = '<div class="dash-empty">Chrome offline</div>';
        }
    },

    async newTab() {
        const input = document.getElementById('dash-new-url');
        const url = input.value.trim() || 'about:blank';
        await fetch('/api/cdp/tab/new', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({url}),
        });
        input.value = '';
        this.loadTabs();
    },

    async closeTab(id) {
        await fetch('/api/cdp/tab/close', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({target_id: id}),
        });
        this.loadTabs();
    },

    async activateTab(id) {
        await fetch('/api/cdp/tab/activate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({target_id: id}),
        });
        this.loadTabs();
    },

    // ─── Macros ───

    async loadMacros() {
        const el = document.getElementById('dash-macros');
        if (!el) return;
        try {
            const r = await fetch('/api/cdp/macro/list');
            const d = await r.json();
            const macros = d.macros || [];
            if (!macros.length) {
                el.innerHTML = '<div class="dash-empty">No macros saved</div>';
                return;
            }
            el.innerHTML = macros.map(m => `
                <div class="dash-macro-row">
                    <div class="dash-macro-info">
                        <span class="dash-macro-name">${this._esc(m.name)}</span>
                        <span class="dash-macro-meta">${m.step_count} steps</span>
                    </div>
                    <div class="dash-macro-actions">
                        <button class="dash-btn-sm" onclick="dashboard.playMacro(${m.id})">Play</button>
                        <button class="dash-btn-sm dash-btn-danger" onclick="dashboard.deleteMacro(${m.id})">Del</button>
                    </div>
                </div>
            `).join('');
        } catch {
            el.innerHTML = '<div class="dash-empty">Failed to load</div>';
        }
    },

    async startRecording() {
        const name = document.getElementById('dash-macro-name').value.trim() || 'macro-' + Date.now();
        await fetch('/api/cdp/macro/record/start', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name}),
        });
        document.getElementById('dash-macro-stop').style.display = '';
        app.showToast('Recording started: ' + name, 'info');
    },

    async stopRecording() {
        const r = await fetch('/api/cdp/macro/record/stop', {method: 'POST'});
        const d = await r.json();
        document.getElementById('dash-macro-stop').style.display = 'none';
        app.showToast(`Saved: ${d.name || 'macro'} (${d.step_count || 0} steps)`, 'success');
        this.loadMacros();
    },

    async playMacro(id) {
        app.showToast('Playing macro...', 'info');
        const r = await fetch('/api/cdp/macro/play', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({macro_id: id}),
        });
        const d = await r.json();
        if (d.status === 'played') {
            app.showToast(`Played: ${d.steps_played}/${d.steps_total} steps`, 'success');
        } else {
            app.showToast(`Macro error: ${d.error || 'failed'}`, 'error');
        }
    },

    async deleteMacro(id) {
        await fetch(`/api/cdp/macro/${id}`, {method: 'DELETE'});
        this.loadMacros();
    },

    // ─── System ───

    async loadSystem() {
        const el = document.getElementById('dash-system');
        if (!el) return;
        let chromeStatus = 'offline';
        let tabCount = 0;
        let ua = '-';
        try {
            const r = await fetch('/api/cdp/status');
            const d = await r.json();
            chromeStatus = d.status;
            tabCount = d.tab_count || 0;
            ua = (d.user_agent || '-').substring(0, 40);
        } catch {}

        let convCount = '-';
        let entityCount = '-';
        try {
            const r = await fetch('/api/conversations');
            const d = await r.json();
            convCount = d.length || 0;
        } catch {}

        el.innerHTML = `
            <div class="dash-sys-item">
                <span class="dash-sys-label">Chrome</span>
                <span class="dash-sys-val ${chromeStatus === 'running' ? 'ok' : 'err'}">${chromeStatus}</span>
            </div>
            <div class="dash-sys-item">
                <span class="dash-sys-label">Tabs</span>
                <span class="dash-sys-val">${tabCount}</span>
            </div>
            <div class="dash-sys-item">
                <span class="dash-sys-label">Threads</span>
                <span class="dash-sys-val">${convCount}</span>
            </div>
            <div class="dash-sys-item">
                <span class="dash-sys-label">UA</span>
                <span class="dash-sys-val" style="font-size:10px">${ua}</span>
            </div>
        `;
    },

    // ─── Helpers ───

    _esc(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    },

    _truncate(s, n) {
        return s.length > n ? s.substring(0, n) + '...' : s;
    },

    _favicon(url) {
        if (!url || url === 'about:blank') return '';
        try {
            const u = new URL(url);
            return `<img src="https://www.google.com/s2/favicons?domain=${u.hostname}&sz=16" width="14" height="14" style="vertical-align:middle;border-radius:2px">`;
        } catch {
            return '';
        }
    },
};

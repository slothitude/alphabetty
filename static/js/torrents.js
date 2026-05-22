/* Alphabetty — Torrent Panel */
const torrents = {
    pollTimer: null,

    load(container) {
        container.innerHTML = `
            <div class="torrent-panel">
                <div class="torrent-header">
                    <input id="torrent-search-input" placeholder="Search torrents..." onkeydown="if(event.key==='Enter')torrents.search()">
                    <button class="icon-btn" onclick="torrents.search()" title="Search">&#128269;</button>
                </div>
                <div id="torrent-add-row" style="display:flex;gap:6px;padding:4px 0 8px;border-bottom:1px solid var(--border);margin-bottom:8px">
                    <input id="torrent-magnet-input" placeholder="Paste magnet link..." onkeydown="if(event.key==='Enter')torrents.addMagnet()">
                    <button class="icon-btn" onclick="torrents.addMagnet()" title="Add">&#10010;</button>
                </div>
                <div id="torrent-list">
                    <div class="torrent-status">Loading torrents...</div>
                </div>
                <div id="torrent-results" style="margin-top:12px"></div>
            </div>
        `;
        this.refreshList();
        // Auto-refresh every 10s while tab is active
        if (this.pollTimer) clearInterval(this.pollTimer);
        this.pollTimer = setInterval(() => this.refreshList(), 10000);
    },

    async refreshList() {
        const el = document.getElementById('torrent-list');
        if (!el) return;

        try {
            const r = await fetch('/api/torrents/list');
            const d = await r.json();
            if (!d.torrents || !d.torrents.length) {
                el.innerHTML = '<div class="torrent-status">No active torrents</div>';
                return;
            }
            el.innerHTML = d.torrents.map(t => {
                const pct = (t.percentDone * 100).toFixed(1);
                const done = t.percentDone >= 1;
                const speed = t.rateDownload ? `${(t.rateDownload / 1024).toFixed(0)} KB/s` : '';
                const size = t.totalSize ? `${(t.totalSize / 1073741824).toFixed(2)} GB` : '';
                return `
                    <div class="torrent-item">
                        <div class="torrent-name" title="${app.escapeHtml(t.name)}">${app.escapeHtml(t.name)}</div>
                        <div class="torrent-bar-bg"><div class="torrent-bar ${done ? 'done' : ''}" style="width:${pct}%"></div></div>
                        <div class="torrent-meta">
                            <span>${pct}%</span>
                            <span>${size}</span>
                            ${speed ? `<span>&#8595; ${speed}</span>` : ''}
                            ${done ? '<span style="color:var(--success)">Complete</span>' : ''}
                        </div>
                        <div class="torrent-actions">
                            ${done ? `<button onclick="torrents.stream(${t.id})">&#9654; Stream</button>` : ''}
                            <button class="danger" onclick="torrents.remove(${t.id},'${app.escapeHtml(t.name)}')">&#10005; Remove</button>
                        </div>
                    </div>
                `;
            }).join('');
        } catch (e) {
            el.innerHTML = `<div class="torrent-status">Transmission offline</div>`;
        }
    },

    async search() {
        const input = document.getElementById('torrent-search-input');
        const resultsEl = document.getElementById('torrent-results');
        if (!input || !resultsEl) return;

        const q = input.value.trim();
        if (!q) return;

        resultsEl.innerHTML = '<div class="torrent-status">Searching...</div>';

        try {
            const r = await fetch('/api/torrents/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: q }),
            });
            const d = await r.json();

            if (!d.results || !d.results.length) {
                resultsEl.innerHTML = '<div class="torrent-status">No results found</div>';
                return;
            }

            resultsEl.innerHTML = '<div style="font-size:10px;color:var(--t4);margin-bottom:6px">Search Results</div>' +
                d.results.map(r => `
                    <div class="torrent-item torrent-results">
                        <div class="torrent-name">${app.escapeHtml(r.title)}</div>
                        <div class="torrent-meta">
                            <span>S:${r.seeders || '?'}</span>
                            <span>L:${r.leechers || '?'}</span>
                            <span>${r.size || '?'}</span>
                            <span>${r.engine || ''}</span>
                        </div>
                        <div class="torrent-actions">
                            <button onclick="torrents.livestream('${app.escapeHtml(r.magnet)}')">&#9654; Stream</button>
                            <button onclick="torrents.addLink('${app.escapeHtml(r.magnet || r.url)}')">&#10010; Add</button>
                        </div>
                    </div>
                `).join('');
        } catch (e) {
            resultsEl.innerHTML = `<div class="torrent-status">Search failed: ${e.message}</div>`;
        }
    },

    async addMagnet() {
        const input = document.getElementById('torrent-magnet-input');
        if (!input) return;
        const url = input.value.trim();
        if (!url) return;

        try {
            const r = await fetch('/api/torrents/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url }),
            });
            const d = await r.json();
            app.showToast(`Added: ${d.name || 'torrent'}`, 'success');
            input.value = '';
            this.refreshList();
        } catch (e) {
            app.showToast(`Add failed: ${e.message}`, 'error');
        }
    },

    async addLink(url) {
        try {
            const r = await fetch('/api/torrents/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url }),
            });
            const d = await r.json();
            app.showToast(`Added: ${d.name || 'torrent'}`, 'success');
            this.refreshList();
        } catch (e) {
            app.showToast(`Add failed: ${e.message}`, 'error');
        }
    },

    async stream(torrentId) {
        // Set video src directly — FileResponse handles range requests natively
        app.playVideo({ type: 'direct', stream_url: `/api/torrents/stream/${torrentId}`, title: 'Torrent Stream' });
    },

    async livestream(magnet) {
        try {
            app.showToast('Starting stream...', 'info');
            const r = await fetch('/api/torrents/livestream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ magnet }),
            });
            if (!r.ok) {
                const err = await r.json().catch(() => ({ detail: 'Stream failed' }));
                app.showToast(err.detail || 'Stream failed', 'error');
                return;
            }
            const d = await r.json();
            const sizeStr = d.file_size > 1073741824
                ? `${(d.file_size / 1073741824).toFixed(1)} GB`
                : `${(d.file_size / 1048576).toFixed(0)} MB`;
            app.showToast(`Streaming: ${d.file_name} (${sizeStr})`, 'success');
            // Set video src to proxy endpoint — handles range requests for seeking
            app.playVideo({
                type: 'direct',
                stream_url: `/api/torrents/livestream/${d.info_hash}`,
                title: d.file_name || 'Live Stream',
            });
        } catch (e) {
            app.showToast(`Stream failed: ${e.message}`, 'error');
        }
    },

    async remove(torrentId, name) {
        if (!confirm(`Remove "${name}"?`)) return;
        try {
            await fetch(`/api/torrents/${torrentId}?delete_files=true`, { method: 'DELETE' });
            app.showToast(`Removed: ${name}`, 'success');
            this.refreshList();
        } catch (e) {
            app.showToast(`Remove failed: ${e.message}`, 'error');
        }
    },
};

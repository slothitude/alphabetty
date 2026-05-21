/* Alphabetty — Spaces management */

const spaces = {
    async load(container) {
        const resp = await fetch('/api/spaces');
        const data = await resp.json();
        container.innerHTML = data.map(s => `
            <div class="sidebar-item" onclick="spaces.open(${s.id})">
                <span style="font-size:12px;color:${s.color}">●</span>
                <span class="item-title">${app.escapeHtml(s.name)}</span>
                <span style="font-size:11px;color:var(--text-muted)">${s.conversation_count}</span>
                <button class="item-delete" onclick="event.stopPropagation();spaces.remove(${s.id})">✕</button>
            </div>
        `).join('') + `
            <div class="sidebar-item" onclick="spaces.create()" style="color:var(--text-muted);justify-content:center;font-size:13px">
                + New Space
            </div>
        `;
    },

    async create() {
        const name = prompt('Space name:');
        if (!name) return;
        await fetch('/api/spaces', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        const list = document.getElementById('sidebar-list');
        this.load(list);
    },

    async open(id) {
        const resp = await fetch(`/api/spaces/${id}`);
        const space = await resp.json();
        const list = document.getElementById('sidebar-list');

        list.innerHTML = `
            <div class="sidebar-item" onclick="app.switchTab('spaces')" style="color:var(--text-muted);font-size:12px">
                ← Back to Spaces
            </div>
            <div style="padding:12px;font-weight:600;color:${space.color}">${app.escapeHtml(space.name)}</div>
        ` + space.conversations.map(c => `
            <div class="sidebar-item" onclick="app.loadConversation(${c.id})">
                <span style="font-size:14px">💬</span>
                <span class="item-title">${app.escapeHtml(c.title)}</span>
            </div>
        `).join('');
    },

    async remove(id) {
        await fetch(`/api/spaces/${id}`, { method: 'DELETE' });
        const list = document.getElementById('sidebar-list');
        this.load(list);
    },

    async addToSpace(spaceId, convId) {
        await fetch(`/api/spaces/${spaceId}/add/${convId}`, { method: 'POST' });
    },
};

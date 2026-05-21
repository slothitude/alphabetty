/* Alphabetty — Local Graph Search + Tags */

const graph = {

    // Quick search bar (Ctrl+Shift+K)
    async search(query) {
        if (!query || query.length < 2) return;
        const resp = await fetch(`/api/graph/search?q=${encodeURIComponent(query)}&limit=10`);
        const data = await resp.json();
        return data;
    },

    // Get stats
    async stats() {
        const resp = await fetch('/api/graph/stats');
        return await resp.json();
    },

    // Entity graph
    async entity(name) {
        const resp = await fetch(`/api/graph/entity/${encodeURIComponent(name)}`);
        return await resp.json();
    },

    // Tags
    async listTags() {
        const resp = await fetch('/api/tags');
        return await resp.json();
    },

    async addTag(conversationId, tag) {
        await fetch('/api/tags/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ conversation_id: conversationId, tag }),
        });
    },

    async removeTag(conversationId, tag) {
        await fetch('/api/tags/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ conversation_id: conversationId, tag }),
        });
    },
};

// ─── Search overlay ───
app.showLocalSearch = function() {
    let overlay = document.getElementById('local-search-overlay');
    if (overlay) { overlay.remove(); return; }

    overlay = document.createElement('div');
    overlay.id = 'local-search-overlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:200;display:flex;align-items:flex-start;justify-content:center;padding-top:15vh;';

    const box = document.createElement('div');
    box.style.cssText = 'background:var(--bg-secondary);border:1px solid var(--border);border-radius:12px;width:600px;max-height:60vh;overflow:hidden;display:flex;flex-direction:column;';

    box.innerHTML = `
        <div style="padding:16px;border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:center">
            <span style="color:var(--text-muted)">🔍</span>
            <input id="local-search-input" placeholder="Search conversations, messages, entities..."
                style="flex:1;background:transparent;border:none;color:var(--text-primary);font-size:15px;outline:none">
            <span style="font-size:11px;color:var(--text-muted)">ESC to close</span>
        </div>
        <div id="local-search-results" style="padding:8px;overflow-y:auto;max-height:50vh"></div>
    `;

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    const input = document.getElementById('local-search-input');
    input.focus();

    // Debounced search
    let timer;
    input.oninput = () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
            const q = input.value.trim();
            if (q.length < 2) return;
            const data = await graph.search(q);
            renderLocalResults(data);
        }, 300);
    };

    input.onkeydown = (e) => {
        if (e.key === 'Escape') overlay.remove();
    };

    overlay.onclick = (e) => {
        if (e.target === overlay) overlay.remove();
    };
};

function renderLocalResults(data) {
    const container = document.getElementById('local-search-results');
    if (!container) return;

    let html = '';

    if (data.conversations.length) {
        html += '<div style="color:var(--text-muted);font-size:11px;padding:4px 8px;text-transform:uppercase">Conversations</div>';
        for (const c of data.conversations) {
            html += `<div class="sidebar-item" onclick="app.loadConversation(${c.id});document.getElementById('local-search-overlay').remove()">
                <span>💬</span>
                <span class="item-title">${app.escapeHtml(c.title)}</span>
                <span style="font-size:11px;color:var(--text-muted)">${c.updated_at ? new Date(c.updated_at).toLocaleDateString() : ''}</span>
            </div>`;
        }
    }

    if (data.messages.length) {
        html += '<div style="color:var(--text-muted);font-size:11px;padding:4px 8px;margin-top:8px;text-transform:uppercase">Messages</div>';
        for (const m of data.messages.slice(0, 10)) {
            const preview = m.content.slice(0, 120).replace(/\n/g, ' ');
            html += `<div class="sidebar-item" onclick="app.loadConversation(${m.conversation_id});document.getElementById('local-search-overlay').remove()">
                <span>${m.role === 'user' ? '👤' : '🤖'}</span>
                <span class="item-title" style="flex-direction:column">
                    <span style="font-size:13px">${app.escapeHtml(preview)}...</span>
                    <span style="font-size:11px;color:var(--text-muted)">${app.escapeHtml(m.conversation_title || '')}</span>
                </span>
            </div>`;
        }
    }

    if (data.entities.length) {
        html += '<div style="color:var(--text-muted);font-size:11px;padding:4px 8px;margin-top:8px;text-transform:uppercase">Entities</div>';
        for (const e of data.entities) {
            html += `<div class="sidebar-item" onclick="app.loadConversation(null)">
                <span>🏷️</span>
                <span class="item-title">${app.escapeHtml(e.name)}</span>
                <span style="font-size:11px;color:var(--text-muted)">${e.mention_count} mentions</span>
            </div>`;
        }
    }

    if (!data.total) {
        html = '<div style="padding:24px;text-align:center;color:var(--text-muted)">No results found</div>';
    }

    container.innerHTML = html;
}

// Keyboard shortcut: Ctrl+Shift+K for local search
document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && e.key === 'K') {
        e.preventDefault();
        app.showLocalSearch();
    }
});

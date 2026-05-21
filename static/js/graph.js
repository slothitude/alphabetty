/* Alphabetty — Local Graph Search + Tags */

const graph = {

    async search(query) {
        if (!query || query.length < 2) return;
        const resp = await fetch(`/api/graph/search?q=${encodeURIComponent(query)}&limit=10`);
        const data = await resp.json();
        return data;
    },

    async stats() {
        const resp = await fetch('/api/graph/stats');
        return await resp.json();
    },

    async entity(name) {
        const resp = await fetch(`/api/graph/entity/${encodeURIComponent(name)}`);
        return await resp.json();
    },

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

// ─── KG Thumbnail (mini constellation in left panel) ───

app.renderKGThumb = async function() {
    const canvas = document.getElementById('kg-canvas');
    if (!canvas) return;

    try {
        const stats = await graph.stats();
        if (!stats.entity_count || stats.entity_count === 0) return;

        const ctx = canvas.getContext('2d');
        const w = canvas.offsetWidth || 240;
        const h = 80;
        canvas.width = w * 2;
        canvas.height = h * 2;
        ctx.scale(2, 2);

        // Draw simple constellation
        const count = Math.min(stats.entity_count || 0, 20);
        const nodes = [];
        for (let i = 0; i < count; i++) {
            nodes.push({
                x: 20 + Math.random() * (w - 40),
                y: 10 + Math.random() * (h - 20),
                r: 2 + Math.random() * 2,
            });
        }

        // Draw connections
        ctx.strokeStyle = 'rgba(232, 160, 32, 0.15)';
        ctx.lineWidth = 0.5;
        for (let i = 0; i < nodes.length; i++) {
            for (let j = i + 1; j < nodes.length; j++) {
                const dx = nodes[i].x - nodes[j].x;
                const dy = nodes[i].y - nodes[j].y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < 60) {
                    ctx.beginPath();
                    ctx.moveTo(nodes[i].x, nodes[i].y);
                    ctx.lineTo(nodes[j].x, nodes[j].y);
                    ctx.stroke();
                }
            }
        }

        // Draw nodes
        for (const node of nodes) {
            ctx.beginPath();
            ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(232, 160, 32, 0.6)';
            ctx.fill();
        }
    } catch {}
};

// ─── Search overlay ───

app.showLocalSearch = function() {
    let overlay = document.getElementById('local-search-overlay');
    if (overlay) { overlay.remove(); return; }

    overlay = document.createElement('div');
    overlay.id = 'local-search-overlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:200;display:flex;align-items:flex-start;justify-content:center;padding-top:15vh;';

    const box = document.createElement('div');
    box.style.cssText = 'background:var(--ink2);border:1px solid var(--border);border-radius:10px;width:560px;max-height:55vh;overflow:hidden;display:flex;flex-direction:column;';

    box.innerHTML = `
        <div style="padding:14px;border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:center">
            <span style="color:var(--t4)">&#128269;</span>
            <input id="local-search-input" placeholder="Search conversations, messages, entities..."
                style="flex:1;background:transparent;border:none;color:var(--t1);font-size:14px;outline:none;font-family:var(--font-sans)">
            <span style="font-size:10px;color:var(--t4)">ESC</span>
        </div>
        <div id="local-search-results" style="padding:6px;overflow-y:auto;max-height:45vh"></div>
    `;

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    const input = document.getElementById('local-search-input');
    input.focus();

    let timer;
    input.oninput = () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
            const q = input.value.trim();
            if (q.length < 2) return;
            const data = await graph.search(q);
            renderLocalResults(data);
        }, 250);
    };

    input.onkeydown = (e) => { if (e.key === 'Escape') overlay.remove(); };
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
};

function renderLocalResults(data) {
    const container = document.getElementById('local-search-results');
    if (!container) return;

    let html = '';

    if (data.conversations && data.conversations.length) {
        html += '<div style="color:var(--t4);font-size:10px;padding:4px 8px;text-transform:uppercase">Conversations</div>';
        for (const c of data.conversations) {
            html += `<div class="nav-item" onclick="app.loadConversation(${c.id});document.getElementById('local-search-overlay').remove()">
                <span class="item-title" style="font-size:13px">${app.escapeHtml(c.title)}</span>
                <span style="font-size:10px;color:var(--t4)">${c.updated_at ? new Date(c.updated_at).toLocaleDateString() : ''}</span>
            </div>`;
        }
    }

    if (data.messages && data.messages.length) {
        html += '<div style="color:var(--t4);font-size:10px;padding:4px 8px;margin-top:6px;text-transform:uppercase">Messages</div>';
        for (const m of data.messages.slice(0, 8)) {
            const preview = m.content.slice(0, 100).replace(/\n/g, ' ');
            html += `<div class="nav-item" onclick="app.loadConversation(${m.conversation_id});document.getElementById('local-search-overlay').remove()">
                <span class="item-content">
                    <span style="font-size:12px">${app.escapeHtml(preview)}...</span>
                    <span style="font-size:10px;color:var(--t4)">${app.escapeHtml(m.conversation_title || '')}</span>
                </span>
            </div>`;
        }
    }

    if (data.entities && data.entities.length) {
        html += '<div style="color:var(--t4);font-size:10px;padding:4px 8px;margin-top:6px;text-transform:uppercase">Entities</div>';
        for (const e of data.entities) {
            html += `<div class="nav-item">
                <div class="entity-dot"></div>
                <span class="item-title">${app.escapeHtml(e.name)}</span>
                <span style="font-size:10px;color:var(--t4)">${e.mention_count}x</span>
            </div>`;
        }
    }

    if (!data.total) {
        html = '<div style="padding:20px;text-align:center;color:var(--t4);font-size:12px">No results found</div>';
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

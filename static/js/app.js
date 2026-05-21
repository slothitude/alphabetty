/* Alphabetty — App Core v2 */
const app = {
    currentConvId: null,
    conversations: [],
    streaming: false,
    proSearch: false,
    agentMode: true,
    domPanelOpen: false,
    sidebarFilter: '',
    activeTagFilter: null,
    viewportOpen: false,
    viewportMode: null, // 'mjpeg' | 'youtube' | null
    activeMode: 'agent',

    async init() {
        this.loadConversations();
        this.setupTextarea();
        this.setupSlashCommands();
        this.loadTheme();
        this.initStatusPolling();
        this.initFeedPanel();
        this.renderAmbientBar();
        this.renderKGThumb();
        this.loadSpaceChips();
        this.initEventStream();
        this.dismissSplash();
        // Reflect default agent mode in UI
        if (this.agentMode) {
            const ab = document.getElementById('agent-btn');
            const al = document.getElementById('agent-label');
            if (ab) ab.style.background = 'var(--gold-dim)';
            if (al) al.style.display = '';
        }
    },

    dismissSplash() {
        setTimeout(() => {
            const splash = document.getElementById('splash');
            if (splash) {
                splash.classList.add('fade-out');
                setTimeout(() => splash.remove(), 500);
            }
        }, 1200);
    },

    setupTextarea() {
        const ta = document.getElementById('chat-input');
        ta.addEventListener('input', () => {
            ta.style.height = 'auto';
            ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
            this.updateSlashAutocomplete(ta.value);
        });
    },

    handleInputKey(e) {
        const ac = document.getElementById('slash-autocomplete');
        if (ac && ac.style.display !== 'none') {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                this.navigateAutocomplete(e.key === 'ArrowDown' ? 1 : -1);
                return;
            }
            if (e.key === 'Enter' || e.key === 'Tab') {
                e.preventDefault();
                this.selectAutocompleteItem();
                return;
            }
            if (e.key === 'Escape') {
                this.hideSlashAutocomplete();
                return;
            }
        }

        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            this.send();
        }
    },

    // ─── Omnibar ───

    omnibarSubmit(val) {
        if (!val.trim()) return;
        document.getElementById('chat-input').value = val.trim();
        this.send();
    },

    fillInput(text) {
        const ta = document.getElementById('chat-input');
        ta.value = text;
        ta.focus();
    },

    // ─── Status Polling ───

    initStatusPolling() {
        const poll = async () => {
            try {
                const r = await fetch('/api/cdp/status');
                const d = await r.json();
                const el = document.getElementById('status-chrome');
                el.className = 'status-dot ' + (d.status === 'running' ? 'ok' : 'err');
            } catch { document.getElementById('status-chrome').className = 'status-dot err'; }

            try {
                const r = await fetch('/api/search?q=test&max_results=1');
                document.getElementById('status-search').className = 'status-dot ok';
            } catch { document.getElementById('status-search').className = 'status-dot warn'; }

            document.getElementById('status-llm').className = 'status-dot gold';
        };
        poll();
        setInterval(poll, 30000);
    },

    // ─── Event Stream (SSE) ───

    initEventStream() {
        try {
            const es = new EventSource('/api/events');
            es.onmessage = (e) => {
                try {
                    const event = JSON.parse(e.data);
                    this.handleEvent(event);
                } catch {}
            };
            es.onerror = () => {
                // Reconnect is handled automatically by EventSource
            };
        } catch (e) {
            console.warn('EventSource not available:', e);
        }
    },

    handleEvent(event) {
        const type = event.type;
        const data = event.data || {};

        // Route events to UI
        switch (type) {
            case 'page.loaded':
                this.appendTraceLog({ type: 'navigate', message: `Loaded: ${data.url || 'page'}` });
                break;
            case 'research.done':
                this.showToast(`Research complete: ${data.sources || 0} sources`, 'success');
                break;
            case 'macro.done':
                this.showToast(`Macro done: ${(data.step_count || data.steps_played || 0)} steps`, 'success');
                break;
            case 'recording.done':
                this.showToast(`Recording stopped: ${data.frames || 0} frames`, 'info');
                break;
            case 'youtube.playing':
                if (data.video_id) {
                    this.appendTraceLog({ type: 'youtube', message: `Playing: ${data.query || 'video'}` });
                }
                break;
            case 'agent.done':
                this.showToast(`Agent done: ${data.tool_calls || 0} tool calls`, 'success');
                break;
            case 'tab.created':
                this.appendTraceLog({ type: 'tab', message: `New tab: ${data.url || 'blank'}` });
                break;
            case 'signin.started':
                this.showToast(`Signing in to ${data.url || 'site'}...`, 'info');
                break;
            case 'signin.2fa_required':
                this.showToast(`2FA required — hint: ${data.hint || 'check your device'}`, 'error');
                break;
            case 'signin.done':
                this.showToast(`Signed in successfully (${data.method || 'password'})`, 'success');
                break;
            case 'signin.failed':
                this.showToast(`Sign-in failed: ${data.reason || data.error || 'unknown'}`, 'error');
                break;
        }
    },

    showToast(message, type = 'info') {
        const container = document.getElementById('toast-container');
        if (!container) return;

        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        container.appendChild(toast);

        // Auto-remove after 4s
        setTimeout(() => {
            toast.classList.add('fade-out');
            setTimeout(() => toast.remove(), 400);
        }, 4000);
    },

    // ─── Ambient Bar ───

    async renderAmbientBar() {
        const threadEl = document.getElementById('stat-threads');
        if (threadEl) threadEl.textContent = this.conversations.length;

        try {
            const stats = await graph.stats();
            const entityEl = document.getElementById('stat-entities');
            if (entityEl) entityEl.textContent = stats.entity_count || 0;
        } catch {}
    },

    // ─── Mode Pills ───

    setModePill(mode) {
        document.querySelectorAll('.mode-pill').forEach(p => p.classList.remove('active'));
        document.querySelector(`.mode-pill[data-mode="${mode}"]`).classList.add('active');
        this.activeMode = mode;

        // Map pill to actual behavior
        if (mode === 'research') {
            this.proSearch = true;
            this.agentMode = false;
        } else if (mode === 'detailed') {
            this.proSearch = false;
            this.agentMode = true;
        } else {
            this.proSearch = false;
            this.agentMode = false;
        }

        // Update toggle indicators
        document.getElementById('pro-search-label').style.display = this.proSearch ? '' : 'none';
        document.getElementById('agent-label').style.display = this.agentMode ? '' : 'none';
        document.getElementById('pro-search-btn').style.background = this.proSearch ? 'var(--gold-dim)' : '';
        document.getElementById('agent-btn').style.background = this.agentMode ? 'var(--gold-dim)' : '';
    },

    // ─── Pipeline ───

    updatePipeline(stage) {
        const pipeline = document.getElementById('pipeline');
        if (!pipeline) return;
        pipeline.style.display = 'flex';

        const stages = ['plan', 'search', 'extract', 'analyze', 'synth'];
        const idx = stages.indexOf(stage);

        pipeline.querySelectorAll('.pipeline-node').forEach((node, i) => {
            node.classList.remove('active', 'done');
            if (i < idx) node.classList.add('done');
            else if (i === idx) node.classList.add('active');
        });
        pipeline.querySelectorAll('.pipeline-connector').forEach((conn, i) => {
            conn.classList.toggle('done', i < idx);
        });
    },

    hidePipeline() {
        const pipeline = document.getElementById('pipeline');
        if (pipeline) pipeline.style.display = 'none';
    },

    // ─── Feed Panel ───

    initFeedPanel() {
        // Default tab is trace
    },

    switchFeedTab(tab) {
        document.querySelectorAll('.feed-tab').forEach(t => t.classList.remove('active'));
        document.querySelector(`.feed-tab[data-feed="${tab}"]`).classList.add('active');

        document.querySelectorAll('.feed-section').forEach(s => s.style.display = 'none');
        const section = document.getElementById('feed-' + tab);
        if (section) section.style.display = '';
    },

    appendTraceLog(event) {
        const feed = document.getElementById('feed-trace');
        if (!feed) return;

        // Remove placeholder
        const placeholder = feed.querySelector('div[style]');
        if (placeholder && placeholder.textContent.includes('will appear')) placeholder.remove();

        const entry = document.createElement('div');
        let cls = 'trace-entry';
        let html = '';

        if (event.type === 'plan') {
            cls += ' plan';
            html = `<strong>Plan:</strong> ${this.escapeHtml((event.message || '').slice(0, 200))}`;
        } else if (event.type === 'tool_call') {
            cls += ' tool-call';
            const icons = { search: '&#128269;', browse: '&#127760;', extract: '&#128203;', click: '&#128070;', type_text: '&#9000;', screenshot: '&#128248;', youtube_play: '&#9654;' };
            const icon = icons[event.tool] || '&#128295;';
            html = `${icon} <strong>${event.tool}</strong>(${JSON.stringify(event.args || {}).slice(0, 80)})`;
        } else if (event.type === 'tool_result') {
            cls += ' tool-result';
            const preview = (event.summary || '').slice(0, 120).replace(/\n/g, ' ');
            html = `<strong>&#10003; ${event.tool}</strong> ${this.escapeHtml(preview)}`;

            // Handle youtube_play video_id
            if (event.tool === 'youtube_play' && event.video_id) {
                this.playYouTube(event.video_id);
            }
        } else if (event.type === 'reflection') {
            cls += ' reflection';
            html = `<strong>Reflect:</strong> ${this.escapeHtml((event.message || '').slice(0, 150))}`;
        } else if (event.type === 'progress') {
            cls += ' plan';
            html = this.escapeHtml(event.message || '');
        } else if (event.type === 'error') {
            cls += ' error';
            html = `<strong>Error:</strong> ${this.escapeHtml(event.error || '')}`;
        }

        if (!html) return;
        entry.className = cls;
        entry.innerHTML = html;
        feed.appendChild(entry);

        const feedContent = document.getElementById('feed-content');
        if (feedContent) feedContent.scrollTop = feedContent.scrollHeight;
    },

    updateFeedEntities(entities) {
        const feed = document.getElementById('feed-entities');
        if (!feed || !entities || !entities.length) return;

        const placeholder = feed.querySelector('div[style]');
        if (placeholder) placeholder.remove();

        feed.innerHTML = entities.map(e => `
            <div class="entity-card">
                <div class="entity-dot"></div>
                <span class="entity-name">${this.escapeHtml(e.name)}</span>
                <span class="entity-type">${this.escapeHtml(e.type || '')}</span>
            </div>
        `).join('');
    },

    updateFeedDomains(domains) {
        const feed = document.getElementById('feed-domains');
        if (!feed || !domains || !domains.length) return;

        const placeholder = feed.querySelector('div[style]');
        if (placeholder) placeholder.remove();

        feed.innerHTML = domains.map(d => `
            <div class="domain-card">
                ${this.escapeHtml(d.domain || d)}
                <span class="domain-count">${d.count || ''}</span>
            </div>
        `).join('');
    },

    clearTrace() {
        const feed = document.getElementById('feed-trace');
        if (feed) feed.innerHTML = '<div style="padding:24px;text-align:center;color:var(--t4);font-size:12px">Agent trace will appear here</div>';
    },

    // ─── Space Chips ───

    async loadSpaceChips() {
        try {
            const resp = await fetch('/api/spaces');
            const spaces = await resp.json();
            const container = document.getElementById('space-chips');
            if (!container || !spaces.length) return;
            container.innerHTML = spaces.map(s =>
                `<button class="space-chip" onclick="app.filterBySpace(${s.id})" style="border-color:${s.color || 'var(--border)'}">${this.escapeHtml(s.name)}</button>`
            ).join('');
        } catch {}
    },

    filterBySpace(spaceId) {
        document.querySelectorAll('.space-chip').forEach(c => c.classList.remove('active'));
        this.loadConversations();
    },

    // ─── Viewport ───

    toggleViewport() {
        if (this.viewportOpen) {
            this.stopViewport();
        } else {
            this.startMjpegStream();
        }
    },

    startMjpegStream() {
        const container = document.getElementById('viewport-container');
        const img = document.getElementById('viewport-mjpeg');
        const iframe = document.getElementById('viewport-youtube');
        const label = document.getElementById('viewport-label');

        iframe.style.display = 'none';
        img.style.display = 'block';
        img.src = '/api/cdp/viewport/stream?fps=6&quality=50';
        container.style.display = '';
        label.textContent = 'Chrome Live';
        this.viewportOpen = true;
        this.viewportMode = 'mjpeg';
    },

    stopViewport() {
        const container = document.getElementById('viewport-container');
        const img = document.getElementById('viewport-mjpeg');
        const iframe = document.getElementById('viewport-youtube');

        img.src = '';
        iframe.src = '';
        img.style.display = 'none';
        iframe.style.display = 'none';
        container.style.display = 'none';
        this.viewportOpen = false;
        this.viewportMode = null;
    },

    playYouTube(videoId) {
        const container = document.getElementById('viewport-container');
        const img = document.getElementById('viewport-mjpeg');
        const iframe = document.getElementById('viewport-youtube');
        const label = document.getElementById('viewport-label');

        img.src = '';
        img.style.display = 'none';
        iframe.style.display = 'block';
        iframe.src = `https://www.youtube.com/embed/${videoId}?autoplay=1&rel=0`;
        container.style.display = '';
        label.textContent = 'YouTube';
        this.viewportOpen = true;
        this.viewportMode = 'youtube';
    },

    // ─── Slash Commands ───

    setupSlashCommands() {
        this.slashCommands = [
            { cmd: '/summarize', desc: 'Summarize the current conversation', icon: '&#128221;' },
            { cmd: '/export', desc: 'Export conversation as markdown', icon: '&#128228;' },
            { cmd: '/tag', desc: 'Add a tag to this conversation', icon: '&#127991;', hasArg: true },
            { cmd: '/search', desc: 'Search your past conversations', icon: '&#128269;', hasArg: true },
            { cmd: '/agent', desc: 'Run agent mode on a query', icon: '&#129302;', hasArg: true },
            { cmd: '/mode concise', desc: 'Switch to concise mode', icon: '&#9889;' },
            { cmd: '/mode detailed', desc: 'Switch to detailed mode', icon: '&#128214;' },
            { cmd: '/mode creative', desc: 'Switch to creative mode', icon: '&#127912;' },
            { cmd: '/mode academic', desc: 'Switch to academic mode', icon: '&#127891;' },
            { cmd: '/mode code', desc: 'Switch to code mode', icon: '&#128187;' },
            { cmd: '/research', desc: 'Run deep research on a query', icon: '&#128300;', hasArg: true },
            { cmd: '/signin', desc: 'Open sign-in panel', icon: '&#128274;' },
        ];
        this.selectedAutocompleteIdx = -1;
    },

    updateSlashAutocomplete(value) {
        if (!value.startsWith('/')) {
            this.hideSlashAutocomplete();
            return;
        }
        const partial = value.toLowerCase().split(' ')[0];
        const matches = this.slashCommands.filter(c => c.cmd.startsWith(partial));
        if (!matches.length || (matches.length === 1 && matches[0].cmd === partial)) {
            this.hideSlashAutocomplete();
            return;
        }
        this.showSlashAutocomplete(matches);
    },

    showSlashAutocomplete(matches) {
        let ac = document.getElementById('slash-autocomplete');
        if (!ac) {
            ac = document.createElement('div');
            ac.id = 'slash-autocomplete';
            ac.className = 'slash-autocomplete';
            document.querySelector('.input-wrapper').appendChild(ac);
        }
        this.selectedAutocompleteIdx = -1;
        ac.innerHTML = matches.map((m, i) =>
            `<div class="slash-item" data-index="${i}" data-cmd="${m.cmd}" onclick="app.applySlashCommand('${m.cmd}', ${m.hasArg || false})">
                <span class="slash-icon">${m.icon}</span>
                <span class="slash-cmd">${m.cmd}</span>
                <span class="slash-desc">${m.desc}</span>
            </div>`
        ).join('');
        ac.style.display = 'block';
    },

    hideSlashAutocomplete() {
        const ac = document.getElementById('slash-autocomplete');
        if (ac) ac.style.display = 'none';
        this.selectedAutocompleteIdx = -1;
    },

    navigateAutocomplete(dir) {
        const ac = document.getElementById('slash-autocomplete');
        if (!ac) return;
        const items = ac.querySelectorAll('.slash-item');
        if (!items.length) return;
        items.forEach(i => i.classList.remove('selected'));
        this.selectedAutocompleteIdx += dir;
        if (this.selectedAutocompleteIdx < 0) this.selectedAutocompleteIdx = items.length - 1;
        if (this.selectedAutocompleteIdx >= items.length) this.selectedAutocompleteIdx = 0;
        items[this.selectedAutocompleteIdx].classList.add('selected');
    },

    selectAutocompleteItem() {
        const ac = document.getElementById('slash-autocomplete');
        if (!ac) return;
        const items = ac.querySelectorAll('.slash-item');
        if (this.selectedAutocompleteIdx >= 0 && this.selectedAutocompleteIdx < items.length) {
            items[this.selectedAutocompleteIdx].click();
        }
    },

    applySlashCommand(cmd, hasArg) {
        const ta = document.getElementById('chat-input');
        if (hasArg) {
            ta.value = cmd + ' ';
            ta.focus();
        } else {
            ta.value = cmd;
            ta.focus();
            this.hideSlashAutocomplete();
            this.send();
            return;
        }
        this.hideSlashAutocomplete();
    },

    handleSlashCommand(text) {
        const parts = text.trim().split(/\s+/);
        const cmd = parts[0].toLowerCase();
        const arg = parts.slice(1).join(' ');

        switch (cmd) {
            case '/summarize': return this.slashSummarize();
            case '/export': return this.slashExport();
            case '/tag': return this.slashTag(arg);
            case '/search': return this.slashSearch(arg);
            case '/agent': return this.slashAgent(arg);
            case '/research': return this.slashResearch(arg);
            case '/signin': return this.slashSignin();
            case '/mode': return this.slashMode(arg);
            default: return text;
        }
    },

    slashSummarize() {
        if (!this.currentConvId) return;
        const input = document.getElementById('chat-input');
        input.value = 'Summarize this conversation so far, highlighting key topics and conclusions.';
        this.send();
    },

    slashExport() {
        if (!this.currentConvId) return;
        exportConv.exportConversation(this.currentConvId);
    },

    async slashTag(tagName) {
        if (!this.currentConvId || !tagName) return;
        await graph.addTag(this.currentConvId, tagName);
        this.loadConversations();
        const surface = document.getElementById('surface');
        surface.appendChild(this.createAssistantBubble(`Added tag: **${tagName}**`));
        this.scrollToBottom();
    },

    slashSearch(query) {
        if (query) {
            this.showLocalSearch();
            setTimeout(() => {
                const input = document.getElementById('local-search-input');
                if (input) { input.value = query; input.dispatchEvent(new Event('input')); }
            }, 100);
        } else {
            this.showLocalSearch();
        }
    },

    slashAgent(query) {
        if (!query) return;
        this.agentMode = true;
        this.proSearch = false;
        document.getElementById('agent-btn').style.background = 'var(--gold-dim)';
        document.getElementById('agent-label').style.display = '';
        document.getElementById('pro-search-label').style.display = 'none';
        const input = document.getElementById('chat-input');
        input.value = query;
        this.send();
    },

    slashResearch(query) {
        if (!query) return;
        this.proSearch = true;
        this.agentMode = false;
        document.getElementById('pro-search-btn').style.background = 'var(--gold-dim)';
        document.getElementById('pro-search-label').style.display = '';
        document.getElementById('agent-label').style.display = 'none';
        const input = document.getElementById('chat-input');
        input.value = query;
        this.send();
    },

    slashMode(mode) {
        this.setModePill(mode);
        const surface = document.getElementById('surface');
        surface.appendChild(this.createAssistantBubble(`Mode switched to **${mode}**.`));
        this.scrollToBottom();
    },

    slashSignin() {
        this.switchTab('signin');
    },

    // ─── Conversations ───

    async loadConversations() {
        const resp = await fetch('/api/conversations');
        this.conversations = await resp.json();
        this.renderThreadList();
        this.renderAmbientBar();
    },

    renderThreadList() {
        const list = document.getElementById('nav-list');
        if (!list) return;

        let convs = this.conversations;
        if (this.sidebarFilter) {
            const f = this.sidebarFilter.toLowerCase();
            convs = convs.filter(c =>
                c.title.toLowerCase().includes(f) ||
                (c.preview || '').toLowerCase().includes(f) ||
                (c.tags || []).some(t => t.name.toLowerCase().includes(f))
            );
        }
        if (this.activeTagFilter) {
            convs = convs.filter(c =>
                (c.tags || []).some(t => t.name === this.activeTagFilter)
            );
        }

        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const yesterday = new Date(today - 86400000);
        const weekAgo = new Date(today - 7 * 86400000);

        const groups = { today: [], yesterday: [], week: [], older: [] };
        for (const c of convs) {
            const d = new Date(c.updated_at);
            if (d >= today) groups.today.push(c);
            else if (d >= yesterday) groups.yesterday.push(c);
            else if (d >= weekAgo) groups.week.push(c);
            else groups.older.push(c);
        }

        let html = '';
        const renderGroup = (label, items) => {
            if (!items.length) return '';
            let h = `<div class="nav-group"><div class="nav-group-label">${label}</div>`;
            for (const c of items) {
                const tagsHtml = (c.tags || []).map(t =>
                    `<span class="nav-tag" onclick="event.stopPropagation();app.filterByTag('${app.escapeHtml(t.name)}')">${app.escapeHtml(t.name)}</span>`
                ).join('');
                h += `<div class="nav-item ${c.id === this.currentConvId ? 'active' : ''}"
                     onclick="app.loadConversation(${c.id})">
                    <div class="item-content">
                        <span class="item-title">${this.escapeHtml(c.title)}</span>
                        ${c.preview ? `<span class="item-preview">${this.escapeHtml(c.preview)}</span>` : ''}
                        ${tagsHtml ? `<div class="item-tags">${tagsHtml}</div>` : ''}
                    </div>
                    <button class="item-delete" onclick="event.stopPropagation();app.deleteConversation(${c.id})">&#10005;</button>
                </div>`;
            }
            h += '</div>';
            return h;
        };

        html += renderGroup('Today', groups.today);
        html += renderGroup('Yesterday', groups.yesterday);
        html += renderGroup('This Week', groups.week);
        html += renderGroup('Older', groups.older);

        if (!html) {
            html = '<div style="padding:20px;text-align:center;color:var(--t4);font-size:12px">No threads yet</div>';
        }

        list.innerHTML = html;
    },

    // Alias for legacy compat
    renderSidebar() { this.renderThreadList(); },

    filterSidebar(value) {
        this.sidebarFilter = value;
        this.renderThreadList();
    },

    filterByTag(tagName) {
        if (this.activeTagFilter === tagName) {
            this.activeTagFilter = null;
        } else {
            this.activeTagFilter = tagName;
        }
        this.renderThreadList();
    },

    async loadConversation(id) {
        this.currentConvId = id;
        this.renderThreadList();

        const resp = await fetch(`/api/conversations/${id}`);
        const data = await resp.json();

        // Render messages
        const surface = document.getElementById('surface');
        surface.innerHTML = '';

        for (const msg of data.messages) {
            if (msg.role === 'user') {
                surface.appendChild(this.createUserBubble(msg.content, msg.id));
            } else {
                if (msg.sources && msg.sources.length) {
                    surface.appendChild(this.createSourcesBar(msg.sources));
                }
                surface.appendChild(this.createAssistantBubble(msg.content, msg.id));
                if (msg.follow_ups && msg.follow_ups.length) {
                    surface.appendChild(this.createFollowUps(msg.follow_ups));
                }
            }
        }

        this.renderConversationHeader(data);
        this.hideWelcome();
        this.scrollToBottom();
    },

    renderConversationHeader(data) {
        const existing = document.getElementById('conv-header');
        if (existing) existing.remove();

        const tags = data.tags || [];
        const tagsHtml = tags.map(t =>
            `<span class="conv-tag">${this.escapeHtml(t.name)} <span class="conv-tag-remove" onclick="app.removeTagFromConv('${this.escapeHtml(t.name)}')">&#10005;</span></span>`
        ).join('');

        const header = document.createElement('div');
        header.id = 'conv-header';
        header.className = 'conv-header';
        header.innerHTML = `
            <div class="conv-tags">${tagsHtml}</div>
            <button class="icon-btn tag-add-btn" onclick="app.showTagInput()" title="Add tag">&#127991; +</button>
        `;
        const surface = document.getElementById('surface');
        surface.insertBefore(header, surface.firstChild);
    },

    async showTagInput() {
        if (!this.currentConvId) return;

        const existing = document.getElementById('tag-input-popup');
        if (existing) { existing.remove(); return; }

        const allTags = await graph.listTags();
        const tagNames = allTags.map(t => t.name);

        const popup = document.createElement('div');
        popup.id = 'tag-input-popup';
        popup.className = 'tag-input-popup';
        popup.innerHTML = `
            <input id="tag-input-field" placeholder="Type tag name..." list="tag-suggestions" />
            <datalist id="tag-suggestions">
                ${tagNames.map(n => `<option value="${this.escapeHtml(n)}">`).join('')}
            </datalist>
            <button onclick="app.addTagFromInput()">Add</button>
        `;
        document.querySelector('.conv-header').appendChild(popup);

        const input = document.getElementById('tag-input-field');
        input.focus();
        input.onkeydown = (e) => {
            if (e.key === 'Enter') this.addTagFromInput();
            if (e.key === 'Escape') popup.remove();
        };
    },

    async addTagFromInput() {
        const input = document.getElementById('tag-input-field');
        if (!input || !input.value.trim()) return;
        const tagName = input.value.trim();
        await graph.addTag(this.currentConvId, tagName);
        document.getElementById('tag-input-popup')?.remove();
        this.loadConversation(this.currentConvId);
        this.loadConversations();
    },

    async removeTagFromConv(tagName) {
        if (!this.currentConvId) return;
        await graph.removeTag(this.currentConvId, tagName);
        this.loadConversation(this.currentConvId);
        this.loadConversations();
    },

    async newChat() {
        this.currentConvId = null;
        const surface = document.getElementById('surface');
        surface.innerHTML = '';
        this.showWelcome();
        this.renderThreadList();
        this.clearTrace();
        this.hidePipeline();
        document.getElementById('chat-input').focus();
    },

    // ─── Send ───

    async send() {
        if (this.streaming) return;

        const input = document.getElementById('chat-input');
        const query = input.value.trim();
        if (!query) return;

        // Check for slash commands
        if (query.startsWith('/')) {
            const parts = query.trim().split(/\s+/);
            const cmd = parts[0].toLowerCase();
            const knownCmd = this.slashCommands.some(c => {
                if (c.hasArg) return cmd === c.cmd;
                return c.cmd === query.trim().toLowerCase();
            });
            if (knownCmd) {
                input.value = '';
                input.style.height = 'auto';
                this.hideSlashAutocomplete();
                this.handleSlashCommand(query);
                return;
            }
            if (cmd === '/mode') {
                input.value = '';
                input.style.height = 'auto';
                this.hideSlashAutocomplete();
                this.handleSlashCommand(query);
                return;
            }
        }

        input.value = '';
        input.style.height = 'auto';
        this.hideWelcome();
        this.hideSlashAutocomplete();

        const surface = document.getElementById('surface');
        surface.appendChild(this.createUserBubble(query));

        // Determine mode from pills
        const mode = this.activeMode === 'research' ? 'detailed' : this.activeMode;

        if (this.agentMode || this.activeMode === 'detailed') {
            await this.runAgent(query, mode);
        } else if (this.proSearch || this.activeMode === 'research') {
            await this.runResearch(query, mode);
        } else {
            await this.streamChat(query, mode, true);
        }
    },

    async streamChat(query, mode, searchEnabled) {
        this.streaming = true;
        this.setSendDisabled(true);

        const surface = document.getElementById('surface');

        const sourcesBar = document.createElement('div');
        sourcesBar.className = 'sources-bar';
        sourcesBar.id = 'current-sources';
        surface.appendChild(sourcesBar);

        const bubble = document.createElement('div');
        bubble.className = 'message message-assistant';
        bubble.innerHTML = '<div class="message-bubble streaming-cursor" id="current-response"><div class="thinking-indicator" id="thinking-indicator"><img src="/static/icon.svg" class="thinking-icon" alt=""> Thinking...</div></div>';
        surface.appendChild(bubble);

        this.scrollToBottom();

        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                query,
                conversation_id: this.currentConvId,
                mode,
                search_enabled: searchEnabled,
            }),
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let fullText = '';
        let sources = [];
        let followUps = [];

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const text = decoder.decode(value);
            const lines = text.split('\n');

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const data = JSON.parse(line.slice(6));

                    if (data.type === 'token') {
                        fullText += data.content;
                        document.getElementById('current-response').innerHTML = this.renderMarkdown(fullText);
                        this.scrollToBottom();
                    } else if (data.type === 'done') {
                        this.currentConvId = data.conversation_id;
                        sources = data.sources || [];
                        followUps = data.follow_ups || [];
                        if (data.title) this.loadConversations();
                    } else if (data.type === 'error') {
                        fullText += `\n\n**Error:** ${data.error}`;
                        document.getElementById('current-response').innerHTML = this.renderMarkdown(fullText);
                    }
                } catch (e) {}
            }
        }

        const respEl = document.getElementById('current-response');
        if (respEl) { respEl.classList.remove('streaming-cursor'); respEl.id = ''; }

        if (sources.length) {
            sourcesBar.innerHTML = sources.map(s => this.createSourceCard(s)).join('');
            sourcesBar.id = '';
            // Update source count in ambient bar
            const srcEl = document.getElementById('stat-sources');
            if (srcEl) srcEl.textContent = (parseInt(srcEl.textContent) || 0) + sources.length;
        } else { sourcesBar.remove(); }

        if (followUps.length) {
            surface.appendChild(this.createFollowUps(followUps));
        }

        this.streaming = false;
        this.setSendDisabled(false);
        this.scrollToBottom();
    },

    // ─── Message Actions ───

    createUserBubble(text, msgId) {
        const div = document.createElement('div');
        div.className = 'message message-user';
        div.dataset.msgId = msgId || '';
        div.innerHTML = `
            <div class="message-bubble">
                <div class="message-text">${this.escapeHtml(text)}</div>
                <div class="message-actions">
                    <button class="msg-action" onclick="app.copyMessage(this)" title="Copy">&#128203;</button>
                    <button class="msg-action" onclick="app.editMessage(this)" title="Edit">&#9998;</button>
                </div>
            </div>`;
        return div;
    },

    createAssistantBubble(html, msgId) {
        const div = document.createElement('div');
        div.className = 'message message-assistant';
        div.dataset.msgId = msgId || '';
        div.innerHTML = `
            <div class="message-bubble">
                <div class="message-text">${this.renderMarkdown(html)}</div>
                <div class="message-actions">
                    <button class="msg-action" onclick="app.copyMessage(this)" title="Copy">&#128203;</button>
                    <button class="msg-action" onclick="app.regenerateMessage(this)" title="Regenerate">&#128260;</button>
                </div>
            </div>`;
        return div;
    },

    copyMessage(btn) {
        const bubble = btn.closest('.message-bubble');
        const text = bubble.querySelector('.message-text').innerText;
        navigator.clipboard.writeText(text);
        btn.textContent = '\u2713';
        setTimeout(() => btn.innerHTML = '&#128203;', 1500);
    },

    editMessage(btn) {
        const msgDiv = btn.closest('.message');
        const textEl = msgDiv.querySelector('.message-text');
        textEl.contentEditable = true;
        textEl.focus();
        textEl.classList.add('editing');

        const actions = msgDiv.querySelector('.message-actions');
        actions.innerHTML = `
            <button class="msg-action save" onclick="app.saveEdit(this, true)" title="Send">&#10003;</button>
            <button class="msg-action cancel" onclick="app.saveEdit(this, false)" title="Cancel">&#10005;</button>
        `;
        actions.style.opacity = '1';
    },

    saveEdit(btn, submit) {
        const msgDiv = btn.closest('.message');
        const textEl = msgDiv.querySelector('.message-text');
        textEl.contentEditable = false;
        textEl.classList.remove('editing');

        const actions = msgDiv.querySelector('.message-actions');
        const isUser = msgDiv.classList.contains('message-user');
        actions.innerHTML = isUser
            ? '<button class="msg-action" onclick="app.copyMessage(this)" title="Copy">&#128203;</button><button class="msg-action" onclick="app.editMessage(this)" title="Edit">&#9998;</button>'
            : '<button class="msg-action" onclick="app.copyMessage(this)" title="Copy">&#128203;</button><button class="msg-action" onclick="app.regenerateMessage(this)" title="Regenerate">&#128260;</button>';

        if (submit) {
            const newQuery = textEl.innerText.trim();
            if (newQuery) {
                const surface = document.getElementById('surface');
                let next = msgDiv.nextElementSibling;
                while (next) {
                    const toRemove = next;
                    next = next.nextElementSibling;
                    toRemove.remove();
                }
                const input = document.getElementById('chat-input');
                input.value = newQuery;
                this.send();
            }
        }
    },

    regenerateMessage(btn) {
        const msgDiv = btn.closest('.message');
        let prev = msgDiv.previousElementSibling;
        while (prev) {
            if (prev.classList.contains('message-user')) {
                const text = prev.querySelector('.message-text').innerText;
                msgDiv.remove();
                const input = document.getElementById('chat-input');
                input.value = text;
                this.send();
                return;
            }
            prev = prev.previousElementSibling;
        }
    },

    // ─── UI Helpers ───

    createSourcesBar(sources) {
        const div = document.createElement('div');
        div.className = 'sources-bar';
        div.innerHTML = sources.map(s => this.createSourceCard(s)).join('');
        return div;
    },

    createSourceCard(s) {
        const scoreBar = s.score ? `<div class="source-score" title="Quality: ${s.score}"><div class="source-score-fill" style="width:${s.score * 100}%"></div></div>` : '';
        return `<div class="source-card" onclick="window.open('${this.escapeHtml(s.url)}', '_blank')">
            <div><span class="source-index">[${s.index}]</span> <span class="source-title">${this.escapeHtml(s.title)}</span></div>
            <div class="source-domain">${this.escapeHtml(s.domain)}${s.authority ? ` &middot; ${(s.authority * 100).toFixed(0)}%` : ''}</div>
            ${scoreBar}
        </div>`;
    },

    createFollowUps(questions) {
        const div = document.createElement('div');
        div.className = 'follow-ups';
        div.innerHTML = questions.map(q =>
            `<button class="follow-up-btn" onclick="app.askFollowUp('${this.escapeHtml(q)}')">${this.escapeHtml(q)}</button>`
        ).join('');
        return div;
    },

    askFollowUp(question) {
        document.getElementById('chat-input').value = question;
        this.send();
    },

    hideWelcome() {
        const w = document.getElementById('welcome');
        if (w) w.style.display = 'none';
    },

    showWelcome() {
        const surface = document.getElementById('surface');
        surface.innerHTML = `<div class="welcome" id="welcome">
            <svg viewBox="0 0 80 80" fill="none" style="width:64px;height:64px;opacity:0.8">
                <circle cx="40" cy="40" r="36" fill="#e8a020"/>
                <path d="M30 55l10-30h.2l10 30" stroke="#07070a" stroke-width="5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
                <line x1="33" y1="47" x2="52" y2="47" stroke="#07070a" stroke-width="4.5" stroke-linecap="round"/>
                <circle cx="59" cy="59" r="11" stroke="#07070a" stroke-width="4" fill="none"/>
                <line x1="67" y1="67" x2="76" y2="76" stroke="#07070a" stroke-width="4" stroke-linecap="round"/>
            </svg>
            <h2>What do you want to know?</h2>
            <span class="brand-tagline">The Search Engine of the Future</span>
            <p>Search the web, analyze pages, control Chrome, and get AI-powered answers with inline citations.</p>
            <div class="welcome-commands">
                <button class="welcome-cmd" onclick="app.fillInput('/research latest AI breakthroughs')">/research AI</button>
                <button class="welcome-cmd" onclick="app.fillInput('/agent summarize the top news')">/agent news</button>
                <button class="welcome-cmd" onclick="app.fillInput('What is quantum computing?')">Ask anything</button>
            </div>
        </div>`;
    },

    setSendDisabled(disabled) {
        document.getElementById('send-btn').disabled = disabled;
    },

    scrollToBottom() {
        const surface = document.getElementById('surface');
        if (surface) surface.scrollTop = surface.scrollHeight;
    },

    setMode(mode) {
        if (this.currentConvId) {
            fetch(`/api/conversations/${this.currentConvId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode }),
            });
        }
    },

    toggleProSearch() {
        this.proSearch = !this.proSearch;
        if (this.proSearch) this.agentMode = false;
        const btn = document.getElementById('pro-search-btn');
        const label = document.getElementById('pro-search-label');
        const agentLabel = document.getElementById('agent-label');
        const agentBtn = document.getElementById('agent-btn');
        btn.style.background = this.proSearch ? 'var(--gold-dim)' : '';
        label.style.display = this.proSearch ? '' : 'none';
        agentLabel.style.display = 'none';
        agentBtn.style.background = '';
    },

    toggleAgent() {
        this.agentMode = !this.agentMode;
        if (this.agentMode) this.proSearch = false;
        const btn = document.getElementById('agent-btn');
        const label = document.getElementById('agent-label');
        const proLabel = document.getElementById('pro-search-label');
        const proBtn = document.getElementById('pro-search-btn');
        btn.style.background = this.agentMode ? 'var(--gold-dim)' : '';
        label.style.display = this.agentMode ? '' : 'none';
        proLabel.style.display = 'none';
        proBtn.style.background = '';
    },

    async deleteConversation(id) {
        await fetch(`/api/conversations/${id}`, { method: 'DELETE' });
        if (this.currentConvId === id) this.newChat();
        this.loadConversations();
    },

    switchTab(tab) {
        document.querySelectorAll('.nav-tab').forEach(b => b.classList.remove('active'));
        document.querySelector(`.nav-tab[data-tab="${tab}"]`).classList.add('active');

        const list = document.getElementById('nav-list');
        if (tab === 'spaces') {
            spaces.load(list);
        } else if (tab === 'signin') {
            signin.load(list);
        } else {
            this.renderThreadList();
        }
    },

    toggleTheme() {
        const html = document.documentElement;
        const current = html.getAttribute('data-theme');
        const next = current === 'dark' ? 'light' : 'dark';
        html.setAttribute('data-theme', next);
        localStorage.setItem('alphabetty-theme', next);
    },

    loadTheme() {
        const saved = localStorage.getItem('alphabetty-theme') || 'dark';
        document.documentElement.setAttribute('data-theme', saved);
    },

    async uploadFile() {
        document.getElementById('file-upload').click();
    },

    async handleFileUpload(input) {
        if (!input.files.length) return;
        const file = input.files[0];
        const formData = new FormData();
        formData.append('file', file);

        const query = document.getElementById('chat-input').value.trim();
        if (query) formData.append('query', query);

        this.hideWelcome();
        const surface = document.getElementById('surface');
        surface.appendChild(this.createUserBubble(`Uploaded: ${file.name}`));

        const resp = await fetch('/api/files/upload', { method: 'POST', body: formData });
        const data = await resp.json();

        if (data.analysis) {
            surface.appendChild(this.createAssistantBubble(data.analysis));
        }
        this.scrollToBottom();
        input.value = '';
    },

    // ─── Agent mode ───

    async runAgent(query, mode) {
        this.streaming = true;
        this.setSendDisabled(true);

        const surface = document.getElementById('surface');

        // Show pipeline
        this.updatePipeline('plan');

        // Steps go to trace panel
        this.clearTrace();

        // Response bubble
        const bubble = document.createElement('div');
        bubble.className = 'message message-assistant';
        bubble.innerHTML = '<div class="message-bubble streaming-cursor" id="current-response"></div>';
        surface.appendChild(bubble);

        // Source bar
        const sourcesBar = document.createElement('div');
        sourcesBar.className = 'sources-bar';
        sourcesBar.id = 'current-sources';
        surface.appendChild(sourcesBar);

        this.scrollToBottom();

        const resp = await fetch('/api/agent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, conversation_id: this.currentConvId, mode }),
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let fullText = '';
        let sources = [];

        // Map tool calls to pipeline stages
        const toolToStage = {
            search: 'search',
            browse: 'extract',
            extract: 'extract',
            click: 'extract',
            type_text: 'extract',
            screenshot: 'extract',
            youtube_play: 'extract',
        };
        let currentStage = 'plan';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const text = decoder.decode(value);
            const lines = text.split('\n');

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const data = JSON.parse(line.slice(6));

                    if (data.type === 'agent_thinking') {
                        const existing = document.getElementById('thinking-indicator');
                        if (!existing) {
                            const surface = document.getElementById('surface');
                            const think = document.createElement('div');
                            think.className = 'thinking-indicator';
                            think.id = 'thinking-indicator';
                            think.innerHTML = '<img src="/static/icon.svg" class="thinking-icon" alt=""> Thinking...';
                            surface.appendChild(think);
                            this.scrollToBottom();
                        }
                    } else if (data.type === 'plan') {
                        this.appendTraceLog(data);
                        this.updatePipeline('plan');
                    } else if (data.type === 'reflection') {
                        this.appendTraceLog(data);
                        this.updatePipeline('analyze');
                    } else if (data.type === 'tool_call') {
                        this.appendTraceLog(data);
                        const stage = toolToStage[data.tool] || 'extract';
                        if (stage !== currentStage) {
                            currentStage = stage;
                            this.updatePipeline(stage);
                        }
                    } else if (data.type === 'tool_result') {
                        this.appendTraceLog(data);
                    } else if (data.type === 'token') {
                        const thinkEl = document.getElementById('thinking-indicator');
                        if (thinkEl) thinkEl.remove();
                        fullText += data.content;
                        const responseEl = document.getElementById('current-response');
                        if (responseEl) responseEl.innerHTML = this.renderMarkdown(fullText);
                        this.scrollToBottom();
                        this.updatePipeline('synth');
                    } else if (data.type === 'done') {
                        this.currentConvId = data.conversation_id;
                        sources = data.sources || [];
                        this.loadConversations();
                    } else if (data.type === 'error') {
                        fullText += `\n\n**Error:** ${data.error}`;
                        this.appendTraceLog(data);
                    }
                } catch (e) {}
            }
        }

        const responseEl = document.getElementById('current-response');
        if (responseEl) { responseEl.classList.remove('streaming-cursor'); responseEl.id = ''; }

        if (sources.length) {
            sourcesBar.innerHTML = sources.map(s => this.createSourceCard(s)).join('');
            sourcesBar.id = '';
        } else { sourcesBar.remove(); }

        this.hidePipeline();
        this.streaming = false;
        this.setSendDisabled(false);
        this.scrollToBottom();
    },

    // ─── Markdown ───

    renderMarkdown(text) {
        let html = this.escapeHtml(text);

        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
        html = html.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
        html = html.replace(/\[(\d+)\]/g, '<sup><a class="cite-link" href="#source-$1">[$1]</a></sup>');
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" style="color:var(--gold)">$1</a>');
        html = html.replace(/^### (.+)$/gm, '<h4 style="margin:10px 0 6px;font-size:14px;font-weight:600">$1</h4>');
        html = html.replace(/^## (.+)$/gm, '<h3 style="margin:14px 0 6px;font-size:16px;font-weight:600">$1</h3>');
        html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
        html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
        html = html.replace(/\n\n/g, '</p><p>');
        html = '<p>' + html + '</p>';
        html = html.replace(/<p><\/p>/g, '');
        html = html.replace(/\n/g, '<br>');

        return html;
    },

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },
};

document.addEventListener('DOMContentLoaded', () => app.init());

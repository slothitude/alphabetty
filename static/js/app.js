/* Alphabetty — App Core */
const app = {
    currentConvId: null,
    conversations: [],
    streaming: false,
    proSearch: false,
    agentMode: false,
    domPanelOpen: false,
    sidebarFilter: '',
    activeTagFilter: null,

    async init() {
        this.loadConversations();
        this.setupTextarea();
        this.setupSlashCommands();
        this.loadTheme();
    },

    setupTextarea() {
        const ta = document.getElementById('chat-input');
        ta.addEventListener('input', () => {
            ta.style.height = 'auto';
            ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
            // Slash command autocomplete
            this.updateSlashAutocomplete(ta.value);
        });
    },

    handleInputKey(e) {
        // Slash autocomplete navigation
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

    // ─── Slash Commands ───

    setupSlashCommands() {
        this.slashCommands = [
            { cmd: '/summarize', desc: 'Summarize the current conversation', icon: '📝' },
            { cmd: '/export', desc: 'Export conversation as markdown', icon: '📤' },
            { cmd: '/tag', desc: 'Add a tag to this conversation', icon: '🏷️', hasArg: true },
            { cmd: '/search', desc: 'Search your past conversations', icon: '🔍', hasArg: true },
            { cmd: '/agent', desc: 'Run agent mode on a query', icon: '🤖', hasArg: true },
            { cmd: '/mode concise', desc: 'Switch to concise mode', icon: '⚡' },
            { cmd: '/mode detailed', desc: 'Switch to detailed mode', icon: '📖' },
            { cmd: '/mode creative', desc: 'Switch to creative mode', icon: '🎨' },
            { cmd: '/mode academic', desc: 'Switch to academic mode', icon: '🎓' },
            { cmd: '/mode code', desc: 'Switch to code mode', icon: '💻' },
            { cmd: '/research', desc: 'Run deep research on a query', icon: '🔬', hasArg: true },
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
            // Auto-send non-arg commands
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
            case '/summarize':
                return this.slashSummarize();
            case '/export':
                return this.slashExport();
            case '/tag':
                return this.slashTag(arg);
            case '/search':
                return this.slashSearch(arg);
            case '/agent':
                return this.slashAgent(arg);
            case '/research':
                return this.slashResearch(arg);
            case '/mode':
                return this.slashMode(arg);
            default:
                return text; // Not a command, send as normal
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
        const chatArea = document.getElementById('chat-area');
        chatArea.appendChild(this.createAssistantBubble(`Added tag: **${tagName}**`));
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
        document.getElementById('agent-btn').style.background = 'rgba(34,197,94,0.15)';
        document.getElementById('agent-label').style.display = '';
        const input = document.getElementById('chat-input');
        input.value = query;
        this.send();
    },

    slashResearch(query) {
        if (!query) return;
        this.proSearch = true;
        document.getElementById('pro-search-btn').style.background = 'var(--accent-dim)';
        document.getElementById('pro-search-label').style.display = '';
        const input = document.getElementById('chat-input');
        input.value = query;
        this.send();
    },

    slashMode(mode) {
        const select = document.getElementById('mode-select');
        if (select.querySelector(`option[value="${mode}"]`)) {
            select.value = mode;
            this.setMode(mode);
            const chatArea = document.getElementById('chat-area');
            chatArea.appendChild(this.createAssistantBubble(`Mode switched to **${mode}**.`));
            this.scrollToBottom();
        }
    },

    // ─── Conversations ───

    async loadConversations() {
        const resp = await fetch('/api/conversations');
        this.conversations = await resp.json();
        this.renderSidebar();
    },

    renderSidebar() {
        const list = document.getElementById('sidebar-list');

        // Apply filter
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

        // Group by date
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
            let h = `<div class="sidebar-group"><div class="sidebar-group-label">${label}</div>`;
            for (const c of items) {
                const tagsHtml = (c.tags || []).map(t =>
                    `<span class="sidebar-tag" onclick="event.stopPropagation();app.filterByTag('${app.escapeHtml(t.name)}')">${app.escapeHtml(t.name)}</span>`
                ).join('');
                h += `<div class="sidebar-item ${c.id === this.currentConvId ? 'active' : ''}"
                     onclick="app.loadConversation(${c.id})">
                    <div class="item-content">
                        <span class="item-title">${this.escapeHtml(c.title)}</span>
                        ${c.preview ? `<span class="item-preview">${this.escapeHtml(c.preview)}</span>` : ''}
                        ${tagsHtml ? `<div class="item-tags">${tagsHtml}</div>` : ''}
                    </div>
                    <button class="item-delete" onclick="event.stopPropagation();app.deleteConversation(${c.id})">✕</button>
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
            html = '<div style="padding:24px;text-align:center;color:var(--text-muted);font-size:13px">No conversations</div>';
        }

        list.innerHTML = html;
    },

    filterSidebar(value) {
        this.sidebarFilter = value;
        this.renderSidebar();
    },

    filterByTag(tagName) {
        if (this.activeTagFilter === tagName) {
            this.activeTagFilter = null;
        } else {
            this.activeTagFilter = tagName;
        }
        this.renderSidebar();
    },

    async loadConversation(id) {
        this.currentConvId = id;
        this.renderSidebar();

        const resp = await fetch(`/api/conversations/${id}`);
        const data = await resp.json();

        // Set mode
        document.getElementById('mode-select').value = data.mode || 'concise';

        // Render messages
        const chatArea = document.getElementById('chat-area');
        chatArea.innerHTML = '';

        for (const msg of data.messages) {
            if (msg.role === 'user') {
                chatArea.appendChild(this.createUserBubble(msg.content, msg.id));
            } else {
                if (msg.sources && msg.sources.length) {
                    chatArea.appendChild(this.createSourcesBar(msg.sources));
                }
                chatArea.appendChild(this.createAssistantBubble(msg.content, msg.id));
                if (msg.follow_ups && msg.follow_ups.length) {
                    chatArea.appendChild(this.createFollowUps(msg.follow_ups));
                }
            }
        }

        // Show conversation header with tag button
        this.renderConversationHeader(data);

        this.hideWelcome();
        this.scrollToBottom();
    },

    renderConversationHeader(data) {
        // Remove existing header
        const existing = document.getElementById('conv-header');
        if (existing) existing.remove();

        const tags = data.tags || [];
        const tagsHtml = tags.map(t =>
            `<span class="conv-tag">${this.escapeHtml(t.name)} <span class="conv-tag-remove" onclick="app.removeTagFromConv('${this.escapeHtml(t.name)}')">✕</span></span>`
        ).join('');

        const header = document.createElement('div');
        header.id = 'conv-header';
        header.className = 'conv-header';
        header.innerHTML = `
            <div class="conv-tags">${tagsHtml}</div>
            <button class="icon-btn tag-add-btn" onclick="app.showTagInput()" title="Add tag">🏷️ +</button>
        `;
        const chatArea = document.getElementById('chat-area');
        chatArea.insertBefore(header, chatArea.firstChild);
    },

    async showTagInput() {
        if (!this.currentConvId) return;

        // Remove existing input
        const existing = document.getElementById('tag-input-popup');
        if (existing) { existing.remove(); return; }

        // Get existing tags for autocomplete
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
        // Reload to refresh tags
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
        document.getElementById('chat-area').innerHTML = '';
        this.showWelcome();
        this.renderSidebar();
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
            // Check partial matches (mode commands etc)
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

        const chatArea = document.getElementById('chat-area');
        chatArea.appendChild(this.createUserBubble(query));

        const mode = document.getElementById('mode-select').value;
        const searchEnabled = document.getElementById('search-toggle').checked;

        if (this.agentMode) {
            await this.runAgent(query, mode);
        } else if (this.proSearch) {
            await this.runResearch(query, mode);
        } else {
            await this.streamChat(query, mode, searchEnabled);
        }
    },

    async streamChat(query, mode, searchEnabled) {
        this.streaming = true;
        this.setSendDisabled(true);

        const chatArea = document.getElementById('chat-area');

        // Source card placeholder
        const sourcesBar = document.createElement('div');
        sourcesBar.className = 'sources-bar';
        sourcesBar.id = 'current-sources';
        chatArea.appendChild(sourcesBar);

        // Assistant bubble
        const bubble = document.createElement('div');
        bubble.className = 'message message-assistant';
        bubble.innerHTML = '<div class="message-bubble streaming-cursor" id="current-response"></div>';
        chatArea.appendChild(bubble);

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

                        if (data.title) {
                            this.loadConversations();
                        }
                    } else if (data.type === 'error') {
                        fullText += `\n\n**Error:** ${data.error}`;
                        document.getElementById('current-response').innerHTML = this.renderMarkdown(fullText);
                    }
                } catch (e) {}
            }
        }

        // Finalize
        const respEl = document.getElementById('current-response');
        if (respEl) {
            respEl.classList.remove('streaming-cursor');
            respEl.id = '';
        }

        if (sources.length) {
            sourcesBar.innerHTML = sources.map(s => this.createSourceCard(s)).join('');
            sourcesBar.id = '';
        } else {
            sourcesBar.remove();
        }

        if (followUps.length) {
            chatArea.appendChild(this.createFollowUps(followUps));
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
                    <button class="msg-action" onclick="app.copyMessage(this)" title="Copy">📋</button>
                    <button class="msg-action" onclick="app.editMessage(this)" title="Edit & Resend">✏️</button>
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
                    <button class="msg-action" onclick="app.copyMessage(this)" title="Copy">📋</button>
                    <button class="msg-action" onclick="app.regenerateMessage(this)" title="Regenerate">🔄</button>
                </div>
            </div>`;
        return div;
    },

    copyMessage(btn) {
        const bubble = btn.closest('.message-bubble');
        const text = bubble.querySelector('.message-text').innerText;
        navigator.clipboard.writeText(text);
        btn.textContent = '✓';
        setTimeout(() => btn.textContent = '📋', 1500);
    },

    editMessage(btn) {
        const msgDiv = btn.closest('.message');
        const textEl = msgDiv.querySelector('.message-text');
        const original = textEl.innerText;

        // Replace text with editable area
        textEl.contentEditable = true;
        textEl.focus();
        textEl.classList.add('editing');

        // Add save/cancel buttons
        const actions = msgDiv.querySelector('.message-actions');
        actions.innerHTML = `
            <button class="msg-action save" onclick="app.saveEdit(this, true)" title="Send">✓</button>
            <button class="msg-action cancel" onclick="app.saveEdit(this, false)" title="Cancel">✕</button>
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
            ? '<button class="msg-action" onclick="app.copyMessage(this)" title="Copy">📋</button><button class="msg-action" onclick="app.editMessage(this)" title="Edit & Resend">✏️</button>'
            : '<button class="msg-action" onclick="app.copyMessage(this)" title="Copy">📋</button><button class="msg-action" onclick="app.regenerateMessage(this)" title="Regenerate">🔄</button>';

        if (submit) {
            const newQuery = textEl.innerText.trim();
            if (newQuery) {
                // Remove this message and all after it
                const chatArea = document.getElementById('chat-area');
                let next = msgDiv.nextElementSibling;
                while (next) {
                    const toRemove = next;
                    next = next.nextElementSibling;
                    toRemove.remove();
                }
                // Resubmit
                const input = document.getElementById('chat-input');
                input.value = newQuery;
                this.send();
            }
        }
    },

    regenerateMessage(btn) {
        const msgDiv = btn.closest('.message');
        const chatArea = document.getElementById('chat-area');

        // Find previous user message
        let prev = msgDiv.previousElementSibling;
        while (prev) {
            if (prev.classList.contains('message-user')) {
                const text = prev.querySelector('.message-text').innerText;
                // Remove this assistant message
                msgDiv.remove();
                // Resubmit with the user message
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
            <div class="source-domain">${this.escapeHtml(s.domain)}${s.authority ? ` · ${(s.authority * 100).toFixed(0)}% authority` : ''}</div>
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
        const chatArea = document.getElementById('chat-area');
        chatArea.innerHTML = `<div class="welcome" id="welcome">
            <h2>What do you want to know?</h2>
            <p>Search the web, analyze pages, control Chrome, and get AI-powered answers with inline citations.</p>
            <p style="font-size:13px;color:var(--text-muted)">Type <code>/</code> for slash commands</p>
        </div>`;
    },

    setSendDisabled(disabled) {
        document.getElementById('send-btn').disabled = disabled;
    },

    scrollToBottom() {
        const chatArea = document.getElementById('chat-area');
        chatArea.scrollTop = chatArea.scrollHeight;
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
        const btn = document.getElementById('pro-search-btn');
        const label = document.getElementById('pro-search-label');
        btn.style.background = this.proSearch ? 'var(--accent-dim)' : '';
        label.style.display = this.proSearch ? '' : 'none';
        if (this.proSearch && this.agentMode) this.toggleAgent();
    },

    toggleAgent() {
        this.agentMode = !this.agentMode;
        const btn = document.getElementById('agent-btn');
        const label = document.getElementById('agent-label');
        btn.style.background = this.agentMode ? 'rgba(34,197,94,0.15)' : '';
        label.style.display = this.agentMode ? '' : 'none';
        if (this.agentMode && this.proSearch) this.toggleProSearch();
    },

    async deleteConversation(id) {
        await fetch(`/api/conversations/${id}`, { method: 'DELETE' });
        if (this.currentConvId === id) {
            this.newChat();
        }
        this.loadConversations();
    },

    switchTab(tab) {
        document.querySelectorAll('.sidebar-nav button').forEach(b => b.classList.remove('active'));
        document.querySelector(`.sidebar-nav button[data-tab="${tab}"]`).classList.add('active');

        const list = document.getElementById('sidebar-list');
        if (tab === 'spaces') {
            spaces.load(list);
        } else {
            this.renderSidebar();
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
        const chatArea = document.getElementById('chat-area');
        chatArea.appendChild(this.createUserBubble(`Uploaded: ${file.name}`));

        const resp = await fetch('/api/files/upload', { method: 'POST', body: formData });
        const data = await resp.json();

        if (data.analysis) {
            chatArea.appendChild(this.createAssistantBubble(data.analysis));
        }
        this.scrollToBottom();
        input.value = '';
    },

    // ─── Agent mode ───
    async runAgent(query, mode) {
        this.streaming = true;
        this.setSendDisabled(true);

        const chatArea = document.getElementById('chat-area');

        // Agent steps log
        const stepsDiv = document.createElement('div');
        stepsDiv.className = 'research-progress';
        stepsDiv.id = 'agent-steps';
        chatArea.appendChild(stepsDiv);

        // Response bubble
        const bubble = document.createElement('div');
        bubble.className = 'message message-assistant';
        bubble.innerHTML = '<div class="message-bubble streaming-cursor" id="current-response"></div>';
        chatArea.appendChild(bubble);

        // Source bar
        const sourcesBar = document.createElement('div');
        sourcesBar.className = 'sources-bar';
        sourcesBar.id = 'current-sources';
        chatArea.appendChild(sourcesBar);

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
                        // Show thinking indicator
                    } else if (data.type === 'plan') {
                        const step = document.createElement('div');
                        step.className = 'progress-step active';
                        step.innerHTML = `<div class="step-icon">📋</div><span><strong>Plan:</strong> ${this.escapeHtml(data.message || '')}</span>`;
                        stepsDiv.appendChild(step);
                        this.scrollToBottom();
                    } else if (data.type === 'reflection') {
                        const step = document.createElement('div');
                        step.className = 'progress-step done';
                        step.innerHTML = `<div class="step-icon">🪞</div><span style="color:var(--text-muted)"><strong>Reflection:</strong> ${this.escapeHtml(data.message || '')}</span>`;
                        stepsDiv.appendChild(step);
                        this.scrollToBottom();
                    } else if (data.type === 'tool_call') {
                        const toolIcons = { search: '🔍', browse: '🌐', extract: '📋', click: '👆', type_text: '⌨️', screenshot: '📸' };
                        const icon = toolIcons[data.tool] || '🔧';
                        const argsStr = JSON.stringify(data.args).slice(0, 100);
                        const step = document.createElement('div');
                        step.className = 'progress-step active';
                        step.innerHTML = `<div class="step-icon">${icon}</div><span><strong>${data.tool}</strong>(${argsStr})</span>`;
                        stepsDiv.appendChild(step);
                        this.scrollToBottom();
                    } else if (data.type === 'tool_result') {
                        const steps = stepsDiv.querySelectorAll('.progress-step');
                        const last = steps[steps.length - 1];
                        if (last) {
                            last.classList.remove('active');
                            last.classList.add('done');
                            last.querySelector('.step-icon').textContent = '✓';
                        }
                        const resultStep = document.createElement('div');
                        resultStep.className = 'progress-step done';
                        const preview = data.summary.slice(0, 150).replace(/\n/g, ' ');
                        resultStep.innerHTML = `<div class="step-icon" style="background:var(--bg-tertiary);color:var(--text-muted)">→</div><span style="color:var(--text-muted);font-size:12px">${this.escapeHtml(preview)}...</span>`;
                        stepsDiv.appendChild(resultStep);
                        this.scrollToBottom();
                    } else if (data.type === 'token') {
                        fullText += data.content;
                        const responseEl = document.getElementById('current-response');
                        if (responseEl) responseEl.innerHTML = this.renderMarkdown(fullText);
                        this.scrollToBottom();
                    } else if (data.type === 'done') {
                        this.currentConvId = data.conversation_id;
                        sources = data.sources || [];
                        this.loadConversations();
                    } else if (data.type === 'error') {
                        fullText += `\n\n**Error:** ${data.error}`;
                    }
                } catch (e) {}
            }
        }

        // Finalize
        const responseEl = document.getElementById('current-response');
        if (responseEl) { responseEl.classList.remove('streaming-cursor'); responseEl.id = ''; }

        if (sources.length) {
            sourcesBar.innerHTML = sources.map(s => this.createSourceCard(s)).join('');
            sourcesBar.id = '';
        } else { sourcesBar.remove(); }

        stepsDiv.id = '';
        this.streaming = false;
        this.setSendDisabled(false);
        this.scrollToBottom();
    },

    // Simple markdown-ish rendering
    renderMarkdown(text) {
        let html = this.escapeHtml(text);

        // Code blocks
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
        html = html.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');

        // Inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Bold
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

        // Italic
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

        // Citation links
        html = html.replace(/\[(\d+)\]/g, '<sup><a class="cite-link" href="#source-$1">[$1]</a></sup>');

        // Links
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" style="color:var(--accent)">$1</a>');

        // Headers
        html = html.replace(/^### (.+)$/gm, '<h4 style="margin:12px 0 8px;font-size:15px;font-weight:600">$1</h4>');
        html = html.replace(/^## (.+)$/gm, '<h3 style="margin:16px 0 8px;font-size:17px;font-weight:600">$1</h3>');

        // Lists
        html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
        html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

        // Paragraphs (double newline)
        html = html.replace(/\n\n/g, '</p><p>');
        html = '<p>' + html + '</p>';
        html = html.replace(/<p><\/p>/g, '');

        // Single newlines within paragraphs
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

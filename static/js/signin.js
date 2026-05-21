/* Alphabetty — Sign-In UI */

const signin = {
    credentials: [],

    async load(container) {
        this.container = container;
        this.render();
    },

    async render() {
        const list = this.container;
        try {
            const resp = await fetch('/api/signin/credentials');
            this.credentials = await resp.json();
        } catch (e) {
            this.credentials = [];
        }

        // Get current signin status
        let status = { state: 'idle' };
        try {
            const resp = await fetch('/api/signin/status');
            status = await resp.json();
        } catch {}

        let html = '';

        // Status indicator
        const stateColors = {
            idle: 'var(--t5)',
            signing_in: 'var(--gold)',
            waiting_2fa: '#e06040',
            signed_in: '#40c060',
            failed: '#e04040',
        };
        const stateLabels = {
            idle: 'Idle',
            signing_in: 'Signing in...',
            waiting_2fa: '2FA Required',
            signed_in: 'Signed In',
            failed: 'Failed',
        };

        html += `<div class="signin-status" style="padding:10px 14px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px">
            <div style="width:8px;height:8px;border-radius:50%;background:${stateColors[status.state] || 'var(--t5)'}"></div>
            <span style="font-size:12px;color:var(--t2)">${stateLabels[status.state] || status.state}</span>
            ${status.site ? `<span style="font-size:11px;color:var(--t4);margin-left:auto">${new URL(status.site).hostname}</span>` : ''}
        </div>`;

        // Quick sign-in form
        html += `<div style="padding:10px 14px;border-bottom:1px solid var(--border)">
            <div style="font-size:11px;color:var(--t4);margin-bottom:6px;font-weight:600">Quick Sign In</div>
            <input id="signin-url" placeholder="Login URL (e.g. https://accounts.google.com)" style="width:100%;background:var(--ink3);border:1px solid var(--border);border-radius:var(--radius-xs);padding:5px 8px;color:var(--t1);font-size:11px;margin-bottom:4px">
            <input id="signin-user" placeholder="Email / Username" style="width:100%;background:var(--ink3);border:1px solid var(--border);border-radius:var(--radius-xs);padding:5px 8px;color:var(--t1);font-size:11px;margin-bottom:4px">
            <input id="signin-pass" type="password" placeholder="Password" style="width:100%;background:var(--ink3);border:1px solid var(--border);border-radius:var(--radius-xs);padding:5px 8px;color:var(--t1);font-size:11px;margin-bottom:6px">
            <div style="display:flex;gap:6px">
                <button onclick="signin.startFlow()" style="flex:1;background:var(--gold);color:var(--void);border:none;border-radius:var(--radius-xs);padding:5px;font-size:11px;font-weight:600;cursor:pointer">Sign In</button>
                <button onclick="signin.startFlow(true)" style="background:var(--ink4);color:var(--t2);border:none;border-radius:var(--radius-xs);padding:5px 8px;font-size:11px;cursor:pointer">+ Save</button>
            </div>
        </div>`;

        // 2FA input (shown when needed)
        if (status.state === 'waiting_2fa') {
            html += `<div class="signin-2fa" style="padding:10px 14px;border-bottom:1px solid var(--border);background:rgba(224,96,64,0.08)">
                <div style="font-size:11px;color:#e06040;margin-bottom:6px;font-weight:600">2FA Code Required</div>
                ${status.hint ? `<div style="font-size:11px;color:var(--t4);margin-bottom:4px">Hint: ${app.escapeHtml(status.hint)}</div>` : ''}
                <div style="display:flex;gap:6px">
                    <input id="signin-2fa-code" placeholder="Enter code..." style="flex:1;background:var(--ink3);border:1px solid var(--border);border-radius:var(--radius-xs);padding:5px 8px;color:var(--t1);font-size:12px;letter-spacing:2px;text-align:center" onkeydown="if(event.key==='Enter')signin.submit2fa()">
                    <button onclick="signin.submit2fa()" style="background:var(--gold);color:var(--void);border:none;border-radius:var(--radius-xs);padding:5px 10px;font-size:11px;font-weight:600;cursor:pointer">Submit</button>
                </div>
            </div>`;
        }

        // Saved credentials list
        html += `<div style="padding:10px 14px">
            <div style="font-size:11px;color:var(--t4);margin-bottom:6px;font-weight:600">Saved Profiles (${this.credentials.length})</div>`;

        if (this.credentials.length === 0) {
            html += `<div style="font-size:11px;color:var(--t5);padding:8px 0;text-align:center">No saved credentials</div>`;
        } else {
            for (const c of this.credentials) {
                const host = (() => { try { return new URL(c.site_url).hostname } catch { return c.site_url } })();
                html += `<div class="sidebar-item" style="display:flex;align-items:center;gap:6px;padding:6px 8px;border-radius:var(--radius-xs);cursor:pointer;margin-bottom:2px" onclick="signin.autoLogin('${app.escapeHtml(c.name)}')">
                    <span style="font-size:12px;color:var(--gold)">&#x1F511;</span>
                    <div style="flex:1;min-width:0">
                        <div style="font-size:11px;color:var(--t1);font-weight:500">${app.escapeHtml(c.name)}</div>
                        <div style="font-size:10px;color:var(--t4);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${app.escapeHtml(c.username)} &middot; ${host}</div>
                    </div>
                    ${c.totp_secret ? '<span style="font-size:10px;color:#40c060" title="TOTP configured">&#x1F510;</span>' : ''}
                    <button class="item-delete" onclick="event.stopPropagation();signin.deleteCred(${c.id})" style="font-size:10px;background:none;border:none;color:var(--t5);cursor:pointer;padding:2px">&#10005;</button>
                </div>`;
            }
        }

        html += `</div>`;
        list.innerHTML = html;
    },

    async startFlow(save = false) {
        const url = document.getElementById('signin-url')?.value?.trim();
        const user = document.getElementById('signin-user')?.value?.trim();
        const pass = document.getElementById('signin-pass')?.value?.trim();

        if (!url || !user || !pass) {
            app.showToast('Fill in URL, username, and password', 'error');
            return;
        }

        app.showToast('Starting sign-in...', 'info');

        try {
            const resp = await fetch('/api/signin/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, username: user, password: pass }),
            });
            const result = await resp.json();

            if (result.state === 'signed_in') {
                app.showToast('Signed in successfully!', 'success');
                if (save) await this.saveCredential(url, user, pass);
            } else if (result.state === 'waiting_2fa') {
                app.showToast('2FA code required — enter it below', 'error');
            } else if (result.state === 'failed') {
                app.showToast(`Sign-in failed: ${result.error || 'unknown error'}`, 'error');
            }
        } catch (e) {
            app.showToast(`Sign-in error: ${e.message}`, 'error');
        }

        this.render();
    },

    async submit2fa() {
        const code = document.getElementById('signin-2fa-code')?.value?.trim();
        if (!code) return;

        app.showToast('Submitting 2FA code...', 'info');

        try {
            const resp = await fetch('/api/signin/2fa', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code }),
            });
            const result = await resp.json();

            if (result.state === 'signed_in') {
                app.showToast('2FA accepted — signed in!', 'success');
            } else {
                app.showToast(`2FA failed: ${result.error || 'verification failed'}`, 'error');
            }
        } catch (e) {
            app.showToast(`2FA error: ${e.message}`, 'error');
        }

        this.render();
    },

    async autoLogin(name) {
        app.showToast(`Auto sign-in: ${name}...`, 'info');

        try {
            const resp = await fetch(`/api/signin/auto/${encodeURIComponent(name)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            const result = await resp.json();

            if (result.state === 'signed_in') {
                app.showToast(`Signed in as ${name}!`, 'success');
            } else if (result.state === 'waiting_2fa') {
                app.showToast(`2FA required for ${name}`, 'error');
            } else {
                app.showToast(`Auto sign-in failed: ${result.error || 'unknown'}`, 'error');
            }
        } catch (e) {
            app.showToast(`Auto sign-in error: ${e.message}`, 'error');
        }

        this.render();
    },

    async saveCredential(url, username, password) {
        const name = username.split('@')[0] + '_' + (() => { try { return new URL(url).hostname.split('.')[0] } catch { return 'site' } })();
        try {
            await fetch('/api/signin/credentials', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, url, username, password }),
            });
            app.showToast(`Credential saved: ${name}`, 'success');
            this.render();
        } catch (e) {
            app.showToast(`Save failed: ${e.message}`, 'error');
        }
    },

    async deleteCred(id) {
        try {
            await fetch(`/api/signin/credentials/${id}`, { method: 'DELETE' });
            app.showToast('Credential deleted', 'info');
            this.render();
        } catch (e) {
            app.showToast(`Delete failed: ${e.message}`, 'error');
        }
    },
};

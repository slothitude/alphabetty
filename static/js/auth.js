/* Alphabetty — Auth Module */
const auth = {
    user: null,

    async init() {
        if (document.getElementById('login-page')) {
            this.initLoginPage();
        } else {
            await this.checkSession();
        }
    },

    initLoginPage() {
        const form = document.getElementById('login-form');
        const toggleBtn = document.getElementById('toggle-mode');
        const titleEl = document.getElementById('form-title');
        const emailField = document.getElementById('email-field');
        const submitBtn = document.getElementById('submit-btn');
        let isRegister = false;

        if (toggleBtn) {
            toggleBtn.addEventListener('click', () => {
                isRegister = !isRegister;
                titleEl.textContent = isRegister ? 'Create Account' : 'Sign In';
                submitBtn.textContent = isRegister ? 'Create Account' : 'Sign In';
                toggleBtn.textContent = isRegister ? 'Already have an account? Sign in' : "Don't have an account? Register";
                emailField.style.display = isRegister ? 'block' : 'none';
            });
        }

        if (form) {
            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                const errorEl = document.getElementById('login-error');
                errorEl.textContent = '';
                errorEl.style.display = 'none';

                const username = document.getElementById('username').value.trim();
                const password = document.getElementById('password').value;
                const email = document.getElementById('email')?.value?.trim() || undefined;

                if (!username || !password) {
                    errorEl.textContent = 'Please fill in all fields';
                    errorEl.style.display = 'block';
                    return;
                }

                const endpoint = isRegister ? '/api/auth/register' : '/api/auth/login';
                const body = isRegister ? { username, password, email } : { username, password };

                try {
                    const resp = await fetch(endpoint, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        credentials: 'include',
                        body: JSON.stringify(body),
                    });

                    const data = await resp.json();

                    if (!resp.ok) {
                        errorEl.textContent = data.detail || 'Authentication failed';
                        errorEl.style.display = 'block';
                        return;
                    }

                    // Success — load dashboard directly (avoids cookie timing issues on navigation)
                    const page = await fetch('/', { credentials: 'include' });
                    const html = await page.text();
                    document.open();
                    document.write(html);
                    document.close();
                } catch (err) {
                    errorEl.textContent = 'Connection error';
                    errorEl.style.display = 'block';
                }
            });
        }
    },

    async checkSession() {
        try {
            const resp = await fetch('/api/auth/me', { credentials: 'include' });
            if (resp.ok) {
                this.user = await resp.json();
                this.updateUI();
                return true;
            }
        } catch {}
        // Not authenticated — redirect to login (only if not already on login page)
        if (!document.getElementById('login-page')) {
            location.replace('/');
        }
        return false;
    },

    updateUI() {
        if (!this.user) return;
        const el = document.getElementById('user-indicator');
        if (el) {
            const badge = this.user.role === 'admin' ? ' <span style="font-size:10px;color:var(--gold);margin-left:4px">admin</span>' : '';
            el.innerHTML = `<span style="color:var(--t2);font-size:12px">${this.user.username}${badge}</span>` +
                `<button onclick="auth.logout()" style="margin-left:8px;font-size:11px;cursor:pointer;background:none;border:1px solid var(--border);color:var(--t3);padding:2px 8px;border-radius:3px" title="Sign out">Sign out</button>`;
        }
    },

    async logout() {
        if (typeof app !== 'undefined' && app._eventSource) {
            app._eventSource.close();
            app._eventSource = null;
        }
        try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }); } catch {}
        this.user = null;
        // Clear cookie client-side to guarantee it's gone before redirect
        document.cookie = 'access_token=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax' +
            (location.protocol === 'https:' ? '; Secure' : '');
        location.replace('/');
    }
};

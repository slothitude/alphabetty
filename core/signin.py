"""Sign-in workflow — reusable auth for any website with 2FA support.

State machine: idle → signing_in → waiting_2fa → signed_in
                                        ↓
                                     failed

Uses JS heuristics to detect login forms and 2FA prompts.
Supports manual 2FA (user provides code) and automatic TOTP.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from core.cdp_bridge import cdp

logger = logging.getLogger(__name__)

# ─── JS for form detection ───

FORM_DETECT_JS = """
(function() {
    const result = {username: null, password: null, submit: null};

    // Username field
    const u = document.querySelector('input[type="email"]')
        || document.querySelector('input[name*="user" i]')
        || document.querySelector('input[name*="email" i]')
        || document.querySelector('input[name*="login" i]')
        || document.querySelector('input[autocomplete="username"]')
        || document.querySelector('input[autocomplete="email"]')
        || document.querySelector('input[type="text"]');
    if (u) result.username = __sel(u);

    // Password field
    const p = document.querySelector('input[type="password"]');
    if (p) result.password = __sel(p);

    // Submit button
    const btns = Array.from(document.querySelectorAll('button, input[type="submit"]'));
    const submit = btns.find(b => /sign\\s*in|log\\s*in|next|submit|continue/i.test(b.textContent || b.value || ''))
        || document.querySelector('button[type="submit"]')
        || document.querySelector('input[type="submit"]')
        || btns[0];
    if (submit) result.submit = __sel(submit);

    function __sel(el) {
        if (el.id) return '#' + el.id;
        if (el.name) return '[name="' + el.name + '"]';
        if (el.type) return 'input[type="' + el.type + '"]';
        return el.tagName.toLowerCase();
    }

    return JSON.stringify(result);
})()
"""

TWOFA_DETECT_JS = """
(function() {
    const body = document.body.innerText.toLowerCase();
    const has2fa = /verification|2fa|two.factor|authenticator|one.time|otp|code has been|enter the code|6-digit/i.test(body);
    if (!has2fa) return JSON.stringify({needs_2fa: false});

    const result = {needs_2fa: true, code_input: null, submit: null, hint: ""};

    // Code input
    const ci = document.querySelector('input[name*="code" i]')
        || document.querySelector('input[name*="otp" i]')
        || document.querySelector('input[name*="verification" i]')
        || document.querySelector('input[name*="pin" i]')
        || document.querySelector('input[type="tel"][maxlength <= "8"]')
        || document.querySelector('input[type="number"]')
        || document.querySelector('input[autocomplete="one-time-code"]')
        || document.querySelector('input[autocomplete="otp"]');
    if (ci) result.code_input = __sel(ci);

    // Submit button
    const btns = Array.from(document.querySelectorAll('button, input[type="submit"]'));
    const submit = btns.find(b => /next|verify|submit|confirm|continue|done/i.test(b.textContent || b.value || ''))
        || document.querySelector('button[type="submit"]')
        || document.querySelector('input[type="submit"]');
    if (submit) result.submit = __sel(submit);

    // Hint (partial phone, email, etc.)
    const hintMatch = body.match(/(\\S*\\*+\\S*|sms to .+?|sent to .+?|\\S*\\•+\\S*)/i);
    if (hintMatch) result.hint = hintMatch[0].substring(0, 60);

    function __sel(el) {
        if (el.id) return '#' + el.id;
        if (el.name) return '[name="' + el.name + '"]';
        return el.tagName.toLowerCase();
    }

    return JSON.stringify(result);
})()
"""

VERIFY_JS = """
(function() {
    // Heuristic: if there's no password field and no "sign in" text, likely signed in
    const pwd = document.querySelector('input[type="password"]');
    const body = document.body.innerText.substring(0, 2000).toLowerCase();
    const hasSignIn = /sign\\s*in|log\\s*in|create account|forgot password/i.test(body);
    // Look for common signed-in indicators
    const hasAvatar = !!document.querySelector('img[alt*="avatar" i], img[alt*="profile" i], [class*="avatar"], [class*="profile-img"]');
    const hasSignOut = /sign\\s*out|log\\s*out/i.test(body);

    if (!pwd && !hasSignIn) return JSON.stringify({signed_in: true, confidence: "high"});
    if (hasAvatar || hasSignOut) return JSON.stringify({signed_in: true, confidence: "medium"});
    if (pwd || hasSignIn) return JSON.stringify({signed_in: false, confidence: "high"});
    return JSON.stringify({signed_in: false, confidence: "low"});
})()
"""


class SignInWorkflow:
    """Singleton sign-in workflow state machine."""

    def __init__(self):
        self._state: str = "idle"
        self._current_site: str = ""
        self._tab_id: str | None = None
        self._credential_id: int | None = None

    @property
    def state(self) -> str:
        return self._state

    def status(self) -> dict:
        return {
            "state": self._state,
            "site": self._current_site,
            "tab_id": self._tab_id,
            "credential_id": self._credential_id,
        }

    async def detect_form(self, tab_id: str = None) -> dict:
        """Inject JS to find login form fields."""
        result = await cdp.evaluate(FORM_DETECT_JS, tab_id=tab_id)
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return {"error": "Failed to parse form detection result", "raw": result}
        return result or {}

    async def detect_2fa(self, tab_id: str = None) -> dict:
        """Inject JS to detect 2FA prompt."""
        result = await cdp.evaluate(TWOFA_DETECT_JS, tab_id=tab_id)
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return {"error": "Failed to parse 2FA detection result", "raw": result}
        return result or {}

    async def start(self, url: str, username: str, password: str,
                    tab_id: str = None, selectors: dict = None) -> dict:
        """Navigate to URL, detect login form, fill + submit. Returns state.

        Handles multi-step flows (e.g. Google: email → next → password → next).
        """
        self._state = "signing_in"
        self._current_site = url
        self._tab_id = tab_id
        self._password = password  # store for multi-step

        from core.events import emit
        emit("signin.started", {"url": url, "username": username})

        try:
            # Navigate to login page
            await cdp.navigate(url, tab_id=tab_id)
            await asyncio.sleep(2)

            # Detect or use provided selectors
            if selectors:
                sel = selectors
            else:
                sel = await self.detect_form(tab_id=tab_id)

            # ─── Step 1: Fill username ───
            if not sel.get("username"):
                self._state = "failed"
                emit("signin.failed", {"url": url, "reason": "Could not find username field"})
                return {
                    "state": "failed",
                    "error": "Could not detect username field",
                    "detected": sel,
                }

            await cdp.click(sel["username"], tab_id=tab_id)
            await asyncio.sleep(0.3)
            await cdp.type_text(sel["username"], username, tab_id=tab_id)
            await asyncio.sleep(0.5)

            # Submit username (click Next/Submit)
            if sel.get("submit"):
                await cdp.click(sel["submit"], tab_id=tab_id)
            else:
                await cdp.evaluate(
                    'document.querySelector("form")?.submit()',
                    tab_id=tab_id,
                )
            await asyncio.sleep(3)

            # ─── Step 2: Fill password (may be on a new page) ───
            if not sel.get("password"):
                # Multi-step flow — redetect form on new page
                sel2 = await self.detect_form(tab_id=tab_id)
                if sel2.get("password"):
                    await cdp.click(sel2["password"], tab_id=tab_id)
                    await asyncio.sleep(0.3)
                    await cdp.type_text(sel2["password"], password, tab_id=tab_id)
                    await asyncio.sleep(0.5)

                    # Submit password
                    submit_btn = sel2.get("submit")
                    if submit_btn:
                        await cdp.click(submit_btn, tab_id=tab_id)
                    else:
                        await cdp.evaluate(
                            'document.querySelector("input[type=password]").form?.submit() || '
                            'document.querySelector("form")?.submit()',
                            tab_id=tab_id,
                        )
                    await asyncio.sleep(3)
            else:
                # Single-page flow — password was already detected
                await cdp.click(sel["password"], tab_id=tab_id)
                await asyncio.sleep(0.3)
                await cdp.type_text(sel["password"], password, tab_id=tab_id)
                await asyncio.sleep(0.5)

                if sel.get("submit"):
                    await cdp.click(sel["submit"], tab_id=tab_id)
                else:
                    await cdp.evaluate(
                        'document.querySelector("input[type=password]").form?.submit() || '
                        'document.querySelector("form")?.submit()',
                        tab_id=tab_id,
                    )
                await asyncio.sleep(3)

            # ─── Step 3: Check for 2FA ───
            twofa = await self.detect_2fa(tab_id=tab_id)
            if twofa.get("needs_2fa"):
                self._state = "waiting_2fa"
                emit("signin.2fa_required", {"url": url, "hint": twofa.get("hint", "")})
                return {
                    "state": "waiting_2fa",
                    "hint": twofa.get("hint", ""),
                    "code_input": twofa.get("code_input"),
                    "submit": twofa.get("submit"),
                }

            # ─── Step 4: Verify ───
            verified = await self.verify(tab_id=tab_id)
            if verified.get("signed_in"):
                self._state = "signed_in"
                emit("signin.done", {"url": url, "method": "password"})
                return {"state": "signed_in", "verified": verified}
            else:
                # Might still be loading — return current state
                self._state = "signed_in"
                emit("signin.done", {"url": url, "method": "password", "confidence": "low"})
                return {"state": "signed_in", "verified": verified}

        except Exception as e:
            self._state = "failed"
            emit("signin.failed", {"url": url, "error": str(e)})
            return {"state": "failed", "error": str(e)}

    async def submit_2fa(self, code: str, tab_id: str = None) -> dict:
        """Submit 2FA code. Detects code input, fills, clicks submit."""
        tid = tab_id or self._tab_id
        if self._state != "waiting_2fa":
            return {"error": "Not in waiting_2fa state", "state": self._state}

        try:
            # Detect 2FA form
            twofa = await self.detect_2fa(tab_id=tid)
            code_input = twofa.get("code_input")
            submit_btn = twofa.get("submit")

            if not code_input:
                return {"error": "Could not find 2FA code input", "detected": twofa}

            # Fill code
            await cdp.click(code_input, tab_id=tid)
            await asyncio.sleep(0.3)
            await cdp.type_text(code_input, code, tab_id=tid)
            await asyncio.sleep(0.5)

            # Submit
            if submit_btn:
                await cdp.click(submit_btn, tab_id=tid)
            else:
                # Try Enter key via form submit
                await cdp.evaluate(
                    f'document.querySelector("{code_input}")?.form?.submit()',
                    tab_id=tid,
                )

            await asyncio.sleep(3)

            # Verify
            verified = await self.verify(tab_id=tid)
            if verified.get("signed_in"):
                self._state = "signed_in"
                from core.events import emit
                emit("signin.done", {"url": self._current_site, "method": "2fa"})
                return {"state": "signed_in", "verified": verified}
            else:
                self._state = "failed"
                from core.events import emit
                emit("signin.failed", {"url": self._current_site, "reason": "2FA verification failed"})
                return {"state": "failed", "verified": verified}

        except Exception as e:
            self._state = "failed"
            return {"state": "failed", "error": str(e)}

    async def check_2fa(self, tab_id: str = None) -> dict:
        """Check if current page is asking for 2FA."""
        return await self.detect_2fa(tab_id=tab_id or self._tab_id)

    async def verify(self, tab_id: str = None) -> dict:
        """Check if signed in successfully."""
        result = await cdp.evaluate(VERIFY_JS, tab_id=tab_id or self._tab_id)
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return {"signed_in": False, "confidence": "low", "raw": result}
        return result or {"signed_in": False}

    async def auto_signin(self, credential_name: str, tab_id: str = None) -> dict:
        """Auto sign-in using saved credential (by name). Handles TOTP if configured."""
        from app import async_session
        from models.credential import Credential
        from sqlalchemy import select

        async with async_session() as db:
            result = await db.execute(
                select(Credential).where(Credential.name == credential_name)
            )
            cred = result.scalar_one_or_none()
            if not cred:
                return {"error": f"Credential '{credential_name}' not found"}

            # Update last_used
            cred.last_used = datetime.now(timezone.utc)
            await db.commit()

            # Store credential_id
            self._credential_id = cred.id

            # Start sign-in
            signin_result = await self.start(
                url=cred.site_url,
                username=cred.username,
                password=cred.password,
                tab_id=tab_id,
                selectors=cred.selectors,
            )

            # If 2FA needed and TOTP configured, auto-handle it
            if signin_result.get("state") == "waiting_2fa" and cred.totp_secret:
                try:
                    import pyotp
                    totp = pyotp.TOTP(cred.totp_secret)
                    code = totp.now()
                    signin_result = await self.submit_2fa(code, tab_id=tab_id or self._tab_id)
                    signin_result["totp_auto"] = True
                except ImportError:
                    signin_result["error"] = "pyotp not installed — cannot auto-generate TOTP"
                except Exception as e:
                    signin_result["totp_error"] = str(e)

            return signin_result


# Global singleton
workflow = SignInWorkflow()

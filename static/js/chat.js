/* Alphabetty — Chat UI helpers */

// Chat SSE connection for real-time streaming
const chat = {
    // Keyboard shortcuts
    init() {
        document.addEventListener('keydown', (e) => {
            // Ctrl+N: New chat
            if (e.ctrlKey && e.key === 'n') {
                e.preventDefault();
                app.newChat();
            }
            // Ctrl+K: Focus input
            if (e.ctrlKey && e.key === 'k') {
                e.preventDefault();
                document.getElementById('chat-input').focus();
            }
        });
    },
};

document.addEventListener('DOMContentLoaded', () => chat.init());

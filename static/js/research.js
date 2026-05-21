/* Alphabetty — Deep Research */

app.runResearch = async function(query, mode) {
    app.streaming = true;
    app.setSendDisabled(true);

    const chatArea = document.getElementById('chat-area');

    // Progress container
    const progressDiv = document.createElement('div');
    progressDiv.className = 'research-progress';
    progressDiv.id = 'research-progress';
    chatArea.appendChild(progressDiv);

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

    app.scrollToBottom();

    const resp = await fetch('/api/research', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            query,
            conversation_id: app.currentConvId,
            depth: 3,
            mode,
        }),
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

                if (data.type === 'progress') {
                    const step = document.createElement('div');
                    step.className = 'progress-step active';
                    step.innerHTML = `<div class="step-icon">⟳</div><span>${data.message}</span>`;
                    progressDiv.appendChild(step);
                    app.scrollToBottom();

                    // Mark previous steps as done
                    progressDiv.querySelectorAll('.progress-step').forEach(s => {
                        if (s !== step) {
                            s.classList.remove('active');
                            s.classList.add('done');
                            s.querySelector('.step-icon').textContent = '✓';
                        }
                    });
                } else if (data.type === 'token') {
                    fullText += data.content;
                    const responseEl = document.getElementById('current-response');
                    if (responseEl) {
                        responseEl.innerHTML = app.renderMarkdown(fullText);
                    }
                    app.scrollToBottom();
                } else if (data.type === 'done') {
                    app.currentConvId = data.conversation_id;
                    sources = data.sources || [];
                    app.loadConversations();
                } else if (data.type === 'error') {
                    fullText += `\n\n**Error:** ${data.error}`;
                }
            } catch (e) {}
        }
    }

    // Finalize
    const responseEl = document.getElementById('current-response');
    if (responseEl) {
        responseEl.classList.remove('streaming-cursor');
        responseEl.id = '';
    }

    if (sources.length) {
        sourcesBar.innerHTML = sources.map(s => app.createSourceCard(s)).join('');
        sourcesBar.id = '';
    } else {
        sourcesBar.remove();
    }

    progressDiv.id = '';

    app.streaming = false;
    app.setSendDisabled(false);
    app.scrollToBottom();
};

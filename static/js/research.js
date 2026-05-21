/* Alphabetty — Deep Research v2 */

app.runResearch = async function(query, mode) {
    app.streaming = true;
    app.setSendDisabled(true);

    const surface = document.getElementById('surface');

    // Show pipeline
    app.updatePipeline('plan');
    app.clearTrace();

    // Response bubble
    const bubble = document.createElement('div');
    bubble.className = 'message message-assistant';
    bubble.innerHTML = '<div class="message-bubble streaming-cursor" id="current-response"></div>';
    surface.appendChild(bubble);

    // Source grid
    const sourceGrid = document.createElement('div');
    sourceGrid.className = 'source-grid';
    sourceGrid.id = 'current-sources';
    surface.appendChild(sourceGrid);

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

    // Map progress messages to pipeline stages
    const progressToStage = {
        'Planning queries': 'plan',
        'Searching': 'search',
        'Extracting': 'extract',
        'Analyzing': 'analyze',
        'Synthesizing': 'synth',
    };

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
                    // Route to pipeline + trace
                    app.appendTraceLog(data);

                    // Update pipeline stage based on message content
                    for (const [keyword, stage] of Object.entries(progressToStage)) {
                        if ((data.message || '').toLowerCase().includes(keyword.toLowerCase())) {
                            app.updatePipeline(stage);
                            break;
                        }
                    }
                    app.scrollToBottom();
                } else if (data.type === 'token') {
                    fullText += data.content;
                    const responseEl = document.getElementById('current-response');
                    if (responseEl) {
                        responseEl.innerHTML = app.renderMarkdown(fullText);
                    }
                    app.updatePipeline('synth');
                    app.scrollToBottom();
                } else if (data.type === 'done') {
                    app.currentConvId = data.conversation_id;
                    sources = data.sources || [];
                    app.loadConversations();
                } else if (data.type === 'error') {
                    fullText += `\n\n**Error:** ${data.error}`;
                    app.appendTraceLog(data);
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
        sourceGrid.innerHTML = sources.map(s => app.createSourceCard(s)).join('');
        sourceGrid.id = '';
        const srcEl = document.getElementById('stat-sources');
        if (srcEl) srcEl.textContent = (parseInt(srcEl.textContent) || 0) + sources.length;
    } else {
        sourceGrid.remove();
    }

    app.hidePipeline();
    app.streaming = false;
    app.setSendDisabled(false);
    app.scrollToBottom();
};

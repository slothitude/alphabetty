/* Alphabetty — DOM Panel + Viewport v2 */

const domPanel = {
    async loadTabs() {
        const resp = await fetch('/api/cdp/tabs');
        const data = await resp.json();
        return data.tabs || [];
    },

    async refresh() {
        const domTree = document.getElementById('dom-tree');
        domTree.innerHTML = '<div style="padding:14px;color:var(--t4);text-align:center">Loading DOM...</div>';

        try {
            const resp = await fetch('/api/cdp/dom?depth=3');
            const data = await resp.json();
            if (data.dom) {
                domTree.innerHTML = '';
                domTree.appendChild(this.renderNode(data.dom, 0));
            }
        } catch (e) {
            domTree.innerHTML = `<div style="padding:14px;color:var(--error)">Chrome not available.</div>`;
        }
    },

    renderNode(node, depth) {
        if (!node || node.nodeType !== 1) return document.createTextNode('');

        const el = document.createElement('div');

        const nodeEl = document.createElement('div');
        nodeEl.className = 'dom-node';
        nodeEl.style.paddingLeft = (depth * 14) + 'px';

        const hasChildren = node.children && node.children.length > 0;

        if (hasChildren) {
            nodeEl.textContent = '\u25B8 ';
            nodeEl.style.cursor = 'pointer';
        }

        const tag = document.createElement('span');
        tag.className = 'tag-name';
        tag.textContent = '<' + (node.nodeName || node.localName || 'div').toLowerCase();

        nodeEl.appendChild(tag);

        if (node.attributes) {
            for (let i = 0; i < node.attributes.length; i += 2) {
                const attrName = document.createElement('span');
                attrName.className = 'attr-name';
                attrName.textContent = ' ' + node.attributes[i];

                const attrVal = document.createElement('span');
                attrVal.className = 'attr-value';
                attrVal.textContent = '="' + node.attributes[i + 1] + '"';

                nodeEl.appendChild(attrName);
                nodeEl.appendChild(attrVal);
            }
        }

        const closeTag = document.createElement('span');
        closeTag.className = 'tag-name';
        closeTag.textContent = '>';
        nodeEl.appendChild(closeTag);

        el.appendChild(nodeEl);

        if (hasChildren) {
            const childContainer = document.createElement('div');
            childContainer.className = 'dom-node-children';

            for (const child of node.children) {
                const childEl = this.renderNode(child, depth + 1);
                if (childEl.textContent || childEl.children.length > 0) {
                    childContainer.appendChild(childEl);
                }
            }

            el.appendChild(childContainer);

            nodeEl.onclick = (e) => {
                e.stopPropagation();
                childContainer.classList.toggle('open');
                nodeEl.textContent = childContainer.classList.contains('open') ? '\u25BE ' : '\u25B8 ';
                nodeEl.appendChild(tag);
                if (node.attributes) {
                    for (let i = 0; i < node.attributes.length; i += 2) {
                        const an = document.createElement('span');
                        an.className = 'attr-name';
                        an.textContent = ' ' + node.attributes[i];
                        nodeEl.appendChild(an);
                        const av = document.createElement('span');
                        av.className = 'attr-value';
                        av.textContent = '="' + node.attributes[i + 1] + '"';
                        nodeEl.appendChild(av);
                    }
                }
                nodeEl.appendChild(closeTag);
            };
        }

        return el;
    },

    async getPageContent() {
        const resp = await fetch('/api/cdp/content');
        const data = await resp.json();
        return data.content || '';
    },

    async takeScreenshot() {
        try {
            const resp = await fetch('/api/cdp/screenshot');
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            window.open(url, '_blank');
        } catch (e) {
            alert('Screenshot failed: ' + e.message);
        }
    },
};

// Wire up app methods
app.refreshDom = () => domPanel.refresh();

app.navigateCdp = async (url) => {
    if (!url.startsWith('http')) url = 'https://' + url;
    await fetch('/api/cdp/navigate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
    });
    domPanel.refresh();
};

app.toggleDomPanel = () => {
    const panel = document.getElementById('dom-panel');
    app.domPanelOpen = !app.domPanelOpen;
    panel.classList.toggle('open', app.domPanelOpen);
    if (app.domPanelOpen) domPanel.refresh();
};

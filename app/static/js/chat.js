/**
 * Chat client for the conversational interface.
 *
 * Handles:
 * - Sending messages to the server via AJAX
 * - Rendering bot and user messages
 * - Rendering clickable option buttons (single and multi-select)
 * - Auto-filling form fields from bot responses
 * - Page reload on phase transitions
 * - Restoring chat history and pending options on page load
 */

(function () {
    'use strict';

    const chatMessages = document.getElementById('chat-messages');
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const chatInputArea = document.querySelector('.chat-input-area');

    // Don't initialize if chat panel is not present
    if (!chatMessages || !chatForm || !chatInput) return;

    // Extract slug from URL: /projects/{slug}/...
    const slugMatch = window.location.pathname.match(/\/projects\/([^/]+)/);
    if (!slugMatch) return;
    const slug = slugMatch[1];

    // Detect current page context
    const currentPath = window.location.pathname;
    const isLockablePhase = currentPath.includes('/feature-discovery');
    const LOCK_SIGNAL = '__LOCK_FEATURES__';

    // --- Render a message bubble ---
    function renderMessage(role, text) {
        const bubble = document.createElement('div');
        bubble.className = 'chat-bubble ' + role;

        // Simple markdown-like bold: **text**
        let html = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\n/g, '<br>');

        bubble.innerHTML = html;
        chatMessages.appendChild(bubble);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    // --- Show typing indicator ---
    function showTyping() {
        const indicator = document.createElement('div');
        indicator.className = 'typing-indicator';
        indicator.id = 'typing-indicator';
        indicator.innerHTML = '<span></span><span></span><span></span>';
        chatMessages.appendChild(indicator);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function hideTyping() {
        const indicator = document.getElementById('typing-indicator');
        if (indicator) indicator.remove();
    }

    // --- Apply field updates to form fields ---
    function applyFieldUpdates(updates) {
        if (!updates) return;
        for (const [fieldId, value] of Object.entries(updates)) {
            const el = document.getElementById(fieldId);
            if (el) {
                el.value = value;
                // Trigger change event so any listeners are notified
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }
    }

    // --- HTML escape helper ---
    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // --- Update extracted features in right pane ---
    function updateExtractedFeatures(features) {
        var container = document.getElementById('extracted-features-list');
        if (!container || !features) return;
        container.innerHTML = '';
        features.forEach(function (feat) {
            var card = document.createElement('div');
            card.className = 'extracted-feature-card';
            card.innerHTML =
                '<div class="feature-name">' + escapeHtml(feat.name) + '</div>' +
                (feat.description ? '<div class="feature-desc">' + escapeHtml(feat.description) + '</div>' : '');
            container.appendChild(card);
        });
        var badge = document.getElementById('extracted-features-count');
        if (badge) badge.textContent = features.length;
        var section = document.getElementById('extracted-features');
        if (section) section.style.display = features.length > 0 ? 'block' : 'none';
    }

    // --- Remove any existing option buttons ---
    function clearOptions() {
        const existing = document.getElementById('chat-options');
        if (existing) existing.remove();
    }

    // --- Render "Lock Features & Continue" button in lockable phases ---
    function renderLockButton(container) {
        if (!isLockablePhase) return;
        var lockBtn = document.createElement('button');
        lockBtn.className = 'chat-option-lock btn btn-success btn-sm';
        lockBtn.textContent = 'Lock Features & Continue';
        lockBtn.addEventListener('click', function () {
            lockBtn.disabled = true;
            sendMessage(LOCK_SIGNAL);
        });
        container.appendChild(lockBtn);
    }

    // --- Render clickable option buttons ---
    function renderOptions(options, mode) {
        clearOptions();

        const container = document.createElement('div');
        container.id = 'chat-options';
        container.className = 'chat-options';

        if (mode === 'multi') {
            // Multi-select: checkboxes + confirm button
            options.forEach(function (label) {
                var checkRow = document.createElement('label');
                checkRow.className = 'chat-option-check';
                var cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.value = label;
                checkRow.appendChild(cb);
                var span = document.createElement('span');
                span.textContent = label;
                checkRow.appendChild(span);
                container.appendChild(checkRow);
            });

            var btnRow = document.createElement('div');
            btnRow.className = 'chat-option-btn-row';

            var confirmBtn = document.createElement('button');
            confirmBtn.className = 'chat-option-confirm btn btn-primary btn-sm';
            confirmBtn.textContent = 'Confirm selections';
            confirmBtn.addEventListener('click', function () {
                var checked = container.querySelectorAll('input[type="checkbox"]:checked');
                var labels = [];
                checked.forEach(function (cb) { labels.push(cb.value); });
                if (labels.length === 0) return;
                sendMessage(labels.join(', '));
            });
            btnRow.appendChild(confirmBtn);
            renderLockButton(btnRow);
            container.appendChild(btnRow);
        } else {
            // Single-select: clickable buttons
            options.forEach(function (label) {
                var btn = document.createElement('button');
                btn.className = 'chat-option-btn';
                btn.textContent = label;
                btn.addEventListener('click', function () {
                    sendMessage(label);
                });
                container.appendChild(btn);
            });
            renderLockButton(container);
        }

        chatMessages.appendChild(container);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    // --- Send message to server ---
    async function sendMessage(text) {
        if (!text.trim()) return;

        // Clear any existing options
        clearOptions();

        // Render user message (show friendly text for lock signal)
        renderMessage('user', text === LOCK_SIGNAL ? 'Locking features...' : text);
        chatInput.value = '';
        chatInput.focus();

        // Show typing indicator
        showTyping();

        try {
            const resp = await fetch('/projects/' + slug + '/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text }),
            });

            hideTyping();

            if (!resp.ok) {
                renderMessage('bot', 'Something went wrong. Please try again.');
                return;
            }

            const data = await resp.json();

            // Render bot messages
            if (data.bot_messages) {
                data.bot_messages.forEach(function (msg) {
                    renderMessage('bot', msg);
                });
            }

            // Render option buttons if present
            if (data.options && data.options.length > 0) {
                renderOptions(data.options, data.options_mode || 'single');
            }

            // Update extracted features in right pane
            if (data.extracted_features) {
                updateExtractedFeatures(data.extracted_features);
            }

            // Apply field updates
            if (data.field_updates) {
                applyFieldUpdates(data.field_updates);
            }

            // Navigate to next phase URL or reload
            if (data.redirect_url) {
                chatInput.disabled = true;
                document.getElementById('chat-send').disabled = true;
                setTimeout(function () {
                    window.location.href = data.redirect_url;
                }, 1200);
            } else if (data.reload) {
                setTimeout(function () {
                    window.location.reload();
                }, 1000);
            }

        } catch (err) {
            hideTyping();
            renderMessage('bot', 'Connection error. Please try again.');
        }
    }

    // --- Form submit handler ---
    chatForm.addEventListener('submit', function (e) {
        e.preventDefault();
        sendMessage(chatInput.value);
    });

    // --- Enter to send (Shift+Enter for newline) ---
    chatInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage(chatInput.value);
        }
    });

    // --- Load chat history on page load ---
    async function loadHistory() {
        try {
            const resp = await fetch('/projects/' + slug + '/api/chat');
            if (!resp.ok) return;

            const data = await resp.json();
            if (data.messages && data.messages.length > 0) {
                data.messages.forEach(function (msg) {
                    var displayText = (msg.text === LOCK_SIGNAL) ? 'Locking features...' : msg.text;
                    renderMessage(msg.role, displayText);
                });
            }

            // Restore pending options if present (e.g. after page refresh)
            if (data.pending_options && data.pending_options.length > 0) {
                renderOptions(data.pending_options, data.pending_options_mode || 'single');
            }

            // Update extracted features on page load
            if (data.extracted_features) {
                updateExtractedFeatures(data.extracted_features);
            }
        } catch (err) {
            // Silently fail — chat history is nice-to-have
        }
    }

    loadHistory();

})();

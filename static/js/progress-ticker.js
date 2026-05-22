/**
 * Progress Ticker — shared UI for keyword scoring & AI thinking animations.
 * Used by AI Researcher, AI Competitor, and ASO Simulator templates.
 */
(function() {
    'use strict';

    // Classification → icon (matches dashboard classificationBadge)
    var ICON_MAP = {
        'Sweet Spot': '\uD83C\uDFAF',     // 🎯
        'Hidden Gem': '\uD83D\uDC8E',      // 💎
        'Good Target': '\u2705',            // ✅
        'Moderate': '\uD83D\uDC4D',        // 👍
        'Low Volume': '\uD83D\uDD0D',      // 🔍
        'High Competition': '\u2694\uFE0F', // ⚔️
        'Avoid': '\uD83D\uDEAB'            // 🚫
    };

    // Classification → badge CSS + ring stroke color (consistent with dashboard)
    var STYLE_MAP = {
        'Sweet Spot':       { badge: 'bg-green-900/20 border-green-500/20 text-green-300', ring: '#22c55e' },
        'Good Target':      { badge: 'bg-green-900/20 border-green-500/20 text-green-300', ring: '#22c55e' },
        'Hidden Gem':       { badge: 'bg-blue-900/20 border-blue-500/20 text-blue-300',    ring: '#3b82f6' },
        'High Competition': { badge: 'bg-yellow-900/20 border-yellow-500/20 text-yellow-300', ring: '#eab308' },
        'Moderate':         { badge: 'bg-slate-800 border-white/10 text-slate-300',        ring: '#64748b' },
        'Low Volume':       { badge: 'bg-slate-800 border-white/10 text-slate-300',        ring: '#64748b' },
        'Avoid':            { badge: 'bg-red-900/20 border-red-500/20 text-red-300',       ring: '#ef4444' }
    };

    // SVG arc constants: circumference of r=23 circle
    var CIRCUMFERENCE = 2 * Math.PI * 23; // ≈ 144.51

    // Live stats state — reset on each start()
    var elapsedTimer = null;
    var startedAt = 0;
    var scoredTerms = Object.create(null);
    var scoredCount = 0;

    function formatElapsed(ms) {
        var s = Math.max(0, Math.floor(ms / 1000));
        var m = Math.floor(s / 60);
        var rem = s % 60;
        return m + ':' + (rem < 10 ? '0' + rem : rem);
    }

    function updateElapsedEl() {
        var el = document.getElementById('progress-elapsed');
        if (el) el.textContent = formatElapsed(Date.now() - startedAt);
    }

    function normalizeElapsedSeconds(seconds) {
        return (typeof seconds === 'number' && isFinite(seconds) && seconds > 0) ? seconds : 0;
    }

    function syncElapsed(seconds) {
        var elapsedSeconds = normalizeElapsedSeconds(seconds);
        if (!elapsedSeconds) return;
        startedAt = Date.now() - (elapsedSeconds * 1000);
        updateElapsedEl();
    }

    function startElapsed(initialElapsedSeconds) {
        startedAt = Date.now() - (normalizeElapsedSeconds(initialElapsedSeconds) * 1000);
        var el = document.getElementById('progress-elapsed');
        if (el) {
            el.textContent = formatElapsed(Date.now() - startedAt);
            el.classList.remove('hidden');
        }
        if (elapsedTimer) clearInterval(elapsedTimer);
        elapsedTimer = setInterval(updateElapsedEl, 1000);
    }

    function stopElapsed() {
        if (elapsedTimer) {
            clearInterval(elapsedTimer);
            elapsedTimer = null;
        }
    }

    function resetCounter() {
        scoredTerms = Object.create(null);
        scoredCount = 0;
        var el = document.getElementById('progress-scored-count');
        if (el) {
            el.textContent = '';
            el.classList.add('hidden');
        }
    }

    function renderCounter() {
        var el = document.getElementById('progress-scored-count');
        if (!el) return;
        if (scoredCount > 0) {
            el.textContent = scoredCount + (scoredCount === 1 ? ' keyword scored' : ' keywords scored');
            el.classList.remove('hidden');
        } else {
            el.textContent = '';
            el.classList.add('hidden');
        }
    }

    /**
     * Adopt a server-reported scored count. Server is the floor (survives tab
     * switches), client never goes backwards. Client may briefly race ahead
     * via bumpCounter() between polls; we keep whichever is higher.
     */
    function syncScoredCount(n) {
        if (typeof n !== 'number' || !isFinite(n) || n <= scoredCount) return;
        scoredCount = Math.floor(n);
        renderCounter();
    }

    function seedScoredCount(initialScoredCount) {
        var n = (typeof initialScoredCount === 'number' && isFinite(initialScoredCount) && initialScoredCount > 0)
            ? Math.floor(initialScoredCount) : 0;
        scoredCount = n;
        renderCounter();
    }

    function bumpCounter(keyword) {
        var key = (keyword || '').trim().toLowerCase();
        if (!key || scoredTerms[key]) return;
        scoredTerms[key] = true;
        scoredCount += 1;
        renderCounter();
    }

    /**
     * Transition a state element to visible, hiding others.
     */
    function showState(showId) {
        var ids = ['progress-idle', 'keyword-ticker', 'ai-thinking'];
        for (var i = 0; i < ids.length; i++) {
            var el = document.getElementById(ids[i]);
            if (!el) continue;
            if (ids[i] === showId) {
                el.style.opacity = '1';
                el.style.visibility = 'visible';
            } else {
                el.style.opacity = '0';
                el.style.visibility = 'hidden';
            }
        }
    }

    /**
     * Update the progress UI based on structured progress_data from the server.
     */
    function updateProgressUI(progressData) {
        if (!progressData || !progressData.type) return;

        if (progressData.type === 'keyword_scored') {
            bumpCounter(progressData.keyword);
            showKeywordCard(progressData);
        } else if (progressData.type === 'ai_thinking') {
            showAIThinking(progressData);
        } else if (progressData.type === 'idle') {
            showState('progress-idle');
        }
    }

    /**
     * Show a scored keyword card with opportunity ring animation.
     */
    function showKeywordCard(data) {
        var ticker = document.getElementById('keyword-ticker');
        if (!ticker) return;

        showState('keyword-ticker');

        var style = STYLE_MAP[data.classification] || STYLE_MAP['Moderate'];
        var icon = ICON_MAP[data.classification] || '\uD83D\uDC4D';
        var opp = data.opportunity || 0;

        // Opportunity ring: animate arc fill
        var arc = document.getElementById('opp-ring-arc');
        var val = document.getElementById('opp-ring-value');
        if (arc) {
            // Reset to zero first for animation effect
            arc.style.transition = 'none';
            arc.setAttribute('stroke-dashoffset', CIRCUMFERENCE);
            arc.setAttribute('stroke', style.ring);
            // Force reflow then animate
            arc.getBoundingClientRect();
            arc.style.transition = 'stroke-dashoffset 0.7s ease-out';
            var offset = CIRCUMFERENCE * (1 - opp / 100);
            arc.setAttribute('stroke-dashoffset', offset);
        }
        if (val) {
            val.textContent = Math.round(opp);
        }

        // Icon + keyword + classification badge
        var iconEl = document.getElementById('ticker-icon');
        if (iconEl) iconEl.textContent = icon;

        var kwEl = document.getElementById('ticker-keyword');
        if (kwEl) kwEl.textContent = data.keyword;

        var classEl = document.getElementById('ticker-classification');
        if (classEl) {
            classEl.textContent = data.classification;
            classEl.className = 'text-[11px] px-2.5 py-0.5 rounded-full font-medium whitespace-nowrap border ' + style.badge;
        }

        // Metric pills
        var popEl = document.getElementById('ticker-pop');
        if (popEl) popEl.textContent = Math.round(data.popularity);

        var diffEl = document.getElementById('ticker-diff');
        if (diffEl) diffEl.textContent = Math.round(data.difficulty);

        var oppEl = document.getElementById('ticker-opp');
        if (oppEl) oppEl.textContent = Math.round(opp);
    }

    /**
     * Show the AI thinking state with neural network animation.
     */
    function showAIThinking(data) {
        showState('ai-thinking');

        var label = document.getElementById('ai-thinking-label');
        if (label) {
            label.textContent = data.phase_label || 'AI is thinking...';
        }
    }

    /**
     * Reset all progress extras to initial idle state. Call on session end.
     */
    function hideProgressExtras() {
        stopElapsed();
        var elapsedEl = document.getElementById('progress-elapsed');
        if (elapsedEl) elapsedEl.classList.add('hidden');
        resetCounter();
        var ids = ['keyword-ticker', 'ai-thinking', 'progress-idle'];
        for (var i = 0; i < ids.length; i++) {
            var el = document.getElementById(ids[i]);
            if (el) {
                el.style.opacity = '0';
                el.style.visibility = 'hidden';
            }
        }
        // Reset idle to visible for next session
        var idle = document.getElementById('progress-idle');
        if (idle) {
            idle.style.opacity = '1';
            idle.style.visibility = 'visible';
        }
        // Reset opportunity ring
        var arc = document.getElementById('opp-ring-arc');
        if (arc) {
            arc.style.transition = 'none';
            arc.setAttribute('stroke-dashoffset', CIRCUMFERENCE);
        }
    }

    // Expose API globally
    window.progressTicker = {
        start: function(initialElapsedSeconds, initialScoredCount) {
            resetCounter();
            seedScoredCount(initialScoredCount);
            startElapsed(initialElapsedSeconds);
        },
        syncElapsed: syncElapsed,
        syncScoredCount: syncScoredCount,
        update: updateProgressUI,
        hide: hideProgressExtras
    };

    /**
     * Format an elapsed-seconds float as M:SS for display.
     */
    function formatElapsedSeconds(secs) {
        if (typeof secs !== 'number' || !isFinite(secs) || secs < 0) return '';
        var s = Math.round(secs);
        var m = Math.floor(s / 60);
        var rem = s % 60;
        return m + ':' + (rem < 10 ? '0' + rem : rem);
    }

    /**
     * Render the per-run stat badges in a results header.
     *
     * Reads `elapsed_seconds` and `chain_stats` from the results JSON and
     * populates `#results-elapsed-badge` and `#results-chain-badge` if present.
     * Badges are hidden if the data is missing (e.g. legacy sessions without
     * elapsed_seconds, or single-iteration sessions without a chain).
     */
    function renderRunStatsBadges(data) {
        // Elapsed-time badge — hidden for legacy sessions where this wasn't tracked
        var elapsedBadge = document.getElementById('results-elapsed-badge');
        if (elapsedBadge) {
            var elapsedText = formatElapsedSeconds(data && data.elapsed_seconds);
            if (elapsedText) {
                elapsedBadge.textContent = 'took ' + elapsedText;
                elapsedBadge.classList.remove('hidden');
            } else {
                elapsedBadge.classList.add('hidden');
            }
        }

        // Chain stats badge — only shown when this run is part of an iteration chain
        var chainBadge = document.getElementById('results-chain-badge');
        if (chainBadge) {
            var stats = (data && data.chain_stats) || null;
            if (stats && stats.chain_length > 1 && stats.chain_total_unique > 0) {
                chainBadge.textContent = stats.chain_total_unique +
                    ' unique across ' + stats.chain_length + ' iterations';
                chainBadge.classList.remove('hidden');
            } else {
                chainBadge.classList.add('hidden');
            }
        }
    }
    window.renderRunStatsBadges = renderRunStatsBadges;
})();

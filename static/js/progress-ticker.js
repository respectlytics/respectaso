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
            showKeywordCard(progressData);
        } else if (progressData.type === 'ai_thinking') {
            showAIThinking(progressData);
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
        update: updateProgressUI,
        hide: hideProgressExtras
    };
})();

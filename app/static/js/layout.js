/**
 * Layout controls for the 3-pane interface.
 *
 * Handles collapse/expand of left nav and right context panes
 * with localStorage persistence across page navigations.
 */

(function () {
    'use strict';

    function setup(toggleId, paneId, key, collapseIcon, expandIcon) {
        var toggle = document.getElementById(toggleId);
        var pane = document.getElementById(paneId);
        if (!toggle || !pane) return;

        // Restore state from localStorage
        if (localStorage.getItem(key) === 'collapsed') {
            pane.classList.add('collapsed');
            toggle.textContent = expandIcon;
        }

        toggle.addEventListener('click', function () {
            pane.classList.toggle('collapsed');
            var collapsed = pane.classList.contains('collapsed');
            localStorage.setItem(key, collapsed ? 'collapsed' : 'expanded');
            toggle.textContent = collapsed ? expandIcon : collapseIcon;
        });
    }

    setup('left-nav-toggle', 'left-nav', 'leftNavState', '\u00AB', '\u00BB');
    setup('right-pane-toggle', 'right-pane', 'rightPaneState', '\u00BB', '\u00AB');
})();

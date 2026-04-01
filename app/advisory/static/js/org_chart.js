/**
 * D3.js-based org chart for the AI Advisory layer.
 * Reads org data from a <script type="application/json"> tag and renders
 * an SVG tree layout.
 */
(function () {
    'use strict';

    var dataEl = document.getElementById('org-data');
    var container = document.getElementById('org-chart');
    if (!dataEl || !container) return;

    var data;
    try {
        data = JSON.parse(dataEl.textContent);
    } catch (e) {
        container.innerHTML = '<p class="text-muted">Unable to render org chart.</p>';
        return;
    }

    if (!data || !data.title) {
        container.innerHTML = '<p class="text-muted">No organization data available.</p>';
        return;
    }

    // Chart dimensions
    var margin = { top: 40, right: 120, bottom: 40, left: 120 };
    var containerWidth = container.clientWidth || 900;

    // Build hierarchy
    var root = d3.hierarchy(data, function (d) { return d.children; });
    var nodeCount = root.descendants().length;
    var treeHeight = Math.max(400, nodeCount * 60);

    var treeLayout = d3.tree().size([treeHeight, containerWidth - margin.left - margin.right]);
    treeLayout(root);

    // Create SVG
    var svg = d3.select('#org-chart')
        .append('svg')
        .attr('width', containerWidth)
        .attr('height', treeHeight + margin.top + margin.bottom);

    var g = svg.append('g')
        .attr('transform', 'translate(' + margin.left + ',' + margin.top + ')');

    // Draw links
    g.selectAll('.org-link')
        .data(root.links())
        .join('path')
        .attr('class', 'org-link')
        .attr('d', function (d) {
            return 'M' + d.source.y + ',' + d.source.x
                + 'C' + (d.source.y + d.target.y) / 2 + ',' + d.source.x
                + ' ' + (d.source.y + d.target.y) / 2 + ',' + d.target.x
                + ' ' + d.target.y + ',' + d.target.x;
        });

    // Draw nodes
    var nodeWidth = 180;
    var nodeHeight = 50;

    var node = g.selectAll('.org-node')
        .data(root.descendants())
        .join('g')
        .attr('class', function (d) { return 'org-node ' + (d.data.type || ''); })
        .attr('transform', function (d) { return 'translate(' + d.y + ',' + d.x + ')'; });

    node.append('rect')
        .attr('x', -nodeWidth / 2)
        .attr('y', -nodeHeight / 2)
        .attr('width', nodeWidth)
        .attr('height', nodeHeight);

    node.append('text')
        .attr('class', 'title')
        .attr('dy', '-0.2em')
        .attr('text-anchor', 'middle')
        .text(function (d) {
            var title = d.data.title || '';
            return title.length > 22 ? title.substring(0, 20) + '...' : title;
        });

    node.append('text')
        .attr('class', 'dept')
        .attr('dy', '1.2em')
        .attr('text-anchor', 'middle')
        .text(function (d) { return d.data.department || ''; });

    // Tooltips
    node.append('title')
        .text(function (d) {
            var lines = [d.data.title];
            if (d.data.department) lines.push('Department: ' + d.data.department);
            if (d.data.responsibilities && d.data.responsibilities.length) {
                lines.push('Responsibilities:');
                d.data.responsibilities.forEach(function (r) { lines.push('  - ' + r); });
            }
            if (d.data.estimated_fte_equivalent) {
                lines.push('FTE Equivalent: ' + d.data.estimated_fte_equivalent);
            }
            return lines.join('\n');
        });
})();

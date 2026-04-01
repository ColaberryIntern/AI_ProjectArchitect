/**
 * Interactive Network Graph with Deep Zoom
 * Level 1: System overview (AI COO + departments)
 * Level 2: Department drill-down (agents within department)
 * Level 3: Agent detail panel
 */
(function() {
    'use strict';

    var dataEl = document.getElementById('network-data');
    var container = document.getElementById('network-graph');
    if (!dataEl || !container) return;

    var rawAgents;
    try { rawAgents = JSON.parse(dataEl.textContent); } catch(e) { return; }
    if (!rawAgents || !rawAgents.length) {
        container.innerHTML = '<p class="text-muted text-center py-5">No agents to visualize</p>';
        return;
    }

    var deptColors = {
        'Executive': '#1a1a2e', 'Sales': '#4361ee', 'Customer Support': '#6f42c1',
        'Marketing': '#198754', 'Operations': '#fd7e14', 'Finance': '#dc3545',
        'Human Resources': '#6c757d', 'Technology': '#0dcaf0', 'Communication': '#20c997'
    };

    // ── Parse data ─────────────────────────────────────────────────
    var cooAgent = null;
    var deptMap = {};  // dept -> [agents]
    rawAgents.forEach(function(a) {
        if (a.is_cory) { cooAgent = a; return; }
        var dept = a.department || 'Operations';
        if (!deptMap[dept]) deptMap[dept] = [];
        deptMap[dept].push(a);
    });

    var width = container.clientWidth || 800;
    var height = Math.max(450, Math.min(width * 0.6, 550));

    // ── State ──────────────────────────────────────────────────────
    var zoomLevel = 'system'; // system | department | agent
    var selectedDept = null;
    var selectedAgent = null;

    // ── Container setup ────────────────────────────────────────────
    container.innerHTML = '';
    container.style.position = 'relative';

    // Breadcrumb
    var breadcrumb = document.createElement('div');
    breadcrumb.className = 'mb-2';
    breadcrumb.style.fontSize = '0.8rem';
    container.appendChild(breadcrumb);

    // Hint text
    var hint = document.createElement('div');
    hint.className = 'text-center text-muted mb-2';
    hint.style.fontSize = '0.75rem';
    container.appendChild(hint);

    // SVG container
    var svgContainer = document.createElement('div');
    svgContainer.style.position = 'relative';
    container.appendChild(svgContainer);

    // Detail panel (for agent view)
    var detailPanel = document.createElement('div');
    detailPanel.style.cssText = 'display:none;position:absolute;right:0;top:0;width:280px;background:white;border:1px solid #dee2e6;border-radius:12px;padding:16px;box-shadow:0 8px 24px rgba(0,0,0,0.15);z-index:20;font-size:0.8rem;max-height:' + height + 'px;overflow-y:auto;';
    svgContainer.appendChild(detailPanel);

    // ── Render Functions ───────────────────────────────────────────

    function renderSystemView() {
        zoomLevel = 'system'; selectedDept = null; selectedAgent = null;
        updateBreadcrumb();
        hint.innerHTML = '<i class="bi bi-hand-index me-1"></i>Click a department to explore its AI agents';

        var nodes = [];
        var edges = [];

        if (cooAgent) {
            nodes.push({ id: 'cory', label: 'AI COO', type: 'cory', r: 38, color: '#1a1a2e' });
        }
        Object.keys(deptMap).forEach(function(dept) {
            var id = 'dept_' + dept.replace(/\s/g, '_');
            var count = deptMap[dept].length;
            nodes.push({ id: id, label: dept, type: 'system', r: 20 + count * 3, color: deptColors[dept] || '#4361ee', dept: dept, agentCount: count });
            if (cooAgent) edges.push({ source: id, target: 'cory' });
        });

        renderGraph(nodes, edges, function(d) {
            if (d.type === 'system') renderDeptView(d.dept);
            if (d.type === 'cory') renderCoryView();
        });
        detailPanel.style.display = 'none';
    }

    function renderDeptView(dept) {
        zoomLevel = 'department'; selectedDept = dept; selectedAgent = null;
        updateBreadcrumb();
        hint.innerHTML = '<i class="bi bi-hand-index me-1"></i>Click an agent to see how it works';

        var agents = deptMap[dept] || [];
        var nodes = [{ id: 'dept_center', label: dept + ' AI System', type: 'system', r: 30, color: deptColors[dept] || '#4361ee', dept: dept }];
        var edges = [];

        agents.forEach(function(a, i) {
            var id = 'agent_' + i;
            nodes.push({
                id: id, label: a.name.replace('AI ', ''), type: a.is_specialist ? 'specialist' : 'agent',
                r: a.is_primary_focus ? 18 : 14, color: a.is_specialist ? '#198754' : (deptColors[dept] || '#4361ee'),
                agentData: a
            });
            edges.push({ source: id, target: 'dept_center' });
        });

        // Add AI COO connection
        if (cooAgent) {
            nodes.push({ id: 'cory_link', label: 'AI COO', type: 'cory', r: 22, color: '#1a1a2e' });
            edges.push({ source: 'dept_center', target: 'cory_link' });
        }

        renderGraph(nodes, edges, function(d) {
            if (d.agentData) renderAgentDetail(d.agentData, d);
            if (d.type === 'cory') renderCoryView();
        });
    }

    function renderCoryView() {
        zoomLevel = 'department'; selectedDept = 'AI COO';
        updateBreadcrumb();
        hint.textContent = 'AI COO monitors all departments and triggers cross-system actions';

        var nodes = [{ id: 'cory_center', label: 'AI COO', type: 'cory', r: 45, color: '#1a1a2e' }];
        var edges = [];

        Object.keys(deptMap).forEach(function(dept) {
            var id = 'dept_' + dept.replace(/\s/g, '_');
            nodes.push({ id: id, label: dept, type: 'system', r: 18, color: deptColors[dept] || '#4361ee', dept: dept });
            edges.push({ source: id, target: 'cory_center' });
        });

        renderGraph(nodes, edges, function(d) {
            if (d.type === 'system' && d.dept) renderDeptView(d.dept);
        });
        detailPanel.style.display = 'none';
    }

    function renderAgentDetail(agent, nodeData) {
        selectedAgent = agent;
        updateBreadcrumb();

        // Highlight the selected node
        d3.select(svgContainer).selectAll('circle').transition().duration(300)
            .attr('opacity', function(d) { return d === nodeData ? 1 : 0.3; })
            .attr('stroke-width', function(d) { return d === nodeData ? 4 : 2; })
            .attr('stroke', function(d) { return d === nodeData ? '#ffc107' : '#fff'; });

        var triggerBadge = {'event': 'primary', 'time': 'warning', 'threshold': 'danger'}[agent.trigger_type] || 'secondary';

        detailPanel.innerHTML =
            '<div class="d-flex justify-content-between align-items-start mb-2">' +
                '<strong>' + agent.name + '</strong>' +
                '<button class="btn btn-sm btn-outline-secondary border-0 p-0" onclick="document.querySelector(\'[data-close-detail]\').click()" style="line-height:1">&times;</button>' +
            '</div>' +
            '<span class="badge bg-' + (deptColors[agent.department] ? 'primary' : 'secondary') + '" style="font-size:0.65rem;background:' + (deptColors[agent.department] || '#6c757d') + ' !important">' + agent.department + '</span>' +
            ' <span class="badge bg-' + triggerBadge + '" style="font-size:0.65rem">' + (agent.trigger_type || 'event') + '</span>' +
            (agent.is_specialist ? ' <span class="badge bg-success" style="font-size:0.65rem">Specialist</span>' : '') +
            '<hr class="my-2">' +
            '<div class="mb-2"><strong>Role:</strong><br><span class="text-muted">' + (agent.role || '').substring(0, 200) + '</span></div>' +
            '<div class="mb-2"><strong>Trigger:</strong><br><span class="text-muted">' + (agent.trigger || 'On demand') + '</span></div>' +
            (agent.inputs ? '<div class="mb-2"><strong>Inputs:</strong><br><span class="text-muted">' + agent.inputs.join(', ') + '</span></div>' : '') +
            (agent.outputs ? '<div class="mb-2"><strong>Outputs:</strong><br><span class="text-muted">' + agent.outputs.join(', ') + '</span></div>' : '') +
            (agent.connected_mcp_servers && agent.connected_mcp_servers.length ? '<div class="mb-2"><strong>Systems:</strong><br><span class="text-muted">' + agent.connected_mcp_servers.join(', ') + '</span></div>' : '');

        detailPanel.style.display = 'block';
    }

    // ── Core Graph Renderer ────────────────────────────────────────
    function renderGraph(nodes, edges, onClickFn) {
        // Clear previous
        d3.select(svgContainer).selectAll('svg').remove();
        detailPanel.style.display = 'none';

        var svgW = detailPanel.style.display === 'none' ? width : width - 290;
        var svg = d3.select(svgContainer).insert('svg', ':first-child')
            .attr('width', width).attr('height', height)
            .attr('viewBox', '0 0 ' + width + ' ' + height);

        // Glow filter
        var defs = svg.append('defs');
        var filter = defs.append('filter').attr('id', 'glow2');
        filter.append('feGaussianBlur').attr('stdDeviation', '4').attr('result', 'blur');
        var merge = filter.append('feMerge');
        merge.append('feMergeNode').attr('in', 'blur');
        merge.append('feMergeNode').attr('in', 'SourceGraphic');
        defs.append('style').text('@keyframes dash2{from{stroke-dashoffset:20}to{stroke-dashoffset:0}}.edge2{stroke-dasharray:5,5;animation:dash2 1.5s linear infinite}');

        var sim = d3.forceSimulation(nodes)
            .force('link', d3.forceLink(edges).id(function(d){return d.id}).distance(80))
            .force('charge', d3.forceManyBody().strength(-300))
            .force('center', d3.forceCenter(width/2, height/2))
            .force('collision', d3.forceCollide().radius(function(d){return d.r + 12}));

        var link = svg.append('g').selectAll('line')
            .data(edges).join('line')
            .attr('class', 'edge2')
            .attr('stroke', '#cbd5e1').attr('stroke-width', 1.5).attr('stroke-opacity', 0.5);

        var nodeG = svg.append('g').selectAll('g')
            .data(nodes).join('g')
            .attr('cursor', 'pointer')
            .call(d3.drag()
                .on('start', function(e,d){if(!e.active)sim.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y})
                .on('drag', function(e,d){d.fx=e.x;d.fy=e.y})
                .on('end', function(e,d){if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null})
            );

        nodeG.append('circle')
            .attr('r', function(d){return d.r})
            .attr('fill', function(d){return d.type==='agent'||d.type==='specialist' ? d3.color(d.color).brighter(0.6) : d.color})
            .attr('stroke', function(d){return d.type==='cory'?'#4361ee':'#fff'})
            .attr('stroke-width', function(d){return d.type==='cory'?3:2})
            .attr('filter', function(d){return d.type==='cory'?'url(#glow2)':null})
            .attr('opacity', 0).transition().duration(400).attr('opacity', 1);

        // Pulse for AI COO
        nodeG.filter(function(d){return d.type==='cory'}).append('circle')
            .attr('r', function(d){return d.r+5}).attr('fill','none').attr('stroke','#4361ee').attr('stroke-width',2);

        // Labels
        nodeG.append('text').attr('text-anchor','middle').attr('dy','0.35em')
            .attr('fill', function(d){return d.type==='agent'||d.type==='specialist'?'#333':'#fff'})
            .attr('font-size', function(d){return d.type==='cory'?'16px':d.type==='system'?'10px':'0px'})
            .attr('font-weight','bold')
            .text(function(d){return d.type==='cory'?'🧠':d.type==='system'?d.label.substring(0,3).toUpperCase():'';});

        nodeG.append('text').attr('text-anchor','middle')
            .attr('dy', function(d){return d.r+13}).attr('fill','#333').attr('font-size','9px').attr('font-weight','500')
            .text(function(d){var t=d.label; return t.length>20?t.substring(0,18)+'..':t;});

        // Agent count badge on system nodes
        nodeG.filter(function(d){return d.type==='system' && d.agentCount})
            .append('text').attr('text-anchor','middle').attr('dy','-' + 4)
            .attr('fill','#fff').attr('font-size','8px')
            .text(function(d){return d.agentCount + ' agents';});

        // Click handler
        nodeG.on('click', function(event, d) {
            event.stopPropagation();
            if (onClickFn) onClickFn(d);
        });

        // Click background to deselect
        svg.on('click', function() {
            if (selectedAgent) {
                selectedAgent = null;
                detailPanel.style.display = 'none';
                nodeG.selectAll('circle').transition().duration(200).attr('opacity', 1).attr('stroke-width', function(d){return d.type==='cory'?3:2}).attr('stroke', function(d){return d.type==='cory'?'#4361ee':'#fff';});
            }
        });

        sim.on('tick', function() {
            link.attr('x1',function(d){return d.source.x}).attr('y1',function(d){return d.source.y})
                .attr('x2',function(d){return d.target.x}).attr('y2',function(d){return d.target.y});
            nodeG.attr('transform', function(d){
                d.x = Math.max(d.r, Math.min(width-d.r, d.x));
                d.y = Math.max(d.r, Math.min(height-d.r, d.y));
                return 'translate('+d.x+','+d.y+')';
            });
        });
    }

    // ── Breadcrumb ─────────────────────────────────────────────────
    function updateBreadcrumb() {
        var parts = ['<a href="#" onclick="window._netZoomSystem();return false" class="text-decoration-none">System</a>'];
        if (selectedDept) parts.push('<span class="text-muted mx-1">›</span><a href="#" onclick="window._netZoomDept(\'' + selectedDept.replace(/'/g,"\\'") + '\');return false" class="text-decoration-none">' + selectedDept + '</a>');
        if (selectedAgent) parts.push('<span class="text-muted mx-1">›</span><strong>' + selectedAgent.name + '</strong>');
        breadcrumb.innerHTML = parts.join('');
    }

    // ── Public API ─────────────────────────────────────────────────
    window._netZoomSystem = renderSystemView;
    window._netZoomDept = renderDeptView;
    window.highlightNetworkNode = function(agentName) {
        // Works at any zoom level — finds matching node and pulses it
        d3.select(svgContainer).selectAll('circle:first-of-type')
            .transition().duration(200)
            .attr('stroke-width', function(d) {
                return (d.label || '').toLowerCase().indexOf(agentName.toLowerCase().replace('ai ', '')) >= 0 ? 5 : 2;
            })
            .attr('stroke', function(d) {
                return (d.label || '').toLowerCase().indexOf(agentName.toLowerCase().replace('ai ', '')) >= 0 ? '#ffc107' : (d.type==='cory'?'#4361ee':'#fff');
            });
        setTimeout(function() {
            d3.select(svgContainer).selectAll('circle:first-of-type').transition().duration(500)
                .attr('stroke-width', function(d){return d.type==='cory'?3:2})
                .attr('stroke', function(d){return d.type==='cory'?'#4361ee':'#fff'});
        }, 1500);
    };

    // ── Initialize ─────────────────────────────────────────────────
    renderSystemView();
})();

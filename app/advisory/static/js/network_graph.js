/**
 * Network Graph Visualization for AI Operating System
 * D3.js force-directed graph showing AI COO, systems, and agents
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

    // ── Build nodes + edges from agents ────────────────────────────
    var nodes = [];
    var edges = [];
    var systemSet = {};
    var deptColors = {
        'Executive': '#1a1a2e', 'Sales': '#4361ee', 'Customer Support': '#6f42c1',
        'Marketing': '#198754', 'Operations': '#fd7e14', 'Finance': '#dc3545',
        'Human Resources': '#6c757d', 'Technology': '#0dcaf0', 'Communication': '#20c997'
    };

    // Find AI COO
    var cooAgent = null;
    rawAgents.forEach(function(a) { if (a.is_cory) cooAgent = a; });

    // AI COO center node
    if (cooAgent) {
        nodes.push({ id: 'cory', label: 'AI COO', type: 'cory', dept: 'Executive', role: cooAgent.role, r: 40 });
    }

    // Group agents by department -> system nodes
    rawAgents.forEach(function(a) {
        if (a.is_cory) return;
        var dept = a.department || 'Operations';
        if (!systemSet[dept]) {
            systemSet[dept] = { id: 'sys_' + dept.replace(/\s/g,'_'), dept: dept, agents: [] };
            nodes.push({ id: systemSet[dept].id, label: dept, type: 'system', dept: dept, r: 25 });
            if (cooAgent) edges.push({ source: systemSet[dept].id, target: 'cory' });
        }
        var nodeId = 'agent_' + nodes.length;
        nodes.push({
            id: nodeId, label: a.name.replace('AI ', ''), type: a.is_specialist ? 'specialist' : 'agent',
            dept: dept, role: a.role, trigger: a.trigger || '', r: a.is_primary_focus ? 16 : 12
        });
        edges.push({ source: nodeId, target: systemSet[dept].id });
        systemSet[dept].agents.push(nodeId);
    });

    // ── D3 Force Layout ────────────────────────────────────────────
    var width = container.clientWidth || 800;
    var height = Math.max(500, Math.min(width * 0.65, 600));

    var svg = d3.select('#network-graph').append('svg')
        .attr('width', width).attr('height', height)
        .attr('viewBox', '0 0 ' + width + ' ' + height);

    // Defs for glow filter
    var defs = svg.append('defs');
    var filter = defs.append('filter').attr('id', 'glow');
    filter.append('feGaussianBlur').attr('stdDeviation', '4').attr('result', 'coloredBlur');
    var feMerge = filter.append('feMerge');
    feMerge.append('feMergeNode').attr('in', 'coloredBlur');
    feMerge.append('feMergeNode').attr('in', 'SourceGraphic');

    // Animated dash for edges
    defs.append('style').text(
        '@keyframes dash{from{stroke-dashoffset:20}to{stroke-dashoffset:0}}' +
        '.edge-line{stroke-dasharray:5,5;animation:dash 1.5s linear infinite}'
    );

    var simulation = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(edges).id(function(d){return d.id}).distance(function(d){
            return d.source.type === 'cory' || d.target.type === 'cory' ? 120 : 70;
        }))
        .force('charge', d3.forceManyBody().strength(-200))
        .force('center', d3.forceCenter(width/2, height/2))
        .force('collision', d3.forceCollide().radius(function(d){return d.r + 10}));

    // Edges
    var link = svg.append('g').selectAll('line')
        .data(edges).join('line')
        .attr('class', 'edge-line')
        .attr('stroke', '#cbd5e1').attr('stroke-width', 1.5)
        .attr('stroke-opacity', 0.6);

    // Node groups
    var node = svg.append('g').selectAll('g')
        .data(nodes).join('g')
        .attr('cursor', 'pointer')
        .call(d3.drag()
            .on('start', function(event,d){if(!event.active)simulation.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y})
            .on('drag', function(event,d){d.fx=event.x;d.fy=event.y})
            .on('end', function(event,d){if(!event.active)simulation.alphaTarget(0);d.fx=null;d.fy=null})
        );

    // Circle for each node
    node.append('circle')
        .attr('r', function(d){return d.r})
        .attr('fill', function(d){
            if(d.type==='cory') return '#1a1a2e';
            if(d.type==='system') return deptColors[d.dept]||'#4361ee';
            if(d.type==='specialist') return '#198754';
            return d3.color(deptColors[d.dept]||'#4361ee').brighter(0.8);
        })
        .attr('stroke', function(d){return d.type==='cory'?'#4361ee':'#fff'})
        .attr('stroke-width', function(d){return d.type==='cory'?3:2})
        .attr('filter', function(d){return d.type==='cory'?'url(#glow)':null});

    // Pulse animation for AI COO
    node.filter(function(d){return d.type==='cory'}).append('circle')
        .attr('r', function(d){return d.r+5})
        .attr('fill', 'none').attr('stroke', '#4361ee').attr('stroke-width', 2)
        .attr('opacity', 0)
        .append('animate')
            .attr('attributeName', 'r').attr('from', '40').attr('to', '55')
            .attr('dur', '2s').attr('repeatCount', 'indefinite');

    node.filter(function(d){return d.type==='cory'}).select('circle:last-of-type')
        .append('animate')
            .attr('attributeName', 'opacity').attr('values', '0.6;0')
            .attr('dur', '2s').attr('repeatCount', 'indefinite');

    // Icon text
    node.append('text')
        .attr('text-anchor', 'middle').attr('dy', '0.35em')
        .attr('fill', function(d){return d.type==='agent'||d.type==='specialist'?deptColors[d.dept]||'#333':'#fff'})
        .attr('font-size', function(d){return d.type==='cory'?'16px':d.type==='system'?'11px':'9px'})
        .attr('font-weight', 'bold')
        .text(function(d){
            if(d.type==='cory') return '🧠';
            if(d.type==='system') return d.label.substring(0,3).toUpperCase();
            return '';
        });

    // Label below node
    node.append('text')
        .attr('text-anchor', 'middle').attr('dy', function(d){return d.r + 14})
        .attr('fill', '#333').attr('font-size', '10px').attr('font-weight', '500')
        .text(function(d){
            var t = d.label;
            return t.length > 18 ? t.substring(0,16) + '..' : t;
        });

    // Tooltip on click
    var tooltip = d3.select('#network-graph').append('div')
        .style('position','absolute').style('display','none')
        .style('background','white').style('border','1px solid #dee2e6')
        .style('border-radius','8px').style('padding','12px').style('font-size','0.8rem')
        .style('box-shadow','0 4px 12px rgba(0,0,0,0.15)').style('max-width','250px').style('z-index','10');

    node.on('click', function(event, d) {
        event.stopPropagation();
        var html = '<strong>' + d.label + '</strong><br>';
        if (d.dept) html += '<span style="color:#6c757d">'+d.dept+'</span><br>';
        if (d.role) html += '<small>' + d.role.substring(0,120) + '</small><br>';
        if (d.trigger) html += '<small><strong>Trigger:</strong> ' + d.trigger + '</small>';
        if (d.type === 'cory') html += '<small>Monitors all systems and triggers actions</small>';
        tooltip.html(html).style('display','block')
            .style('left', (event.offsetX + 15) + 'px')
            .style('top', (event.offsetY - 10) + 'px');
    });

    svg.on('click', function() { tooltip.style('display','none'); });

    // Tick
    simulation.on('tick', function() {
        link.attr('x1',function(d){return d.source.x}).attr('y1',function(d){return d.source.y})
            .attr('x2',function(d){return d.target.x}).attr('y2',function(d){return d.target.y});
        node.attr('transform', function(d){
            d.x = Math.max(d.r, Math.min(width-d.r, d.x));
            d.y = Math.max(d.r, Math.min(height-d.r, d.y));
            return 'translate('+d.x+','+d.y+')';
        });
    });

    // ── Node highlight API (for simulation integration) ────────────
    window.highlightNetworkNode = function(agentName) {
        node.selectAll('circle:first-of-type')
            .transition().duration(200)
            .attr('stroke-width', function(d){
                return d.label.toLowerCase().indexOf(agentName.toLowerCase().replace('AI ','')) >= 0 ? 5 : 2;
            })
            .attr('stroke', function(d){
                return d.label.toLowerCase().indexOf(agentName.toLowerCase().replace('AI ','')) >= 0 ? '#ffc107' : (d.type==='cory'?'#4361ee':'#fff');
            });
        setTimeout(function(){
            node.selectAll('circle:first-of-type').transition().duration(500)
                .attr('stroke-width', function(d){return d.type==='cory'?3:2})
                .attr('stroke', function(d){return d.type==='cory'?'#4361ee':'#fff'});
        }, 1500);
    };
})();

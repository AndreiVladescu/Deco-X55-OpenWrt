'use strict';
'require view';
'require rpc';
'require ui';
'require poll';

var callStatus = rpc.declare({ object: 'decomesh', method: 'status' });
var callPair = rpc.declare({ object: 'decomesh', method: 'start_pairing' });
var callStop = rpc.declare({ object: 'decomesh', method: 'stop_pairing' });
var callApprove = rpc.declare({ object: 'decomesh', method: 'approve', params: [ 'id' ] });
var callReject = rpc.declare({ object: 'decomesh', method: 'reject', params: [ 'id' ] });
var callCreate = rpc.declare({ object: 'decomesh', method: 'create' });
var callJoin = rpc.declare({ object: 'decomesh', method: 'join' });
var callLeave = rpc.declare({ object: 'decomesh', method: 'leave' });

var stateText = {
	idle: _('Idle'),
	registrar: _('Pairing window open - press the button on the new Deco'),
	joining: _('Looking for a Deco with an open pairing window')
};

function ago(t) {
	var s = Math.max(0, Math.floor(Date.now() / 1000) - t);
	return s < 60 ? _('%d s ago').format(s) : _('%d min ago').format(Math.floor(s / 60));
}

return view.extend({
	load: function() { return callStatus(); },

	renderState: function(st) {
		var pending = (st.requests || []).filter(function(r) { return r.status == 'pending'; });
		var header = E('div', { 'class': 'cbi-section' }, [
			E('h3', {}, _('This node')),
			E('p', {}, [
				E('strong', {}, st.configured ? _('Mesh member') : _('Not in a mesh')),
				st.mesh_id ? ' (' + st.mesh_id + ')' : '', ' - ', st.model, ' ', st.mac
			]),
			E('p', {}, [ _('Pairing: '), E('em', {}, stateText[st.state] || st.state) ]),
			E('div', {}, st.configured ? [
				E('button', {
					'class': 'cbi-button cbi-button-action',
					'disabled': st.state != 'idle' ? '' : null,
					'click': ui.createHandlerFn(this, function() { return callPair(); })
				}, _('Add a Deco (open pairing window)')),
				' ',
				E('button', {
					'class': 'cbi-button cbi-button-reset',
					'disabled': st.state == 'idle' ? '' : null,
					'click': ui.createHandlerFn(this, function() { return callStop(); })
				}, _('Stop pairing')),
				' ',
				E('button', {
					'class': 'cbi-button cbi-button-negative',
					'click': ui.createHandlerFn(this, function() {
						if (confirm(_('Remove this Deco from the mesh?'))) return callLeave();
					})
				}, _('Leave mesh'))
			] : [
				E('button', {
					'class': 'cbi-button cbi-button-action',
					'disabled': st.state != 'idle' ? '' : null,
					'click': ui.createHandlerFn(this, function() { return callCreate(); })
				}, _('Create a mesh here (main unit)')),
				' ',
				E('button', {
					'class': 'cbi-button cbi-button-action',
					'disabled': st.state != 'idle' ? '' : null,
					'click': ui.createHandlerFn(this, function() { return callJoin(); })
				}, _('Join an existing mesh')),
				' ',
				E('button', {
					'class': 'cbi-button cbi-button-reset',
					'disabled': st.state == 'idle' ? '' : null,
					'click': ui.createHandlerFn(this, function() { return callStop(); })
				}, _('Stop'))
			])
		]);

		var reqTable = E('table', { 'class': 'table' }, [
			E('tr', { 'class': 'tr table-titles' }, [
				E('th', { 'class': 'th' }, _('Device')), E('th', { 'class': 'th' }, _('MAC')),
				E('th', { 'class': 'th' }, _('Requested')), E('th', { 'class': 'th' }, _('Status')),
				E('th', { 'class': 'th cbi-section-actions' }, '')
			])
		].concat((st.requests || []).map(function(r) {
			return E('tr', { 'class': 'tr' }, [
				E('td', { 'class': 'td' }, r.model), E('td', { 'class': 'td' }, r.mac),
				E('td', { 'class': 'td' }, ago(r.time)), E('td', { 'class': 'td' }, r.status),
				E('td', { 'class': 'td cbi-section-actions' }, r.status != 'pending' ? '' : [
					E('button', { 'class': 'cbi-button cbi-button-positive',
						'click': ui.createHandlerFn(this, function() { return callApprove(r.id); }) }, _('Approve')),
					' ',
					E('button', { 'class': 'cbi-button cbi-button-negative',
						'click': ui.createHandlerFn(this, function() { return callReject(r.id); }) }, _('Reject'))
				])
			]);
		}, this)));

		var nodeTable = E('table', { 'class': 'table' }, [
			E('tr', { 'class': 'tr table-titles' }, [
				E('th', { 'class': 'th' }, _('Neighbour (mesh MAC)')), E('th', { 'class': 'th' }, _('Interface')),
				E('th', { 'class': 'th' }, _('Last seen'))
			])
		].concat((st.nodes || []).map(function(n) {
			return E('tr', { 'class': 'tr' }, [
				E('td', { 'class': 'td' }, n.mac), E('td', { 'class': 'td' }, n.iface), E('td', { 'class': 'td' }, n.last_seen)
			]);
		})));

		return E('div', {}, [
			header,
			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, pending.length ? _('%d Deco wants to join').format(pending.length) : _('Join requests')),
				(st.requests || []).length ? reqTable : E('p', {}, E('em', {}, _('No requests.')))
			]),
			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, _('Mesh neighbours')),
				(st.nodes || []).length ? nodeTable : E('p', {}, E('em', {}, _('No mesh neighbours.')))
			])
		]);
	},

	render: function(st) {
		var root = E('div', { 'id': 'decomesh' }, this.renderState(st));
		poll.add(L.bind(function() {
			return callStatus().then(L.bind(function(s) {
				var r = document.getElementById('decomesh');
				if (r) { r.innerHTML = ''; r.appendChild(this.renderState(s)); }
			}, this));
		}, this), 3);
		return E('div', {}, [
			E('h2', {}, _('Mesh nodes')),
			E('p', { 'class': 'cbi-section-descr' },
				_('Press the reset button briefly (or use the button below) on a Deco that is already in the mesh, then on the new Deco. The request appears here and needs your approval.')),
			root
		]);
	},

	handleSave: null, handleSaveApply: null, handleReset: null
});

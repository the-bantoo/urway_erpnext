// Copyright (c) 2021, Bantoo and Saudi BTI and contributors
// For license information, please see license.txt

frappe.ui.form.on('URWay Gateway Settings', {
	refresh: function(frm) {
		set_indicator(frm)
	},
	after_save: function(frm) {
		set_indicator(frm)
	}
	
});

function set_indicator(frm){
	if(frm.doc.testing === 1 || frm.testing === '1'){
		frm.page.set_indicator(__("Testing"), "red");
	}
	else {
		frm.page.set_indicator(__("Live"), "green");
	}
}
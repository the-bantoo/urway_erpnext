import frappe
from frappe.utils import cint

def get_context(context):
	# Add homepage as parent
	context.body_class = "product-page"
	context.parents = [{"name": frappe._("Home"), "route":"/"}]

	context.no_cache = 1

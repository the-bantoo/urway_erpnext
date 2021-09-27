import base64, json
import datetime
import hashlib

from pip._vendor.urllib3 import response
import frappe
import hashlib
from frappe.integrations.utils import get_request_session, parse_qs 
from frappe.utils import today

"""
Todo
- Validate payment
- Add production url
- 
"""

def make_request(method, url, auth=None, headers=None, data=None):
	auth = auth or ''
	data = data or {}
	headers = headers or {}

	try:
		s = get_request_session()
		frappe.flags.integration_request = s.request(method, url, json=data, auth=auth, headers=headers)
		frappe.flags.integration_request.raise_for_status()

		if frappe.flags.integration_request.headers.get("content-type") == "text/plain; charset=utf-8":
			return parse_qs(frappe.flags.integration_request.text)

		return frappe.flags.integration_request.json()
	except Exception as exc:
		frappe.log_error()
		raise exc

def encrypt_string(hash_string):
	return hashlib.sha256(hash_string.encode()).hexdigest()

@frappe.whitelist()
def exe(name):
	
	url = ""
	invoice = frappe.get_doc( 'Sales Invoice', name )
	if int(invoice.outstanding_amount) != 0:
	
		transaction = frappe.new_doc( 'URWay Payment Transaction' )
		settings = frappe.get_doc('URWay Gateway Settings' )

		testing = settings.testing
		terminal = settings.terminal_id
		payment_mode = settings.mode_of_payment
		key = settings.merchantsecret_key
		password = settings.password
		customer_address = invoice.contact_display
		amount = str(invoice.outstanding_amount)
		action = str(1)

		"""add to doctype"""
		server_ip = "194.195.217.200"
		customer_ip = "10.10.10.10"
		country = "Saudi Arabia"
		currency = "SAR"
		customer_email = "customer@mail.com"

		hash = encrypt_string( "|".join([invoice.name, terminal, password, key, amount, currency]) )
		
		request_url = ""
		response = ""
		exc = ""
		
		if str(testing) == "1":
			request_url = "https://payments-dev.urway-tech.com/URWAYPGService/transaction/jsonProcess/JSONrequest"
		else:
			request_url = ""

		try:
			response = make_request("POST", request_url, data = {
				"amount": amount,
				"address": customer_address,
				"city": "Arabia",
				"customerIp": customer_ip,
				"customerEmail": customer_email,
				"zipCode": "",
				"trackid": invoice.name,
				"terminalId": terminal,
				"action": action,
				"password": password,
				"merchantIp": server_ip,
				"requestHash": hash,
				"country": country,
				"currency": currency
			})
		except Exception as exc:
			frappe.msgprint(str(exc), "Oops !!!")
			transaction.error_message = str(exc)
			transaction.status = "Failed"
			
		finally:
			transaction.message = str(response)
			transaction.customer = invoice.customer
			transaction.amount = amount
			transaction.sales_invoice = invoice.name
			
			if exc == "" or not exc:
				transaction.error_message = ""
				transaction.status = "Pending"
				if response['responseCode']:
					frappe.errprint("-------- error --------")
					if response['responseCode'] == "612":
						frappe.errprint("Invalid amount \n \n" + str(response) )
					else:
						frappe.errprint( str(response) )

					error_message(response['reason'], response['responseCode'])

				else:
					url = ( response['targetUrl'] + "?paymentid=" + response['payid'] )
					payment_name = make_payment_entry(invoice.customer, str(invoice.name), amount, payment_mode).name
					invoice.terms = str(invoice.terms or "") + "<a href='" + url + "'><underline>Click to Pay with URWay</underline><a/>"
					invoice.flags.ignore_validate_update_after_submit = True
					invoice.flags.ignore_validate = True
					invoice.save()					

					payment_entry = frappe.get_doc("Payment Entry", payment_name)
					payment_entry.submit()

					transaction.insert()
					transaction.submit()
	else:
		frappe.msgprint("This invoice is already fully paid")

	return url or ""

def error_message(reason, response):
	frappe.msgprint(reason, "Error " + response)

def make_payment_entry(customer, invoice_name, amount, payment_mode):
	company = frappe.get_doc("Company", frappe.defaults.get_user_default("Company"))
	
	payment_entry = frappe.get_doc({
		"doctype": "Payment Entry",
		"paid_amount": amount,
		"received_amount": amount,
		"base_received_amount": amount,
		"paid_to_account_currency": company.default_currency,
		"paid_to": company.default_cash_account,
		"company": company.name,
		"party_type": "Customer",
		"party": customer,
		"mode_of_payment": payment_mode,
		"payment_type": "Receive",
		"target_exchange_rate": 1,
		"source_exchange_rate": 1,
		"posting_date": today(),
		"references": [
			{ 
				"reference_doctype": "Sales Invoice",
				"reference_name": invoice_name,
				"allocated_amount": amount,
				"total_amount": amount,
				"outstanding_amount": 0
			}
		]
	})

	payment_name = payment_entry.insert()

	return payment_name

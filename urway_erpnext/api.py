import base64, json
import datetime
import hashlib
import socket

from frappe import _
from pip._vendor.urllib3 import response
import frappe
import hashlib
from frappe.integrations.utils import get_request_session, parse_qs 
from frappe.utils import today
from frappe.utils.background_jobs import enqueue
from frappe.core.page.background_jobs.background_jobs import get_info


"""
Todo
- Validate payment
  - Check and validate, make payment

- improvements
	- add purchase details
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

def get_server_ip():
	s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	s.connect(('8.8.8.8', 1))
	ip = s.getsockname()[0]
	s.close()

	frappe.db.set_value('URWay Gateway Settings', 'URWay Gateway Settings', 'server_ip', ip, notify=True, commit=True)

	return ip

def get_country(company_name):
	company = frappe.get_doc('Company', company_name)
	return company.country

def get_customer_email(customer_name):
	customer = frappe.get_doc('Customer', customer_name)
	if customer.email_id:
		return customer.email_id
	else:
		frappe.throw(_("An email address is required on this customer's primary contact"))

"""
[
	1. set payment link on sales invoice
	2. create urway payment transaction when link is clicked
	3. redirect request to urway payment page once the link is generated
]
4. Check payment status when payment is redirected back to ERPNext INVOICE
	- if successful
		- get urway transaction message with invoice number
		- convert to json
		- get payid
		- use payid to get payment status
	- Create payment entry 
	- Show payment status on sales invoice

5. Link Payment Entry to URWay Payment Transaction
6. Hash the links for security

"""


# backward compatibility
@frappe.whitelist(allow_guest=True)
def show_payment_status(invoice):
	status(invoice)

# redirects api requests to the sales invoice print which shows the payment status
@frappe.whitelist(allow_guest=True)
def status(invoice):

	invoice = frappe.get_doc("Sales Invoice", invoice)
	get_payment_status(invoice)

	from urllib.parse import urlencode

	# Creating public url to print format
	# System Language
	language = frappe.get_system_settings('language')

	params = urlencode({
		'format': "Paid Invoice" or 'Standard',
		'_lang': language,
		'key': invoice.get_signature()
	})

	# creating qr code for the url
	doc_url = f"{ frappe.utils.get_url() }/{ 'Sales%20Invoice' }/{ invoice.name }?{ params }"
	
	frappe.local.response['type'] = "redirect"
	frappe.local.response['location'] = doc_url


# check the status of the payment from urway
@frappe.whitelist(allow_guest=True)
def get_payment_status(invoice):
	
	# check payment status of invoice
	if invoice.status != "Paid" and invoice.docstatus == 1:
		payment_status = fetch_payment_status(invoice)

		if get_urway_transaction_status(invoice.name) == "Paid":
			make_payment_entry(invoice)

def make_payment_entry(invoice):
	company = frappe.get_doc("Company", frappe.defaults.get_user_default("Company"))
	trans = frappe.get_doc( 'URWay Gateway Settings' )

	#paid_from, paid_from_account_currency, base_paid_amount
	logged_in_user = frappe.session.user
	frappe.set_user("Administrator")
	frappe.local.no_cache = True

	payment_entry = frappe.get_doc({
		"doctype": "Payment Entry",
		"docstatus": 1,
		"paid_amount": invoice.outstanding_amount,
		"received_amount": invoice.outstanding_amount,
		"base_received_amount": invoice.outstanding_amount,
		"paid_to_account_currency": company.default_currency,
		"paid_to": company.default_cash_account,
		"company": company.name,
		"party_type": "Customer",
		"party": invoice.customer,
		"mode_of_payment": trans.mode_of_payment,
		"payment_type": "Receive",
		"target_exchange_rate": 1,
		"source_exchange_rate": 1,
		"posting_date": today(),
		"references": [
			{ 
				"reference_doctype": "Sales Invoice",
				"reference_name": invoice.name,
				"allocated_amount": invoice.outstanding_amount,
				"total_amount": invoice.grand_total,
				"outstanding_amount": 0
			}
		]
	})
	#payment_entry.flags.ignore_validate_update_after_submit = True ignore_permissions=True
	payment_name = payment_entry.insert() 
	
	trans.db_set(payment_entry, payment_name)
	
	frappe.db.commit()
	
	# frappe.set_user(logged_in_user)

	return payment_name

def set_urway_link(invoice, method=None):
	href = frappe.utils.get_url() + "/api/method/urway_erpnext.api.pay/?invoice=" + invoice.name
	
	invoice.db_set('urway',
			"<a href='" + href + "' target='_blank' style='text-decoration: underline;'> \
			<br/><img src='/assets/urway_erpnext/images/visa-mastercard-mada-logo.png' style='max-width: 75% !important;'><br/> \
			<b>Click to Pay with URWay | اضغط هنا لدفع الفاتورة إلكترونيا</b> \
			</a>",
			notify=True,
			commit=True
		)


@frappe.whitelist(allow_guest=True)
def make_urway_payment_link(invoice):
	pay(invoice)

@frappe.whitelist(allow_guest=True)
def pay(invoice):

	invoice = frappe.get_doc("Sales Invoice", invoice)

	# check if invoice is already paid
	if invoice.status == "Paid":
		status(invoice.name)
		return

	payment_link = get_payment_link(invoice)
	frappe.local.response['type'] = "redirect"
	frappe.local.response['location'] = payment_link


def get_urway_transaction_status(invoice_name):
	status = ""
	if frappe.db.exists({'doctype': 'URWay Payment Transaction', 'sales_invoice': invoice_name }):
		return frappe.get_doc('URWay Payment Transaction', {'sales_invoice': invoice_name }).status
	else:
		return status


def get_or_make_urway_transaction(invoice):
	if frappe.db.exists({'doctype': 'URWay Payment Transaction', 'sales_invoice': invoice.name }):
		return frappe.get_doc('URWay Payment Transaction', {'sales_invoice': invoice.name })
	else:
		return frappe.new_doc( 'URWay Payment Transaction' )


def get_request_url(testing_mode):
	if str(testing_mode) == "1":
		return "https://payments-dev.urway-tech.com/URWAYPGService/transaction/jsonProcess/JSONrequest"
	else:
		return "https://payments.urway-tech.com/URWAYPGService/transaction/jsonProcess/JSONrequest"


count = 0
@frappe.whitelist()
def get_payment_link(invoice):
	frappe.errprint("get_payment_link")
	if int(invoice.outstanding_amount) != 0:

		response = ""
		exc = ""

		transaction = get_or_make_urway_transaction(invoice)
		transaction_name = transaction.name

		settings = frappe.get_doc( 'URWay Gateway Settings' )
		
		testing_mode = settings.testing
		terminal = settings.terminal_id
		key = settings.merchantsecret_key
		password = settings.password
		customer_address = invoice.contact_display or "None"
		amount = str(invoice.outstanding_amount)
		currency = invoice.currency
		server_ip = settings.server_ip or get_server_ip()
		country = get_country(invoice.company)
		customer_email = get_customer_email(invoice.customer)

		hash = encrypt_string( "|".join([invoice.name, terminal, password, key, amount, currency]) )
		
		doc_url = frappe.utils.get_url() + "/api/method/urway_erpnext.api.status/?invoice=" + invoice.name

		payload = {
			"amount": amount,
			"address": customer_address,
			#"city": "Arabia",
			"customerEmail": customer_email,
			"zipCode": "",
			"trackid": invoice.name,
			"terminalId": terminal,
			"action": "1",
			"password": password,
			"merchantIp": server_ip,
			"requestHash": hash,
			"country": country,
			"udf2": doc_url,
			"currency": currency
		}
		# frappe.errprint(str(payload))

		try:
			response = make_request("POST", get_request_url(testing_mode), data = payload)
		except Exception as exc:
			frappe.msgprint(str(exc), "Oops !!!")
			transaction.error_message = str(exc)
			transaction.status = "Failed to Reach URWay" # add to transaction status options
			
		finally:
			transaction.message = str(response)
			transaction.customer = invoice.customer
			transaction.amount = amount
			transaction.sales_invoice = invoice.name
			
			# in case of errors, show the error, these are to be specified and handled one by one, otherwise just show as is
			if exc == "" or not exc:
				
				if 'responseCode' in response:
					# in no errors were detected: create a transaction with details for the scheduler to verify
					href = None
					
					if response['responseCode'] in [None, "", "000", "001"]:

						#frappe.errprint(response['responseCode'])
						if response['responseCode'] == "000" or response['responseCode'] == None:
							
							transaction.status = "Link Generated"					
							href = ( response['targetUrl'] + "?paymentid=" + response['payid'] )
							transaction.trans_id = response['payid']

						elif response['responseCode'] == "001":
							transaction.status = "Pending"
						
						transaction.error_message = ""

						invoice.flags.ignore_validate_update_after_submit = True
						invoice.flags.ignore_validate = True

					elif response['responseCode'] == "612":
						frappe.errprint("-------- error --------")
						frappe.errprint(_("Invalid amount \n \n" + str(response)))
						transaction.status = "Failed"
						transaction.trans_id = response['payid']
						transaction.error_message = str(response['responseCode']) +": "+ response['result']
						show_error_message(response['result'], response['responseCode'])
					
					elif response['responseCode'] == "600":
						frappe.errprint("-------- error --------")
						frappe.errprint(_("Invalid transaction message id or track id \n \n" + str(response)))
						transaction.status = "Failed"
						transaction.trans_id = response['payid']
						transaction.error_message = str(response['responseCode']) +": "+ response['result']
						show_error_message(response['result'], response['responseCode'])
					
					elif response['responseCode'] == "660":
						while count < 1:
							get_payment_link(invoice)
							invoice = frappe.get_doc('Sales Invoice', invoice.name)
							fetch_payment_status(invoice) # is this needed?

					else:
						frappe.errprint("-------- error --------")
						frappe.errprint(str(response))
						transaction.status = "Failed"
						transaction.trans_id = response['payid']
						transaction.error_message = str(response['responseCode']) +": "+ response['result']
						show_error_message(response['result'], response['responseCode'])

					if transaction_name:
						transaction.flags.ignore_validate_update_after_submit = True
						transaction.flags.ignore_validate = True
						transaction.save(ignore_permissions=True)
					else:
						transaction.insert(ignore_permissions=True)
						transaction.submit()

					frappe.db.commit()
					invoice.notify_update()

					return href

				else:
					frappe.throw(str(response))


@frappe.whitelist()
def fetch_payment_status(invoice):

	frappe.errprint("fetch_payment_status")

	response = ""
	exc = ""
	
	transaction = get_or_make_urway_transaction(invoice)
	transaction_name = transaction.name

	settings = frappe.get_doc( 'URWay Gateway Settings' )
	
	testing_mode = settings.testing
	terminal = settings.terminal_id
	key = settings.merchantsecret_key
	password = settings.password
	amount = str(invoice.outstanding_amount)
	currency = invoice.currency
	server_ip = settings.server_ip or get_server_ip()

	hash = encrypt_string( "|".join([invoice.name, terminal, password, key, amount, currency]) )
	
	doc_url = frappe.utils.get_url() + "/api/method/urway_erpnext.api.show_payment_status/?invoice=" + invoice.name
	
	payload = {
		"transid": transaction.trans_id,		
		"trackid": invoice.name,
		"terminalId": terminal,
		"action": "10",
		"merchantIp": server_ip,
		"password": password,
		"currency": currency,
		"amount": amount,
		"requestHash": hash,
		"udf2": doc_url,
	}
	# frappe.errprint(str(payload))

	try:
		response = make_request("POST", get_request_url(testing_mode), data = payload)
	except Exception as exc:
		frappe.msgprint(str(exc), "Oops !!!")
		transaction.error_message = str(exc)
		transaction.status = "Failed to Reach URWay"
		
	finally:
		transaction.message = str(response)
		transaction.customer = invoice.customer
		transaction.amount = amount
		transaction.sales_invoice = invoice.name
		
		# in case of errors, show the error, these are to be specified and handled one by one, otherwise just show as is
		if exc == "" or not exc:
			
			href = None
			if 'responseCode' in response:
				# in no errors were detected: create a transaction with details for the scheduler to verify

				if response['responseCode'] in [None, "", "000", "001"]:

					#frappe.errprint(response['responseCode'])
					if response['responseCode'] == "000" or response['responseCode'] == None:
						transaction.status = "Paid"

					elif response['responseCode'] == "001":
						transaction.status = "Pending"
					
					transaction.error_message = ""

					invoice.flags.ignore_validate_update_after_submit = True
					invoice.flags.ignore_validate = True

				elif response['responseCode'] == "612":
					frappe.errprint("-------- error --------")
					frappe.errprint(_("Invalid amount \n \n" + str(response)))
					transaction.status = "Failed"
					transaction.trans_id = response['payid']
					transaction.error_message = str(response['responseCode']) +": "+ response['result']
					show_error_message(response['result'], response['responseCode'])
				
				elif response['responseCode'] == "600":
					frappe.errprint("-------- error --------")
					frappe.errprint(_("Invalid transaction message id or track id \n \n" + str(response)))
					transaction.status = "Failed"
					transaction.trans_id = response['payid']
					transaction.error_message = str(response['responseCode']) +": "+ response['result']
					show_error_message(response['result'], response['responseCode'])
				
					"""elif response['responseCode'] == "660":
						while count < 1:
							make_urway_request(invoice, method=None, trans_type="pay")
							invoice = frappe.get_doc('Sales Invoice', invoice.name)
							make_urway_request(invoice, method=None, trans_type="check")
					"""
				else:
					frappe.errprint("-------- error --------")
					frappe.errprint(str(response))
					transaction.status = "Failed"
					transaction.trans_id = response['payid']
					transaction.error_message = str(response['responseCode']) +": "+ response['result']
					show_error_message(response['result'], response['responseCode'])

				if transaction_name:
					transaction.flags.ignore_validate_update_after_submit = True
					transaction.flags.ignore_validate = True
					transaction.save(ignore_permissions=True)
				else:
					transaction.insert(ignore_permissions=True)
					transaction.submit()

				frappe.db.commit()
				invoice.notify_update()

				return transaction.status

			else:
				frappe.throw(str(response))


def show_error_message(reason, response):
	frappe.throw(
		title = reason,
		msg = "Error code: " + response
	)
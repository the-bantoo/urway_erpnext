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
- ...
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



def get_customer_ip():
	return frappe.get_request_header('X-Forwarded-For') or frappe.get_request_header('REMOTE_ADDR')


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

4. Check payment status when payment is redirected back to ERPNext INVOICE
	- if successful
		- get urway transaction message with invoice number
		- convert to json
		- get payid
		- use payid to get payment status
	- Create payment entry 
	- Show payment status on sales invoice

5. Link Payment Entry to URWay Payment Transaction
]
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
	error = get_payment_status(invoice)

	from urllib.parse import urlencode

	# Creating public url to print format
	# System Language
	language = frappe.get_system_settings('language')

	params = urlencode({
		'format': "Paid Invoice" or 'Standard',
		'compact_item_print': 1,
		'_lang': language,
		'key': invoice.get_signature()
	})

	# creating qr code for the url
	url = f"{ frappe.utils.get_url() }/{ 'Sales%20Invoice' }/{ invoice.name }?{ params }"
	
	if error:
		create_an_issue(error[1], invoice)

		payment_link = invoice.urway_sms_link.replace("<b>Click to Pay with URWay | ???????? ?????? ???????? ???????????????? ??????????????????</b><br/>", "")
		url = frappe.utils.get_url() + "/payment-failed?code=" + error[1] + "&retry=" + payment_link

	frappe.local.response['location'] = url
	frappe.local.response['type'] = "redirect"


# check the status of the payment from urway
@frappe.whitelist(allow_guest=True)
def get_payment_status(invoice):
	
	# check payment status of invoice
	# return err_code in case of err
	if invoice.status != "Paid" and invoice.docstatus == 1:
		err_code = fetch_payment_status(invoice)

		tran = get_urway_transaction_status(invoice.name)
		if tran:
			if tran.status == "Paid":
				make_payment_entry(invoice, tran)
			elif tran.status == "Failed": #revisit
				return err_code
			
def make_payment_entry(invoice, urway_trans):
	company = frappe.get_doc("Company", frappe.defaults.get_user_default("Company"))
	settings = frappe.get_doc( 'URWay Gateway Settings' )
	date = today()

	data = {
		"doctype": "Payment Entry",		
		"company": company.name,
		"posting_date": date,
		"party_type": "Customer",
		"party": invoice.customer,
		"mode_of_payment": settings.mode_of_payment,
		"payment_type": "Receive",
		"base_received_amount": invoice.outstanding_amount,
		"received_amount": invoice.outstanding_amount,
		"paid_amount": invoice.outstanding_amount,
		"base_paid_amount": invoice.outstanding_amount,
		"paid_to_account_currency": company.default_currency,
		"paid_from_account_currency": company.default_currency,
		"paid_to": company.default_bank_account,
		"paid_from": company.default_receivable_account,
		"target_exchange_rate": 1,
		"reference_no": urway_trans.trans_id,
		"reference_date": date,
		#"source_exchange_rate": 1,
		"docstatus": 1,
		"references": [
			{
				"reference_doctype": "Sales Invoice",
				"reference_name": invoice.name,
				"allocated_amount": invoice.outstanding_amount
			}
		]
	}

	payment_entry = frappe.get_doc(data)

	payment = payment_entry.insert(ignore_permissions=True)

	urway_trans.db_set('payment_entry', payment.name)
	frappe.db.commit()

	return payment.name

def set_urway_link(invoice, method=None):
	href = frappe.utils.get_url() + "/api/method/urway_erpnext.api.pay?invoice=" + invoice.name
	
	invoice.db_set('urway',
			"<a href='" + href + "' target='_blank' style='text-decoration: underline;'> \
			<br/><img src='/assets/urway_erpnext/images/visa-mastercard-mada-logo.png' style='max-width: 75% !important;'><br/> \
			<b>Click to Pay with URWay | ???????? ?????? ???????? ???????????????? ??????????????????</b> \
			</a>"
		)
	invoice.db_set('urway_sms_link',
			"<b>Click to Pay with URWay | ???????? ?????? ???????? ???????????????? ??????????????????</b><br/>" + href,
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

	payment_link = get_payment_link(invoice)

	frappe.local.response['location'] = payment_link
	frappe.local.response['type'] = "redirect"


def get_urway_transaction_status(invoice_name):
	if frappe.db.exists({'doctype': 'URWay Payment Transaction', 'sales_invoice': invoice_name }):
		return frappe.get_doc('URWay Payment Transaction', {'sales_invoice': invoice_name })
	else:
		return False


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
	
	if invoice.status != "Paid":

		response = ""
		exc = ""
		error = None

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
		
		doc_url = frappe.utils.get_url() + "/api/method/urway_erpnext.api.status?invoice=" + invoice.name

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
			"customerIp": get_customer_ip(),
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
						transaction.status = "Failed"
						transaction.trans_id = response['payid']
						transaction.error_message = str(response['responseCode']) +": "+ response['result']
						error = 1
					
					elif response['responseCode'] == "600":
						transaction.status = "Failed"
						transaction.trans_id = response['payid']
						transaction.error_message = str(response['responseCode']) +": "+ response['result']
						error = 1
					
					elif response['responseCode'] == "660":
						while count < 1:
							get_payment_link(invoice)
							invoice = frappe.get_doc('Sales Invoice', invoice.name)
							fetch_payment_status(invoice) # is this needed?
						error = 1

					else:
						transaction.status = "Failed"

						pay_id = 'None was generated'
						if 'payid' in response:
							response['payid']

						transaction.trans_id = pay_id
						transaction.error_message = str(response['responseCode']) +": "+ response['result']
						error = 1

					if transaction_name:
						transaction.flags.ignore_validate_update_after_submit = True
						transaction.flags.ignore_validate = True
						transaction.save(ignore_permissions=True)
					else:
						transaction.insert(ignore_permissions=True)
						transaction.submit()

					frappe.db.commit()
					invoice.notify_update()

					if error == 1:
						create_an_issue(response['responseCode'], invoice)		# add setting to toggle off		
						url = frappe.utils.get_url() + "/payment-failed?code=" + response['responseCode'] + "&retry=" + (href or "")
						return url
					else:
						return href
				else:
					frappe.throw(str(response))
	else:
		from urllib.parse import urlencode

		# Creating public url to print format
		# System Language
		language = frappe.get_system_settings('language')

		params = urlencode({
			'format': "Paid Invoice" or 'Standard',
			'compact_item_print': 1,
			'_lang': language,
			'key': invoice.get_signature()
		})

		# creating qr code for the url
		return f"{ frappe.utils.get_url() }/{ 'Sales%20Invoice' }/{ invoice.name }?{ params }"


@frappe.whitelist()
def fetch_payment_status(invoice):

	response = ""
	exc = ""
	error = None
	
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
	
	doc_url = frappe.utils.get_url() + "/api/method/urway_erpnext.api.status?invoice=" + invoice.name
	
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
					transaction.status = "Failed"
					transaction.trans_id = response['payid']
					transaction.error_message = str(response['responseCode']) +": "+ response['result']
					error = 1
				
				elif response['responseCode'] == "600":
					frappe.errprint(_("Invalid transaction message id or track id \n \n" + str(response)))
					transaction.status = "Failed"
					transaction.trans_id = response['payid']
					transaction.error_message = str(response['responseCode']) +": "+ response['result']
					error = 1

				else:
					transaction.status = "Failed"
					transaction.trans_id = response['payid']
					transaction.error_message = str(response['responseCode']) +": "+ response['result']
					error = 1

				if transaction_name:
					transaction.flags.ignore_validate_update_after_submit = True
					transaction.flags.ignore_validate = True
					transaction.save(ignore_permissions=True)
				else:
					transaction.insert(ignore_permissions=True)
					transaction.submit()

				frappe.db.commit()
				invoice.notify_update()
				
				if error:
					return [response['result'], response['responseCode']]

				return transaction.status

			else:
				frappe.throw(str(response))

@frappe.whitelist(allow_guest=True)
def create_an_issue(error_code, invoice):
	
	invoice_url = invoice.get_url()
	desc = 'Error code: {error_code} - \n\n<a class="btn btn-secondary btn-lg" href="{invoice_url}">Related Invoice</a>'.format(
		error_code=error_code, invoice_url=invoice_url)

	issue = frappe.get_doc({
		'doctype': 'Issue',
		'subject': 'Urway Payment Problem',
		'description': desc
	})
	
	issue.flags.ignore_validate_update_after_submit = True
	issue.flags.ignore_validate = True
	issue.insert(ignore_permissions=True)
	frappe.db.commit()
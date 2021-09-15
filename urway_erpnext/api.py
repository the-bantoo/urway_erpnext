import base64, json
import datetime
import hashlib
import frappe
import hashlib
from frappe.integrations.utils import get_request_session, parse_qs 

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
	sha_signature = hashlib.sha256(hash_string.encode()).hexdigest()
	return sha_signature

@frappe.whitelist()
def exe(name):
	doc = frappe.get_doc( 'URWay Payment Transaction', name )
	
	settings = frappe.get_doc('URWay Gateway Settings')

	testing = settings.testing
	terminal = settings.terminal_id
	key = settings.merchantsecret_key
	password = settings.password


	"""add to doctype"""
	server_ip = "172.105.90.8"
	customer_ip = "10.10.10.10"
	country = "Saudi Arabia"
	currency = "SAR"
	customer_email = "customer@mail.com"
	customer_address = "customer_address"
	amount = str(doc.amount)
	action = str(1)	

	data = {}

	test_data = {}

	"""
	test_data["amount"] = "10.00"
	test_data["address"] = "address"
	test_data["customerIp"] = "10.10.10.10"
	test_data["city"] = "Arabia"
	test_data["trackid"] = "123123"
	test_data["terminalId"] = "tis"
	test_data["action"] = "1"
	test_data["password"] = "tis@123"
	test_data["merchantIp"] = server_ip
	test_data["requestHash"] = "edecfb35e7bd2559c88ecc761553e79b1f868c1c9a0d0ea6ac3051f5bd058c2d"
	test_data["country"] = "SA"
	test_data["currency"] = "SAR"
	test_data["customerEmail"] = "a@a.com"
	test_data["zipCode"] = ""

	for i in test_data:
		frappe.errprint( i + ": " + str(test_data[i]) )
	"""

	hash = encrypt_string( "|".join([doc.name, terminal, password, key, amount, currency]) )

	res = ""
	e = ""
	feedback = ""
	url = ""

	try:
		res = make_request("POST", "https://payments-dev.urway-tech.com/URWAYPGService/transaction/jsonProcess/JSONrequest", data = {
			"amount": amount,
			"address": customer_address,
			"city": "Arabia",
			"customerIp": customer_ip,
			"customerEmail": customer_email,
			"zipCode": "",
			"trackid": doc.name,
			"terminalId": terminal,
			"action": action,
			"password": password,
			"merchantIp": server_ip,
			"requestHash": hash,
			"country": country,
			"currency": currency
		})
	except Exception as e:
		frappe.errprint("Oops !!! \n \n" + str(e))
		doc.error_message = str(e)
		doc.status = "Failed"
		
	finally:
		doc.message = str(res)
		frappe.errprint("------- done --------" )
		#doc.save()
		if e == "" or not e:
			doc.error_message = ""
			doc.status = "Pending"
			url = ( res['targetUrl'] + "?paymentid=" + res['payid'] )

	return url
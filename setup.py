from setuptools import setup, find_packages

with open('requirements.txt') as f:
	install_requires = f.read().strip().split('\n')

# get version from __version__ variable in urway_erpnext/__init__.py
from urway_erpnext import __version__ as version

setup(
	name='urway_erpnext',
	version=version,
	description='URWay ERPNext Integration',
	author='Bantoo and Saudi BTI',
	author_email='devs@thebantoo.com',
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)

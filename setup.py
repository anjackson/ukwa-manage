from distutils.core import setup

setup(
	name="python-har-daemon",
	version="0.0.2",
	author="Roger G. Coram",
	author_email="roger.coram@bl.uk",
	packages=[ "harchiverd" ],
	license="LICENSE.txt",
	description="Stores HAR records in WARC files.",
	long_description=open( "README.md" ).read(),
	install_requires=[
		"pika",
		"python-warcwriterpool",
		"python-daemonize",
		"requests",
	],
	data_files=[
		( "/etc/init.d", [ "harchiverd-init" ] ),
		( "/usr/local/bin", [ "harchiver.py" ] ),
	],
)

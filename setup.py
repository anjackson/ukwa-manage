from setuptools import setup

with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    name='shepherd',
    version='1.0.0',
    packages=['crawl', 'crawl.h3', 'crawl.hdfs', 'crawl.job', 'crawl.profiles', 'crawl.sip', 'crawl.w3act'],
    install_requires=requirements,
    license='Apache 2.0',
    long_description=open('README.md').read(),
    entry_points={
        'console_scripts': [
            'get-ids-from-hdfs=crawl.sip.ids:main',
            'create-sip=crawl.sip.creator:main',
            'movetohdfs=crawl.hdfs.movetohdfs:main'
        ],
    }
)
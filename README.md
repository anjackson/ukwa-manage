Heritrix BL Configs
===================

This repository contains our general utility code, configuration and scripts used to orchestrate the overall crawl and data management workflows.

warc-compare-hdfs.py
--------------------

Compare local files with those on HDFS.

   $ python -u bin/warcs-compare-hdfs.py | tee delete.log


   $ python -u bin/warcs-compare-hdfs.py delete | delete.log
